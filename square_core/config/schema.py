"""The config-key registry.

Config on disk is JSON, but every key has a known type, scope, default and
(sometimes) an allowed range. This module is that description:

  - `ConfigKey`         -- one key's descriptor
  - `register(...)`     -- add a key (studio + tools call this at import)
  - `all()` / `for_scope()` / `get()` -- read the registry
  - `resolve(...)`      -- a key's effective value for a project
  - `validate(...)`     -- type / range / required / unknown-key checks

The **config editor** tool (the only writer of config) renders one field per
`ConfigKey`. `ProjectConfig.check()` / `PipelineConfig.check()` call
`validate()`. See `docs/config_schema.md`.

Pure stdlib. `square_core.paths` runs the template-specific checks separately
(from `ProjectConfig.check()`), so this module never imports it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

logger = logging.getLogger("square.config.schema")

SCOPES = ("studio", "project", "both")

# kinds that render as a plain editor field + get a value-type check here
SCALAR_KINDS = ("str", "int", "float", "bool", "path", "enum")
CONTAINER_KINDS = ("list", "dict")
# kinds the editor opens a specialised sub-editor for and that PathResolver,
# not this module, validates in depth
STRUCTURED_KINDS = ("template", "root", "media_type_registry", "delivery_registry")
KINDS = SCALAR_KINDS + CONTAINER_KINDS + STRUCTURED_KINDS


class SchemaError(RuntimeError):
    """Two registrations disagree about one key."""


@dataclass(frozen=True)
class ConfigKey:
    key: str                              # dotted path, e.g. "tools.ingest.copy_workers"
    kind: str                             # one of KINDS
    scope: str = "both"                   # "studio" | "project" | "both"
    default: Any = None
    description: str = ""
    choices: tuple = ()                   # enum
    minimum: float | None = None          # int / float
    maximum: float | None = None
    item_kind: str = ""                   # for "list": the kind of each item
    required: bool = False                # must be present & non-empty in that scope
    secret: bool = False                  # never render / log the value in plain text

    def __post_init__(self):
        if self.kind not in KINDS:
            raise SchemaError(f"{self.key}: unknown kind {self.kind!r}")
        if self.scope not in SCOPES:
            raise SchemaError(f"{self.key}: scope must be one of {SCOPES}, not {self.scope!r}")
        if self.kind == "enum" and not self.choices:
            raise SchemaError(f"{self.key}: kind 'enum' needs choices")
        if self.item_kind and self.item_kind not in SCALAR_KINDS:
            raise SchemaError(f"{self.key}: item_kind {self.item_kind!r} not a scalar kind")

    def applies_to(self, scope: str) -> bool:
        return self.scope == scope or self.scope == "both" or scope == "both"


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

_REGISTRY: dict[str, ConfigKey] = {}


def register(key: str, kind: str, *, scope: str = "both", default: Any = None,
             description: str = "", choices=(), minimum=None, maximum=None,
             item_kind: str = "", required: bool = False, secret: bool = False) -> ConfigKey:
    """Add a key to the registry. Idempotent when the descriptor is identical
    (modules get imported more than once); raises `SchemaError` on a conflict
    (two tools claiming one key with different rules)."""
    ck = ConfigKey(key=key, kind=kind, scope=scope, default=default,
                   description=description, choices=tuple(choices),
                   minimum=minimum, maximum=maximum, item_kind=item_kind,
                   required=required, secret=secret)
    existing = _REGISTRY.get(key)
    if existing is not None and existing != ck:
        raise SchemaError(
            f"config key {key!r} already registered as {existing}; refused to "
            f"re-register as {ck}")
    _REGISTRY[key] = ck
    return ck


def unregister(key: str) -> None:
    _REGISTRY.pop(key, None)


def clear() -> None:
    """Drop everything and re-seed the built-ins (tests)."""
    _REGISTRY.clear()
    _register_builtins()


def get(key: str) -> ConfigKey | None:
    return _REGISTRY.get(key)


def all() -> dict[str, ConfigKey]:
    return dict(_REGISTRY)


def for_scope(scope: str) -> list[ConfigKey]:
    if scope not in SCOPES:
        raise SchemaError(f"unknown scope {scope!r}")
    return [ck for ck in _REGISTRY.values() if ck.applies_to(scope)]


# --------------------------------------------------------------------------
# dotted-path access
# --------------------------------------------------------------------------

_MISSING = object()


def _dig(data: dict, dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def put(data: dict, dotted: str, value: Any) -> None:
    """Set a dotted key, creating intermediate dicts. Used by the editor."""
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def resolve(project_data: dict | None, key: str, *,
            pipeline_defaults: dict | None = None) -> Any:
    """The effective value of `key` for a project:

      1. the project's own config
      2. `PipelineConfig.project_defaults` (snapshotted at project create)
      3. the `ConfigKey.default`

    `pipeline_defaults` is `PipelineConfig.project_defaults`. For a
    `scope="studio"` key pass the studio config as `project_data` and omit
    `pipeline_defaults`.
    """
    ck = _REGISTRY.get(key)
    for src in (project_data, pipeline_defaults):
        if src is None:
            continue
        v = _dig(src, key)
        if v is not _MISSING:
            return v
    return ck.default if ck else None


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _kind_ok(kind: str, v: Any) -> bool:
    if kind == "str" or kind == "path":
        return isinstance(v, str)
    if kind == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if kind == "float":
        return _is_number(v)
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "enum":
        return True                       # choice membership checked by caller
    if kind == "list":
        return isinstance(v, list)
    if kind in ("dict",) + STRUCTURED_KINDS:
        return isinstance(v, dict)
    return True


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def check_value(ck: ConfigKey, v: Any) -> list[str]:
    """Errors for one key's value against its descriptor. Empty list == ok."""
    errs: list[str] = []
    if not _kind_ok(ck.kind, v):
        errs.append(f"{ck.key}: expected {ck.kind}, got {type(v).__name__}")
        return errs
    if ck.kind == "enum" and v not in ck.choices:
        errs.append(f"{ck.key}: {v!r} not one of {list(ck.choices)}")
    if ck.kind in ("int", "float") and _is_number(v):
        if ck.minimum is not None and v < ck.minimum:
            errs.append(f"{ck.key}: {v} below minimum {ck.minimum}")
        if ck.maximum is not None and v > ck.maximum:
            errs.append(f"{ck.key}: {v} above maximum {ck.maximum}")
    if ck.kind == "list" and ck.item_kind and isinstance(v, list):
        for i, item in enumerate(v):
            if not _kind_ok(ck.item_kind, item):
                errs.append(f"{ck.key}[{i}]: expected {ck.item_kind}, got {type(item).__name__}")
    return errs


def validate(data: dict, scope: str) -> tuple[list[str], list[str]]:
    """`(errors, warnings)` for a whole config blob at `scope`.

      - a `required` key for this scope that is absent / empty  -> error
      - a present key whose value violates its descriptor        -> error
      - a key present on disk but not in the registry            -> warning
        (usually a tool that isn't installed here; sometimes a typo)

    `template` / `root` / `media_type_registry` / `delivery_registry` keys get
    only a shape check here -- `PathResolver.validate()` does the deep checks,
    driven from `ProjectConfig.check()`.
    """
    errors: list[str] = []
    warnings: list[str] = []
    keys = for_scope(scope)

    # `required` is about the config a tool finally consumes -- the project one
    # (studio keys are consumed as-is). A `scope="both"` key may be absent from
    # `studio_config.json` and supplied by `project_defaults` / the project.
    enforce_required = (scope != "studio")

    for ck in keys:
        v = _dig(data, ck.key)
        if v is _MISSING:
            if ck.required and (enforce_required or ck.scope == "studio"):
                errors.append(f"{ck.key}: required but missing")
            continue
        if ck.required and _empty(v) and (enforce_required or ck.scope == "studio"):
            errors.append(f"{ck.key}: required but empty")
            continue
        errors.extend(check_value(ck, v))

    known = {ck.key for ck in _REGISTRY.values()}
    for path in _leaf_paths(data):
        if path in known:
            continue
        if any(k.startswith(path + ".") for k in known):
            continue                       # a container above known keys
        warnings.append(f"{path}: not a known config key")
    return errors, warnings


def _leaf_paths(data: dict, prefix: str = "") -> list[str]:
    """Every dotted path in `data`, stopping at any key that is registered
    (whatever its kind) -- a registered `dict`/`root`/`media_type_registry`
    key is a leaf, not something to recurse into."""
    out: list[str] = []
    for k, v in (data.items() if isinstance(data, dict) else []):
        p = f"{prefix}.{k}" if prefix else k
        if p not in _REGISTRY and isinstance(v, dict):
            out.extend(_leaf_paths(v, p))   # recurse; an empty {} contributes nothing
        else:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# built-in keys  (kept in sync with DEFAULT_PROJECT_CONFIG)
# --------------------------------------------------------------------------

def _register_builtins() -> None:
    # --- studio -----------------------------------------------------
    register("kitsu_host", "str", scope="studio", default="http://localhost/api",
             required=True, description="Kitsu API base URL")
    register("nas_roots", "dict", scope="studio", default={"default": "X:/projects"},
             required=True, description="named NAS roots; a project picks one by name")
    register("kitsu_project_templates", "list", item_kind="str", scope="studio",
             default=[], description="Kitsu project templates offered at project create")
    register("project_defaults", "dict", scope="studio", default={},
             description="a ProjectConfig template copied into each new project")

    # --- project scalars (also studio-default-able) ---------------
    register("schema_version", "int", scope="both", default=2, minimum=1)
    register("fps", "float", scope="both", default=24.0, minimum=1.0, maximum=240.0,
             description="project frame rate")
    register("resolution", "str", scope="both", default="3840x2160",
             description="WxH in pixels")
    register("aspect_ratio", "str", scope="both", default="")
    register("version_pad", "int", scope="both", default=3, minimum=1, maximum=6,
             description="zero-pad width for {version}")
    register("frame_pad", "int", scope="both", default=4, minimum=1, maximum=9,
             description="zero-pad width for {frame}")
    register("copy_workers", "int", scope="both", default=4, minimum=1, maximum=32,
             description="parallel file copies for a media.publish transfer")

    register("colorspace.ocio", "path", scope="both", default="",
             description="path to an OCIO config, or empty for the studio default")
    register("colorspace.working", "str", scope="both", default="ACEScg")
    register("colorspace.delivery", "str", scope="both", default="Rec.709")
    register("colorspace.plate_assumed", "str", scope="both", default="ACEScg",
             description="colorspace assumed for a delivered plate that declares none")

    register("slugify", "dict", scope="both",
             default={"spaces_to": "_", "strip": '<>:"/\\|?*', "collapse": "_"},
             description="how a token value is cleaned for the filesystem")

    # --- structured (PathResolver does the deep validation) -------
    # NOT required: each falls back to the matching DEFAULT_PROJECT_CONFIG
    # entry (deep-merged, per sub-key) when absent from a file entirely --
    # see ProjectConfig.roots / .media_type() / .delivery_template(). A file
    # only needs to hold what it actually overrides.
    register("roots", "root", scope="both",
             default={}, description="named path roots; each may reference {<name>_root}")
    register("media_types", "media_type_registry", scope="both",
             default={}, description="every ingest / render / workfile media type")
    register("delivery_presets", "delivery_registry", scope="project", default={},
             description="per-client delivery packaging")

    # --- folder-structure lists ----------------------------------
    register("shot_folder_structure", "list", item_kind="str", scope="both", default=[])
    register("asset_folder_structure", "list", item_kind="str", scope="both", default=[])
    register("project_folder_structure", "list", item_kind="str", scope="both",
             default=["shots", "assets", "_delivery", "_pipeline"])

    # No `tools.*` keys here -- each desktop tool registers its own
    # `tools.<tool>.*` descriptors with `schema.register()` at import time.
    # `square_core` itself ships no tool.


_register_builtins()
