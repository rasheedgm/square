"""work -- the workfile lifecycle for a DCC task.

A *workfile* is a versioned DCC scene (Nuke script, Maya file, ...) saved
against a task; Kitsu stores it as a `working_file`, `PathResolver` places it.
An *output* is a versioned published result (a comp render, a cache); a Kitsu
`output_file`. Both go through `media.publish` -- this module is the
task-centric view a workfile manager or a DCC publish panel drives.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from square_core.model import Workfile

from . import media
from ._common import entity_coords as _entity_coords
from ._common import task_name as _task_name

logger = logging.getLogger("square.services.work")


@dataclass
class WorkfileSlot:
    """The result of reserving the next workfile version."""
    path: str
    revision: int
    record: object = None          # model.Workfile from Kitsu
    seeded: bool = False           # a file was written to `path` on disk
    seeded_from: str = ""


# ---- reading ----------------------------------------------------------

def versions(pctx, task, *, name: str = "main") -> list[Workfile]:
    """Every saved workfile version on this task, oldest first."""
    wfs = [w for w in pctx.kitsu.working_files(task) if (w.name or "main") == name]
    return sorted(wfs, key=lambda w: w.revision)


def latest(pctx, task, *, name: str = "main") -> Workfile | None:
    vs = versions(pctx, task, name=name)
    return vs[-1] if vs else None


def outputs(pctx, entity, media_type: str) -> list:
    """Every published output version of `media_type` on `entity`, oldest first."""
    return sorted(media.list_versions(pctx, entity, media_type),
                  key=lambda o: getattr(o, "revision", 0))


# ---- paths -----------------------------------------------------------

def workfile_path(pctx, entity, task, *, media_type: str, revision: int,
                  software: str = "", name: str = "main", ext: str = "") -> str:
    ctx = pctx.ctx(**_entity_coords(entity), task=_task_name(task), software=software,
                   version=revision, name=name, ext=ext)
    return pctx.paths.media_path(media_type, ctx)


def next_workfile_path(pctx, entity, task, *, media_type: str, software: str = "",
                       name: str = "main", ext: str = "") -> tuple[str, int]:
    """The path the *next* save should land at, and its revision number."""
    rev = pctx.kitsu.next_working_revision(task, name=name)
    path = workfile_path(pctx, entity, task, media_type=media_type, revision=rev,
                         software=software, name=name, ext=ext)
    return path, rev


# ---- writing --------------------------------------------------------

def new_workfile(pctx, entity, task, *, media_type: str, software: str = "",
                 template: str = "", from_current: bool = False, name: str = "main",
                 comment: str = "", ext: str = "") -> WorkfileSlot:
    """Reserve the next workfile version.

    Resolves its path, optionally seeds the file on disk -- copied up from the
    current version (`from_current`) or from a DCC `template` -- and registers
    the slot in Kitsu. With no seed the slot is still registered at the resolved
    path and the DCC is expected to save into it.
    """
    rev = pctx.kitsu.next_working_revision(task, name=name)

    seed = ""
    if from_current:
        cur = latest(pctx, task, name=name)
        seed = getattr(cur, "path", "") or ""
    seed = seed or template
    if not ext and seed:
        ext = Path(seed).suffix.lstrip(".")

    dest = workfile_path(pctx, entity, task, media_type=media_type, revision=rev,
                         software=software, name=name, ext=ext)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)

    seeded = False
    if seed:
        if not Path(seed).is_file():
            raise FileNotFoundError(f"workfile seed not found: {seed}")
        if Path(seed).resolve() != Path(dest).resolve():
            shutil.copy2(seed, dest)
        seeded = True

    result = media.publish(pctx, entity, media_type, task, files=[dest], name=name,
                           version=rev, comment=comment, software=software,
                           make_review_proxy=False)
    logger.info("workfile %s v%03d -> %s%s", _task_name(task), rev, dest,
                f" (from {Path(seed).name})" if seeded else "")
    return WorkfileSlot(path=dest, revision=rev, record=result.record,
                        seeded=seeded, seeded_from=seed)


def save_workfile(pctx, entity, task, src_path, *, media_type: str, software: str = "",
                  name: str = "main", comment: str = "", inputs=()):
    """Register an existing DCC scene file as the next (or given) workfile
    version -- the DCC 'Save Version' action. `src_path` may already be at the
    resolved location (nothing is copied then)."""
    return media.publish(pctx, entity, media_type, task, files=[str(src_path)],
                         name=name, comment=comment, software=software,
                         inputs=inputs, make_review_proxy=False)


def publish_output(pctx, entity, task, *, media_type: str, frames, name: str = "main",
                   media_info=None, source_workfile=None, comment: str = "",
                   transfer_mode: str = "copy", make_review_proxy: bool | None = None,
                   proxy_dry_run: bool = False) -> "media.MediaResult":
    """Publish a rendered result (frames or a movie) as `media_type` -- the DCC
    'Publish' action. `source_workfile` records the scene it came from."""
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
