"""
Headless smoke test for the rebuilt MainWindow: it constructs, loads a
delivery through the folder-tree signal path, pre-flights, and ingests --
with Kitsu and the NAS faked out. Guards the wiring, not the pixels.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtCore, QtWidgets

from square_core.ingest_item import Status
from tests.test_ingest_controller import FakeNAS, FakeRecorder, FakeProxyGen, FakeExtractor


def _make_delivery(root: Path):
    d = root / "SQ010" / "SH0100"
    d.mkdir(parents=True)
    files = []
    for i in range(3):
        f = d / f"plate.{1001+i}.exr"
        f.write_bytes(b"exrdata" + bytes([i]))
        files.append(f)
    return files


class MainWindowSmoke(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.delivery = self.tmp / "deliver"
        self.delivery.mkdir()
        _make_delivery(self.delivery)

        # no Kitsu, and never pop the "resume last session?" dialog (it's modal
        # and would deadlock a headless run -- the real MainWindow keeps it)
        self.p_conn = patch("tools.ingest_tool.ui_main.MainWindow._connect_kitsu", return_value=False)
        self.p_resume = patch("tools.ingest_tool.ui_main.MainWindow._offer_resume")
        self.p_conn.start(); self.p_resume.start()
        self.addCleanup(self.p_conn.stop); self.addCleanup(self.p_resume.stop)

        from tools.ingest_tool.ui_main import MainWindow
        self.win = MainWindow()
        self.addCleanup(self.win.close)
        # keep everything inside tmp -- no writes to the real configured NAS
        self.win.config.nas_root = str(self.tmp / "nas")
        self.win.project_data = {"id": "p1", "name": "Show", "code": "SHW"}
        self.win._rebuild_controller()
        self._swap_fakes()

    def _swap_fakes(self):
        c = self.win.controller
        c.nas = FakeNAS(self.tmp / "nas")
        c.recorder = FakeRecorder()
        c.proxy_generator = FakeProxyGen()
        c.extractor = FakeExtractor()

    def _wait_job(self, timeout=5000):
        loop = QtCore.QEventLoop()
        self.win.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(timeout, loop.quit)
        loop.exec()

    def test_window_builds_offline(self):
        self.assertFalse(self.win.is_kitsu_live)
        self.assertIn("Offline", self.win.kitsu_lbl.text())

    def test_load_then_preflight_populates_table(self):
        self.win._on_load_requested(str(self.delivery), None, None, False)
        self._wait_job()
        self.assertEqual(self.win.table._table.rowCount(), 1)
        it = self.win.controller.items[0]
        self.assertTrue(it.preflight_done)

    def test_ingest_all_runs_through(self):
        self.win._on_load_requested(str(self.delivery), None, None, False)
        self._wait_job()
        it = self.win.controller.items[0]
        # give it the fields a bare scan won't have
        self.win.controller.set_field(it.key, "sequence_code", "SQ010")
        self.win.controller.set_field(it.key, "shot_code", "SH0100")
        self.win.controller.set_field(it.key, "media_type", "Plate")
        self.win.controller.set_field(it.key, "media_name", "plate")

        with patch("tools.ingest_tool.ui_main.TaskSelectionDialog") as TD, \
             patch("tools.ingest_tool.ui_main.DryRunResultsDialog"):
            inst = TD.return_value
            inst.exec.return_value = __import__("tools.qt_compat", fromlist=["DIALOG_ACCEPTED"]).DIALOG_ACCEPTED
            inst.get_selected_tasks.return_value = ["Ingest"]
            self.win._start_ingest()
            self._wait_job()

        self.assertEqual(self.win.controller.items[0].status, Status.COMPLETED)

    def test_summary_label_updates(self):
        self.win._on_load_requested(str(self.delivery), None, None, False)
        self._wait_job()
        self.assertIn("row", self.win.summary_lbl.text())

    def test_save_then_resume_restores_tree_and_rows(self):
        self.win._on_load_requested(str(self.delivery), None, None, False)
        self._wait_job()
        n_rows = self.win.table._table.rowCount()

        sess_path = str(self.tmp / "s.sqingest.json")
        self.win.session_path = sess_path
        self.win._write_session()
        self.assertTrue(Path(sess_path).exists())

        # fresh window, resume
        from tools.ingest_tool.ui_main import MainWindow
        win2 = MainWindow()
        win2.config.nas_root = str(self.tmp / "nas")
        self.addCleanup(win2.close)
        win2._resume(sess_path)
        self._swap_on(win2)
        self._wait_job_on(win2)

        self.assertEqual(win2.folder_tree.root_path, str(self.delivery))
        self.assertGreater(win2.folder_tree._tree.topLevelItemCount(), 0)
        self.assertEqual(win2.table._table.rowCount(), n_rows)

    def _swap_on(self, w):
        w.controller.nas = FakeNAS(self.tmp / "nas")
        w.controller.recorder = FakeRecorder()
        w.controller.proxy_generator = FakeProxyGen()
        w.controller.extractor = FakeExtractor()

    def _wait_job_on(self, w):
        loop = QtCore.QEventLoop()
        w.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(5000, loop.quit)
        loop.exec()


if __name__ == "__main__":
    unittest.main()
