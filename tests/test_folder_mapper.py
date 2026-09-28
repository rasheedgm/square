import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.ingest_tool.core.folder_mapper import FolderMapper, parse_fps, parse_resolution
from square_core.paths.path_pattern import PathPattern


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


class TestPatternMetadataDefaults(unittest.TestCase):
    """
    A Path Pattern's `defaults` can also cover fps/resolution/colorspace --
    a fallback for a delivery whose files never carry that metadata, exactly
    like a media_type default covers a field that's never part of the path.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "SQ010" / "SH0100").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "plate.1001.exr").write_text("x")

    def test_metadata_defaults_land_on_the_scanned_item_not_extra_tags(self):
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(
            template="<sequence>/<shot>/plate.####.exr",
            defaults={"fps": "24", "colorspace": "ACEScg"},
        ))
        item = mapper.build_items()[0]
        self.assertEqual(item.fps, 24.0)
        self.assertEqual(item.colorspace, "ACEScg")
        self.assertEqual(item.extra_tags, {})   # not lumped in as a custom tag
        self.assertEqual(item.metadata_defaulted, {"fps", "colorspace"})

    def test_resolution_default_sets_a_string_verbatim(self):
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(
            template="<sequence>/<shot>/plate.####.exr",
            defaults={"resolution": "2048x1152"},
        ))
        item = mapper.build_items()[0]
        self.assertEqual(item.resolution, "2048x1152")

    def test_an_unparseable_fps_default_is_dropped_not_crashed(self):
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(
            template="<sequence>/<shot>/plate.####.exr",
            defaults={"fps": "not-a-number"},
        ))
        item = mapper.build_items()[0]
        self.assertNotIn("fps", item.metadata_defaulted)


class TestTaggedOnlyLoading(unittest.TestCase):
    """A bare Load (nothing picked in the tree) used to bring in every file
    under the root -- untagged ones too."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for shot, stem in (("SH0100", "plate"), ("SH0200", "other")):
            d = self.tmp / "SQ010" / shot
            d.mkdir(parents=True)
            for f in range(1001, 1004):
                (d / f"{stem}.{f}.exr").write_text("x")
        self.mapper = FolderMapper(self.tmp)
        self.mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))

    def test_tagged_only_keeps_just_what_a_pattern_matched(self):
        items = self.mapper.build_items(tagged_only=True)
        self.assertEqual([i.shot_code for i in items], ["SH0100"])

    def test_without_it_everything_is_still_returned(self):
        self.assertEqual(len(self.mapper.build_items()), 2)

    def test_a_manual_media_type_counts_as_tagged(self):
        other = next(i for i in self.mapper.build_items() if "other" in i.files[0])
        self.mapper.set_media_type(other.files[0], "Plate")
        items = self.mapper.build_items(tagged_only=True)
        self.assertEqual(len(items), 2)

    def test_an_explicit_selection_can_still_pull_in_an_untagged_item(self):
        other = next(i for i in self.mapper.build_items() if "other" in i.files[0])
        picked = {os.path.normcase(os.path.abspath(f)) for f in other.files}
        items = self.mapper.build_items(filter_paths=picked)          # tagged_only off
        self.assertEqual(len(items), 1)
        self.assertIn("other", items[0].files[0])


class TestExplicitPathsWithTaggedOnly(unittest.TestCase):
    """A folder in the tree means what is tagged under it; a row picked by
    hand always loads."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for shot, stem in (("SH0100", "plate"), ("SH0100", "other"), ("SH0200", "plate")):
            d = self.tmp / "SQ010" / shot
            d.mkdir(parents=True, exist_ok=True)
            for f in range(1001, 1004):
                (d / f"{stem}.{f}.exr").write_text("x")
        self.mapper = FolderMapper(self.tmp)
        self.mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/plate.####.exr"))

    def _norm(self, *parts):
        return os.path.normcase(os.path.abspath(str(self.tmp.joinpath(*parts))))

    def test_a_folder_loads_only_the_tagged_items_under_it(self):
        folder = {self._norm("SQ010", "SH0100")}
        items = self.mapper.build_items(filter_paths=folder, tagged_only=True)
        self.assertEqual([(i.shot_code, "plate" in i.files[0]) for i in items],
                         [("SH0100", True)])

    def test_a_parent_folder_loads_every_tagged_item_below_it(self):
        with_children = {self._norm("SQ010"), self._norm("SQ010", "SH0100"),
                         self._norm("SQ010", "SH0200")}
        items = self.mapper.build_items(filter_paths=with_children, tagged_only=True)
        self.assertEqual(sorted(i.shot_code for i in items), ["SH0100", "SH0200"])

    def test_an_explicitly_picked_untagged_row_still_loads_beside_the_folder(self):
        other_files = {self._norm("SQ010", "SH0100", f"other.{f}.exr") for f in (1001, 1002, 1003)}
        picked = {self._norm("SQ010", "SH0200")} | other_files
        items = self.mapper.build_items(filter_paths=picked, tagged_only=True,
                                        explicit_paths=other_files)
        self.assertEqual(sorted("other" in i.files[0] for i in items), [False, True])


class TestNoExtensionLimit(unittest.TestCase):
    """The scanner used to drop anything that was not an image or a video."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        d = self.tmp / "SQ010" / "SH0100"
        d.mkdir(parents=True)
        for name in ("grade.cdl", "notes.txt", "plate.1001.exr", "plate.1002.exr",
                     "lens.1001.xyz", "lens.1002.xyz", "lens.1003.xyz", "look_001.lut",
                     ".DS_Store", "Thumbs.db"):
            (d / name).write_text("x")
        self.d = d

    def _by_name(self):
        from square_core.media.scanner import PlateScanner
        return {i.name: i for i in PlateScanner(self.tmp).scan()}

    def test_a_cdl_and_other_odd_files_come_through_as_items(self):
        items = self._by_name()
        self.assertEqual(items["grade.cdl"].files, [str(self.d / "grade.cdl")])
        self.assertIn("notes.txt", items)
        self.assertFalse(items["grade.cdl"].is_video)

    def test_numbered_files_of_an_unknown_type_form_a_sequence(self):
        items = self._by_name()
        self.assertEqual(len(items["lens"].files), 3)
        self.assertEqual(items["lens"].ext, ".xyz")
        self.assertEqual((items["lens"].start_frame, items["lens"].end_frame), (1001, 1003))

    def test_a_lone_numbered_file_of_an_unknown_type_is_just_a_file(self):
        items = self._by_name()
        self.assertIn("look_001.lut", items)
        self.assertEqual(len(items["look_001.lut"].files), 1)

    def test_hidden_files_and_thumbnail_caches_are_ignored(self):
        names = set(self._by_name())
        self.assertNotIn(".DS_Store", names)
        self.assertNotIn("Thumbs.db", names)

    def test_images_still_group_as_before(self):
        self.assertEqual(len(self._by_name()["plate"].files), 2)

    def test_a_pattern_can_tag_a_cdl(self):
        from tools.ingest_tool.core.folder_mapper import FolderMapper
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template="<sequence>/<shot>/grade.cdl"))
        items = mapper.build_items(tagged_only=True)
        self.assertEqual([(i.shot_code, i.name) for i in items], [("SH0100", "grade.cdl")])


class TestMediaInfoFromThePath(unittest.TestCase):
    """fps / resolution / colorspace can be read from a folder or filename."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _build(self, rel_dir, filename, template, defaults=None):
        d = self.tmp / rel_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text("x")
        (d / filename.replace("1001", "1002")).write_text("x")
        mapper = FolderMapper(self.tmp)
        mapper.add_path_pattern(PathPattern(template=template, defaults=defaults or {}))
        [item] = mapper.build_items(tagged_only=True)
        return item

    def test_all_three_from_folders_and_filename(self):
        item = self._build("SQ010/SH0100/ACEScg/2048x1152", "plate_25fps.1001.exr",
                           "<sequence>/<shot>/<colorspace>/<resolution>/plate_<fps>fps.####.exr")
        self.assertEqual((item.colorspace, item.resolution, item.fps), ("ACEScg", "2048x1152", 25.0))
        self.assertEqual(item.metadata_defaulted, {"colorspace", "resolution", "fps"})

    def test_values_are_normalised(self):
        self.assertEqual(parse_fps("25fps"), 25.0)
        self.assertEqual(parse_fps("23,976"), 23.976)
        self.assertEqual(parse_fps("fps"), None)
        self.assertEqual(parse_resolution("2048X1152"), "2048x1152")
        self.assertEqual(parse_resolution("4448_3096"), "4448x3096")
        self.assertEqual(parse_resolution("2048 x 1152"), "2048x1152")
        self.assertEqual(parse_resolution("UHD"), None)

    def test_a_value_that_cannot_be_read_stays_visible_as_a_plain_tag(self):
        item = self._build("SQ010/SH0100/UHD", "plate.1001.exr",
                           "<sequence>/<shot>/<resolution>/plate.####.exr")
        self.assertEqual(item.extra_tags.get("resolution"), "UHD")
        self.assertNotIn("resolution", item.metadata_defaulted)

    def test_a_path_tag_wins_over_the_typed_default(self):
        item = self._build("SQ010/SH0100/24", "plate.1001.exr",
                           "<sequence>/<shot>/<fps>/plate.####.exr", defaults={"fps": "30"})
        self.assertEqual(item.fps, 24.0)

    def test_the_typed_default_applies_when_the_path_does_not_carry_it(self):
        item = self._build("SQ010/SH0100", "plate.1001.exr",
                           "<sequence>/<shot>/plate.####.exr", defaults={"fps": "30"})
        self.assertEqual(item.fps, 30.0)

    def test_the_files_own_metadata_still_wins_over_the_path(self):
        from tools.ingest_tool.core.item import IngestItem
        item = self._build("SQ010/SH0100/25", "plate.1001.exr",
                           "<sequence>/<shot>/<fps>/plate.####.exr")
        ingest_item = IngestItem.from_scan_item(item)
        self.assertEqual(ingest_item.fps, 25.0)

        class _Probe:
            @staticmethod
            def probe(path):
                return ({"fps": 30.0, "resolution": "1920x1080", "colorspace": "sRGB"}, "fake")

        ingest_item.probe_metadata(_Probe)
        self.assertEqual((ingest_item.fps, ingest_item.resolution), (30.0, "1920x1080"))


if __name__ == "__main__":
    unittest.main()
