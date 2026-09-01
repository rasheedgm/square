import os
import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtWidgets

from square_core.path_pattern import PathPattern
from tools.ingest_tool.widgets.folder_tree_widget import FolderTreeWidget


class TestSessionRestore(unittest.TestCase):
    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        for fr in range(1001, 1004):
            (self.tmp / "SQ010" / "SH0100" / f"plate.{fr}.exr").write_text("x")

    def test_current_patterns_are_serializable_dicts(self):
        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        tree._mapper.set_path_patterns(["<sequence>/<shot>/####.<extension>"])
        pats = tree.current_patterns()
        self.assertEqual(len(pats), 1)
        self.assertIsInstance(pats[0], dict)
        self.assertIn("template", pats[0])

    def test_restore_reopens_folder_and_reapplies_patterns(self):
        tree = FolderTreeWidget()
        tree.restore(str(self.tmp),
                     patterns=[{"name": "p", "template": "<sequence>/<shot>/####.<extension>"}])
        self.assertEqual(tree.root_path, str(self.tmp))
        self.assertEqual(len(tree._mapper.get_path_patterns()), 1)
        # tree actually populated
        self.assertGreater(tree._tree.topLevelItemCount(), 0)

    def test_restore_ignores_a_missing_folder(self):
        tree = FolderTreeWidget()
        tree.restore(str(self.tmp / "gone"), patterns=[])
        self.assertIsNone(tree.root_path)


class TestSingleSequenceSelection(unittest.TestCase):
    """
    Confirmed bug: selecting one sequence item directly in the tree (not its
    parent folder) and clicking Load/Update silently loaded nothing --
    ROLE_PATH on a "sequence" row is a synthetic "prefix.ext" display path
    with no frame digits (e.g. "plate.exr"), which never equals any of a
    real IngestSequenceItem's actual frame file paths, so build_items()'s
    filter_paths intersection was always empty for a directly-selected
    sequence. Selecting the parent folder worked because the folder's own
    real path IS one of the paths build_items() checks against.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _find_item(self, parent, name):
        for i in range(parent.childCount()):
            c = parent.child(i)
            if c.text(0).startswith(name):
                return c
            found = self._find_item(c, name)
            if found:
                return found
        return None

    def test_selecting_one_sequence_directly_resolves_to_its_real_frame_files(self):
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for f in range(1001, 1004):
            (d / f"plate.{f}.exr").write_text("x")

        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))

        root_item = tree._tree.topLevelItem(0)
        seq_item = self._find_item(root_item, "plate.")
        self.assertIsNotNone(seq_item, "expected to find the collapsed sequence row")

        tree._tree.clearSelection()
        seq_item.setSelected(True)

        result = tree.get_selected_file_paths()
        self.assertIsNotNone(result)
        expected = {str((d / f"plate.{f}.exr").resolve()).lower() for f in (1001, 1002, 1003)}
        self.assertEqual({p.lower() for p in result}, expected)

    def test_filtered_build_items_actually_returns_the_selected_sequence(self):
        # End-to-end: the bug's real symptom was an empty table after
        # Load/Update on a directly-selected single item.
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for f in range(1001, 1004):
            (d / f"plate.{f}.exr").write_text("x")

        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        tree._mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))

        root_item = tree._tree.topLevelItem(0)
        seq_item = self._find_item(root_item, "plate.")
        tree._tree.clearSelection()
        seq_item.setSelected(True)

        selected_paths = tree.get_selected_file_paths()
        items = tree._mapper.build_items(filter_paths=selected_paths)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].shot_code, "SH0100")

    def test_selecting_one_of_two_sibling_sequences_does_not_pull_in_the_other(self):
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for f in range(1001, 1003):
            (d / f"bg.{f}.exr").write_text("x")
        for f in range(1001, 1003):
            (d / f"fg.{f}.exr").write_text("x")

        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))

        root_item = tree._tree.topLevelItem(0)
        bg_item = self._find_item(root_item, "bg.")
        tree._tree.clearSelection()
        bg_item.setSelected(True)

        result = tree.get_selected_file_paths()
        self.assertTrue(all("bg." in p for p in result))
        self.assertFalse(any("fg." in p for p in result))


if __name__ == "__main__":
    unittest.main()
