"""video_to_exr_sequence -- decode a delivered video/mov into a numbered EXR
frame sequence.

For a vendor who delivers a single video file where the studio wants a real
image sequence (a Plate ingested as EXR, not the compressed mov itself). Runs
through the same ffmpeg binary as the review-proxy encoder.
"""

from __future__ import annotations

from pathlib import Path

from square_core.media.proxy import ffmpeg_bin, ProxyError, run_ffmpeg


def video_to_exr_sequence(video_path, out_dir, *, start_frame: int = 1001,
                          progress=None, should_stop=None) -> list[str]:
    """Decodes every frame of `video_path` to `<out_dir>/<stem>.<frame>.exr`,
    numbered from `start_frame`. Returns the written frame paths, sorted.
    `progress(pct)` / `should_stop()` -- see `run_ffmpeg`."""
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
        run_ffmpeg(cmd, label=f"convert {stem}", progress=progress, should_stop=should_stop)
    except ProxyError as e:
        raise ProxyError(f"video-to-EXR conversion failed: {e}") from e
    except Exception as e:
        raise ProxyError(f"video-to-EXR conversion failed: {e}") from e

    return sorted(str(p) for p in out_dir.glob(f"{stem}.*.exr"))
