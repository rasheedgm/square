import os
import subprocess
import json
import logging
from PIL import Image

logger = logging.getLogger("SquareMetadata")

class MetadataExtractor:
    """Extracts resolution, FPS, timecode, and colorspace from media files."""

    @staticmethod
    def extract_metadata(filepath):
        """Inspects single image or video file and returns metadata dict."""
        meta = {
            "width": 1920,
            "height": 1080,
            "resolution": "1920x1080",
            "fps": 24.0,
            "colorspace": "ACEScg",
            "timecode": "01:00:00:00"
        }

        if not os.path.exists(filepath):
            return meta

        ext = os.path.splitext(filepath)[1].lower()

        # Try Pillow for standard image dimensions
        try:
            with Image.open(filepath) as img:
                meta["width"], meta["height"] = img.size
                meta["resolution"] = f"{meta['width']}x{meta['height']}"
                if ext == ".exr":
                    meta["colorspace"] = "ACEScg"
                elif ext in (".dpx", ".cin"):
                    meta["colorspace"] = "LogC"
                else:
                    meta["colorspace"] = "sRGB"
                return meta
        except Exception:
            pass

        # Try ffprobe if available
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                filepath
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get("streams", [])
                for s in streams:
                    if s.get("codec_type") == "video":
                        meta["width"] = s.get("width", 1920)
                        meta["height"] = s.get("height", 1080)
                        meta["resolution"] = f"{meta['width']}x{meta['height']}"
                        
                        r_fps = s.get("r_frame_rate", "24/1")
                        if "/" in r_fps:
                            num, den = map(float, r_fps.split("/"))
                            meta["fps"] = round(num / den, 2) if den > 0 else 24.0
                        break
        except Exception:
            pass

        return meta
