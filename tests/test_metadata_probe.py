import shutil
import tempfile
import unittest
from pathlib import Path

from square_core.metadata_extractor import MetadataExtractor, DEFAULT_METADATA


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_file_returns_empty(self):
        found, backend = MetadataExtractor.probe(str(self.tmp / "nope.exr"))
        self.assertEqual(found, {})
        self.assertIsNone(backend)

    def test_probe_only_returns_probe_fields(self):
        # A real image the header readers can decode -- probe reports the
        # dimensions (a genuine read) and nothing outside PROBE_FIELDS.
        from PIL import Image
        p = self.tmp / "x.png"
        Image.new("RGB", (640, 480)).save(p)
        found, backend = MetadataExtractor.probe(str(p))
        self.assertIn(backend, ("oiio", "pillow"))
        self.assertEqual(found["resolution"], "640x480")
        self.assertTrue(set(found).issubset(set(MetadataExtractor.PROBE_FIELDS)))

    def test_probe_does_not_invent_defaults(self):
        # Whatever probe returns, it must not just be DEFAULT_METADATA.
        from PIL import Image
        p = self.tmp / "x.png"
        Image.new("RGB", (100, 100)).save(p)
        found, _ = MetadataExtractor.probe(str(p))
        # fps/timecode were never in a PNG -- must be absent, not the 24.0 default
        self.assertNotIn("fps", found)
        self.assertNotIn("timecode", found)

    def test_extract_metadata_still_fills_defaults(self):
        # The old API is unchanged: always a full dict.
        meta = MetadataExtractor.extract_metadata(str(self.tmp / "nope.exr"))
        self.assertEqual(meta, DEFAULT_METADATA)


if __name__ == "__main__":
    unittest.main()
