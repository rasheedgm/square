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


class MainWindowIngestBugsTest(unittest.TestCase):
    """Round-2 real-UI bugs: what Load brings in, and what happens to the
    outgoing controller when a new one replaces it."""

    # the same window fixture as the smoke tests above, without re-running them
    setUp = MainWindowSmoke.setUp
    _wait_job = MainWindowSmoke._wait_job

    def _mapper_with_one_tagged_shot(self):
        from tools.ingest_tool.core.folder_mapper import FolderMapper
        from square_core.paths.path_pattern import PathPattern
        d = self.delivery / "SQ010" / "SH0200"
        d.mkdir(parents=True)
        for i in range(3):
            (d / f"other.{1001 + i}.exr").write_bytes(b"x")
        mapper = FolderMapper(self.delivery)
        mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))
        return mapper

    def test_a_bare_load_only_brings_in_what_is_tagged(self):
        mapper = self._mapper_with_one_tagged_shot()
        self.win._on_load_requested(str(self.delivery), mapper, None, False)
        self._wait_job()
        self.assertEqual([i.shot_code for i in self.win.controller.items], ["SH0100"])

    def test_picking_rows_loads_exactly_those_even_if_untagged(self):
        import os
        mapper = self._mapper_with_one_tagged_shot()
        other = next(i for i in mapper.build_items() if "other" in i.files[0])
        picked = {os.path.normcase(os.path.abspath(f)) for f in other.files}
        self.win._on_load_requested(str(self.delivery), mapper, picked, False)
        self._wait_job()
        self.assertEqual(len(self.win.controller.items), 1)
        self.assertIn("other", self.win.controller.items[0].source_files[0])

    def test_nothing_tagged_and_nothing_picked_says_so_instead_of_loading_everything(self):
        from tools.ingest_tool.core.folder_mapper import FolderMapper
        from square_core.paths.path_pattern import PathPattern
        mapper = FolderMapper(self.delivery)
        mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/nomatch.####.exr"))
        with patch("tools.ingest_tool.ui_main.QtWidgets.QMessageBox.information") as info:
            self.win._on_load_requested(str(self.delivery), mapper, None, False)
        info.assert_called_once()
        self.assertEqual(self.win.controller.items, [])

    def test_replacing_the_controller_stops_the_old_one(self):
        old_controller, old_bridge = self.win.controller, self.win.bridge
        self.win._rebuild_controller()
        self.assertIsNot(self.win.controller, old_controller)
        self.assertTrue(old_controller._cancel.is_set())      # its work is told to stop
        self.assertTrue(old_bridge._closed)
        self.assertIs(self.win.table.bridge, self.win.bridge)

    def test_a_warning_event_reaches_the_user(self):
        from tools.ingest_tool.core.controller import ControllerEvent
        with patch("tools.ingest_tool.ui_main.QtWidgets.QMessageBox.warning") as warn:
            self.win._on_controller_event(ControllerEvent(
                kind="warning", payload={"message": "Task type Comp is not on the project"}))
        warn.assert_called_once()
        self.assertIn("Task type Comp", warn.call_args[0][2])


if __name__ == "__main__":
    unittest.main()
