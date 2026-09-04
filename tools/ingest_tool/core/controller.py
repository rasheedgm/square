"""IngestController -- owns the item list and runs the two pieces of work
(pre-flight, ingest) as stage sequences over a thread pool.

Ported onto the pipeline core: every write (folders, copy, Kitsu record,
review proxy) goes through ONE call, `services.media.publish(..., source=
"delivery")` -- the same call a DCC publish uses. `services.breakdown`
creates the shot + tasks; `services.media` does everything after that.
`PathResolver`/`pctx.paths` decide the path; Kitsu decides the version
number (`config_and_paths.md`, `decisions.md`).

Framework-agnostic: no Qt. It emits ControllerEvents through subscribers;
the UI layer subscribes one function that marshals them onto the main
thread and updates the view. Everything the controller needs beyond the
`ProjectContext` (the ledger, hashing, metadata extraction) is injected, so
the whole flow is unit-testable with fakes.

Simplification from the pre-port controller, worth knowing: slot/version
conflict detection now reads Kitsu's existing output_files for the shot
(read-only -- preflight never creates anything in Kitsu, same as before)
instead of inspecting NAS folders directly. If the shot doesn't exist in
Kitsu yet, there is nothing to conflict with; the ledger's own
content-hash duplicate check still catches a re-delivery either way.
"""

from __future__ import annotations

import os
import uuid
import logging
import datetime
import threading
from dataclasses import dataclass, field
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from square_core.hashing import FileHasher
from square_core.services import breakdown, media as media_service

from . import preflight
from .item import IngestItem, Stage, Action, IssueKind, Status

logger = logging.getLogger("SquareIngestController")


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ControllerEvent:
    kind: str
    item: IngestItem | None = None
    payload: dict = field(default_factory=dict)


_NON_VISUAL = {"audio", "lut"}


def _wants_preview(pctx, media_type: str) -> bool:
    m = (media_type or "").strip().lower()
    if m in _NON_VISUAL:
        return False
    try:
        return bool(pctx.paths.media_entry(media_type).get("previewable"))
    except Exception:
        return False


class IngestController:
    def __init__(self, pctx, *, ledger, task_types, hasher: FileHasher | None = None,
                 extractor=None, ingested_by: str = "", ingest_task_status: str = "Done"):
        self.pctx = pctx
        self.ledger = ledger
        self.task_types = list(task_types or [])
        self.hasher = hasher or FileHasher()
        self.extractor = extractor
        self.ingested_by = ingested_by or getattr(pctx.pipeline.user, "email", "")
        self.ingest_task_status = ingest_task_status

        self.items: list[IngestItem] = []
        self._by_key: dict[str, IngestItem] = {}
        self.batch_id = str(uuid.uuid4())

        self._listeners = []
        self._cancel = threading.Event()
        self._undo: list[dict] = []
        self._scanned: set[str] = set()          # keys whose metadata+hashes are done
        self._slot_state: dict[str, tuple] = {}   # key -> (state, detail) from last Kitsu inspect
        self._shot_cache: dict[str, object] = {}  # sequence/shot code -> Shot | None (this batch)

        # Preview encode + upload runs OFF the ingest critical path, same as
        # before the port: the row goes Completed the moment files are
        # verified + Kitsu has the version; previews trickle in behind on
        # this small pool. media.publish(preview_pool=...) submits to it.
        self._preview_pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ingest-preview"
        )

    @property
    def known_media_types(self) -> list:
        return self.pctx.config.media_type_names(source="delivery")

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
            item.preview_default = _wants_preview(self.pctx, item.media_type)
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
    # Pre-flight (read-only: never creates anything in Kitsu)
    # ------------------------------------------------------------------

    def run_preflight(self, keys=None) -> None:
        self._cancel.clear()
        targets = self._resolve_targets(keys)
        if not targets:
            return
        # a shot ingested since the last preflight (this run or an earlier
        # one) must be visible to THIS run's slot check -- a cache that
        # outlives one run would keep serving a stale "doesn't exist yet"
        self._shot_cache.clear()
        self._emit("preflight_started", payload={"keys": [i.key for i in targets]})

        for it in targets:
            it.preflight_done = False
            it.check_error = ""
            self._emit("item_updated", item=it)

        workers = max(1, min(self.pctx.config.copy_workers, len(targets)))
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
        """Parallel-safe per-item work: metadata probe, hashing, dest, Kitsu slot."""
        if self._cancel.is_set():
            return
        if item.key not in self._scanned:
            item.probe_metadata(self.extractor)
            if item.source_files:
                item.hashes = self.hasher.hash_files(item.source_files)
            self._scanned.add(item.key)
        self._recheck_one(item)
        item.preflight_done = True

    def _find_shot(self, sequence_code: str, shot_code: str):
        """Read-only shot lookup, cached per batch. Preflight must never
        create a shot -- that's ensure_shot's job, at real ingest time only."""
        key = f"{sequence_code}/{shot_code}".lower()
        if key not in self._shot_cache:
            shot = None
            try:
                for s in self.pctx.kitsu.shots(self.pctx.project):
                    if (getattr(s, "code", "") or "").lower() == shot_code.lower():
                        shot = s
                        break
            except Exception:
                shot = None
            self._shot_cache[key] = shot
        return self._shot_cache[key]

    def _recheck_one(self, item: IngestItem) -> None:
        """Cheap re-evaluation after an edit: dest path + Kitsu slot state. No re-hash."""
        if not item.source_files or not item.sequence_code or not item.shot_code:
            item.dest_dir = ""
            item.sample_dest_file = ""
            self._slot_state[item.key] = (preflight.SLOT_EMPTY, "")
            item.slot_state = preflight.SLOT_EMPTY
            return

        ext = Path(item.source_files[0]).suffix.lstrip(".")
        entry = self.pctx.paths.media_entry(item.media_type)
        rep = entry.get("representation") or ext
        ctx = self.pctx.ctx(sequence=item.sequence_code, shot=item.shot_code,
                            name=item.media_name or "main", version=item.version,
                            representation=rep, ext=ext, frame=item.start_frame or None)
        try:
            item.dest_dir = self.pctx.paths.media_dir(item.media_type, ctx)
            item.sample_dest_file = str(
                Path(item.dest_dir) / os.path.basename(item.source_files[0])
            )
        except Exception as e:
            item.dest_dir = ""
            item.sample_dest_file = ""
            item.check_error = str(e)

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

        state, detail = self._inspect_slot(item)
        self._slot_state[item.key] = (state, detail)
        item.slot_state = state

    def _inspect_slot(self, item: IngestItem) -> tuple[str, str]:
        shot = self._find_shot(item.sequence_code, item.shot_code)
        if shot is None:
            return preflight.SLOT_EMPTY, ""
        name = item.media_name or "main"
        try:
            existing = [o for o in self.pctx.kitsu.output_files(shot, output_type_name=item.media_type)
                       if o.name == name and o.revision == item.version]
        except Exception:
            return preflight.SLOT_EMPTY, ""
        if not existing:
            return preflight.SLOT_EMPTY, ""
        rec = existing[0]
        current_hash = item.hashes.get(item.source_files[0], "") if item.hashes else ""
        recorded_hash = ((rec.data or {}).get("square") or {}).get("checksum", "")
        if current_hash and recorded_hash and current_hash == recorded_hash:
            return preflight.SLOT_ALREADY, (
                f"v{item.version:03d} already holds exactly this content."
            )
        return preflight.SLOT_CONFLICT, (
            f"v{item.version:03d} already exists in Kitsu with different content. "
            f"Version up, or overwrite it."
        )

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
                known_media_types=self.known_media_types or None,
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
            item.preview_default = _wants_preview(self.pctx, item.media_type)
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
            item.version += 1
            while self._inspect_slot(item)[0] != preflight.SLOT_EMPTY:
                item.version += 1
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

    def run_ingest(self, keys=None, *, dry_run=False, transfer_mode: str = "copy") -> dict:
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

        cw = max(1, self.pctx.config.copy_workers)
        item_workers = max(1, min(cw, len(targets)))
        done = failed = 0
        preview_futs = []

        # One shared pool for EVERY frame transfer in the batch: total
        # concurrent file copies == cw, no matter how many items overlap.
        # media.publish(pool=copy_pool, progress=...) shares it.
        with ThreadPoolExecutor(max_workers=cw, thread_name_prefix="ingest-copy") as copy_pool, \
             ThreadPoolExecutor(max_workers=item_workers, thread_name_prefix="ingest-item") as item_pool:
            futs = {item_pool.submit(self._ingest_core, it, dry_run, transfer_mode, copy_pool): it
                   for it in targets}
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
    # Core ingest (critical path: files safe on disk + Kitsu has the version)
    # ------------------------------------------------------------------

    def _ingest_core(self, item: IngestItem, dry_run: bool, transfer_mode: str, copy_pool):
        if self._cancel.is_set():
            return None

        if dry_run:
            entry = self.pctx.paths.media_entry(item.media_type)
            ext = Path(item.source_files[0]).suffix.lstrip(".")
            rep = entry.get("representation") or ext
            ctx = self.pctx.ctx(sequence=item.sequence_code, shot=item.shot_code,
                                name=item.media_name or "main", version=item.version,
                                representation=rep, ext=ext, frame=item.start_frame or None)
            dest_dir = self.pctx.paths.media_dir(item.media_type, ctx)
            copied = [str(Path(dest_dir) / os.path.basename(f)) for f in item.source_files]
            checksum = item.hashes.get(item.source_files[0], "") if item.source_files else ""
            item.dest_dir = dest_dir
            item.ingest_result = {
                "dest_dir": dest_dir, "files": copied, "checksum": checksum,
                "preview_id": "", "kitsu_shot_id": "", "dry_run": True,
            }
            item.ingested = False
            self._set_stage(item, Stage.DONE, 100)
            self._emit("item_updated", item=item)
            return None

        # 1. Kitsu shot / tasks
        self._set_stage(item, Stage.KITSU_SHOT, 5)
        shot = breakdown.ensure_shot(
            self.pctx, item.sequence_code, item.shot_code,
            frame_in=item.start_frame, frame_out=item.end_frame,
            fps=item.fps, create_folders=True,
        )
        tasks = breakdown.build_task_grid(self.pctx, [shot], self.task_types)
        ingest_task = self.pctx.kitsu.ingest_task(tasks) if tasks else None
        if ingest_task is None:
            raise RuntimeError(f"no task on {item.shot_code} to record the ingest against")

        # 2-5. Folders + copy (verified, progress-tracked) + Kitsu record + ledger
        self._set_stage(item, Stage.COPYING, 18)

        def _cp(cdone, ctotal):
            self._set_stage(item, Stage.COPYING, 18 + (int((cdone / ctotal) * 60) if ctotal else 60))

        from square_core.model import MediaInfo
        media_info = MediaInfo(fps=item.fps, resolution=item.resolution, colorspace=item.colorspace)

        result = media_service.publish(
            self.pctx, shot, item.media_type, ingest_task,
            files=item.source_files, name=item.media_name or "main", version=item.version,
            media_info=media_info, transfer_mode=transfer_mode,
            pool=copy_pool, progress=_cp,
            make_review_proxy=(item.preview_wanted if not self._cancel.is_set() else False),
            preview_pool=self._preview_pool,
            comment=f"Ingested by {self.ingested_by}" if self.ingested_by else "",
        )
        item.dest_dir = result.dir

        self._set_stage(item, Stage.METADATA, 90)
        self.pctx.kitsu.set_status(ingest_task, self.ingest_task_status)
        if self.ingest_task_status:
            self.pctx.kitsu.comment(ingest_task, f"Ingested {item.media_type} '{item.media_name}'")

        checksum = next(iter(result.checksums.values()), "") if result.checksums else ""
        self._write_ledger(item, result.dir, result.files, checksum)

        item.ingest_result = {
            "dest_dir": result.dir, "files": result.files, "checksum": checksum,
            "preview_id": getattr(result.preview, "id", ""),
            "kitsu_shot_id": getattr(shot, "id", ""), "dry_run": False,
        }
        item.ingested = True

        preview_fut = result.preview_future
        item.preview_state = "pending" if preview_fut is not None else (
            "" if not item.preview_wanted else "skipped")

        self._set_stage(item, Stage.DONE, 100)
        self._emit("item_updated", item=item)
        return preview_fut

    def _write_ledger(self, item, dest_dir, dest_files, checksum) -> None:
        from .ledger import LedgerRecord
        now = _utcnow()
        recs = []
        for src, dst in zip(item.source_files, dest_files):
            h = item.hashes.get(src)
            if not h:
                continue
            try:
                size = os.path.getsize(src)
            except OSError:
                size = 0
            recs.append(LedgerRecord(
                file_hash=h, hash_algo=self.hasher.algo, size=size,
                src_path=src, dest_path=dst, batch_id=self.batch_id,
                ingested_at=now, seq=item.sequence_code, shot=item.shot_code,
                media_type=item.media_type, media_name=item.media_name,
                version=item.version, ingested_by=self.ingested_by,
            ))
        if recs:
            self.ledger.record(recs)

    # ------------------------------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()

    def shutdown(self) -> None:
        """Stop the preview pool -- call on app close."""
        self._cancel.set()
        self._preview_pool.shutdown(wait=False)

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
