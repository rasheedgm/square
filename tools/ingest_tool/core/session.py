"""
IngestSession -- the user-named, user-placed `*.sqingest.json` that lets an
ingest be stopped and resumed (crash, close, or just "finish tomorrow").

No hidden sidecars: everything needed to resume is in this one file -- the
project CODE (not a config snapshot -- a resume reads the LIVE ProjectConfig
through PipelineContext, same as every other tool; a snapshot is itself
editable between opens, not real reproducibility), the delivery root and its
Path Patterns, and every row's full state including which ones already
ingested.

Autosave writes to the path the user chose; there's a small debouncer here
for that, framework-agnostic (the UI ticks it or lets its timer thread
fire).
"""

from __future__ import annotations

import os
import json
import time
import uuid
import threading
import datetime
from dataclasses import dataclass, field, asdict

from .item import IngestItem

SESSION_SUFFIX = ".sqingest.json"
SCHEMA_VERSION = 2   # v2: project_code replaces config_snapshot + project

try:
    from square_core import __version__ as _APP_VERSION
except Exception:   # pragma: no cover
    _APP_VERSION = "0"


def _utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class IngestSession:
    schema_version: int = SCHEMA_VERSION
    saved_at: str = ""
    app_version: str = _APP_VERSION

    project_code: str = ""
    task_types: list = field(default_factory=list)
    dry_run: bool = False

    delivery_root: str = ""
    path_patterns: list = field(default_factory=list)
    manual_media_types: dict = field(default_factory=dict)
    active_preset: str = ""

    batch_id: str = ""
    items: list = field(default_factory=list)        # list[IngestItem.to_dict()]
    undo_stack: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Build from / apply to a controller
    # ------------------------------------------------------------------

    @classmethod
    def capture(cls, controller, *, delivery_root="", path_patterns=None,
                manual_media_types=None, active_preset="", dry_run=False) -> "IngestSession":
        return cls(
            saved_at=_utcnow(),
            project_code=controller.pctx.code,
            task_types=list(controller.task_types or []),
            dry_run=bool(dry_run),
            delivery_root=delivery_root or "",
            path_patterns=list(path_patterns or []),
            manual_media_types=dict(manual_media_types or {}),
            active_preset=active_preset or "",
            batch_id=controller.batch_id,
            items=[it.to_dict() for it in controller.items],
            undo_stack=list(getattr(controller, "_undo", []) or []),
        )

    def restore_into(self, controller) -> None:
        """
        Repopulate an already-constructed controller (built with the deps and
        the config this session carries) from the saved item state.
        Completed rows come back locked; the rest exactly where they were.
        """
        controller.batch_id = self.batch_id or controller.batch_id
        controller.items.clear()
        controller._by_key.clear()
        controller._scanned.clear()
        controller._slot_state.clear()
        controller._undo = list(self.undo_stack or [])

        for d in self.items:
            it = IngestItem.from_dict(d)
            controller.items.append(it)
            controller._by_key[it.key] = it
            # hashes + probe survive in the file; no need to redo them on resume
            if it.hashes and it.preflight_done:
                controller._scanned.add(it.key)

        controller._emit(
            "items_loaded",
            payload={"restored": [i.key for i in controller.items],
                     "total": len(controller.items), "resumed": True},
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IngestSession":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    def save(self, path) -> str:
        path = _normalize_path(path)
        self.saved_at = _utcnow()
        self.app_version = _APP_VERSION
        tmp = f"{path}.tmp-{uuid.uuid4().hex}"
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        os.replace(tmp, path)   # atomic -- a crash mid-write can't corrupt the last good session
        return path

    @classmethod
    def load(cls, path) -> "IngestSession":
        # No migration path, deliberately -- nothing has shipped yet, so
        # there's no old-shape session file to accommodate (decisions.md "No
        # migration before v1.0"). A version mismatch means re-run the ingest
        # from a fresh session, not a silently-patched-up old one.
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        version = int(data.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"session file is schema v{version}, this build understands "
                f"v{SCHEMA_VERSION} -- start a fresh session (no migration path before v1.0)"
            )
        return cls.from_dict(data)


def _normalize_path(path) -> str:
    p = str(path)
    if not p.endswith(SESSION_SUFFIX):
        # allow the user to type "showX" or "showX.json" and still get one clean suffix
        if p.endswith(".json"):
            p = p[:-5]
        p += SESSION_SUFFIX
    return p


# ---------------------------------------------------------------------------
# Autosave debouncer -- framework-agnostic
# ---------------------------------------------------------------------------

class SessionAutosaver:
    """
    Coalesces a burst of changes into one write ~`delay` seconds after the
    last one. `mark_dirty()` on every controller change; `flush()` to force
    an immediate write (on close, before ingest, on explicit Save).

    Uses a daemon timer thread so it works with any UI; the save callback
    must be safe to call off the UI thread (writing JSON is).
    """

    def __init__(self, save_fn, *, delay: float = 1.0):
        self._save_fn = save_fn
        self._delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._dirty = False
        self.last_saved_at: float | None = None
        self.last_error: str = ""

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
        self._do_save()

    def flush(self) -> bool:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            was_dirty = self._dirty
            self._dirty = False
        if was_dirty:
            return self._do_save()
        return True

    def _do_save(self) -> bool:
        try:
            self._save_fn()
            self.last_saved_at = time.time()
            self.last_error = ""
            return True
        except Exception as e:   # autosave failure must never crash the tool
            self.last_error = str(e)
            return False

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


# ---------------------------------------------------------------------------
# "Reopen last session?" -- a tiny recent-files store, no Qt
# ---------------------------------------------------------------------------

def _history_path() -> str:
    base = os.environ.get("SQUARE_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".square"
    )
    return os.path.join(base, "ingest_recent.json")


def remember_session(path: str, *, limit: int = 10) -> None:
    path = os.path.abspath(str(path))
    hp = _history_path()
    recent = recent_sessions()
    recent = [p for p in recent if p != path]
    recent.insert(0, path)
    recent = recent[:limit]
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    try:
        with open(hp, "w", encoding="utf-8") as fh:
            json.dump({"recent": recent}, fh, indent=2)
    except OSError:
        pass


def recent_sessions() -> list:
    try:
        with open(_history_path(), "r", encoding="utf-8") as fh:
            return list(json.load(fh).get("recent", []))
    except (OSError, ValueError):
        return []


def last_session() -> str | None:
    for p in recent_sessions():
        if os.path.exists(p):
            return p
    return None
