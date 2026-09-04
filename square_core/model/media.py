"""MediaInfo -- resolution / fps / colorspace / frame range for a piece of
media, plus which of those were actually read from a file header vs left
unknown.

`square_core.media.metadata` fills this in. The `verified` map is the whole
point: a field that an extractor could not read stays out of `verified` (or
maps to False), so a tool can block the row for the user to set it rather
than shipping a guessed colorspace. Never silently default.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    resolution: str = ""              # "3840x2160"
    fps: float | None = None
    colorspace: str = ""
    frame_in: int | None = None
    frame_out: int | None = None
    missing_frames: list = field(default_factory=list)
    timecode: str = ""

    # field name -> was it read from the file (True) or is it a guess / unset (False)
    verified: dict = field(default_factory=dict)

    def is_verified(self, field_name: str) -> bool:
        return bool(self.verified.get(field_name))

    @property
    def frame_count(self) -> int:
        if self.frame_in is None or self.frame_out is None:
            return 0
        return self.frame_out - self.frame_in + 1

    def range_label(self) -> str:
        if self.frame_in is None or self.frame_out is None:
            return ""
        n = self.frame_count
        base = f"{self.frame_in}-{self.frame_out} ({n} frame{'s' if n != 1 else ''})"
        if self.missing_frames:
            base += f", {len(self.missing_frames)} missing"
        return base

    def missing_required(self, required=("resolution", "fps", "colorspace")) -> list:
        """Required fields that are empty or unverified -- the row is Needs-Info
        until these are set."""
        out = []
        for name in required:
            value = getattr(self, name, None)
            if not value or not self.is_verified(name):
                out.append(name)
        return out
