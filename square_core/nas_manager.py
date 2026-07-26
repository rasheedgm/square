import os
import shutil
import hashlib
import logging
from pathlib import Path
from square_core.config import SHOT_DIRECTORY_TEMPLATE

logger = logging.getLogger("SquareNAS")

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False


class NASManager:
    """Handles NAS directory creation, plate versioning, and checksum-verified file transfers."""

    def __init__(self, nas_root, dry_run=True):
        self.nas_root = Path(nas_root)
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

    def get_plate_version_info(self, project_code, sequence_code, shot_code, plate_name, item=None):
        """
        Inspects existing version folders (v001, v002, ...) on NAS storage.
        Returns (version_number, is_already_ingested)
        """
        parent_dir = self.nas_root / project_code / "shots" / sequence_code / shot_code / "plates" / plate_name
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
            existing_files = list(latest_dir.glob("*"))
            if len(existing_files) == len(item.files):
                existing_names = {f.name for f in existing_files}
                item_names = {os.path.basename(f) for f in item.files}
                if existing_names == item_names:
                    return latest_version, True

        return latest_version + 1, False

    def get_dest_dir(self, project_code, sequence_code, shot_code, plate_name, version=1):
        """Builds standardized NAS destination folder path."""
        path_str = SHOT_DIRECTORY_TEMPLATE.format(
            nas_root=self.nas_root,
            project_code=project_code,
            sequence_code=sequence_code,
            shot_code=shot_code,
            plate_name=plate_name,
            version=version
        )
        return Path(path_str)

    def create_shot_structure(self, dest_dir):
        """Creates target NAS folder and default subfolders (work, renders)."""
        if self.dry_run:
            logger.info(f"[Mock NAS] Created Directory: {dest_dir}")
            return dest_dir

        os.makedirs(dest_dir, exist_ok=True)
        
        # Create work and renders folders under shot root
        shot_root = dest_dir.parent.parent
        work_dir = shot_root / "work" / "comp"
        renders_dir = shot_root / "renders" / "comp"
        
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(renders_dir, exist_ok=True)

        return dest_dir

    def copy_sequence(self, item, dest_dir, progress_callback=None):
        """Copies sequence files to dest_dir with optional checksum verification."""
        copied_files = []
        total_files = len(item.files)

        if self.dry_run:
            logger.info(f"[Mock NAS] Copying {total_files} files for plate '{item.plate_name}' to '{dest_dir}'")
            for idx, src_file in enumerate(item.files):
                dest_file = dest_dir / os.path.basename(src_file)
                copied_files.append(str(dest_file))
                if progress_callback:
                    progress_callback(idx + 1, total_files, os.path.basename(src_file))
            return copied_files

        os.makedirs(dest_dir, exist_ok=True)

        for idx, src_file in enumerate(item.files):
            filename = os.path.basename(src_file)
            dest_file = dest_dir / filename

            shutil.copy2(src_file, dest_file)
            
            # Perform checksum check
            src_hash = self.calculate_checksum(src_file)
            dest_hash = self.calculate_checksum(dest_file)

            if src_hash and dest_hash and src_hash != dest_hash:
                raise IOError(f"Checksum mismatch for file {filename}! Src: {src_hash}, Dest: {dest_hash}")

            copied_files.append(str(dest_file))
            if progress_callback:
                progress_callback(idx + 1, total_files, filename)

        logger.info(f"[NASManager] Copied {len(copied_files)} files to {dest_dir}")
        return copied_files
