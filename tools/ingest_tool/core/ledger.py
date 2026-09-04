"""
IngestLedger -- a per-project record of every file this tool has ever
copied to the NAS, keyed by content hash, so the review table can answer
"have these exact files gone in before?" even when they come back under a
different name, shot, or version.

Stored as SQLite at:

    {nas_root}/{project_code}/_pipeline/ingest_ledger.db

One row per ingested file. PRIMARY KEY (file_hash, dest_path) makes
re-recording the same content at the same place idempotent.

SMB-share friendly: every call opens its own short-lived connection (no
long-held handles, safe to call from worker threads) with a busy timeout
so a competing writer waits rather than erroring.
"""

from __future__ import annotations

import os
import sqlite3
import contextlib
from dataclasses import dataclass, field
from pathlib import Path

LEDGER_DIRNAME = "_pipeline"
LEDGER_FILENAME = "ingest_ledger.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingested_file (
    file_hash    TEXT NOT NULL,
    hash_algo    TEXT NOT NULL,
    size         INTEGER NOT NULL,
    src_path     TEXT NOT NULL,
    dest_path    TEXT NOT NULL,
    seq          TEXT,
    shot         TEXT,
    media_type   TEXT,
    media_name   TEXT,
    version      INTEGER,
    batch_id     TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    ingested_by  TEXT,
    PRIMARY KEY (file_hash, dest_path)
);
CREATE INDEX IF NOT EXISTS idx_ingested_file_hash ON ingested_file(file_hash);
CREATE INDEX IF NOT EXISTS idx_ingested_file_shot ON ingested_file(shot, media_name);
"""

_COLUMNS = (
    "file_hash", "hash_algo", "size", "src_path", "dest_path",
    "seq", "shot", "media_type", "media_name", "version",
    "batch_id", "ingested_at", "ingested_by",
)


@dataclass
class LedgerRecord:
    file_hash: str
    hash_algo: str
    size: int
    src_path: str
    dest_path: str
    batch_id: str
    ingested_at: str
    seq: str = ""
    shot: str = ""
    media_type: str = ""
    media_name: str = ""
    version: int = 1
    ingested_by: str = ""

    def as_row(self) -> tuple:
        return (
            self.file_hash, self.hash_algo, self.size, self.src_path, self.dest_path,
            self.seq, self.shot, self.media_type, self.media_name, self.version,
            self.batch_id, self.ingested_at, self.ingested_by,
        )

    @classmethod
    def from_row(cls, row) -> "LedgerRecord":
        d = {c: row[i] for i, c in enumerate(_COLUMNS)}
        return cls(**d)


@dataclass
class LedgerMatch:
    """
    Result of checking one item's files against the ledger.

    kind:
      "none"    -- no file of this item has been ingested before
      "partial" -- some but not all files match a prior ingest
      "full"    -- every file matches a prior ingest (identical content)
    """
    kind: str
    total_count: int
    matched_hashes: set[str] = field(default_factory=set)
    records: list[LedgerRecord] = field(default_factory=list)   # every prior row that matched

    @property
    def matched_count(self) -> int:
        return len(self.matched_hashes)

    @property
    def latest(self) -> "LedgerRecord | None":
        """The most recently ingested matching record (by ingested_at)."""
        if not self.records:
            return None
        return max(self.records, key=lambda r: r.ingested_at)

    @property
    def destinations(self) -> list[str]:
        seen, out = set(), []
        for r in self.records:
            d = os.path.dirname(r.dest_path)
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out


class NullLedger:
    """
    Stand-in when there's no project / NAS root yet -- the controller always
    has a ledger to call; this one just never matches anything and never
    records. Swap in a real IngestLedger once a project is chosen.
    """
    def record(self, records) -> int:
        return 0

    def lookup_hashes(self, hashes) -> dict:
        return {}

    def classify(self, file_hashes) -> LedgerMatch:
        return LedgerMatch(kind="none", total_count=len(list(file_hashes)))

    def all_for_shot(self, shot, media_name=None) -> list:
        return []

    def count(self) -> int:
        return 0


class IngestLedger:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self._ensure()

    @classmethod
    def for_project(cls, nas_root, project_code) -> "IngestLedger":
        p = Path(nas_root) / str(project_code) / LEDGER_DIRNAME / LEDGER_FILENAME
        return cls(p)

    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            conn.execute("PRAGMA busy_timeout = 30000;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(self, records) -> int:
        """
        Insert (or replace, on the same hash+dest) a batch of file records in
        one transaction. Returns the number of rows written.
        """
        rows = [r.as_row() for r in records]
        if not rows:
            return 0
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        sql = f"INSERT OR REPLACE INTO ingested_file ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
        with self._connect() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def lookup_hashes(self, hashes) -> dict[str, list[LedgerRecord]]:
        """{hash: [records]} for every given hash that appears in the ledger."""
        wanted = list({h for h in hashes if h})
        if not wanted:
            return {}
        out: dict[str, list[LedgerRecord]] = {}
        CHUNK = 400   # stay well under SQLite's variable limit
        with self._connect() as conn:
            for i in range(0, len(wanted), CHUNK):
                part = wanted[i:i + CHUNK]
                q = (
                    f"SELECT {', '.join(_COLUMNS)} FROM ingested_file "
                    f"WHERE file_hash IN ({', '.join(['?'] * len(part))})"
                )
                for row in conn.execute(q, part):
                    rec = LedgerRecord.from_row(row)
                    out.setdefault(rec.file_hash, []).append(rec)
        return out

    def classify(self, file_hashes) -> LedgerMatch:
        """
        Classify one item against the ledger.

        `file_hashes` is an iterable of this item's per-file content hashes
        (duplicates allowed; they're de-duplicated here).
        """
        hashes = [h for h in file_hashes if h]
        total = len(hashes)
        if total == 0:
            return LedgerMatch(kind="none", total_count=0)

        found = self.lookup_hashes(set(hashes))
        matched_hashes = {h for h in hashes if h in found}
        records: list[LedgerRecord] = []
        for h in matched_hashes:
            records.extend(found[h])

        if not matched_hashes:
            kind = "none"
        elif len(matched_hashes) == len(set(hashes)):
            kind = "full"
        else:
            kind = "partial"

        return LedgerMatch(
            kind=kind,
            total_count=total,
            matched_hashes=matched_hashes,
            records=records,
        )

    def all_for_shot(self, shot, media_name=None) -> list[LedgerRecord]:
        q = f"SELECT {', '.join(_COLUMNS)} FROM ingested_file WHERE shot = ?"
        params = [shot]
        if media_name is not None:
            q += " AND media_name = ?"
            params.append(media_name)
        q += " ORDER BY ingested_at"
        with self._connect() as conn:
            return [LedgerRecord.from_row(r) for r in conn.execute(q, params)]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM ingested_file").fetchone()[0]
