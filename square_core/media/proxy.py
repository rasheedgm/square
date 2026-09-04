"""make_proxy -- a low-res H.264 MP4 for Kitsu review, from an image sequence
or a video.

Clean, function-based successor to the ingest tool's `ProxyGenerator`. ffmpeg
comes from `imageio-ffmpeg` (bundled) or `$FFMPEG_BINARY` / PATH.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger("square.media")


class ProxyError(RuntimeError):
    pass


def ffmpeg_bin() -> str:
    if os.environ.get("FFMPEG_BINARY"):
        return os.environ["FFMPEG_BINARY"]
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


_FRAME_RE = re.compile(r"(\d+)(?=\.\w+$)")


def _to_pattern(first_frame: str, start: int) -> str:
    """`.../name.1001.exr` -> `.../name.%04d.exr` (pad from the real frame)."""
    m = _FRAME_RE.search(first_frame)
    if not m:
        return first_frame
    pad = len(m.group(1))
    return first_frame[: m.start()] + f"%0{pad}d" + first_frame[m.end():]


def make_proxy(source, out_path, *, fps: float = 24.0, is_video: bool = False,
               start_frame: int | None = None, height: int = 720,
               dry_run: bool = False) -> str:
    """`source` is a list of frame paths (image sequence) or a single video path.
    Writes an MP4 to `out_path`, returns it. `dry_run` writes a tiny stub."""
    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if dry_run:
        Path(out_path).write_bytes(b"MOCK MP4 PROXY")
        return out_path

    files = [source] if isinstance(source, (str, Path)) else list(source)
    if not files:
        raise ProxyError("no source media for proxy")

    ff = ffmpeg_bin()
    vf = f"scale=-2:{height}"
    if is_video or len(files) == 1 and not _FRAME_RE.search(str(files[0])):
        cmd = [ff, "-y", "-i", str(files[0]), "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
               out_path]
    else:
        start = start_frame if start_frame is not None else _guess_start(files[0])
        pattern = _to_pattern(str(files[0]), start)
        cmd = [ff, "-y", "-start_number", str(start), "-framerate", str(fps),
               "-i", pattern, "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
               out_path]

    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception as e:
        raise ProxyError(f"ffmpeg failed: {e}") from e
    return out_path


def _guess_start(first_frame: str) -> int:
    m = _FRAME_RE.search(str(first_frame))
    return int(m.group(1)) if m else 1
