"""The config editor's headless core -- the ONLY writer of studio / project config.

`ConfigStore` loads `studio_config.json` and (optionally) a project's
`_pipeline/project_config.json`, exposes every editable key with its *effective*
value and where that value comes from, applies validated edits in memory, and
saves atomically with a timestamped `.bak` after a full `check()`.

The Qt editor is a thin shell over this. A CLI (`python -m tools.config_editor
--cli`) drives the same object. No Qt import here.

Write access is gated on the Kitsu user's role (`admin` / `manager`).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from square_core.config import ProjectConfig, PipelineConfig, ConfigError, schema
from square_core.config.pipeline import DEFAULT_PROJECT_CONFIG

ADMIN_ROLES = {"admin", "manager"}

_MISSING = object()


class NotAuthorized(RuntimeError):
    """The current user's Kitsu role may not write config."""


# --------------------------------------------------------------------------

@dataclass
class FieldView:
    """One config key as the editor should show it."""
    key: str
    kind: str
    scope: str
    value: Any                     # effective value (override / default / built-in)
    source: str                    # "project" | "studio" | "studio-default" | "builtin"
    description: str = ""
    choices: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    item_kind: str = ""
    required: bool = False
    secret: bool = False
    overridden: bool = False       # project scope: set in the project's own file

    @property
    def descriptor(self):
        return schema.get(self.key)


# --------------------------------------------------------------------------

def _dig(data: dict, dotted: str):
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _del_path(data: dict, dotted: str) -> bool:
    parts = dotted.split(".")
    cur = data
    for part in parts[:-1]:
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return False
    if isinstance(cur, dict) and parts[-1] in cur:
        del cur[parts[-1]]
        return True
    return False


def _clone(d: dict) -> dict:
    return json.loads(json.dumps(d))


def _atomic_write(path: Path, data: dict, *, backup: bool) -> Path | None:
    """Write `data` as pretty JSON to `path` atomically. If `backup` and the
    file exists, copy it to `<name>.bak-<ts>` first; return that backup path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    bak = None
    if backup and path.exists():
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = path.with_name(f"{path.name}.bak-{ts}")
        bak.write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return bak


# --------------------------------------------------------------------------

class ConfigStore:
    def __init__(self, pipeline: PipelineConfig, *, user=None, studio_path=None):
        self.pipeline = pipeline
        self.user = user
        self.studio_path = Path(studio_path or pipeline.source_path
                                or PipelineConfig._resolve_path(None)
                                or "studio_config.json")
        # a working copy of the raw studio file -- unknown / legacy keys are
        # preserved untouched on save
        self.studio_raw: dict = {}
        if self.studio_path.exists():
            self.studio_raw = json.loads(self.studio_path.read_text(encoding="utf-8"))

        self.project_root: Path | None = None
        self.project_code: str = ""
        self.project_raw: dict | None = None

    # ---- auth ---------------------------------------------------------

    @property
    def role(self) -> str:
        return (getattr(self.user, "role", "") or "").lower()

    def can_write(self) -> bool:
        return self.role in ADMIN_ROLES

    def _require_write(self) -> None:
        if not self.can_write():
            who = getattr(self.user, "email", None) or "offline session"
            raise NotAuthorized(
                f"{who} (role {self.role or 'none'!r}) may not edit config; "
                f"need one of {sorted(ADMIN_ROLES)}")

    # ---- project binding -------------------------------------------

    def open_project(self, project_root: str | Path, code: str = "") -> None:
        cfg = ProjectConfig.load(project_root)          # raises ConfigError if broken
        self.project_root = Path(project_root)
        self.project_code = code or self.project_root.name
        self.project_raw = _clone(cfg.data)

    def close_project(self) -> None:
        self.project_root = self.project_code = None
        self.project_raw = None

    @property
    def has_project(self) -> bool:
        return self.project_raw is not None

    # ---- provenance ----------------------------------------------

    def _studio_default(self, key: str):
        """The value a project inherits, and whether that came from a studio
        choice (`studio-default`) or is just the shipped value (`builtin`).
        `PipelineConfig.load` merges `project_defaults` over the full built-in
        config, so the distinction is: does it differ from the built-in?"""
        builtin = _dig(DEFAULT_PROJECT_CONFIG, key)
        if builtin is _MISSING:
            ck = schema.get(key)
            builtin = ck.default if ck else None
        v = _dig(self.pipeline.project_defaults or {}, key)
        if v is _MISSING:
            return builtin, "builtin"
        return v, ("studio-default" if v != builtin else "builtin")

    def field(self, scope: str, key: str) -> FieldView:
        ck = schema.get(key)
        if ck is None:
            raise KeyError(key)
        common = dict(key=key, kind=ck.kind, scope=ck.scope, description=ck.description,
                      choices=ck.choices, minimum=ck.minimum, maximum=ck.maximum,
                      item_kind=ck.item_kind, required=ck.required, secret=ck.secret)
        if scope == "studio":
            v = _dig(self.studio_raw, key)
            if v is not _MISSING:
                return FieldView(value=v, source="studio", **common)
            return FieldView(value=(ck.default), source="builtin", **common)

        # project scope: effective value is the project's own if present, else
        # the studio default. It counts as an *override* only when the project
        # file carries a value that differs from what the studio would give.
        if self.project_raw is None:
            raise RuntimeError("no project open")
        dflt, dflt_src = self._studio_default(key)
        own = _dig(self.project_raw, key)
        if own is not _MISSING and own != dflt:
            return FieldView(value=own, source="project", overridden=True, **common)
        val = own if own is not _MISSING else dflt
        return FieldView(value=val, source=dflt_src, **common)

    def fields(self, scope: str) -> list[FieldView]:
        return [self.field(scope, ck.key)
                for ck in sorted(schema.for_scope(scope), key=lambda c: c.key)]

    # ---- edits (in memory) -------------------------------------

    def _target(self, scope: str) -> dict:
        if scope == "studio":
            return self.studio_raw
        if self.project_raw is None:
            raise RuntimeError("no project open")
        return self.project_raw

    def set(self, scope: str, key: str, value: Any) -> None:
        ck = schema.get(key)
        if ck is None:
            raise KeyError(f"{key!r} is not a known config key")
        if not ck.applies_to(scope):
            raise ValueError(f"{key!r} is not editable at {scope} scope")
        errs = schema.check_value(ck, value)
        if errs:
            raise ValueError("; ".join(errs))
        schema.put(self._target(scope), key, value)

    def reset(self, key: str) -> bool:
        """Undo a project override: put the studio default value back (the
        project file stays complete and loadable). Returns True if the value
        changed. Project scope only."""
        if self.project_raw is None:
            raise RuntimeError("no project open")
        dflt, _ = self._studio_default(key)
        cur = _dig(self.project_raw, key)
        if cur == dflt:
            return False
        if dflt is None and schema.get(key) is None:
            return _del_path(self.project_raw, key)
        schema.put(self.project_raw, key, _clone({"_": dflt})["_"] if isinstance(dflt, (dict, list)) else dflt)
        return True

    # ---- diff vs disk ------------------------------------------

    def _on_disk(self, scope: str) -> dict:
        if scope == "studio":
            if self.studio_path.exists():
                return json.loads(self.studio_path.read_text(encoding="utf-8"))
            return {}
        p = ProjectConfig.path_for(self.project_root)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def pending(self, scope: str) -> dict:
        """`{key: (old, new)}` for every registered key whose value changed."""
        disk = self._on_disk(scope)
        cur = self._target(scope)
        out = {}
        for ck in schema.for_scope(scope):
            a, b = _dig(disk, ck.key), _dig(cur, ck.key)
            if a != b:
                out[ck.key] = (None if a is _MISSING else a,
                               None if b is _MISSING else b)
        return out

    # ---- validate + save -------------------------------------

    def validate(self, scope: str) -> tuple[list[str], list[str]]:
        if scope == "studio":
            return schema.validate(self.studio_raw, "studio")
        cfg = ProjectConfig(data=_clone(self.project_raw))
        try:
            cfg.check()
        except ConfigError as e:
            return [str(e)], []
        return schema.validate(self.project_raw, "project")

    def save_studio(self) -> tuple[Path, Path | None]:
        self._require_write()
        errs, _ = self.validate("studio")
        if errs:
            raise ConfigError("studio config invalid:\n  - " + "\n  - ".join(errs))
        bak = _atomic_write(self.studio_path, self.studio_raw, backup=True)
        return self.studio_path, bak

    def save_project(self) -> tuple[Path, Path | None]:
        self._require_write()
        if self.project_raw is None:
            raise RuntimeError("no project open")
        cfg = ProjectConfig(data=_clone(self.project_raw))
        cfg.check()                                    # raises ConfigError
        path = ProjectConfig.path_for(self.project_root)
        bak = _atomic_write(path, self.project_raw, backup=True)
        return path, bak
