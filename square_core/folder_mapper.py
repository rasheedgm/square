"""
FolderMapper — full-path, build-by-example tagging for incoming media.

One mechanism only: a studio builds a Path Pattern (see path_pattern.py) by
tagging one real example file's whole path, and that saved template is then
matched against every other file under the root. A root can hold several
patterns, tried in the order they were saved -- first match wins -- so a
delivery with more than one shape (an item missing a folder its siblings
have, a vendor drop nested one level deeper) just gets a second pattern
instead of forcing one template to describe every shape at once.

A lightweight manual per-item media-type tag sits on top as an escape
hatch for the rare file that matches no saved pattern at all -- everything
else (sequence/shot/media name/version, and any custom tag a pattern
captured) is reviewed and fixed, if needed, in the ingest table itself.

Saved as .square_ingest_map.json next to the root so re-opening the same
delivery remembers its patterns and manual tags.
"""

import os
import re
import json
import logging
from pathlib import Path

from square_core.path_pattern import PathPattern, match_first, split_canonical_and_extra

logger = logging.getLogger("SquareFolderMapper")

SIDECAR_FILENAME = ".square_ingest_map.json"


class FolderMapper:
    """
    Manages the ordered Path Pattern list and manual media-type tags for one
    incoming media root folder.

        add_path_pattern(pattern)     — append a saved template (highest index = tried last)
        set_path_patterns(patterns)   — replace the whole ordered list
        match_relative_path(path)     — first pattern (if any) that matches this exact path
        set_media_type(path, type)    — manual per-item override, always wins over a pattern
        build_items(...)              — scan + apply patterns + manual overrides -> IngestSequenceItem list
    """

    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self._path_patterns = []   # list of PathPattern dicts, in try-order
        self._media_types   = {}   # resolved file/folder path str -> media type name (manual, highest precedence)
        self._table_state   = []   # list of serialized item dicts
        self._rep_paths_cache = None

        self.load()

    # ------------------------------------------------------------------
    # Helpers (private)
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_path(path) -> str:
        """
        Normalise a path to a consistent string key.
        - Resolves to absolute path (handles .. and .)
        - Uses os.path.normcase for case-insensitivity on Windows
          (lowercases on Windows, no-op on POSIX)
        """
        return os.path.normcase(os.path.abspath(str(path)))

    def _relative_posix(self, path):
        """Path relative to root, POSIX-style ("/" separated) -- the string a PathPattern matches against."""
        try:
            rel = Path(path).resolve().relative_to(self.root)
        except ValueError:
            return None
        # Path(".").as_posix() == "." (not "") when path IS root itself --
        # e.g. a flat delivery with media files sitting directly in the
        # browsed root folder, no subfolders at all.
        rel_str = rel.as_posix()
        return "" if rel_str == "." else rel_str

    # ------------------------------------------------------------------
    # Path Pattern API
    # ------------------------------------------------------------------

    def get_path_patterns(self):
        return [PathPattern.from_dict(d) for d in self._path_patterns]

    def set_path_patterns(self, patterns):
        """Replace this root's ordered pattern list (PathPattern objects, dicts, or template strings)."""
        self._path_patterns = [self._pattern_to_dict(p) for p in (patterns or [])]
        self._rep_paths_cache = None

    def add_path_pattern(self, pattern):
        self._path_patterns.append(self._pattern_to_dict(pattern))

    def remove_path_pattern(self, index):
        if 0 <= index < len(self._path_patterns):
            self._path_patterns.pop(index)

    def update_path_pattern(self, index, pattern):
        if 0 <= index < len(self._path_patterns):
            self._path_patterns[index] = self._pattern_to_dict(pattern)

    def move_path_pattern(self, from_index, to_index):
        """Reorders the pattern list -- order matters, since the first match wins."""
        n = len(self._path_patterns)
        if 0 <= from_index < n and 0 <= to_index < n and from_index != to_index:
            item = self._path_patterns.pop(from_index)
            self._path_patterns.insert(to_index, item)

    @staticmethod
    def _pattern_to_dict(pattern):
        if isinstance(pattern, str):
            return {"name": pattern, "template": pattern}
        if hasattr(pattern, "to_dict"):
            return pattern.to_dict()
        return dict(pattern)

    def match_relative_path(self, path):
        """Returns (PathPattern, extracted_dict) for the first saved pattern that matches this exact path, else (None, None)."""
        rel = self._relative_posix(path)
        if rel is None:
            return None, None
        return match_first(self.get_path_patterns(), rel)

    def preview_pattern(self, template, limit=8):
        """
        Live-preview for the pattern builder/manager UI: how many of the
        media items currently under root would match this candidate
        template right now, plus up to `limit` (relative_path,
        extracted_dict_or_None) sample rows -- so a mistagged pattern's
        false positives/negatives show up before it's saved, not after.
        """
        pattern = PathPattern(template=template)
        reps = self._representative_paths()
        match_count = 0
        samples = []
        for rel in reps:
            extracted = pattern.match(rel)
            if extracted is not None:
                match_count += 1
            if len(samples) < limit:
                samples.append((rel, extracted))
        return match_count, len(reps), samples

    def _representative_paths(self):
        """Every media item's representative relative path under root, cached for the life of this mapper."""
        if self._rep_paths_cache is None:
            from square_core.plate_scanner import PlateScanner
            items = PlateScanner(self.root).scan()
            paths = []
            for item in items:
                if not item.files:
                    continue
                rel = self._relative_posix(Path(item.files[0]))
                if rel:
                    paths.append(rel)
            self._rep_paths_cache = paths
        return self._rep_paths_cache

    # ------------------------------------------------------------------
    # Manual media-type override -- lightweight escape hatch
    # ------------------------------------------------------------------

    def set_media_type(self, path, type_name):
        """Assign (or clear, with type_name=None) a manual media type label for a specific file/sequence path."""
        key = self._norm_path(path)
        if type_name is None:
            self._media_types.pop(key, None)
        else:
            self._media_types[key] = str(type_name)

    def get_media_type(self, path):
        """Manual tag for this exact path, if any (does not consider pattern matches -- see build_items)."""
        return self._media_types.get(self._norm_path(path))

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def has_map(self) -> bool:
        return bool(self._path_patterns) or bool(self._media_types) or bool(self._table_state)

    def clear_all(self):
        """Clears all path patterns, manual media-type tags, and table state, and removes the sidecar file."""
        self._path_patterns.clear()
        self._media_types.clear()
        self._table_state.clear()
        self._rep_paths_cache = None
        sidecar = self.root / SIDECAR_FILENAME
        if sidecar.exists():
            try:
                sidecar.unlink()
            except Exception as e:
                logger.warning(f"[FolderMapper] Could not remove sidecar file: {e}")

    # ------------------------------------------------------------------
    # Build IngestSequenceItems
    # ------------------------------------------------------------------

    def build_items(self, filter_paths=None):
        from square_core.plate_scanner import PlateScanner

        items = PlateScanner(self.root).scan()
        patterns = self.get_path_patterns()
        for item in items:
            self._apply_patterns_to_item(item, patterns)
            self._apply_manual_media_type(item)

        if filter_paths is not None:
            filtered = []
            for item in items:
                item_paths = {self._norm_path(f) for f in item.files}
                if item.files:
                    item_paths.add(self._norm_path(Path(item.files[0]).parent))
                if item_paths.intersection(filter_paths):
                    filtered.append(item)
            return filtered
        return items

    def _apply_patterns_to_item(self, item, patterns):
        if not patterns or not item.files:
            return
        rel = self._relative_posix(Path(item.files[0]))
        if rel is None:
            return
        _, extracted = match_first(patterns, rel)
        if extracted is None:
            return
        canonical, extra = split_canonical_and_extra(extracted)

        if canonical.get("sequence_code"): item.sequence_code = canonical["sequence_code"]
        if canonical.get("shot_code"):     item.shot_code     = canonical["shot_code"]
        if canonical.get("media_type"):    item.media_type    = canonical["media_type"]
        if canonical.get("media_name"):
            item.media_name = canonical["media_name"]
        if canonical.get("version"):
            m_v = re.search(r"\d+", canonical["version"])
            if m_v:
                item.version = int(m_v.group(0))
        if extra:
            item.extra_tags.update(extra)

    def _apply_manual_media_type(self, item):
        if not item.files:
            return
        candidates = (self._norm_path(item.files[0]), self._norm_path(Path(item.files[0]).parent))
        for key in candidates:
            if key in self._media_types:
                item.media_type = self._media_types[key]
                return

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        try:
            sidecar = self.root / SIDECAR_FILENAME
            data = {
                "path_patterns": self._path_patterns,
                "media_types":   self._media_types,
                "table_state":   self._table_state,
            }
            with open(sidecar, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"[FolderMapper] Could not save sidecar: {e}")

    def load(self) -> bool:
        sidecar = self.root / SIDECAR_FILENAME
        if not sidecar.exists():
            return False
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._path_patterns = data.get("path_patterns", [])
            self._media_types   = data.get("media_types", {})
            self._table_state   = data.get("table_state", [])
            self._rep_paths_cache = None
            return self.has_map()
        except Exception as e:
            logger.warning(f"[FolderMapper] Could not load sidecar: {e}")
            return False

    def save_table_state(self, items):
        """Saves current table items state to sidecar file."""
        table_data = []
        for item in items:
            mname = getattr(item, "media_name", "") or ""
            table_data.append({
                "name": item.name,
                "files": item.files,
                "ext": item.ext,
                "is_video": item.is_video,
                "sequence_code": getattr(item, "sequence_code", "") or "",
                "shot_code": getattr(item, "shot_code", "") or "",
                "media_name": mname,
                "media_type": getattr(item, "media_type", "Plate") or "Plate",
                "version": getattr(item, "version", 1),
                "start_frame": getattr(item, "start_frame", 1001),
                "end_frame": getattr(item, "end_frame", 1001),
                "fps": getattr(item, "fps", 24.0),
                "width": getattr(item, "width", 1920),
                "height": getattr(item, "height", 1080),
                "resolution": getattr(item, "resolution", "1920x1080"),
                "colorspace": getattr(item, "colorspace", "ACEScg"),
                "timecode": getattr(item, "timecode", "01:00:00:00"),
                "extra_tags": dict(getattr(item, "extra_tags", {}) or {}),
            })
        self._table_state = table_data
        self.save()

    def get_saved_table_items(self):
        """Reconstructs IngestSequenceItem objects from saved table state."""
        from square_core.plate_scanner import IngestSequenceItem
        items = []
        for d in self._table_state:
            item = IngestSequenceItem(d["name"], d.get("files", []), d.get("ext", ".exr"), is_video=d.get("is_video", False))
            item.sequence_code = d.get("sequence_code", "")
            item.shot_code     = d.get("shot_code", "")
            item.media_name    = d.get("media_name", "")
            item.media_type    = d.get("media_type", "Plate")
            item.version       = d.get("version", 1)
            item.start_frame   = d.get("start_frame", 1001)
            item.end_frame     = d.get("end_frame", 1001)
            item.fps           = d.get("fps", 24.0)
            item.width         = d.get("width", 1920)
            item.height        = d.get("height", 1080)
            item.resolution    = d.get("resolution", "1920x1080")
            item.colorspace    = d.get("colorspace", "ACEScg")
            item.timecode      = d.get("timecode", "01:00:00:00")
            item.extra_tags    = dict(d.get("extra_tags", {}) or {})
            items.append(item)
        return items
