"""ProjectConfig -- the per-project path/naming/convention config that travels
with the project on the NAS (`{project_root}/_pipeline/project_config.json`).

Written by `projects.create` from `StudioConfig.project_defaults` + a thin
`ProjectSpec`. Read live by every tool -- never snapshotted into a session.
`load()` validates and refuses a broken config (a bad template means writes
land in the wrong place). Schema: `docs/config_and_paths.md` §4.

Pure-ish: stdlib + `square_core.paths` (for template validation) only.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .conventions import SHOT_FOLDER_STRUCTURE

SCHEMA_VERSION = 1
PIPELINE_DIRNAME = "_pipeline"
PROJECT_CONFIG_FILENAME = "project_config.json"


class ConfigError(RuntimeError):
    """A project config is structurally invalid or its templates don't hold up."""


# --------------------------------------------------------------------------
# Built-in default -- the shape `StudioConfig.project_defaults` should hold,
# seeded from today's config.py constants so behaviour matches the ingest tool.
# --------------------------------------------------------------------------

DEFAULT_PROJECT_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "fps": 24.0,
    "resolution": "3840x2160",
    "aspect_ratio": "",
    "colorspace": {
        "ocio": "",
        "working": "ACEScg",
        "delivery": "Rec.709",
        "plate_assumed": "ACEScg",
    },
    "version_pad": 3,
    "frame_pad": 4,
    "slugify": {"spaces_to": "_", "strip": '<>:"/\\|?*', "collapse": "_"},
    "roots": {
        "project": "{nas_root}/{project}",
        "shot": "{project_root}/{episode}/shots/{sequence}/{shot}",
        "asset": "{project_root}/assets/{asset_type}/{asset}",
        "delivery": "{project_root}/_delivery",
    },
    "templates": {
        "workfile": {
            "base": "shot",
            "dir": "work/{task}/{software}",
            "file": "{project}_{sequence}_{shot}_{task}_{name}_v{version}.{ext}",
        },
        "output": {
            "base": "shot",
            "dir": "output/{output_type}/v{version}/{representation}",
            "file": "{project}_{sequence}_{shot}_{output_type}_{name}_v{version}.{frame}.{ext}",
        },
    },
    "ingest": {
        "default": {
            "base": "shot",
            "dir": "input/{media_type}/{name}_v{version}",
            "file": "{project}_{sequence}_{shot}_{media_type}_{name}_v{version}.{frame}.{ext}",
        },
        "by_type": {
            "Plate": {"dir": "plates/{name}_v{version}"},
            "Ref": {"dir": "ref/{name}_v{version}"},
            "BG Plate": {"dir": "bg_plates/{name}_v{version}"},
            "Comp Render": {"dir": "comp/{name}_v{version}"},
            "Precomp": {"dir": "precomp/{name}_v{version}"},
            "Element": {"dir": "elements/{name}_v{version}"},
            "LUT": {"dir": "luts/{name}_v{version}"},
            "Audio": {"dir": "audio/{name}_v{version}"},
            "Matte": {"dir": "mattes/{name}_v{version}"},
        },
    },
    "shot_folder_structure": list(SHOT_FOLDER_STRUCTURE),
    "asset_folder_structure": [],
    "project_folder_structure": ["shots", "assets", "_delivery", "_pipeline"],
    "delivery_presets": {
        "default": {
            "base": "delivery",
            "dir": "{client}/{package}",
            "file": "{shot}_{output_type}_v{version}.{frame}.{ext}",
            "case": "preserve",
            "container": "exr",
            "frame_pad": 4,
            "colorspace": "Rec.709",
            "slate": False,
            "burnin": [],
        },
    },
}

# fields the ingest tool still reads off a plain settings blob
_INGEST_RUN_DEFAULTS = {
    "copy_workers": 4,
    "transfer_mode": "copy",
    "preview_enabled_media_types": ["Plate", "Ref", "BG Plate", "Comp Render", "Precomp"],
}

_REQUIRED_TOP_KEYS = ("roots", "templates", "ingest")
_REQUIRED_ROOTS = ("project", "shot")


def _deep_merge(base: dict, over: dict | None) -> dict:
    out = json.loads(json.dumps(base))  # cheap deep copy
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class ProjectConfig:
    data: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_PROJECT_CONFIG)))

    # ---- typed views -----------------------------------------------------

    @property
    def fps(self) -> float:
        return float(self.data.get("fps") or 0.0)

    @property
    def resolution(self) -> str:
        return str(self.data.get("resolution") or "")

    @property
    def version_pad(self) -> int:
        return int(self.data.get("version_pad") or 3)

    @property
    def frame_pad(self) -> int:
        return int(self.data.get("frame_pad") or 4)

    @property
    def slugify(self) -> dict:
        return dict(self.data.get("slugify") or DEFAULT_PROJECT_CONFIG["slugify"])

    @property
    def colorspace(self) -> dict:
        return dict(self.data.get("colorspace") or {})

    @property
    def roots(self) -> dict:
        return dict(self.data.get("roots") or {})

    @property
    def templates(self) -> dict:
        return dict(self.data.get("templates") or {})

    @property
    def shot_folder_structure(self) -> list:
        return list(self.data.get("shot_folder_structure") or [])

    @property
    def asset_folder_structure(self) -> list:
        return list(self.data.get("asset_folder_structure") or [])

    @property
    def project_folder_structure(self) -> list:
        return list(self.data.get("project_folder_structure") or [])

    @property
    def delivery_presets(self) -> dict:
        return dict(self.data.get("delivery_presets") or {})

    # ---- template lookups (with inheritance) ---------------------------

    def template(self, kind: str) -> dict:
        """A `templates.<kind>` block (workfile / output)."""
        try:
            return dict(self.data["templates"][kind])
        except (KeyError, TypeError):
            raise ConfigError(f"no template block named {kind!r}")

    def ingest_template(self, media_type: str) -> dict:
        ing = self.data.get("ingest") or {}
        base = dict(ing.get("default") or {})
        override = (ing.get("by_type") or {}).get(media_type) or {}
        base.update(override)
        if not base:
            raise ConfigError("ingest.default is missing")
        return base

    def delivery_template(self, client: str = "") -> dict:
        presets = self.data.get("delivery_presets") or {}
        base = dict(presets.get("default") or {})
        if client and client in presets:
            base.update(presets[client])
        if not base:
            raise ConfigError("delivery_presets.default is missing")
        return base

    # ---- build / load / save ------------------------------------------

    @classmethod
    def from_defaults(cls, defaults: dict | None = None, *, overrides: dict | None = None) -> "ProjectConfig":
        merged = _deep_merge(defaults or DEFAULT_PROJECT_CONFIG, overrides)
        merged.setdefault("schema_version", SCHEMA_VERSION)
        cfg = cls(data=merged)
        cfg.check()
        return cfg

    @staticmethod
    def path_for(project_root: str | Path) -> Path:
        return Path(project_root) / PIPELINE_DIRNAME / PROJECT_CONFIG_FILENAME

    @classmethod
    def load(cls, project_root: str | Path) -> "ProjectConfig":
        p = cls.path_for(project_root)
        if not p.exists():
            raise ConfigError(f"no project config at {p}")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            raise ConfigError(f"{p} is not valid JSON: {e}") from e
        if int(data.get("schema_version", 1)) > SCHEMA_VERSION:
            raise ConfigError(
                f"{p} is schema v{data['schema_version']}, this build understands v{SCHEMA_VERSION}"
            )
        cfg = cls(data=data)
        cfg.check()
        return cfg

    def save(self, project_root: str | Path) -> Path:
        p = self.path_for(project_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return p

    # ---- validation --------------------------------------------------

    def structural_errors(self) -> list[str]:
        errs: list[str] = []
        for key in _REQUIRED_TOP_KEYS:
            if not isinstance(self.data.get(key), dict):
                errs.append(f"missing or non-object '{key}'")
        roots = self.data.get("roots") or {}
        for r in _REQUIRED_ROOTS:
            if not roots.get(r):
                errs.append(f"roots.{r} is required")
        tmpl = self.data.get("templates") or {}
        for kind in ("workfile", "output"):
            block = tmpl.get(kind)
            if not isinstance(block, dict) or "file" not in block:
                errs.append(f"templates.{kind} needs at least a 'file'")
        return errs

    def check(self) -> None:
        """Structural + template validation. Raises ConfigError on any problem."""
        errs = self.structural_errors()
        if not errs:
            # template rendering / version-variance checks live in the resolver
            from square_core.paths import PathResolver, PathError

            try:
                errs = PathResolver(self).validate()
            except PathError as e:
                errs = [str(e)]
        if errs:
            raise ConfigError("invalid project config:\n  - " + "\n  - ".join(errs))
