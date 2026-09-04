"""square_core.config

- `pipeline.PipelineConfig` -- per-install site config (Kitsu host, NAS roots,
  project_defaults, template list). Read from `studio_config.json`.
- `project.ProjectConfig` -- per-project path/naming config, on the NAS at
  `{project_root}/_pipeline/project_config.json`, written by `projects.create`.

See `docs/config_and_paths.md`.
"""

from __future__ import annotations

from .project import ProjectConfig, ConfigError, DEFAULT_PROJECT_CONFIG
from .pipeline import PipelineConfig
from .conventions import SHOT_FOLDER_STRUCTURE
from . import schema
from .schema import ConfigKey, SchemaError

__all__ = [
    "PipelineConfig",
    "ProjectConfig",
    "ConfigError",
    "DEFAULT_PROJECT_CONFIG",
    "SHOT_FOLDER_STRUCTURE",
    "schema",
    "ConfigKey",
    "SchemaError",
]
