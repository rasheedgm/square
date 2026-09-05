"""Named, reusable Ingest Presets -- a saved ordered list of Path Pattern
templates for a vendor whose delivery shape repeats.

Tool-local, like the recent-sessions history in `session.py`: NOT studio
config. `studio_config.json` is schema-described and the config editor is
its only writer (`decisions.md`); a person's personal collection of "here's
how ACME Studios structures their deliveries" presets doesn't belong there.
"""

from __future__ import annotations

import json
import os

_FILENAME = "ingest_presets.json"


def _path() -> str:
    base = os.environ.get("SQUARE_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".square")
    return os.path.join(base, _FILENAME)


def load() -> dict:
    """`{"presets": {name: {"name", "patterns": [template, ...]}}, "active": name}`."""
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault("presets", {})
    data.setdefault("active", "")
    return data


def save(data: dict) -> None:
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass
