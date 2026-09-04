"""media -- the ONE call for creating a versioned media on an entity.

Ingest a delivered plate, publish a comp render, save a Nuke script, write a
cache: all `media.publish(pctx, entity, media_type, task, files=...)`. The
media type's config entry (`config_and_paths.md` v2) decides the path, whether
Kitsu stores it as an `output_file` or `working_file`, and whether a review
proxy is generated.

    media.publish(pctx, shot, "Plate", ingest_task, files=delivered_frames)
    media.publish(pctx, shot, "CompRender", comp_task, files=rendered_exrs,
                  inputs=[nuke_script_record])
"""

from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path

from square_core.model import MediaResult, Provenance
from square_core.storage import transfer

logger = logging.getLogger("square.services.media")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entity_coords(entity) -> dict:
    """seq/shot or asset coords from a model entity."""
    kind = type(entity).__name__.lower()
    if kind == "shot":
        return {"sequence": getattr(entity, "sequence_code", "") or "",
                "shot": getattr(entity, "code", "") or "",
                "episode": getattr(entity, "episode_code", "") or ""}
    if kind == "asset":
        return {"asset": getattr(entity, "code", "") or "",
                "asset_type": getattr(entity, "asset_type", "") or ""}
    return {}


def _normalize_inputs(inputs) -> list:
    out = []
    for i in inputs or ():
        if isinstance(i, dict):
            out.append({"kind": i.get("kind", "output"), "id": i.get("id", "")})
        else:
            kind = "working" if type(i).__name__ == "Workfile" else "output"
            out.append({"kind": kind, "id": getattr(i, "id", "")})
    return [i for i in out if i["id"]]


def next_version(pctx, entity, media_type: str, task, *, name: str = "main") -> int:
    entry = pctx.paths.media_entry(media_type)
    if entry.get("kitsu_kind", "output") == "working":
        return pctx.kitsu.next_working_revision(task, name=name)
    return pctx.kitsu.next_output_revision(entity, media_type, task, name=name)


def list_versions(pctx, entity, media_type: str) -> list:
    entry = pctx.paths.media_entry(media_type)
    if entry.get("kitsu_kind", "output") == "working":
        return []                                   # working files are per-task; caller passes the task
    return pctx.kitsu.output_files(entity, output_type_name=media_type)


def publish(pctx, entity, media_type: str, task, *, files, name: str = "main",
            version: int | None = None, media_info=None, inputs=(),
            transfer_mode: str = "copy", make_review_proxy: bool | None = None,
            proxy_dry_run: bool = False, comment: str = "",
            source_workfile_id: str = "", dry_run: bool = False) -> MediaResult:
    files = [str(f) for f in files]
    if not files:
        raise ValueError("media.publish: no files")

    entry = pctx.paths.media_entry(media_type)
    kind = entry.get("kitsu_kind", "output")
    coords = _entity_coords(entity)
    rev = version or next_version(pctx, entity, media_type, task, name=name)
    is_seq = len(files) > 1
    rep = entry.get("representation") or Path(files[0]).suffix.lstrip(".")
    ext = Path(files[0]).suffix.lstrip(".")

    base_ctx = pctx.ctx(**coords, task=_task_name(task), name=name, version=rev,
                        representation=rep, ext=ext)

    dest_dir = pctx.paths.media_dir(media_type, base_ctx)
    if is_seq:
        dest_files = [f"{dest_dir}/{Path(f).name}" for f in files]
    else:
        dest_files = [pctx.paths.media_path(media_type, base_ctx)]

    result = MediaResult(media_type=media_type, name=name, version=rev,
                         kitsu_kind=kind, dir=dest_dir, files=dest_files)

    if dry_run:
        return result

    # move into place (verified), skipping anything already there
    pairs = [(s, d) for s, d in zip(files, dest_files)
             if Path(s).resolve() != Path(d).resolve()]
    if pairs:
        workers = pctx.config.copy_workers
        rs = transfer.transfer_sequence(pairs, mode=transfer_mode, workers=workers)
        result.checksums = {r.dest: r.hash for r in rs if r.hash}
        result.copied = True

    prov = Provenance(
        kind="publish" if kind == "output" else "workfile",
        source_path=str(Path(files[0]).parent), source_sample_file=Path(files[0]).name,
        dest_path=dest_dir, dest_sample_file=Path(dest_files[0]).name,
        file_count=len(files), transfer_mode=transfer_mode,
        episode_code=coords.get("episode", ""), sequence_code=coords.get("sequence", ""),
        shot_code=coords.get("shot", ""), asset_code=coords.get("asset", ""),
        task_type=_task_name(task), output_type=media_type, representation=rep,
        name=name, version=rev, recorded_at=_now(),
        recorded_by=getattr(pctx.pipeline.user, "email", ""),
        checksum=result.checksums.get(dest_files[0], ""),
        resolution=getattr(media_info, "resolution", "") if media_info else "",
        fps=getattr(media_info, "fps", None) if media_info else None,
        colorspace=(getattr(media_info, "colorspace", "") if media_info else "")
                   or entry.get("colorspace", ""),
    )
    data = prov.to_kitsu_data()
    deps = _normalize_inputs(inputs)
    if source_workfile_id:
        deps.append({"kind": "working", "id": source_workfile_id})
    if deps:
        data["square"]["inputs"] = deps

    kitsu_path = dest_files[0] if is_seq else dest_dir  # a seq -> a real frame; single -> its folder
    if kind == "working":
        rec = pctx.kitsu.record_working_file(task, revision=rev, path=kitsu_path,
                                             name=name, software=base_ctx.software or None,
                                             data=data)
    else:
        rec = pctx.kitsu.record_output_file(
            entity, media_type, task, revision=rev, representation=rep, name=name,
            path=kitsu_path, comment=comment, data=data)
    result.record = rec

    if entry.get("previewable") and make_review_proxy is not False:
        try:
            result.preview = _review_proxy(pctx, task, files, dest_dir, rev, name,
                                           media_info, proxy_dry_run)
            if result.preview:
                pctx.kitsu.set_main_preview(result.preview)
                pctx.kitsu.stamp_provenance(result.preview, prov, on="preview")
        except Exception as e:
            logger.warning("review proxy for %s %s v%03d failed: %s",
                           media_type, name, rev, e)

    return result


# --------------------------------------------------------------------------

def _task_name(task) -> str:
    return (getattr(task, "task_type_name", "") or "").lower() or "task"


def _review_proxy(pctx, task, files, dest_dir, rev, name, media_info, dry_run):
    from square_core.media import make_proxy

    proxy = Path(dest_dir) / "_review" / f"{name}_v{rev:03d}.mp4"
    fps = getattr(media_info, "fps", None) or pctx.config.fps or 24.0
    is_video = len(files) == 1 and not any(c.isdigit() for c in Path(files[0]).stem[-6:])
    path = make_proxy(files, proxy, fps=float(fps), is_video=is_video, dry_run=dry_run)
    return pctx.kitsu.upload_preview(task, path, comment=f"Preview v{rev:03d}")
