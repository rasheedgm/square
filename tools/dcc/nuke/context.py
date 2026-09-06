"""The Nuke session's pipeline coordinates: project / sequence / shot / task.

A Nuke launched by another Square tool inherits these from `SQUARE_*` env vars;
the panel lets the artist change them. `Target` is the plain value object every
`ops` call takes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV = {
    "project": "SQUARE_PROJECT",
    "sequence": "SQUARE_SEQUENCE",
    "shot": "SQUARE_SHOT",
    "task_type": "SQUARE_TASK",
}


@dataclass
class Target:
    project: str = ""
    sequence: str = ""
    shot: str = ""
    task_type: str = ""

    @property
    def complete(self) -> bool:
        return all((self.project, self.sequence, self.shot, self.task_type))

    def label(self) -> str:
        if not self.complete:
            return "(no shot selected)"
        return f"{self.project} · {self.sequence}/{self.shot} · {self.task_type}"


def from_env(environ=None) -> Target:
    e = environ if environ is not None else os.environ
    return Target(**{k: (e.get(var) or "").strip() for k, var in ENV.items()})


def to_env(target: Target, environ=None) -> None:
    """Push a target into the environment so a subprocess (a render, a farm
    submit) sees the same coordinates."""
    e = environ if environ is not None else os.environ
    for k, var in ENV.items():
        v = getattr(target, k, "")
        if v:
            e[var] = v
        else:
            e.pop(var, None)
