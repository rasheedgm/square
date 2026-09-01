import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from square_core.ingest_ledger import (
    IngestLedger, LedgerRecord, LEDGER_DIRNAME, LEDGER_FILENAME,
)


def _rec(file_hash, dest_path, **kw):
    base = dict(
        file_hash=file_hash, hash_algo="xxh3_64", size=100,
        src_path=f"/deliver/{file_hash}.exr", dest_path=dest_path,
        batch_id="batch-1", ingested_at="2026-09-01T10:00:00Z",
        seq="SQ010", shot="SH0100", media_type="Plate", media_name="main", version=1,
    )
    base.update(kw)
    return LedgerRecord(**base)


class TestLedgerBasics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = IngestLedger(self.tmp / "ledger.db")

    def test_for_project_path_layout(self):
        led = IngestLedger.for_project(self.tmp, "SHOWX")
        self.assertEqual(led.db_path, self.tmp / "SHOWX" / LEDGER_DIRNAME / LEDGER_FILENAME)
        self.assertTrue(led.db_path.exists())

    def test_record_and_count(self):
        n = self.ledger.record([_rec("h1", "/nas/a/f.1001.exr"), _rec("h2", "/nas/a/f.1002.exr")])
        self.assertEqual(n, 2)
        self.assertEqual(self.ledger.count(), 2)

    def test_record_is_idempotent_on_hash_plus_dest(self):
        self.ledger.record([_rec("h1", "/nas/a/f.1001.exr", version=1)])
        self.ledger.record([_rec("h1", "/nas/a/f.1001.exr", version=1, ingested_at="2026-09-02T00:00:00Z")])
        self.assertEqual(self.ledger.count(), 1)
        rows = self.ledger.lookup_hashes(["h1"])["h1"]
        self.assertEqual(rows[0].ingested_at, "2026-09-02T00:00:00Z")   # replaced

    def test_same_hash_different_dest_is_two_rows(self):
        self.ledger.record([_rec("h1", "/nas/a/f.exr"), _rec("h1", "/nas/b/f.exr")])
        self.assertEqual(len(self.ledger.lookup_hashes(["h1"])["h1"]), 2)

    def test_lookup_only_returns_known_hashes(self):
        self.ledger.record([_rec("h1", "/nas/a/f.exr")])
        got = self.ledger.lookup_hashes(["h1", "h2", ""])
        self.assertEqual(set(got), {"h1"})

    def test_lookup_large_hash_set_chunks_cleanly(self):
        recs = [_rec(f"h{i}", f"/nas/a/f{i}.exr") for i in range(1000)]
        self.ledger.record(recs)
        got = self.ledger.lookup_hashes([f"h{i}" for i in range(1000)])
        self.assertEqual(len(got), 1000)


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = IngestLedger(self.tmp / "ledger.db")

    def test_none_when_nothing_matches(self):
        m = self.ledger.classify(["x1", "x2", "x3"])
        self.assertEqual(m.kind, "none")
        self.assertEqual(m.matched_count, 0)
        self.assertEqual(m.total_count, 3)

    def test_full_when_every_file_matches(self):
        self.ledger.record([
            _rec("a", "/nas/v1/f.1001.exr", version=2),
            _rec("b", "/nas/v1/f.1002.exr", version=2),
        ])
        m = self.ledger.classify(["a", "b"])
        self.assertEqual(m.kind, "full")
        self.assertEqual(m.matched_count, 2)
        self.assertEqual(m.latest.version, 2)
        self.assertEqual(m.destinations, ["/nas/v1"])

    def test_partial_when_some_files_match(self):
        self.ledger.record([_rec("a", "/nas/v1/f.1001.exr")])
        m = self.ledger.classify(["a", "b", "c"])
        self.assertEqual(m.kind, "partial")
        self.assertEqual(m.matched_count, 1)
        self.assertEqual(m.total_count, 3)

    def test_empty_item_is_none(self):
        self.assertEqual(self.ledger.classify([]).kind, "none")

    def test_latest_picks_most_recent_by_timestamp(self):
        self.ledger.record([
            _rec("a", "/nas/v1/f.exr", version=1, ingested_at="2026-01-01T00:00:00Z"),
            _rec("a", "/nas/v3/f.exr", version=3, ingested_at="2026-06-01T00:00:00Z"),
        ])
        m = self.ledger.classify(["a"])
        self.assertEqual(m.kind, "full")
        self.assertEqual(m.latest.version, 3)

    def test_duplicate_hashes_in_item_are_deduped_for_kind(self):
        self.ledger.record([_rec("a", "/nas/v1/f.exr")])
        m = self.ledger.classify(["a", "a", "a"])
        self.assertEqual(m.kind, "full")
        self.assertEqual(m.total_count, 3)


class TestShotQuery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = IngestLedger(self.tmp / "ledger.db")

    def test_all_for_shot_filters_and_orders(self):
        self.ledger.record([
            _rec("a", "/nas/v2/f.exr", shot="SH0100", media_name="main", version=2,
                 ingested_at="2026-02-01T00:00:00Z"),
            _rec("b", "/nas/v1/f.exr", shot="SH0100", media_name="main", version=1,
                 ingested_at="2026-01-01T00:00:00Z"),
            _rec("c", "/nas/v1/f.exr", shot="SH0200", media_name="main", version=1),
        ])
        rows = self.ledger.all_for_shot("SH0100", media_name="main")
        self.assertEqual([r.version for r in rows], [1, 2])   # ordered by ingested_at


class TestConcurrentWriters(unittest.TestCase):
    def test_many_threads_recording_at_once(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = IngestLedger(tmp / "ledger.db")

        errs = []

        def work(n):
            try:
                ledger.record([_rec(f"h{n}", f"/nas/a/f{n}.exr")])
            except Exception as e:  # pragma: no cover
                errs.append(e)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errs, [])
        self.assertEqual(ledger.count(), 20)


if __name__ == "__main__":
    unittest.main()
