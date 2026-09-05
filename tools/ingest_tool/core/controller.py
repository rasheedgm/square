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
import re
import uuid
import shutil
import tempfile
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
                 extractor=None, converter=None, ingested_by: str = "",
                 ingest_task_status: str = "Done", transfer_mode: str = "copy"):
        self.pctx = pctx
        self.ledger = ledger
        self.task_types = list(task_types or [])
        self.transfer_mode = transfer_mode or "copy"
        self.hasher = hasher or FileHasher()
        self.extractor = extractor
        if converter is None:
            from square_core.media.convert import video_to_exr_sequence as converter
        self.converter = converter
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
            item.original_values = {a: getattr(item, a) for a in self.RENAMEABLE_ATTRS}
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
            item.check_error = ""
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

    def set_convert_to_exr(self, key, wanted: bool) -> None:
        item = self._by_key[key]
        self._push_undo("toggle convert to EXR", [key])
        item.convert_to_exr = bool(wanted)
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
            # A slot-scoped issue (dest-exists-diff / already-in-slot, both
            # tagged column="version") was about the OLD version's slot --
            # drop it so it can't linger once we're sitting on a fresh one.
            # A ledger-scoped issue (duplicate-content, partial-overlap) is
            # a fact about this content's HASH, not this slot -- it stays
            # true at the new version too, so its own resolution must stay
            # resolved or the exact same warning reappears unresolved right
            # after "fixing" it (confirmed bug: Version Up left the row
            # stuck on Warning forever for a ledger-duplicate).
            if issue_id.endswith(":version"):
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
    # Batch rename
    # ------------------------------------------------------------------

    RENAME_FIELDS = {
        "sequence": "sequence_code", "shot": "shot_code",
        "media_type": "media_type", "media_name": "media_name",
    }

    # Every field a rename/set-value action can target, attribute name
    # directly (matches review_table.py's _EDIT_FIELD column mapping).
    RENAMEABLE_ATTRS = (
        "sequence_code", "shot_code", "media_type", "media_name",
        "fps", "resolution", "colorspace", "version",
    )

    RENAME_TOKENS = ("project", "sequence", "shot", "media_type", "media_name",
                     "current", "original", "source", "version", "date")

    # {token} or {token:modifier} -- e.g. {shot:upper}. An unrecognized
    # modifier just falls back to the plain value rather than erroring, so a
    # typo shows up as "not what I typed" in the live preview, not a crash.
    _RENAME_TOKEN_RE = re.compile(r"\{(\w+)(?::(\w+))?\}")
    _RENAME_CASE_MODIFIERS = {
        "upper": str.upper, "lower": str.lower, "title": str.title,
        "capitalize": str.capitalize,
    }

    @staticmethod
    def _rename_str(value) -> str:
        return "" if value is None else str(value)

    def resolve_rename_template(self, item: IngestItem, template: str, attr: str = None) -> str:
        """Substitute every {token} (optionally {token:upper|lower|title|
        capitalize}) in `template` with this item's own values -- the same
        resolution `rename_cells`/`rename_batch` apply, exposed for a live
        preview to call without mutating anything.

        `attr` is the specific field this resolution is FOR (e.g. "shot_code"
        when renaming the Shot cell) -- it's what {current} and {original}
        resolve against, so the same template means "whatever this cell's own
        value is" no matter which column it's applied to. Left blank (no
        `attr`) they resolve empty rather than erroring."""
        today = datetime.date.today().strftime("%Y%m%d")
        values = {
            "project": self.pctx.code or "",
            "sequence": item.sequence_code or "",
            "shot": item.shot_code or "",
            "media_type": item.media_type or "",
            "media_name": item.media_name or "",
            # the file/folder name the scanner grouped this item under at
            # discovery, e.g. "plate" from "plate.1001.exr" -- fixed forever,
            # independent of any field's own value or edits.
            "source": item.source_name or "",
            # this cell's value right now, and as it was the moment this row
            # first entered the table -- e.g. renaming the Shot cell with
            # "{original}_{current}" when it loaded as "Fgt10" and hasn't
            # been touched since renders "Fgt10_Fgt10"; after an edit,
            # {original} still says "Fgt10" while {current} follows the edit.
            "current": self._rename_str(getattr(item, attr, "")) if attr else "",
            "original": self._rename_str(item.original_values.get(attr, "")) if attr else "",
            "version": f"v{item.version:03d}",
            "date": today,
        }

        def _sub(m: re.Match) -> str:
            name, modifier = m.group(1), m.group(2)
            if name not in values:
                return m.group(0)   # not a token we know -- leave it literal
            value = values[name]
            fn = self._RENAME_CASE_MODIFIERS.get(modifier)
            return fn(value) if fn else value

        return self._RENAME_TOKEN_RE.sub(_sub, template)

    def rename_cells(self, cell_targets, template: str) -> int:
        """Apply a token template to a set of specific (key, attr) cells --
        one undo entry for the whole batch, not one per cell. `attr` is an
        IngestItem attribute name (RENAMEABLE_ATTRS). fps/version are coerced
        to their numeric type; a cell whose resolved value doesn't coerce is
        left untouched rather than failing the whole batch. Returns the
        number of cells actually changed."""
        template = (template or "").strip()
        pairs = [(self._by_key[k], attr) for k, attr in cell_targets
                if k in self._by_key and attr in self.RENAMEABLE_ATTRS]
        if not pairs or not template:
            return 0

        keys = list(dict.fromkeys(item.key for item, _ in pairs))
        self._push_undo(f"rename {len(keys)} row(s)", keys)
        changed = 0
        for item, attr in pairs:
            value = self.resolve_rename_template(item, template, attr)
            if attr == "fps":
                try:
                    value = float(value)
                except ValueError:
                    continue
            elif attr == "version":
                try:
                    value = int(value)
                except ValueError:
                    continue
            setattr(item, attr, value)
            if attr in ("fps", "resolution", "colorspace"):
                item.metadata_verified[attr] = True
            if attr == "media_type":
                item.preview_default = _wants_preview(self.pctx, item.media_type)
                if not item.preview_user_set:
                    item.preview_wanted = item.preview_default
            self._recheck_one(item)
            changed += 1
        self._reassemble_all()
        return changed

    def rename_batch(self, keys, field_name: str, template: str) -> int:
        """Apply a token template to one field across every item in `keys`.
        `field_name` is one of RENAME_FIELDS' keys (matching the Path
        Pattern display names, so the same vocabulary the studio already
        tags with also renames with). Tokens: {sequence} {shot} {media_type}
        {media_name} {version} {original} {project} {date}. Returns the
        number of rows renamed."""
        attr = self.RENAME_FIELDS.get(field_name)
        if attr is None:
            raise ValueError(f"not a renameable field: {field_name}")
        return self.rename_cells([(k, attr) for k in keys], template)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingestable_items(self, keys=None) -> list[IngestItem]:
        pool = self._resolve_targets(keys)
        return [i for i in pool if i.ingestable]

    def run_ingest(self, keys=None, *, dry_run=False, transfer_mode: str = None) -> dict:
        transfer_mode = transfer_mode or self.transfer_mode
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

    def run_pending_previews(self) -> None:
        """Resume: re-attempt the review proxy for rows that ingested last
        run but whose proxy never landed -- the session was saved while it
        was still pending/running, or it failed. The Kitsu version already
        exists; this only re-encodes + re-uploads the MP4."""
        self._cancel.clear()
        # a stale per-preflight shot cache (or none at all on a bare resume)
        # would make _find_shot miss a shot that really does exist now
        self._shot_cache.clear()
        pending = [
            it for it in self.items
            if it.ingested and it.preview_wanted
            and it.preview_state in ("pending", "running", "failed")
            and _wants_preview(self.pctx, it.media_type)
        ]
        if not pending:
            return
        futs = []
        for it in pending:
            it.preview_state = "pending"
            self._emit("item_updated", item=it)
            futs.append(self._preview_pool.submit(self._resume_one_preview, it.key))
        threading.Thread(target=self._await_previews, args=(futs,), daemon=True,
                        name="ingest-preview-resume").start()

    def _resume_one_preview(self, key: str) -> None:
        item = self._by_key.get(key)
        if item is None or self._cancel.is_set():
            return
        item.preview_state = "running"
        self._emit("item_updated", item=item)
        try:
            shot = self._find_shot(item.sequence_code, item.shot_code)
            if shot is None:
                raise RuntimeError(f"shot {item.shot_code!r} not found in Kitsu")
            tasks = breakdown.build_task_grid(self.pctx, [shot], self.task_types)
            ingest_task = self.pctx.kitsu.ingest_task(tasks) if tasks else None
            if ingest_task is None:
                raise RuntimeError("no ingest task to attach the preview to")
            from square_core.model import MediaInfo
            media_info = MediaInfo(fps=item.fps, resolution=item.resolution,
                                   colorspace=item.colorspace)
            files = item.source_files or item.ingest_result.get("files", [])
            preview = media_service.make_review_proxy_for(
                self.pctx, shot, item.media_type, ingest_task,
                files=files, name=item.media_name or "main", version=item.version,
                media_info=media_info, dest_dir=item.dest_dir,
            )
            item.preview_state = "done" if preview else "failed"
            if preview:
                item.ingest_result["preview_id"] = getattr(preview, "id", "")
        except Exception as e:
            logger.warning("[IngestController] resume preview failed for %s: %s", key, e)
            item.preview_state = "failed"
        finally:
            self._emit("item_updated", item=item)

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
            converting = item.is_video and item.convert_to_exr
            entry = self.pctx.paths.media_entry(item.media_type)
            ext = "exr" if converting else Path(item.source_files[0]).suffix.lstrip(".")
            rep = entry.get("representation") or ext
            ctx = self.pctx.ctx(sequence=item.sequence_code, shot=item.shot_code,
                                name=item.media_name or "main", version=item.version,
                                representation=rep, ext=ext, frame=item.start_frame or None)
            dest_dir = self.pctx.paths.media_dir(item.media_type, ctx)
            if converting:
                stem = Path(item.source_files[0]).stem
                copied = [str(Path(dest_dir) / f"{stem}.{item.start_frame or 1001:04d}.exr")]
            else:
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

        # 0. Video -> EXR sequence, if the user opted in. Runs before the
        # shot even exists, so a bad delivery fails before touching Kitsu.
        scratch_dir = None
        files_to_publish = item.source_files
        if item.is_video and item.convert_to_exr:
            self._set_stage(item, Stage.CONVERTING, 2)
            scratch_dir = tempfile.mkdtemp(prefix="ingest_exr_")
            try:
                converted = self.converter(
                    item.source_files[0], scratch_dir, start_frame=item.start_frame or 1001,
                )
            except Exception:
                shutil.rmtree(scratch_dir, ignore_errors=True)
                raise
            if not converted:
                shutil.rmtree(scratch_dir, ignore_errors=True)
                raise RuntimeError("video-to-EXR conversion produced no frames")
            files_to_publish = converted

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
            files=files_to_publish, name=item.media_name or "main", version=item.version,
            media_info=media_info, transfer_mode=transfer_mode,
            pool=copy_pool, progress=_cp,
            make_review_proxy=(item.preview_wanted if not self._cancel.is_set() else False),
            preview_pool=self._preview_pool,
            comment=f"Ingested by {self.ingested_by}" if self.ingested_by else "",
        )
        item.dest_dir = result.dir

        if scratch_dir:
            # The review proxy (if any) reads straight from `files_to_publish`
            # on its own pool, off this call's critical path -- clean up the
            # scratch frames once that job (if pending) is done with them,
            # not before.
            if result.preview_future is not None:
                result.preview_future.add_done_callback(
                    lambda _f, d=scratch_dir: shutil.rmtree(d, ignore_errors=True))
            else:
                shutil.rmtree(scratch_dir, ignore_errors=True)

        self._set_stage(item, Stage.METADATA, 90)
        # ONE comment that both says what happened and moves the task -- was
        # a set_status("Done") followed by a plain comment(), but that
        # comment (and the async review-proxy comment) were built from the
        # pre-"Done" task object and each flipped the task back to "Todo".
        note = f"Ingested {item.media_type} '{item.media_name}'"
        if self.ingest_task_status:
            self.pctx.kitsu.set_status(ingest_task, self.ingest_task_status, comment=note)
        else:
            self.pctx.kitsu.comment(ingest_task, note)

        checksum = next(iter(result.checksums.values()), "") if result.checksums else ""
        self._write_ledger(item, result.dir, result.files, checksum)

        item.ingest_result = {
            "dest_dir": result.dir, "files": result.files, "checksum": checksum,
            "preview_id": getattr(result.preview, "id", ""),
            "kitsu_shot_id": getattr(shot, "id", ""), "dry_run": False,
        }
        item.ingested = True

        preview_fut = result.preview_future
        if preview_fut is not None:
            item.preview_state = "pending"
            # media.publish encodes + uploads the proxy on its own pool and
            # hands back a Future -- but nothing downstream was consuming its
            # result, so the row sat on "pending" forever even after the
            # proxy landed in Kitsu. Fold the outcome back onto the item.
            def _finish_preview(fut, it=item):
                try:
                    pv = fut.result()
                except Exception:
                    pv = None
                it.preview_state = "done" if pv else "failed"
                if pv:
                    it.ingest_result["preview_id"] = getattr(pv, "id", "")
                self._emit("item_updated", item=it)
            preview_fut.add_done_callback(_finish_preview)
        else:
            item.preview_state = "" if not item.preview_wanted else "skipped"

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
