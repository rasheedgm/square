"""PipelineConfig -- the per-install site config.

Kitsu host, the named NAS roots, the list of Kitsu project templates, and
`project_defaults` (a whole `ProjectConfig` blob minus the per-show values,
copied into each new project by `projects.create`).

Read from `studio_config.json` (or `$STUDIO_CONFIG_PATH`). This is the pipeline
successor to the ingest-era `square_core.config.StudioConfig` -- that one stays
until the ingest tool is ported.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .project import DEFAULT_PROJECT_CONFIG, ConfigError

_DEFAULT_HOST = os.getenv("KITSU_URL", "http://localhost/api")
_DEFAULT_NAS = os.getenv("SQUARE_NAS_ROOT", "X:/projects")


@dataclass
class PipelineConfig:
    kitsu_host: str = _DEFAULT_HOST
    nas_roots: dict = field(default_factory=lambda: {"default": _DEFAULT_NAS})
    kitsu_project_templates: list = field(default_factory=list)
    project_defaults: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_PROJECT_CONFIG)))
    source_path: str = ""

    # ------------------------------------------------------------------

    def nas_root(self, name: str = "default") -> str:
        if name in self.nas_roots:
            return str(self.nas_roots[name]).replace("\\", "/").rstrip("/")
        if "default" in self.nas_roots:
            return str(self.nas_roots["default"]).replace("\\", "/").rstrip("/")
        raise ConfigError(f"no NAS root named {name!r} and no 'default'")

    def project_root(self, project_code: str, nas_root_name: str = "default") -> str:
        return f"{self.nas_root(nas_root_name)}/{project_code}"

    # ------------------------------------------------------------------

    @classmethod
    def _resolve_path(cls, path: str | Path | None) -> Path | None:
        if path:
            return Path(path)
        env = os.environ.get("STUDIO_CONFIG_PATH")
        if env:
            return Path(env)
        # repo checkout: <repo>/studio_config.json (this file is
        # square_core/config/pipeline.py -> three parents up is the repo root)
        repo = Path(__file__).parent.parent.parent / "studio_config.json"
        return repo if repo.exists() else None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PipelineConfig":
        p = cls._resolve_path(path)
        cfg = cls()
        if p and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                raise ConfigError(f"{p} is not valid JSON: {e}") from e
            cfg.source_path = str(p)
            cfg.kitsu_host = _clean_url(data.get("kitsu_host") or data.get("kitsu_url") or cfg.kitsu_host)
            roots = data.get("nas_roots")
            if isinstance(roots, dict) and roots:
                cfg.nas_roots = roots
            elif data.get("nas_root"):
                cfg.nas_roots = {"default": data["nas_root"]}
            cfg.kitsu_project_templates = list(data.get("kitsu_project_templates") or [])
            pd = data.get("project_defaults")
            if isinstance(pd, dict) and pd:
                merged = json.loads(json.dumps(DEFAULT_PROJECT_CONFIG))
                merged.update(pd)
                cfg.project_defaults = merged
        return cfg

    # ------------------------------------------------------------------

    def as_dict(self) -> dict:
        return {
            "kitsu_host": self.kitsu_host,
            "nas_roots": dict(self.nas_roots),
            "kitsu_project_templates": list(self.kitsu_project_templates),
            "project_defaults": self.project_defaults,
        }

    def check(self) -> None:
        """Schema validation of the studio config. Raises `ConfigError`; logs a
        warning per unknown key."""
        import logging

        from . import schema

        errors, warnings = schema.validate(self.as_dict(), "studio")
        for w in warnings:
            logging.getLogger("square.config.pipeline").warning("studio config: %s", w)
        if errors:
            raise ConfigError("invalid studio config:\n  - " + "\n  - ".join(errors))


def _clean_url(url: str) -> str:
    if not url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    while "//" in rest:
        rest = rest.replace("//", "/")
    return f"{scheme}://{rest}"
