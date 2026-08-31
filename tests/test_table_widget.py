import unittest
from Qt import QtWidgets

from tools.ingest_tool.widgets.table_widget import (
    IngestTableWidget, STATUS_CONFLICT, STATUS_NEW, STATUS_DISCARDED,
    COL_PROGRESS, COL_VERSION, STAGE_QUEUED, STAGE_COPYING, STAGE_DONE,
)
from square_core.plate_scanner import IngestSequenceItem


def _make_item(name, seq, shot, mtype, media_name, path):
    item = IngestSequenceItem(name, [path], ".mov", is_video=True)
    item.sequence_code = seq
    item.shot_code = shot
    item.media_type = mtype
    item.media_name = media_name
    return item


class TestTableWidgetConflicts(unittest.TestCase):
    """
    Regression coverage for a confirmed bug: has_unresolved_conflicts() checked
    `if id not in self.item_discarded` where `id` was Python's builtin function
    (not the row's key), so it never actually excluded discarded rows -- and
    the discard checkbox handler never re-ran conflict detection at all. Net
    effect: unchecking one side of a conflicting pair never cleared the
    "Unresolved conflicts" block on the Ingest button.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_discarding_one_side_resolves_conflict(self):
        item1 = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        item2 = _make_item("b", "SQ010", "SH0100", "Plate", "PL01", "/tmp/b.mov")

        self.table.populate_table([item1, item2])
        self.table._run_conflict_detection()
        self.table._refresh_table()

        self.assertTrue(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.item_status[id(item1)], STATUS_CONFLICT)
        self.assertEqual(self.table.item_status[id(item2)], STATUS_CONFLICT)

        # Discard item2 via the same code path the row checkbox uses.
        self.table._on_checkbox_changed(id(item2), 0)

        self.assertFalse(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.item_status[id(item1)], STATUS_NEW)
        self.assertEqual(self.table.item_status[id(item2)], STATUS_DISCARDED)

    def test_non_conflicting_discarded_row_never_blocks(self):
        item1 = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        self.table.populate_table([item1])
        self.table._run_conflict_detection()
        self.table._refresh_table()

        self.table._on_checkbox_changed(id(item1), 0)
        self.assertFalse(self.table.has_unresolved_conflicts())


class TestTableWidgetProgressAndBatchTools(unittest.TestCase):
    """Per-row live ingest progress + the Media Type / batch-version tools."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_progress_bar_updates_without_full_rebuild(self):
        item = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        self.table.populate_table([item])

        bar = self.table._table.cellWidget(0, COL_PROGRESS)
        self.assertEqual(bar.value(), 0)

        self.table.update_ingest_progress(item, STAGE_COPYING, 50)
        bar = self.table._table.cellWidget(0, COL_PROGRESS)
        self.assertEqual(bar.value(), 50)

        self.table.update_ingest_progress(item, STAGE_DONE)
        bar = self.table._table.cellWidget(0, COL_PROGRESS)
        self.assertEqual(bar.value(), 100)

    def test_batch_set_version(self):
        item1 = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        item2 = _make_item("b", "SQ020", "SH0200", "Plate", "PL02", "/tmp/b.mov")
        self.table.populate_table([item1, item2])

        self.table._batch_version_spin.setValue(7)
        self.table._on_batch_set_version()

        self.assertEqual(self.table.item_version[id(item1)], 7)
        self.assertEqual(self.table.item_version[id(item2)], 7)

    def test_batch_rename_media_type_and_case(self):
        item = _make_item("a", "SQ010", "SH0100", "plate", "PL01", "/tmp/a.mov")
        self.table.populate_table([item])

        self.table._tmpl_edit.setText("BG Plate")
        self.table._target_combo.setCurrentText("Media Type")
        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._on_apply_rename()
        self.assertEqual(item.media_type, "BG Plate")

        self.table._apply_case("upper")
        self.assertEqual(item.media_type, "BG PLATE")


class TestTableAuditFixes(unittest.TestCase):
    """
    Audit findings: the toolbar's discard/re-include buttons skipped conflict
    re-detection (only the row checkbox did it), two of the three batch tools
    ignored the scope dropdown next to them, a "New" row's version dropdown
    offered exactly one option so it couldn't change anything, and the
    summary line never counted Missing Details rows.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def _two_conflicting(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        b = _make_item("b", "SQ010", "SH0100", "Plate", "BG", "/tmp/b.mov")
        self.table.populate_table([a, b])
        self.table._run_conflict_detection()
        self.table._refresh_table()
        return a, b

    def test_discard_button_resolves_a_conflict_like_the_checkbox_does(self):
        _a, _b = self._two_conflicting()
        self.assertTrue(self.table.has_unresolved_conflicts())
        self.table._table.selectRow(1)
        self.table._on_discard_selected()
        self.assertFalse(self.table.has_unresolved_conflicts())

    def test_reinclude_button_recreates_the_conflict_it_was_hiding(self):
        _a, b = self._two_conflicting()
        self.table._on_checkbox_changed(id(b), 0)
        self.assertFalse(self.table.has_unresolved_conflicts())
        self.table._table.selectRow(1)
        self.table._on_restore_selected()
        self.assertTrue(self.table.has_unresolved_conflicts())

    def test_scope_dropdown_is_honoured_by_case_and_version_tools(self):
        a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
        b = _make_item("b", "sq020", "sh0200", "plate", "fg", "/tmp/b.mov")
        self.table.populate_table([a, b])
        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._table.selectRow(0)   # a row is selected, but scope says All

        self.table._apply_case("upper")
        self.assertEqual(b.shot_code, "SH0200")

        self.table._batch_version_spin.setValue(7)
        self.table._on_batch_set_version()
        self.assertEqual(self.table.item_version[id(b)], 7)

    def test_selected_scope_with_nothing_selected_touches_nothing(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._scope_combo.setCurrentText("Apply to Selected Rows")
        self.table._table.clearSelection()
        self.table._apply_case("lower")
        self.assertEqual(a.shot_code, "SH0100")   # untouched, not silently "all"

    def test_new_row_version_dropdown_offers_more_than_one_choice(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        combo = self.table._table.cellWidget(0, COL_VERSION)
        self.assertGreater(combo.count(), 1)
        self.assertTrue(combo.currentText().startswith("v001"))

        combo.setCurrentIndex(2)   # pick v003
        self.assertEqual(self.table.item_version[id(a)], 3)

    def test_summary_counts_missing_details_rows(self):
        good = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        bad = _make_item("b", "", "", "", "", "/tmp/b.mov")
        self.table.populate_table([good, bad])
        self.assertIn("1 missing details", self.table._status_lbl.text())


if __name__ == "__main__":
    unittest.main()
