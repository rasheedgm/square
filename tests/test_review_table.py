import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtCore, QtWidgets

from tools.ingest_tool.core.item import Status
from tools.ingest_tool.controller_bridge import ControllerBridge
from tools.ingest_tool.widgets.review_table import (
    IngestReviewTable, C_SHOT, C_VER, C_STATUS, C_PREV, C_CS, C_MEDIA,
)
from tests.test_ingest_controller import _pctx, _controller, _make_item, _load


class ReviewTableTest(unittest.TestCase):
    def setUp(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "d"; self.src.mkdir()
        self.work = self.tmp / "work"; self.work.mkdir()

        self.pctx = _pctx(str(self.work))
        self.ctrl = _controller(self.pctx, str(self.work))
        self.bridge = ControllerBridge(self.ctrl)
        self.table = IngestReviewTable(self.bridge)

    def _item(self, name, **kw):
        return _make_item(str(self.src), name=name, **kw)

    def _load(self, *items):
        return _load(self.ctrl, list(items))

    def _wait_job(self, timeout=5000):
        loop = QtCore.QEventLoop()
        self.bridge.job_finished.connect(lambda *a: loop.quit())
        QtCore.QTimer.singleShot(timeout, loop.quit)
        loop.exec()

    def _t(self):
        return self.table._table

    def test_rows_appear_on_load(self):
        self._load(self._item("a"), self._item("b"))
        self.assertEqual(self._t().rowCount(), 2)
        self.assertEqual(self._t().item(0, C_SHOT).text(), "SH0100")

    def test_status_updates_from_preflight_event(self):
        self._load(self._item("a"))
        self.bridge.preflight()
        self._wait_job()
        lbl = self._t().cellWidget(0, C_STATUS)
        self.assertEqual(lbl.text(), Status.NEW.value)

    def test_editing_shot_cell_calls_bridge(self):
        self._load(self._item("a"))
        self.bridge.preflight(); self._wait_job()
        self._t().item(0, C_SHOT).setText("SH0200")
        self.assertEqual(self.ctrl.items[0].shot_code, "SH0200")

    def test_editing_version_cell_parses_int(self):
        self._load(self._item("a"))
        self.bridge.preflight(); self._wait_job()
        self._t().item(0, C_VER).setText("4")
        self.assertEqual(self.ctrl.items[0].version, 4)

    def test_preview_checkbox_reflects_and_pushes(self):
        [it] = self.bridge.load([self._item("a")])          # Plate -> preview on by default
        chk = self._t().cellWidget(0, C_PREV).findChild(QtWidgets.QCheckBox)
        self.assertTrue(chk.isChecked())
        chk.setChecked(False)
        self.assertFalse(self.ctrl.get(it.key).preview_wanted)

    def test_unverified_metadata_cell_is_flagged(self):
        it = self._item("a")
        it.metadata_verified = {}
        it.resolution = it.fps = it.colorspace = ""
        self._load(it)
        self.bridge.preflight(); self._wait_job()
        cs_cell = self._t().item(0, C_CS)
        self.assertEqual(cs_cell.text(), "set…")

    def test_completed_row_is_not_editable(self):
        self._load(self._item("a"))
        self.bridge.preflight(); self._wait_job()
        self.bridge.ingest(); self._wait_job()
        flags = self._t().item(0, C_SHOT).flags()
        self.assertFalse(bool(flags & QtCore.Qt.ItemFlag.ItemIsEditable))

    def test_selection_signal(self):
        got = []
        self.table.selection_changed.connect(got.append)
        self._load(self._item("a"), self._item("b"))
        self._t().selectRow(1)
        self.assertTrue(got and got[-1])

    def test_selected_cells_reports_key_and_field_for_editable_columns_only(self):
        self._load(self._item("a"), self._item("b"))
        self._t().item(0, C_MEDIA).setSelected(True)
        self._t().item(1, C_CS).setSelected(True)
        self._t().cellWidget(0, C_PREV)   # a non-item (widget) cell -- never selectable as data
        cells = self.table.selected_cells()
        self.assertEqual(set(cells), {
            (self.ctrl.items[0].key, "media_name"),
            (self.ctrl.items[1].key, "colorspace"),
        })

    def test_selected_cells_ignores_non_editable_columns(self):
        self._load(self._item("a"))
        self._t().item(0, C_SHOT).setSelected(True)
        # C_STATUS/C_PREV/C_PROG hold widgets, not items -- nothing to select there
        cells = self.table.selected_cells()
        self.assertEqual(cells, [(self.ctrl.items[0].key, "shot_code")])

    def test_context_menu_offers_rename_only_when_editable_cells_are_selected(self):
        self._load(self._item("a"))
        self.assertEqual(self.table.selected_cells(), [])
        self._t().item(0, C_SHOT).setSelected(True)
        self.assertEqual(len(self.table.selected_cells()), 1)

    def test_rename_selected_opens_the_dialog_with_the_right_cells(self):
        self._load(self._item("a", shot="SH0100"), self._item("b", shot="SH0200"))
        self._t().item(0, C_SHOT).setSelected(True)
        self._t().item(1, C_SHOT).setSelected(True)
        cells = self.table.selected_cells()

        with patch("tools.ingest_tool.widgets.review_table.RenameCellsDialog") as Dlg:
            self.table._on_rename_selected(cells)
            Dlg.rename_selected.assert_called_once()
            args, kwargs = Dlg.rename_selected.call_args
            self.assertIs(args[0], self.bridge)
            self.assertEqual(list(args[1]), cells)

    def test_rename_dialog_apply_updates_the_table_cell_end_to_end(self):
        from tools.ingest_tool.widgets.rename_dialog import RenameCellsDialog
        self._load(self._item("a", shot="SH0100"))
        self._t().item(0, C_MEDIA).setSelected(True)
        cells = self.table.selected_cells()

        dlg = RenameCellsDialog(self.bridge, cells)
        dlg.edit.setText("{shot}_renamed")
        dlg._accept()

        self.assertEqual(self.ctrl.items[0].media_name, "SH0100_renamed")
        self.assertEqual(self._t().item(0, C_MEDIA).text(), "SH0100_renamed")


if __name__ == "__main__":
    unittest.main()
