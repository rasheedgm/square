"""
FileHasher -- content hashing with a hash-once cache.

One ingest run hashes the same file up to three times: the pre-flight
duplicate check, the post-copy verification, and the ledger record. This
computes it once and serves the rest from a cache keyed by
(path, size, mtime_ns) -- so an edited/replaced file at the same path is
still re-hashed, but an unchanged one never is.

Algorithm: xxh3_64 (xxHash v3, ~GB/s). If the xxhash extension isn't
importable the hasher refuses to guess a substitute -- a ledger full of
xxh3 digests can't be compared against blake2b ones, and silently mixing
them would make "already ingested?" wrong. Callers that must degrade can
pass algo="blake2b" explicitly.

Thread-safe: the cache is guarded by a lock, held only around the dict
get/set, never during the file read, so many files hash in parallel.
"""

from __future__ import annotations

import os
import hashlib
import threading

try:
    import xxhash
    _HAS_XXHASH = True
except Exception:   # pragma: no cover - environment-dependent
    _HAS_XXHASH = False

_CHUNK = 1 << 20   # 1 MiB

DEFAULT_ALGO = "xxh3_64"


def _new_hasher(algo: str):
    if algo == "xxh3_64":
        if not _HAS_XXHASH:
            raise RuntimeError(
                "xxh3_64 requested but the 'xxhash' extension is not available. "
                "Install it (pip install xxhash) or pass algo='blake2b'."
            )
        return xxhash.xxh3_64()
    if algo == "blake2b":
        return hashlib.blake2b(digest_size=16)
    if algo == "md5":
        return hashlib.md5()
    raise ValueError(f"Unknown hash algo: {algo!r}")


def _signature(path: str):
    st = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)


class FileHasher:
    def __init__(self, algo: str = DEFAULT_ALGO):
        # Validate the algo up front so construction fails loudly, not the
        # first hash call deep in a worker thread.
        _new_hasher(algo)
        self.algo = algo
        self._cache: dict[tuple, str] = {}
        self._lock = threading.Lock()

    def new_raw(self):
        """A fresh, empty hasher object for this instance's algo -- for callers
        that stream bytes themselves (e.g. hash-while-copying) and just want a
        digest that will compare equal to hash_file()."""
        return _new_hasher(self.algo)

    def hash_file(self, path: str) -> str:
        """Hex digest of the file's full content, from cache when unchanged."""
        sig = _signature(path)
        with self._lock:
            hit = self._cache.get(sig)
        if hit is not None:
            return hit

        hasher = _new_hasher(self.algo)
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                hasher.update(chunk)
        digest = hasher.hexdigest()

        with self._lock:
            self._cache[sig] = digest
        return digest

    def hash_files(self, paths) -> dict[str, str]:
        """{path: digest} for each path, sequentially (caller parallelizes if wanted)."""
        return {p: self.hash_file(p) for p in paths}

    def prime(self, path: str, digest: str) -> None:
        """
        Seed the cache with a digest computed elsewhere (e.g. read back from
        the ledger for a file we already know) -- only honoured if the file
        still has the same size/mtime it would need for a fresh hash to match.
        """
        try:
            sig = _signature(path)
        except OSError:
            return
        with self._lock:
            self._cache.setdefault(sig, digest)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def cache_size(self) -> int:
        with self._lock:
            return len(self._cache)
