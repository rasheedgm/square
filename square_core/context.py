"""The composition root.

`PipelineContext.connect()` loads the site config and attaches to Kitsu (or the
offline stand-in). `.project(code)` binds a project's `ProjectConfig` + a
`PathResolver` into a `ProjectContext`, which is what every service call takes.

Tools do only:

    ctx  = PipelineContext.connect()          # or catch NeedsLogin, prompt, retry
    pctx = ctx.project("ABC")
    result = work.publish_output(pctx, ...)

No tool imports gazu or calls square_core.kitsu directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from square_core import kitsu as _kitsu
from square_core.config import ProjectConfig
from square_core.config.pipeline import PipelineConfig
from square_core.config.project import ConfigError
from square_core.errors import PipelineError
from square_core.model import PathContext, Project, User
from square_core.paths import PathResolver

logger = logging.getLogger("square.context")


@dataclass
class ProjectContext:
    pipeline: "PipelineContext"
    project: Project
    config: ProjectConfig
    paths: PathResolver

    @property
    def kitsu(self):
        return self.pipeline.kitsu

    @property
    def code(self) -> str:
        return self.project.code

    def path_context(self, **over) -> PathContext:
        """A `PathContext` pre-filled with this project's coordinates. Callers
        add `sequence=`, `shot=`, `task=`, `version=`, `frame=`, ..."""
        base = dict(
            nas_root=self.pipeline.nas_root,
            project=self.project.code,
            resolution=self.config.resolution,
            fps=str(self.config.fps or ""),
        )
        base.update(over)
        return PathContext(**base)

    # short alias
    ctx = path_context


@dataclass
class PipelineContext:
    config: PipelineConfig
    kitsu: object                       # KitsuApi | OfflineApi
    user: User
    _nas_root_name: str = "default"
    _project_cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------

    @classmethod
    def connect(cls, *, offline: bool = False, config_path=None,
                nas_root: str = "default") -> "PipelineContext":
        cfg = PipelineConfig.load(config_path)
        if offline:
            api = _kitsu.OfflineApi()
        else:
            api = _kitsu.connect(cfg.kitsu_host)     # raises NeedsLogin
        return cls(config=cfg, kitsu=api, user=api.current_user(), _nas_root_name=nas_root)

    @property
    def offline(self) -> bool:
        return isinstance(self.kitsu, _kitsu.OfflineApi)

    @property
    def nas_root(self) -> str:
        return self.config.nas_root(self._nas_root_name)

    def project_root(self, code: str) -> str:
        return f"{self.nas_root}/{code}"

    def project(self, ref: str) -> ProjectContext:
        if ref in self._project_cache:
            return self._project_cache[ref]
        proj = self.kitsu.project(ref)
        if proj is None:
            raise PipelineError(f"no project {ref!r} in Kitsu")
        if not proj.root_path:
            proj.root_path = self.project_root(proj.code)

        try:
            cfg = ProjectConfig.load(proj.root_path)
        except ConfigError as e:
            logger.warning("no project config at %s (%s) -- using site defaults",
                           proj.root_path, e)
            cfg = ProjectConfig.from_defaults(self.config.project_defaults)

        pctx = ProjectContext(self, proj, cfg, PathResolver(cfg))
        self._project_cache[ref] = pctx
        return pctx
