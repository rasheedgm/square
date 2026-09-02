"""The verified copy engine.

- `copy_file`   -- byte copy, destination hashed FROM THE BYTES AS WRITTEN
                   (no re-read). Native CopyFileExW on Windows, stream loop
                   elsewhere.
- `transfer_file` -- one file per `mode`, cascading symlink -> hardlink -> copy;
                   a real copy is verified against the source hash.
- `transfer_sequence` -- a whole frame range through one shared thread pool.

Lifted from the ingest tool's `nas_manager` (live-verified) and made
Kitsu-free / class-free so publish, localize and delivery reuse it.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from square_core.hashing import FileHasher

logger = logging.getLogger("square.storage")

_CHUNK = 8 * 1024 * 1024
VALID_MODES = ("copy", "hardlink", "symlink")


class VerificationError(IOError):
    """A copied file's hash did not match the source."""


@dataclass
class TransferResult:
    src: str
    dest: str
    mode: str = "copy"            # actual mode used
    hash: str = ""               # destination content hash (copy only)
    verified: bool = False


# --------------------------------------------------------------------------
# Windows native copy
# --------------------------------------------------------------------------

def _win_copyfile(src: Path, dest: Path) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.CopyFileExW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        return bool(fn(str(src), str(dest), None, None, None, 0))
    except Exception as e:  # pragma: no cover - platform dependent
        logger.debug("CopyFileExW unavailable (%s); stream copy", e)
        return False


# --------------------------------------------------------------------------


def copy_file(src, dest, *, hasher: FileHasher | None = None) -> str:
    """Copy `src` -> `dest`, return the destination's content hash computed
    while writing. Creates parent dirs."""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = hasher.new_raw() if hasher is not None else FileHasher().new_raw()

    if sys.platform == "win32" and _win_copyfile(src, dest):
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                raw.update(chunk)
    else:
        with open(src, "rb") as fin, open(dest, "wb") as fout:
            for chunk in iter(lambda: fin.read(_CHUNK), b""):
                fout.write(chunk)
                raw.update(chunk)
    try:
        shutil.copystat(src, dest)
    except OSError:
        pass
    return raw.hexdigest()


def transfer_file(src, dest, *, mode: str = "copy", hasher: FileHasher | None = None,
                  expected_hash: str = "") -> TransferResult:
    """Transfer one file. symlink/hardlink fall back to copy on failure
    (cross-volume, no privilege). A real copy is hash-verified."""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    m = mode if mode in VALID_MODES else "copy"

    if m == "symlink":
        try:
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            os.symlink(src, dest)
            return TransferResult(str(src), str(dest), "symlink")
        except OSError as e:
            logger.warning("symlink failed for %s (%s); trying hardlink", dest.name, e)
            m = "hardlink"

    if m == "hardlink":
        try:
            if dest.exists():
                dest.unlink()
            os.link(src, dest)
            return TransferResult(str(src), str(dest), "hardlink")
        except OSError as e:
            logger.warning("hardlink failed for %s (%s); full copy", dest.name, e)

    h = hasher or FileHasher()
    dest_hash = copy_file(src, dest, hasher=h)
    expected = expected_hash or h.hash_file(str(src))
    if expected and dest_hash and expected != dest_hash:
        raise VerificationError(
            f"checksum mismatch for {dest.name}: source={expected} dest={dest_hash}"
        )
    return TransferResult(str(src), str(dest), "copy", hash=dest_hash, verified=bool(expected))


def transfer_sequence(pairs, *, mode: str = "copy", workers: int = 4,
                      hasher: FileHasher | None = None, source_hashes: dict | None = None,
                      pool: ThreadPoolExecutor | None = None, progress=None) -> list:
    """Transfer many (src, dest) pairs. Uses `pool` if given (so a whole batch
    of sequences shares one), else a local pool of `workers`. `source_hashes`
    maps a source path (as given) to its pre-flight hash, skipping a re-hash."""
    pairs = [(str(s), str(d)) for s, d in pairs]
    h = hasher or FileHasher()
    src_hashes = source_hashes or {}
    results: list = [None] * len(pairs)
    done = 0

    def one(i, s, d):
        return i, transfer_file(s, d, mode=mode, hasher=h, expected_hash=src_hashes.get(s, ""))

    own_pool = pool is None
    ex = pool or ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="sq-copy")
    try:
        futs = [ex.submit(one, i, s, d) for i, (s, d) in enumerate(pairs)]
        for fut in futs:
            i, res = fut.result()
            results[i] = res
            done += 1
            if progress:
                progress(done, len(pairs))
    finally:
        if own_pool:
            ex.shutdown(wait=True)
    return results
