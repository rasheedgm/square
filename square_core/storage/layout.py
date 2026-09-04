"""Create folder trees on disk -- the part Zou never does.

`create_tree(root, subdirs)` makes `root` and each `root/<subdir>`.
`ensure_dirs(paths)` makes each path (and parents). Both are idempotent and
return the list of directories that were actually created (not the ones that
already existed) so `projects.create` can report them.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("square.storage")


def ensure_dirs(paths) -> list:
    created = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(str(p))
    return created


def create_tree(root, subdirs) -> list:
    root = Path(root)
    targets = [root] + [root / s.strip("/\\") for s in (subdirs or [])]
    created = ensure_dirs(targets)
    logger.info("created %d dir(s) under %s", len(created), root)
    return created
