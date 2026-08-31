import tempfile
import unittest
from pathlib import Path

from Qt import QtWidgets

from tools.ingest_tool.ui_main import MainWindow
from square_core.plate_scanner import IngestSequenceItem


def _make_item(name, seq, shot, mtype, mname):
    tmp_file = Path(tempfile.mkdtemp()) / f"{name}.mov"
    tmp_file.write_text("x" * 1000)
    item = IngestSequenceItem(name, [str(tmp_file)], ".mov", is_video=True)
    item.sequence_code = seq
    item.shot_code = shot
    item.media_type = mtype
    item.media_name = mname
    return item


class TestOverlappingNASChecksBothComplete(unittest.TestCase):
    """
    Confirmed bug: every NAS check (initial batch load, a rename's
    revalidation, a manual version pick) shared ONE worker slot, and
    starting a new check while one was still running called .terminate()
    on it -- so a quick second action (e.g. picking a version on one row
    while a big initial load-check was still scanning the rest) killed the
    first check before it could report results for anything else it was
    covering. Those rows then showed "Checking..." forever, with nothing
    left to ever resolve them short of a full reload. .terminate() is also
    itself unsafe -- reproduced a real
    "QThread: Destroyed while thread '' is still running" warning from it.

    Each check now gets its own worker and they run to completion
    independently; nothing is killed mid-flight.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.window = MainWindow()
        self.window.config.nas_root = str(self.tmp / "nas")
        self.window.project_data = {"code": "ALPHA"}
        self.window.table_widget.set_project_code("ALPHA")
        self.window.table_widget.set_nas_root(str(self.tmp / "nas"))
        self.addCleanup(self.window.close)

    def test_a_second_check_does_not_kill_the_first(self):
        items = [_make_item(f"clip{i}", f"SQ{i:03d}", f"SH0{i}00", "Plate", "BG") for i in range(6)]
        self.window.table_widget.populate_table(items)

        self.window._start_nas_check(items)

        # A second, overlapping request for just one row -- exactly what a
        # manual version pick fires while the first check is still going.
        self.window._start_nas_check([items[0]], forced_versions={id(items[0]): 5})

        self.assertEqual(len(self.window._nas_check_workers), 2)   # both live, neither replaced/killed
        for w in list(self.window._nas_check_workers):
            w.wait(5000)
        QtWidgets.QApplication.processEvents()

        stuck = [
            it.name for it in items
            if self.window.table_widget.item_status.get(id(it)) == "Checking..."
        ]
        self.assertEqual(stuck, [], "rows left stuck on Checking... -- the first check was cut off")
        self.assertEqual(self.window._nas_check_workers, [])

    def test_three_overlapping_checks_all_resolve(self):
        items = [_make_item(f"c{i}", f"SQ{i:03d}", f"SH0{i}00", "Plate", "BG") for i in range(9)]
        self.window.table_widget.populate_table(items)

        self.window._start_nas_check(items[0:3])
        self.window._start_nas_check(items[3:6])
        self.window._start_nas_check(items[6:9])
        self.assertEqual(len(self.window._nas_check_workers), 3)

        for w in list(self.window._nas_check_workers):
            w.wait(5000)
        QtWidgets.QApplication.processEvents()

        for it in items:
            self.assertNotEqual(self.window.table_widget.item_status.get(id(it)), "Checking...")

    def test_progress_bar_reflects_the_combined_total_while_both_run(self):
        items_a = [_make_item(f"a{i}", f"SQ0{i}0", f"SH0{i}00", "Plate", "BG") for i in range(4)]
        items_b = [_make_item(f"b{i}", f"SQ1{i}0", f"SH1{i}00", "Plate", "FG") for i in range(2)]
        self.window.table_widget.populate_table(items_a + items_b)

        self.window._start_nas_check(items_a)
        self.window._start_nas_check(items_b)

        self.assertEqual(self.window._check_bar.maximum(), 6)   # 4 + 2, not just the latest request's total

    def test_close_event_waits_on_every_worker_not_just_one(self):
        items = [_make_item(f"x{i}", f"SQ2{i}0", f"SH2{i}00", "Plate", "BG") for i in range(4)]
        self.window.table_widget.populate_table(items)
        self.window._start_nas_check(items[0:2])
        self.window._start_nas_check(items[2:4])
        self.assertEqual(len(self.window._nas_check_workers), 2)

        from Qt import QtGui
        event = QtGui.QCloseEvent()
        self.window.closeEvent(event)   # must not raise, must not leave a thread dangling

        for w in self.window._nas_check_workers:
            self.assertFalse(w.isRunning())


if __name__ == "__main__":
    unittest.main()
