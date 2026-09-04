import tempfile
import unittest
from pathlib import Path

from square_core.paths.path_pattern import (
    PathPattern, match_first, split_canonical_and_extra,
    seed_filename_segment, render_placeholder, is_frame_piece_text,
    explode_segment_template,
)
from square_core.media.scanner import IngestSequenceItem


class TestPathPatternMatching(unittest.TestCase):
    """
    Core by-example engine: a template string built from one real path,
    matched whole-segment-by-segment against every other candidate path.
    """

    def test_placeholders_extract_by_position_across_segments(self):
        pattern = PathPattern(template="<sequence>/<shot>/<media_type>/<sequence>_<shot>_<media_name>.####.<extension>")
        extracted = pattern.match("SEQ01/SHOT010/Plate/SEQ01_SHOT010_BG.1001.exr")
        self.assertEqual(extracted["sequence"], "SEQ01")
        self.assertEqual(extracted["shot"], "SHOT010")
        self.assertEqual(extracted["media_type"], "Plate")
        self.assertEqual(extracted["media_name"], "BG")
        self.assertEqual(extracted["extension"], "exr")

    def test_repeated_placeholder_last_occurrence_wins(self):
        # "sequence" is tagged both on its own folder and again inside the
        # filename; when a real file's two copies disagree, the deeper
        # (filename) occurrence -- matched later -- is the one kept.
        pattern = PathPattern(template="<sequence>/<sequence>_plate.exr")
        extracted = pattern.match("SQ010/SQ099_plate.exr")
        self.assertEqual(extracted["sequence"], "SQ099")

    def test_no_prefix_no_invented_prefix(self):
        # Bare numeric codes with no SQ/SH prefix at all -- captured exactly
        # as-is, since the engine never reformats a captured value.
        pattern = PathPattern(template="<sequence>/<shot>/<media_name>.####.<extension>")
        extracted = pattern.match("01/010/01_010_BG.1001.exr")
        self.assertEqual(extracted["sequence"], "01")
        self.assertEqual(extracted["shot"], "010")

    def test_literal_text_must_match_exactly_by_default(self):
        # A segment left untouched (no placeholder, no wildcard) is literal:
        # it must match byte-for-byte, so a same-prefix sibling with extra
        # trailing text does NOT also match -- this is the direct fix for
        # the earlier "sh10 also matches sh10_ref/sh10_edl" false positive.
        pattern = PathPattern(template="sh10/media.exr")
        self.assertIsNotNone(pattern.match("sh10/media.exr"))
        self.assertIsNone(pattern.match("sh10_ref/media.exr"))
        self.assertIsNone(pattern.match("sh10_edl/media.exr"))

    def test_explicit_wildcard_matches_anything_but_captures_nothing(self):
        pattern = PathPattern(template="<shot>/*/plate.exr")
        extracted = pattern.match("SH0100/v001/plate.exr")
        self.assertEqual(extracted, {"shot": "SH0100"})
        extracted2 = pattern.match("SH0100/anything_at_all/plate.exr")
        self.assertEqual(extracted2, {"shot": "SH0100"})

    def test_frame_run_matches_variable_digit_counts(self):
        pattern = PathPattern(template="plate.####.exr")
        self.assertIsNotNone(pattern.match("plate.1001.exr"))
        self.assertIsNotNone(pattern.match("plate.100001.exr"))
        self.assertIsNone(pattern.match("plate.exr"))

    def test_segment_count_must_match_exactly(self):
        # A pattern built from one shape must not silently match a shorter
        # or longer path -- a delivery with a different shape needs its own
        # saved pattern instead.
        pattern = PathPattern(template="<sequence>/<shot>/<media_name>/plate.####.exr")
        self.assertIsNone(pattern.match("SQ010/SH0100/plate.1001.exr"))
        self.assertIsNone(pattern.match("SQ010/SH0100/BG/extra/plate.1001.exr"))

    def test_custom_open_tag_and_canonical_split(self):
        pattern = PathPattern(template="<sequence>/<shot>/camera/<camera>/<date>/source")
        extracted = pattern.match("SHOW01/020/camera/A_CAM/2026_08_12/source")
        canonical, extra = split_canonical_and_extra(extracted)
        self.assertEqual(canonical, {"sequence_code": "SHOW01", "shot_code": "020"})
        self.assertEqual(extra, {"camera": "A_CAM", "date": "2026_08_12"})

    def test_extension_tag_is_recognized_but_discarded(self):
        # <extension> is harmless to tag (the studio's own worked example did)
        # but the real file's extension is already known from the scanned
        # file itself -- it should never show up as a redundant extra tag.
        pattern = PathPattern(template="<shot>/plate.####.<extension>")
        extracted = pattern.match("SH0100/plate.1001.exr")
        canonical, extra = split_canonical_and_extra(extracted)
        self.assertEqual(canonical, {"shot_code": "SH0100"})
        self.assertEqual(extra, {})

    def test_case_insensitive_literal_match(self):
        pattern = PathPattern(template="<shot>/Plate/media.exr")
        self.assertIsNotNone(pattern.match("SH0100/PLATE/MEDIA.EXR"))

    def test_match_first_tries_patterns_in_order(self):
        p1 = PathPattern(template="<sequence>/<shot>/<media_name>/plate.####.exr")
        p2 = PathPattern(template="<sequence>/<shot>/plate.####.exr")  # a shorter, second shape
        matched_pattern, extracted = match_first([p1, p2], "SQ020/SH0200/plate.1001.exr")
        self.assertIs(matched_pattern, p2)
        self.assertEqual(extracted["shot"], "SH0200")

        matched_pattern2, extracted2 = match_first([p1, p2], "not/matching/anything")
        self.assertIsNone(matched_pattern2)
        self.assertIsNone(extracted2)

    def test_render_placeholder_and_frame_detection_helpers(self):
        self.assertEqual(render_placeholder("sequence"), "<sequence>")
        self.assertTrue(is_frame_piece_text("####"))
        self.assertTrue(is_frame_piece_text("##"))
        self.assertFalse(is_frame_piece_text("plate"))


class TestExplodeSegmentTemplate(unittest.TestCase):
    """
    Reversing a saved template back into chip state, so reopening the
    builder on an already-tagged file shows that file's tagging as it was
    originally made rather than a blank slate.
    """

    def test_whole_segment_placeholder(self):
        chips, seps, roles = explode_segment_template("<sequence>", "SQ010")
        self.assertEqual(chips, ["SQ010"])
        self.assertEqual(seps, [])
        self.assertEqual(roles, ["sequence"])

    def test_combined_filename_with_frame_and_extension(self):
        chips, seps, roles = explode_segment_template(
            "<sequence>_<shot>_<media_name>.####.<extension>", "SQ010_SH0100_BG.1001.exr"
        )
        self.assertEqual(chips, ["SQ010", "SH0100", "BG", "####", "exr"])
        self.assertEqual(seps, ["_", "_", ".", "."])
        self.assertEqual(roles, ["sequence", "shot", "media_name", None, "extension"])

    def test_placeholder_name_containing_the_delimiter_stays_atomic(self):
        # "<media_name>" must not be split in half on its own underscore --
        # the reason reconstruction is driven by the template rather than by
        # re-tokenizing the text.
        chips, _seps, roles = explode_segment_template("<media_name>.mov", "BG_MAIN.mov")
        self.assertEqual(chips, ["BG_MAIN", "mov"])
        self.assertEqual(roles, ["media_name", None])

    def test_wildcard_chip_keeps_the_text_it_ignores(self):
        chips, _seps, roles = explode_segment_template("*", "v001")
        self.assertEqual(chips, ["v001"])
        self.assertEqual(roles, ["*"])

    def test_literal_prefix_glued_to_a_placeholder(self):
        chips, seps, roles = explode_segment_template("<shot>_v<version>", "SH0100_v003")
        self.assertEqual(chips, ["SH0100", "v", "003"])
        self.assertEqual(seps, ["_", ""])
        self.assertEqual(roles, ["shot", None, "version"])

    def test_returns_none_when_the_template_does_not_match_the_text(self):
        self.assertIsNone(explode_segment_template("<shot>_plate.exr", "totally_different.mov"))

    def test_round_trips_back_to_the_original_template(self):
        for template, real in [
            ("<sequence>_<shot>_<media_name>.####.<extension>", "SQ010_SH0100_BG.1001.exr"),
            ("<media_type>_*_<media_name>.mov", "PLATE_junk_BG.mov"),
            ("plate.####.exr", "plate.1001.exr"),
            ("vendor_drop", "vendor_drop"),
        ]:
            chips, seps, roles = explode_segment_template(template, real)
            rendered = []
            for i, (text, role) in enumerate(zip(chips, roles)):
                if i:
                    rendered.append(seps[i - 1])
                if role == "*":
                    rendered.append("*")
                elif role:
                    rendered.append(f"<{role}>")
                else:
                    rendered.append(text)
            self.assertEqual("".join(rendered), template)


class TestSeedFilenameSegment(unittest.TestCase):
    """The human-readable seed string the builder dialog shows for a leaf item's own filename piece."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_sequence_frame_digits_become_a_hash_run(self):
        files = []
        for i in range(1001, 1004):
            f = self.tmp / f"ALPHA_SQ010_SH0100_PL01.{i}.exr"
            f.write_text("x")
            files.append(str(f))
        item = IngestSequenceItem("ALPHA_SQ010_SH0100_PL01", files, ".exr", is_video=False)
        self.assertEqual(seed_filename_segment(item), "ALPHA_SQ010_SH0100_PL01.####.exr")

    def test_bare_numbered_sequence_uses_real_basename(self):
        f = self.tmp / "1001.exr"
        f.write_text("x")
        item = IngestSequenceItem(self.tmp.name, [str(f)], ".exr", is_video=False)
        self.assertEqual(seed_filename_segment(item), "####.exr")

    def test_video_keeps_real_filename_untouched(self):
        f = self.tmp / "final_final2.mov"
        f.write_text("x")
        item = IngestSequenceItem("final_final2.mov", [str(f)], ".mov", is_video=True)
        self.assertEqual(seed_filename_segment(item), "final_final2.mov")


class TestPatternDefaults(unittest.TestCase):
    """A pattern can declare a default for a token it never captures at all --
    e.g. every delivery under this root is a Plate even though the folder
    structure has no <media_type> segment to tag it from."""

    def test_default_fills_a_token_the_template_never_captures(self):
        pattern = PathPattern(template="<sequence>/<shot>/<media_name>.####.<extension>",
                              defaults={"media_type": "Plate"})
        extracted = pattern.match("SQ010/SH0100/comp.1001.exr")
        self.assertEqual(extracted["media_type"], "Plate")
        self.assertEqual(extracted["sequence"], "SQ010")

    def test_default_does_not_override_a_captured_value(self):
        pattern = PathPattern(
            template="<sequence>/<shot>/<media_type>/<media_name>.####.<extension>",
            defaults={"media_type": "Plate"})
        extracted = pattern.match("SQ010/SH0100/Ref/comp.1001.exr")
        self.assertEqual(extracted["media_type"], "Ref")

    def test_defaults_round_trip_through_to_dict_from_dict(self):
        pattern = PathPattern(template="<shot>/<media_name>.####.<extension>",
                              defaults={"media_type": "Plate", "sequence": "SQ010"})
        restored = PathPattern.from_dict(pattern.to_dict())
        self.assertEqual(restored.defaults, {"media_type": "Plate", "sequence": "SQ010"})

    def test_no_defaults_key_when_none_set(self):
        self.assertNotIn("defaults", PathPattern(template="<shot>/x").to_dict())

    def test_plain_string_or_dict_without_defaults_still_loads(self):
        self.assertEqual(PathPattern.from_dict("<shot>/x").defaults, {})
        self.assertEqual(PathPattern.from_dict({"template": "<shot>/x"}).defaults, {})


if __name__ == "__main__":
    unittest.main()
