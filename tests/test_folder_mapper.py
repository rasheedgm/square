import shutil
import tempfile
import unittest
from pathlib import Path

from square_core.folder_mapper import FolderMapper
from square_core.path_pattern import PathPattern


class TestFolderMapperPathPatterns(unittest.TestCase):
    """
    FolderMapper applies its ordered Path Pattern list to every item
    PlateScanner discovers -- first pattern to match an item's own real
    path wins, so a delivery with more than one shape just needs a second
    saved pattern rather than one template trying to describe every shape.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_build_items_applies_canonical_fields_and_extra_tags(self):
        (self.tmp / "SQ010" / "SH0100" / "camera" / "A_CAM").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "camera" / "A_CAM" / "plate.1001.exr").write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/camera/<camera>/plate.####.exr"))

        items = mapper.build_items()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.sequence_code, "SQ010")
        self.assertEqual(item.shot_code, "SH0100")
        self.assertEqual(item.extra_tags, {"camera": "A_CAM"})

    def test_no_invented_prefix_captured_value_used_verbatim(self):
        # Bare numeric codes, no SQ/SH prefix anywhere -- and letters mixed
        # into the shot code -- must survive completely unmodified.
        (self.tmp / "01" / "gfg_010_a").mkdir(parents=True)
        (self.tmp / "01" / "gfg_010_a" / "plate.1001.exr").write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))

        items = mapper.build_items()
        self.assertEqual(items[0].sequence_code, "01")
        self.assertEqual(items[0].shot_code, "gfg_010_a")

    def test_sibling_ref_folder_is_not_confused_with_the_real_shot(self):
        # "sh10_ref" sits next to "sh10" for an unrelated reason (reference
        # material named after the shot it belongs to). A pattern built from
        # "sh10" must capture a DIFFERENT shot value for "sh10_ref", never
        # collide with "sh10" itself the way an unanchored regex guess used to.
        (self.tmp / "sh10").mkdir(parents=True)
        (self.tmp / "sh10" / "plate.1001.exr").write_text("x")
        (self.tmp / "sh10_ref").mkdir(parents=True)
        (self.tmp / "sh10_ref" / "plate.1001.exr").write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<shot>/plate.####.exr"))

        items = mapper.build_items()
        by_shot = {i.shot_code: i for i in items}
        self.assertEqual(set(by_shot.keys()), {"sh10", "sh10_ref"})

    def test_literal_segment_does_not_match_sibling_with_extra_suffix(self):
        # The other direction of the same fix: a literal folder name in the
        # template ("sh10", left untagged) must match only that exact
        # folder, never a sibling that merely starts with the same text.
        (self.tmp / "sh10").mkdir(parents=True)
        (self.tmp / "sh10" / "plate.1001.exr").write_text("x")
        (self.tmp / "sh10_ref").mkdir(parents=True)
        (self.tmp / "sh10_ref" / "plate.1001.exr").write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="sh10/plate.####.exr"))

        _, matched_sh10 = mapper.match_relative_path(self.tmp / "sh10" / "plate.1001.exr")
        _, matched_ref = mapper.match_relative_path(self.tmp / "sh10_ref" / "plate.1001.exr")
        self.assertEqual(matched_sh10, {})   # matches, captures nothing (no placeholder)
        self.assertIsNone(matched_ref)       # literal "sh10" != "sh10_ref" -- no match at all

    def test_ordered_pattern_list_first_match_wins_across_mixed_shapes(self):
        # SQ010 is a normal 2-level delivery; SQ020 has an extra vendor-added
        # nesting level -- one pattern alone can't cover both shapes.
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "plate.1001.exr").write_text("x")
        (self.tmp / "SQ020" / "vendor_drop" / "SH0200").mkdir(parents=True)
        (self.tmp / "SQ020" / "vendor_drop" / "SH0200" / "plate.1001.exr").write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.set_path_patterns([
            PathPattern(template="<sequence>/<shot>/plate.####.exr"),
            PathPattern(template="<sequence>/vendor_drop/<shot>/plate.####.exr"),
        ])

        items = mapper.build_items()
        by_seq = {i.sequence_code: i for i in items}
        self.assertEqual(by_seq["SQ010"].shot_code, "SH0100")
        self.assertEqual(by_seq["SQ020"].shot_code, "SH0200")

    def test_manual_media_type_override_wins_over_pattern(self):
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        exr = self.tmp / "SQ010" / "SH0100" / "plate.1001.exr"
        exr.write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/<media_type>.####.exr"))
        # "plate" (lowercase, from the filename) is what the pattern would
        # capture; a manual tag on the same path must win over it.
        mapper.set_media_type(exr, "BG Plate")

        items = mapper.build_items()
        self.assertEqual(items[0].media_type, "BG Plate")

    def test_media_types_dict_round_trip(self):
        # FolderMapper is in-memory only now (no hidden sidecar); the session
        # file persists this via get_media_types / set_media_types.
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        exr = self.tmp / "SQ010" / "SH0100" / "plate.1001.exr"
        exr.write_text("x")

        mapper = FolderMapper(self.tmp)
        mapper.set_media_type(exr, "Ref")
        dumped = mapper.get_media_types()

        other = FolderMapper(self.tmp)
        other.set_media_types(dumped)
        self.assertEqual(other.get_media_type(exr), "Ref")

    def test_no_sidecar_file_is_written(self):
        (self.tmp / "SQ010").mkdir(parents=True)
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<sequence>"))
        mapper.set_media_type(self.tmp / "SQ010", "Plate")
        self.assertFalse((self.tmp / ".square_ingest_map.json").exists())
        self.assertFalse(hasattr(mapper, "save"))

    def test_reordering_changes_which_pattern_wins(self):
        mapper = FolderMapper(self.tmp)
        mapper.set_path_patterns([
            PathPattern(template="a/<shot>.exr"),
            PathPattern(template="<sequence>/<shot>.exr"),
        ])
        mapper.move_path_pattern(1, 0)
        patterns = mapper.get_path_patterns()
        self.assertEqual(patterns[0].template, "<sequence>/<shot>.exr")
        self.assertEqual(patterns[1].template, "a/<shot>.exr")

    def test_clear_all_removes_patterns_and_tags(self):
        (self.tmp / "SQ010").mkdir(parents=True)
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<sequence>"))
        mapper.set_media_type(self.tmp / "SQ010", "Plate")
        self.assertTrue(mapper.has_map())

        mapper.clear_all()
        self.assertFalse(mapper.has_map())

    def test_flat_delivery_with_no_subfolders_matches_correctly(self):
        # Confirmed bug: a file sitting directly in the browsed root (no
        # subfolders at all -- the flat MOV-delivery shape) produced a
        # relative path of "." instead of "", corrupting the seed segments
        # a Path Pattern is built from.
        (self.tmp / "SEQ010_SHOT0010_PLATE_BG.mov").write_text("x")

        mapper = FolderMapper(self.tmp)
        self.assertEqual(mapper._relative_posix(self.tmp), "")

        mapper.add_path_pattern(
            PathPattern(template="<sequence>_<shot>_<media_type>_<media_name>.<extension>")
        )
        items = mapper.build_items()
        self.assertEqual(items[0].sequence_code, "SEQ010")
        self.assertEqual(items[0].shot_code, "SHOT0010")
        self.assertEqual(items[0].media_type, "PLATE")
        self.assertEqual(items[0].media_name, "BG")

    def test_preview_pattern_reports_match_count_and_samples(self):
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "plate.1001.exr").write_text("x")
        (self.tmp / "SQ010" / "SH0100_ref").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100_ref" / "clip.mov").write_text("x")

        mapper = FolderMapper(self.tmp)
        count, total, samples = mapper.preview_pattern("<sequence>/<shot>/plate.####.exr")
        self.assertEqual(count, 1)
        self.assertEqual(total, 2)
        matched_rels = {rel for rel, extracted in samples if extracted is not None}
        self.assertEqual(matched_rels, {"SQ010/SH0100/plate.1001.exr"})


if __name__ == "__main__":
    unittest.main()
