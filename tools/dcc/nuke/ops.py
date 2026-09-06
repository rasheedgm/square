"""Pipeline operations for the Nuke integration.

No `import nuke` here -- the panel/menu pass results back to Nuke. This keeps
every pipeline call unit-testable with a fake Kitsu, exactly like the workfile
manager's core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from square_core.context import PipelineContext
from square_core.services import media, work

from tools.workfile_manager.core import media_names

from .context import Target

WORKFILE_MEDIA_TYPE = "NukeScript"
SOFTWARE = "nuke"
DEFAULT_OUTPUT_TYPE = "CompRender"


class OpsError(RuntimeError):
    """A pipeline operation could not complete (shot/task missing, not logged
    in, ...). The panel shows the message."""


@dataclass
class Resolved:
    pctx: object
    shot: object
    task: object


class NukeOps:
    def __init__(self, ctx=None):
        self._ctx = ctx or PipelineContext.connect()      # raises NeedsLogin

    # ---- navigation (for the picker) -------------------------------

    def projects(self) -> list[str]:
        return [p.code for p in sorted(self._ctx.kitsu.projects(), key=lambda p: p.code)]

    def shots_by_sequence(self, project: str) -> dict[str, list[str]]:
        pctx = self._ctx.project(project)
        out: dict[str, list[str]] = {}
        for s in pctx.kitsu.shots(pctx.project):
            out.setdefault(s.sequence_code or "", []).append(s.code)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def task_types(self, project: str, sequence: str, shot: str) -> list[str]:
        r = self._resolve(Target(project, sequence, shot, ""), need_task=False)
        return sorted({t.task_type_name for t in r.pctx.kitsu.tasks_for_shot(r.shot)})

    # ---- resolution ---------------------------------------------

    def _resolve(self, t: Target, *, need_task: bool = True) -> Resolved:
        if not (t.project and t.sequence and t.shot):
            raise OpsError("Pick a project, sequence and shot first.")
        pctx = self._ctx.project(t.project)
        shot = next((s for s in pctx.kitsu.shots(pctx.project)
                     if s.code == t.shot and (s.sequence_code or "") == t.sequence), None)
        if shot is None:
            raise OpsError(f"{t.sequence}/{t.shot} isn't in {t.project} yet — "
                           "create it in the project-setup tool.")
        task = None
        if need_task:
            if not t.task_type:
                raise OpsError("Pick a task.")
            task = next((tk for tk in pctx.kitsu.tasks_for_shot(shot)
                         if tk.task_type_name == t.task_type), None)
            if task is None:
                raise OpsError(f"No {t.task_type} task on {t.shot} — "
                               "add it in the project-setup tool's Roadmap.")
        return Resolved(pctx, shot, task)

    # ---- workfiles ---------------------------------------------

    def next_workfile_path(self, t: Target) -> tuple[str, int]:
        r = self._resolve(t)
        return work.next_workfile_path(r.pctx, r.shot, r.task,
                                       media_type=WORKFILE_MEDIA_TYPE, software=SOFTWARE)

    def versions(self, t: Target) -> list:
        r = self._resolve(t)
        return work.versions(r.pctx, r.task)

    def version_path(self, t: Target, revision: int) -> str:
        for w in self.versions(t):
            if w.revision == revision:
                return w.path
        raise OpsError(f"no v{revision:03d} workfile on this task")

    def register_saved(self, t: Target, nk_path: str, *, comment: str = ""):
        """Record a `.nk` the panel has already saved to `nk_path`."""
        r = self._resolve(t)
        return work.save_workfile(r.pctx, r.shot, r.task, nk_path,
                                  media_type=WORKFILE_MEDIA_TYPE, software=SOFTWARE,
                                  comment=comment)

    # ---- outputs / render ------------------------------------

    def output_types(self, t: Target) -> list[str]:
        r = self._resolve(t, need_task=False)
        return media_names(r.pctx.config, "publish") or [DEFAULT_OUTPUT_TYPE]

    def colorspace_for(self, t: Target, media_type: str) -> str:
        r = self._resolve(t, need_task=False)
        return r.pctx.config.media_type(media_type).get("colorspace", "")

    def next_output_path(self, t: Target, media_type: str = DEFAULT_OUTPUT_TYPE) -> tuple[str, int]:
        """Where a Write node should render the next version -- with Nuke hash
        padding (`name.####.exr`) in place of the frame number."""
        r = self._resolve(t)
        rev = media.next_version(r.pctx, r.shot, media_type, r.task)
        ctx = r.pctx.ctx(sequence=t.sequence, shot=t.shot, task=t.task_type.lower(),
                         version=rev, name="main", representation="exr", ext="exr")
        one = r.pctx.paths.media_path(media_type, ctx.with_(frame=1001))
        hashed = re.sub(r"\.(\d+)(\.\w+)$",
                        lambda m: "." + "#" * len(m.group(1)) + m.group(2), one)
        return hashed, rev

    def publish_render(self, t: Target, frames, *, media_type: str = DEFAULT_OUTPUT_TYPE,
                       comment: str = "", source_workfile=None, proxy_dry_run: bool = False):
        r = self._resolve(t)
        return work.publish_output(r.pctx, r.shot, r.task, media_type=media_type,
                                   frames=[str(f) for f in frames], comment=comment,
                                   source_workfile=source_workfile,
                                   proxy_dry_run=proxy_dry_run)

    # ---- plates (SquareRead) --------------------------------

    def plate_versions(self, t: Target, media_type: str = "Plate") -> list:
        r = self._resolve(t, need_task=False)
        return work.outputs(r.pctx, r.shot, media_type)

    def plate_for_read(self, t: Target, *, media_type: str = "Plate",
                       revision: int | None = None) -> dict:
        r = self._resolve(t, need_task=False)
        outs = work.outputs(r.pctx, r.shot, media_type)
        if not outs:
            raise OpsError(f"no {media_type} published on {t.shot}")
        pick = outs[-1] if revision is None else next(
            (o for o in outs if o.revision == revision), None)
        if pick is None:
            raise OpsError(f"no {media_type} v{revision:03d} on {t.shot}")
        entry = r.pctx.config.media_type(media_type)
        return {"path": pick.path, "colorspace": entry.get("colorspace", ""),
                "version": pick.revision, "frame_in": getattr(r.shot, "frame_in", 0),
                "frame_out": getattr(r.shot, "frame_out", 0)}
