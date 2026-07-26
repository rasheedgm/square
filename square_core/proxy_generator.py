import os
import sys
import shutil
import subprocess
import logging
from pathlib import Path
from PIL import Image, ImageDraw

logger = logging.getLogger("SquareProxy")

def find_ffmpeg_bin():
    """Finds ffmpeg executable on PATH or inside local conda environment."""
    ffmpeg_on_path = shutil.which("ffmpeg")
    if ffmpeg_on_path:
        return ffmpeg_on_path

    # Check local conda env paths
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
        dest_name = dest_name or f"{item.sequence_code}_{item.shot_code}_{item.plate_name}_preview.mp4"
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
                "-vf", f"scale=1280:-2,drawtext=text='{item.sequence_code} {item.shot_code} | %{{frame_num}}':x=20:y=h-40:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                str(out_mp4)
            ]
        else:
            first_frame = item.files[0]
            # Pattern matching for image sequence
            pattern = first_frame.replace(str(item.start_frame), "%04d")
            
            cmd = [
                self.ffmpeg_cmd, "-y",
                "-start_number", str(item.start_frame),
                "-framerate", str(item.fps),
                "-i", pattern,
                "-vf", f"scale=1280:-2,drawtext=text='{item.sequence_code}_{item.shot_code} | Frame\\: %{{frame_num}}':x=20:y=h-40:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                str(out_mp4)
            ]

        try:
            logger.info(f"[ProxyGenerator] Rendering MP4 via FFmpeg: {' '.join(cmd)}")
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            return str(out_mp4)
        except Exception as e:
            logger.warning(f"[ProxyGenerator] FFmpeg encoding failed: {e}. Generating studio slate preview card.")
            return self._generate_slate_proxy(item, out_mp4)

    def _generate_slate_proxy(self, item, out_mp4):
        """Generates a synthetic studio slate preview card when raw files cannot be converted."""
        preview_img_path = str(out_mp4).replace(".mp4", "_preview.jpg")
        
        try:
            # Create a 1280x720 dark slate image card
            img = Image.new("RGB", (1280, 720), color=(26, 31, 41))
            draw = ImageDraw.Draw(img)

            # Draw Slate Borders & Title
            draw.rectangle([(20, 20), (1260, 700)], outline=(0, 180, 216), width=3)
            draw.rectangle([(30, 30), (1250, 100)], fill=(38, 45, 61))
            
            draw.text((50, 50), "SQUARE VFX STUDIO - PLATE INGEST SLATE", fill=(0, 180, 216))
            draw.text((50, 140), f"Sequence: {item.sequence_code}", fill=(248, 250, 252))
            draw.text((50, 180), f"Shot Code: {item.shot_code}", fill=(248, 250, 252))
            draw.text((50, 220), f"Plate Name: {item.plate_name}", fill=(248, 250, 252))
            draw.text((50, 260), f"Frame Range: {item.frame_range_str}", fill=(248, 250, 252))
            draw.text((50, 300), f"FPS: {item.fps} | Colorspace: {item.colorspace}", fill=(248, 250, 252))
            draw.text((50, 340), f"Total Files: {len(item.files)}", fill=(248, 250, 252))

            img.save(preview_img_path)
            logger.info(f"[ProxyGenerator] Created studio slate preview card: {preview_img_path}")
            return preview_img_path
        except Exception as e:
            logger.error(f"[ProxyGenerator] Slate proxy generation failed: {e}")
            return None
