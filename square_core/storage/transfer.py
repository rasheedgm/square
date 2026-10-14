"""The verified copy engine.

- `copy_file`   -- byte copy. On Windows the native CopyFileExW does the
                   transfer (so a same-server SMB copy never crosses the
                   client's network); elsewhere a stream loop. Optionally
                   hashes (the destination, FROM THE BYTES AS WRITTEN where it
                   can) and can be aborted part-way through a file.
- `transfer_file` -- one file per `mode`, cascading symlink -> hardlink -> copy.
                   A real copy is verified to the level asked for:
                   "size" (byte count -- catches a short/truncated copy, costs
                   nothing) or "full" (content hash against the source --
                   re-reads the destination, and the source unless a hash is
                   handed in).
- `transfer_sequence` -- a whole frame range through one shared thread pool.

Lifted from the ingest tool's `nas_manager` (live-verified) and made
Kitsu-free / class-free so publish, localize and delivery reuse it.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from square_core.hashing import FileHasher, HashCancelled

logger = logging.getLogger("square.storage")

_CHUNK = 8 * 1024 * 1024
VALID_MODES = ("copy", "hardlink", "symlink")
VERIFY_MODES = ("size", "full")

_ERROR_REQUEST_ABORTED = 1235          # CopyFileEx stopped by its progress routine


class VerificationError(IOError):
    """A copied file's hash did not match the source."""


class TransferCancelled(Exception):
    """The caller's `should_stop` said to give up -- a skipped row, a cancelled
    ingest. Whatever was half-written of the file in flight is removed."""


@dataclass
class TransferResult:
    src: str
    dest: str
    mode: str = "copy"            # actual mode used
    hash: str = ""               # destination content hash (copy + verify="full" only)
    verified: bool = False


# --------------------------------------------------------------------------
# Windows native copy
# --------------------------------------------------------------------------

def _win_copyfile(src: Path, dest: Path, should_stop=None) -> bool:
    """CopyFileExW. True if it copied; False if it is unavailable / failed
    (caller falls back to a stream copy). Raises TransferCancelled if
    `should_stop` aborted it -- Windows then removes the partial destination."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        routine_t = ctypes.WINFUNCTYPE(
            wintypes.DWORD,
            ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_longlong,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p)
        fn = kernel32.CopyFileExW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, routine_t,
                       ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), wintypes.DWORD]
        fn.restype = wintypes.BOOL

        routine = None
        if should_stop is not None:
            def _progress(_total, _done, _stream_total, _stream_done,
                          _stream_no, _reason, _hsrc, _hdst, _data):
                return 1 if should_stop() else 0       # PROGRESS_CANCEL : PROGRESS_CONTINUE
            routine = routine_t(_progress)

        ok = bool(fn(str(src), str(dest), routine, None, None, 0))
        if not ok and ctypes.get_last_error() == _ERROR_REQUEST_ABORTED:
            raise TransferCancelled(str(src))
        return ok
    except TransferCancelled:
        raise
    except Exception as e:  # pragma: no cover - platform dependent
        logger.debug("CopyFileExW unavailable (%s); stream copy", e)
        return False


def _remove_partial(dest: Path) -> None:
    try:
        dest.unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------


def copy_file(src, dest, *, hasher: FileHasher | None = None, should_stop=None,
              compute_hash: bool = True) -> str:
    """Copy `src` -> `dest`, return the destination's content hash (or "" when
    `compute_hash` is False -- no extra read of the file at all). Creates
    parent dirs. `should_stop` is polled during the copy; when it says stop the
    partial destination is removed and TransferCancelled is raised."""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = None
    if compute_hash:
        raw = hasher.new_raw() if hasher is not None else FileHasher().new_raw()

    if sys.platform == "win32" and _win_copyfile(src, dest, should_stop):
        if raw is not None:
            with open(dest, "rb") as fh:
                for chunk in iter(lambda: fh.read(_CHUNK), b""):
                    if should_stop is not None and should_stop():
                        raise TransferCancelled(str(src))
                    raw.update(chunk)
    else:
        try:
            with open(src, "rb") as fin, open(dest, "wb") as fout:
                for chunk in iter(lambda: fin.read(_CHUNK), b""):
                    if should_stop is not None and should_stop():
                        raise TransferCancelled(str(src))
                    fout.write(chunk)
                    if raw is not None:
                        raw.update(chunk)
        except TransferCancelled:
            _remove_partial(dest)
            raise
    try:
        shutil.copystat(src, dest)
    except OSError:
        pass
    return raw.hexdigest() if raw is not None else ""


def transfer_file(src, dest, *, mode: str = "copy", hasher: FileHasher | None = None,
                  expected_hash: str = "", verify: str = "full",
                  should_stop=None) -> TransferResult:
    """Transfer one file. symlink/hardlink fall back to copy on failure
    (cross-volume, no privilege). A real copy is verified per `verify`:
    "size" compares byte counts only; "full" hashes the destination (and the
    source, unless `expected_hash` is given) and compares."""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    m = mode if mode in VALID_MODES else "copy"
    if verify not in VERIFY_MODES:
        raise ValueError(f"unknown verify mode {verify!r}; expected one of {VERIFY_MODES}")

    if should_stop is not None and should_stop():
        raise TransferCancelled(str(src))

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

    if verify == "size":
        copy_file(src, dest, should_stop=should_stop, compute_hash=False)
        want, got = os.path.getsize(src), os.path.getsize(dest)
        if want != got:
            _remove_partial(dest)
            raise VerificationError(
                f"size mismatch for {dest.name}: source={want} bytes dest={got} bytes")
        return TransferResult(str(src), str(dest), "copy", hash="", verified=True)

    h = hasher or FileHasher()
    dest_hash = copy_file(src, dest, hasher=h, should_stop=should_stop)
    try:
        expected = expected_hash or h.hash_file(str(src), should_stop)
    except HashCancelled:
        _remove_partial(dest)
        raise TransferCancelled(str(src)) from None
    if expected and dest_hash and expected != dest_hash:
        raise VerificationError(
            f"checksum mismatch for {dest.name}: source={expected} dest={dest_hash}"
        )
    return TransferResult(str(src), str(dest), "copy", hash=dest_hash, verified=bool(expected))


def transfer_sequence(pairs, *, mode: str = "copy", workers: int = 4,
                      hasher: FileHasher | None = None, source_hashes: dict | None = None,
                      pool: ThreadPoolExecutor | None = None, progress=None,
                      verify: str = "full", should_stop=None) -> list:
    """Transfer many (src, dest) pairs. Uses `pool` if given (so a whole batch
    of sequences shares one), else a local pool of `workers`. `source_hashes`
    maps a source path (as given) to its pre-flight hash, skipping a re-hash.

    If anything fails or `should_stop` says stop, files not yet started are
    not started and the ones in flight are waited for before the error is
    raised -- the caller can then clean up knowing nothing is still writing."""
    pairs = [(str(s), str(d)) for s, d in pairs]
    h = hasher or FileHasher()
    src_hashes = source_hashes or {}
    results: list = [None] * len(pairs)
    done = 0

    def one(i, s, d):
        return i, transfer_file(s, d, mode=mode, hasher=h, expected_hash=src_hashes.get(s, ""),
                                verify=verify, should_stop=should_stop)

    own_pool = pool is None
    ex = pool or ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="sq-copy")
    futs = []
    try:
        futs = [ex.submit(one, i, s, d) for i, (s, d) in enumerate(pairs)]
        for fut in futs:
            i, res = fut.result()
            results[i] = res
            done += 1
            if progress:
                progress(done, len(pairs))
    except BaseException:
        for f in futs:
            f.cancel()
        wait([f for f in futs if not f.cancelled()])
        raise
    finally:
        if own_pool:
            ex.shutdown(wait=True)
    return results
