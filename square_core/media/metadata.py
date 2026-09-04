import os
import re
import shutil
import subprocess
import json
import logging
from PIL import Image

logger = logging.getLogger("SquareMetadata")

DEFAULT_METADATA = {
    "width": 1920,
    "height": 1080,
    "resolution": "1920x1080",
    "fps": 24.0,
    "colorspace": "ACEScg",
    "timecode": "01:00:00:00",
}


class MetadataExtractor:
    """Extracts resolution, FPS, timecode, and colorspace from media files.

    Backend order: OpenImageIO (real EXR/DPX/TIFF header reads — Pillow has
    no OpenEXR/DPX decoder at all, so without this the two main VFX plate
    formats always fell through to the hardcoded defaults below) -> Pillow
    (PNG/JPG/TIFF Pillow can actually decode) -> ffprobe/ffmpeg (video
    containers). Any backend that isn't installed, or fails to read a
    given file, is skipped silently and falls through to the next.
    """

    # Fields probe() may report. Anything not in the returned dict was NOT
    # read from the file and must not be treated as known.
    PROBE_FIELDS = ("width", "height", "resolution", "fps", "colorspace", "timecode")

    @staticmethod
    def probe(filepath):
        """
        Like extract_metadata(), but returns ONLY the fields a backend
        actually read from the file -- no DEFAULT_METADATA fill-in -- plus
        the backend name.

        Returns (found: dict, backend: str | None). Lets a caller tell a
        real "ACEScg" header read apart from the ACEScg fallback, so an
        unread colorspace can be surfaced for the user to set rather than
        silently shipped.
        """
        if not os.path.exists(filepath):
            return {}, None
        for name, fn in (
            ("oiio", MetadataExtractor._extract_with_oiio),
            ("pillow", MetadataExtractor._extract_with_pillow),
            ("video", MetadataExtractor._extract_video_metadata),
        ):
            got = fn(filepath)
            if got:
                return {k: v for k, v in got.items() if k in MetadataExtractor.PROBE_FIELDS}, name
        return {}, None

    @staticmethod
    def extract_metadata(filepath):
        """Inspects a single image or video file and returns a metadata dict."""
        meta = dict(DEFAULT_METADATA)

        if not os.path.exists(filepath):
            return meta

        oiio_meta = MetadataExtractor._extract_with_oiio(filepath)
        if oiio_meta:
            meta.update(oiio_meta)
            return meta

        pillow_meta = MetadataExtractor._extract_with_pillow(filepath)
        if pillow_meta:
            meta.update(pillow_meta)
            return meta

        video_meta = MetadataExtractor._extract_video_metadata(filepath)
        if video_meta:
            meta.update(video_meta)

        return meta

    @staticmethod
    def _extract_with_oiio(filepath):
        """Real EXR/DPX/TIFF header metadata via OpenImageIO, when installed."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in (".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg", ".cin"):
            return None
        try:
            import OpenImageIO as oiio
        except ImportError:
            return None

        try:
            inp = oiio.ImageInput.open(filepath)
            if inp is None:
                oiio.geterror()  # consume the pending global error so it isn't logged as unretrieved
                return None
            try:
                spec = inp.spec()
                result = {
                    "width": spec.width,
                    "height": spec.height,
                    "resolution": f"{spec.width}x{spec.height}",
                }
                fps = spec.get_float_attribute("FramesPerSecond", 0.0)
                if fps:
                    result["fps"] = round(fps, 3)

                cs = (
                    spec.get_string_attribute("oiio:ColorSpace")
                    or spec.get_string_attribute("colorspace")
                    or spec.get_string_attribute("ACESImageContainerFlag")
                )
                if cs:
                    result["colorspace"] = cs
                elif ext in (".dpx", ".cin"):
                    result["colorspace"] = "LogC"

                tc = spec.get_string_attribute("timecode") or spec.get_string_attribute("smpte:TimeCode")
                if tc:
                    result["timecode"] = tc

                return result
            finally:
                inp.close()
        except Exception as e:
            logger.debug(f"[MetadataExtractor] OpenImageIO could not read {filepath}: {e}")
            return None

    @staticmethod
    def _extract_with_pillow(filepath):
        """Standard raster formats Pillow can actually decode (EXR/DPX are NOT among them)."""
        ext = os.path.splitext(filepath)[1].lower()
        try:
            with Image.open(filepath) as img:
                width, height = img.size
                result = {"width": width, "height": height, "resolution": f"{width}x{height}"}
                if ext in (".dpx", ".cin"):
                    result["colorspace"] = "LogC"
                elif ext not in (".exr",):
                    result["colorspace"] = "sRGB"
                return result
        except Exception:
            return None

    @staticmethod
    def _extract_video_metadata(filepath):
        ffprobe_bin = shutil.which("ffprobe")
        if ffprobe_bin:
            result = MetadataExtractor._extract_with_ffprobe(filepath, ffprobe_bin)
            if result:
                return result

        # ffprobe isn't on PATH (imageio-ffmpeg only bundles ffmpeg, not ffprobe) --
        # fall back to parsing `ffmpeg -i <file>` stderr, which always prints
        # input stream info even without decoding anything.
        try:
            from square_core.media.proxy import ffmpeg_bin as _resolve_ffmpeg
            ffmpeg_bin = _resolve_ffmpeg()
        except Exception:
            ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            return MetadataExtractor._extract_with_ffmpeg_stderr(filepath, ffmpeg_bin)
        return None

    @staticmethod
    def _extract_with_ffprobe(filepath, ffprobe_bin):
        try:
            cmd = [
                ffprobe_bin, "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                filepath
            ]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if result.returncode != 0:
                return None
            data = json.loads(result.stdout)
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    width, height = s.get("width", 1920), s.get("height", 1080)
                    out = {"width": width, "height": height, "resolution": f"{width}x{height}"}
                    r_fps = s.get("r_frame_rate", "24/1")
                    if "/" in r_fps:
                        num, den = map(float, r_fps.split("/"))
                        out["fps"] = round(num / den, 2) if den > 0 else 24.0
                    tc = s.get("tags", {}).get("timecode")
                    if tc:
                        out["timecode"] = tc
                    return out
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_with_ffmpeg_stderr(filepath, ffmpeg_bin):
        try:
            result = subprocess.run(
                [ffmpeg_bin, "-i", filepath],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
            )
            text = result.stderr or ""
            m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
            if not m:
                return None
            width, height = int(m.group(1)), int(m.group(2))
            out = {"width": width, "height": height, "resolution": f"{width}x{height}"}
            m_fps = re.search(r"([\d.]+)\s+fps", text)
            if m_fps:
                out["fps"] = round(float(m_fps.group(1)), 2)
            return out
        except Exception:
            return None
