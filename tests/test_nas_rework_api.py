import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import patch, MagicMock

from square_core.nas_manager import NASManager
from square_core.ingest_item import IngestItem
from square_core.hashing import FileHasher
from square_core.config import SHOT_DIRECTORY_TEMPLATE, DEFAULT_MEDIA_TYPE_CONFIGS


def _stub_cfg():
    c = MagicMock()
    c.media_type_configs = dict(DEFAULT_MEDIA_TYPE_CONFIGS)
    c.nas_dir_template = SHOT_DIRECTORY_TEMPLATE
    return c


class TestReworkAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        self.nas = NASManager(nas_root=self.tmp / "nas", dry_run=False)
        self.tmpl = "{shot}_{name}_v{version:03d}.{frame}{ext}"
        self.p = patch("square_core.config.StudioConfig", side_effect=_stub_cfg)
        self.p.start()
        self.addCleanup(self.p.stop)

    def _item(self, n=3, version=1, media_type="Plate"):
        files = []
        for i in range(n):
            f = self.src / f"raw.{1001+i:04d}.exr"
            f.write_bytes(b"frame-" + str(i).encode())
            files.append(str(f))
        return IngestItem(
            key=IngestItem.compute_key(files), source_files=files, ext=".exr",
            sequence_code="SQ010", shot_code="SH0100", media_type=media_type,
            media_name="main", version=version, start_frame=1001,
            end_frame=1000 + n, frame_count=n,
        )

    def test_dest_names_one_per_file(self):
        it = self._item(n=2)
        names = self.nas.dest_names(it, 1, "SHW", self.tmpl)
        self.assertEqual(len(names), 2)
        self.assertTrue(all(v.startswith("SH0100_main_v001.") for v in names.values()))

    def test_inspect_slot_empty(self):
        it = self._item()
        dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=1, media_type="Plate")
        state, _ = self.nas.inspect_slot(dest, it, 1, "SHW", self.tmpl, hasher=FileHasher())
        self.assertEqual(state, "empty")

    def test_inspect_slot_already_after_a_real_copy(self):
        it = self._item()
        h = FileHasher()
        dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=1, media_type="Plate")
        self.nas.copy_sequence(it, dest, filename_template=self.tmpl, version_num=1, proj_code="SHW")
        state, detail = self.nas.inspect_slot(dest, it, 1, "SHW", self.tmpl, hasher=h)
        self.assertEqual(state, "already")

    def test_inspect_slot_conflict_on_different_content(self):
        it = self._item()
        dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=1, media_type="Plate")
        self.nas.copy_sequence(it, dest, filename_template=self.tmpl, version_num=1, proj_code="SHW")
        # tamper one destination file
        first = next(dest.iterdir())
        first.write_bytes(b"tampered")
        state, detail = self.nas.inspect_slot(dest, it, 1, "SHW", self.tmpl, hasher=FileHasher())
        self.assertEqual(state, "conflict")
        self.assertIn("differs", detail)

    def test_inspect_slot_conflict_on_count_mismatch(self):
        it = self._item(n=3)
        dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=1, media_type="Plate")
        dest.mkdir(parents=True)
        (dest / "SH0100_main_v001.1001.exr").write_bytes(b"x")   # only 1 of 3
        state, detail = self.nas.inspect_slot(dest, it, 1, "SHW", self.tmpl, hasher=FileHasher())
        self.assertEqual(state, "conflict")

    def test_next_free_version_skips_occupied_slots(self):
        it = self._item()
        for v in (1, 2):
            dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=v, media_type="Plate")
            self.nas.copy_sequence(it, dest, filename_template=self.tmpl, version_num=v, proj_code="SHW")
        self.assertEqual(self.nas.next_free_version(it, "SHW", self.tmpl), 3)

    def test_next_free_version_respects_start(self):
        it = self._item()
        self.assertEqual(self.nas.next_free_version(it, "SHW", self.tmpl, start=5), 5)

    def test_copy_reuses_preflight_hash_no_source_rehash(self):
        it = self._item(n=3)
        h = FileHasher()
        source_hashes = {f: h.hash_file(f) for f in it.source_files}

        rehashed = []
        orig = FileHasher.hash_file
        h.hash_file = lambda p, _o=orig: (rehashed.append(p), _o(h, p))[1]

        dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=1, media_type="Plate")
        copied = self.nas.copy_sequence(it, dest, filename_template=self.tmpl, version_num=1,
                                        proj_code="SHW", hasher=h, source_hashes=source_hashes)
        self.assertEqual(len(copied), 3)
        # the verify compared against the supplied pre-flight hashes -- the copy
        # path never called back into FileHasher.hash_file for any source file
        self.assertEqual(rehashed, [])
        # and the bytes really landed
        for c in copied:
            self.assertTrue(Path(c).exists())

    def test_copy_detects_a_corrupt_transfer(self):
        it = self._item(n=2)
        h = FileHasher()
        sh = {f: h.hash_file(f) for f in it.source_files}
        dest = self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", version=1, media_type="Plate")

        # make _copy_and_hash write the wrong bytes
        orig = NASManager._copy_and_hash
        def bad(self, src, dst, hasher=None):
            Path(dst).write_bytes(b"corrupted")
            return "0000000000000000"
        with unittest.mock.patch.object(NASManager, "_copy_and_hash", bad):
            with self.assertRaises(IOError):
                self.nas.copy_sequence(it, dest, filename_template=self.tmpl, version_num=1,
                                       proj_code="SHW", hasher=h, source_hashes=sh)


if __name__ == "__main__":
    unittest.main()
