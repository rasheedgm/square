import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtCore, QtWidgets

from square_core.ingest_controller import IngestController, ControllerConfig
from square_core.ingest_ledger import IngestLedger
from square_core.ingest_item import IngestItem, Status, IssueKind
from tools.ingest_tool.controller_bridge import ControllerBridge
from tools.ingest_tool.widgets.detail_panel import DetailPanel
from tests.test_ingest_controller import FakeNAS, FakeRecorder, FakeProxyGen, FakeExtractor, PROJECT


class DetailPanelTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        cfg = ControllerConfig(
            nas_root=str(self.tmp / "nas"), project_code="SHW",
            filename_template="{shot}_{name}_v{version:03d}.{frame}{ext}",
            preview_media_types=["Plate"], media_type_configs={"Plate": "p"}, task_types=["Ingest"],
        )
        self.ctrl = IngestController(
            cfg, PROJECT, nas=FakeNAS(self.tmp / "nas"), ledger=IngestLedger(self.tmp / "l.db"),
            recorder=FakeRecorder(), proxy_generator=FakeProxyGen(),
            extractor=FakeExtractor(found={"resolution": "1920x1080", "width": 1920, "height": 1080}),
        )
        self.bridge = ControllerBridge(self.ctrl)
        self.panel = DetailPanel(self.bridge)

    def _item(self, name, **kw):
        files = [str(self.src / f"{name}.{1001+i}.exr") for i in range(2)]
        for i, f in enumerate(files):
            Path(f).write_bytes(b"x" + name.encode() + bytes([i]))
        base = dict(sequence_code="SQ010", shot_code="SH0100", media_type="Plate",
                    media_name=name, version=1, start_frame=1001, end_frame=1002, frame_count=2)
        base.update(kw)
        return IngestItem(key=IngestItem.compute_key(files), source_files=files, ext=".exr",
                          source_name=name, **base)

    def _wait(self):
        loop = QtCore.QEventLoop()
        self.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(4000, loop.quit)
        loop.exec()

    def _buttons(self):
        return [b.text() for b in self.panel.findChildren(QtWidgets.QPushButton)]

    def test_placeholder_when_nothing_selected(self):
        self.panel.set_selection([])
        self.assertTrue(self.panel.findChildren(QtWidgets.QLabel))

    def test_single_needs_info_offers_metadata_setters(self):
        [it] = self.bridge.load([self._item("a")])
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([it.key])
        edits = self.panel.findChildren(QtWidgets.QLineEdit)
        self.assertTrue(any(e.placeholderText() == "colorspace" for e in edits))

    def test_conflict_issue_shows_action_buttons(self):
        a = self._item("a", media_name="main")
        b = self._item("b", media_name="main")   # collide
        self.bridge.load([a, b])
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([a.key])
        btns = self._buttons()
        self.assertTrue(any("Skip" in b for b in btns))
        self.assertTrue(any("Version Up" in b for b in btns))

    def test_action_button_resolves_through_bridge(self):
        a = self._item("a", media_name="main")
        b = self._item("b", media_name="main")
        self.bridge.load([a, b])
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([a.key])
        for btn in self.panel.findChildren(QtWidgets.QPushButton):
            if btn.text() == "Skip":
                btn.click()
                break
        self.assertEqual(self.ctrl.get(a.key).status, Status.SKIPPED)

    def test_batch_selection_header(self):
        a = self._item("a", shot_code="SH0100")
        b = self._item("b", shot_code="SH0200", media_name="bg")
        self.bridge.load([a, b])
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([a.key, b.key])
        labels = [l.text() for l in self.panel.findChildren(QtWidgets.QLabel)]
        self.assertTrue(any("2 rows selected" in x for x in labels))
        self.assertIn("Skip all", self._buttons())


if __name__ == "__main__":
    unittest.main()
