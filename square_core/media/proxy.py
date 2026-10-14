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
import sys
import time
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
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def run_ffmpeg(cmd, *, label: str = "ffmpeg", total_frames: int | None = None,
               progress=None, should_stop=None, echo: bool = True) -> None:
    """Run an ffmpeg command line to completion, reporting progress.

    `progress(pct)` (0-100) is called as it advances; a line is also printed to
    the terminal every couple of seconds (`echo`) so a long encode isn't a
    silent hang. `should_stop()` is polled -- when it says stop, ffmpeg is
    terminated and ProxyError("cancelled") raised. `total_frames` is the
    expected frame count when known (an image sequence); otherwise progress is
    taken from the input's duration.
    """
    full = [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1", *cmd[1:]]
    proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, errors="replace")
    duration = None
    pct = 0
    last_echo = 0.0
    tail: list = []
    try:
        for line in proc.stdout:
            line = line.strip()
            if should_stop is not None and should_stop():
                proc.terminate()
                proc.wait(10)
                raise ProxyError("cancelled")
            m = _DURATION_RE.search(line)
            if m and duration is None:
                h, mi, sec = m.groups()
                duration = int(h) * 3600 + int(mi) * 60 + float(sec)
                continue
            if "=" in line and line.split("=", 1)[0] in (
                    "frame", "fps", "out_time_us", "out_time_ms", "out_time", "bitrate",
                    "total_size", "speed", "progress", "dup_frames", "drop_frames",
                    "stream_0_0_q"):
                key, val = line.split("=", 1)
                new = None
                if key == "frame" and total_frames:
                    try:
                        new = min(99, int(val) * 100 // total_frames)
                    except ValueError:
                        pass
                elif key in ("out_time_us", "out_time_ms") and duration and not total_frames:
                    try:
                        new = min(99, int(float(val) / 1_000_000 * 100 / duration))
                    except ValueError:
                        pass
                if new is not None and new != pct:
                    pct = new
                    if progress is not None:
                        progress(pct)
                    now = time.monotonic()
                    if echo and now - last_echo >= 2.0:
                        last_echo = now
                        print(f"[{label}] {pct}%", file=sys.stderr, flush=True)
            elif line:
                tail.append(line)
                del tail[:-12]
        rc = proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    if rc != 0:
        raise ProxyError(f"ffmpeg exited with status {rc}: " + " | ".join(tail[-4:]))
    if progress is not None:
        progress(100)
    if echo:
        print(f"[{label}] done", file=sys.stderr, flush=True)


def _to_pattern(first_frame: str, start: int) -> str:
    """`.../name.1001.exr` -> `.../name.%04d.exr` (pad from the real frame)."""
    m = _FRAME_RE.search(first_frame)
    if not m:
        return first_frame
    pad = len(m.group(1))
    return first_frame[: m.start()] + f"%0{pad}d" + first_frame[m.end():]


def make_proxy(source, out_path, *, fps: float = 24.0, is_video: bool = False,
               start_frame: int | None = None, height: int = 720,
               dry_run: bool = False, progress=None, should_stop=None) -> str:
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

    is_seq = not (is_video or len(files) == 1 and not _FRAME_RE.search(str(files[0])))
    try:
        run_ffmpeg(cmd, label=f"proxy {Path(out_path).stem}",
                   total_frames=len(files) if is_seq else None,
                   progress=progress, should_stop=should_stop)
    except ProxyError:
        raise
    except Exception as e:
        raise ProxyError(f"ffmpeg failed: {e}") from e
    return out_path


def _guess_start(first_frame: str) -> int:
    m = _FRAME_RE.search(str(first_frame))
    return int(m.group(1)) if m else 1
