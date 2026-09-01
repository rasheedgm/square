import os
import sys
import shutil
import subprocess
import logging
from pathlib import Path
from PIL import Image, ImageDraw

logger = logging.getLogger("SquareProxy")

def find_ffmpeg_bin():
    """Finds ffmpeg executable via imageio-ffmpeg, PATH, or local conda environment."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass

    ffmpeg_on_path = shutil.which("ffmpeg")
    if ffmpeg_on_path:
        return ffmpeg_on_path

    root_dir = Path(__file__).parent.parent
    possible_paths = [
        root_dir / "env" / "Library" / "bin" / "ffmpeg.exe",
        root_dir / "env" / "Scripts" / "ffmpeg.exe",
        root_dir / "env" / "ffmpeg.exe"
    ]
    for p in possible_paths:
        if p.exists():
            return str(p)

    return "ffmpeg"


class ProxyGenerator:
    """Generates low-res MP4/MOV previews for Kitsu review."""

    def __init__(self, output_dir=None, dry_run=True):
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "temp_proxies"
        self.dry_run = dry_run
        self.ffmpeg_cmd = find_ffmpeg_bin()
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_proxy(self, item, dest_name=None):
        """Generates H.264 MP4 preview from an IngestSequenceItem."""
        dest_name = dest_name or f"{item.sequence_code}_{item.shot_code}_{item.media_name}_preview.mp4"
        out_mp4 = self.output_dir / dest_name

        if self.dry_run:
            logger.info(f"[Mock Proxy] Created low-res proxy MP4: {out_mp4}")
            # Create a lightweight dummy file if dry-run
            with open(out_mp4, "wb") as f:
                f.write(b"MOCK MP4 PREVIEW CONTENT")
            return str(out_mp4)

        if not item.files:
            return None

        # Check if single video file vs image sequence
        if item.is_video:
            src_video = item.files[0]
            cmd = [
                self.ffmpeg_cmd, "-y", "-i", src_video,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                str(out_mp4)
            ]
        else:
            first_frame = item.files[0]
            pattern = first_frame.replace(str(item.start_frame), "%04d")
            
            cmd = [
                self.ffmpeg_cmd, "-y",
                "-start_number", str(item.start_frame),
                "-framerate", str(item.fps),
                "-i", pattern,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                str(out_mp4)
            ]

        try:
            logger.info(f"[ProxyGenerator] Rendering MP4 via FFmpeg: {' '.join(cmd)}")
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return str(out_mp4)
        except Exception as e:
            logger.warning(f"[ProxyGenerator] FFmpeg sequence encoding failed: {e}. Generating studio slate preview video card.")
            return self._generate_slate_proxy(item, out_mp4)

    def _generate_slate_proxy(self, item, out_mp4):
        """Generates a 1-second MP4 video preview card when raw sequence files are mock text or non-decodable."""
        slate_jpg = self.output_dir / f"{item.sequence_code}_{item.shot_code}_{item.media_name}_slate.jpg"
        
        try:
            # Create a 1280x720 dark slate image card
            img = Image.new("RGB", (1280, 720), color=(26, 31, 41))
            draw = ImageDraw.Draw(img)

            # Draw Slate Borders & Title
            draw.rectangle([(20, 20), (1260, 700)], outline=(0, 180, 216), width=3)
            draw.rectangle([(30, 30), (1250, 100)], fill=(38, 45, 61))
            
            draw.text((50, 50), "SQUARE VFX STUDIO - MEDIA INGEST SLATE", fill=(0, 180, 216))
            draw.text((50, 140), f"Sequence: {item.sequence_code}", fill=(248, 250, 252))
            draw.text((50, 180), f"Shot Code: {item.shot_code}", fill=(248, 250, 252))
            draw.text((50, 220), f"Media Name: {item.media_name}", fill=(248, 250, 252))
            draw.text((50, 260), f"Frame Range: {item.frame_range_str}", fill=(248, 250, 252))
            draw.text((50, 300), f"FPS: {item.fps} | Colorspace: {item.colorspace}", fill=(248, 250, 252))
            draw.text((50, 340), f"Total Files: {len(item.files)}", fill=(248, 250, 252))

            img.save(slate_jpg)

            # Convert slate JPG into a 1-second MP4 video using FFmpeg
            cmd = [
                self.ffmpeg_cmd, "-y",
                "-loop", "1", "-i", str(slate_jpg),
                "-c:v", "libx264", "-t", "1", "-pix_fmt", "yuv420p",
                str(out_mp4)
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info(f"[ProxyGenerator] Created studio slate MP4 preview video card: {out_mp4}")
            return str(out_mp4)
        except Exception as e:
            logger.error(f"[ProxyGenerator] Slate proxy generation failed: {e}")
            try:
                out_mp4.write_text("mock mp4 preview content")
                return str(out_mp4)
            except Exception:
                return str(slate_jpg) if slate_jpg.exists() else None
