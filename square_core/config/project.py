"""ProjectConfig -- the per-project path/naming config that travels with the
project on the NAS (`{project_root}/_pipeline/project_config.json`).

Written by `projects.create` from `PipelineConfig.project_defaults` + a thin
`ProjectSpec`. Read live by every tool -- never snapshotted. `load()` validates
and refuses a broken config (a bad template means writes land in the wrong
place). Schema: `docs/config_and_paths.md`.

v2 (2026-09-04): one `media_types` registry -- ingest and render-output are the
same operation. `templates.output` / `templates.workfile` / `ingest.by_type`
are gone. A `schema_version < 2` file is migrated in memory on load.

Pure-ish: stdlib + `square_core.paths` (for template validation) only.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .conventions import SHOT_FOLDER_STRUCTURE

_log = logging.getLogger("square.config.project")

SCHEMA_VERSION = 2
PIPELINE_DIRNAME = "_pipeline"
PROJECT_CONFIG_FILENAME = "project_config.json"


class ConfigError(RuntimeError):
    """A project config is structurally invalid or its templates don't hold up."""


# --------------------------------------------------------------------------
# Built-in default -- the shape `PipelineConfig.project_defaults` should hold.
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
    "copy_workers": 4,          # parallel file copies for any media.publish transfer
    "slugify": {"spaces_to": "_", "strip": '<>:"/\\|?*', "collapse": "_"},

    "roots": {
        "project": "{nas_root}/{project}",
        "shot": "{project_root}/{episode}/shots/{sequence}/{shot}",
        "asset": "{project_root}/assets/{asset_type}/{asset}",
        "delivery": "{project_root}/_delivery",
    },

    # the media-type registry: every ingest / render / workfile goes through this.
    # each entry deep-merges over "_default".
    "media_types": {
        "_default": {
            "base": "shot",
            "dir": "input/{media_type}/{name}_v{version}",
            "file": "{project}_{sequence}_{shot}_{media_type}_{name}_v{version}.{frame}.{ext}",
            "kitsu_kind": "output",          # output -> Kitsu output_file; working -> working_file
            "source": "publish",            # delivery | publish | work -- where the media comes from;
                                            #   a tool offers the types matching its stage
            "previewable": False,
            "colorspace": "",               # assumed for this type if a file doesn't declare one
        },
        "Plate":      {"source": "delivery", "dir": "plates/{name}_v{version}", "previewable": True, "colorspace": "ACEScg"},
        "Ref":        {"source": "delivery", "dir": "ref/{name}_v{version}", "previewable": True},
        "BG Plate":   {"source": "delivery", "dir": "bg_plates/{name}_v{version}", "previewable": True, "colorspace": "ACEScg"},
        "Element":    {"source": "delivery", "dir": "elements/{name}_v{version}"},
        "LUT":        {"source": "delivery", "dir": "luts/{name}_v{version}"},
        "Audio":      {"source": "delivery", "dir": "audio/{name}_v{version}"},
        "Matte":      {"source": "delivery", "dir": "mattes/{name}_v{version}"},

        "CompRender": {"dir": "output/comp/v{version}/{representation}",
                       "file": "{project}_{sequence}_{shot}_comp_{name}_v{version}.{frame}.{ext}",
                       "previewable": True, "colorspace": "ACEScg"},
        "Precomp":    {"dir": "output/precomp/v{version}/{representation}",
                       "file": "{project}_{sequence}_{shot}_precomp_{name}_v{version}.{frame}.{ext}"},
        "Cache":      {"dir": "output/cache/{name}/v{version}", "representation": "abc",
                       "file": "{project}_{sequence}_{shot}_{name}_v{version}.{frame}.{ext}"},

        "NukeScript": {"kitsu_kind": "working", "source": "work",
                       "dir": "work/comp/nuke",
                       "file": "{project}_{sequence}_{shot}_comp_{name}_v{version}.nk"},
        "MayaScene":  {"kitsu_kind": "working", "source": "work",
                       "dir": "work/{task}/maya",
                       "file": "{project}_{sequence}_{shot}_{task}_{name}_v{version}.ma"},
    },

    "shot_folder_structure": list(SHOT_FOLDER_STRUCTURE),
    "asset_folder_structure": [],
    "project_folder_structure": ["shots", "assets", "_delivery", "_pipeline"],

    "delivery_presets": {
        "_default": {
            "base": "delivery",
            "dir": "{client}/{package}",
            "file": "{shot}_{media_type}_v{version}.{frame}.{ext}",
            "case": "preserve",
            "container": "exr",
            "frame_pad": 4,
            "colorspace": "Rec.709",
            "slate": False,
            "burnin": [],
        },
    },

    # per-tool settings. Each tool registers its own `tools.<tool>.*` keys with
    # `config.schema` when it is installed and writes them here through the
    # config editor -- core ships none (there is no tool inside `square_core`).
    "tools": {},
}

_DEFAULT_ENTRY = "_default"
_REQUIRED_ROOTS = ("project", "shot")
_MEDIA_SOURCES = ("delivery", "publish", "work")


def _deep_merge(base: dict, over: dict | None) -> dict:
    out = json.loads(json.dumps(base))  # cheap deep copy
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate_v1(data: dict) -> dict:
    """Fold a v1 `templates` + `ingest` config into a v2 `media_types` registry,
    in memory. Best-effort -- a v1 config was never in production."""
    if int(data.get("schema_version", 1)) >= 2:
        return data
    d = json.loads(json.dumps(data))
    templates = d.pop("templates", {}) or {}
    ingest = d.pop("ingest", {}) or {}

    mt: dict = {}
    base_default = dict(ingest.get("default") or {})
    base_default.setdefault("kitsu_kind", "output")
    base_default.setdefault("previewable", False)
    base_default.setdefault("colorspace", "")
    mt[_DEFAULT_ENTRY] = base_default or DEFAULT_PROJECT_CONFIG["media_types"][_DEFAULT_ENTRY]

    for name, entry in (ingest.get("by_type") or {}).items():
        mt[name] = {"source": "delivery", **dict(entry)}
    if templates.get("output"):
        mt.setdefault("Output", {**templates["output"], "kitsu_kind": "output"})
    if templates.get("workfile"):
        mt.setdefault("Workfile", {**templates["workfile"],
                                   "kitsu_kind": "working", "source": "work"})

    prev = set(d.pop("preview_enabled_media_types", []) or [])
    for name in prev:
        if name in mt:
            mt[name]["previewable"] = True

    d["media_types"] = mt
    d.pop("transfer_mode", None)          # now a per-publish arg, not stored
    d.setdefault("copy_workers", DEFAULT_PROJECT_CONFIG["copy_workers"])
    d.setdefault("tools", {})
    d["schema_version"] = SCHEMA_VERSION
    return d


@dataclass
class ProjectConfig:
    data: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_PROJECT_CONFIG)))

    # ---- typed views -------------------------------------------------

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
    def copy_workers(self) -> int:
        return int(self.data.get("copy_workers") or 4)

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
    def media_types(self) -> dict:
        return dict(self.data.get("media_types") or {})

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

    @property
    def tools(self) -> dict:
        return dict(self.data.get("tools") or {})

    def tool(self, name: str) -> dict:
        return dict((self.data.get("tools") or {}).get(name) or {})

    # ---- registry lookups (with inheritance) -----------------------

    def media_type(self, name: str) -> dict:
        """The fully-resolved config entry for a media type: the built-in
        `_default`, then this config's `_default`, then the named entry, each
        deep-merged on top. Merging the built-in first means a key added to
        `_default` in a newer release (e.g. `source`) is present even for a
        config written before it. An unknown name still resolves."""
        reg = self.data.get("media_types") or {}
        own_default = reg.get(_DEFAULT_ENTRY)
        if not isinstance(own_default, dict):
            raise ConfigError("media_types._default is missing")
        base = _deep_merge(DEFAULT_PROJECT_CONFIG["media_types"][_DEFAULT_ENTRY], own_default)
        return _deep_merge(base, reg.get(name) or {})

    def media_type_names(self, *, source: str | None = None) -> list:
        """Configured media-type names (never `_default`). With `source` given
        (`delivery` / `publish` / `work`), only the types a tool at that stage
        offers -- e.g. ingest passes `source="delivery"`, a DCC publish panel
        `source="publish"`. A missing `source` on an entry inherits `_default`'s."""
        names = [k for k in (self.data.get("media_types") or {}) if k != _DEFAULT_ENTRY]
        if source is None:
            return names
        return [n for n in names if self.media_type(n).get("source") == source]

    def delivery_template(self, client: str = "") -> dict:
        presets = self.data.get("delivery_presets") or {}
        base = dict(presets.get(_DEFAULT_ENTRY) or presets.get("default") or {})
        if client and client in presets:
            base = _deep_merge(base, presets[client])
        if not base:
            raise ConfigError("delivery_presets._default is missing")
        return base

    # ---- build / load / save --------------------------------------

    @classmethod
    def from_defaults(cls, defaults: dict | None = None, *, overrides: dict | None = None) -> "ProjectConfig":
        merged = _deep_merge(_migrate_v1(defaults or DEFAULT_PROJECT_CONFIG), overrides)
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
        cfg = cls(data=_migrate_v1(data))
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

    # ---- validation ----------------------------------------------

    def structural_errors(self) -> list[str]:
        errs: list[str] = []
        roots = self.data.get("roots")
        if not isinstance(roots, dict):
            errs.append("missing or non-object 'roots'")
            roots = {}
        for r in _REQUIRED_ROOTS:
            if not roots.get(r):
                errs.append(f"roots.{r} is required")

        reg = self.data.get("media_types")
        if not isinstance(reg, dict) or _DEFAULT_ENTRY not in reg:
            errs.append("media_types._default is required")
        elif "file" not in (reg.get(_DEFAULT_ENTRY) or {}):
            errs.append("media_types._default needs at least a 'file'")

        for name in [n for n in (reg or {}) if n != _DEFAULT_ENTRY]:
            entry = self.media_type(name)
            kind = entry.get("kitsu_kind", "output")
            if kind not in ("output", "working"):
                errs.append(f"media_types.{name}.kitsu_kind must be 'output' or 'working', not {kind!r}")
            src = entry.get("source", "publish")
            if src not in _MEDIA_SOURCES:
                errs.append(f"media_types.{name}.source must be one of {list(_MEDIA_SOURCES)}, not {src!r}")
        return errs

    def check(self) -> None:
        """Structural + schema + template validation. Raises ConfigError on any
        problem; logs a warning for each unknown key (a tool not installed here,
        or a typo)."""
        from . import schema

        errs = self.structural_errors()

        schema_errs, warnings = schema.validate(self.data, "project")
        errs.extend(schema_errs)
        for w in warnings:
            _log.warning("project config: %s", w)

        if not errs:
            from square_core.paths import PathResolver, PathError

            try:
                errs = PathResolver(self).validate()
            except PathError as e:
                errs = [str(e)]
        if errs:
            raise ConfigError("invalid project config:\n  - " + "\n  - ".join(errs))
