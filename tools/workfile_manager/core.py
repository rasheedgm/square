"""Headless core for the workfile manager.

`WorkfileHub` holds the `PipelineContext` and turns tool actions into
`square_core.services.work` calls. It also owns the one non-pipeline concern:
launching a DCC on a workfile via the configured per-software command.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from square_core.config.project import DEFAULT_PROJECT_CONFIG
from square_core.services import work

from . import config_keys

__all__ = ["WorkfileHub", "SeedMode"]

_BUILTIN_MEDIA = DEFAULT_PROJECT_CONFIG["media_types"]


class SeedMode:
    EMPTY = "empty"           # register the slot, DCC creates the file
    CURRENT = "current"       # copy up from the current version
    TEMPLATE = "template"     # copy from a configured / chosen template


@dataclass
class TaskRow:
    task: object              # model.Task
    shot_code: str
    sequence_code: str

    @property
    def label(self) -> str:
        return f"{self.sequence_code}/{self.shot_code}  ·  {self.task.task_type_name}"


def _media_names(cfg, source: str) -> list[str]:
    """Configured media-type names of a given source, plus the built-ins of that
    source that a sparse project config doesn't list explicitly."""
    names = set(cfg.media_type_names())
    names.update(n for n in _BUILTIN_MEDIA if n != "_default")
    return sorted(n for n in names if cfg.media_type(n).get("source") == source)


class WorkfileHub:
    def __init__(self, ctx):
        self.ctx = ctx
        self.user = getattr(ctx, "user", None)
        self._pctx_cache: dict = {}

    # ---- navigation --------------------------------------------------

    def projects(self) -> list:
        return sorted(self.ctx.kitsu.projects(), key=lambda p: p.code)

    def _pctx(self, project_code: str):
        if project_code not in self._pctx_cache:
            self._pctx_cache[project_code] = self.ctx.project(project_code)
        return self._pctx_cache[project_code]

    def clear_cache(self) -> None:
        self._pctx_cache.clear()

    def shots(self, project_code: str) -> list:
        pctx = self._pctx(project_code)
        return sorted(pctx.kitsu.shots(pctx.project),
                      key=lambda s: (s.sequence_code or "", s.code))

    def tasks_for_shot(self, project_code: str, shot) -> list[TaskRow]:
        pctx = self._pctx(project_code)
        rows = []
        for t in pctx.kitsu.tasks_for_shot(shot):
            rows.append(TaskRow(task=t, shot_code=shot.code,
                                sequence_code=shot.sequence_code or ""))
        return sorted(rows, key=lambda r: r.task.task_type_name)

    # ---- media types ----------------------------------------------

    def workfile_types(self, project_code: str) -> list[str]:
        return _media_names(self._pctx(project_code).config, "work")

    def output_types(self, project_code: str) -> list[str]:
        return _media_names(self._pctx(project_code).config, "publish")

    def software_for(self, project_code: str, media_type: str) -> str:
        """A sensible default software name for a working media type."""
        entry = self._pctx(project_code).config.media_type(media_type)
        d = (entry.get("dir") or "").lower()
        for sw in ("nuke", "maya", "houdini", "blender", "fusion"):
            if sw in d or sw in media_type.lower():
                return sw
        return ""

    # ---- reads ---------------------------------------------------

    def workfiles(self, project_code: str, task) -> list:
        return work.versions(self._pctx(project_code), task)

    def outputs(self, project_code: str, shot, media_type: str) -> list:
        return work.outputs(self._pctx(project_code), shot, media_type)

    # ---- actions ------------------------------------------------

    def new_workfile(self, project_code: str, shot, task, *, media_type: str,
                     software: str = "", seed: str = SeedMode.EMPTY,
                     template_path: str = "", comment: str = ""):
        pctx = self._pctx(project_code)
        tmpl = ""
        if seed == SeedMode.TEMPLATE:
            tmpl = template_path or (config_keys.read(pctx, "templates") or {}).get(media_type, "")
            if not tmpl:
                raise ValueError(f"no template configured for {media_type!r}")
        return work.new_workfile(
            pctx, shot, task, media_type=media_type, software=software,
            template=tmpl, from_current=(seed == SeedMode.CURRENT), comment=comment)

    def publish_output(self, project_code: str, shot, task, *, media_type: str,
                       frames, comment: str = "", proxy_dry_run: bool = False):
        pctx = self._pctx(project_code)
        return work.publish_output(pctx, shot, task, media_type=media_type,
                                   frames=[str(f) for f in frames], comment=comment,
                                   proxy_dry_run=proxy_dry_run)

    # ---- launching the DCC --------------------------------------

    def launcher_for(self, project_code: str, software: str) -> str:
        return (config_keys.read(self._pctx(project_code), "launchers") or {}).get(software, "")

    def open_workfile(self, project_code: str, software: str, path: str) -> str:
        """Launch the DCC on `path` via the configured command, or fall back to
        revealing the containing folder. Returns a short description of what
        happened."""
        cmd = self.launcher_for(project_code, software)
        if cmd:
            parts = shlex.split(cmd, posix=(os.name != "nt"))
            parts = [path if p == "{file}" else p.replace("{file}", path) for p in parts]
            if "{file}" not in cmd:
                parts.append(path)
            subprocess.Popen(parts)
            return f"launched {software or 'DCC'}: {parts[0]}"
        self.reveal(str(Path(path).parent))
        return "no launcher configured for this software — opened the folder"

    @staticmethod
    def reveal(folder: str) -> None:
        p = Path(folder)
        if not p.exists():
            return
        if sys.platform.startswith("win"):
            os.startfile(str(p))            # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
