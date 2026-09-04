"""square_core.media -- format-aware media helpers.

- `metadata.MetadataExtractor` -- OIIO / Pillow / ffprobe header reads
- `scanner.PlateScanner` -- image-sequence + video discovery, frame parsing
- `proxy.make_proxy` -- ffmpeg review proxy from a frame range or a video
"""

from __future__ import annotations

from .metadata import MetadataExtractor
from .scanner import PlateScanner, IngestSequenceItem
from .proxy import make_proxy, ProxyError, ffmpeg_bin

__all__ = [
    "MetadataExtractor",
    "PlateScanner",
    "IngestSequenceItem",
    "make_proxy",
    "ProxyError",
    "ffmpeg_bin",
]
