import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtWidgets

from square_core.folder_mapper import FolderMapper
from square_core.plate_scanner import PlateScanner
from tools.ingest_tool.widgets.path_pattern_dialog import ChipButton, SegmentRow, PathPatternBuilderDialog


class TestChipButton(unittest.TestCase):
    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_frame_piece_is_auto_locked_not_taggable(self):
        chip = ChipButton(0, "####")
        self.assertTrue(chip.is_frame)
        self.assertFalse(chip.isCheckable())
        self.assertFalse(chip.isEnabled())
        self.assertEqual(chip.rendered_piece(), "####")

    def test_untagged_chip_renders_as_its_own_literal_text(self):
        chip = ChipButton(0, "SQ010")
        self.assertEqual(chip.rendered_piece(), "SQ010")

    def test_role_tag_renders_as_placeholder(self):
        chip = ChipButton(0, "SQ010")
        chip.set_role("sequence")
        self.assertEqual(chip.rendered_piece(), "<sequence>")

    def test_wildcard_renders_as_star_and_clears_role(self):
        chip = ChipButton(0, "v001")
        chip.set_role("version")
        chip.set_wildcard(True)
        self.assertEqual(chip.rendered_piece(), "*")
        self.assertIsNone(chip.role)


class TestSegmentRowDrillMergeCollapse(unittest.TestCase):
    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_whole_segment_is_one_chip_until_drilled(self):
        row = SegmentRow(0, "SQ010_SH0100_PL01")
        self.assertEqual(len(row.all_chips()), 1)
        self.assertEqual(row.rendered_template(), "SQ010_SH0100_PL01")

    def test_drill_splits_into_sub_chips_preserving_separators(self):
        row = SegmentRow(0, "SQ010_SH0100_PL01")
        row.drill()
        texts = [c.raw_text for c in row.all_chips()]
        self.assertEqual(texts, ["SQ010", "SH0100", "PL01"])
        self.assertEqual(row.rendered_template(), "SQ010_SH0100_PL01")  # untagged -> rebuilds identically

    def test_tagging_sub_chips_changes_rendered_template(self):
        row = SegmentRow(0, "SQ010_SH0100_PL01")
        row.drill()
        row.all_chips()[0].set_role("sequence")
        row.all_chips()[1].set_role("shot")
        self.assertEqual(row.rendered_template(), "<sequence>_<shot>_PL01")

    def test_collapse_undoes_drill(self):
        row = SegmentRow(0, "SQ010_SH0100_PL01")
        row.drill()
        row.all_chips()[0].set_role("sequence")
        row._on_collapse()
        self.assertEqual(len(row.all_chips()), 1)
        self.assertEqual(row.rendered_template(), "SQ010_SH0100_PL01")

    def test_merge_selected_rejoins_chips_into_one(self):
        row = SegmentRow(0, "SQ010_SH0100_PL01")
        row.drill()
        row.all_chips()[0].setChecked(True)
        row.all_chips()[1].setChecked(True)
        row.merge_selected()
        texts = [c.raw_text for c in row.all_chips()]
        self.assertEqual(texts, ["SQ010_SH0100", "PL01"])

    def test_frame_run_is_its_own_locked_chip_after_drilling_filename(self):
        row = SegmentRow(0, "plate.####.exr")
        row.drill()
        frame_chips = [c for c in row.all_chips() if c.is_frame]
        self.assertEqual(len(frame_chips), 1)
        self.assertEqual(row.rendered_template(), "plate.####.exr")

    def test_wildcard_on_untouched_folder_ignores_content(self):
        row = SegmentRow(0, "v001")
        row.all_chips()[0].setChecked(True)
        row.all_chips()[0].set_wildcard(True)
        self.assertEqual(row.rendered_template(), "*")


class TestPathPatternBuilderDialog(unittest.TestCase):
    """End-to-end: seeding the dialog from a real leaf item, tagging pieces, reading back the template."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for frame in range(1001, 1004):
            (d / f"ALPHA_SQ010_SH0100_PL01.{frame}.exr").write_text("x")
        self.mapper = FolderMapper(self.tmp)
        self.item = PlateScanner(self.tmp).scan()[0]

    def test_seed_segments_include_hash_substituted_filename(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self.assertEqual(dlg._rel_segments, ["SQ010", "SH0100", "ALPHA_SQ010_SH0100_PL01.####.exr"])

    def test_filename_segment_auto_drills_but_folders_do_not(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        folder_rows = dlg._segment_rows[:2]
        filename_row = dlg._segment_rows[2]
        for row in folder_rows:
            self.assertEqual(len(row.all_chips()), 1)
        self.assertGreater(len(filename_row.all_chips()), 1)

    def test_tagging_produces_expected_template_and_matches_the_source_item(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        dlg._segment_rows[0].all_chips()[0].setChecked(True)   # whole "SQ010" folder chip
        dlg._tag_selected("sequence")
        dlg._segment_rows[1].all_chips()[0].setChecked(True)   # whole "SH0100" folder chip
        dlg._tag_selected("shot")

        filename_chips = {c.raw_text: c for c in dlg._segment_rows[2].all_chips()}
        filename_chips["PL01"].setChecked(True)
        dlg._tag_selected("media_name")
        filename_chips["exr"].setChecked(True)
        dlg._tag_selected("extension")

        template = dlg._current_template()
        self.assertEqual(template, "<sequence>/<shot>/ALPHA_SQ010_SH0100_<media_name>.####.<extension>")

        # It must match the very file it was built from.
        from square_core.path_pattern import PathPattern
        pattern = PathPattern(template=template)
        rel = self.mapper._relative_posix(Path(self.item.files[0]))
        result = pattern.match(rel)
        self.assertEqual(result["sequence"], "SQ010")
        self.assertEqual(result["shot"], "SH0100")
        self.assertEqual(result["media_name"], "PL01")

    def test_tag_custom_strips_invalid_characters(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        dlg._segment_rows[0].all_chips()[0].setChecked(True)
        with patch.object(QtWidgets.QInputDialog, "getText", return_value=("cam era!", True)), \
             patch.object(QtWidgets.QMessageBox, "warning") as mock_warn:
            dlg._tag_custom()
        self.assertEqual(dlg._segment_rows[0].rendered_template(), "<camera>")
        mock_warn.assert_not_called()

    def test_tag_custom_rejects_a_canonical_field_name(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        dlg._segment_rows[0].all_chips()[0].setChecked(True)
        with patch.object(QtWidgets.QInputDialog, "getText", return_value=("sequence", True)), \
             patch.object(QtWidgets.QMessageBox, "warning") as mock_warn:
            dlg._tag_custom()
        mock_warn.assert_called_once()
        self.assertIsNone(dlg._segment_rows[0].all_chips()[0].role)  # rejected -- stays untagged

    def test_accept_without_a_name_falls_back_to_the_template_itself(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        dlg._segment_rows[0].all_chips()[0].setChecked(True)
        dlg._tag_selected("sequence")
        expected_template = dlg._current_template()
        with patch.object(QtWidgets.QInputDialog, "getText", return_value=("", True)):
            dlg._on_accept()
        self.assertIsNotNone(dlg.result_pattern)
        self.assertEqual(dlg.result_pattern.template, expected_template)
        self.assertEqual(dlg.result_pattern.name, expected_template)


if __name__ == "__main__":
    unittest.main()
