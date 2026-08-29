import shutil
import tempfile
import unittest
from pathlib import Path

from square_core.folder_mapper import (
    FolderMapper, PatternRule, _detect_level,
    LEVEL_SEQ, LEVEL_SHOT, LEVEL_MEDIA_NAME, LEVEL_MEDIA_TYPE, LEVEL_VERSION,
)
from square_core.token_parser import TokenRule, normalise_code, parse_string_with_token_rule


class TestFolderMapperCrashFixes(unittest.TestCase):
    """
    Confirmed crash bugs: set_level()/set_level_for_folder() rejected
    LEVEL_MEDIA_TYPE/LEVEL_VERSION with a ValueError even though the folder
    tree's own right-click menu offers exactly those two options, and
    build_items() had a typo (item.plate_name = item.media_n) that raised
    AttributeError whenever a Media Name depth-tag was actually used.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "SQ010" / "SH0100" / "PL01").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "PL01" / "shot.1001.exr").write_text("x")

    def test_set_level_accepts_media_type_and_version(self):
        mapper = FolderMapper(self.tmp)
        mapper.set_level(1, LEVEL_MEDIA_TYPE)  # must not raise
        mapper.set_level(2, LEVEL_VERSION)      # must not raise
        self.assertEqual(mapper.get_level(1), LEVEL_MEDIA_TYPE)
        self.assertEqual(mapper.get_level(2), LEVEL_VERSION)

    def test_set_level_for_folder_accepts_media_type_and_version(self):
        mapper = FolderMapper(self.tmp)
        mapper.set_level_for_folder(self.tmp / "SQ010", LEVEL_MEDIA_TYPE)  # must not raise
        self.assertEqual(mapper.get_level_for_folder(self.tmp / "SQ010"), LEVEL_MEDIA_TYPE)

    def test_build_items_with_media_name_depth_tag_does_not_crash(self):
        mapper = FolderMapper(self.tmp)
        mapper.set_level(1, "seq")
        mapper.set_level(2, "shot")
        mapper.set_level(3, LEVEL_MEDIA_NAME)
        items = mapper.build_items()  # previously: AttributeError on item.media_n
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].sequence_code, "SQ010")
        self.assertEqual(items[0].shot_code, "SH0100")
        self.assertEqual(items[0].media_name, "PL01")

    def test_ingest_sequence_item_has_default_version(self):
        from square_core.plate_scanner import IngestSequenceItem
        item = IngestSequenceItem("x", [], ".exr")
        self.assertEqual(item.version, 1)  # previously: no default at all, AttributeError


class TestFolderMapperPatternRules(unittest.TestCase):
    """
    New capability: tag anything matching a pattern anywhere in the tree,
    independent of depth -- covers irregular/mixed-depth deliveries that
    depth-only tagging can never handle.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # SQ010 is a normal 2-level delivery; SQ020 has an extra vendor-added
        # nesting level, so a depth-based rule alone could never tag both.
        (self.tmp / "SQ010" / "SH0100" / "PL01").mkdir(parents=True)
        (self.tmp / "SQ010" / "SH0100" / "PL01" / "a.1001.exr").write_text("x")
        (self.tmp / "SQ020" / "vendor_drop" / "extra" / "SH0200" / "PL02").mkdir(parents=True)
        (self.tmp / "SQ020" / "vendor_drop" / "extra" / "SH0200" / "PL02" / "a.1001.exr").write_text("x")

    def test_level_pattern_rule_handles_mixed_depths(self):
        mapper = FolderMapper(self.tmp)
        seq_rule = PatternRule(name="seq", pattern=r"(?i)^SQ\d{2,4}$", is_regex=True,
                                target="folder", action="level", level=LEVEL_SEQ)
        shot_rule = PatternRule(name="shot", pattern=r"(?i)^SH\d{2,4}$", is_regex=True,
                                 target="folder", action="level", level=LEVEL_SHOT)
        mapper.set_pattern_rules([seq_rule, shot_rule])

        items = mapper.build_items()
        by_seq = {i.sequence_code: i for i in items}
        self.assertEqual(by_seq["SQ010"].shot_code, "SH0100")
        self.assertEqual(by_seq["SQ020"].shot_code, "SH0200")  # only reachable via pattern, not depth

    def test_manual_folder_override_wins_over_pattern_rule(self):
        mapper = FolderMapper(self.tmp)
        rule = PatternRule(name="seq", pattern=r"(?i)^SQ\d{2,4}$", is_regex=True,
                            target="folder", action="level", level=LEVEL_SEQ)
        mapper.set_pattern_rules([rule])
        # Manually override one folder to NOT be a sequence level.
        mapper.set_level_for_folder(self.tmp / "SQ010", LEVEL_SHOT)
        self.assertEqual(mapper.level_of_path(self.tmp / "SQ010"), LEVEL_SHOT)

    def test_media_type_pattern_rule_on_filename(self):
        # match_scope="anywhere" is required here on purpose: the safer default
        # ("whole") would require the ENTIRE filename to be just "ref", which
        # "extra_ref.mov" is not -- substring matching is an explicit opt-in,
        # not the default, precisely so a bare "sh10" can't silently also
        # match "sh10_ref"/"sh10_edl" alongside the real shot folder.
        mapper = FolderMapper(self.tmp)
        rule = PatternRule(name="ref files", pattern=r"(?i)ref", is_regex=True,
                            target="file", match_scope="anywhere", action="media_type", media_type="Ref")
        mapper.set_pattern_rules([rule])
        self.assertEqual(mapper.count_pattern_matches(rule), 0)  # no "ref" filenames in this fixture yet

        (self.tmp / "SQ010" / "SH0100" / "extra_ref.mov").write_text("x")
        mapper.apply_pattern_rules()
        self.assertEqual(mapper.count_pattern_matches(rule), 1)
        self.assertEqual(mapper.get_media_type(self.tmp / "SQ010" / "SH0100" / "extra_ref.mov"), "Ref")

    def test_pattern_rules_persist_across_reload(self):
        mapper = FolderMapper(self.tmp)
        rule = PatternRule(name="seq", pattern=r"(?i)^SQ\d{2,4}$", is_regex=True,
                            target="folder", action="level", level=LEVEL_SEQ)
        mapper.set_pattern_rules([rule])
        mapper.save()

        reloaded = FolderMapper(self.tmp)
        self.assertEqual(len(reloaded.get_pattern_rules()), 1)
        self.assertEqual(reloaded.level_of_path(self.tmp / "SQ010"), LEVEL_SEQ)


class TestApplyDepthTokenPreset(unittest.TestCase):
    """
    Confirmed bug: an Ingest Preset's "token_preset" depth rule (e.g. the
    built-in "Nested Sequence + Combined File" preset) was silently ignored
    on apply -- folder_tree_widget only ever handled the "direct" rule type.
    """

    def test_applies_to_a_combined_filename_one_level_deeper_than_its_folder(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "SEQ_ALPHA").mkdir(parents=True)
        (tmp / "SEQ_ALPHA" / "SHOT0100_PL01_v001.mov").write_text("x")

        mapper = FolderMapper(tmp)
        mapper.set_level(1, LEVEL_SEQ)
        token_rule = TokenRule(
            name="Shot_Media_Version", delimiter="_",
            mapping={"shot_code": [0], "media_name": [1], "version": [2]},
        )
        mapper.apply_depth_token_preset(2, token_rule.to_dict())

        items = mapper.build_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].sequence_code, "SEQ_ALPHA")
        self.assertEqual(items[0].shot_code, "SH0100")
        self.assertEqual(items[0].media_name, "PL01")
        self.assertEqual(items[0].version, 1)


class TestNoAssumedShotSeqConvention(unittest.TestCase):
    """
    Real-world feedback: shot/seq names don't necessarily have an SH/SQ
    prefix, aren't necessarily purely numeric (letters can be mixed in, e.g.
    "gfg_010_a"), and a folder can legitimately contain "sh10" as a
    substring for an unrelated reason (a "sh10_ref"/"sh10_edl" subfolder
    holding reference material or an EDL for shot 10, not the shot folder
    itself). None of that should be hardcoded away.
    """

    def test_normalise_code_never_invents_a_prefix(self):
        # No prefix at all -- must be returned exactly as-is, not "SH0010".
        self.assertEqual(normalise_code("0010", "SH|shot|sc", "SH", 4), "0010")
        # Letters mixed in, no recognizable prefix -- preserved exactly.
        self.assertEqual(normalise_code("gfg_010_a", "SH|shot|sc", "SH", 4), "gfg_010_a")
        # A recognizable prefix IS present -- standardizing padding/case here
        # is fine, since nothing is being invented that wasn't already implied.
        self.assertEqual(normalise_code("sh100", "SH|shot|sc", "SH", 4), "SH0100")

    def test_token_rule_shot_code_role_does_not_invent_prefix_or_drop_letters(self):
        rule = TokenRule(name="r", delimiter="_", mapping={"shot_code": [0]})
        # A bare numeric chip with no prefix -- kept exactly as-is, not "SH0100".
        self.assertEqual(parse_string_with_token_rule("0100", rule)["shot_code"], "0100")
        # A single-letter-code chip ("gfg") with no digits at all -- passed through untouched.
        self.assertEqual(parse_string_with_token_rule("gfg_010_a", rule)["shot_code"], "gfg")

        # A chip spanning all three tokens, with letters and no prefix, must
        # survive completely intact -- no invented "SH", nothing dropped.
        rule2 = TokenRule(name="r2", delimiter="_", mapping={"shot_code": [0, 1, 2]})
        res = parse_string_with_token_rule("gfg_010_a", rule2)
        self.assertEqual(res["shot_code"], "gfg_010_a")

    def test_depth_map_does_not_invent_prefix_for_plain_folder_name(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "0100" / "PL01").mkdir(parents=True)
        (tmp / "0100" / "PL01" / "a.1001.exr").write_text("x")

        mapper = FolderMapper(tmp)
        mapper.set_level(1, LEVEL_SHOT)
        mapper.set_level(2, LEVEL_MEDIA_NAME)
        items = mapper.build_items()
        self.assertEqual(items[0].shot_code, "0100")  # not "SH0100"

    def test_auto_detect_does_not_flag_ref_or_edl_subfolders_as_shots(self):
        # "sh10_ref"/"sh10_edl" reference/EDL folders named after the shot they
        # belong to must NOT be mistaken for the shot folder itself.
        self.assertIsNone(_detect_level("sh10_ref"))
        self.assertIsNone(_detect_level("sh10_edl"))
        self.assertEqual(_detect_level("SH0100"), LEVEL_SHOT)
        self.assertEqual(_detect_level("sh100"), LEVEL_SHOT)

    def test_default_whole_name_pattern_rule_does_not_match_ref_and_edl_siblings(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "sh10").mkdir(parents=True)
        (tmp / "sh10_ref").mkdir(parents=True)
        (tmp / "sh10_edl").mkdir(parents=True)

        # match_scope defaults to "whole" -- this rule must ONLY match the
        # exact shot folder, not its ref/edl siblings that merely start with
        # the same "sh10" text.
        rule = PatternRule(name="shot", pattern=r"(?i)^sh\d+$", is_regex=True,
                            target="folder", action="level", level=LEVEL_SHOT)
        mapper = FolderMapper(tmp)
        mapper.set_pattern_rules([rule])

        matched_names = {name for name, _ in mapper.sample_pattern_matches(rule, limit=10)}
        self.assertEqual(matched_names, {"sh10"})

    def test_pattern_rule_capture_group_extracts_letters_with_no_invented_prefix(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "gfg_010_a" / "PL01").mkdir(parents=True)
        (tmp / "gfg_010_a" / "PL01" / "a.1001.exr").write_text("x")

        # Capture just the part after "gfg_" -- keeps the letter suffix, no
        # "SH"/"SQ" prefix invented anywhere.
        rule = PatternRule(name="shot", pattern=r"(?i)^gfg_(\d+_[a-z])$", is_regex=True,
                            target="folder", action="level", level=LEVEL_SHOT)
        mapper = FolderMapper(tmp)
        mapper.set_level(2, LEVEL_MEDIA_NAME)
        mapper.set_pattern_rules([rule])

        items = mapper.build_items()
        self.assertEqual(items[0].shot_code, "010_a")


if __name__ == "__main__":
    unittest.main()
