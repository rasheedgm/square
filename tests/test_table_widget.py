import unittest
from Qt import QtWidgets

from tools.ingest_tool.widgets.table_widget import IngestTableWidget, STATUS_CONFLICT, STATUS_NEW, STATUS_DISCARDED
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


if __name__ == "__main__":
    unittest.main()
