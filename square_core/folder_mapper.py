"""
FolderMapper — depth-based, per-folder, and pattern-based tagging for incoming media.

Four tagging primitives, from most general to most specific (a more specific
rule always wins over a more general one):
  1. depth-wide     — all folders at depth N share a level
  2. pattern rule    — anything (folder or file) whose name matches a saved
                        regex/glob gets a level / media type / token-preset
                        parse, at any depth (or a chosen depth range).
                        Reusable across incoming batches via Ingest Presets.
  3. per-folder override — one specific folder gets its own level
  4. per-item tag    — a media type or a Tag-Name-Tokens rule assigned
                        directly to one sequence/file

Saved as .square_ingest_map.json next to the root so re-opening the same
delivery remembers all tags (including which pattern rules were active).
"""

import os
import re
import json
import logging
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger("SquareFolderMapper")

# Level constants
LEVEL_SEQ        = "seq"
LEVEL_SHOT       = "shot"
LEVEL_MEDIA_NAME = "media_name"
LEVEL_MEDIA_TYPE = "media_type"
LEVEL_VERSION    = "version"
LEVEL_NONE       = None

VALID_LEVELS = (LEVEL_SEQ, LEVEL_SHOT, LEVEL_MEDIA_NAME, LEVEL_MEDIA_TYPE, LEVEL_VERSION)

SUPPORTED_IMAGE_EXTS = {".exr", ".dpx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".m4v"}

SIDECAR_FILENAME = ".square_ingest_map.json"

# Pattern-rule action kinds
ACTION_LEVEL        = "level"
ACTION_MEDIA_TYPE    = "media_type"
ACTION_TOKEN_PRESET  = "token_preset"

# ------------------------------------------------------------------
# Auto-detection regexes (search in compound names too)
# ------------------------------------------------------------------
RE_SEQ_PART   = re.compile(r"(?i)(?:^|[_\-])(?:SQ|seq|reel|ep)[_\-]?(\d{2,4})(?:[_\-]|$)")
RE_SHOT_PART  = re.compile(r"(?i)(?:^|[_\-])(?:SH|shot|sc)[_\-]?(\d{2,4})(?:[_\-]|$)")
RE_MEDIA_PART = re.compile(r"(?i)(?:^|[_\-])(?:PL|plate|plates?|img|render|raw|media)[_\-]?(\w*)(?:[_\-]|$)")


def _detect_level(folder_name: str):
    """Return the single dominant level for a folder name, or None."""
    has_seq   = bool(RE_SEQ_PART.search(folder_name))
    has_shot  = bool(RE_SHOT_PART.search(folder_name))
    has_media = bool(RE_MEDIA_PART.search(folder_name))
    if has_media and has_shot:
        return LEVEL_MEDIA_NAME
    if has_shot:
        return LEVEL_SHOT
    if has_seq:
        return LEVEL_SEQ
    return None


def _canonicalize_level(level):
    clean_level = level.lower() if isinstance(level, str) else level
    if clean_level in ("plate", "media"):
        clean_level = LEVEL_MEDIA_NAME
    return clean_level


def _glob_to_regex(glob_pat: str) -> str:
    import fnmatch
    return fnmatch.translate(glob_pat)


class PatternRule:
    """
    Matches folders and/or files anywhere under an incoming root by name,
    independent of depth (optionally bounded to a depth range), and applies
    one action to every match:
      - action="level"         -> tag the matched folder as a level (SEQ/SHOT/...)
      - action="media_type"    -> tag the matched file/sequence as a media type
      - action="token_preset"  -> parse the matched name with a saved token
                                   rule and take seq/shot/media/version/type
                                   from the result

    Lets one saved rule cover an entire irregular delivery ("tag anything
    that looks like SHxxxx, wherever it occurs") instead of tagging every
    folder by hand, and — saved inside an Ingest Preset — is reusable
    across future incoming batches with the same naming convention.
    """

    def __init__(self, name="Custom Pattern", pattern="", is_regex=True,
                 target="folder", min_depth=None, max_depth=None,
                 action=ACTION_LEVEL, level=None, media_type=None, token_preset_name=None):
        self.name = name
        self.pattern = pattern or ""
        self.is_regex = is_regex
        self.target = target if target in ("folder", "file", "both") else "folder"
        self.min_depth = min_depth
        self.max_depth = max_depth
        self.action = action if action in (ACTION_LEVEL, ACTION_MEDIA_TYPE, ACTION_TOKEN_PRESET) else ACTION_LEVEL
        self.level = _canonicalize_level(level) if action == ACTION_LEVEL else None
        self.media_type = media_type
        self.token_preset_name = token_preset_name
        self._compiled = None

    def to_dict(self):
        return {
            "name": self.name,
            "pattern": self.pattern,
            "is_regex": self.is_regex,
            "target": self.target,
            "min_depth": self.min_depth,
            "max_depth": self.max_depth,
            "action": self.action,
            "level": self.level,
            "media_type": self.media_type,
            "token_preset_name": self.token_preset_name,
        }

    @classmethod
    def from_dict(cls, data):
        if not data:
            return cls()
        return cls(
            name=data.get("name", "Custom Pattern"),
            pattern=data.get("pattern", ""),
            is_regex=data.get("is_regex", True),
            target=data.get("target", "folder"),
            min_depth=data.get("min_depth"),
            max_depth=data.get("max_depth"),
            action=data.get("action", ACTION_LEVEL),
            level=data.get("level"),
            media_type=data.get("media_type"),
            token_preset_name=data.get("token_preset_name"),
        )

    def _regex(self):
        if self._compiled is None:
            pat = self.pattern if self.is_regex else _glob_to_regex(self.pattern)
            self._compiled = re.compile(pat, re.IGNORECASE)
        return self._compiled

    def matches(self, name: str, depth: int, is_folder: bool) -> bool:
        if not self.pattern:
            return False
        if self.target == "folder" and not is_folder:
            return False
        if self.target == "file" and is_folder:
            return False
        if self.min_depth is not None and depth < self.min_depth:
            return False
        if self.max_depth is not None and depth > self.max_depth:
            return False
        try:
            return bool(self._regex().search(name))
        except re.error:
            return False


class FolderMapper:
    """
    Manages folder-depth-to-level tagging for an incoming media root folder.

    depth 0 = root folder itself (never tagged)
    depth 1 = first level of subfolders

    Tagging modes:
        set_level(depth, level)           — tags the whole depth
        set_level_for_folder(path, level) — overrides one specific folder
        set_pattern_rules(rules)          — tags anything matching a pattern, any depth
        set_media_type(path, type_name)   — tags one sequence/file
        set_token_rule(path, rule)        — parses one sequence/file name into fields
    """

    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self._depth_map        = {}   # depth (int) -> level str
        self._folder_overrides = {}   # resolved path str -> level str (manual, highest precedence)
        self._media_types      = {}   # resolved file path str -> media type name (manual)
        self._token_rules      = {}   # resolved file/folder path str -> token rule dict (manual)
        self._item_overrides   = {}   # resolved file/folder path str -> item metadata overrides dict (manual)
        self._table_state      = []   # list of serialized item dicts

        self._pattern_rules              = []   # list of PatternRule dicts, active for this root
        self._pattern_applied_folder_levels = {}  # resolved path -> level (lower precedence than _folder_overrides)
        self._pattern_applied_media_types   = {}  # resolved path -> media type (lower precedence than _media_types)
        self._pattern_applied_item_overrides = {} # resolved path -> overrides dict (lower precedence than _item_overrides)

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

    # ------------------------------------------------------------------
    # Tag API — depth-wide
    # ------------------------------------------------------------------

    def set_level(self, depth: int, level):
        """Tag all folders at this depth. Pass None/LEVEL_NONE to clear."""
        clean_level = _canonicalize_level(level)
        if clean_level not in VALID_LEVELS + (LEVEL_NONE,):
            raise ValueError(f"Invalid level: {level!r}")
        if clean_level is None:
            self._depth_map.pop(depth, None)
        else:
            self._depth_map[depth] = clean_level

    def get_level(self, depth: int):
        return self._depth_map.get(depth, LEVEL_NONE)

    def apply_depth_token_preset(self, depth: int, token_rule):
        """
        For every folder OR file currently at `depth` under root, parse its
        own name with `token_rule` and store the parsed fields as an item
        override — used when an Ingest Preset's depth rule is type ==
        "token_preset" (one name yields several fields — seq/shot/media/
        version/type — in a single parse). Depth may land on a folder
        (e.g. "SQ010_SH0100") or directly on a file (e.g. a combined
        "SHOT0100_PL01_v001.mov" one level deeper than its sequence folder).
        """
        from square_core.token_parser import TokenRule
        t_rule = TokenRule.from_dict(token_rule) if isinstance(token_rule, dict) else token_rule
        if not self.root.exists():
            return
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            folder = Path(dirpath).resolve()
            folder_depth = self.depth_of_path(folder)

            if folder_depth == depth:
                self.set_token_rule(folder, t_rule)

            if folder_depth == depth - 1:
                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    self.set_token_rule(folder / filename, t_rule)

    def clear_all_levels(self):
        """Clears all tags (depth, folder, pattern, media type, token rules), table state, and removes the sidecar file."""
        self._depth_map.clear()
        self._folder_overrides.clear()
        self._media_types.clear()
        self._token_rules.clear()
        self._item_overrides.clear()
        self._pattern_rules.clear()
        self._pattern_applied_folder_levels.clear()
        self._pattern_applied_media_types.clear()
        self._pattern_applied_item_overrides.clear()
        self._table_state.clear()
        sidecar = self.root / SIDECAR_FILENAME
        if sidecar.exists():
            try:
                sidecar.unlink()
            except Exception as e:
                logger.warning(f"[FolderMapper] Could not remove sidecar file: {e}")

    # ------------------------------------------------------------------
    # Tag API — per-folder override
    # ------------------------------------------------------------------

    def set_level_for_folder(self, path, level):
        """Tag one specific folder. Pass None to clear the override."""
        key = self._norm_path(path)
        clean_level = _canonicalize_level(level)

        if clean_level is None:
            self._folder_overrides.pop(key, None)
        else:
            if clean_level not in VALID_LEVELS:
                raise ValueError(f"Invalid level: {level!r}")
            self._folder_overrides[key] = clean_level

    def get_level_for_folder(self, path):
        """Return the effective level for this exact folder: manual override first, then pattern-rule match."""
        key = self._norm_path(path)
        return self._folder_overrides.get(key) or self._pattern_applied_folder_levels.get(key)

    # ------------------------------------------------------------------
    # Tag API — pattern rules (any depth, reusable)
    # ------------------------------------------------------------------

    def set_pattern_rules(self, rules):
        """Replace this root's active pattern rules (PatternRule objects or dicts) and re-apply them."""
        self._pattern_rules = [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in (rules or [])]
        self.apply_pattern_rules()

    def get_pattern_rules(self):
        return [PatternRule.from_dict(d) for d in self._pattern_rules]

    def add_pattern_rule(self, rule):
        self._pattern_rules.append(rule.to_dict() if hasattr(rule, "to_dict") else dict(rule))
        self.apply_pattern_rules()

    def remove_pattern_rule(self, index):
        if 0 <= index < len(self._pattern_rules):
            self._pattern_rules.pop(index)
            self.apply_pattern_rules()

    def count_pattern_matches(self, rule) -> int:
        """Number of folders/files under root that would currently match a single rule (for live preview in the tagging UI)."""
        if not self.root.exists() or not rule.pattern:
            return 0
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            folder = Path(dirpath).resolve()
            depth = self.depth_of_path(folder)
            if depth <= 0:
                continue
            if rule.target in ("folder", "both") and rule.matches(folder.name, depth, True):
                count += 1
            if rule.target in ("file", "both"):
                for fn in filenames:
                    if not fn.startswith('.') and rule.matches(fn, depth, False):
                        count += 1
        return count

    def apply_pattern_rules(self):
        """
        Re-evaluate all active pattern rules against the current tree and
        materialize matches into the pattern-applied layers. These sit
        beneath the manual per-folder/per-item tags, which always win —
        so one saved rule can cover an entire delivery while exceptions
        are still fixable by hand.
        """
        self._pattern_applied_folder_levels.clear()
        self._pattern_applied_media_types.clear()
        self._pattern_applied_item_overrides.clear()

        if not self._pattern_rules or not self.root.exists():
            return

        rules = self.get_pattern_rules()
        level_rules = [r for r in rules if r.action == ACTION_LEVEL and r.level]
        leaf_rules  = [r for r in rules if r.action in (ACTION_MEDIA_TYPE, ACTION_TOKEN_PRESET)]

        cfg = None
        if any(r.action == ACTION_TOKEN_PRESET for r in leaf_rules):
            from square_core.config import StudioConfig
            cfg = StudioConfig()

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            folder = Path(dirpath).resolve()
            depth = self.depth_of_path(folder)
            if depth <= 0:
                continue

            if level_rules:
                for rule in level_rules:
                    if rule.matches(folder.name, depth, is_folder=True):
                        self._pattern_applied_folder_levels[self._norm_path(folder)] = rule.level
                        break

            if leaf_rules:
                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    fkey = self._norm_path(folder / filename)
                    for rule in leaf_rules:
                        if not rule.matches(filename, depth, is_folder=False):
                            continue
                        if rule.action == ACTION_MEDIA_TYPE and rule.media_type:
                            self._pattern_applied_media_types[fkey] = rule.media_type
                        elif rule.action == ACTION_TOKEN_PRESET and cfg is not None:
                            self._apply_token_preset_to_key(fkey, filename, rule.token_preset_name, cfg)
                        break

    def _apply_token_preset_to_key(self, fkey, name, preset_name, cfg):
        from square_core.token_parser import TokenRule, parse_string_with_token_rule
        preset = cfg.token_presets.get(preset_name)
        if not preset:
            return
        parsed = parse_string_with_token_rule(name, TokenRule.from_dict(preset))
        override = {k: v for k, v in {
            "sequence_code": parsed.get("sequence_code"),
            "shot_code":     parsed.get("shot_code"),
            "media_name":    parsed.get("media_name"),
            "version":       parsed.get("version"),
            "media_type":    parsed.get("media_type"),
        }.items() if v}
        if override:
            self._pattern_applied_item_overrides[fkey] = override
        if parsed.get("media_type"):
            self._pattern_applied_media_types[fkey] = parsed["media_type"]

    # ------------------------------------------------------------------
    # Media Type & Token Rule API — for sequences/files
    # ------------------------------------------------------------------

    MEDIA_TYPES = ["Plate", "Ref", "BG Plate", "Comp Render", "Precomp"]

    def set_media_type(self, path, type_name):
        """Assign a media type label to a specific file/sequence path."""
        key = self._norm_path(path)
        if type_name is None:
            self._media_types.pop(key, None)
            self._token_rules.pop(key, None)
            self._item_overrides.pop(key, None)
        else:
            self._media_types[key] = str(type_name)

    def get_media_type(self, path) -> str:
        """Return the effective media type for a path: manual tag first, then pattern-rule match."""
        key = self._norm_path(path)
        return self._effective_media_type_by_key(key)

    def _effective_media_type_by_key(self, key):
        return self._media_types.get(key) or self._pattern_applied_media_types.get(key)

    def _effective_item_override_by_key(self, key):
        return self._item_overrides.get(key) or self._pattern_applied_item_overrides.get(key)

    def set_token_rule(self, path, rule):
        """Store custom TokenRule and item parse override for a path."""
        key = self._norm_path(path)
        rule_dict = rule.to_dict() if hasattr(rule, "to_dict") else rule
        self._token_rules[key] = rule_dict

        from square_core.token_parser import parse_string_with_token_rule, TokenRule
        t_rule = TokenRule.from_dict(rule_dict) if isinstance(rule_dict, dict) else rule
        p_name = Path(path).name
        parsed = parse_string_with_token_rule(p_name, t_rule)

        override = {}
        if parsed.get("sequence_code"): override["sequence_code"] = parsed["sequence_code"]
        if parsed.get("shot_code"):     override["shot_code"]     = parsed["shot_code"]
        if parsed.get("media_name"):    override["media_name"]    = parsed["media_name"]
        if parsed.get("plate_name"):    override["plate_name"]    = parsed["plate_name"]
        if parsed.get("version"):       override["version"]       = parsed["version"]
        if parsed.get("media_type"):    override["media_type"]    = parsed["media_type"]

        if override:
            self._item_overrides[key] = override
        if parsed.get("media_type"):
            self._media_types[key] = parsed["media_type"]

    def get_token_rule(self, path):
        key = self._norm_path(path)
        return self._token_rules.get(key)

    def get_effective_item_override(self, path):
        """Return the effective parsed-field override for a path: manual tag first, then pattern-rule match."""
        return self._effective_item_override_by_key(self._norm_path(path))

    def clear_token_rule(self, path):
        """Clears custom token rule, item overrides, and media type for a specific path."""
        key = self._norm_path(path)
        self._token_rules.pop(key, None)
        self._item_overrides.pop(key, None)
        self._media_types.pop(key, None)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def depth_of_path(self, path) -> int:
        try:
            rel = Path(path).resolve().relative_to(self.root)
            return len(rel.parts)
        except ValueError:
            return -1

    def has_map(self) -> bool:
        return (
            bool(self._depth_map) or
            bool(self._folder_overrides) or
            bool(self._media_types) or
            bool(self._token_rules) or
            bool(self._item_overrides) or
            bool(self._pattern_rules) or
            bool(self._pattern_applied_folder_levels) or
            bool(self._pattern_applied_media_types) or
            bool(self._pattern_applied_item_overrides)
        )

    def level_of_path(self, path):
        """Return effective level for a path (folder override / pattern match first, then depth_map)."""
        lvl = self.get_level_for_folder(path)
        if lvl:
            return lvl
        depth = self.depth_of_path(path)
        if depth < 0:
            return LEVEL_NONE
        return self._depth_map.get(depth, LEVEL_NONE)

    def ancestor_levels(self, path) -> list:
        """
        Return list of (level, path) for all tagged ancestors of `path`,
        from shallowest to deepest. Used for smart context menu logic.
        """
        result = []
        try:
            p = Path(path).resolve()
            parts = p.relative_to(self.root).parts
        except ValueError:
            return result

        # Walk from root down to (but not including) path itself
        current = self.root
        for part in parts[:-1]:
            current = current / part
            lvl = self.level_of_path(current)
            if lvl:
                result.append((lvl, current))
        return result

    # ------------------------------------------------------------------
    # Auto-Detection
    # ------------------------------------------------------------------

    def auto_detect(self):
        """
        Walk root and vote on which depth is SEQ / SHOT / PLATE.
        Returns True if at least one level detected.
        """
        votes = defaultdict(lambda: defaultdict(int))

        for dirpath, dirnames, _ in os.walk(self.root):
            # Skip hidden dirs
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            folder = Path(dirpath).resolve()
            depth  = self.depth_of_path(folder)
            if depth <= 0:
                continue
            lvl = _detect_level(folder.name)
            if lvl:
                votes[depth][lvl] += 1

        self._depth_map.clear()
        for depth, dv in sorted(votes.items()):
            best  = max(dv, key=dv.get)
            total = sum(dv.values())
            conf  = dv[best] / total
            if conf >= 0.5:
                self._depth_map[depth] = best
                logger.info(f"[FolderMapper] depth {depth} → {best} ({conf:.0%})")

        return bool(self._depth_map)

    # ------------------------------------------------------------------
    # Build IngestSequenceItems
    # ------------------------------------------------------------------

    def build_items(self, filter_paths=None):
        from square_core.plate_scanner import IngestSequenceItem

        if not self.has_map():
            from square_core.plate_scanner import PlateScanner
            items = PlateScanner(self.root).scan()

            for item in items:
                vpath   = self._norm_path(Path(item.files[0])) if item.files else self._norm_path(item.name)
                vfolder = self._norm_path(Path(item.files[0]).parent if item.files else "")
                ov = self._effective_item_override_by_key(vpath) or self._effective_item_override_by_key(vfolder)
                if ov:
                    if ov.get("sequence_code"): item.sequence_code = ov["sequence_code"]
                    if ov.get("shot_code"):     item.shot_code     = ov["shot_code"]
                    if ov.get("media_name"):    item.media_name    = ov["media_name"]; item.plate_name = ov["media_name"]
                    if ov.get("version"):       item.version       = ov["version"]
                    if ov.get("media_type"):    item.media_type    = ov["media_type"]

                mtype = self._effective_media_type_by_key(vpath) or self._effective_media_type_by_key(vfolder)
                if mtype:
                    item.media_type = mtype

            if filter_paths:
                filtered = []
                for item in items:
                    item_paths = {self._norm_path(f) for f in item.files}
                    item_paths.add(self._norm_path(Path(item.files[0]).parent if item.files else ""))
                    if item_paths.intersection(filter_paths):
                        filtered.append(item)
                return filtered
            return items

        pattern_dotted     = re.compile(r"^(.*?)[._](\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE)
        pattern_standalone = re.compile(r"^(\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$",           re.IGNORECASE)

        sequence_groups = defaultdict(list)
        single_videos   = []
        group_meta      = {}

        for dirpath, dirnames, filenames in os.walk(self.root):
            folder = Path(dirpath).resolve()
            seq_name, shot_name, media_name, mtype_val, ver_val = self.get_effective_ancestor_names(folder)

            for filename in filenames:
                if filename.startswith('.'):
                    continue
                filepath = str(folder / filename)
                ext = os.path.splitext(filename)[1].lower()

                if ext in SUPPORTED_VIDEO_EXTS:
                    item = IngestSequenceItem(filename, [filepath], ext, is_video=True)
                    if seq_name:   item.sequence_code = self._normalise_seq(seq_name)
                    if shot_name:  item.shot_code     = self._normalise_shot(shot_name)
                    if media_name: item.media_name    = self._normalise_plate(media_name); item.plate_name = item.media_name
                    if mtype_val:  item.media_type    = mtype_val
                    if ver_val:
                        m_v = re.search(r"v?(\d+)", ver_val, re.IGNORECASE)
                        if m_v: item.version = int(m_v.group(1))
                    single_videos.append(item)
                    continue

                if ext not in SUPPORTED_IMAGE_EXTS:
                    continue

                m_dot  = pattern_dotted.match(filename)
                m_bare = pattern_standalone.match(filename)
                if m_dot and m_dot.group(1):
                    base_prefix = m_dot.group(1)
                elif m_bare:
                    base_prefix = folder.name
                else:
                    base_prefix = filename

                key = (str(folder), base_prefix, ext)
                sequence_groups[key].append(filepath)
                group_meta[key] = (seq_name, shot_name, media_name, mtype_val, ver_val)

        items = []
        for (folder_str, base_prefix, ext), file_list in sequence_groups.items():
            seq_key = self._norm_path(
                Path(folder_str) / f"{base_prefix}.{ext.lstrip('.')}"
            )
            folder_key = self._norm_path(folder_str)
            file_keys  = {self._norm_path(f) for f in file_list}

            # If filter_paths specified, check if sequence/folder/files are selected
            if filter_paths is not None:
                if (seq_key not in filter_paths and
                    folder_key not in filter_paths and
                    not file_keys.intersection(filter_paths)):
                    continue

            item = IngestSequenceItem(base_prefix, file_list, ext, is_video=False)
            seq_n, shot_n, media_n, mtype_n, ver_n = group_meta.get((folder_str, base_prefix, ext), ("", "", "", "", ""))
            if seq_n:   item.sequence_code = self._normalise_seq(seq_n)
            if shot_n:  item.shot_code     = self._normalise_shot(shot_n)
            if media_n: item.media_name    = self._normalise_plate(media_n); item.plate_name = item.media_name
            if mtype_n: item.media_type    = mtype_n
            if ver_n:
                m_v = re.search(r"v?(\d+)", ver_n, re.IGNORECASE)
                if m_v: item.version = int(m_v.group(1))

            # Apply item overrides (from token rules, pattern rules, or manual token tagging)
            ov = (
                self._effective_item_override_by_key(seq_key) or
                self._effective_item_override_by_key(folder_key) or
                next((self._effective_item_override_by_key(fk) for fk in file_keys if self._effective_item_override_by_key(fk)), None)
            )
            if ov:
                if ov.get("sequence_code"): item.sequence_code = ov["sequence_code"]
                if ov.get("shot_code"):     item.shot_code     = ov["shot_code"]
                if ov.get("media_name"):    item.media_name    = ov["media_name"]; item.plate_name = ov["media_name"]
                if ov.get("version"):       item.version       = ov["version"]
                if ov.get("media_type"):    item.media_type    = ov["media_type"]

            # Apply media type from user tags or pattern rules
            mtype = (
                self._effective_media_type_by_key(seq_key) or
                self._effective_media_type_by_key(folder_key) or
                next((self._effective_media_type_by_key(fk) for fk in file_keys if self._effective_media_type_by_key(fk)), None)
            )
            if mtype:
                item.media_type = mtype

            items.append(item)

        for item in single_videos:
            vpath   = self._norm_path(Path(item.files[0]))
            vfolder = self._norm_path(Path(item.files[0]).parent)
            if filter_paths is not None:
                if vpath not in filter_paths and vfolder not in filter_paths:
                    continue

            ov = self._effective_item_override_by_key(vpath) or self._effective_item_override_by_key(vfolder)
            if ov:
                if ov.get("sequence_code"): item.sequence_code = ov["sequence_code"]
                if ov.get("shot_code"):     item.shot_code     = ov["shot_code"]
                if ov.get("media_name"):    item.media_name    = ov["media_name"]; item.plate_name = ov["media_name"]
                if ov.get("version"):       item.version       = ov["version"]
                if ov.get("media_type"):    item.media_type    = ov["media_type"]

            mtype = self._effective_media_type_by_key(vpath) or self._effective_media_type_by_key(vfolder)
            if mtype:
                item.media_type = mtype
            items.append(item)

        return items

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        try:
            sidecar = self.root / SIDECAR_FILENAME
            data = {
                "depth_map":        {str(k): v for k, v in self._depth_map.items()},
                "folder_overrides": self._folder_overrides,
                "media_types":      self._media_types,
                "token_rules":      self._token_rules,
                "item_overrides":   self._item_overrides,
                "pattern_rules":    self._pattern_rules,
                "table_state":      self._table_state,
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
            if "depth_map" in data:
                self._depth_map        = {int(k): v for k, v in data.get("depth_map", {}).items()}
                self._folder_overrides = data.get("folder_overrides", {})
                self._media_types      = data.get("media_types", {})
                self._token_rules      = data.get("token_rules", {})
                self._item_overrides   = data.get("item_overrides", {})
                self._pattern_rules    = data.get("pattern_rules", [])
                self._table_state      = data.get("table_state", [])
            else:
                # Legacy flat depth dict
                self._depth_map = {int(k): v for k, v in data.items()}
                self._folder_overrides = {}
                self._media_types      = {}
                self._token_rules      = {}
                self._item_overrides   = {}
                self._pattern_rules    = []
                self._table_state      = []

            if self._pattern_rules:
                self.apply_pattern_rules()

            return self.has_map() or bool(self._table_state)
        except Exception as e:
            logger.warning(f"[FolderMapper] Could not load sidecar: {e}")
            return False

    def save_table_state(self, items):
        """Saves current table items state to sidecar file."""
        table_data = []
        for item in items:
            mname = getattr(item, "media_name", getattr(item, "plate_name", "")) or ""
            table_data.append({
                "name": item.name,
                "files": item.files,
                "ext": item.ext,
                "is_video": item.is_video,
                "sequence_code": getattr(item, "sequence_code", "") or "",
                "shot_code": getattr(item, "shot_code", "") or "",
                "media_name": mname,
                "plate_name": mname,
                "media_type": getattr(item, "media_type", "Plate") or "Plate",
                "version": getattr(item, "version", 1),
                "start_frame": getattr(item, "start_frame", 1001),
                "end_frame": getattr(item, "end_frame", 1001),
                "fps": getattr(item, "fps", 24.0),
                "resolution": getattr(item, "resolution", "1920x1080"),
                "colorspace": getattr(item, "colorspace", "ACEScg")
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
            mname              = d.get("media_name") or d.get("plate_name", "")
            item.media_name    = mname
            item.plate_name    = mname
            item.media_type    = d.get("media_type", "Plate")
            item.version       = d.get("version", 1)
            item.start_frame   = d.get("start_frame", 1001)
            item.end_frame     = d.get("end_frame", 1001)
            item.fps           = d.get("fps", 24.0)
            item.resolution    = d.get("resolution", "1920x1080")
            item.colorspace    = d.get("colorspace", "ACEScg")
            items.append(item)
        return items

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_effective_ancestor_names(self, folder: Path):
        """
        Returns (seq_name, shot_name, media_name, media_type_val, version_val) for a folder
        by evaluating depth_map, per-folder overrides, AND pattern rules across all ancestor folders.
        """
        seq_name = ""
        shot_name = ""
        media_name = ""
        media_type_val = ""
        version_val = ""

        seq_depth   = next((d for d, l in self._depth_map.items() if l == LEVEL_SEQ),        None)
        shot_depth  = next((d for d, l in self._depth_map.items() if l == LEVEL_SHOT),       None)
        media_depth = next((d for d, l in self._depth_map.items() if l == LEVEL_MEDIA_NAME), None)
        type_depth  = next((d for d, l in self._depth_map.items() if l == LEVEL_MEDIA_TYPE), None)
        ver_depth   = next((d for d, l in self._depth_map.items() if l == LEVEL_VERSION),    None)

        if seq_depth is not None:   seq_name   = self._ancestor_name(folder, seq_depth)
        if shot_depth is not None:  shot_name  = self._ancestor_name(folder, shot_depth)
        if media_depth is not None: media_name = self._ancestor_name(folder, media_depth)
        if type_depth is not None:  media_type_val = self._ancestor_name(folder, type_depth)
        if ver_depth is not None:   version_val    = self._ancestor_name(folder, ver_depth)

        # Check per-folder overrides + pattern-rule matches on all ancestor folders (including self)
        p = Path(folder).resolve()
        ancestors = []
        curr = p
        while True:
            ancestors.append(curr)
            if curr == self.root or curr.parent == curr:
                break
            curr = curr.parent
        ancestors.reverse()

        for anc in ancestors:
            lvl = self.get_level_for_folder(anc)
            if lvl == LEVEL_SEQ:
                seq_name = anc.name
            elif lvl == LEVEL_SHOT:
                shot_name = anc.name
            elif lvl == LEVEL_MEDIA_NAME:
                media_name = anc.name
            elif lvl == LEVEL_MEDIA_TYPE:
                media_type_val = anc.name
            elif lvl == LEVEL_VERSION:
                version_val = anc.name

        return seq_name, shot_name, media_name, media_type_val, version_val

    def _ancestor_name(self, folder: Path, target_depth) -> str:
        if target_depth is None:
            return ""
        try:
            parts = folder.relative_to(self.root).parts
        except ValueError:
            return ""
        idx = target_depth - 1
        if 0 <= idx < len(parts):
            return parts[idx]
        return ""

    def _normalise_seq(self, name: str) -> str:
        m = RE_SEQ_PART.search(name)
        if m: return f"SQ{int(m.group(1)):03d}"
        m = re.search(r"(\d{2,4})", name)
        return f"SQ{int(m.group(1)):03d}" if m else name.upper()

    def _normalise_shot(self, name: str) -> str:
        m = RE_SHOT_PART.search(name)
        if m: return f"SH{int(m.group(1)):04d}"
        m = re.search(r"(\d{2,4})", name)
        return f"SH{int(m.group(1)):04d}" if m else name.upper()

    def _normalise_plate(self, name: str) -> str:
        if not name:
            return ""
        name_str = name.strip()
        m = re.search(r"(?i)^(?:PL|plate)[-_]?(\d{1,3})$", name_str)
        if m:
            return f"PL{int(m.group(1)):02d}"
        cleaned = re.sub(r"[^A-Za-z0-9_]", "", name_str).upper()
        return cleaned
