import os
import time
import shutil
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from square_core.hashing import FileHasher, DEFAULT_ALGO


class TestFileHasher(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name, data=b"hello world"):
        p = self.tmp / name
        p.write_bytes(data)
        return str(p)

    def test_same_content_same_digest(self):
        a = self._write("a.bin", b"identical")
        b = self._write("b.bin", b"identical")
        h = FileHasher()
        self.assertEqual(h.hash_file(a), h.hash_file(b))

    def test_different_content_different_digest(self):
        a = self._write("a.bin", b"one")
        b = self._write("b.bin", b"two")
        h = FileHasher()
        self.assertNotEqual(h.hash_file(a), h.hash_file(b))

    def test_second_call_is_served_from_cache(self):
        import builtins
        p = self._write("a.bin")
        h = FileHasher()

        real_open = builtins.open
        reads = []

        def counting_open(file, *a, **kw):
            if str(file) == str(p):
                reads.append(1)
            return real_open(file, *a, **kw)

        with unittest.mock.patch("builtins.open", counting_open):
            first = h.hash_file(p)
            second = h.hash_file(p)

        self.assertEqual(first, second)
        self.assertEqual(len(reads), 1)          # only the first call touched disk
        self.assertEqual(h.cache_size, 1)

    def test_changed_file_is_rehashed(self):
        p = self._write("a.bin", b"before")
        h = FileHasher()
        first = h.hash_file(p)
        time.sleep(0.01)
        Path(p).write_bytes(b"after-and-longer")   # different size + mtime
        second = h.hash_file(p)
        self.assertNotEqual(first, second)

    def test_large_multichunk_file(self):
        big = os.urandom(5 * (1 << 20) + 123)
        a = self._write("big1.bin", big)
        b = self._write("big2.bin", big)
        h = FileHasher()
        self.assertEqual(h.hash_file(a), h.hash_file(b))

    def test_prime_seeds_cache_only_when_signature_matches(self):
        p = self._write("a.bin", b"content")
        h = FileHasher()
        h.prime(p, "deadbeefdeadbeef")
        self.assertEqual(h.hash_file(p), "deadbeefdeadbeef")

    def test_prime_ignored_for_missing_file(self):
        h = FileHasher()
        h.prime(str(self.tmp / "nope.bin"), "x")   # must not raise
        self.assertEqual(h.cache_size, 0)

    def test_blake2b_fallback_algo(self):
        p = self._write("a.bin")
        h = FileHasher(algo="blake2b")
        self.assertEqual(h.algo, "blake2b")
        self.assertEqual(len(h.hash_file(p)), 32)   # digest_size=16 -> 32 hex chars

    def test_unknown_algo_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            FileHasher(algo="sha0")

    def test_parallel_hashing_is_consistent(self):
        paths = [self._write(f"f{i}.bin", os.urandom(1 << 20)) for i in range(8)]
        h = FileHasher()
        serial = {p: h.hash_file(p) for p in paths}

        h2 = FileHasher()
        out = {}
        errs = []

        def work(p):
            try:
                out[p] = h2.hash_file(p)
            except Exception as e:  # pragma: no cover
                errs.append(e)

        threads = [threading.Thread(target=work, args=(p,)) for p in paths * 3]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errs, [])
        self.assertEqual(out, serial)

    def test_default_algo_is_xxh3(self):
        self.assertEqual(DEFAULT_ALGO, "xxh3_64")
        self.assertEqual(FileHasher().algo, "xxh3_64")


if __name__ == "__main__":
    unittest.main()
