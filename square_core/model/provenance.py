"""Provenance -- the self-describing "where did this come from / where did it
land" record stamped onto a Kitsu record whenever the pipeline writes media.

Ingest, publish, and delivery all stamp one of these so a later tool can fetch
a file / preview and get back, in the same object, the full trail.

Zou's file / preview / entity models have no columns for any of this and drop
unknown top-level keys on update -- the only writable free-form field is
`data` (JSONB). So this record nests under `data["square"]`, merged on top of
whatever `data` already holds, never replacing it.

Pure value object -- no gazu, no Qt, no other square_core import.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, asdict
from typing import Any

# The single key under a Kitsu record's `data` that pipeline info occupies.
# Namespaced so it never collides with Zou's own fields.
KITSU_DATA_KEY = "square"

SCHEMA_VERSION = 1


@dataclass
class Provenance:
    schema_version: int = SCHEMA_VERSION
    kind: str = ""                    # "ingest" | "publish" | "delivery"

    # where it came from
    source_path: str = ""            # the source folder
    source_sample_file: str = ""     # one real source filename

    # where it landed
    dest_path: str = ""              # the destination folder
    dest_sample_file: str = ""       # one real destination filename

    # what it is
    frame_range: str = ""
    file_count: int = 0
    fps: float | None = None
    resolution: str = ""
    colorspace: str = ""

    # integrity
    checksum: str = ""               # hash of source_sample_file
    checksum_algo: str = "xxh3_64"
    transfer_mode: str = "copy"      # copy | hardlink | symlink

    # studio coordinates
    episode_code: str = ""
    sequence_code: str = ""
    shot_code: str = ""
    asset_code: str = ""
    task_type: str = ""
    output_type: str = ""            # media type on an ingest
    representation: str = ""         # exr | mov | jpg ...
    name: str = "main"
    version: int = 1

    # who / when
    recorded_at: str = ""            # ISO 8601, UTC
    recorded_by: str = ""            # kitsu user email
    batch_id: str = ""

    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_kitsu_data(self, existing_data: dict | None = None) -> dict[str, Any]:
        """The full `data` dict to write back: `existing_data` with this record
        merged in under KITSU_DATA_KEY. Pass the record's current `data` so
        nothing Zou wrote is lost."""
        merged = dict(existing_data or {})
        merged[KITSU_DATA_KEY] = self.to_dict()
        return merged

    @classmethod
    def _known(cls) -> set[str]:
        return {f.name for f in dataclasses.fields(cls)}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Provenance | None":
        if not isinstance(data, dict) or not data:
            return None
        known = cls._known()
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_kitsu_data(cls, record_data: dict | None) -> "Provenance | None":
        if not isinstance(record_data, dict):
            return None
        return cls.from_dict(record_data.get(KITSU_DATA_KEY))
