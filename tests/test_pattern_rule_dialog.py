import shutil
import tempfile
import unittest
from pathlib import Path

from Qt import QtWidgets

from square_core.folder_mapper import FolderMapper
from tools.ingest_tool.widgets.pattern_rule_dialog import PatternRuleEditDialog


class TestPatternRuleEditDialogScopeToggle(unittest.TestCase):
    """
    Confirmed bug: the "Whole name" / "Anywhere" radio buttons weren't
    actually mutually exclusive (plain sibling QRadioButtons across a nested
    layout don't reliably auto-exclude), so checking "Anywhere" left "Whole
    name" still checked too and _current_rule() kept reporting match_scope
    "whole" -- the dialog's own live preview couldn't demonstrate the one
    thing it exists to demonstrate. Same bug, same fix, on the pre-existing
    Regex/Glob pair while in there.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "sh10").mkdir(parents=True)
        (self.tmp / "sh10_ref").mkdir(parents=True)
        (self.tmp / "sh10_edl").mkdir(parents=True)
        self.mapper = FolderMapper(self.tmp)

    def test_scope_radios_are_mutually_exclusive(self):
        dlg = PatternRuleEditDialog(self.mapper)
        dlg.anywhere_scope_radio.setChecked(True)
        self.assertFalse(dlg.whole_scope_radio.isChecked())
        self.assertTrue(dlg.anywhere_scope_radio.isChecked())
        self.assertEqual(dlg._current_rule().match_scope, "anywhere")

    def test_pattern_type_radios_are_mutually_exclusive(self):
        dlg = PatternRuleEditDialog(self.mapper)
        dlg.glob_radio.setChecked(True)
        self.assertFalse(dlg.regex_radio.isChecked())
        self.assertTrue(dlg.glob_radio.isChecked())
        self.assertFalse(dlg._current_rule().is_regex)

    def test_whole_vs_anywhere_scope_changes_the_actual_match_set(self):
        dlg = PatternRuleEditDialog(self.mapper)
        dlg.pattern_edit.setText(r"(?i)sh10")

        dlg.whole_scope_radio.setChecked(True)
        whole_matches = {n for n, _ in self.mapper.sample_pattern_matches(dlg._current_rule(), limit=10)}
        self.assertEqual(whole_matches, {"sh10"})

        dlg.anywhere_scope_radio.setChecked(True)
        anywhere_matches = {n for n, _ in self.mapper.sample_pattern_matches(dlg._current_rule(), limit=10)}
        self.assertEqual(anywhere_matches, {"sh10", "sh10_ref", "sh10_edl"})


if __name__ == "__main__":
    unittest.main()
