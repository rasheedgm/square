"""square_core.storage -- the verified copy engine + tree creation."""

import os
import tempfile
import unittest
from pathlib import Path

from square_core.hashing import FileHasher
from square_core.storage import (
    copy_file, transfer_file, transfer_sequence, create_tree, VerificationError,
)


class TestCopyFile(unittest.TestCase):
    def test_copies_bytes_and_returns_matching_hash(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.bin"
            src.write_bytes(b"hello world" * 1000)
            dest = Path(td) / "out" / "a.bin"
            h = copy_file(src, dest)
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), src.read_bytes())
            self.assertEqual(h, FileHasher().hash_file(str(src)))

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.bin"
            src.write_bytes(b"x")
            copy_file(src, Path(td) / "deep" / "nested" / "a.bin")
            self.assertTrue((Path(td) / "deep" / "nested" / "a.bin").exists())


class TestTransferFile(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.src = Path(self.td) / "src.exr"
        self.src.write_bytes(b"frame-data" * 500)
        self.dest = Path(self.td) / "dest" / "out.exr"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def test_copy_mode_verifies(self):
        r = transfer_file(self.src, self.dest, mode="copy")
        self.assertEqual(r.mode, "copy")
        self.assertTrue(r.verified)
        self.assertEqual(self.dest.read_bytes(), self.src.read_bytes())

    def test_copy_uses_preflight_hash(self):
        h = FileHasher()
        expected = h.hash_file(str(self.src))
        r = transfer_file(self.src, self.dest, mode="copy", hasher=h, expected_hash=expected)
        self.assertEqual(r.hash, expected)

    def test_mismatch_raises(self):
        r = transfer_file(self.src, self.dest, mode="copy")  # noqa: F841
        # corrupt the source hash we claim, transfer again -> mismatch
        with self.assertRaises(VerificationError):
            transfer_file(self.src, Path(self.td) / "d2.exr", mode="copy",
                          expected_hash="deadbeef")

    def test_hardlink_mode(self):
        r = transfer_file(self.src, self.dest, mode="hardlink")
        # same volume -> hardlink; falls back to copy only if the FS refuses
        self.assertIn(r.mode, ("hardlink", "copy"))
        self.assertEqual(self.dest.read_bytes(), self.src.read_bytes())


class TestTransferSequence(unittest.TestCase):
    def test_transfers_all_frames(self):
        with tempfile.TemporaryDirectory() as td:
            srcs = []
            for f in range(1001, 1006):
                p = Path(td) / f"in.{f}.exr"
                p.write_bytes(f"frame{f}".encode() * 100)
                srcs.append(p)
            pairs = [(s, Path(td) / "out" / s.name) for s in srcs]
            seen = []
            results = transfer_sequence(pairs, mode="copy", workers=3,
                                        progress=lambda d, t: seen.append((d, t)))
            self.assertEqual(len(results), 5)
            self.assertTrue(all(r.verified for r in results))
            self.assertEqual(seen[-1], (5, 5))
            for _, d in pairs:
                self.assertTrue(Path(d).exists())


class TestLayout(unittest.TestCase):
    def test_create_tree_returns_only_new(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ABC"
            made = create_tree(root, ["shots", "assets", "_pipeline"])
            self.assertEqual(len(made), 4)                 # root + 3
            again = create_tree(root, ["shots", "assets", "_pipeline"])
            self.assertEqual(again, [])                    # idempotent
            self.assertTrue((root / "shots").is_dir())


if __name__ == "__main__":
    unittest.main()
