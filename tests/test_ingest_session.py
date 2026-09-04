import os
import time
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.ingest_tool.core.item import Status, Action, IssueKind
from tools.ingest_tool.core.session import (
    IngestSession, SessionAutosaver, SESSION_SUFFIX,
    remember_session, recent_sessions, last_session,
)

# reuse the controller test's fakes/helpers
from tests.test_ingest_controller import _pctx, _controller, _make_item, _load


class SessionTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "deliver"
        self.src.mkdir()
        self.pctx = _pctx(str(self.tmp / "nas"))
        self.ctrl = _controller(self.pctx, str(self.tmp))

    def _mkitem(self, name, **kw):
        return _make_item(str(self.src), name=name, **kw)


class TestRoundTrip(SessionTestBase):
    def test_capture_and_reload_preserves_items_and_state(self):
        c = self.ctrl
        a = self._mkitem("a")
        b = self._mkitem("b", media_name="a")     # collides with a
        _load(c, [a, b])
        c.run_preflight()
        iss = next(i for i in a.issues if i.kind == IssueKind.DEST_COLLISION)
        c.resolve(a.key, iss.id, Action.SKIP)

        sess = IngestSession.capture(
            c, delivery_root=str(self.src),
            path_patterns=[{"template": "<sequence>/<shot>/####.exr"}],
            manual_media_types={r"/deliv/a.1001.exr": "BG Plate"},
            active_preset="VFX Standard",
        )
        path = sess.save(self.tmp / "showX")
        self.assertTrue(path.endswith(SESSION_SUFFIX))

        loaded = IngestSession.load(path)
        self.assertEqual(loaded.project_code, "ABC")
        self.assertEqual(loaded.delivery_root, str(self.src))
        self.assertEqual(len(loaded.path_patterns), 1)
        self.assertEqual(loaded.manual_media_types, {r"/deliv/a.1001.exr": "BG Plate"})
        self.assertEqual(loaded.active_preset, "VFX Standard")

        # resume reads the LIVE ProjectConfig via the same pctx -- no
        # config_snapshot to rebuild a controller from
        c2 = _controller(self.pctx, str(self.tmp))
        loaded.restore_into(c2)
        self.assertEqual(len(c2.items), 2)
        ra = c2.get(a.key)
        rb = c2.get(b.key)
        self.assertEqual(ra.status, Status.SKIPPED)
        self.assertEqual(rb.status, Status.NEW)     # collision cleared by a's skip
        self.assertEqual(c2.batch_id, c.batch_id)

    def test_completed_items_come_back_locked(self):
        c = self.ctrl
        _load(c, [self._mkitem("a")])
        c.run_preflight()
        c.run_ingest()
        self.assertEqual(c.items[0].status, Status.COMPLETED)

        sess = IngestSession.capture(c)
        path = sess.save(self.tmp / "s")

        c2 = _controller(self.pctx, str(self.tmp))
        IngestSession.load(path).restore_into(c2)
        self.assertEqual(c2.items[0].status, Status.COMPLETED)
        # not offered for ingest again
        self.assertEqual(c2.ingestable_items(), [])

    def test_resume_does_not_rehash(self):
        c = self.ctrl
        _load(c, [self._mkitem("a")])
        c.run_preflight()
        sess = IngestSession.capture(c)
        path = sess.save(self.tmp / "s")

        c2 = _controller(self.pctx, str(self.tmp))
        IngestSession.load(path).restore_into(c2)
        self.assertIn(c2.items[0].key, c2._scanned)   # marked done -> preflight won't re-hash

    def test_atomic_save_leaves_no_tmp_files(self):
        c = self.ctrl
        _load(c, [self._mkitem("a")])
        IngestSession.capture(c).save(self.tmp / "s")
        leftovers = [p for p in self.tmp.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])

    def test_rejects_mismatched_schema(self):
        # No migration path (decisions.md "No migration before v1.0"): any
        # version other than current is a hard rejection, not a transform.
        p = self.tmp / "future.sqingest.json"
        p.write_text(json.dumps({"schema_version": 999, "items": []}))
        with self.assertRaises(ValueError):
            IngestSession.load(p)
        p2 = self.tmp / "old.sqingest.json"
        p2.write_text(json.dumps({"schema_version": 1, "items": []}))
        with self.assertRaises(ValueError):
            IngestSession.load(p2)

    def test_suffix_normalization(self):
        c = self.ctrl
        s = IngestSession.capture(c)
        self.assertTrue(s.save(self.tmp / "a").endswith(".sqingest.json"))
        self.assertTrue(s.save(self.tmp / "b.json").endswith(".sqingest.json"))
        self.assertFalse((self.tmp / "b.json").exists())


class TestAutosaver(unittest.TestCase):
    def test_coalesces_a_burst_into_one_write(self):
        calls = []
        saver = SessionAutosaver(lambda: calls.append(time.time()), delay=0.15)
        for _ in range(10):
            saver.mark_dirty()
            time.sleep(0.01)
        time.sleep(0.3)
        self.assertEqual(len(calls), 1)
        saver.stop()

    def test_flush_writes_immediately_when_dirty(self):
        calls = []
        saver = SessionAutosaver(lambda: calls.append(1), delay=5.0)
        saver.mark_dirty()
        self.assertTrue(saver.flush())
        self.assertEqual(len(calls), 1)
        saver.stop()

    def test_flush_is_noop_when_clean(self):
        calls = []
        saver = SessionAutosaver(lambda: calls.append(1), delay=5.0)
        self.assertTrue(saver.flush())
        self.assertEqual(calls, [])
        saver.stop()

    def test_save_error_is_captured_not_raised(self):
        def boom():
            raise IOError("disk full")
        saver = SessionAutosaver(boom, delay=5.0)
        saver.mark_dirty()
        self.assertFalse(saver.flush())
        self.assertIn("disk full", saver.last_error)
        saver.stop()


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._old = os.environ.get("SQUARE_STATE_DIR")
        os.environ["SQUARE_STATE_DIR"] = str(self.tmp / "state")
        self.addCleanup(lambda: os.environ.__setitem__("SQUARE_STATE_DIR", self._old)
                        if self._old else os.environ.pop("SQUARE_STATE_DIR", None))

    def test_remember_and_recent_order(self):
        a = self.tmp / "a.sqingest.json"; a.write_text("{}")
        b = self.tmp / "b.sqingest.json"; b.write_text("{}")
        remember_session(str(a))
        remember_session(str(b))
        remember_session(str(a))       # bump a to front
        self.assertEqual([os.path.basename(p) for p in recent_sessions()][:2],
                         ["a.sqingest.json", "b.sqingest.json"])

    def test_last_session_skips_missing_files(self):
        gone = self.tmp / "gone.sqingest.json"
        here = self.tmp / "here.sqingest.json"; here.write_text("{}")
        remember_session(str(here))
        remember_session(str(gone))     # most recent, but doesn't exist
        self.assertEqual(last_session(), str(here))


if __name__ == "__main__":
    unittest.main()
