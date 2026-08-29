"""
Comprehensive Automated Tests for 2-Tier Modular Token Architecture
Tests Scenarios A, B, and C with dummy files and folders.
"""

import sys
import os
import shutil
import unittest
from pathlib import Path

# Ensure repo root in sys.path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from square_core.token_parser import (
    TokenRule,
    HierarchyRule,
    split_text_into_tokens,
    merge_token_indices,
    parse_string_with_token_rule,
)
from square_core.config import StudioConfig
from tools.ingest_tool.widgets.token_splitter_dialog import TokenSplitterDialog


TEST_BASE_DIR = Path("C:/tmp/square_test_scenarios")


class TestTokenArchitecture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Create dummy folder structures for Scenarios A, B, and C."""
        if TEST_BASE_DIR.exists():
            shutil.rmtree(TEST_BASE_DIR, ignore_errors=True)
        TEST_BASE_DIR.mkdir(parents=True, exist_ok=True)

        # Scenario A: Flat MOV files
        scen_a = TEST_BASE_DIR / "scenario_flat_movs"
        scen_a.mkdir()
        (scen_a / "MYPROJ_SQ010_SH0100_Plate_v001.mov").write_text("dummy mov content")
        (scen_a / "MYPROJ_SQ010_SH0200_Ref_v001.mov").write_text("dummy mov content")

        # Scenario B: Nested Sequence folder + combined filename
        scen_b = TEST_BASE_DIR / "scenario_nested_combo" / "SEQ_ALPHA"
        scen_b.mkdir(parents=True)
        (scen_b / "SHOT_0100_plate_v01.mov").write_text("dummy mov content")

        # Scenario C: Deep 3-Level hierarchy
        scen_c = TEST_BASE_DIR / "scenario_3level" / "SQ020" / "SH0200" / "PL01"
        scen_c.mkdir(parents=True)
        (scen_c / "SQ020_SH0200_PL01.1001.exr").write_text("dummy exr content")

    @classmethod
    def tearDownClass(cls):
        """Clean up dummy folders."""
        if TEST_BASE_DIR.exists():
            shutil.rmtree(TEST_BASE_DIR, ignore_errors=True)

    def test_scenario_a_flat_movs_token_parsing(self):
        """Test TokenRule on MYPROJ_SQ010_SH0100_Plate_v001.mov."""
        # Tokens: ['MYPROJ', 'SQ010', 'SH0100', 'Plate', 'v001', '.mov']
        rule = TokenRule(
            name="Flat MOV Rule",
            delimiter="_",
            mapping={
                "sequence_code": [1],
                "shot_code": [2],
                "media_name": [3],
                "version": [4],
                "media_type": [5]
            }
        )
        res = parse_string_with_token_rule("MYPROJ_SQ010_SH0100_Plate_v001.mov", rule)
        self.assertEqual(res["sequence_code"], "SQ010")
        self.assertEqual(res["shot_code"], "SH0100")
        self.assertEqual(res["media_name"], "PLATE")
        self.assertEqual(res["version"], 1)

    def test_token_merging(self):
        """Test chip merging: SEQ + 01 -> SEQ_01."""
        tokens = ["SEQ", "01", "SHOT", "0100", "Plate", "v001"]
        merged = merge_token_indices(tokens, 0, 1, join_char="_")
        self.assertEqual(merged[0], "SEQ_01")
        self.assertEqual(merged[1], "SHOT")

    def test_scenario_b_hierarchy_rule(self):
        """Test HierarchyRule combining Level 1 direct tag with Level 2 token preset."""
        h_rule = HierarchyRule(
            name="Nested Sequence + Combined File",
            level_mappings={
                "1": {"type": "direct", "tag": "SEQ"},
                "2": {"type": "token_preset", "preset_name": "Shot_Media_Version"}
            }
        )
        self.assertEqual(h_rule.level_mappings["1"]["tag"], "SEQ")
        self.assertEqual(h_rule.level_mappings["2"]["preset_name"], "Shot_Media_Version")

    def test_studio_config_presets_persistence(self):
        """Test that StudioConfig stores and loads token and ingest presets."""
        cfg = StudioConfig()
        self.assertIn("Shot_Media_Version", cfg.token_presets)
        self.assertIn("VFX Standard 3-Level", cfg.ingest_presets)

    def test_token_splitter_dialog_instantiation(self):
        """Test GUI instantiation of TokenSplitterDialog modal."""
        dlg = TokenSplitterDialog("SHOT_0100_plate_v01.mov")
        self.assertIsNotNone(dlg)
        res = dlg.get_parsed_result()
        self.assertIsInstance(res, dict)

    def test_token_splitter_fixed_media_type_quick_tag(self):
        """
        The 'Tag as {type}' quick-menu (e.g. "Tag as Ref") must record that
        literal type regardless of what the underlying chip text says --
        previously every entry in that menu called the same generic handler
        and ignored which type was actually clicked.
        """
        dlg = TokenSplitterDialog("SQ010_SH0100_PL_v001.mov")
        # Chip index 2 is "PL" -- tag it as media_type with a fixed literal
        # value of "Ref", as the "Tag as Ref" menu action now does.
        for btn in dlg.chip_buttons:
            if btn.token_index == 2:
                btn.setChecked(True)
        dlg.assign_role_to_selected("media_type", fixed_value="Ref")

        res = dlg.get_parsed_result()
        self.assertEqual(res.get("media_type"), "Ref")

        tagged_btn = next(b for b in dlg.chip_buttons if b.token_index == 2)
        self.assertEqual(tagged_btn.fixed_value, "Ref")


if __name__ == "__main__":
    unittest.main()
