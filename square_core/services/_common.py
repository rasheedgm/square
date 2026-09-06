"""Tiny shared helpers for the service layer -- coordinate extraction that both
`media` and `work` need. Not part of the public API."""

from __future__ import annotations


def entity_coords(entity) -> dict:
    """seq / shot / episode or asset coords from a model entity, as the token
    dict `pctx.ctx(**coords)` wants."""
    kind = type(entity).__name__.lower()
    if kind == "shot":
        return {"sequence": getattr(entity, "sequence_code", "") or "",
                "shot": getattr(entity, "code", "") or "",
                "episode": getattr(entity, "episode_code", "") or ""}
    if kind == "asset":
        return {"asset": getattr(entity, "code", "") or "",
                "asset_type": getattr(entity, "asset_type", "") or ""}
    return {}


def task_name(task) -> str:
    return (getattr(task, "task_type_name", "") or "").lower() or "task"
