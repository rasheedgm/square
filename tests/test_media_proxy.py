"""square_core.media.proxy.make_proxy -- the ffmpeg command it builds."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from square_core.media.proxy import make_proxy


class TestMakeProxyScaling(unittest.TestCase):
    """Fits by WIDTH (always exactly that many pixels wide, height auto to
    preserve aspect) -- never crops or distorts, so a wide anamorphic plate
    and a tall portrait one both keep their own true aspect ratio, just
    always the same width."""

    def _vf(self, run_mock) -> str:
        cmd = run_mock.call_args[0][0]
        return cmd[cmd.index("-vf") + 1]

    def test_default_width_is_1280_no_crop(self):
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "c.1001.exr"
            frame.write_bytes(b"x")
            out = Path(td) / "out.mp4"
            with patch("square_core.media.proxy.subprocess.run") as run:
                run.return_value = MagicMock(returncode=0)
                make_proxy([str(frame)], out)
            self.assertEqual(self._vf(run), "scale=1280:-2")

    def test_custom_width_is_honored(self):
        with tempfile.TemporaryDirectory() as td:
            frame = Path(td) / "c.1001.exr"
            frame.write_bytes(b"x")
            out = Path(td) / "out.mp4"
            with patch("square_core.media.proxy.subprocess.run") as run:
                run.return_value = MagicMock(returncode=0)
                make_proxy([str(frame)], out, width=1920)
            self.assertEqual(self._vf(run), "scale=1920:-2")

    def test_video_source_uses_the_same_fit_by_width_filter(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "clip.mov"
            src.write_bytes(b"x")
            out = Path(td) / "out.mp4"
            with patch("square_core.media.proxy.subprocess.run") as run:
                run.return_value = MagicMock(returncode=0)
                make_proxy(str(src), out, is_video=True)
            self.assertEqual(self._vf(run), "scale=1280:-2")

    def test_dry_run_never_touches_ffmpeg(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.mp4"
            with patch("square_core.media.proxy.subprocess.run") as run:
                path = make_proxy(["never-checked.exr"], out, dry_run=True)
            run.assert_not_called()
            self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
