import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtWidgets

from tools.ingest_tool.controller_bridge import ControllerBridge
from tools.ingest_tool.widgets.rename_dialog import RenameCellsDialog
from tests.test_ingest_controller import _pctx, _controller, _make_item, _load


class RenameCellsDialogTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        self.work = self.tmp / "work"; self.work.mkdir()

        self.pctx = _pctx(str(self.work))
        self.ctrl = _controller(self.pctx, str(self.work))
        self.bridge = ControllerBridge(self.ctrl)

    def _item(self, name, **kw):
        return _make_item(str(self.src), name=name, **kw)

    def test_scope_label_counts_rows_and_cells(self):
        a = self._item("a", shot="SH0100")
        b = self._item("b", shot="SH0200")
        _load(self.ctrl, [a, b])
        dlg = RenameCellsDialog(self.bridge, [(a.key, "media_name"), (b.key, "colorspace")])
        # two distinct rows AND two distinct fields -> both counts shown
        text = dlg.findChild(QtWidgets.QLabel).text()
        self.assertIn("2 cell(s)", text)
        self.assertIn("2 row(s)", text)
        self.assertIn("2 field(s)", text)

    def test_preview_shows_resolved_value_per_row(self):
        a = self._item("a", shot="SH0100")
        _load(self.ctrl, [a])
        dlg = RenameCellsDialog(self.bridge, [(a.key, "media_name")])
        dlg.edit.setText("{shot}_renamed")
        self.assertIn("SH0100_renamed", dlg.preview.text())

    def test_preview_shows_placeholder_when_template_is_blank(self):
        a = self._item("a")
        _load(self.ctrl, [a])
        dlg = RenameCellsDialog(self.bridge, [(a.key, "media_name")])
        self.assertFalse(dlg._ok.isEnabled())
        self.assertIn("type a template", dlg.preview.text())

    def test_accept_applies_the_rename_and_does_not_mutate_before_accept(self):
        a = self._item("a", shot="SH0100")
        _load(self.ctrl, [a])
        dlg = RenameCellsDialog(self.bridge, [(a.key, "media_name")])
        dlg.edit.setText("{shot}_renamed")
        self.assertEqual(self.ctrl.get(a.key).media_name, "a")   # not yet applied
        dlg._accept()
        self.assertEqual(self.ctrl.get(a.key).media_name, "SH0100_renamed")

    def test_token_button_inserts_the_token_at_cursor(self):
        a = self._item("a")
        _load(self.ctrl, [a])
        dlg = RenameCellsDialog(self.bridge, [(a.key, "media_name")])
        dlg.edit.setText("prefix_")
        dlg.edit.setCursorPosition(len("prefix_"))
        dlg._insert("shot")
        self.assertEqual(dlg.edit.text(), "prefix_{shot}")

    def test_preview_caps_at_the_limit_and_notes_the_remainder(self):
        items = [self._item(f"n{i}") for i in range(12)]
        _load(self.ctrl, items)
        cells = [(it.key, "media_name") for it in items]
        dlg = RenameCellsDialog(self.bridge, cells)
        dlg.edit.setText("renamed")
        self.assertIn("more", dlg.preview.text())


if __name__ == "__main__":
    unittest.main()
