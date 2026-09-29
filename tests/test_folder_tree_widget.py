import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtWidgets

from square_core.paths.path_pattern import PathPattern
from tools.ingest_tool.widgets.folder_tree_widget import FolderTreeWidget
from tools.ingest_tool.widgets.path_pattern_dialog import PathPatternManagerDialog


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


class TestPatternManagerSaveAndImport(unittest.TestCase):
    """Save As... / Import... in the Path Patterns manager: a reusable
    pattern list is a plain file the user places, not a named preset
    registry -- there is no active/loaded-preset tracking left afterward."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        (d / "plate.1001.exr").write_text("x")
        self.tree = FolderTreeWidget()
        self.tree.load_path(str(self.tmp))
        self.tree._mapper.add_path_pattern(PathPattern(
            template="<sequence>/<shot>/plate.####.exr", defaults={"media_type": "Plate"}))
        self.save_dir = Path(tempfile.mkdtemp())       # outside the scanned root
        self.addCleanup(shutil.rmtree, self.save_dir, ignore_errors=True)
        self.out = self.save_dir / "saved.json"

    def _dlg(self):
        return PathPatternManagerDialog(self.tree._mapper)

    def test_save_as_writes_the_full_pattern_including_its_defaults(self):
        dlg = self._dlg()
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName",
                          return_value=(str(self.out), "")):
            dlg._on_save_as()
        self.assertTrue(self.out.exists())
        data = json.loads(self.out.read_text())
        self.assertEqual(len(data["patterns"]), 1)
        self.assertEqual(data["patterns"][0]["defaults"], {"media_type": "Plate"})

    def test_save_as_appends_json_if_the_user_left_it_off(self):
        dlg = self._dlg()
        bare = str(self.tmp / "saved")
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(bare, "")):
            dlg._on_save_as()
        self.assertTrue((self.tmp / "saved.json").exists())

    def test_import_replace_swaps_the_pattern_list(self):
        self.out.write_text(json.dumps({"patterns": [
            {"template": "<sequence>/<shot>/other.####.exr"}]}))
        dlg = self._dlg()
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                          return_value=(str(self.out), "")):
            dlg._on_import(replace=True)
        patterns = self.tree._mapper.get_path_patterns()
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0].template, "<sequence>/<shot>/other.####.exr")
        self.assertTrue(dlg.changed)

    def test_import_append_keeps_what_was_already_there(self):
        self.out.write_text(json.dumps({"patterns": [
            {"template": "<sequence>/<shot>/other.####.exr"}]}))
        dlg = self._dlg()
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                          return_value=(str(self.out), "")):
            dlg._on_import(replace=False)
        patterns = self.tree._mapper.get_path_patterns()
        self.assertEqual([p.template for p in patterns],
                         ["<sequence>/<shot>/plate.####.exr", "<sequence>/<shot>/other.####.exr"])

    def test_a_bare_list_file_is_also_accepted(self):
        self.out.write_text(json.dumps([{"template": "<sequence>/<shot>/other.####.exr"}]))
        dlg = self._dlg()
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                          return_value=(str(self.out), "")):
            dlg._on_import(replace=True)
        self.assertEqual(len(self.tree._mapper.get_path_patterns()), 1)

    def test_an_unreadable_file_is_reported_and_changes_nothing(self):
        bad = self.tmp / "bad.json"
        bad.write_text("not json")
        dlg = self._dlg()
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName", return_value=(str(bad), "")),              patch.object(QtWidgets.QMessageBox, "warning") as warn:
            dlg._on_import(replace=True)
        warn.assert_called_once()
        self.assertEqual(len(self.tree._mapper.get_path_patterns()), 1)   # untouched
        self.assertFalse(dlg.changed)

    def test_round_trip_preserves_everything_a_pattern_needs(self):
        with patch.object(QtWidgets.QFileDialog, "getSaveFileName",
                          return_value=(str(self.out), "")):
            self._dlg()._on_save_as()
        tree2 = FolderTreeWidget()
        tree2.load_path(str(self.tmp))
        dlg2 = PathPatternManagerDialog(tree2._mapper)
        with patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                          return_value=(str(self.out), "")):
            dlg2._on_import(replace=True)
        items = tree2._mapper.build_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].media_type, "Plate")

    def test_there_is_no_named_preset_tracking_left(self):
        for name in ("_on_save_ingest_preset", "_on_preset_selected",
                    "_maybe_sync_active_preset", "_refresh_preset_combo", "_presets",
                    "active_preset"):
            self.assertFalse(hasattr(self.tree, name), name)


class TestFolderSelectionLoadsTaggedChildren(unittest.TestCase):
    """Selecting a parent folder must not mean selecting every row by hand."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for shot, stem in (("SH0100", "plate"), ("SH0100", "other"), ("SH0200", "plate")):
            d = self.tmp / "SQ010" / shot
            d.mkdir(parents=True, exist_ok=True)
            for f in range(1001, 1004):
                (d / f"{stem}.{f}.exr").write_text("x")
        self.tree = FolderTreeWidget()
        self.tree.load_path(str(self.tmp))
        self.tree._mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))

    def _find(self, parent, name):
        for i in range(parent.childCount()):
            c = parent.child(i)
            if c.text(0).startswith(name):
                return c
            found = self._find(c, name)
            if found:
                return found
        return None

    def _select(self, item):
        self.tree._tree.clearSelection()
        item.setSelected(True)
        return self.tree.get_selected_file_paths()

    def test_a_selected_folder_has_no_explicit_paths(self):
        sel = self._select(self._find(self.tree._tree.topLevelItem(0), "SQ010"))
        self.assertEqual(sel.explicit, set())
        self.assertTrue(sel)

    def test_a_selected_sequence_row_is_explicit(self):
        sel = self._select(self._find(self.tree._tree.topLevelItem(0), "other."))
        self.assertEqual(len(sel.explicit), 3)
        self.assertTrue(all("other." in p for p in sel.explicit))

    def test_selecting_the_top_folder_yields_only_the_tagged_shots(self):
        sel = self._select(self.tree._tree.topLevelItem(0))
        items = self.tree._mapper.build_items(filter_paths=sel, tagged_only=True,
                                              explicit_paths=sel.explicit)
        self.assertEqual(sorted(i.shot_code for i in items), ["SH0100", "SH0200"])
        self.assertTrue(all("plate." in i.files[0] for i in items))


class TestLoadDropDown(unittest.TestCase):
    """Load / Update are split buttons: the click loads matched/tagged only,
    the drop-down entry also brings in what nothing matched."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        (d / "plate.1001.exr").write_text("x")
        self.tree = FolderTreeWidget()
        self.tree.load_path(str(self.tmp))
        self.got = []
        self.tree.load_requested.connect(lambda *a: self.got.append(a))

    def _entry(self, button, fragment):
        return next(a for a in button.menu().actions() if fragment in a.text())

    def test_the_main_click_excludes_unmatched(self):
        self.tree._load_btn.click()
        self.assertEqual(len(self.got), 1)
        root, _mapper, _sel, is_update, include_untagged = self.got[0]
        self.assertFalse(is_update)
        self.assertFalse(include_untagged)

    def test_the_drop_down_entry_includes_unmatched(self):
        self._entry(self.tree._load_btn, "everything").trigger()
        self.assertEqual(self.got[0][3:], (False, True))

    def test_update_has_the_same_pair(self):
        self._entry(self.tree._update_btn, "everything").trigger()
        self.assertEqual(self.got[0][3:], (True, True))
        self.tree._update_btn.click()
        self.assertEqual(self.got[1][3:], (True, False))


class TestEveryFileTypeIsShown(unittest.TestCase):
    """The tree must not hide a file just because its extension is unfamiliar."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for name in ("grade.cdl", "look.lut", "notes.txt", "plate.1001.exr",
                     "lensgrid.1001.xyz", "lensgrid.1002.xyz", "v_001.cdl",
                     ".hidden", "Thumbs.db"):
            (d / name).write_text("x")
        self.tree = FolderTreeWidget()
        self.tree.load_path(str(self.tmp))

    def _labels(self):
        out = []

        def walk(it):
            out.append((it.text(0), it.data(0, 257)))
            for i in range(it.childCount()):
                walk(it.child(i))
        walk(self.tree._tree.topLevelItem(0))
        return out

    def test_unfamiliar_extensions_are_listed_as_files(self):
        labels = dict(self._labels())
        for name in ("grade.cdl", "look.lut", "notes.txt", "v_001.cdl"):
            self.assertEqual(labels.get(name), "file", name)

    def test_numbered_files_of_any_type_still_group_into_a_sequence(self):
        seqs = [t for t, k in self._labels() if k == "sequence"]
        self.assertTrue(any(t.startswith("lensgrid.") and "2f" in t for t in seqs), seqs)

    def test_hidden_and_os_junk_files_stay_out(self):
        names = [t for t, _k in self._labels()]
        self.assertNotIn(".hidden", names)
        self.assertNotIn("Thumbs.db", names)


if __name__ == "__main__":
    unittest.main()
