"""
IngestController -- owns the item list and runs the two pieces of work
(pre-flight, ingest) as stage sequences over a thread pool.

Framework-agnostic: no Qt. It emits ControllerEvents through subscribers;
the UI layer subscribes one function that marshals them onto the main
thread and updates the view. Everything the controller needs from the
outside (NAS, ledger, hashing, Kitsu, proxy encoding, metadata) is
injected, so the whole flow is unit-testable with fakes.

run_preflight() and run_ingest() are synchronous and internally parallel:
the UI runs each in one background thread and the controller fans out
across the pool inside. Events fire from worker threads -- the UI adapter
is responsible for thread-hopping them.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
import logging
import datetime
import threading
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

from square_core.ingest_item import IngestItem, Stage, Action, IssueKind, Status
from square_core import preflight
from square_core.hashing import FileHasher
from square_core.preview_metadata import PreviewMetadata

logger = logging.getLogger("SquareIngestController")


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ControllerConfig:
    nas_root: str = ""
    project_code: str = ""
    filename_template: str = ""
    nas_dir_template: str = ""
    media_type_configs: dict = field(default_factory=dict)
    transfer_mode: str = "copy"
    copy_workers: int = 4
    preview_media_types: list = field(default_factory=list)
    shot_folder_structure: list = field(default_factory=list)
    task_types: list = field(default_factory=list)
    ingested_by: str = ""
    ingest_task_status: str = "Done"     # move the Ingest task here on success; "" leaves it

    @property
    def known_media_types(self) -> list:
        return list(self.media_type_configs.keys())

    def dir_template_for(self, media_type) -> str | None:
        return (
            self.media_type_configs.get((media_type or "").strip())
            or self.nas_dir_template
            or None
        )

    @classmethod
    def from_studio_config(cls, cfg, project_code="", ingested_by="") -> "ControllerConfig":
        return cls(
            nas_root=getattr(cfg, "nas_root", ""),
            project_code=project_code,
            filename_template=getattr(cfg, "filename_template", ""),
            nas_dir_template=getattr(cfg, "nas_dir_template", ""),
            media_type_configs=dict(getattr(cfg, "media_type_configs", {}) or {}),
            transfer_mode=getattr(cfg, "transfer_mode", "copy"),
            copy_workers=getattr(cfg, "copy_workers", 4),
            preview_media_types=list(getattr(cfg, "preview_enabled_media_types", []) or []),
            shot_folder_structure=list(getattr(cfg, "shot_folder_structure", []) or []),
            task_types=list(getattr(cfg, "tasks", []) or []),
            ingested_by=ingested_by or getattr(cfg, "kitsu_user", ""),
            ingest_task_status=getattr(cfg, "ingest_task_status", "Done"),
        )

    def to_snapshot(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_snapshot(cls, d: dict) -> "ControllerConfig":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


@dataclass
class ControllerEvent:
    kind: str
    item: IngestItem | None = None
    payload: dict = field(default_factory=dict)


_NON_VISUAL = {"audio", "lut"}


def _wants_preview(media_type: str, enabled: list) -> bool:
    m = (media_type or "").strip().lower()
    if m in _NON_VISUAL:
        return False
    return m in {(t or "").strip().lower() for t in (enabled or [])}


class IngestController:
    def __init__(self, config: ControllerConfig, project, *,
                 nas, ledger, recorder, proxy_generator=None,
                 hasher: FileHasher | None = None, extractor=None):
        self.config = config
        self.project = project or {}
        self.nas = nas
        self.ledger = ledger
        self.recorder = recorder
        self.proxy_generator = proxy_generator
        self.hasher = hasher or FileHasher()
        self.extractor = extractor

        self.items: list[IngestItem] = []
        self._by_key: dict[str, IngestItem] = {}
        self.batch_id = str(uuid.uuid4())

        self._listeners = []
        self._cancel = threading.Event()
        self._undo: list[dict] = []
        self._scanned: set[str] = set()          # keys whose metadata+hashes are done
        self._slot_state: dict[str, tuple] = {}   # key -> (state, detail) from last NAS inspect

        # Preview encode + upload runs OFF the ingest critical path: the row
        # goes Completed the moment files are verified on the NAS + Kitsu has
        # the version, and previews trickle in behind on this small pool.
        self._preview_pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ingest-preview"
        )
        self._preview_futures: dict[str, object] = {}
        self._preview_outcomes: dict[str, object] = {}   # key -> RecordOutcome (for the deferred attach)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def subscribe(self, fn) -> None:
        self._listeners.append(fn)

    def _emit(self, kind, item=None, **payload) -> None:
        ev = ControllerEvent(kind=kind, item=item, payload=payload)
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:   # a bad listener must not break the pipeline
                logger.exception("[IngestController] listener error on %s", kind)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, scan_items, *, replace=False) -> list[IngestItem]:
        if replace:
            self.items.clear()
            self._by_key.clear()
            self._scanned.clear()
            self._slot_state.clear()

        added = []
        for si in scan_items:
            item = si if isinstance(si, IngestItem) else IngestItem.from_scan_item(si)
            if item.key in self._by_key:
                continue
            item.preview_default = _wants_preview(item.media_type, self.config.preview_media_types)
            item.preview_wanted = item.preview_default
            self.items.append(item)
            self._by_key[item.key] = item
            added.append(item)

        self._emit("items_loaded", payload={"added": [i.key for i in added], "total": len(self.items)})
        return added

    def get(self, key) -> IngestItem | None:
        return self._by_key.get(key)

    def remove(self, key) -> None:
        item = self._by_key.pop(key, None)
        if item:
            self.items.remove(item)
            self._scanned.discard(key)
            self._slot_state.pop(key, None)
            self._reassemble_all()
            self._emit("items_loaded", payload={"removed": [key], "total": len(self.items)})

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def run_preflight(self, keys=None) -> None:
        self._cancel.clear()
        targets = self._resolve_targets(keys)
        if not targets:
            return
        self._emit("preflight_started", payload={"keys": [i.key for i in targets]})

        for it in targets:
            it.preflight_done = False
            it.check_error = ""
            self._emit("item_updated", item=it)

        workers = max(1, min(self.config.copy_workers, len(targets)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(self._scan_one, it): it for it in targets}
            for fut in as_completed(futs):
                it = futs[fut]
                try:
                    fut.result()
                except Exception as e:
                    it.check_error = str(e)
                    logger.exception("[IngestController] pre-flight failed for %s", it.key)

        self._reassemble_all()
        self._emit("preflight_finished", payload={"keys": [i.key for i in targets]})

    def _scan_one(self, item: IngestItem) -> None:
        """Parallel-safe per-item work: metadata probe, hashing, dest, ledger, slot."""
        if self._cancel.is_set():
            return
        if item.key not in self._scanned:
            item.probe_metadata(self.extractor)
            if item.source_files:
                item.hashes = self.hasher.hash_files(item.source_files)
            self._scanned.add(item.key)
        self._recheck_one(item)
        item.preflight_done = True

    def _recheck_one(self, item: IngestItem) -> None:
        """Cheap re-evaluation after an edit: dest path, ledger, NAS slot. No re-hash."""
        code = self.config.project_code
        dest = self.nas.get_dest_dir(
            code, item.sequence_code, item.shot_code, item.media_name,
            version=item.version, media_type=item.media_type or "",
            resolution=item.resolution or "1920x1080",
            dir_template=self.config.dir_template_for(item.media_type),
        )
        item.dest_dir = str(dest)
        names = self.nas.dest_names(item, item.version, code, self.config.filename_template)
        if item.source_files:
            first = item.source_files[0]
            item.sample_dest_file = str(dest / names.get(first, os.path.basename(first)))

        match = self.ledger.classify(list(item.hashes.values()))
        item.ledger_kind = match.kind
        if match.kind == "full" and match.latest:
            item.ledger_detail = (
                f"Identical content already ingested as v{match.latest.version} "
                f"on {match.latest.ingested_at[:10]}."
            )
        elif match.kind == "partial":
            item.ledger_detail = (
                f"{match.matched_count} of {match.total_count} file(s) already ingested."
            )
        else:
            item.ledger_detail = ""

        state, detail = self.nas.inspect_slot(
            dest, item, item.version, code,
            self.config.filename_template, hasher=self.hasher,
        )
        self._slot_state[item.key] = (state, detail)
        item.slot_state = state

    def _reassemble_all(self) -> None:
        """Rebuild every item's issue list -- cross-item issues depend on the whole batch."""
        cross = preflight.cross_item_issues(self.items)
        for it in self.items:
            if not it.preflight_done and it.key not in self._slot_state:
                continue
            state, detail = self._slot_state.get(it.key, (preflight.SLOT_EMPTY, ""))
            it.issues = preflight.assemble_issues(
                it, state, detail,
                cross=cross.get(it.key, []),
                known_media_types=self.config.known_media_types or None,
            )
            self._maybe_preview_nonvisual_issue(it)
            self._emit("item_updated", item=it)

    def _maybe_preview_nonvisual_issue(self, item: IngestItem) -> None:
        if item.preview_wanted and (item.media_type or "").strip().lower() in _NON_VISUAL:
            item.issues.append(preflight.Issue(
                IssueKind.PREVIEW_NONVISUAL,
                f"Preview is on for '{item.media_type}', which has no meaningful video preview. "
                f"Turn it off, or ignore.",
                severity=preflight.Severity.WARN, column="preview_wanted",
            ))

    # ------------------------------------------------------------------
    # Undo (pre-ingest user edits only)
    # ------------------------------------------------------------------

    _UNDO_CAP = 100

    def _push_undo(self, label: str, keys) -> None:
        snap = {
            k: self._by_key[k].to_dict()
            for k in keys
            if k in self._by_key and not self._by_key[k].ingested
        }
        if not snap:
            return
        self._undo.append({"label": label, "items": snap})
        del self._undo[:-self._UNDO_CAP]

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1]["label"] if self._undo else ""

    def undo(self) -> bool:
        if not self._undo:
            return False
        entry = self._undo.pop()
        for k, d in entry["items"].items():
            cur = self._by_key.get(k)
            if not cur or cur.ingested:
                continue
            restored = IngestItem.from_dict(d)
            idx = self.items.index(cur)
            self.items[idx] = restored
            self._by_key[k] = restored
            self._recheck_one(restored)
        self._reassemble_all()
        self._emit("undo", payload={"label": entry["label"], "remaining": len(self._undo)})
        return True

    # ------------------------------------------------------------------
    # Edits & resolutions
    # ------------------------------------------------------------------

    def set_field(self, key, field_name, value) -> None:
        item = self._by_key[key]
        if field_name not in (
            "sequence_code", "shot_code", "media_type", "media_name", "version",
            "fps", "resolution", "colorspace",
        ):
            raise ValueError(f"not an editable field: {field_name}")
        if field_name == "version":
            value = int(value)
        self._push_undo(f"edit {field_name}", [key])
        setattr(item, field_name, value)
        if field_name in ("resolution", "fps", "colorspace"):
            item.metadata_verified[field_name] = True   # user-set counts as known
        if field_name == "media_type":
            # the preview default belongs to the media type -- follow it unless
            # the user has explicitly ticked/unticked the box for this row
            item.preview_default = _wants_preview(item.media_type, self.config.preview_media_types)
            if not item.preview_user_set:
                item.preview_wanted = item.preview_default
        self._recheck_one(item)
        self._reassemble_all()

    def set_preview(self, key, wanted: bool) -> None:
        item = self._by_key[key]
        self._push_undo("toggle preview", [key])
        item.preview_wanted = bool(wanted)
        item.preview_user_set = True
        self._reassemble_all()

    def skip(self, key) -> None:
        self._push_undo("skip", [key])
        self._by_key[key].skipped = True
        self._reassemble_all()

    def include(self, key) -> None:
        self._push_undo("include", [key])
        self._by_key[key].include()
        self._reassemble_all()

    def resolve(self, key, issue_id, action: Action, *, _record_undo=True) -> None:
        item = self._by_key[key]
        if _record_undo:
            self._push_undo(f"resolve {action.value}", [key])
        item.resolve(issue_id, action)
        if action == Action.VERSION_UP:
            item.version = self.nas.next_free_version(
                item, self.config.project_code, self.config.filename_template,
                start=item.version + 1,
                dir_template=self.config.dir_template_for(item.media_type),
            )
            # the resolution we just recorded was for the OLD version's issue;
            # drop it so it can't linger. Other resolutions (Ignore on a
            # duplicate, etc.) stay.
            item.unresolve(issue_id)
            self._recheck_one(item)
        elif action == Action.OVERWRITE:
            self._recheck_one(item)
        self._reassemble_all()

    def resolve_many(self, keys, issue_kind: IssueKind, action: Action) -> None:
        keys = [k for k in keys if k in self._by_key]
        # Resolve the issue id for every key BEFORE mutating anything --
        # resolving one row can clear a cross-item issue on the next
        # (skipping one colliding row un-flags the other), which would
        # otherwise make a batch action silently skip rows.
        targets = []
        for key in keys:
            for iss in self._by_key[key].issues:
                if iss.kind == issue_kind:
                    targets.append((key, iss.id))
                    break
        if not targets:
            return
        self._push_undo(f"resolve {action.value} ×{len(targets)}", [k for k, _ in targets])
        for key, iid in targets:
            self.resolve(key, iid, action, _record_undo=False)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingestable_items(self, keys=None) -> list[IngestItem]:
        pool = self._resolve_targets(keys)
        return [i for i in pool if i.ingestable]

    def run_ingest(self, keys=None, *, dry_run=False) -> dict:
        self._cancel.clear()
        targets = self.ingestable_items(keys)
        if not targets:
            self._emit("ingest_finished", payload={"done": 0, "failed": 0, "dry_run": dry_run})
            return {"done": 0, "failed": 0, "items": []}

        self._emit("ingest_started", payload={"keys": [i.key for i in targets], "dry_run": dry_run})
        for it in targets:
            it.stage = Stage.QUEUED
            it.stage_pct = 0
            it.ingest_error = ""
            it.preview_state = ""

        cw = max(1, self.config.copy_workers)
        item_workers = max(1, min(cw, len(targets)))
        done = failed = 0
        preview_futs = []

        # One shared pool for EVERY frame transfer in the batch: total
        # concurrent file copies == cw, no matter how many items overlap.
        with ThreadPoolExecutor(max_workers=cw, thread_name_prefix="ingest-copy") as copy_pool, \
             ThreadPoolExecutor(max_workers=item_workers, thread_name_prefix="ingest-item") as item_pool:
            futs = {item_pool.submit(self._ingest_core, it, dry_run, copy_pool): it for it in targets}
            for fut in as_completed(futs):
                it = futs[fut]
                try:
                    pv = fut.result()
                    if pv is not None:
                        preview_futs.append(pv)
                    done += 1
                except Exception as e:
                    failed += 1
                    it.ingest_error = str(e)
                    it.stage = Stage.FAILED
                    logger.exception("[IngestController] ingest failed for %s", it.key)
                    self._emit("item_updated", item=it)

        self._emit("ingest_finished", payload={
            "done": done, "failed": failed, "dry_run": dry_run,
            "previews_pending": sum(1 for f in preview_futs if not f.done()),
        })

        pending = [f for f in preview_futs if not f.done()]
        if pending:
            threading.Thread(
                target=self._await_previews, args=(pending,), daemon=True,
                name="ingest-preview-wait",
            ).start()
        elif preview_futs:
            self._emit("previews_finished", payload={})

        return {"done": done, "failed": failed, "items": [i.key for i in targets]}

    def _await_previews(self, futures) -> None:
        for f in futures:
            try:
                f.result()
            except Exception:
                pass
        self._emit("previews_finished", payload={})

    def _set_stage(self, item: IngestItem, stage: Stage, pct: int) -> None:
        item.stage = stage
        item.stage_pct = pct
        self._emit("item_stage", item=item, stage=stage.value, pct=pct)

    # ------------------------------------------------------------------
    # Core ingest (critical path: files safe on NAS + Kitsu has the version)
    # ------------------------------------------------------------------

    def _ingest_core(self, item: IngestItem, dry_run: bool, copy_pool):
        if self._cancel.is_set():
            return None
        code = self.config.project_code
        tmpl = self.config.filename_template
        dest = self.nas.get_dest_dir(
            code, item.sequence_code, item.shot_code, item.media_name,
            version=item.version, media_type=item.media_type or "",
            resolution=item.resolution or "1920x1080",
            dir_template=self.config.dir_template_for(item.media_type),
        )
        item.dest_dir = str(dest)

        if dry_run:
            # Pure simulation: compute the plan, touch NOTHING.
            names = self.nas.dest_names(item, item.version, code, tmpl)
            copied = [str(dest / names.get(f, os.path.basename(f))) for f in item.source_files]
            checksum = item.hashes.get(item.source_files[0], "") if item.source_files else ""
            item.ingest_result = {
                "dest_dir": str(dest), "files": copied, "checksum": checksum,
                "preview_id": "", "kitsu_shot_id": "", "dry_run": True,
            }
            item.ingested = False
            self._set_stage(item, Stage.DONE, 100)
            self._emit("item_updated", item=item)
            return None

        # 1. Kitsu shot / tasks
        self._set_stage(item, Stage.KITSU_SHOT, 5)
        shot = self.recorder.ensure_shot(self.project, item)
        self.recorder.ensure_tasks(shot, self.config.task_types)

        # 2. Folders -- only when the shot dir doesn't exist yet
        self._set_stage(item, Stage.FOLDERS, 12)
        if not dest.exists():
            self.nas.create_shot_structure(dest, structure=self.config.shot_folder_structure or None)

        # 3. Copy (+ verify-on-write against the pre-flight source hash)
        self._set_stage(item, Stage.COPYING, 18)

        def _cp(cdone, ctotal, _name):
            self._set_stage(item, Stage.COPYING, 18 + (int((cdone / ctotal) * 60) if ctotal else 60))

        copied = self.nas.copy_sequence(
            item, dest, filename_template=tmpl, version_num=item.version,
            proj_code=code, progress_callback=_cp,
            pool=copy_pool, hasher=self.hasher, source_hashes=item.hashes,
        )

        self._set_stage(item, Stage.VERIFYING, 82)
        checksum = item.hashes.get(item.source_files[0], "") if item.source_files else ""
        pmeta = self._build_preview_meta(item, dest, copied, checksum)

        # 4. Kitsu version record (comment + task->Done + shot.data entry). No preview here.
        self._set_stage(item, Stage.METADATA, 90)
        outcome = self.recorder.record_version(
            self.project, item, pmeta, task_types=self.config.task_types
        )

        # 5. NAS ledger
        self._write_ledger(item, dest, copied)

        item.ingest_result = {
            "dest_dir": str(dest), "files": copied, "checksum": checksum,
            "preview_id": "", "kitsu_shot_id": outcome.shot_id, "dry_run": False,
        }
        item.ingested = True

        # 6. Preview -- OFF the critical path. Row is Completed now.
        want = (item.preview_wanted
                and _wants_preview(item.media_type, self.config.preview_media_types)
                and self.proxy_generator is not None)
        preview_fut = None
        if want and not self._cancel.is_set():
            item.preview_state = "pending"
            self._preview_outcomes[item.key] = (outcome, pmeta)
            preview_fut = self._preview_pool.submit(self._run_preview, item.key)
            self._preview_futures[item.key] = preview_fut
        else:
            item.preview_state = "skipped" if not want else ""

        self._set_stage(item, Stage.DONE, 100)
        self._emit("item_updated", item=item)
        return preview_fut

    def _run_preview(self, key: str) -> None:
        item = self._by_key.get(key)
        stash = self._preview_outcomes.pop(key, None)
        if not item or not stash or self._cancel.is_set():
            if item:
                item.preview_state = "skipped"
                self._emit("item_updated", item=item)
            return
        outcome, pmeta = stash
        item.preview_state = "running"
        self._emit("item_updated", item=item)
        try:
            path = self.proxy_generator.generate_proxy(item)
            preview_id = self.recorder.attach_preview(outcome, item, pmeta, path) if path else ""
            item.preview_state = "done" if preview_id else "failed"
            if preview_id:
                item.ingest_result["preview_id"] = preview_id
        except Exception as e:
            logger.warning("[IngestController] preview failed for %s: %s", key, e)
            item.preview_state = "failed"
        finally:
            self._preview_futures.pop(key, None)
            self._emit("item_updated", item=item)

    def requeue_pending_previews(self) -> None:
        """
        On a resumed session, re-run previews for rows that ingested but
        never got their proxy (pending / running / failed when the session
        was saved). The version was already recorded last run, so this only
        re-resolves the task handles and attaches.
        """
        self._cancel.clear()
        pending = [
            it for it in self.items
            if it.ingested and it.preview_state in ("pending", "running", "failed")
            and it.preview_wanted
            and _wants_preview(it.media_type, self.config.preview_media_types)
            and self.proxy_generator is not None
        ]
        if not pending:
            return
        for it in pending:
            it.preview_state = "pending"
            self._emit("item_updated", item=it)
        futs = []
        for it in pending:
            futs.append(self._preview_pool.submit(self._resume_preview, it.key))
        threading.Thread(target=self._await_previews, args=(futs,), daemon=True).start()

    def _resume_preview(self, key: str) -> None:
        item = self._by_key.get(key)
        if not item or self._cancel.is_set():
            return
        try:
            outcome = self.recorder.resolve_ingest_task(
                self.project, item, self.config.task_types
            )
            dest = Path(item.dest_dir) if item.dest_dir else None
            pmeta = self._build_preview_meta(
                item, dest or Path("."), item.ingest_result.get("files", []),
                item.ingest_result.get("checksum", ""),
            )
            self._preview_outcomes[key] = (outcome, pmeta)
        except Exception as e:
            logger.warning("[IngestController] resume-preview setup failed for %s: %s", key, e)
            item.preview_state = "failed"
            self._emit("item_updated", item=item)
            return
        self._run_preview(key)

    def shutdown(self) -> None:
        """Stop the preview pool -- call on app close."""
        self._cancel.set()
        self._preview_pool.shutdown(wait=False)

    def _build_preview_meta(self, item, dest, copied, checksum) -> PreviewMetadata:
        src0 = item.source_files[0] if item.source_files else ""
        dst0 = copied[0] if copied else ""
        return PreviewMetadata(
            source_path=os.path.dirname(src0),
            source_sample_file=os.path.basename(src0),
            nas_path=str(dest),
            nas_sample_file=os.path.basename(dst0),
            frame_range=item.frame_range_str,
            file_count=len(item.source_files),
            fps=item.fps,
            resolution=item.resolution,
            colorspace=item.colorspace,
            checksum=checksum,
            checksum_algo=self.hasher.algo,
            transfer_mode=self.config.transfer_mode,
            sequence_code=item.sequence_code,
            shot_code=item.shot_code,
            media_type=item.media_type,
            media_name=item.media_name,
            version=item.version,
            ingested_at=_utcnow(),
            ingested_by=self.config.ingested_by,
            batch_id=self.batch_id,
        )

    def _write_ledger(self, item, dest, copied) -> None:
        from square_core.ingest_ledger import LedgerRecord
        names = self.nas.dest_names(item, item.version, self.config.project_code,
                                    self.config.filename_template)
        now = _utcnow()
        recs = []
        for src in item.source_files:
            h = item.hashes.get(src)
            if not h:
                continue
            dest_file = str(dest / names.get(src, os.path.basename(src)))
            try:
                size = os.path.getsize(src)
            except OSError:
                size = 0
            recs.append(LedgerRecord(
                file_hash=h, hash_algo=self.hasher.algo, size=size,
                src_path=src, dest_path=dest_file, batch_id=self.batch_id,
                ingested_at=now, seq=item.sequence_code, shot=item.shot_code,
                media_type=item.media_type, media_name=item.media_name,
                version=item.version, ingested_by=self.config.ingested_by,
            ))
        if recs:
            self.ledger.record(recs)

    # ------------------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for it in self.items:
            counts[it.status.value] = counts.get(it.status.value, 0) + 1
        return counts

    def _resolve_targets(self, keys) -> list[IngestItem]:
        if keys is None:
            return list(self.items)
        want = set(keys)
        return [i for i in self.items if i.key in want]
