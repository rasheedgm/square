"""Pipeline operations for the Nuke integration.

No `import nuke` here -- the panel / gizmos call these and apply the results to
Nuke. Every pipeline call is unit-testable with a fake Kitsu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from square_core.context import PipelineContext
from square_core.services import media, work

from tools.workfile_manager.core import media_names

from .context import Target

WORKFILE_MEDIA_TYPE = "NukeScript"
SOFTWARE = "nuke"
DEFAULT_OUTPUT_TYPE = "CompRender"
NEW_VERSION = "(new)"


class OpsError(RuntimeError):
    """A pipeline operation could not complete -- the panel shows the message."""


@dataclass
class Resolved:
    pctx: object
    shot: object
    task: object


class NukeOps:
    def __init__(self, ctx=None):
        self._ctx = ctx or PipelineContext.connect()      # raises NeedsLogin
        self._proj_cache: dict = {}

    # ---- navigation (the panel / gizmo pickers) -------------------

    def projects(self) -> list[str]:
        return [p.code for p in sorted(self._ctx.kitsu.projects(), key=lambda p: p.code)]

    def is_episodic(self, project: str) -> bool:
        p = self._ctx.kitsu.project(project)
        return getattr(p, "production_type", "") == "tvshow"

    def episodes(self, project: str) -> list[str]:
        return sorted(e.code for e in self._ctx.kitsu.episodes(
            self._pctx(project).project) if e.code)

    def sequences(self, project: str, episode: str = "") -> list[str]:
        pctx = self._pctx(project)
        seqs = pctx.kitsu.sequences(pctx.project) if hasattr(pctx.kitsu, "sequences") else []
        out = [s.code for s in seqs
               if not episode or (s.episode_code or "") == episode]
        if out:
            return sorted(set(out))
        # fall back to whatever the shots say
        return sorted({s.sequence_code or "" for s in self._shots(project)} - {""})

    def shots(self, project: str, sequence: str = "", episode: str = "") -> list[str]:
        return sorted(s.code for s in self._shots(project)
                      if (not sequence or (s.sequence_code or "") == sequence))

    def task_types(self, project: str, sequence: str, shot: str, episode: str = "") -> list[str]:
        r = self._resolve(Target(project, episode, sequence, shot, ""), need_task=False)
        return sorted({t.task_type_name for t in r.pctx.kitsu.tasks_for_shot(r.shot)})

    def default_task_for(self, project: str, sequence: str, shot: str) -> str:
        tt = self.task_types(project, sequence, shot)
        return "Comp" if "Comp" in tt else (tt[0] if tt else "")

    # ---- resolution --------------------------------------------

    def _pctx(self, project: str):
        if project not in self._proj_cache:
            self._proj_cache[project] = self._ctx.project(project)
        return self._proj_cache[project]

    def _shots(self, project: str) -> list:
        pctx = self._pctx(project)
        return pctx.kitsu.shots(pctx.project)

    def _resolve(self, t: Target, *, need_task: bool = True) -> Resolved:
        if not (t.project and t.sequence and t.shot):
            raise OpsError("Pick a project, sequence and shot first.")
        pctx = self._pctx(t.project)
        shot = next((s for s in pctx.kitsu.shots(pctx.project)
                     if s.code == t.shot and (s.sequence_code or "") == t.sequence), None)
        if shot is None:
            raise OpsError(f"{t.sequence}/{t.shot} isn't in {t.project} yet — "
                           "create it in the project-setup tool.")
        if t.episode and not getattr(shot, "episode_code", ""):
            shot.episode_code = t.episode                 # fill {episode} for paths
        task = None
        if need_task:
            if not t.task_type:
                raise OpsError("Pick a task.")
            task = next((tk for tk in pctx.kitsu.tasks_for_shot(shot)
                         if tk.task_type_name == t.task_type), None)
            if task is None:
                raise OpsError(f"No {t.task_type} task on {t.shot} — add it in the "
                               "project-setup tool's Roadmap.")
        return Resolved(pctx, shot, task)

    # ---- workfiles ---------------------------------------------

    def workfile_names(self, t: Target) -> list[str]:
        r = self._resolve(t)
        return work.workfile_names(r.pctx, r.task)

    def workfile_versions(self, t: Target, *, name: str = "main") -> list:
        """`work.MajorVersion` list, newest major last, each with disk minors."""
        r = self._resolve(t)
        return work.workfile_versions(r.pctx, r.shot, r.task, name=name,
                                      media_type=WORKFILE_MEDIA_TYPE)

    def next_save(self, t: Target, *, name: str = "main", bump: str = "minor"):
        r = self._resolve(t)
        return work.next_save(r.pctx, r.shot, r.task, name=name,
                              media_type=WORKFILE_MEDIA_TYPE, bump=bump)

    def register_major(self, t: Target, target, *, name: str = "main", comment: str = ""):
        r = self._resolve(t)
        return work.register_major(r.pctx, r.shot, r.task, target, name=name,
                                   media_type=WORKFILE_MEDIA_TYPE, software=SOFTWARE,
                                   comment=comment)

    # ---- outputs / render ------------------------------------

    def output_types(self, t: Target, *, renderable_only: bool = True) -> list[str]:
        r = self._resolve(t, need_task=False)
        names = media_names(r.pctx.config, "publish")
        if renderable_only:
            names = [n for n in names if r.pctx.config.media_type(n).get("renderable")]
        return names or [DEFAULT_OUTPUT_TYPE]

    def read_types(self, t: Target) -> list[str]:
        """Media types a SquareRead can load -- delivery + publish, minus
        working files."""
        r = self._resolve(t, need_task=False)
        cfg = r.pctx.config
        return sorted(set(media_names(cfg, "delivery")) | set(media_names(cfg, "publish")))

    def colorspace_for(self, t: Target, media_type: str) -> str:
        r = self._resolve(t, need_task=False)
        return r.pctx.config.media_type(media_type).get("colorspace", "")

    def output_versions(self, t: Target, media_type: str) -> list:
        r = self._resolve(t, need_task=False)
        return work.outputs(r.pctx, r.shot, media_type)

    def resolve_output_path(self, t: Target, media_type: str, version) -> dict:
        """Where a SquareWrite should render `version` (int) or the next new one
        (`version` is None / '(new)'). Returns path (with #### padding), the
        version number, and whether it's locked."""
        r = self._resolve(t)
        existing = {o.revision: o for o in work.outputs(r.pctx, r.shot, media_type)}
        if version in (None, NEW_VERSION, ""):
            rev = media.next_version(r.pctx, r.shot, media_type, r.task)
            locked = False
        else:
            rev = int(version)
            locked = rev in existing and work.output_locked(existing[rev])
        ctx = r.pctx.ctx(**_coords(r.shot, t), task=t.task_type.lower(),
                         version=rev, name="main", representation="exr", ext="exr")
        one = r.pctx.paths.media_path(media_type, ctx.with_(frame=1001))
        hashed = re.sub(r"\.(\d+)(\.\w+)$",
                        lambda m: "." + "#" * len(m.group(1)) + m.group(2), one)
        return {"path": hashed, "version": rev, "locked": locked,
                "colorspace": r.pctx.config.media_type(media_type).get("colorspace", "")}

    def publish_render(self, t: Target, frames, *, media_type: str = DEFAULT_OUTPUT_TYPE,
                       comment: str = "", make_preview: bool = True,
                       source_workfile=None, proxy_dry_run: bool = False):
        r = self._resolve(t)
        # guard: never overwrite a locked version
        target_rev = media.next_version(r.pctx, r.shot, media_type, r.task)
        for o in work.outputs(r.pctx, r.shot, media_type):
            if o.revision == target_rev and work.output_locked(o):
                raise OpsError(f"{media_type} v{target_rev:03d} is locked (reviewed / "
                               "delivered) — render a new version.")
        return work.publish_output(r.pctx, r.shot, r.task, media_type=media_type,
                                   frames=[str(f) for f in frames], comment=comment,
                                   source_workfile=source_workfile,
                                   make_review_proxy=make_preview,
                                   proxy_dry_run=proxy_dry_run)

    # ---- plates (SquareRead) --------------------------------

    def resolve_read_path(self, t: Target, media_type: str, version) -> dict:
        r = self._resolve(t, need_task=False)
        outs = {o.revision: o for o in work.outputs(r.pctx, r.shot, media_type)}
        if not outs:
            raise OpsError(f"no {media_type} published on {t.shot}")
        rev = max(outs) if version in (None, "", "latest") else int(version)
        pick = outs.get(rev)
        if pick is None:
            raise OpsError(f"no {media_type} v{rev:03d} on {t.shot}")
        entry = r.pctx.config.media_type(media_type)
        return {"path": pick.path, "version": rev,
                "colorspace": entry.get("colorspace", ""),
                "frame_in": getattr(r.shot, "frame_in", 0),
                "frame_out": getattr(r.shot, "frame_out", 0),
                "versions": sorted(outs, reverse=True)}


def _coords(shot, t: Target) -> dict:
    return {"sequence": t.sequence, "shot": t.shot,
            "episode": t.episode or getattr(shot, "episode_code", "") or ""}
