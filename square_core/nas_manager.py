import os
import re
import sys
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

# Pulls the frame number out of a sequence frame's filename (e.g.
# "plate.1001.exr" -> "1001"). Shared by copy_sequence's own renaming and by
# the version-slot inspector below, so what gets checked and what gets
# written can never drift apart again.
_FRAME_SUFFIX_RE = re.compile(r"(?:[._]|^)(\d{3,7})\.[^.]+$")

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False


class NASManager:
    """Handles NAS directory creation, media versioning, and checksum-verified file transfers."""

    def __init__(self, nas_root=None, dry_run=True, transfer_mode="copy", workers=None):
        self.nas_root = Path(nas_root) if nas_root else Path("X:/projects")
        self.dry_run = dry_run
        self.transfer_mode = transfer_mode if transfer_mode in VALID_TRANSFER_MODES else "copy"
        self.workers = workers if workers and workers > 0 else DEFAULT_COPY_WORKERS

    @staticmethod
    def _fallback_hasher():
        """The hasher used when no shared FileHasher is supplied. Whatever this
        returns, calculate_checksum() and _copy_and_hash() MUST agree on it or
        a fresh copy fails its own verify."""
        return xxhash.xxh3_64() if HAS_XXHASH else hashlib.md5()

    def calculate_checksum(self, filepath):
        """Content hash of a file (xxh3_64, or MD5 without xxhash)."""
        if not os.path.exists(filepath):
            return ""
        hasher = self._fallback_hasher()
        with open(filepath, "rb") as f:
            while chunk := f.read(1 << 20):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _render_target_filename(self, item, src_file, filename_template, version_num, proj_code):
        """
        The exact destination filename copy_sequence() will give this one
        source file. Shared with _inspect_version_slot below so "what will
        this be renamed to" is computed in exactly one place -- the version
        checker used to compare un-renamed SOURCE names against already-
        renamed DESTINATION names, which can never be equal by construction.
        """
        tmpl = filename_template or DEFAULT_FILE_NAME_TEMPLATE
        filename = os.path.basename(src_file)
        m_frame = _FRAME_SUFFIX_RE.search(filename)
        frame_val = m_frame.group(1) if (m_frame and not item.is_video) else None
        return format_dest_filename(
            tmpl, proj_code, item.sequence_code, item.shot_code,
            getattr(item, "media_type", "") or "", media_name=item.media_name,
            version_num=version_num, frame=frame_val, ext=item.ext
        )

    def _inspect_version_slot(self, dest_dir, item, version_num, proj_code, filename_template=None):
        """
        Looks at exactly one version folder on disk and classifies it
        against `item`'s real source files by CONTENT HASH, not by name --
        a destination file is always renamed from its source per the naming
        template, so comparing names can never match.

        Returns "empty" (nothing there, safe to ingest), "already" (this
        exact content is already there file-for-file, safe to skip), or
        "conflict" (something else is there -- ingesting would write on top
        of it).
        """
        if not dest_dir.exists():
            return "empty"
        existing_files = [f for f in dest_dir.rglob("*") if f.is_file()]
        if not existing_files:
            return "empty"
        if not item or not item.files or len(existing_files) != len(item.files):
            return "conflict"

        existing_by_name = {f.name: f for f in existing_files}
        for src_file in item.files:
            expected_name = self._render_target_filename(item, src_file, filename_template, version_num, proj_code)
            match = existing_by_name.get(expected_name)
            if match is None or self.calculate_checksum(src_file) != self.calculate_checksum(match):
                return "conflict"
        return "already"

    def get_media_version_info(self, project_code, sequence_code, shot_code, media_name, item=None,
                                media_type="Plate", resolution="1920x1080", filename_template=None):
        """
        Finds the next version to ingest into, by scanning v1, v2, ... via
        the SAME path get_dest_dir() actually copies into. This used to
        hardcode its own "input/{type}_{name}/v###" path -- which none of
        the studio's real per-media-type destination templates
        (DEFAULT_MEDIA_TYPE_CONFIGS) ever write to, so this was silently
        checking an empty directory on every real ingest.

        Returns (version_number, is_already_ingested). is_already_ingested
        is a real hash comparison against the latest version's files (see
        _inspect_version_slot), not a filename comparison.
        """
        if item:
            media_type = getattr(item, "media_type", media_type)
            resolution = getattr(item, "resolution", resolution)
            media_name = getattr(item, "media_name", media_name)

        seq_c  = (sequence_code or "").strip()
        shot_c = (shot_code or "").strip()

        # Scan forward from v1 using the real destination path for each
        # candidate version rather than listing a parent directory -- the
        # per-media-type template nests the version INTO the path
        # differently per type (e.g. "<name>_v###", not a "v###" child of a
        # shared parent), so there is no one parent folder to list.
        latest_found = 0
        v = 1
        while v <= 9999:   # sane ceiling against a runaway/corrupt scan
            dest_dir = self.get_dest_dir(project_code, seq_c, shot_c, media_name,
                                          version=v, media_type=media_type, resolution=resolution)
            if not dest_dir.exists() or not any(p.is_file() for p in dest_dir.rglob("*")):
                break
            latest_found = v
            v += 1

        if latest_found == 0:
            return 1, False

        latest_dir = self.get_dest_dir(project_code, seq_c, shot_c, media_name,
                                        version=latest_found, media_type=media_type, resolution=resolution)
        state = self._inspect_version_slot(latest_dir, item, latest_found, project_code, filename_template)
        if state == "already":
            return latest_found, True
        return latest_found + 1, False

    def check_specific_version(self, project_code, sequence_code, shot_code, media_name, version_num, item,
                                media_type="Plate", resolution="1920x1080", filename_template=None):
        """
        Verifies ONE exact, caller-chosen version number -- used when a
        version was picked by hand (per-row dropdown, batch Set Version)
        instead of accepted from get_media_version_info(). A manual pick
        bypasses that auto-detection entirely, so without this, picking an
        already-used version was never checked against the NAS at all --
        it would silently proceed to ingest into an occupied slot.

        Returns "empty" / "already" / "conflict", same meaning as
        _inspect_version_slot.
        """
        seq_c  = (sequence_code or "").strip()
        shot_c = (shot_code or "").strip()
        dest_dir = self.get_dest_dir(project_code, seq_c, shot_c, media_name,
                                      version=version_num, media_type=media_type, resolution=resolution)
        return self._inspect_version_slot(dest_dir, item, version_num, project_code, filename_template)

    # ------------------------------------------------------------------
    # Rework API -- explicit, hasher-injected, item-shape-agnostic.
    # The controller drives version selection itself; these just answer
    # "what would this file be named" and "what's in that slot".
    # ------------------------------------------------------------------

    def dest_names(self, item, version_num, proj_code, filename_template=None) -> dict:
        """{source_path: final basename} for every file of `item`."""
        tmpl = filename_template or DEFAULT_FILE_NAME_TEMPLATE
        return {
            f: self._render_target_filename(item, f, tmpl, version_num, proj_code)
            for f in (getattr(item, "files", None) or getattr(item, "source_files", []))
        }

    def inspect_slot(self, dest_dir, item, version_num, proj_code,
                     filename_template=None, hasher=None):
        """
        Classify one version folder against `item`'s source files by CONTENT
        HASH (names are rewritten on copy, so a name compare is meaningless).

        Returns (state, detail) where state is "empty" / "already" /
        "conflict". When `hasher` (a hashing.FileHasher) is given, both
        source and destination hashes come from it -- computed once, shared
        with the ledger and the post-copy verify.
        """
        dest_dir = Path(dest_dir)
        if not dest_dir.exists():
            return "empty", ""
        existing = [f for f in dest_dir.rglob("*") if f.is_file()]
        if not existing:
            return "empty", ""

        src_files = list(getattr(item, "files", None) or getattr(item, "source_files", []))
        vlabel = f"v{int(version_num):03d}"
        if not src_files or len(existing) != len(src_files):
            return "conflict", (
                f"{vlabel} already exists with {len(existing)} file(s); "
                f"this delivery has {len(src_files)}."
            )

        def _h(p):
            return hasher.hash_file(str(p)) if hasher is not None else self.calculate_checksum(str(p))

        by_name = {f.name: f for f in existing}
        names = self.dest_names(item, version_num, proj_code, filename_template)
        for src in src_files:
            expected = names[src]
            match = by_name.get(expected)
            if match is None:
                return "conflict", f"{vlabel} exists but does not contain '{expected}'."
            if _h(src) != _h(match):
                return "conflict", f"{vlabel} exists with different content ('{expected}' differs)."
        return "already", f"{vlabel} already holds this exact content."

    def next_free_version(self, item, proj_code, filename_template=None, start=1, dir_template=None):
        """Lowest version number whose destination folder is empty/absent."""
        v = max(1, int(start))
        while v <= 9999:
            d = self.get_dest_dir(
                proj_code, item.sequence_code, item.shot_code, item.media_name,
                version=v, media_type=getattr(item, "media_type", "") or "",
                resolution=getattr(item, "resolution", "1920x1080") or "1920x1080",
                dir_template=dir_template,
            )
            if not d.exists() or not any(p.is_file() for p in d.rglob("*")):
                return v
            v += 1
        return v

    def get_dest_dir(self, project_code, sequence_code, shot_code, media_name, version=1, media_type="", resolution="1920x1080", dir_template=None):
        """
        Builds standardized NAS destination folder path based on per-media-type config template.
        """
        p_type = (media_type or "").strip()
        p_name = (media_name or "").strip()
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
                type=p_type,
                media_name=p_name,
                name=p_name,
                version=version or 1,
                resolution=res
            )
        except Exception as e:
            # MUST still include the version number -- a fallback path that
            # doesn't would alias every version of this media onto the same
            # folder, so v2 silently overwrites v1. This used to happen for
            # real: a persisted nas_dir_template left over from before the
            # plate-to-media rename still used {plate_type}/{plate_name},
            # which fails here on every call for any media type not covered
            # by media_type_configs, hitting this exact fallback every time.
            logger.error(
                f"[NASManager] Destination template failed to render ({e}); "
                f"falling back to a generic versioned path. Template was: {tmpl!r}"
            )
            path_str = str(
                self.nas_root / (project_code or "PROJ") / "shots" / (sequence_code or "SQ010")
                / (shot_code or "SH0100") / "input" / f"{p_type}_{p_name}" / f"v{int(version or 1):03d}"
            )

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

    _COPY_CHUNK = 4 * 1024 * 1024   # 4 MiB

    def _copy_and_hash(self, src_file: Path, dest_file: Path, hasher=None):
        """
        Byte-copy src -> dest and return the destination's content hash,
        computed FROM THE BYTES AS THEY ARE WRITTEN -- no separate re-read of
        either file. The digest matches FileHasher's (same algo) so the
        caller can compare it against the source hash it already has from
        pre-flight.

        On Windows a native CopyFileExW does the data move (markedly faster
        than a Python buffer loop for large files), then the destination is
        hashed once. Elsewhere the stream loop hashes for free while copying.
        """
        raw = hasher.new_raw() if hasher is not None else self._fallback_hasher()

        native = sys.platform == "win32" and self._win_copyfile(src_file, dest_file)
        if native:
            with open(dest_file, "rb") as fh:
                while True:
                    chunk = fh.read(self._COPY_CHUNK)
                    if not chunk:
                        break
                    raw.update(chunk)
        else:
            with open(src_file, "rb") as fin, open(dest_file, "wb") as fout:
                while True:
                    chunk = fin.read(self._COPY_CHUNK)
                    if not chunk:
                        break
                    fout.write(chunk)
                    raw.update(chunk)
        shutil.copystat(src_file, dest_file)
        return raw.hexdigest()

    @staticmethod
    def _win_copyfile(src_file: Path, dest_file: Path) -> bool:
        """Win32 CopyFileExW for the raw data move. Returns True on success, False to fall back."""
        try:
            import ctypes
            from ctypes import wintypes
            CopyFileExW = ctypes.windll.kernel32.CopyFileExW
            CopyFileExW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), wintypes.DWORD,
            ]
            CopyFileExW.restype = wintypes.BOOL
            ok = CopyFileExW(str(src_file), str(dest_file), None, None, None, 0)
            return bool(ok)
        except Exception as e:   # pragma: no cover - platform/edge dependent
            logger.debug("[NASManager] CopyFileExW unavailable (%s); using stream copy", e)
            return False

    def _transfer_one_file(self, src_file: Path, dest_file: Path, hasher=None, expected_hash=None):
        """
        Transfer one file per self.transfer_mode, with a safe cascading
        fallback symlink -> hardlink -> copy (a hardlink can't cross volumes;
        a Windows symlink may lack privilege).

        For a real copy the destination hash is computed while writing and
        compared to `expected_hash` (the source hash from pre-flight) so a
        corrupt/truncated transfer is still caught without re-reading the
        source. symlink/hardlink share the same inode -- nothing to verify.

        Returns the mode actually used ("symlink" / "hardlink" / "copy").
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

        dest_hash = self._copy_and_hash(Path(src_file), Path(dest_file), hasher=hasher)
        expected = expected_hash or (
            hasher.hash_file(str(src_file)) if hasher is not None else self.calculate_checksum(src_file)
        )
        if expected and dest_hash and expected != dest_hash:
            raise IOError(
                f"Checksum mismatch for {dest_file.name}! source={expected} dest={dest_hash}"
            )
        return "copy"

    def copy_sequence(self, item, dest_dir, filename_template=None, version_num=1,
                      proj_code="PROJ", progress_callback=None,
                      pool=None, hasher=None, source_hashes=None):
        """
        Transfer a sequence's files to dest_dir, renamed per the filename
        template, using self.transfer_mode.

        pool           -- a shared ThreadPoolExecutor for the frame transfers.
                          When the controller passes one, EVERY sequence being
                          ingested draws from the same pool, so total
                          concurrent file transfers stay capped at the pool
                          size instead of (items x frames). Falls back to a
                          local pool when not given.
        hasher         -- the shared FileHasher; lets the copy verify reuse
                          pre-flight source hashes instead of re-reading.
        source_hashes  -- {src_path: hash} already computed at pre-flight.
        """
        tmpl = filename_template or DEFAULT_FILE_NAME_TEMPLATE
        total_files = len(item.files)
        source_hashes = source_hashes or {}

        def _target_name(src_file):
            return self._render_target_filename(item, src_file, tmpl, version_num, proj_code)

        if self.dry_run:
            logger.info(f"[Mock NAS] {self.transfer_mode}: {total_files} files for media '{item.media_name}' -> '{dest_dir}'")
            copied_files = []
            for idx, src_file in enumerate(item.files):
                target_name = _target_name(src_file)
                copied_files.append(str(dest_dir / target_name))
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
            mode_used = self._transfer_one_file(
                Path(src_file), dest_file, hasher=hasher,
                expected_hash=source_hashes.get(src_file),
            )
            return idx, str(dest_file), target_name, mode_used

        def _drain(pl):
            nonlocal done_count
            futures = [pl.submit(_do_transfer, idx, f) for idx, f in enumerate(item.files)]
            for future in as_completed(futures):
                idx, dest_path, target_name, _mode = future.result()
                copied_files[idx] = dest_path
                with progress_lock:
                    done_count += 1
                    if progress_callback:
                        progress_callback(done_count, total_files, target_name)

        if pool is not None:
            _drain(pool)
        else:
            workers = max(1, min(self.workers, total_files))
            with ThreadPoolExecutor(max_workers=workers) as local_pool:
                _drain(local_pool)

        logger.info(f"[NASManager] Transferred {len(copied_files)} files to {dest_dir} (mode={self.transfer_mode})")
        return copied_files

    def check_all_media(self, items, proj_code, progress_callback=None, forced_versions=None, errors=None):
        """
        Check version / duplicate status for all items in parallel.

        forced_versions, when given, is {id(item): version_num} for rows
        whose version was picked by hand (per-row dropdown, batch Set
        Version) rather than auto-detected -- those get verified at EXACTLY
        that number via check_specific_version() instead of having a fresh
        "best" version computed for them, which would silently discard the
        user's choice.

        Returns dict: id(item) -> (version_num, state, was_forced) where
        state is "new" / "already" / "conflict" / "error". Only a forced
        check can ever come back "conflict" -- the auto path always
        resolves to either the exact existing content ("already") or a
        guaranteed-empty next slot ("new").

        A real-world NAS throws things synthetic test data never does --
        a permission error, a path with characters os.path chokes on, a
        disconnected mount raising mid-scan. That used to be fatal for the
        WHOLE batch: future.result() re-raised inside the as_completed loop
        with nothing catching it, so one bad item's exception propagated
        out of check_all_media entirely, discarding every other item's
        already-computed result -- the entire batch stayed on "Checking..."
        forever with no error surfaced anywhere. Now a per-item exception
        is caught, logged, and reported as state "error" for just that one
        item (held out of ingest same as a conflict); every other item in
        the batch still resolves normally. `errors`, when passed a dict, is
        updated with {id(item): str(exception)} for each one so a caller
        that wants the real message (not just the fact that it failed) can
        show it.
        """
        forced_versions = forced_versions or {}
        results = {}
        total = len(items)

        def _check(item):
            key = id(item)
            mname = getattr(item, "media_name", "") or ""
            if key in forced_versions:
                v = forced_versions[key]
                slot_state = self.check_specific_version(
                    proj_code, item.sequence_code, item.shot_code, mname, v, item
                )
                # "empty" (nothing there yet) reads exactly like an
                # auto-detected "new" slot to every caller -- normalize so
                # "new" always means the same thing regardless of which path
                # produced it, rather than having two spellings for it.
                state = "new" if slot_state == "empty" else slot_state
                return v, state, True
            ver, already = self.get_media_version_info(
                proj_code, item.sequence_code, item.shot_code, mname, item=item
            )
            return ver, ("already" if already else "new"), False

        with ThreadPoolExecutor(max_workers=min(self.workers, total or 1)) as pool:
            futures = {pool.submit(_check, item): item for item in items}
            done = 0
            for future in as_completed(futures):
                item = futures[future]
                try:
                    ver, state, forced = future.result()
                except Exception as e:
                    logger.error(f"[NASManager] Check failed for '{item.name}': {e}", exc_info=True)
                    if errors is not None:
                        errors[id(item)] = str(e)
                    ver, state, forced = forced_versions.get(id(item), 1), "error", id(item) in forced_versions
                results[id(item)] = (ver, state, forced)
                done += 1
                if progress_callback:
                    progress_callback(done, total)
        return results
