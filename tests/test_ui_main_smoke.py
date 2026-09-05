"""
Headless smoke test for the rebuilt MainWindow: it constructs, loads a
delivery through the folder-tree signal path, pre-flights, and ingests --
against a PipelineContext backed by the in-memory tracking Kitsu fake and a
real (tmp) NAS root. Guards the wiring, not the pixels.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtCore, QtWidgets

from square_core.context import PipelineContext
from square_core.config.pipeline import PipelineConfig
from square_core.services import projects as projects_service
from square_core.services.projects import ProjectSpec

from tools.ingest_tool.core.item import Status
from tests.test_ingest_controller import _TrackingKitsu


class _FakeExtractor:
    """Stands in for square_core.media.metadata.MetadataExtractor -- the
    smoke test's frame files are junk bytes, not real .exr data, so the
    real extractor would find nothing and leave every item Needs Info."""

    @staticmethod
    def probe(path):
        return ({"resolution": "1920x1080", "fps": 24.0, "colorspace": "ACEScg",
                 "width": 1920, "height": 1080}, "fake")


def _make_delivery(root: Path):
    d = root / "SQ010" / "SH0100"
    d.mkdir(parents=True)
    files = []
    for i in range(3):
        f = d / f"plate.{1001+i}.exr"
        f.write_bytes(b"exrdata" + bytes([i]))
        files.append(f)
    return files


def _build_ctx(nas_root: str, code="SHW") -> PipelineContext:
    cfg = PipelineConfig(nas_roots={"default": nas_root})
    api = _TrackingKitsu()
    ctx = PipelineContext(config=cfg, kitsu=api, user=api.current_user())
    projects_service.create(ctx, ProjectSpec(code=code, fps=24.0))
    return ctx


class MainWindowSmoke(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.delivery = self.tmp / "deliver"
        self.delivery.mkdir()
        _make_delivery(self.delivery)
        self.nas = self.tmp / "nas"

        # a fresh PipelineContext per window, and never pop the "resume last
        # session?" dialog (it's modal and would deadlock a headless run)
        self.p_conn = patch("tools.ingest_tool.ui_main.MainWindow._connect",
                             side_effect=lambda: _build_ctx(str(self.nas)))
        self.p_resume = patch("tools.ingest_tool.ui_main.MainWindow._offer_resume")
        self.p_conn.start(); self.p_resume.start()
        self.addCleanup(self.p_conn.stop); self.addCleanup(self.p_resume.stop)

        from tools.ingest_tool.ui_main import MainWindow
        self.win = MainWindow()
        self.addCleanup(self.win.close)
        self.win.pctx = self.win.ctx.project("SHW")
        self.win._rebuild_controller()
        self.win.controller.extractor = _FakeExtractor()

    def _wait_job(self, timeout=5000):
        loop = QtCore.QEventLoop()
        self.win.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(timeout, loop.quit)
        loop.exec()

    def test_window_builds_offline(self):
        self.assertFalse(self.win.is_kitsu_live)
        self.assertIn("Offline", self.win.user_lbl.text())

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

        # fresh window, resume -- same PipelineContext backing (same nas/kitsu)
        # so the resumed session's project code can reconnect
        with patch("tools.ingest_tool.ui_main.MainWindow._connect",
                   side_effect=lambda: _build_ctx(str(self.nas))), \
             patch("tools.ingest_tool.ui_main.MainWindow._offer_resume"):
            from tools.ingest_tool.ui_main import MainWindow
            win2 = MainWindow()
        self.addCleanup(win2.close)
        win2._resume(sess_path)
        self._wait_job_on(win2)

        self.assertEqual(win2.folder_tree.root_path, str(self.delivery))
        self.assertGreater(win2.folder_tree._tree.topLevelItemCount(), 0)
        self.assertEqual(win2.table._table.rowCount(), n_rows)

    def _wait_job_on(self, w):
        loop = QtCore.QEventLoop()
        w.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(5000, loop.quit)
        loop.exec()


if __name__ == "__main__":
    unittest.main()
