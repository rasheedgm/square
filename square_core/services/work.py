"""work -- thin, familiar wrappers over `services.media`.

Every publish/save is `media.publish(pctx, entity, media_type, task, files=...)`.
These helpers just fix the media type and read it from `tools.<dcc>.*` config.
"""

from __future__ import annotations

from pathlib import Path

from . import media


def workfile_path(pctx, *, media_type: str, sequence="", shot="", task="",
                  software="", revision=1, name="main", ext="") -> str:
    ctx = pctx.ctx(sequence=sequence, shot=shot, task=task, software=software,
                   version=revision, name=name, ext=ext)
    return pctx.paths.media_path(media_type, ctx)


def next_version(pctx, entity, media_type: str, task, *, name="main") -> int:
    return media.next_version(pctx, entity, media_type, task, name=name)


def save_workfile(pctx, entity, task, src_path, *, media_type: str, name="main",
                  comment="", inputs=()):
    """Save a DCC scene as the given `media_type` (a `kitsu_kind: working` entry)."""
    return media.publish(pctx, entity, media_type, task, files=[str(src_path)],
                         name=name, comment=comment, inputs=inputs,
                         make_review_proxy=False)


def publish_output(pctx, entity, task, *, media_type: str, frames, name="main",
                   media_info=None, source_workfile=None, comment="",
                   transfer_mode="copy", make_review_proxy=None,
                   proxy_dry_run=False) -> "media.MediaResult":
    src_id = getattr(source_workfile, "id", "") if source_workfile else ""
    return media.publish(pctx, entity, media_type, task, files=frames, name=name,
                         media_info=media_info, comment=comment,
                         transfer_mode=transfer_mode, make_review_proxy=make_review_proxy,
                         proxy_dry_run=proxy_dry_run, source_workfile_id=src_id)


def make_preview(pctx, frames, out_path, *, fps=24.0, is_video=False,
                 start_frame=None, dry_run=False) -> str:
    from square_core.media import make_proxy

    return make_proxy(frames, out_path, fps=fps, is_video=is_video,
                      start_frame=start_frame, dry_run=dry_run)
