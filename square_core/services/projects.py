"""projects -- create / plan / archive a project.

`create` is the load-bearing one: one call yields a Kitsu project (with a
template applied and a minimal file_tree), its NAS root + folder skeleton, and
its `project_config.json`. Partial results are not left behind on the happy
path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from square_core.config import ProjectConfig
from square_core.model import ProjectCreated
from square_core.storage import layout

logger = logging.getLogger("square.services.projects")


@dataclass
class ProjectSpec:
    code: str
    name: str = ""
    production_type: str = "short"          # short | tvshow | feature | commercial
    kitsu_template: str = ""
    nas_root: str = "default"               # a key in PipelineConfig.nas_roots
    client: str = ""
    fps: float | None = None
    resolution: str = ""
    overrides: dict = field(default_factory=dict)   # any ProjectConfig key


def plan(pipeline, spec: ProjectSpec):
    """Just the Kitsu shell -- no storage, no config. For a bid / pre-award."""
    proj = pipeline.kitsu.create_project(
        code=spec.code, name=spec.name or spec.code,
        production_type=spec.production_type,
        fps=spec.fps, resolution=spec.resolution,
    )
    return proj


def create(pipeline, spec: ProjectSpec) -> ProjectCreated:
    kitsu = pipeline.kitsu

    proj = kitsu.create_project(
        code=spec.code, name=spec.name or spec.code,
        production_type=spec.production_type,
        kitsu_template=spec.kitsu_template,
        fps=spec.fps, resolution=spec.resolution,
    )

    root = pipeline.config.project_root(proj.code, spec.nas_root)
    proj.root_path = root

    overrides = dict(spec.overrides)
    if spec.fps:
        overrides.setdefault("fps", spec.fps)
    if spec.resolution:
        overrides.setdefault("resolution", spec.resolution)
    cfg = ProjectConfig.from_defaults(pipeline.config.project_defaults, overrides=overrides)
    cfg_path = cfg.save(root)

    created = layout.create_tree(root, cfg.project_folder_structure)

    logger.info("created project %s at %s (%d folders)", proj.code, root, len(created))
    return ProjectCreated(project=proj, config_path=str(cfg_path),
                          folders_created=created, kitsu_template=spec.kitsu_template)


def archive(pipeline, project_ref: str):
    """Move the Kitsu project to a Closed status. Storage is left in place
    (a cold-storage move is a separate, later step)."""
    proj = pipeline.kitsu.project(project_ref)
    if proj is None:
        raise ValueError(f"no project {project_ref!r}")
    return pipeline.kitsu.set_project_status(proj, "Closed")
