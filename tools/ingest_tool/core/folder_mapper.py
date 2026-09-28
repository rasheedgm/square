"""
FolderMapper — full-path, build-by-example tagging for one incoming media root.

One mechanism: a studio builds a Path Pattern (see path_pattern.py) by tagging
one real example file's whole path, and that saved template is matched against
every other file under the root. A root can hold several patterns, tried in the
order they were added — first match wins — so a delivery with more than one
shape just gets a second pattern.

Patterns are the only way an item gets tagged; whatever a pattern doesn't
cover (sequence / shot / media name / version, any custom tag) is reviewed and
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

# Media-info fields a Path Pattern can supply, either as a tag on a piece of
# the path (a folder called "2048x1152", a filename ending "_25fps" -- tag the
# piece with the name fps / resolution / colorspace) or as a typed default for
# a delivery that never spells it out. Either way it is a fallback: the file's
# own metadata wins when it can be read.
METADATA_DEFAULT_FIELDS = ("fps", "resolution", "colorspace")

_FPS_RE = re.compile(r"\d+(?:[.,]\d+)?")
_RES_RE = re.compile(r"(\d{3,5})\s*[xX\u00d7_]\s*(\d{3,5})")


def parse_fps(value):
    """'25', '25fps', '23.976', '23,976' -> float; None if there is no number."""
    m = _FPS_RE.search(str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def parse_resolution(value):
    """'2048x1152', '2048X1152', '2048_1152', '2048 x 1152' -> '2048x1152'; None otherwise."""
    m = _RES_RE.search(str(value))
    return f"{int(m.group(1))}x{int(m.group(2))}" if m else None


class FolderMapper:
    """
        add_path_pattern(pattern)     — append a template (tried last)
        set_path_patterns(patterns)   — replace the whole ordered list
        match_relative_path(path)     — first pattern (if any) that matches this exact path
        build_items(...)              — scan + apply patterns -> IngestSequenceItem list
    """

    def __init__(self, root_path):
        self._given_root = Path(root_path)     # as the tree spells it (Z:\...)
        self.root = self._given_root.resolve() # as the scanner walks it (may be \\server\...)
        self._path_patterns = []   # list of PathPattern dicts, in try-order
        self._rep_paths_cache = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_path(path) -> str:
        p = str(path)
        # long-path prefixes the scanner adds on Windows
        if p.startswith("\\\\?\\UNC\\"):
            p = "\\\\" + p[8:]
        elif p.startswith("\\\\?\\"):
            p = p[4:]
        return os.path.normcase(os.path.abspath(p))

    def _to_scan_space(self, paths):
        """Paths the tree hands over are spelled from the root as it was given
        (`Z:\\jobs\\...`, a mapped or substituted drive) but the scanner walks
        the *resolved* root (`\\\\server\\share\\...`), so a straight comparison
        never matches and a selected row loads nothing. Re-spell them from the
        resolved root (the originals are kept too)."""
        if not paths:
            return paths
        given = self._norm_path(self._given_root).rstrip(os.sep)
        real = self._norm_path(self.root).rstrip(os.sep)
        if given == real:
            return paths
        out = set(paths)
        for p in paths:
            n = self._norm_path(p)
            if n == given or n.startswith(given + os.sep):
                out.add(real + n[len(given):])
        return out

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
    # Query
    # ------------------------------------------------------------------

    def has_map(self) -> bool:
        return bool(self._path_patterns)

    def clear_all(self):
        self._path_patterns.clear()
        self._rep_paths_cache = None

    # ------------------------------------------------------------------
    # Build IngestSequenceItems
    # ------------------------------------------------------------------

    def build_items(self, filter_paths=None, tagged_only=False, explicit_paths=None):
        """Scan the root into items, applying the Path Patterns.
        `filter_paths` restricts to what the user picked in the tree.
        `tagged_only` keeps only items a pattern matched -- what a bare "Load"
        (nothing selected) or a selected folder should bring in, rather than
        every stray file under it that nothing described. `explicit_paths` are
        files the user picked one by one: their items are kept even when
        untagged."""
        from square_core.media.scanner import PlateScanner

        filter_paths = self._to_scan_space(filter_paths)
        explicit_paths = self._to_scan_space(explicit_paths)
        items = PlateScanner(self.root).scan()
        patterns = self.get_path_patterns()
        tagged = []
        for item in items:
            if self._apply_patterns_to_item(item, patterns):
                tagged.append(item)
        if tagged_only:
            if explicit_paths:
                tagged_ids = {id(i) for i in tagged}
                items = [i for i in items if id(i) in tagged_ids
                         or {self._norm_path(f) for f in i.files} & set(explicit_paths)]
            else:
                items = tagged

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

    def _apply_patterns_to_item(self, item, patterns) -> bool:
        """Apply the first matching pattern's tags to the item. True if one matched."""
        if not patterns or not item.files:
            return False
        rel = self._relative_posix(Path(item.files[0]))
        if rel is None:
            return False
        _, extracted = match_first(patterns, rel)
        if extracted is None:
            return False
        canonical, extra = split_canonical_and_extra(extracted)

        if canonical.get("sequence_code"): item.sequence_code = canonical["sequence_code"]
        if canonical.get("shot_code"):     item.shot_code     = canonical["shot_code"]
        if canonical.get("media_type"):    item.media_type    = canonical["media_type"]
        if canonical.get("media_name"):    item.media_name    = canonical["media_name"]
        if canonical.get("version"):
            m_v = re.search(r"\d+", canonical["version"])
            if m_v:
                item.version = int(m_v.group(0))

        for f in METADATA_DEFAULT_FIELDS:
            if f not in extra:
                continue
            value = extra.pop(f)
            if f == "fps":
                parsed = parse_fps(value)
            elif f == "resolution":
                parsed = parse_resolution(value)
            else:
                parsed = str(value).strip() or None
            if parsed is None:
                # not a value we can read (e.g. a resolution folder called
                # "UHD") -- keep it visible as a plain tag rather than lose it
                extra[f] = value
                continue
            setattr(item, f, parsed)
            item.metadata_defaulted.add(f)

        if extra:
            item.extra_tags.update(extra)
        return True
