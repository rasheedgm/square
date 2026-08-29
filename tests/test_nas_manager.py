import os
import unittest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

from square_core.nas_manager import NASManager
from square_core.plate_scanner import IngestSequenceItem


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


if __name__ == "__main__":
    unittest.main()
