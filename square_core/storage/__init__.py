"""square_core.storage -- the filesystem layer.

- `transfer`: the verified copy engine (copy / hardlink / symlink,
  hash-on-write). Used by ingest, publish, localize, delivery.
- `layout`: create a project / shot folder tree.

Pure stdlib + `square_core.hashing`. No Kitsu, no Qt.
"""

from __future__ import annotations

from .transfer import (
    TransferResult,
    copy_file,
    transfer_file,
    transfer_sequence,
    VerificationError,
)
from .layout import create_tree, ensure_dirs

__all__ = [
    "TransferResult",
    "copy_file",
    "transfer_file",
    "transfer_sequence",
    "VerificationError",
    "create_tree",
    "ensure_dirs",
]
