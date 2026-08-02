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
LEVEL_SEQ   = "seq"
LEVEL_SHOT  = "shot"
LEVEL_PLATE = "plate"
LEVEL_NONE  = None

SUPPORTED_IMAGE_EXTS = {".exr", ".dpx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".m4v"}

SIDECAR_FILENAME = ".square_ingest_map.json"

# ------------------------------------------------------------------
# Auto-detection regexes (search in compound names too)
# ------------------------------------------------------------------
RE_SEQ_PART   = re.compile(r"(?i)(?:^|[_\-])(?:SQ|seq|reel|ep)[_\-]?(\d{2,4})(?:[_\-]|$)")
RE_SHOT_PART  = re.compile(r"(?i)(?:^|[_\-])(?:SH|shot|sc)[_\-]?(\d{2,4})(?:[_\-]|$)")
RE_PLATE_PART = re.compile(r"(?i)(?:^|[_\-])(?:PL|plate|plates?|img|render|raw)[_\-]?(\w*)(?:[_\-]|$)")


def _detect_level(folder_name: str):
    """Return the single dominant level for a folder name, or None."""
    has_seq   = bool(RE_SEQ_PART.search(folder_name))
    has_shot  = bool(RE_SHOT_PART.search(folder_name))
    has_plate = bool(RE_PLATE_PART.search(folder_name))
    if has_plate and has_shot:
        return LEVEL_PLATE
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
        if level not in (LEVEL_SEQ, LEVEL_SHOT, LEVEL_PLATE, LEVEL_NONE, None):
            raise ValueError(f"Invalid level: {level!r}")
        if level is None:
            self._depth_map.pop(depth, None)
        else:
            self._depth_map[depth] = level

    def get_level(self, depth: int):
        return self._depth_map.get(depth, LEVEL_NONE)

    # ------------------------------------------------------------------
    # Tag API — per-folder override
    # ------------------------------------------------------------------

    def set_level_for_folder(self, path, level):
        """Tag one specific folder. Pass None to clear the override."""
        key = self._norm_path(path)
        if level is None:
            self._folder_overrides.pop(key, None)
        else:
            if level not in (LEVEL_SEQ, LEVEL_SHOT, LEVEL_PLATE):
                raise ValueError(f"Invalid level: {level!r}")
            self._folder_overrides[key] = level

    def get_level_for_folder(self, path):
        """Return override level for this exact folder, or None."""
        return self._folder_overrides.get(self._norm_path(path), None)

    # ------------------------------------------------------------------
    # Media Type API — for sequences/files (future: Plate, Ref, BG, etc.)
    # ------------------------------------------------------------------

    # Default types available now; can be extended later
    MEDIA_TYPES = ["Plate", "Ref", "BG Plate", "Comp Render", "Precomp"]

    def set_media_type(self, path, type_name):
        """Assign a media type label to a specific file/sequence path."""
        key = self._norm_path(path)
        if type_name is None:
            self._media_types.pop(key, None)
        else:
            self._media_types[key] = str(type_name)

    def get_media_type(self, path) -> str:
        """Return the media type for a path, or None if unset."""
        return self._media_types.get(self._norm_path(path), None)

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
        return bool(self._depth_map) or bool(self._folder_overrides)

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

        if not self._depth_map and not self._folder_overrides:
            from square_core.plate_scanner import PlateScanner
            items = PlateScanner(self.root).scan()
            if filter_paths:
                filtered = []
                for item in items:
                    item_paths = {self._norm_path(f) for f in item.files}
                    item_paths.add(self._norm_path(Path(item.files[0]).parent if item.files else ""))
                    if item_paths.intersection(filter_paths):
                        filtered.append(item)
                return filtered
            return items

        seq_depth   = next((d for d, l in self._depth_map.items() if l == LEVEL_SEQ),   None)
        shot_depth  = next((d for d, l in self._depth_map.items() if l == LEVEL_SHOT),  None)
        plate_depth = next((d for d, l in self._depth_map.items() if l == LEVEL_PLATE), None)

        pattern_dotted     = re.compile(r"^(.*?)[._](\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE)
        pattern_standalone = re.compile(r"^(\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$",           re.IGNORECASE)

        sequence_groups = defaultdict(list)
        single_videos   = []
        group_meta      = {}

        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            folder = Path(dirpath).resolve()

            seq_name   = self._ancestor_name(folder, seq_depth)
            shot_name  = self._ancestor_name(folder, shot_depth)
            plate_name = self._ancestor_name(folder, plate_depth) if plate_depth is not None else folder.name

            for filename in filenames:
                if filename.startswith('.'):
                    continue
                filepath = str(folder / filename)
                ext = os.path.splitext(filename)[1].lower()

                if ext in SUPPORTED_VIDEO_EXTS:
                    item = IngestSequenceItem(filename, [filepath], ext, is_video=True)
                    if seq_name:  item.sequence_code = self._normalise_seq(seq_name)
                    if shot_name: item.shot_code     = self._normalise_shot(shot_name)
                    item.plate_name = self._normalise_plate(plate_name or "MAIN")
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
                group_meta[key] = (seq_name, shot_name, plate_name)

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
            seq_n, shot_n, plate_n = group_meta.get((folder_str, base_prefix, ext), ("", "", ""))
            if seq_n:   item.sequence_code = self._normalise_seq(seq_n)
            if shot_n:  item.shot_code     = self._normalise_shot(shot_n)
            if plate_n: item.plate_name    = self._normalise_plate(plate_n)

            # Apply media type from user tags
            mtype = self._media_types.get(seq_key)
            if mtype:
                item.media_type = mtype

            items.append(item)

        for item in single_videos:
            vpath   = self._norm_path(Path(item.files[0]))
            vfolder = self._norm_path(Path(item.files[0]).parent)
            if filter_paths is not None:
                if vpath not in filter_paths and vfolder not in filter_paths:
                    continue

            mtype = self._media_types.get(vpath)
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
            else:
                # Legacy flat depth dict
                self._depth_map = {int(k): v for k, v in data.items()}
                self._folder_overrides = {}
                self._media_types      = {}
            return bool(self._depth_map) or bool(self._folder_overrides)
        except Exception as e:
            logger.warning(f"[FolderMapper] Could not load sidecar: {e}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        m = RE_PLATE_PART.search(name)
        if m:
            num = m.group(1)
            if num and num.isdigit(): return f"PL{int(num):02d}"
            elif num: return num.upper()
        cleaned = re.sub(r"[^A-Za-z0-9_]", "", name).upper()
        return cleaned or "MAIN"
