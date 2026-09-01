import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtCore, QtWidgets

from square_core.ingest_controller import IngestController, ControllerConfig
from square_core.ingest_ledger import IngestLedger
from square_core.ingest_item import IngestItem, Status
from tools.ingest_tool.controller_bridge import ControllerBridge
from tests.test_ingest_controller import FakeNAS, FakeRecorder, FakeProxyGen, FakeExtractor, PROJECT


def _pump(ms=2000):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(ms, loop.quit)
    # also quit early once idle-ish
    QtWidgets.QApplication.processEvents()
    loop.exec_() if hasattr(loop, "exec_") else loop.exec()


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        self.nas = FakeNAS(self.tmp / "nas")
        self.cfg = ControllerConfig(
            nas_root=str(self.tmp / "nas"), project_code="SHW",
            filename_template="{shot}_{name}_v{version:03d}.{frame}{ext}",
            preview_media_types=["Plate"], media_type_configs={"Plate": "p"},
            task_types=["Ingest"],
        )
        self.ctrl = IngestController(
            self.cfg, PROJECT, nas=self.nas, ledger=IngestLedger(self.tmp / "l.db"),
            recorder=FakeRecorder(), proxy_generator=FakeProxyGen(), extractor=FakeExtractor(),
        )
        self.bridge = ControllerBridge(self.ctrl)

    def _item(self, name):
        files = []
        for i in range(2):
            p = self.src / f"{name}.{1001+i}.exr"; p.write_bytes(b"x" + name.encode() + bytes([i]))
            files.append(str(p))
        return IngestItem(key=IngestItem.compute_key(files), source_files=files, ext=".exr",
                          source_name=name, sequence_code="SQ010", shot_code="SH0100",
                          media_type="Plate", media_name=name, version=1,
                          start_frame=1001, end_frame=1002, frame_count=2)

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

    def test_busy_guard_rejects_a_second_job(self):
        self.bridge.load([self._item("a")])
        self.assertTrue(self.bridge.preflight())
        second = self.bridge.preflight()   # while first still running
        self.assertFalse(second)
        self._wait_for_job()

    def test_ingest_through_bridge(self):
        self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_for_job()
        self.bridge.ingest(); self._wait_for_job()
        self.assertEqual(self.ctrl.items[0].status, Status.COMPLETED)

    def test_sync_edit_passthrough(self):
        [it] = self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait_for_job()
        self.bridge.set_field(it.key, "shot_code", "SH0200")
        self.assertEqual(self.ctrl.get(it.key).shot_code, "SH0200")
        self.assertTrue(self.bridge.can_undo)


if __name__ == "__main__":
    unittest.main()
