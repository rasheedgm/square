import os
import subprocess
import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("SquareProxy")

class ProxyGenerator:
    """Generates low-res MP4/MOV previews for Kitsu review."""

    def __init__(self, output_dir=None, dry_run=True):
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "temp_proxies"
        self.dry_run = dry_run
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_proxy(self, item, dest_name=None):
        """Generates H.264 MP4 preview from an IngestSequenceItem."""
        dest_name = dest_name or f"{item.sequence_code}_{item.shot_code}_{item.plate_name}_preview.mp4"
        out_mp4 = self.output_dir / dest_name

        if self.dry_run:
            logger.info(f"[Mock Proxy] Created low-res proxy MP4: {out_mp4}")
            # Create a lightweight dummy file if dry-run
            with open(out_mp4, "w") as f:
                f.write("MOCK MP4 PREVIEW CONTENT")
            return str(out_mp4)

        if not item.files:
            return None

        # Check if single video file vs image sequence
        if item.is_video:
            src_video = item.files[0]
            cmd = [
                "ffmpeg", "-y", "-i", src_video,
                "-vf", f"scale=1280:-2,drawtext=text='{item.sequence_code} {item.shot_code} | %{{frame_num}}':x=20:y=h-40:fontsize=24:fontcolor=white:box=1:boxcolor=black@0.5",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                str(out_mp4)
            ]
        else:
            first_frame = item.files[0]
            ext = item.ext.lstrip(".")
            # Pattern matching for image sequence
            pattern = first_frame.replace(str(item.start_frame), "%04d")
            
            cmd = [
                "ffmpeg", "-y",
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
            logger.warning(f"[ProxyGenerator] FFmpeg encoding failed or not installed: {e}. Falling back to fallback generator.")
            return self._fallback_image_proxy(item, out_mp4)

    def _fallback_image_proxy(self, item, out_mp4):
        """Fallback: Creates a static frame image preview if ffmpeg is unavailable."""
        if not item.files:
            return None
            
        first_frame = item.files[0]
        preview_img_path = str(out_mp4).replace(".mp4", "_preview.jpg")
        
        try:
            with Image.open(first_frame) as img:
                img = img.convert("RGB")
                img.thumbnail((1280, 720))
                
                draw = ImageDraw.Draw(img)
                text = f"{item.sequence_code} | {item.shot_code} | {item.plate_name} ({item.frame_range_str})"
                draw.rectangle([(10, img.height - 40), (img.width - 10, img.height - 10)], fill=(0, 0, 0, 180))
                draw.text((20, img.height - 35), text, fill=(255, 255, 255))
                
                img.save(preview_img_path)
                logger.info(f"[ProxyGenerator] Created fallback image preview: {preview_img_path}")
                return preview_img_path
        except Exception as e:
            logger.error(f"[ProxyGenerator] Fallback proxy generation failed: {e}")
            return None
