import os
import re
import shutil
import hashlib
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from square_core.config import (
    SHOT_DIRECTORY_TEMPLATE,
    SHOT_FOLDER_STRUCTURE,
    DEFAULT_FILE_NAME_TEMPLATE,
    format_dest_filename
)

logger = logging.getLogger("SquareNAS")

DEFAULT_COPY_WORKERS = 4

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False


class NASManager:
    """Handles NAS directory creation, plate versioning, and checksum-verified file transfers."""

    def __init__(self, nas_root=None, dry_run=True):
        self.nas_root = Path(nas_root) if nas_root else Path("X:/projects")
        self.dry_run = dry_run

    def calculate_checksum(self, filepath):
        """Calculates xxHash (fast) or MD5 for a file."""
        if not os.path.exists(filepath):
            return ""

        if HAS_XXHASH:
            hasher = xxhash.xxh64()
        else:
            hasher = hashlib.md5()

        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _resolve_input_subfolder(self, media_type=None, resolution=None):
        """
        Determines the subfolder name inside input/.
        If media_type is standard 'Plate' or empty -> folder by resolution (e.g. '1920x1080').
        If media_type is a specific type (e.g. 'Ref', 'BG Plate', 'Edit', 'LUT') -> folder as media_type.
        """
        mtype = (media_type or "").strip()
        if not mtype or mtype.lower() == "plate":
            return resolution or "1920x1080"
        return mtype

    def get_plate_version_info(self, project_code, sequence_code, shot_code, plate_name, item=None, media_type="Plate", resolution="1920x1080"):
        """
        Inspects existing version folders (v001, v002, ...) on NAS storage.
        Returns (version_number, is_already_ingested)
        """
        if item:
            media_type = getattr(item, "media_type", media_type)
            resolution = getattr(item, "resolution", resolution)
            plate_name = getattr(item, "media_name", plate_name)

        seq_c  = (sequence_code or "").strip()
        shot_c = (shot_code or "").strip()
        p_type = (media_type or "").strip()
        p_name = (plate_name or "").strip()

        parent_dir = self.nas_root / (project_code or "PROJ") / "shots" / seq_c / shot_c / "input" / f"{p_type}_{p_name}"

        if not parent_dir.exists():
            return 1, False

        v_dirs = sorted([d for d in parent_dir.iterdir() if d.is_dir() and d.name.startswith("v")], key=lambda d: d.name)
        if not v_dirs:
            return 1, False

        latest_dir = v_dirs[-1]
        try:
            latest_version = int(latest_dir.name.lstrip("v"))
        except ValueError:
            latest_version = len(v_dirs)

        if item and item.files:
            existing_files = [f for f in latest_dir.rglob("*") if f.is_file()]
            if len(existing_files) == len(item.files):
                existing_names = {f.name for f in existing_files}
                item_names = {os.path.basename(f) for f in item.files}
                if existing_names == item_names:
                    return latest_version, True

        return latest_version + 1, False

    def get_dest_dir(self, project_code, sequence_code, shot_code, plate_name, version=1, media_type="", resolution="1920x1080", dir_template=None):
        """
        Builds standardized NAS destination folder path based on per-media-type config template.
        """
        p_type = (media_type or "").strip()
        p_name = (plate_name or "").strip()
        res    = (resolution or "1920x1080").strip()

        from square_core.config import StudioConfig, SHOT_DIRECTORY_TEMPLATE
        config = StudioConfig()

        tmpl = dir_template
        if not tmpl:
            tmpl = config.media_type_configs.get(p_type, config.nas_dir_template or SHOT_DIRECTORY_TEMPLATE)

        try:
            path_str = tmpl.format(
                nas_root=self.nas_root,
                project_code=project_code or "PROJ",
                sequence_code=sequence_code or "SQ010",
                shot_code=shot_code or "SH0100",
                seq=sequence_code or "SQ010",
                shot=shot_code or "SH0100",
                media_type=p_type,
                plate_type=p_type,
                type=p_type,
                media_name=p_name,
                plate_name=p_name,
                name=p_name,
                version=version or 1,
                resolution=res
            )
        except Exception:
            path_str = str(self.nas_root / (project_code or "PROJ") / "shots" / (sequence_code or "SQ010") / (shot_code or "SH0100") / "input" / f"{p_type}_{p_name}")

        return Path(path_str)

    def create_shot_structure(self, dest_dir_or_shot_root, structure=None):
        """Creates target shot folder structure (2D, 3D, input hierarchy)."""
        path = Path(dest_dir_or_shot_root)
        parts = list(path.parts)
        if "shots" in parts:
            idx = parts.index("shots")
            if len(parts) >= idx + 3:
                shot_root = Path(*parts[:idx + 3])
            else:
                shot_root = path
        else:
            shot_root = path

        sub_folders = structure if structure is not None else SHOT_FOLDER_STRUCTURE

        if self.dry_run:
            logger.info(f"[Mock NAS] Created Full Shot Structure at: {shot_root}")
            return path

        os.makedirs(shot_root, exist_ok=True)
        for sub in sub_folders:
            os.makedirs(shot_root / sub, exist_ok=True)

        os.makedirs(path, exist_ok=True)
        return path

    def copy_sequence(self, item, dest_dir, filename_template=None, version_num=1, proj_code="PROJ", progress_callback=None):
        """Copies sequence files to dest_dir with optional renaming and checksum verification."""
        tmpl = filename_template or DEFAULT_FILE_NAME_TEMPLATE
        copied_files = []
        total_files = len(item.files)

        re_frame = re.compile(r"(?:[._]|^)(\d{3,7})\.[^.]+$")

        if self.dry_run:
            logger.info(f"[Mock NAS] Copying {total_files} files for plate '{item.plate_name}' to '{dest_dir}'")
            for idx, src_file in enumerate(item.files):
                filename = os.path.basename(src_file)
                m_frame  = re_frame.search(filename)
                frame_val = m_frame.group(1) if (m_frame and not item.is_video) else None

                target_name = format_dest_filename(
                    tmpl, proj_code, item.sequence_code, item.shot_code,
                    getattr(item, "media_type", "") or "", item.plate_name,
                    version_num, frame=frame_val, ext=item.ext, media_name=item.media_name
                )
                dest_file = dest_dir / target_name
                copied_files.append(str(dest_file))
                if progress_callback:
                    progress_callback(idx + 1, total_files, target_name)
            return copied_files

        os.makedirs(dest_dir, exist_ok=True)

        for idx, src_file in enumerate(item.files):
            filename = os.path.basename(src_file)
            m_frame  = re_frame.search(filename)
            frame_val = m_frame.group(1) if (m_frame and not item.is_video) else None

            target_name = format_dest_filename(
                tmpl, proj_code, item.sequence_code, item.shot_code,
                getattr(item, "media_type", "") or "", item.plate_name,
                version_num, frame=frame_val, ext=item.ext, media_name=item.media_name
            )
            dest_file = dest_dir / target_name

            shutil.copy2(src_file, dest_file)

            # Perform checksum check
            src_hash = self.calculate_checksum(src_file)
            dest_hash = self.calculate_checksum(dest_file)

            if src_hash and dest_hash and src_hash != dest_hash:
                raise IOError(f"Checksum mismatch for file {target_name}! Src: {src_hash}, Dest: {dest_hash}")

            copied_files.append(str(dest_file))
            if progress_callback:
                progress_callback(idx + 1, total_files, target_name)

        logger.info(f"[NASManager] Copied {len(copied_files)} files to {dest_dir}")
        return copied_files

    def check_all_plates(self, items, proj_code, progress_callback=None):
        """
        Check version / duplicate status for all items in parallel.
        Returns dict: id(item) -> (version_num, is_already_ingested)
        """
        results = {}
        total = len(items)

        def _check(item):
            mname = getattr(item, "media_name", getattr(item, "plate_name", "")) or ""
            return item, self.get_plate_version_info(
                proj_code, item.sequence_code, item.shot_code, mname, item=item
            )

        with ThreadPoolExecutor(max_workers=min(DEFAULT_COPY_WORKERS, total or 1)) as pool:
            futures = {pool.submit(_check, item): item for item in items}
            done = 0
            for future in as_completed(futures):
                item, (ver, already) = future.result()
                results[id(item)] = (ver, already)
                done += 1
                if progress_callback:
                    progress_callback(done, total)
        return results

    check_all_media = check_all_plates
    get_media_version_info = get_plate_version_info

    def copy_sequence_parallel(self, items_dest_pairs, workers=DEFAULT_COPY_WORKERS, progress_callback=None):
        """
        Copy multiple plate sequences in parallel.
        items_dest_pairs: list of (item, dest_dir)
        progress_callback(item, copied_files) called per completed plate.
        """
        results = []

        def _copy(item, dest_dir):
            return item, self.copy_sequence(item, dest_dir)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_copy, item, dest): (item, dest) for item, dest in items_dest_pairs}
            for future in as_completed(futures):
                item, copied = future.result()
                results.append((item, copied))
                if progress_callback:
                    progress_callback(item, copied)
        return results
