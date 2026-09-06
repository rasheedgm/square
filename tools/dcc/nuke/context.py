"""The Nuke session's pipeline coordinates: project / episode / sequence / shot
/ task.

A Nuke launched by another Square tool inherits these from `SQUARE_*` env vars;
the panels and the Square gizmo tabs let the artist change them. `Target` is
the plain value object every `ops` call takes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV = {
    "project": "SQUARE_PROJECT",
    "episode": "SQUARE_EPISODE",
    "sequence": "SQUARE_SEQUENCE",
    "shot": "SQUARE_SHOT",
    "task_type": "SQUARE_TASK",
}


@dataclass
class Target:
    project: str = ""
    episode: str = ""
    sequence: str = ""
    shot: str = ""
    task_type: str = ""

    @property
    def complete(self) -> bool:
        return all((self.project, self.sequence, self.shot, self.task_type))

    def label(self) -> str:
        if not (self.project and self.sequence and self.shot):
            return "(no shot selected)"
        ep = f"{self.episode}/" if self.episode else ""
        task = f" · {self.task_type}" if self.task_type else ""
        return f"{self.project} · {ep}{self.sequence}/{self.shot}{task}"


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
