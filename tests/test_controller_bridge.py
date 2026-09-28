import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtCore, QtWidgets

from tools.ingest_tool.core.item import IngestItem, Status
from tools.ingest_tool.controller_bridge import ControllerBridge
from tests.test_ingest_controller import _pctx, _controller, _make_item, _load


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        self.work = self.tmp / "work"; self.work.mkdir()

        self.pctx = _pctx(str(self.work))
        self.ctrl = _controller(self.pctx, str(self.work))
        self.bridge = ControllerBridge(self.ctrl)

    def _item(self, name):
        return _make_item(str(self.src), name=name)

    def _wait_for_job(self, timeout=5000):
        done = []
        self.bridge.job_finished.connect(lambda *a: done.append(a))
        loop = QtCore.QEventLoop()
        self.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(timeout, loop.quit)
        loop.exec()
        return done[0] if done else None

    def test_events_are_delivered_on_the_main_thread_with_full_payload(self):
        # This is the regression guard: the payload survives the thread hop.
        got = []
        self.bridge.event.connect(lambda ev: got.append(ev))
        self.bridge.load([self._item("a"), self._item("b")])
        self.bridge.preflight()
        res = self._wait_for_job()
        self.assertIsNotNone(res)
        self.assertEqual(res[1], "")   # no error

        kinds = [e.kind for e in got]
        self.assertIn("preflight_finished", kinds)
        upd = [e for e in got if e.kind == "item_updated" and e.item is not None]
        self.assertTrue(upd)
        # the item objects came through intact, not as an empty dict
        self.assertTrue(all(isinstance(e.item, IngestItem) for e in upd))
        self.assertTrue(all(e.item.key for e in upd))

    def test_preflight_resolves_every_row(self):
        self.bridge.load([self._item("a"), self._item("b")])
        self.bridge.preflight()
        self._wait_for_job()
        for it in self.ctrl.items:
            self.assertNotEqual(it.status, Status.CHECKING)

    def test_a_second_ingest_is_rejected_while_a_job_runs(self):
        self.bridge.load([self._item("a")])
        self.assertTrue(self.bridge.preflight())
        self.assertFalse(self.bridge.ingest())      # while the check is still running
        self._wait_for_job()

    def test_ingest_through_bridge(self):
        _load(self.ctrl, [self._item("a")])
        self.bridge.preflight(); self._wait_for_job()
        self.bridge.ingest(); self._wait_for_job()
        self.assertEqual(self.ctrl.items[0].status, Status.COMPLETED)

    def test_sync_edit_passthrough(self):
        [it] = self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_for_job()
        self.bridge.set_field(it.key, "shot_code", "SH0200")
        self.assertEqual(self.ctrl.get(it.key).shot_code, "SH0200")
        self.assertTrue(self.bridge.can_undo)


class BridgeQueueTest(unittest.TestCase):
    """A check requested while another job runs used to be dropped on the
    floor (rows loaded in the meantime were never scanned and sat on
    Checking forever); it is queued now."""

    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        self.work = self.tmp / "work"; self.work.mkdir()
        self.pctx = _pctx(str(self.work))
        self.ctrl = _controller(self.pctx, str(self.work))
        self.bridge = ControllerBridge(self.ctrl)

    def _pump(self, cond, timeout=8000):
        loop = QtCore.QEventLoop()
        timer = QtCore.QTimer()
        timer.setInterval(20)
        timer.timeout.connect(lambda: loop.quit() if cond() else None)
        timer.start()
        QtCore.QTimer.singleShot(timeout, loop.quit)
        loop.exec()
        timer.stop()
        return cond()

    def test_a_check_requested_while_busy_is_queued_and_runs_afterwards(self):
        import time as _time

        class _Slow:
            @staticmethod
            def probe(path):
                if "first" in path:
                    _time.sleep(0.4)
                return ({"resolution": "1920x1080", "fps": 24.0, "colorspace": "ACEScg"}, "fake")

        self.ctrl.extractor = _Slow()
        first = _make_item(str(self.src), name="first")
        second = _make_item(str(self.src), name="second", shot="SH0200")
        [a] = self.bridge.load([first])
        self.assertTrue(self.bridge.preflight())
        self.assertTrue(self.bridge.busy)
        [b] = self.bridge.load([second])            # arrives while the first check runs
        self.assertTrue(self.bridge.preflight([b.key]))          # queued, not refused
        done = self._pump(lambda: all(i.preflight_done for i in self.ctrl.items)
                          and not self.bridge.busy)
        self.assertTrue(done)
        self.assertNotEqual(self.ctrl.get(b.key).status, Status.CHECKING)

    def test_cancel_drops_anything_queued(self):
        import time as _time

        class _Slow:
            @staticmethod
            def probe(path):
                _time.sleep(0.3)
                return ({}, "fake")

        self.ctrl.extractor = _Slow()
        self.bridge.load([_make_item(str(self.src), name="a")])
        self.bridge.preflight()
        self.bridge.preflight()                     # queued
        self.bridge.cancel()
        self._pump(lambda: not self.bridge.busy)
        self.assertFalse(self.bridge._queued_all)
        self.assertFalse(self.bridge._queued_keys)

    def test_including_a_row_that_never_finished_checking_runs_the_check(self):
        [it] = self.bridge.load([_make_item(str(self.src), name="a")])
        self.bridge.skip_many([it.key])
        self.bridge.preflight()
        self._pump(lambda: not self.bridge.busy)
        self.assertFalse(self.ctrl.get(it.key).preflight_done)      # skipped -> not scanned
        self.bridge.include_many([it.key])
        self._pump(lambda: self.ctrl.get(it.key).preflight_done)
        self.assertTrue(self.ctrl.get(it.key).preflight_done)

    def test_close_stops_a_running_check_quickly_and_silences_the_bridge(self):
        import time as _time

        class _Waits:
            def __init__(self, controller):
                self.controller = controller

            def probe(self, path):
                self.controller._cancel.wait(5)     # a long scan that notices cancel
                return ({}, "fake")

        self.ctrl.extractor = _Waits(self.ctrl)
        self.bridge.load([_make_item(str(self.src), name="a")])
        self.bridge.preflight()
        got = []
        self.bridge.event.connect(got.append)
        t0 = _time.time()
        self.bridge.close(wait_ms=6000)
        self.assertLess(_time.time() - t0, 3)
        self.assertFalse(self.bridge.busy)
        n = len(got)
        self.ctrl._emit("item_updated", item=self.ctrl.items[0])
        self.assertEqual(len(got), n)               # nothing gets through a closed bridge
        self.assertFalse(self.bridge.preflight())   # and it refuses new work


if __name__ == "__main__":
    unittest.main()
