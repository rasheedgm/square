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
NEW_VERSION = "(new)"     # always the next-after-highest number, ignoring workfile major
SYNC_VERSION = "(sync)"   # always the current workfile major, re-rendering in place


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
        # per-instance, session-lifetime caches -- _resolve() (shots + tasks)
        # is called from nearly every method below, and gizmos._populate()
        # calls a whole chain of them back to back for one node creation;
        # without these, the SAME shot list / task list / output list for
        # one shot got re-fetched from Kitsu 5-7 times over for a single
        # SquareRead/SquareWrite. _outputs_cache is invalidated per
        # (shot, media_type) by publish_render() -- it's the one cache here
        # that a Nuke action in THIS session actually changes; shots/tasks
        # don't, so they're never invalidated (same tradeoff _proj_cache
        # already makes: a project-setup edit mid-session needs a fresh
        # NukeOps, i.e. a new Nuke session, to be seen).
        self._shots_cache: dict = {}                        # project -> [shot, ...]
        self._tasks_cache: dict = {}                         # shot.id -> [task, ...]
        self._outputs_cache: dict = {}                # (shot.id, media_type) -> [output, ...]

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
        return sorted({t.task_type_name for t in self._tasks(r.pctx, r.shot)})

    def default_task_for(self, project: str, sequence: str, shot: str) -> str:
        tt = self.task_types(project, sequence, shot)
        return "Comp" if "Comp" in tt else (tt[0] if tt else "")

    # ---- resolution --------------------------------------------

    def _pctx(self, project: str):
        if project not in self._proj_cache:
            self._proj_cache[project] = self._ctx.project(project)
        return self._proj_cache[project]

    def _shots(self, project: str) -> list:
        if project not in self._shots_cache:
            pctx = self._pctx(project)
            self._shots_cache[project] = pctx.kitsu.shots(pctx.project)
        return self._shots_cache[project]

    def _tasks(self, pctx, shot) -> list:
        key = getattr(shot, "id", None) or id(shot)
        if key not in self._tasks_cache:
            self._tasks_cache[key] = pctx.kitsu.tasks_for_shot(shot)
        return self._tasks_cache[key]

    def _outputs(self, pctx, shot, media_type: str, name: str) -> list:
        key = (getattr(shot, "id", None) or id(shot), media_type, name)
        if key not in self._outputs_cache:
            self._outputs_cache[key] = work.outputs(pctx, shot, media_type, name=name)
        return self._outputs_cache[key]

    def _resolve(self, t: Target, *, need_task: bool = True) -> Resolved:
        if not (t.project and t.sequence and t.shot):
            raise OpsError("Pick a project, sequence and shot first.")
        pctx = self._pctx(t.project)
        shot = next((s for s in self._shots(t.project)
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
            task = next((tk for tk in self._tasks(pctx, shot)
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

    def output_versions(self, t: Target, media_type: str, *, name: str = "main") -> list:
        r = self._resolve(t, need_task=False)
        return self._outputs(r.pctx, r.shot, media_type, name)

    def workfile_major(self, t: Target, *, name: str = "main") -> int:
        r = self._resolve(t)
        return work.current_workfile_major(r.pctx, r.task, name=name)

    def verify_workfile_for_render(self, t: Target, *, name: str = "main",
                                   open_script_path: str = "", sync: bool = False) -> str:
        """Empty string if `open_script_path` is fine to render from; otherwise
        a human-readable reason it isn't. Checked BEFORE rendering starts, so
        the caller can warn (save first, or explicitly proceed anyway)
        instead of discovering the mismatch only after frames exist."""
        r = self._resolve(t)
        if not open_script_path:
            return "This script hasn't been saved as a workfile yet — save it first."
        parsed = work.verify_open_script(r.pctx, r.shot, r.task, open_script_path,
                                         name=name, media_type=WORKFILE_MEDIA_TYPE)
        if parsed is None:
            return (f"This script doesn't look like a saved workfile for "
                    f"{t.sequence}/{t.shot} ({name}) — save it first.")
        if sync:
            major, _ = parsed
            current = work.current_workfile_major(r.pctx, r.task, name=name)
            if current and major != current:
                return (f"This script is v{major:03d}, but v{current:03d} is the latest "
                        "workfile major registered for this task — Sync may not do what "
                        "you expect.")
        return ""

    def resolve_output_path(self, t: Target, media_type: str, version, *,
                            name: str = "main") -> dict:
        """Where a SquareWrite should render.

        `version` = NEW_VERSION -> always the next-after-highest number for
        (shot, media_type, name), ignoring the workfile major entirely --
        can never collide with a lock, nothing occupies that number yet.
        `version` = SYNC_VERSION (or empty/None) -> the current workfile
        major, re-rendering in place if that revision already has output,
        refused if it's locked. `version` = an int -> that existing version
        explicitly (a re-render). Returns the #### path, the version, and
        whether it is locked.
        """
        r = self._resolve(t)
        existing = {o.revision: o for o in self._outputs(r.pctx, r.shot, media_type, name)}
        if version == NEW_VERSION:
            rev = media.next_version(r.pctx, r.shot, media_type, r.task, name=name)
            locked = False
        elif version in (None, SYNC_VERSION, ""):
            rev = (work.current_workfile_major(r.pctx, r.task, name=name)
                   or media.next_version(r.pctx, r.shot, media_type, r.task, name=name))
            locked = rev in existing and work.output_locked(existing[rev])
        else:
            rev = int(version)
            locked = rev in existing and work.output_locked(existing[rev])
        ctx = r.pctx.ctx(**_coords(r.shot, t), task=t.task_type.lower(),
                         version=rev, name=name, representation="exr", ext="exr")
        one = r.pctx.paths.media_path(media_type, ctx.with_(frame=1001))
        hashed = re.sub(r"\.(\d+)(\.\w+)$",
                        lambda m: "." + "#" * len(m.group(1)) + m.group(2), one)
        return {"path": hashed, "version": rev, "locked": locked,
                "colorspace": r.pctx.config.media_type(media_type).get("colorspace", "")}

    def publish_render(self, t: Target, frames, *, media_type: str = DEFAULT_OUTPUT_TYPE,
                       name: str = "main", version: str | int | None = None, comment: str = "",
                       make_preview: bool = True, proxy_dry_run: bool = False,
                       source_script_path: str = ""):
        r = self._resolve(t)
        if version == NEW_VERSION:
            rev = media.next_version(r.pctx, r.shot, media_type, r.task, name=name)
        elif version in (None, SYNC_VERSION, ""):
            rev = (work.current_workfile_major(r.pctx, r.task, name=name)
                   or media.next_version(r.pctx, r.shot, media_type, r.task, name=name))
        else:
            rev = int(version)

        for o in self._outputs(r.pctx, r.shot, media_type, name):
            if o.revision == rev and work.output_locked(o):
                raise OpsError(f"{media_type} v{rev:03d} is locked (reviewed / delivered) "
                               "— save a new workfile major and re-render.")

        wf = next((w for w in r.pctx.kitsu.working_files(r.task)
                   if (w.name or "main") == name and w.revision == rev), None)
        if source_script_path:
            if wf is None:
                # a NEW_VERSION render is decoupled from the workfile's own
                # major by design -- nothing may be registered at `rev` yet.
                # Give the output something real to point at: snapshot the
                # actually-open script as this major's read-only v{rev}.000
                # and register it, rather than leave the output orphaned.
                snap = work.snapshot_rendered_script(
                    r.pctx, r.shot, r.task, source_path=source_script_path,
                    major=rev, name=name, media_type=WORKFILE_MEDIA_TYPE)
                wf = work.register_major_at(r.pctx, r.shot, r.task, rev, snap,
                                            name=name, media_type=WORKFILE_MEDIA_TYPE,
                                            software=SOFTWARE)
            else:
                # already registered (a Sync render, or a NEW_VERSION that
                # happened to land where the workfile already was) -- still
                # refresh .000 so it stays the exact, current record of what
                # produced THIS render, even if the artist rendered from a
                # later real minor than whatever registered it originally.
                work.snapshot_rendered_script(
                    r.pctx, r.shot, r.task, source_path=source_script_path,
                    major=rev, name=name, media_type=WORKFILE_MEDIA_TYPE)

        result = work.publish_output(r.pctx, r.shot, r.task, media_type=media_type, name=name,
                                     frames=[str(f) for f in frames], version=rev,
                                     comment=comment, source_workfile=wf,
                                     make_review_proxy=make_preview,
                                     proxy_dry_run=proxy_dry_run)
        # this just created (or re-rendered) an output version -- the cached
        # list for this (shot, media_type, name) is now stale (wrong max
        # version, possibly a lock that just got set); drop it so the next
        # resolve sees the real state instead of the pre-publish snapshot.
        key = (getattr(r.shot, "id", None) or id(r.shot), media_type, name)
        self._outputs_cache.pop(key, None)
        return result

    # ---- plates (SquareRead) --------------------------------

    def resolve_read_path(self, t: Target, media_type: str, version, *,
                          name: str = "main") -> dict:
        r = self._resolve(t, need_task=False)
        outs = {o.revision: o for o in self._outputs(r.pctx, r.shot, media_type, name)}
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
