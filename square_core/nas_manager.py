import os
import re
import shutil
import hashlib
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from square_core.config import (
    SHOT_DIRECTORY_TEMPLATE,
    SHOT_FOLDER_STRUCTURE,
    DEFAULT_FILE_NAME_TEMPLATE,
    DEFAULT_COPY_WORKERS,
    VALID_TRANSFER_MODES,
    format_dest_filename
)

logger = logging.getLogger("SquareNAS")

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False


class NASManager:
    """Handles NAS directory creation, plate versioning, and checksum-verified file transfers."""

    def __init__(self, nas_root=None, dry_run=True, transfer_mode="copy", workers=None):
        self.nas_root = Path(nas_root) if nas_root else Path("X:/projects")
        self.dry_run = dry_run
        self.transfer_mode = transfer_mode if transfer_mode in VALID_TRANSFER_MODES else "copy"
        self.workers = workers if workers and workers > 0 else DEFAULT_COPY_WORKERS

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

    def _transfer_one_file(self, src_file: Path, dest_file: Path):
        """
        Transfers a single file using self.transfer_mode, with a safe
        cascading fallback: symlink -> hardlink -> full copy. A hardlink
        can't cross filesystems/volumes and a symlink can fail on Windows
        without the right privilege -- either failure just drops to the
        next safer mode rather than aborting the whole ingest, and always
        logs what actually happened so it's never silent.

        Returns the transfer mode that was actually used ("symlink" /
        "hardlink" / "copy"). Checksum verification only applies to real
        copies -- a hardlink/symlink's "destination" IS the same
        underlying file, so hashing it again would be redundant.
        """
        mode = self.transfer_mode

        if mode == "symlink":
            try:
                if dest_file.exists() or dest_file.is_symlink():
                    dest_file.unlink()
                os.symlink(src_file, dest_file)
                return "symlink"
            except OSError as e:
                logger.warning(f"[NASManager] symlink failed for {dest_file.name} ({e}); falling back to hardlink")
                mode = "hardlink"

        if mode == "hardlink":
            try:
                if dest_file.exists():
                    dest_file.unlink()
                os.link(src_file, dest_file)
                return "hardlink"
            except OSError as e:
                logger.warning(f"[NASManager] hardlink failed for {dest_file.name} ({e}); falling back to full copy")

        shutil.copy2(src_file, dest_file)
        src_hash = self.calculate_checksum(src_file)
        dest_hash = self.calculate_checksum(dest_file)
        if src_hash and dest_hash and src_hash != dest_hash:
            raise IOError(f"Checksum mismatch for file {dest_file.name}! Src: {src_hash}, Dest: {dest_hash}")
        return "copy"

    def copy_sequence(self, item, dest_dir, filename_template=None, version_num=1, proj_code="PROJ", progress_callback=None):
        """
        Transfers sequence files to dest_dir (renaming per the filename
        template) using self.transfer_mode. Files are transferred in
        parallel across self.workers threads -- a single sequence's own
        frames, not just separate sequences, since that's the common case
        that actually needs to be faster (one shot with hundreds of
        frames, not hundreds of one-frame shots).
        """
        tmpl = filename_template or DEFAULT_FILE_NAME_TEMPLATE
        total_files = len(item.files)
        re_frame = re.compile(r"(?:[._]|^)(\d{3,7})\.[^.]+$")

        def _target_name(src_file):
            filename = os.path.basename(src_file)
            m_frame = re_frame.search(filename)
            frame_val = m_frame.group(1) if (m_frame and not item.is_video) else None
            return format_dest_filename(
                tmpl, proj_code, item.sequence_code, item.shot_code,
                getattr(item, "media_type", "") or "", item.plate_name,
                version_num, frame=frame_val, ext=item.ext, media_name=item.media_name
            )

        if self.dry_run:
            logger.info(f"[Mock NAS] {self.transfer_mode}: {total_files} files for plate '{item.plate_name}' -> '{dest_dir}'")
            copied_files = []
            for idx, src_file in enumerate(item.files):
                target_name = _target_name(src_file)
                dest_file = dest_dir / target_name
                copied_files.append(str(dest_file))
                if progress_callback:
                    progress_callback(idx + 1, total_files, target_name)
            return copied_files

        os.makedirs(dest_dir, exist_ok=True)

        copied_files = [None] * total_files
        progress_lock = threading.Lock()
        done_count = 0

        def _do_transfer(idx, src_file):
            target_name = _target_name(src_file)
            dest_file = dest_dir / target_name
            mode_used = self._transfer_one_file(Path(src_file), dest_file)
            return idx, str(dest_file), target_name, mode_used

        workers = max(1, min(self.workers, total_files))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_do_transfer, idx, f) for idx, f in enumerate(item.files)]
            for future in as_completed(futures):
                idx, dest_path, target_name, mode_used = future.result()
                copied_files[idx] = dest_path
                with progress_lock:
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, total_files, target_name)

        logger.info(f"[NASManager] Transferred {len(copied_files)} files to {dest_dir} (mode={self.transfer_mode}, workers={workers})")
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

        with ThreadPoolExecutor(max_workers=min(self.workers, total or 1)) as pool:
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
