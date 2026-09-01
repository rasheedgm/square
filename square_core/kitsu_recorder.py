"""
KitsuRecorder -- the one place that writes an ingested version into Kitsu.

Everything the ingest flow does to Kitsu goes through here: ensure the
sequence/shot/tasks exist, attach the preview (or a text comment when there
is none), stamp the PreviewMetadata onto the preview file, and append a
version entry to the shot's data ledger.

Why it's isolated behind a gateway:

- The Kitsu representation of "an ingested version" is expected to change
  (shot.data blob + task comments today; gazu output-files / asset
  instances later). Keeping all of it in this one class, talking to a
  small gateway interface, means that change touches nothing else.
- It makes the whole thing unit-testable with a fake gateway -- no gazu, no
  server.

The gateway interface (see GazuKitsuGateway for the real implementation):

    get_or_create_sequence(project, name)            -> seq dict
    get_or_create_shot(project, seq, name, **fields) -> shot dict (carries "data")
    ensure_tasks(shot, task_type_names)              -> [task dict]  (idempotent)
    add_comment(task, text)                          -> comment dict
    upload_preview(task, text, file_path)            -> preview-file dict
    set_main_preview(preview)                        -> None
    get_preview_data(preview_id)                     -> dict (the preview file's `data`)
    update_preview_data(preview, data)               -> preview-file dict
    update_shot_data(shot, data)                     -> shot dict
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from square_core.preview_metadata import PreviewMetadata

logger = logging.getLogger("SquareKitsuRecorder")

# Which task the ingest preview/comment lands on, first match wins.
INGEST_TASK_PREFERENCE = ("Ingest", "Prep")


@dataclass
class RecordOutcome:
    shot_id: str = ""
    sequence_id: str = ""
    task_id: str = ""
    task_name: str = ""
    preview_id: str = ""
    comment_id: str = ""
    task_status: str = ""
    has_preview: bool = False
    dry_run: bool = False
    skipped_reason: str = ""
    task_ids: list = field(default_factory=list)
    # runtime handles so a deferred attach_preview() needn't re-fetch
    shot: dict | None = None
    ingest_task: dict | None = None


def default_comment(item, preview_meta: PreviewMetadata) -> str:
    lines = [
        f"Media Ingest v{item.version:03d} — {item.media_name}",
        "",
        f"Type: {item.media_type or 'Plate'}",
        f"NAS: {preview_meta.nas_path}",
        f"Source: {preview_meta.source_path}",
        f"Frames: {preview_meta.frame_range}  ·  {preview_meta.file_count} file(s)",
        f"Res: {preview_meta.resolution}  ·  FPS: {preview_meta.fps}  ·  CS: {preview_meta.colorspace}",
        f"Transfer: {preview_meta.transfer_mode}",
    ]
    if preview_meta.checksum:
        lines.append(f"Checksum ({preview_meta.checksum_algo}, first file): {preview_meta.checksum}")
    if item.extra_tags:
        lines.append("Tags: " + ", ".join(f"{k}={v}" for k, v in item.extra_tags.items()))
    return "\n".join(lines)


def _task_type_name(t: dict) -> str:
    """The task's TYPE name (Ingest/Comp/...), not its own name (Kitsu calls the first one 'main')."""
    if t.get("task_type_name"):
        return t["task_type_name"]
    tt = t.get("task_type")
    if isinstance(tt, dict) and tt.get("name"):
        return tt["name"]
    return t.get("name") or ""


def _pick_ingest_task(tasks: list[dict]) -> dict | None:
    if not tasks:
        return None
    by_type = {}
    for t in tasks:
        by_type.setdefault(_task_type_name(t), t)
    for pref in INGEST_TASK_PREFERENCE:
        if pref in by_type:
            return by_type[pref]
    return tasks[0]


class KitsuRecorder:
    def __init__(self, gateway, *, dry_run: bool = False, ingested_by: str = ""):
        self.gw = gateway
        self.dry_run = dry_run
        self.ingested_by = ingested_by
        # Task status to move the Ingest task to once the media is in. "" or
        # None leaves the status untouched.
        self.ingest_task_status = "Done"

    # ------------------------------------------------------------------

    def ensure_shot(self, project, item) -> dict:
        """Sequence + shot exist; returns the shot dict (with its 'data')."""
        seq = self.gw.get_or_create_sequence(project, item.sequence_code)
        nb = (item.end_frame - item.start_frame + 1) if not item.is_video else 1
        shot = self.gw.get_or_create_shot(
            project, seq, item.shot_code,
            frame_in=item.start_frame,
            frame_out=item.end_frame,
            fps=item.fps,
            nb_frames=nb,
        )
        # stash the seq id where the caller can see it
        if isinstance(shot, dict):
            shot.setdefault("_sequence_id", seq.get("id") if isinstance(seq, dict) else "")
        return shot

    def ensure_tasks(self, shot, task_types) -> list[dict]:
        return self.gw.ensure_tasks(shot, list(task_types or []))

    # ------------------------------------------------------------------

    def record_version(
        self,
        project,
        item,
        preview_meta: PreviewMetadata,
        *,
        task_types,
        comment_text: str | None = None,
    ) -> RecordOutcome:
        """
        Record one ingested version in Kitsu -- the part that must happen the
        moment the media is safely on the NAS: ensure the sequence/shot/tasks,
        post the self-describing comment, move the Ingest task to Done, and
        write the version's ledger entry into shot.data.

        The preview (encode + upload) is deliberately NOT here -- it's slow
        and non-critical. Call attach_preview() with this outcome once the
        proxy exists.

        dry_run: does nothing, returns an outcome flagged dry_run=True.
        """
        if self.dry_run:
            return RecordOutcome(dry_run=True)

        out = RecordOutcome()

        shot = self.ensure_shot(project, item)
        out.shot = shot if isinstance(shot, dict) else None
        out.shot_id = str(shot.get("id", "")) if isinstance(shot, dict) else str(shot)
        out.sequence_id = shot.get("_sequence_id", "") if isinstance(shot, dict) else ""

        tasks = self.ensure_tasks(shot, task_types)
        out.task_ids = [t.get("id") for t in tasks if isinstance(t, dict)]
        ingest_task = _pick_ingest_task(tasks)
        out.ingest_task = ingest_task if isinstance(ingest_task, dict) else None

        if not ingest_task:
            out.skipped_reason = "no task to attach the ingest record to"
            logger.warning("[KitsuRecorder] %s", out.skipped_reason)
        else:
            out.task_id = str(ingest_task.get("id", ""))
            out.task_name = _task_type_name(ingest_task)
            status = self.ingest_task_status or None
            comment = self.gw.add_comment(
                ingest_task, comment_text or default_comment(item, preview_meta), status=status
            )
            if isinstance(comment, dict):
                out.comment_id = str(comment.get("id", ""))
            out.task_status = status or ""

        self._append_version_entry(shot, item, preview_meta, out)
        return out

    def resolve_ingest_task(self, project, item, task_types) -> RecordOutcome:
        """
        Just the shot/task handles for `item` -- no comment, no version
        entry. Used to re-attach a preview on a resumed session, where the
        version was already recorded in a previous run.
        """
        if self.dry_run:
            return RecordOutcome(dry_run=True)
        out = RecordOutcome()
        shot = self.ensure_shot(project, item)
        out.shot = shot if isinstance(shot, dict) else None
        out.shot_id = str(shot.get("id", "")) if isinstance(shot, dict) else str(shot)
        tasks = self.ensure_tasks(shot, task_types)
        ingest_task = _pick_ingest_task(tasks)
        out.ingest_task = ingest_task if isinstance(ingest_task, dict) else None
        out.task_id = str(ingest_task.get("id", "")) if isinstance(ingest_task, dict) else ""
        return out

    def attach_preview(self, outcome: RecordOutcome, item, preview_meta: PreviewMetadata,
                       preview_path: str) -> str:
        """
        Upload the proxy for an already-recorded version, register it as the
        shot's main preview, stamp the source metadata onto the preview file,
        and update the version's ledger entry with the preview id.

        Returns the Kitsu preview id ("" on dry-run / failure). Safe to run
        long after record_version(), on a background queue.
        """
        if self.dry_run or not preview_path or not outcome or not outcome.ingest_task:
            return ""
        task = outcome.ingest_task
        try:
            # keep the task where record_version() left it (Done) -- a status
            # of None here would let the preview comment revert it via a stale
            # task_status_id on the cached task dict
            preview = self.gw.upload_preview(
                task, f"Preview v{item.version:03d}", preview_path,
                status=self.ingest_task_status or None,
            )
        except Exception as e:
            logger.error("[KitsuRecorder] preview upload failed: %s", e)
            return ""
        if not (isinstance(preview, dict) and preview.get("id")):
            return ""

        preview_id = str(preview["id"])
        try:
            self.gw.set_main_preview(preview)
        except Exception as e:
            logger.warning("[KitsuRecorder] set_main_preview failed: %s", e)
        self._stamp_preview(preview, preview_meta)

        outcome.preview_id = preview_id
        outcome.has_preview = True
        if isinstance(outcome.shot, dict):
            self._append_version_entry(outcome.shot, item, preview_meta, outcome)
        return preview_id

    # ------------------------------------------------------------------

    def _stamp_preview(self, preview: dict, preview_meta: PreviewMetadata) -> None:
        """
        Merge the PreviewMetadata into the preview file's `data` blob under
        its namespaced key -- Zou drops unknown top-level keys, and `data`
        already holds the media dimensions Zou wrote on upload, so this
        reads-merges-writes rather than replacing.
        """
        try:
            existing = self.gw.get_preview_data(str(preview["id"])) or {}
        except Exception as e:
            logger.warning("[KitsuRecorder] could not read preview data before stamp: %s", e)
            existing = preview.get("data") or {}
        try:
            self.gw.update_preview_data(preview, preview_meta.to_kitsu_data(existing))
        except Exception as e:
            logger.error("[KitsuRecorder] failed to stamp preview metadata: %s", e)

    def _append_version_entry(self, shot, item, preview_meta, out: RecordOutcome) -> None:
        """
        shot.data['media_items'][media_name]['versions']['v###'] = entry,
        merged with whatever versions are already recorded (not overwritten).
        This is the current, swappable representation -- see module docstring.
        """
        if not isinstance(shot, dict):
            return
        data = dict(shot.get("data") or {})
        media_items = dict(data.get("media_items") or {})
        entry_wrap = dict(media_items.get(item.media_name) or {})
        versions = dict(entry_wrap.get("versions") or {})

        vkey = f"v{item.version:03d}"
        entry = preview_meta.to_dict()
        entry["kitsu_preview_id"] = out.preview_id or None
        entry["kitsu_comment_id"] = out.comment_id or None
        entry["has_preview"] = out.has_preview
        versions[vkey] = entry

        entry_wrap["versions"] = versions
        entry_wrap["latest_version"] = item.version
        entry_wrap["nas_path"] = preview_meta.nas_path
        media_items[item.media_name] = entry_wrap
        data["media_items"] = media_items

        try:
            self.gw.update_shot_data(shot, data)
        except Exception as e:
            logger.error("[KitsuRecorder] failed to append version entry: %s", e)
