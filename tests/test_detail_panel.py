import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtCore, QtWidgets

from tools.ingest_tool.core.item import Status
from tools.ingest_tool.controller_bridge import ControllerBridge
from tools.ingest_tool.widgets.detail_panel import DetailPanel
from tests.test_ingest_controller import _pctx, _controller, _make_item, _load


class DetailPanelTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"; self.src.mkdir()
        self.work = self.tmp / "work"; self.work.mkdir()

        self.pctx = _pctx(str(self.work))
        self.ctrl = _controller(self.pctx, str(self.work))
        self.bridge = ControllerBridge(self.ctrl)
        self.panel = DetailPanel(self.bridge)

    def _item(self, name, **kw):
        return _make_item(str(self.src), name=name, **kw)

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
        it = self._item("a")
        it.metadata_verified = {}
        it.resolution = it.fps = it.colorspace = ""
        [added] = _load(self.ctrl, [it])
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([added.key])
        edits = self.panel.findChildren(QtWidgets.QLineEdit)
        self.assertTrue(any(e.placeholderText() == "colorspace" for e in edits))

    def test_conflict_issue_shows_action_buttons(self):
        a = self._item("a", media_name="main")
        b = self._item("b", media_name="main")   # collide (same slot)
        _load(self.ctrl, [a])
        self.bridge.preflight(); self._wait()
        self.ctrl.run_ingest()
        _load(self.ctrl, [b])
        for f in b.source_files:
            Path(f).write_bytes(b"different-bytes" * 5)
        b.hashes = {}
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([b.key])
        btns = self._buttons()
        self.assertTrue(any("Skip" in x for x in btns))
        self.assertTrue(any("Version Up" in x for x in btns))

    def test_action_button_resolves_through_bridge(self):
        a = self._item("a", media_name="main")
        b = self._item("b", media_name="main")
        _load(self.ctrl, [a])
        self.bridge.preflight(); self._wait()
        self.ctrl.run_ingest()
        _load(self.ctrl, [b])
        for f in b.source_files:
            Path(f).write_bytes(b"different-bytes" * 5)
        b.hashes = {}
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([b.key])
        for btn in self.panel.findChildren(QtWidgets.QPushButton):
            if btn.text() == "Skip":
                btn.click()
                break
        self.assertEqual(self.ctrl.get(b.key).status, Status.SKIPPED)

    def test_batch_selection_header(self):
        a = self._item("a", shot="SH0100")
        b = self._item("b", shot="SH0200", media_name="bg")
        _load(self.ctrl, [a, b])
        self.bridge.preflight(); self._wait()
        self.panel.set_selection([a.key, b.key])
        labels = [l.text() for l in self.panel.findChildren(QtWidgets.QLabel)]
        self.assertTrue(any("2 rows selected" in x for x in labels))
        self.assertIn("Skip all", self._buttons())


if __name__ == "__main__":
    unittest.main()
