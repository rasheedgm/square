"""video_to_exr_sequence -- decode a delivered video/mov into a numbered EXR
frame sequence.

For a vendor who delivers a single video file where the studio wants a real
image sequence (a Plate ingested as EXR, not the compressed mov itself). Runs
through the same ffmpeg binary as the review-proxy encoder.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from square_core.media.proxy import ffmpeg_bin, ProxyError


def video_to_exr_sequence(video_path, out_dir, *, start_frame: int = 1001) -> list[str]:
    """Decodes every frame of `video_path` to `<out_dir>/<stem>.<frame>.exr`,
    numbered from `start_frame`. Returns the written frame paths, sorted."""
    video_path = str(video_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem
    pattern = str(out_dir / f"{stem}.%04d.exr")

    cmd = [ffmpeg_bin(), "-y", "-i", video_path,
           "-start_number", str(start_frame),
           "-pix_fmt", "gbrpf32le",
           pattern]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception as e:
        raise ProxyError(f"video-to-EXR conversion failed: {e}") from e

    return sorted(str(p) for p in out_dir.glob(f"{stem}.*.exr"))
