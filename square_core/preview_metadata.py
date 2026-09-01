"""
PreviewMetadata -- the self-describing record stamped onto every Kitsu
preview file at ingest, so a downstream review or delivery tool can fetch a
preview by task+revision and get back, in that same object, exactly where
the real media came from and where it now lives.

Why it is shaped this way:

- Zou's preview-file model has no columns for any of these fields and
  silently drops unknown top-level keys on `PUT /data/preview-files/<id>`
  (confirmed against a live server). The only writable free-form field is
  `data` (JSONB), which Zou itself also fills with the media dimensions on
  upload. So this record is written into `data["square_ingest"]`, merged on
  top of whatever `data` already holds -- never replacing it.

- The exact same object is also written into the shot-data version ledger
  entry and into the NAS ingest ledger row, so disk, Kitsu, and the ledger
  all carry one identical answer that can be cross-checked.

This module has no dependency on gazu, Qt, or the rest of square_core -- it
is a plain serializable value object.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, asdict
from typing import Any

# The single key under preview_file["data"] that this record occupies.
# Namespaced so it can never collide with Zou's own media metadata
# (original_width, original_height, original_duration, ...).
KITSU_DATA_KEY = "square_ingest"

# Bump when the field set changes in a way a reader must know about.
SCHEMA_VERSION = 1


@dataclass
class PreviewMetadata:
    schema_version: int = SCHEMA_VERSION

    # Where the media was delivered from.
    source_path: str = ""          # the delivered folder
    source_sample_file: str = ""   # one real delivered filename (first frame / the video)

    # Where the media now lives after ingest.
    nas_path: str = ""             # the ingested destination folder
    nas_sample_file: str = ""      # one real ingested filename

    # What the media is.
    frame_range: str = ""          # e.g. "1001-1096 (96 frames)" or "1 (Video File)"
    file_count: int = 0
    fps: float | None = None
    resolution: str = ""
    colorspace: str = ""

    # Integrity.
    checksum: str = ""             # hash of source_sample_file
    checksum_algo: str = "xxh3_64"
    transfer_mode: str = "copy"    # copy | hardlink | symlink

    # Studio schema coordinates.
    sequence_code: str = ""
    shot_code: str = ""
    media_type: str = ""
    media_name: str = ""
    version: int = 1

    # Provenance.
    ingested_at: str = ""          # ISO 8601, UTC, e.g. "2026-09-01T12:00:00Z"
    ingested_by: str = ""          # kitsu user email
    batch_id: str = ""             # the ingest batch this belonged to

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Flat dict of every field -- the payload that goes under data['square_ingest']."""
        return asdict(self)

    def to_kitsu_data(self, existing_data: dict | None = None) -> dict[str, Any]:
        """
        The full `data` dict to PUT back onto a preview file: a copy of
        whatever `data` currently holds (Zou's own media metadata included)
        with this record merged in under KITSU_DATA_KEY. Pass the preview
        file's current `data` as `existing_data` so nothing Zou wrote is
        lost.
        """
        merged = dict(existing_data or {})
        merged[KITSU_DATA_KEY] = self.to_dict()
        return merged

    @classmethod
    def _known_fields(cls) -> set[str]:
        return {f.name for f in dataclasses.fields(cls)}

    @classmethod
    def from_dict(cls, data: dict | None) -> "PreviewMetadata | None":
        """
        Build from a flat dict (the payload stored under data['square_ingest']).
        Tolerant: unknown keys are ignored, missing keys take their default,
        so an older or newer record still round-trips as far as it can.
        Returns None for a falsy / non-dict input.
        """
        if not isinstance(data, dict) or not data:
            return None
        known = cls._known_fields()
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_kitsu_data(cls, preview_data: dict | None) -> "PreviewMetadata | None":
        """
        Build from a preview file's whole `data` dict -- pulls out the
        KITSU_DATA_KEY sub-dict and defers to from_dict(). Returns None if
        this preview carries no square_ingest record.
        """
        if not isinstance(preview_data, dict):
            return None
        return cls.from_dict(preview_data.get(KITSU_DATA_KEY))
