import shutil
import tempfile
import unittest
from pathlib import Path

from square_core.media.metadata import MetadataExtractor, DEFAULT_METADATA


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


class TestRunFfmpeg(unittest.TestCase):
    """ffmpeg now reports progress (and can be stopped) instead of running
    silent until it ends."""

    def _cmd(self, out):
        from square_core.media.proxy import ffmpeg_bin
        return [ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x64:rate=10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]

    def test_progress_is_reported_and_reaches_100(self):
        import tempfile
        from square_core.media.proxy import run_ffmpeg
        seen = []
        with tempfile.TemporaryDirectory() as d:
            run_ffmpeg(self._cmd(Path(d) / "o.mp4"), total_frames=20, progress=seen.append,
                       echo=False)
            self.assertTrue((Path(d) / "o.mp4").exists())
        self.assertEqual(seen[-1], 100)
        self.assertEqual(seen, sorted(seen))                    # only ever moves forward

    def test_a_failing_ffmpeg_raises_with_its_message(self):
        import tempfile
        from square_core.media.proxy import run_ffmpeg, ProxyError, ffmpeg_bin
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ProxyError) as cm:
                run_ffmpeg([ffmpeg_bin(), "-y", "-i", str(Path(d) / "missing.mov"),
                            str(Path(d) / "o.mp4")], echo=False)
        self.assertIn("missing.mov", str(cm.exception))

    def test_should_stop_terminates_it(self):
        import tempfile
        from square_core.media.proxy import run_ffmpeg, ProxyError
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ProxyError) as cm:
                run_ffmpeg(self._cmd(Path(d) / "o.mp4"), should_stop=lambda: True, echo=False)
        self.assertIn("cancelled", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
