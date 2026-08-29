"""
FolderMapper — depth-based + per-folder level tagging for incoming media.

Supports two tagging modes:
  - depth-wide: all folders at depth N share a level
  - per-folder override: a specific folder gets its own level

Saved as .square_ingest_map.json next to the root so re-opening
the same delivery remembers all tags.
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

SUPPORTED_IMAGE_EXTS = {".exr", ".dpx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".m4v"}

SIDECAR_FILENAME = ".square_ingest_map.json"

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


class FolderMapper:
    """
    Manages folder-depth-to-level tagging for an incoming media root folder.

    depth 0 = root folder itself (never tagged)
    depth 1 = first level of subfolders

    Tagging modes:
        set_level(depth, level)           — tags the whole depth
        set_level_for_folder(path, level) — overrides one specific folder
    """

    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self._depth_map        = {}   # depth (int) -> level str
        self._folder_overrides = {}   # resolved path str -> level str
        self._media_types      = {}   # resolved file path str -> media type name ("Plate", "Ref", ...)
        self._table_state      = []   # list of serialized item dicts

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
        clean_level = level.lower() if isinstance(level, str) else level
        if clean_level in ("plate", "media"):
            clean_level = LEVEL_MEDIA_NAME

        if clean_level not in (LEVEL_SEQ, LEVEL_SHOT, LEVEL_MEDIA_NAME, LEVEL_NONE, None):
            raise ValueError(f"Invalid level: {level!r}")
        if clean_level is None:
            self._depth_map.pop(depth, None)
        else:
            self._depth_map[depth] = clean_level

    def get_level(self, depth: int):
        return self._depth_map.get(depth, LEVEL_NONE)

    def clear_all_levels(self):
        """Clears all depth maps, folder level overrides, media types, token rules, table state, and removes sidecar file from disk."""
        self._depth_map.clear()
        self._folder_overrides.clear()
        self._media_types.clear()
        self._token_rules.clear()
        self._item_overrides.clear()
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
        clean_level = level.lower() if isinstance(level, str) else level
        if clean_level in ("plate", "media"):
            clean_level = LEVEL_MEDIA_NAME

        if clean_level is None:
            self._folder_overrides.pop(key, None)
        else:
            if clean_level not in (LEVEL_SEQ, LEVEL_SHOT, LEVEL_MEDIA_NAME):
                raise ValueError(f"Invalid level: {level!r}")
            self._folder_overrides[key] = clean_level

    def get_level_for_folder(self, path):
        """Return override level for this exact folder, or None."""
        return self._folder_overrides.get(self._norm_path(path), None)

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
        """Return the media type for a path, or None if unset."""
        return self._media_types.get(self._norm_path(path), None)

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
            bool(self._item_overrides)
        )

    def level_of_path(self, path):
        """Return effective level for a path (override first, then depth_map)."""
        key = self._norm_path(path)
        if key in self._folder_overrides:
            return self._folder_overrides[key]
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
                ov = self._item_overrides.get(vpath) or self._item_overrides.get(vfolder)
                if ov:
                    if ov.get("sequence_code"): item.sequence_code = ov["sequence_code"]
                    if ov.get("shot_code"):     item.shot_code     = ov["shot_code"]
                    if ov.get("media_name"):    item.media_name    = ov["media_name"]; item.plate_name = ov["media_name"]
                    if ov.get("version"):       item.version       = ov["version"]
                    if ov.get("media_type"):    item.media_type    = ov["media_type"]

                mtype = self._media_types.get(vpath) or self._media_types.get(vfolder)
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
            if media_n: item.media_name    = self._normalise_plate(media_n); item.plate_name = item.media_n
            if mtype_n: item.media_type    = mtype_n
            if ver_n:
                m_v = re.search(r"v?(\d+)", ver_n, re.IGNORECASE)
                if m_v: item.version = int(m_v.group(1))

            # Apply item overrides (from token rules or manual token tagging)
            ov = (
                self._item_overrides.get(seq_key) or
                self._item_overrides.get(folder_key) or
                next((self._item_overrides[fk] for fk in file_keys if fk in self._item_overrides), None)
            )
            if ov:
                if ov.get("sequence_code"): item.sequence_code = ov["sequence_code"]
                if ov.get("shot_code"):     item.shot_code     = ov["shot_code"]
                if ov.get("media_name"):    item.media_name    = ov["media_name"]; item.plate_name = ov["media_name"]
                if ov.get("version"):       item.version       = ov["version"]
                if ov.get("media_type"):    item.media_type    = ov["media_type"]

            # Apply media type from user tags
            mtype = (
                self._media_types.get(seq_key) or
                self._media_types.get(folder_key) or
                next((self._media_types[fk] for fk in file_keys if fk in self._media_types), None)
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

            ov = self._item_overrides.get(vpath) or self._item_overrides.get(vfolder)
            if ov:
                if ov.get("sequence_code"): item.sequence_code = ov["sequence_code"]
                if ov.get("shot_code"):     item.shot_code     = ov["shot_code"]
                if ov.get("media_name"):    item.media_name    = ov["media_name"]; item.plate_name = ov["media_name"]
                if ov.get("version"):       item.version       = ov["version"]
                if ov.get("media_type"):    item.media_type    = ov["media_type"]

            mtype = self._media_types.get(vpath) or self._media_types.get(vfolder)
            if mtype:
                item.media_type = mtype
            items.append(item)

        return items

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def __init__(self, root_path):
        self.root = Path(root_path).resolve()
        self._depth_map        = {}   # depth (int) -> level str
        self._folder_overrides = {}   # resolved path str -> level str
        self._media_types      = {}   # resolved file path str -> media type name ("Plate", "Ref", ...)
        self._token_rules      = {}   # resolved file/folder path str -> token rule dict
        self._item_overrides   = {}   # resolved file/folder path str -> item metadata overrides dict
        self._table_state      = []   # list of serialized item dicts
        self.load()

    def save(self):
        try:
            sidecar = self.root / SIDECAR_FILENAME
            data = {
                "depth_map":        {str(k): v for k, v in self._depth_map.items()},
                "folder_overrides": self._folder_overrides,
                "media_types":      self._media_types,
                "token_rules":      self._token_rules,
                "item_overrides":   self._item_overrides,
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
                self._table_state      = data.get("table_state", [])
            else:
                # Legacy flat depth dict
                self._depth_map = {int(k): v for k, v in data.items()}
                self._folder_overrides = {}
                self._media_types      = {}
                self._token_rules      = {}
                self._item_overrides   = {}
                self._table_state      = []
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
        by evaluating both depth_map AND per-folder level overrides across all ancestor folders.
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

        # Check per-folder level overrides on all ancestor folders (including self)
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
