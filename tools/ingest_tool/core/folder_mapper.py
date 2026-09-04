"""
FolderMapper — full-path, build-by-example tagging for one incoming media root.

One mechanism: a studio builds a Path Pattern (see path_pattern.py) by tagging
one real example file's whole path, and that saved template is matched against
every other file under the root. A root can hold several patterns, tried in the
order they were added — first match wins — so a delivery with more than one
shape just gets a second pattern.

A lightweight manual per-item media-type tag sits on top as an escape hatch for
the rare file that matches no saved pattern; everything else (sequence / shot /
media name / version, and any custom tag a pattern captured) is reviewed and
fixed in the ingest table itself.

This object is **in-memory only**. It used to persist to a hidden
`.square_ingest_map.json` sidecar next to the root; that idea is now the ingest
session file (`*.sqingest.json`), which the user names and places, and which the
tool re-applies on resume. Reusable pattern lists are saved as named Ingest
Presets in the studio config.
"""

import os
import re
import logging
from pathlib import Path

from square_core.paths.path_pattern import PathPattern, match_first, split_canonical_and_extra

logger = logging.getLogger("SquareFolderMapper")


class FolderMapper:
    """
        add_path_pattern(pattern)     — append a template (tried last)
        set_path_patterns(patterns)   — replace the whole ordered list
        match_relative_path(path)     — first pattern (if any) that matches this exact path
        set_media_type(path, type)    — manual per-item override, always wins over a pattern
        build_items(...)              — scan + apply patterns + manual overrides -> IngestSequenceItem list
    """

    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self._path_patterns = []   # list of PathPattern dicts, in try-order
        self._media_types = {}     # resolved file/folder path str -> media type name (manual)
        self._rep_paths_cache = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_path(path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def _relative_posix(self, path):
        """Path relative to root, POSIX-style — the string a PathPattern matches against."""
        try:
            rel = Path(path).resolve().relative_to(self.root)
        except ValueError:
            return None
        rel_str = rel.as_posix()
        return "" if rel_str == "." else rel_str

    # ------------------------------------------------------------------
    # Path Pattern API
    # ------------------------------------------------------------------

    def get_path_patterns(self):
        return [PathPattern.from_dict(d) for d in self._path_patterns]

    def set_path_patterns(self, patterns):
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
        rel = self._relative_posix(path)
        if rel is None:
            return None, None
        return match_first(self.get_path_patterns(), rel)

    def preview_pattern(self, template, limit=8):
        """How many media items under root would match this candidate template, plus samples."""
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
        if self._rep_paths_cache is None:
            from square_core.media.scanner import PlateScanner
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
    # Manual media-type override
    # ------------------------------------------------------------------

    def set_media_type(self, path, type_name):
        key = self._norm_path(path)
        if type_name is None:
            self._media_types.pop(key, None)
        else:
            self._media_types[key] = str(type_name)

    def get_media_type(self, path):
        return self._media_types.get(self._norm_path(path))

    def get_media_types(self) -> dict:
        """The manual overrides as a plain {path: type} dict — for the session file."""
        return dict(self._media_types)

    def set_media_types(self, mapping: dict) -> None:
        self._media_types = {str(k): str(v) for k, v in (mapping or {}).items()}

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def has_map(self) -> bool:
        return bool(self._path_patterns) or bool(self._media_types)

    def clear_all(self):
        self._path_patterns.clear()
        self._media_types.clear()
        self._rep_paths_cache = None

    # ------------------------------------------------------------------
    # Build IngestSequenceItems
    # ------------------------------------------------------------------

    def build_items(self, filter_paths=None):
        from square_core.media.scanner import PlateScanner

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
        if canonical.get("media_name"):    item.media_name    = canonical["media_name"]
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
