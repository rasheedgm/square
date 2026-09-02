"""review -- the supervisor loop.

submit a version for review, record a note (+ annotations), approve / request
changes. Downstream effects (shot status rollups, unlocking the next
department) are Kitsu's own status automation, not ours.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("square.services.review")


def submit(pctx, task, preview_path: str, *, comment: str = "",
           status: str = "Pending Review"):
    """Upload a preview and move the task into review."""
    preview = pctx.kitsu.upload_preview(task, preview_path, comment=comment or "Submitted for review",
                                        status=status)
    if preview:
        pctx.kitsu.set_main_preview(preview)
    return preview


def record_note(pctx, task, *, text: str, status: str = "", annotations=None,
                preview=None):
    """Supervisor feedback: a comment, an optional status change, and optional
    annotations pushed onto the preview."""
    comment = pctx.kitsu.comment(task, text, status=status or None)
    if annotations and preview is not None:
        pctx.kitsu.update_annotations(preview, additions=list(annotations))
    return comment


def approve(pctx, task, *, comment: str = "Approved", status: str = "Done"):
    return pctx.kitsu.set_status(task, status, comment=comment)


def request_changes(pctx, task, *, comment: str = "Retake", status: str = "Retake"):
    return pctx.kitsu.set_status(task, status, comment=comment)


def thread(pctx, task) -> list:
    getter = getattr(pctx.kitsu, "comments_for_task", None)
    return getter(task) if getter else []
