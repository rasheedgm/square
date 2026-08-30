import tempfile
import unittest
from pathlib import Path

from square_core.path_pattern import (
    PathPattern, match_first, split_canonical_and_extra,
    seed_filename_segment, render_placeholder, is_frame_piece_text,
)
from square_core.plate_scanner import IngestSequenceItem


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


if __name__ == "__main__":
    unittest.main()
