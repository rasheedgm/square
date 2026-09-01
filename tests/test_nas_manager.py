import os
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from square_core.nas_manager import NASManager
from square_core.plate_scanner import IngestSequenceItem
from square_core.config import SHOT_DIRECTORY_TEMPLATE, DEFAULT_MEDIA_TYPE_CONFIGS


def _stub_studio_config():
    """
    get_dest_dir() constructs a fresh StudioConfig() (reading the repo's own
    studio_config.json) on every call. Patched out in these tests so version
    detection is verified against the CODE's own current defaults, not
    whatever a real, mutable, on-disk config happens to hold.
    """
    cfg = MagicMock()
    cfg.media_type_configs = dict(DEFAULT_MEDIA_TYPE_CONFIGS)
    cfg.nas_dir_template = SHOT_DIRECTORY_TEMPLATE
    return cfg


class TestNASManagerTransferModes(unittest.TestCase):
    """
    Copying was previously always shutil.copy2, fully serial, one file at a
    time -- the thread-pool code in this module existed but nothing in the
    real ingest flow ever called it. These tests cover the reworked
    copy_sequence(): real parallel transfer, plus copy/hardlink/symlink as
    explicit modes with a safe fallback chain when a mode isn't possible.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src_dir = self.tmp / "src"
        self.src_dir.mkdir()
        self.files = []
        for i in range(1001, 1006):
            p = self.src_dir / f"shot.{i}.exr"
            p.write_text(f"frame {i}" * 50)
            self.files.append(str(p))

        self.item = IngestSequenceItem("shot", sorted(self.files), ".exr", is_video=False)
        self.item.sequence_code = "SQ010"
        self.item.shot_code = "SH0100"
        self.item.media_type = "Plate"
        self.item.media_name = "PL01"

    def test_copy_mode_produces_independent_verified_files(self):
        nas = NASManager(nas_root=self.tmp, dry_run=False, transfer_mode="copy", workers=4)
        dest = self.tmp / "dest_copy"
        copied = nas.copy_sequence(self.item, dest, version_num=1, proj_code="PROJ")

        self.assertEqual(len(copied), 5)
        for path in copied:
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.islink(path))

    def test_hardlink_mode_shares_inode(self):
        nas = NASManager(nas_root=self.tmp, dry_run=False, transfer_mode="hardlink", workers=4)
        dest = self.tmp / "dest_hardlink"
        copied = nas.copy_sequence(self.item, dest, version_num=1, proj_code="PROJ")

        src_first = sorted(self.files)[0]
        self.assertEqual(Path(copied[0]).stat().st_ino, Path(src_first).stat().st_ino)

    def test_symlink_mode_creates_real_symlink(self):
        nas = NASManager(nas_root=self.tmp, dry_run=False, transfer_mode="symlink", workers=4)
        dest = self.tmp / "dest_symlink"
        copied = nas.copy_sequence(self.item, dest, version_num=1, proj_code="PROJ")

        self.assertTrue(Path(copied[0]).is_symlink())
        self.assertEqual(Path(copied[0]).read_text(), Path(sorted(self.files)[0]).read_text())

    def test_symlink_and_hardlink_failure_falls_back_to_full_copy(self):
        """A cross-device link (or missing Windows privilege) must never abort the ingest -- just degrade."""
        nas = NASManager(nas_root=self.tmp, dry_run=False, transfer_mode="symlink")
        src = self.src_dir / "shot.1001.exr"
        dest = self.tmp / "fallback_dest.exr"

        with patch("os.symlink", side_effect=OSError("simulated EXDEV")), \
             patch("os.link", side_effect=OSError("simulated EXDEV")):
            mode_used = nas._transfer_one_file(src, dest)

        self.assertEqual(mode_used, "copy")
        self.assertTrue(dest.exists())
        self.assertFalse(dest.is_symlink())
        self.assertEqual(dest.read_text(), src.read_text())

    def test_copy_preserves_file_order_despite_parallel_completion(self):
        nas = NASManager(nas_root=self.tmp, dry_run=False, transfer_mode="copy", workers=4)
        dest = self.tmp / "dest_order"
        copied = nas.copy_sequence(self.item, dest, version_num=1, proj_code="PROJ")

        frame_numbers = [int(Path(p).stem.split(".")[-1]) for p in copied]
        self.assertEqual(frame_numbers, sorted(frame_numbers))


@patch("square_core.config.StudioConfig", side_effect=_stub_studio_config)
class TestVersionDetectionCorrectness(unittest.TestCase):
    """
    Two confirmed bugs in the old get_media_version_info(): it hardcoded its
    own "input/{type}_{name}/v###" path, which none of the real per-media-
    type destination templates (DEFAULT_MEDIA_TYPE_CONFIGS) ever write to --
    so it was silently checking an empty directory on every real ingest.
    And it compared un-renamed SOURCE filenames against already-renamed
    DESTINATION filenames, which can never be equal (copy_sequence always
    renames per the naming template), so "Already Ingested" could never
    fire even if the path had been right.

    These tests go through the REAL get_dest_dir() / copy_sequence() for the
    destination, exactly as the actual ingest flow does, then verify the
    check sees what was really written.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.nas = NASManager(nas_root=self.tmp / "nas", dry_run=False)

    def _item(self, name, content, seq, shot, mtype, mname):
        f = self.src / name
        f.write_text(content)
        it = IngestSequenceItem(Path(name).stem, [str(f)], ".mov", is_video=True)
        it.sequence_code, it.shot_code, it.media_type, it.media_name = seq, shot, mtype, mname
        return it

    def _ingest(self, item, version):
        dest_dir = self.nas.get_dest_dir(
            "PROJ", item.sequence_code, item.shot_code, item.media_name,
            version=version, media_type=item.media_type, resolution=item.resolution,
        )
        self.nas.create_shot_structure(dest_dir)
        self.nas.copy_sequence(item, dest_dir, version_num=version, proj_code="PROJ")
        return dest_dir

    def test_fresh_media_reads_new_v1(self, _cfg):
        it = self._item("clip1.mov", "hello", "SQ010", "SH0100", "Plate", "BG")
        ver, already = self.nas.get_media_version_info("PROJ", "SQ010", "SH0100", "BG", item=it)
        self.assertEqual((ver, already), (1, False))

    def test_checks_the_same_directory_the_real_copy_wrote_to(self, _cfg):
        it = self._item("clip1.mov", "hello", "SQ010", "SH0100", "Plate", "BG")
        self._ingest(it, 1)
        ver, already = self.nas.get_media_version_info("PROJ", "SQ010", "SH0100", "BG", item=it)
        self.assertEqual((ver, already), (1, True), "did not find the real copy -- checking the wrong path")

    def test_already_ingested_is_a_real_hash_match_not_a_filename_match(self, _cfg):
        # The destination file is ALWAYS renamed (e.g. clip1.mov ->
        # SQ010_SH0100_Plate_BG_v001.mov) -- if the check were still
        # comparing names, this could never come back True.
        it = self._item("clip1.mov", "hello", "SQ010", "SH0100", "Plate", "BG")
        dest_dir = self._ingest(it, 1)
        written = list(dest_dir.iterdir())
        self.assertEqual(len(written), 1)
        self.assertNotEqual(written[0].name, "clip1.mov")

        _ver, already = self.nas.get_media_version_info("PROJ", "SQ010", "SH0100", "BG", item=it)
        self.assertTrue(already)

    def test_different_content_at_the_same_slot_is_a_new_version_not_a_false_match(self, _cfg):
        it1 = self._item("clip1.mov", "hello", "SQ010", "SH0100", "Plate", "BG")
        self._ingest(it1, 1)
        it2 = self._item("clip2.mov", "totally different", "SQ010", "SH0100", "Plate", "BG")
        ver, already = self.nas.get_media_version_info("PROJ", "SQ010", "SH0100", "BG", item=it2)
        self.assertEqual((ver, already), (2, False))

    def test_a_media_type_without_a_preset_still_versions_correctly(self, _cfg):
        # Falls back to SHOT_DIRECTORY_TEMPLATE -- must still be internally
        # consistent between what's checked and what's written.
        it = self._item("clip1.mov", "hello", "SQ099", "SH9900", "CustomVendorCam", "XX")
        self._ingest(it, 1)
        ver, already = self.nas.get_media_version_info("PROJ", "SQ099", "SH9900", "XX", item=it)
        self.assertEqual((ver, already), (1, True))


@patch("square_core.config.StudioConfig", side_effect=_stub_studio_config)
class TestCheckSpecificVersion(unittest.TestCase):
    """
    check_specific_version() verifies ONE caller-chosen version number --
    used when a version is picked by hand instead of accepted from
    get_media_version_info(). Before this existed, a manual pick was never
    checked against the NAS at all.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.nas = NASManager(nas_root=self.tmp / "nas", dry_run=False)

    def _item(self, name, content, seq="SQ010", shot="SH0100", mtype="Plate", mname="BG"):
        f = self.src / name
        f.write_text(content)
        it = IngestSequenceItem(Path(name).stem, [str(f)], ".mov", is_video=True)
        it.sequence_code, it.shot_code, it.media_type, it.media_name = seq, shot, mtype, mname
        return it

    def test_free_slot_is_empty(self, _cfg):
        it = self._item("a.mov", "content")
        self.assertEqual(
            self.nas.check_specific_version("PROJ", "SQ010", "SH0100", "BG", 5, it),
            "empty",
        )

    def test_occupied_by_different_content_is_conflict(self, _cfg):
        original = self._item("a.mov", "original content")
        dest_dir = self.nas.get_dest_dir("PROJ", "SQ010", "SH0100", "BG", version=1, media_type="Plate")
        self.nas.create_shot_structure(dest_dir)
        self.nas.copy_sequence(original, dest_dir, version_num=1, proj_code="PROJ")

        different = self._item("b.mov", "different content")
        self.assertEqual(
            self.nas.check_specific_version("PROJ", "SQ010", "SH0100", "BG", 1, different),
            "conflict",
        )

    def test_occupied_by_the_same_content_is_already(self, _cfg):
        original = self._item("a.mov", "identical content")
        dest_dir = self.nas.get_dest_dir("PROJ", "SQ010", "SH0100", "BG", version=1, media_type="Plate")
        self.nas.create_shot_structure(dest_dir)
        self.nas.copy_sequence(original, dest_dir, version_num=1, proj_code="PROJ")

        self.assertEqual(
            self.nas.check_specific_version("PROJ", "SQ010", "SH0100", "BG", 1, original),
            "already",
        )


class TestCheckAllMediaForcedVersions(unittest.TestCase):
    """check_all_media()'s forced_versions -- mixed auto-detected and manually-picked rows in one batch call."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.nas = NASManager(nas_root=self.tmp / "nas", dry_run=False)

    def _item(self, name, seq, shot, mname):
        f = self.src / name
        f.write_text("x")
        it = IngestSequenceItem(Path(name).stem, [str(f)], ".mov", is_video=True)
        it.sequence_code, it.shot_code, it.media_type, it.media_name = seq, shot, "Plate", mname
        return it

    @patch("square_core.config.StudioConfig", side_effect=_stub_studio_config)
    def test_auto_and_forced_items_resolve_independently_in_one_call(self, _cfg):
        auto_item = self._item("a.mov", "SQ010", "SH0100", "BG")
        forced_item = self._item("b.mov", "SQ020", "SH0200", "FG")

        results = self.nas.check_all_media(
            [auto_item, forced_item], "PROJ", forced_versions={id(forced_item): 7}
        )
        self.assertEqual(results[id(auto_item)], (1, "new", False))
        # forced_item's v7 is a free slot -- check_specific_version's "empty"
        # is normalized to "new" here, the same label the auto path uses for
        # the same situation.
        self.assertEqual(results[id(forced_item)], (7, "new", True))

    def test_no_forced_versions_behaves_like_the_old_two_state_contract(self):
        item = self._item("a.mov", "SQ010", "SH0100", "BG")
        with patch("square_core.config.StudioConfig", side_effect=_stub_studio_config):
            results = self.nas.check_all_media([item], "PROJ")
        ver, state, forced = results[id(item)]
        self.assertEqual((ver, state, forced), (1, "new", False))


class TestCheckAllMediaErrorIsolation(unittest.TestCase):
    """
    check_all_media()'s ThreadPoolExecutor + as_completed loop used to let
    ANY per-item exception -- future.result() re-raising with nothing to
    catch it -- take the WHOLE batch down: every other item's already-
    computed result was silently discarded and the exception propagated
    straight out of check_all_media. With no try/except in NASCheckWorker
    either, results_ready never fired and every row in that batch stayed
    "Checking..." forever with no error shown anywhere. Real NAS data hits
    this in ways synthetic test data never does -- a permission error, an
    unusual path, a flaky mount -- exactly the class of bug that only shows
    up once a user pulls and runs for real.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.nas = NASManager(nas_root=self.tmp / "nas", dry_run=False)

    def _item(self, name, seq, shot, mname):
        f = self.src / name
        f.write_text("x")
        it = IngestSequenceItem(Path(name).stem, [str(f)], ".mov", is_video=True)
        it.sequence_code, it.shot_code, it.media_type, it.media_name = seq, shot, "Plate", mname
        return it

    @patch("square_core.config.StudioConfig", side_effect=_stub_studio_config)
    def test_one_items_exception_does_not_take_down_the_batch(self, _cfg):
        good_a = self._item("a.mov", "SQ010", "SH0100", "BG")
        bad    = self._item("b.mov", "SQ020", "SH0200", "FG")
        good_b = self._item("c.mov", "SQ030", "SH0300", "MM")

        real_get_info = self.nas.get_media_version_info

        def _flaky(proj_code, seq, shot, media_name, item=None, **kw):
            if item is bad:
                raise PermissionError("simulated: permission denied on the NAS mount")
            return real_get_info(proj_code, seq, shot, media_name, item=item, **kw)

        with patch.object(self.nas, "get_media_version_info", side_effect=_flaky):
            errors = {}
            results = self.nas.check_all_media([good_a, bad, good_b], "PROJ", errors=errors)

        # The two good items still resolved normally -- not swallowed by
        # the bad one's exception.
        self.assertEqual(results[id(good_a)], (1, "new", False))
        self.assertEqual(results[id(good_b)], (1, "new", False))
        # The bad item is reported as its own distinct state, not silently
        # dropped and not crashing the whole call.
        ver, state, forced = results[id(bad)]
        self.assertEqual(state, "error")
        self.assertIn(id(bad), errors)
        self.assertIn("permission denied", errors[id(bad)])

    @patch("square_core.config.StudioConfig", side_effect=_stub_studio_config)
    def test_errors_dict_is_optional(self, _cfg):
        # A caller that doesn't pass errors= (doesn't want the per-item
        # message) must not crash either -- it just doesn't get the detail.
        bad = self._item("b.mov", "SQ020", "SH0200", "FG")
        with patch.object(self.nas, "get_media_version_info", side_effect=RuntimeError("boom")):
            results = self.nas.check_all_media([bad], "PROJ")
        self.assertEqual(results[id(bad)][1], "error")

    @patch("square_core.config.StudioConfig", side_effect=_stub_studio_config)
    def test_a_forced_items_error_keeps_its_requested_version_number(self, _cfg):
        # An "error" result's version number is otherwise meaningless, but
        # for a forced (manually-picked) item it should still echo back the
        # number the user picked rather than an arbitrary default -- so the
        # row doesn't look like it silently reverted to v1.
        bad = self._item("b.mov", "SQ020", "SH0200", "FG")
        with patch.object(self.nas, "check_specific_version", side_effect=RuntimeError("boom")):
            results = self.nas.check_all_media([bad], "PROJ", forced_versions={id(bad): 5})
        ver, state, forced = results[id(bad)]
        self.assertEqual((ver, state, forced), (5, "error", True))


if __name__ == "__main__":
    unittest.main()
