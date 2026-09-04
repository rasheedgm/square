"""breakdown -- sequences, shots, and the task grid ("roadmap").

`ensure_shot` also creates the shot's folder skeleton on disk when the shot is
new or its directory is missing -- the one place that happens.
"""

from __future__ import annotations

import logging
from pathlib import Path

from square_core.storage import layout

logger = logging.getLogger("square.services.breakdown")


def ensure_sequence(pctx, code: str):
    return pctx.kitsu.ensure_sequence(pctx.project, code)


def ensure_shot(pctx, sequence_code: str, shot_code: str, *,
                frame_in: int = 1001, frame_out: int = 1100, fps: float | None = None,
                create_folders: bool = True):
    seq = pctx.kitsu.ensure_sequence(pctx.project, sequence_code)
    shot = pctx.kitsu.ensure_shot(
        pctx.project, seq, shot_code,
        frame_in=frame_in, frame_out=frame_out, fps=fps or pctx.config.fps or None,
    )
    shot.sequence_code = sequence_code

    if create_folders:
        ctx = pctx.ctx(sequence=sequence_code, shot=shot_code)
        shot_dir = pctx.paths.shot_dir(ctx)
        if not Path(shot_dir).exists():
            made = layout.create_tree(shot_dir, pctx.config.shot_folder_structure)
            logger.info("shot %s: created %d folders", shot_code, len(made))
    return shot


def create_asset(pctx, name: str, asset_type: str, *, create_folders: bool = True):
    asset = pctx.kitsu.ensure_asset(pctx.project, name, asset_type) \
        if hasattr(pctx.kitsu, "ensure_asset") else None
    if asset and create_folders and pctx.config.asset_folder_structure:
        ctx = pctx.ctx(asset=name, asset_type=asset_type)
        adir = pctx.paths.asset_dir(ctx)
        if not Path(adir).exists():
            layout.create_tree(adir, pctx.config.asset_folder_structure)
    return asset


def build_task_grid(pctx, shots, task_types) -> list:
    """A task per (shot, task_type). Idempotent -- ensure_tasks won't duplicate."""
    out = []
    for shot in shots:
        out.extend(pctx.kitsu.ensure_tasks(shot, list(task_types)))
    return out
