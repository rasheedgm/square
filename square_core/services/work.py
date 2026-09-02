"""work -- workfiles and published outputs.

Paths come from `pctx.paths` (the resolver); version numbers from Kitsu. On
publish: resolve the output dir, move the frames there (verified, skipped when
they're already in place), register the output file in Kitsu with our path +
provenance, and optionally trickle a review proxy behind it.
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

from square_core.model import Provenance, PublishResult
from square_core.storage import transfer

logger = logging.getLogger("square.services.work")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# paths / versions
# --------------------------------------------------------------------------

def workfile_path(pctx, *, sequence, shot, task, software, revision, name="main", ext="") -> str:
    ctx = pctx.ctx(sequence=sequence, shot=shot, task=task, software=software,
                   version=revision, name=name, ext=ext)
    return pctx.paths.workfile_path(ctx)


def next_version(pctx, entity, output_type: str, task, *, name="main") -> int:
    return pctx.kitsu.next_output_revision(entity, output_type, task, name=name)


# --------------------------------------------------------------------------
# save a workfile
# --------------------------------------------------------------------------

def save_workfile(pctx, entity, task, src_path, *, sequence, shot, software,
                  name="main", comment="", ext="") -> object:
    revision = pctx.kitsu.next_working_revision(task, name=name)
    dest = workfile_path(pctx, sequence=sequence, shot=shot, task=task_type_name(task),
                         software=software, revision=revision, name=name,
                         ext=ext or Path(str(src_path)).suffix.lstrip("."))
    if Path(src_path).resolve() != Path(dest).resolve():
        transfer.transfer_file(src_path, dest, mode="copy")
    return pctx.kitsu.record_working_file(task, revision=revision, path=dest,
                                          name=name, software=software or None)


# --------------------------------------------------------------------------
# publish an output
# --------------------------------------------------------------------------

def publish_output(pctx, entity, task, *, output_type, frames, sequence, shot,
                   name="main", representation="", media_info=None,
                   source_workfile=None, comment="", transfer_mode="copy",
                   make_review_proxy=True, proxy_dry_run=False) -> PublishResult:
    frames = [str(f) for f in frames]
    if not frames:
        raise ValueError("publish_output: no frames")

    revision = next_version(pctx, entity, output_type, task, name=name)
    rep = representation or Path(frames[0]).suffix.lstrip(".")

    base_ctx = pctx.ctx(sequence=sequence, shot=shot, task=task_type_name(task),
                        output_type=output_type, name=name, version=revision,
                        representation=rep, ext=Path(frames[0]).suffix.lstrip("."))
    out_dir = pctx.paths.output_dir(base_ctx)

    # move frames into place (verified); skip any already there
    dest_frames = []
    pairs = []
    for f in frames:
        fn = Path(f).name
        d = f"{out_dir}/{fn}"
        dest_frames.append(d)
        if Path(f).resolve() != Path(d).resolve():
            pairs.append((f, d))
    checksums = {}
    if pairs:
        workers = pctx.config.data.get("copy_workers") or 4
        results = transfer.transfer_sequence(pairs, mode=transfer_mode, workers=workers)
        checksums = {r.dest: r.hash for r in results if r.hash}

    sample = frames[0]
    prov = Provenance(
        kind="publish", source_path=str(Path(sample).parent),
        source_sample_file=Path(sample).name,
        dest_path=out_dir, dest_sample_file=Path(dest_frames[0]).name,
        file_count=len(frames), transfer_mode=transfer_mode,
        sequence_code=sequence, shot_code=shot, output_type=output_type,
        name=name, version=revision, representation=rep,
        checksum=checksums.get(dest_frames[0], ""),
        recorded_at=_now(), recorded_by=getattr(pctx.pipeline.user, "email", ""),
        resolution=getattr(media_info, "resolution", "") if media_info else "",
        fps=getattr(media_info, "fps", None) if media_info else None,
        colorspace=getattr(media_info, "colorspace", "") if media_info else "",
    )

    out = pctx.kitsu.record_output_file(
        entity, output_type, task, revision=revision, representation=rep,
        name=name, path=dest_frames[0] if len(frames) > 1 else out_dir,
        comment=comment, data=prov.to_kitsu_data(),
    )

    preview = None
    if make_review_proxy:
        try:
            proxy = _review_proxy(pctx, entity, task, frames, out_dir, revision,
                                  output_type, name, media_info, proxy_dry_run)
            preview = pctx.kitsu.upload_preview(task, proxy, comment=f"Preview v{revision:03d}")
            if preview:
                pctx.kitsu.set_main_preview(preview)
                pctx.kitsu.stamp_provenance(preview, prov, on="preview")
        except Exception as e:                       # preview is non-critical
            logger.warning("review proxy for %s v%03d failed: %s", shot, revision, e)

    return PublishResult(output=out, path=out_dir, preview=preview, checksums=checksums)


def make_preview(pctx, frames, out_path, *, fps=24.0, is_video=False,
                 start_frame=None, dry_run=False) -> str:
    from square_core.media import make_proxy

    return make_proxy(frames, out_path, fps=fps, is_video=is_video,
                      start_frame=start_frame, dry_run=dry_run)


# --------------------------------------------------------------------------

def _review_proxy(pctx, entity, task, frames, out_dir, revision, output_type,
                  name, media_info, dry_run) -> str:
    from square_core.media import make_proxy

    proxy_dir = Path(out_dir) / "_review"
    proxy_path = proxy_dir / f"{name}_v{revision:03d}.mp4"
    fps = getattr(media_info, "fps", None) or pctx.config.fps or 24.0
    is_video = len(frames) == 1 and not any(c.isdigit() for c in Path(frames[0]).stem[-6:])
    return make_proxy(frames, proxy_path, fps=float(fps), is_video=is_video,
                      dry_run=dry_run)


def task_type_name(task) -> str:
    return (getattr(task, "task_type_name", "") or "").lower() or "task"
