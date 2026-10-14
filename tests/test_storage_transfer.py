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


class TestVerifyModesAndCancel(unittest.TestCase):
    """The studio's complaint: a copy was slower than their .bat. The default
    verification re-read every file; "size" does not read anything back."""

    def setUp(self):
        import shutil
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.dst = self.tmp / "dst"

    def _files(self, n=3):
        out = []
        for i in range(n):
            p = self.src / f"f.{1001 + i}.exr"
            p.write_bytes(os.urandom(2048))
            out.append(p)
        return out

    def test_size_verify_reads_nothing_back_and_returns_no_hash(self):
        from unittest.mock import patch
        from square_core.storage import transfer_file
        [f] = self._files(1)
        hasher = FileHasher()
        with patch.object(FileHasher, "hash_file") as hf:
            r = transfer_file(f, self.dst / f.name, hasher=hasher, verify="size")
        hf.assert_not_called()
        self.assertEqual(r.hash, "")
        self.assertTrue(r.verified)
        self.assertEqual((self.dst / f.name).read_bytes(), f.read_bytes())

    def test_size_verify_catches_a_short_copy(self):
        from unittest.mock import patch
        from square_core.storage import transfer, VerificationError
        [f] = self._files(1)

        def short_copy(src, dest, **kw):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(Path(src).read_bytes()[:100])
            return ""

        with patch.object(transfer, "copy_file", side_effect=short_copy):
            with self.assertRaises(VerificationError):
                transfer.transfer_file(f, self.dst / f.name, verify="size")
        self.assertFalse((self.dst / f.name).exists())          # the short file is not left behind

    def test_full_verify_still_hashes_and_returns_the_hash(self):
        from square_core.storage import transfer_file
        [f] = self._files(1)
        r = transfer_file(f, self.dst / f.name, verify="full")
        self.assertEqual(r.hash, FileHasher().hash_file(str(f)))

    def test_a_handed_in_source_hash_is_not_recomputed(self):
        from unittest.mock import patch
        from square_core.storage import transfer_file
        [f] = self._files(1)
        known = FileHasher().hash_file(str(f))
        h = FileHasher()
        with patch.object(FileHasher, "hash_file", wraps=h.hash_file) as hf:
            transfer_file(f, self.dst / f.name, hasher=h, verify="full", expected_hash=known)
        # only the destination is hashed -- by copy_file's own re-read, not hash_file
        hf.assert_not_called()

    def test_an_unknown_verify_mode_is_rejected(self):
        from square_core.storage import transfer_file
        [f] = self._files(1)
        with self.assertRaises(ValueError):
            transfer_file(f, self.dst / f.name, verify="sometimes")

    def test_should_stop_before_a_file_starts_copies_nothing(self):
        from square_core.storage import transfer_file
        from square_core.storage.transfer import TransferCancelled
        [f] = self._files(1)
        with self.assertRaises(TransferCancelled):
            transfer_file(f, self.dst / f.name, should_stop=lambda: True)
        self.assertFalse((self.dst / f.name).exists())

    def test_cancelling_a_sequence_stops_the_rest_and_waits_for_the_ones_in_flight(self):
        from square_core.storage import transfer_sequence
        from square_core.storage.transfer import TransferCancelled
        files = self._files(8)
        done = []

        def stop():
            return len(done) >= 2

        def progress(d, t):
            done.append(d)

        pairs = [(f, self.dst / f.name) for f in files]
        with self.assertRaises(TransferCancelled):
            transfer_sequence(pairs, workers=1, verify="size", should_stop=stop, progress=progress)
        written = sorted(p.name for p in self.dst.iterdir()) if self.dst.exists() else []
        self.assertLess(len(written), len(files))               # it did not copy them all


if __name__ == "__main__":
    unittest.main()
