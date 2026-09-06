import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtWidgets

from square_core.paths.path_pattern import PathPattern
from tools.ingest_tool.core import presets as ingest_presets
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


class TestMediaTypeContextMenuTagging(unittest.TestCase):
    """
    Confirmed bug: the "Tag as <type>" context-menu action for a sequence
    leaf stored the tag keyed by the tree's SYNTHETIC display path
    ("plate.exr", frame digits stripped) -- but FolderMapper.build_items()
    looks the tag up keyed by the real first-frame file (or its parent
    folder), which never equals that synthetic path. The tag looked applied
    in the tree (its own badge lookup used the same wrong key, so it read
    back consistently) but never actually reached the row once loaded into
    the review table. `_set_media_type` now resolves to the real file first.
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

    def test_tagging_a_sequence_via_context_menu_reaches_the_built_item(self):
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for f in range(1001, 1004):
            (d / f"plate.{f}.exr").write_text("x")

        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        root_item = tree._tree.topLevelItem(0)
        seq_item = self._find_item(root_item, "plate.")
        path_str = seq_item.data(0, __import__(
            "tools.ingest_tool.widgets.folder_tree_widget", fromlist=["ROLE_PATH"]).ROLE_PATH)

        real_path = tree._real_key_for(Path(path_str), "sequence")
        self.assertNotEqual(str(real_path), path_str)   # confirms the synthetic/real gap is real
        tree._set_media_type(seq_item, real_path, "Plate")

        items = tree._mapper.build_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].media_type, "Plate")

    def test_badge_and_stored_tag_agree_after_tagging(self):
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for f in range(1001, 1004):
            (d / f"plate.{f}.exr").write_text("x")

        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        root_item = tree._tree.topLevelItem(0)
        seq_item = self._find_item(root_item, "plate.")
        path_str = seq_item.data(0, __import__(
            "tools.ingest_tool.widgets.folder_tree_widget", fromlist=["ROLE_PATH"]).ROLE_PATH)
        real_path = tree._real_key_for(Path(path_str), "sequence")

        tree._set_media_type(seq_item, real_path, "Ref")
        tree._refresh_item_colours()
        badge = seq_item.data(0, __import__(
            "tools.ingest_tool.widgets.folder_tree_widget", fromlist=["ROLE_MEDIA_TYPE"]).ROLE_MEDIA_TYPE)
        self.assertEqual(badge, "Ref")
        self.assertEqual(tree._mapper.build_items()[0].media_type, "Ref")


class TestIngestPresetsPreserveDefaults(unittest.TestCase):
    """
    Confirmed bug: saving the current tagging as an Ingest Preset kept only
    each pattern's bare template STRING, so a pattern's "Defaults for Fields
    Not in the Path" (e.g. media_type defaulted to "Plate" because it's never
    part of this vendor's folder structure) silently vanished the moment it
    round-tripped through a preset -- reselecting the preset later reapplied
    the template but never the default again.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_dir = self.tmp / "state"
        self._old_state_dir = os.environ.get("SQUARE_STATE_DIR")
        os.environ["SQUARE_STATE_DIR"] = str(self.state_dir)
        self.addCleanup(self._restore_state_dir)

        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        (d / "plate.1001.exr").write_text("x")

    def _restore_state_dir(self):
        if self._old_state_dir is None:
            os.environ.pop("SQUARE_STATE_DIR", None)
        else:
            os.environ["SQUARE_STATE_DIR"] = self._old_state_dir

    def test_saving_and_reapplying_a_preset_keeps_the_pattern_s_defaults(self):
        from unittest.mock import patch

        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        tree._mapper.add_path_pattern(PathPattern(
            template="<sequence>/<shot>/plate.####.exr",
            defaults={"media_type": "Plate"},
        ))

        with patch.object(QtWidgets.QInputDialog, "getText", return_value=("Vendor", True)):
            tree._on_save_ingest_preset()

        # simulate a fresh session: a brand new tree, presets reloaded from disk
        tree2 = FolderTreeWidget()
        tree2.load_path(str(self.tmp))
        tree2._on_preset_selected("Vendor")

        patterns = tree2._mapper.get_path_patterns()
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0].defaults, {"media_type": "Plate"})

        items = tree2._mapper.build_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].media_type, "Plate")


class TestActivePresetSync(unittest.TestCase):
    """
    Feature: once a preset is active, a pattern edit made afterward (via
    either the builder or the Path Patterns manager) offers to update that
    preset instead of silently drifting out of sync with what's actually
    tagging this root.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state_dir = self.tmp / "state"
        self._old_state_dir = os.environ.get("SQUARE_STATE_DIR")
        os.environ["SQUARE_STATE_DIR"] = str(self.state_dir)
        self.addCleanup(self._restore_state_dir)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        (d / "plate.1001.exr").write_text("x")

    def _restore_state_dir(self):
        if self._old_state_dir is None:
            os.environ.pop("SQUARE_STATE_DIR", None)
        else:
            os.environ["SQUARE_STATE_DIR"] = self._old_state_dir

    def _tree_with_saved_preset(self):
        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        tree._mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))
        with patch.object(QtWidgets.QInputDialog, "getText", return_value=("Vendor", True)):
            tree._on_save_ingest_preset()
        return tree

    def test_no_prompt_when_no_preset_is_active(self):
        tree = FolderTreeWidget()
        tree.load_path(str(self.tmp))
        with patch.object(QtWidgets.QMessageBox, "question") as q:
            tree._maybe_sync_active_preset()
            q.assert_not_called()

    def test_accepting_the_prompt_updates_the_saved_preset(self):
        tree = self._tree_with_saved_preset()
        tree._mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/other.####.exr"))

        yes = QtWidgets.QMessageBox.StandardButton.Yes
        with patch.object(QtWidgets.QMessageBox, "question", return_value=yes):
            tree._maybe_sync_active_preset()

        reloaded = ingest_presets.load()
        self.assertEqual(len(reloaded["presets"]["Vendor"]["patterns"]), 2)

    def test_declining_the_prompt_leaves_the_saved_preset_untouched(self):
        tree = self._tree_with_saved_preset()
        tree._mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/other.####.exr"))

        no = QtWidgets.QMessageBox.StandardButton.No
        with patch.object(QtWidgets.QMessageBox, "question", return_value=no):
            tree._maybe_sync_active_preset()

        reloaded = ingest_presets.load()
        self.assertEqual(len(reloaded["presets"]["Vendor"]["patterns"]), 1)

    def test_manage_patterns_dialog_offers_sync_only_when_something_changed(self):
        tree = self._tree_with_saved_preset()

        with patch("tools.ingest_tool.widgets.folder_tree_widget.PathPatternManagerDialog") as MgrDlg, \
             patch.object(tree, "_maybe_sync_active_preset") as sync:
            inst = MgrDlg.return_value
            inst.exec.return_value = 1
            inst.changed = False
            tree._on_manage_patterns()
            sync.assert_not_called()

            inst.changed = True
            tree._on_manage_patterns()
            sync.assert_called_once()

    def test_open_pattern_builder_offers_sync_when_a_pattern_is_saved(self):
        from tools.qt_compat import DIALOG_ACCEPTED

        tree = self._tree_with_saved_preset()
        with patch("tools.ingest_tool.widgets.folder_tree_widget.PathPatternBuilderDialog") as Dlg, \
             patch.object(tree, "_maybe_sync_active_preset") as sync:
            inst = Dlg.return_value
            inst.exec.return_value = DIALOG_ACCEPTED
            inst.result_pattern = PathPattern(template="<sequence>/<shot>/other.####.exr")
            inst.result_replace_index = None
            tree._resolve_item_for_node = lambda *a, **k: type(
                "S", (), {"files": [str(self.tmp / "SQ010" / "SH0100" / "plate.1001.exr")]})()
            tree._open_path_pattern_builder(self.tmp / "SQ010" / "SH0100" / "plate.exr", "sequence")
            sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
