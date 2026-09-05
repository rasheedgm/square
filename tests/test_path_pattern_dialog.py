import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Qt import QtWidgets

from tools.ingest_tool.core.folder_mapper import FolderMapper
from square_core.paths.path_pattern import PathPattern
from square_core.media.scanner import PlateScanner
from tools.ingest_tool.widgets.path_pattern_dialog import (
    ChipButton, SegmentRow, PathPatternBuilderDialog, PathPatternManagerDialog,
)


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

    def test_split_chip_at_on_an_undrilled_segment_splits_with_no_separator(self):
        # "GGG01080" has no delimiter at all between "GGG" and "01080" for
        # Drill Into Piece to find -- splitting at an arbitrary position is
        # the only way to separate them.
        row = SegmentRow(0, "GGG01080")
        row.split_chip_at(0, 3)
        texts = [c.raw_text for c in row.all_chips()]
        self.assertEqual(texts, ["GGG", "01080"])
        # round-trips byte-for-byte: the inserted separator is empty
        self.assertEqual(row.rendered_template(), "GGG01080")

    def test_split_chip_at_on_an_already_drilled_sub_chip(self):
        row = SegmentRow(0, "SQ010_SH010080")
        row.drill()
        row.split_chip_at(1, 4)   # "SH010080" -> "SH01", "0080"
        texts = [c.raw_text for c in row.all_chips()]
        self.assertEqual(texts, ["SQ010", "SH01", "0080"])
        self.assertEqual(row.rendered_template(), "SQ010_SH010080")

    def test_split_chip_at_ignores_an_out_of_range_position(self):
        row = SegmentRow(0, "GGG01080")
        row.split_chip_at(0, 0)      # position 0 -- nothing to the left
        self.assertEqual(len(row.all_chips()), 1)
        row.split_chip_at(0, len("GGG01080"))   # position at the very end -- nothing to the right
        self.assertEqual(len(row.all_chips()), 1)

    def test_split_pieces_can_still_be_tagged_independently(self):
        row = SegmentRow(0, "GGG01080")
        row.split_chip_at(0, 3)
        chips = row.all_chips()
        chips[0].set_role("sequence")
        chips[1].set_role("shot")
        self.assertEqual(row.rendered_template(), "<sequence><shot>")


class TestSplitChipDialog(unittest.TestCase):
    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_default_split_position_is_the_midpoint(self):
        from tools.ingest_tool.widgets.path_pattern_dialog import SplitChipDialog
        dlg = SplitChipDialog("GGG01080")
        self.assertEqual(dlg.split_pos, 4)
        self.assertEqual(dlg.preview.text(), "GGG0  |  1080")

    def test_preview_updates_as_the_position_changes(self):
        from tools.ingest_tool.widgets.path_pattern_dialog import SplitChipDialog
        dlg = SplitChipDialog("GGG01080")
        dlg.spin.setValue(3)
        self.assertEqual(dlg.preview.text(), "GGG  |  01080")

    def test_get_split_position_returns_none_for_a_single_character(self):
        from tools.ingest_tool.widgets.path_pattern_dialog import SplitChipDialog
        self.assertIsNone(SplitChipDialog.get_split_position("G"))

    def test_get_split_position_returns_the_chosen_position_on_accept(self):
        from tools.ingest_tool.widgets.path_pattern_dialog import SplitChipDialog
        with patch("tools.ingest_tool.widgets.path_pattern_dialog.exec_dialog", return_value=True):
            pos = SplitChipDialog.get_split_position("GGG01080")
        self.assertIsInstance(pos, int)
        self.assertTrue(0 < pos < len("GGG01080"))


class TestPathPatternBuilderDialogSplitAction(unittest.TestCase):
    """The dialog's own "Split at Position..." button end to end."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "GGG01080").mkdir(parents=True)
        (self.tmp / "GGG01080" / "plate.1001.exr").write_text("x")
        self.mapper = FolderMapper(self.tmp)
        self.item = PlateScanner(self.tmp).scan()[0]

    def test_split_at_button_splits_the_selected_chip_and_updates_the_template(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        folder_row = dlg._segment_rows[0]
        folder_row.all_chips()[0].setChecked(True)

        with patch("tools.ingest_tool.widgets.path_pattern_dialog.SplitChipDialog.get_split_position",
                  return_value=3):
            dlg._on_split()

        texts = [c.raw_text for c in folder_row.all_chips()]
        self.assertEqual(texts, ["GGG", "01080"])
        self.assertIn("GGG01080", dlg._current_template())

    def test_split_at_button_does_nothing_without_exactly_one_selected_chip(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        with patch("tools.ingest_tool.widgets.path_pattern_dialog.SplitChipDialog.get_split_position") as get_pos:
            dlg._on_split()   # nothing selected
            get_pos.assert_not_called()


class TestPathPatternBuilderDialog(unittest.TestCase):
    """End-to-end: seeding the dialog from a real leaf item, tagging pieces, reading back the template."""

    # A template that genuinely matches this fixture's file
    # (SQ010/SH0100/ALPHA_SQ010_SH0100_PL01.1001.exr).
    MATCHING_TEMPLATE = "<sequence>/<shot>/ALPHA_<sequence>_<shot>_<media_name>.####.exr"

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

    def test_reopening_on_a_tagged_file_restores_that_pattern_s_own_tagging(self):
        # Reported confusion: after tagging a file, reopening the builder on
        # it showed a blank slate, as if the tag had been lost. It now opens
        # with the matching pattern's tagging restored, exactly as it was
        # originally made.
        saved = self.MATCHING_TEMPLATE
        self.mapper.add_path_pattern(PathPattern(template=saved))

        dlg = PathPatternBuilderDialog(self.mapper, self.item)

        self.assertEqual(dlg._segment_rows[0].all_chips()[0].role, "sequence")
        self.assertEqual(dlg._segment_rows[1].all_chips()[0].role, "shot")
        filename_roles = [c.role for c in dlg._segment_rows[2].all_chips()]
        self.assertIn("media_name", filename_roles)
        # ...and it round-trips: what's shown renders back to what was saved.
        self.assertEqual(dlg._current_template(), saved)

    def test_restored_chips_show_this_file_s_own_values(self):
        self.mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))
        d2 = self.tmp / "SQ010" / "SH0200"
        d2.mkdir(parents=True)
        for frame in range(1001, 1003):
            (d2 / f"plate.{frame}.exr").write_text("x")
        other_item = next(i for i in PlateScanner(self.tmp).scan() if "SH0200" in i.files[0])

        dlg = PathPatternBuilderDialog(self.mapper, other_item)
        self.assertEqual(dlg._segment_rows[1].all_chips()[0].raw_text, "SH0200")
        self.assertEqual(dlg._segment_rows[1].all_chips()[0].role, "shot")

    def test_starts_fresh_when_nothing_matches_yet(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self.assertIsNone(dlg._existing_index)
        self.assertIsNone(dlg._segment_rows[0].all_chips()[0].role)
        text_seen = "\n".join(
            w.text() for w in dlg.findChildren(QtWidgets.QLabel) if "saved pattern" in w.text()
        )
        self.assertEqual(text_seen, "")

    def test_banner_escapes_placeholders_so_they_survive_rich_text_rendering(self):
        self.mapper.add_path_pattern(PathPattern(template=self.MATCHING_TEMPLATE))
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        text_seen = "\n".join(
            w.text() for w in dlg.findChildren(QtWidgets.QLabel) if "saved pattern" in w.text()
        )
        # QLabel.text() returns the raw source string, HTML markup and all --
        # it can't tell an escaped "<sequence>" from one Qt's rich-text
        # parser would silently swallow as an unknown tag. The escaped form
        # is the only way to confirm the placeholder survives rendering.
        self.assertIn("&lt;sequence&gt;", text_seen)
        self.assertNotIn("<sequence>", text_seen)

    def test_saving_can_overwrite_the_matched_pattern_instead_of_adding_one(self):
        self.mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr", name="Std"))
        self.mapper.add_path_pattern(PathPattern(template=self.MATCHING_TEMPLATE))
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self.assertEqual(dlg._existing_index, 1)  # the one that actually matches this file

        with patch.object(PathPatternBuilderDialog, "_ask_save_mode", return_value="overwrite"), \
             patch.object(QtWidgets.QInputDialog, "getText", return_value=("Edited", True)):
            dlg._on_accept()
        self.assertEqual(dlg.result_replace_index, 1)

    def test_saving_can_add_a_new_pattern_alongside_the_matched_one(self):
        self.mapper.add_path_pattern(PathPattern(template=self.MATCHING_TEMPLATE))
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self.assertEqual(dlg._existing_index, 0)

        with patch.object(PathPatternBuilderDialog, "_ask_save_mode", return_value="new"), \
             patch.object(QtWidgets.QInputDialog, "getText", return_value=("Another", True)):
            dlg._on_accept()
        self.assertIsNone(dlg.result_replace_index)
        self.assertIsNotNone(dlg.result_pattern)

    def test_cancelling_the_save_choice_saves_nothing(self):
        self.mapper.add_path_pattern(PathPattern(template=self.MATCHING_TEMPLATE))
        dlg = PathPatternBuilderDialog(self.mapper, self.item)

        with patch.object(PathPatternBuilderDialog, "_ask_save_mode", return_value=None):
            dlg._on_accept()
        self.assertIsNone(dlg.result_pattern)

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
        from square_core.paths.path_pattern import PathPattern
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


class TestDefaultsForUntaggedFields(unittest.TestCase):
    """
    Feature: media_type (or any canonical field) that never appears anywhere
    in the delivery's own path can be given a fixed default instead, so the
    saved pattern's matches all carry that value without needing a folder or
    filename piece to tag.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for frame in range(1001, 1004):
            (d / f"plate.{frame}.exr").write_text("x")
        self.mapper = FolderMapper(self.tmp)
        self.item = PlateScanner(self.tmp).scan()[0]

    def _tag_sequence_and_shot(self, dlg):
        dlg._segment_rows[0].all_chips()[0].setChecked(True)
        dlg._tag_selected("sequence")
        dlg._segment_rows[1].all_chips()[0].setChecked(True)
        dlg._tag_selected("shot")

    def test_default_is_ignored_until_typed(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self._tag_sequence_and_shot(dlg)
        self.assertEqual(dlg._current_defaults(dlg._current_template()), {})

    def test_typing_a_default_for_an_untagged_field_is_picked_up(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self._tag_sequence_and_shot(dlg)   # media_type is never tagged -- not part of this path at all
        dlg._default_edits["media_type"].setText("Plate")
        self.assertEqual(dlg._current_defaults(dlg._current_template()), {"media_type": "Plate"})

    def test_default_field_is_disabled_once_that_field_is_tagged_in_the_path(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self.assertTrue(dlg._default_edits["sequence"].isEnabled())
        self._tag_sequence_and_shot(dlg)
        self.assertFalse(dlg._default_edits["sequence"].isEnabled())
        self.assertFalse(dlg._default_edits["shot"].isEnabled())
        self.assertTrue(dlg._default_edits["media_type"].isEnabled())

    def test_saved_pattern_applies_the_default_on_match(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self._tag_sequence_and_shot(dlg)
        dlg._default_edits["media_type"].setText("Plate")

        with patch.object(PathPatternBuilderDialog, "_ask_save_mode", return_value="new"), \
             patch.object(QtWidgets.QInputDialog, "getText", return_value=("Vendor", True)):
            dlg._on_accept()

        self.assertEqual(dlg.result_pattern.defaults, {"media_type": "Plate"})
        rel = self.mapper._relative_posix(Path(self.item.files[0]))
        result = dlg.result_pattern.match(rel)
        self.assertEqual(result["sequence"], "SQ010")
        self.assertEqual(result["media_type"], "Plate")

        # and it round-trips through build_items() into the actual IngestItem
        self.mapper.add_path_pattern(dlg.result_pattern)
        items = self.mapper.build_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].media_type, "Plate")

    def test_metadata_default_fields_are_offered_and_never_disabled(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self.assertEqual(set(dlg._metadata_default_edits), {"fps", "resolution", "colorspace"})
        self._tag_sequence_and_shot(dlg)   # tagging path fields must not disable these
        for edit in dlg._metadata_default_edits.values():
            self.assertTrue(edit.isEnabled())

    def test_metadata_default_applies_unconditionally_alongside_path_defaults(self):
        dlg = PathPatternBuilderDialog(self.mapper, self.item)
        self._tag_sequence_and_shot(dlg)
        dlg._default_edits["media_type"].setText("Plate")
        dlg._metadata_default_edits["fps"].setText("24")
        dlg._metadata_default_edits["colorspace"].setText("ACEScg")

        defaults = dlg._current_defaults(dlg._current_template())
        self.assertEqual(defaults, {"media_type": "Plate", "fps": "24", "colorspace": "ACEScg"})

        with patch.object(PathPatternBuilderDialog, "_ask_save_mode", return_value="new"), \
             patch.object(QtWidgets.QInputDialog, "getText", return_value=("Vendor", True)):
            dlg._on_accept()

        self.mapper.add_path_pattern(dlg.result_pattern)
        item = self.mapper.build_items()[0]
        self.assertEqual(item.fps, 24.0)
        self.assertEqual(item.colorspace, "ACEScg")


class TestPathPatternManagerDialog(unittest.TestCase):
    """
    Confirmed bug: every mutating action (Move Up/Down, Edit Text, Remove)
    called `self.mapper.save()` -- a method FolderMapper doesn't have (it's
    in-memory only; that sidecar-file era ended when the ingest session file
    took over). Every one of those buttons raised AttributeError. Also,
    "Edit Text..." rebuilt the pattern from scratch and dropped its Defaults
    for Fields Not in the Path in the process.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "plate.1001.exr").write_text("x")
        (self.tmp / "SQ020" / "SH0200").mkdir(parents=True)
        (self.tmp / "SQ020" / "SH0200" / "plate.1001.exr").write_text("x")
        self.mapper = FolderMapper(self.tmp)
        self.mapper.add_path_pattern(PathPattern(
            template="<sequence>/<shot>/plate.####.exr", name="A",
            defaults={"media_type": "Plate"}))
        self.mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/other.####.exr", name="B"))

    def test_move_up_does_not_crash_and_marks_changed(self):
        dlg = PathPatternManagerDialog(self.mapper)
        dlg.table.selectRow(1)
        dlg._on_move_up()   # used to raise AttributeError: 'FolderMapper' object has no attribute 'save'
        self.assertTrue(dlg.changed)
        self.assertEqual(self.mapper.get_path_patterns()[0].name, "B")

    def test_move_down_does_not_crash_and_marks_changed(self):
        dlg = PathPatternManagerDialog(self.mapper)
        dlg.table.selectRow(0)
        dlg._on_move_down()
        self.assertTrue(dlg.changed)
        self.assertEqual(self.mapper.get_path_patterns()[0].name, "B")

    def test_remove_does_not_crash_and_marks_changed(self):
        dlg = PathPatternManagerDialog(self.mapper)
        dlg.table.selectRow(1)
        dlg._on_remove()
        self.assertTrue(dlg.changed)
        self.assertEqual(len(self.mapper.get_path_patterns()), 1)

    def test_edit_does_not_crash_and_preserves_defaults(self):
        dlg = PathPatternManagerDialog(self.mapper)
        dlg.table.selectRow(0)
        with patch.object(QtWidgets.QInputDialog, "getText",
                          return_value=("<sequence>/<shot>/renamed.####.exr", True)):
            dlg._on_edit()
        self.assertTrue(dlg.changed)
        edited = self.mapper.get_path_patterns()[0]
        self.assertEqual(edited.template, "<sequence>/<shot>/renamed.####.exr")
        self.assertEqual(edited.defaults, {"media_type": "Plate"})   # not dropped

    def test_closing_without_changes_leaves_changed_false(self):
        dlg = PathPatternManagerDialog(self.mapper)
        self.assertFalse(dlg.changed)


if __name__ == "__main__":
    unittest.main()
