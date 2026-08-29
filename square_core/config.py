import os
import json
from pathlib import Path

DEFAULT_KITSU_URL = os.getenv("KITSU_URL", "http://localhost/api")
DEFAULT_KITSU_USER = os.getenv("KITSU_USER", "admin@example.com")
DEFAULT_KITSU_PASSWORD = os.getenv("KITSU_PASSWORD", "12345678")

# NAS Storage Settings
DEFAULT_NAS_ROOT = os.getenv("SQUARE_NAS_ROOT", "X:/projects")
DEFAULT_LOCAL_CACHE_ROOT = os.getenv("SQUARE_CACHE_ROOT", "C:/cache/square")

# Default Tasks created for new shots (Ingest first)
DEFAULT_SHOT_TASKS = [
    "Ingest",
    "Prep",
    "Roto",
    "Matchmove",
    "3D",
    "Comp"
]

# Copy engine defaults
DEFAULT_COPY_WORKERS = 4
VALID_TRANSFER_MODES = ("copy", "hardlink", "symlink")

# Media types that get a low-res preview MOV generated + uploaded to Kitsu.
# Non-visual types (Audio, LUT, Matte, ...) are skipped unless added here.
DEFAULT_PREVIEW_ENABLED_MEDIA_TYPES = ["Plate", "Ref", "BG Plate", "Comp Render", "Precomp"]

# Naming Convention Regex Templates
REGEX_SEQUENCE = r"(?i)(?:SQ|seq)[-_]?(\d{3,4})"
REGEX_SHOT = r"(?i)(?:SH|shot)[-_]?(\d{3,4})"
REGEX_MEDIA_NAME = r"(?i)(?:PL|plate|media|name)[-_]?(\w+|\d+)"
REGEX_FRAME = r"\.(\d{4,7})\.(exr|dpx|png|jpg|jpeg|tif|tiff)$"

# Default File Naming Template
# Variables: {project}, {seq}, {shot}, {type}, {name}, {version}, {frame}, {ext}
DEFAULT_FILE_NAME_TEMPLATE = "{seq}_{shot}_{type}_{name}_v{version:03d}.{frame}{ext}"


def format_dest_filename(template, proj_code, sequence_code, shot_code, media_type, plate_name=None, version_num=1, frame=None, ext=".exr", media_name=None):
    mtype = (media_type or "").strip()
    mname = (media_name or plate_name or "").strip()
    clean_ext = ext if (ext and ext.startswith(".")) else (f".{ext}" if ext else ".exr")

    res = template or DEFAULT_FILE_NAME_TEMPLATE
    res = res.replace("{project}", proj_code or "PROJ")
    res = res.replace("{seq}", sequence_code or "")
    res = res.replace("{shot}", shot_code or "")
    res = res.replace("{media_type}", mtype)
    res = res.replace("{plate_type}", mtype)
    res = res.replace("{type}", mtype)
    res = res.replace("{media_name}", mname)
    res = res.replace("{plate_name}", mname)
    res = res.replace("{name}", mname)
    res = res.replace("{version:03d}", f"{version_num:03d}")
    res = res.replace("{version}", f"{version_num:03d}")

    if frame is not None:
        res = res.replace("{frame}", str(frame))
        res = res.replace("{ext}", clean_ext)
    else:
        res = res.replace(".{frame}", "").replace("_{frame}", "").replace("{frame}", "")
        res = res.replace("{ext}", clean_ext)

    return res

# NAS Directory Template
# Formats into: X:/projects/PROJ_NAME/shots/SQ010/SH0100/input/{plate_type}_{plate_name}/v{version:03d}/{resolution}
SHOT_DIRECTORY_TEMPLATE = os.path.join(
    "{nas_root}",
    "{project_code}",
    "shots",
    "{sequence_code}",
    "{shot_code}",
    "input",
    "{plate_type}_{plate_name}",
    "v{version:03d}",
    "{resolution}"
)

# Standard Shot Directory Structure Hierarchy
SHOT_FOLDER_STRUCTURE = [
    # 2D
    "2D/comp/dailes",
    "2D/comp/elements/DMP",
    "2D/comp/elements/precomp",
    "2D/comp/feedback",
    "2D/comp/ref",
    "2D/comp/render/exr",
    "2D/comp/render/mov",
    "2D/comp/workfiles/mocha",
    "2D/comp/workfiles/nuke",
    "2D/comp/workfiles/sfx",
    "2D/dmp/dailes",
    "2D/dmp/elements/DMP",
    "2D/dmp/elements/precomp",
    "2D/dmp/feedback",
    "2D/dmp/ref",
    "2D/dmp/render/exr",
    "2D/dmp/render/mov",
    "2D/dmp/workfiles/mocha",
    "2D/dmp/workfiles/nuke",
    "2D/dmp/workfiles/sfx",
    "2D/prep/dailes",
    "2D/prep/elements/DMP",
    "2D/prep/elements/precomp",
    "2D/prep/feedback",
    "2D/prep/ref",
    "2D/prep/render/exr",
    "2D/prep/render/mov",
    "2D/prep/workfiles/mocha",
    "2D/prep/workfiles/nuke",
    "2D/prep/workfiles/sfx",
    "2D/roto/dailes",
    "2D/roto/elements/DMP",
    "2D/roto/elements/precomp",
    "2D/roto/feedback",
    "2D/roto/ref",
    "2D/roto/render/exr",
    "2D/roto/render/mov",
    "2D/roto/workfiles/mocha",
    "2D/roto/workfiles/nuke",
    "2D/roto/workfiles/sfx",
    "2D/temp",
    # 3D
    "3D/animation/reference",
    "3D/animation/render",
    "3D/animation/temp",
    "3D/animation/workfiles",
    "3D/env/reference",
    "3D/env/render",
    "3D/env/temp",
    "3D/env/workfiles",
    "3D/fx/reference",
    "3D/fx/render",
    "3D/fx/temp",
    "3D/fx/workfiles",
    "3D/grooming/reference",
    "3D/grooming/render",
    "3D/grooming/temp",
    "3D/grooming/workfiles",
    "3D/lighting/reference",
    "3D/lighting/render",
    "3D/lighting/temp",
    "3D/lighting/workfiles",
    "3D/matchmove/camera",
    "3D/matchmove/dailies",
    "3D/matchmove/geo",
    "3D/matchmove/LD",
    "3D/matchmove/workfiles",
    "3D/rotoanim/reference",
    "3D/rotoanim/render",
    "3D/rotoanim/temp",
    "3D/rotoanim/workfiles",
    # input
    "input",
]

# Render Directory Template
SHOT_RENDER_TEMPLATE = os.path.join(
    "{nas_root}",
    "{project_code}",
    "shots",
    "{sequence_code}",
    "{shot_code}",
    "renders",
    "{task_type}",
    "v{version:03d}"
)


DEFAULT_TOKEN_PRESETS = {
    "Shot_Media_Version": {
        "name": "Shot_Media_Version",
        "delimiter": "_",
        "mapping": {"shot_code": [0], "media_name": [1], "version": [2]},
        "merged_ranges": []
    },
    "Seq_Shot_Media_Version": {
        "name": "Seq_Shot_Media_Version",
        "delimiter": "_",
        "mapping": {"sequence_code": [0], "shot_code": [1], "media_name": [2], "version": [3]},
        "merged_ranges": []
    }
}

# Ingest Presets — the reusable, saveable unit of "how do I tag this delivery".
# Each preset bundles depth rules (apply to every folder at a given depth) and
# pattern rules (apply anywhere in the tree, any depth, by regex/glob match on
# name) so a whole incoming-folder convention can be captured once and
# re-applied to every future batch that follows the same convention.
DEFAULT_INGEST_PRESETS = {
    "VFX Standard 3-Level": {
        "name": "VFX Standard 3-Level",
        "depth_rules": {
            "1": {"type": "direct", "tag": "seq"},
            "2": {"type": "direct", "tag": "shot"},
            "3": {"type": "direct", "tag": "media_name"}
        },
        "pattern_rules": []
    },
    "Nested Sequence + Combined File": {
        "name": "Nested Sequence + Combined File",
        "depth_rules": {
            "1": {"type": "direct", "tag": "seq"},
            "2": {"type": "token_preset", "preset_name": "Shot_Media_Version"}
        },
        "pattern_rules": []
    },
    "Pattern-Based (SEQ/SHOT Anywhere)": {
        "name": "Pattern-Based (SEQ/SHOT Anywhere)",
        "depth_rules": {},
        "pattern_rules": [
            {"name": "Sequence folders anywhere", "pattern": r"(?i)^(?:SQ|seq)[-_]?\d{2,4}$", "is_regex": True,
             "target": "folder", "min_depth": None, "max_depth": None, "action": "level", "level": "seq"},
            {"name": "Shot folders anywhere", "pattern": r"(?i)^(?:SH|shot)[-_]?\d{2,4}$", "is_regex": True,
             "target": "folder", "min_depth": None, "max_depth": None, "action": "level", "level": "shot"}
        ]
    }
}

DEFAULT_MEDIA_TYPE_CONFIGS = {
    "Plate": "{nas_root}/{project_code}/shots/{seq}/{shot}/plates/{media_name}_v{version:03d}",
    "Ref": "{nas_root}/{project_code}/shots/{seq}/{shot}/ref/{media_name}_v{version:03d}",
    "BG Plate": "{nas_root}/{project_code}/shots/{seq}/{shot}/bg_plates/{media_name}_v{version:03d}",
    "Comp Render": "{nas_root}/{project_code}/shots/{seq}/{shot}/comp/{media_name}_v{version:03d}",
    "Precomp": "{nas_root}/{project_code}/shots/{seq}/{shot}/precomp/{media_name}_v{version:03d}",
    "Element": "{nas_root}/{project_code}/shots/{seq}/{shot}/elements/{media_name}",
    "LUT": "{nas_root}/{project_code}/shots/{seq}/{shot}/luts/{media_name}",
    "Audio": "{nas_root}/{project_code}/shots/{seq}/{shot}/audio/{media_name}",
    "Matte": "{nas_root}/{project_code}/shots/{seq}/{shot}/mattes/{media_name}"
}


class StudioConfig:
    """Studio configuration manager."""
    
    def __init__(self, config_path=None):
        if config_path:
            self.config_path = Path(config_path)
        elif "STUDIO_CONFIG_PATH" in os.environ and os.environ["STUDIO_CONFIG_PATH"]:
            self.config_path = Path(os.environ["STUDIO_CONFIG_PATH"])
        else:
            nas_config = Path(__file__).parent.parent.parent / "config" / "studio_config.json"
            repo_config = Path(__file__).parent.parent / "studio_config.json"
            if nas_config.exists():
                self.config_path = nas_config
            else:
                self.config_path = repo_config
        self.kitsu_url = DEFAULT_KITSU_URL
        self.kitsu_user = DEFAULT_KITSU_USER
        self.kitsu_password = DEFAULT_KITSU_PASSWORD
        self.nas_root = DEFAULT_NAS_ROOT
        self.cache_root = DEFAULT_LOCAL_CACHE_ROOT
        self.filename_template = DEFAULT_FILE_NAME_TEMPLATE
        self.nas_dir_template = SHOT_DIRECTORY_TEMPLATE
        self.shot_folder_structure = list(SHOT_FOLDER_STRUCTURE)
        self.tasks = DEFAULT_SHOT_TASKS
        self.dry_run = True

        self.token_presets = dict(DEFAULT_TOKEN_PRESETS)
        self.ingest_presets = dict(DEFAULT_INGEST_PRESETS)
        self.active_ingest_preset = "VFX Standard 3-Level"
        self.media_type_configs = dict(DEFAULT_MEDIA_TYPE_CONFIGS)
        self.preview_enabled_media_types = list(DEFAULT_PREVIEW_ENABLED_MEDIA_TYPES)
        self.copy_workers = DEFAULT_COPY_WORKERS
        self.transfer_mode = "copy"

        self.load()

    def clean_url(self, url):
        """Clean double slashes in URL path except after http:// or https://."""
        if not url:
            return url
        if "://" in url:
            scheme, path = url.split("://", 1)
            while "//" in path:
                path = path.replace("//", "/")
            return f"{scheme}://{path}"
        return url.replace("//", "/")

    def load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.kitsu_url = self.clean_url(data.get("kitsu_url", self.kitsu_url))
                    self.kitsu_user = data.get("kitsu_user", self.kitsu_user)
                    self.kitsu_password = data.get("kitsu_password", self.kitsu_password)
                    self.nas_root = data.get("nas_root", self.nas_root)
                    self.cache_root = data.get("cache_root", self.cache_root)
                    self.filename_template = data.get("filename_template", self.filename_template)
                    self.nas_dir_template = data.get("nas_dir_template", self.nas_dir_template)
                    self.shot_folder_structure = data.get("shot_folder_structure", self.shot_folder_structure)
                    self.dry_run = data.get("dry_run", self.dry_run)
                    self.tasks = data.get("tasks", self.tasks)
                    self.token_presets = data.get("token_presets", self.token_presets)
                    self.media_type_configs = data.get("media_type_configs", self.media_type_configs)
                    self.preview_enabled_media_types = data.get(
                        "preview_enabled_media_types", self.preview_enabled_media_types
                    )
                    self.copy_workers = data.get("copy_workers", self.copy_workers)
                    self.transfer_mode = data.get("transfer_mode", self.transfer_mode)

                    self.ingest_presets = data.get("ingest_presets", self.ingest_presets)
                    self.active_ingest_preset = data.get("active_ingest_preset", self.active_ingest_preset)
            except Exception as e:
                print(f"[StudioConfig] Error loading config: {e}")

    def save(self):
        """
        Merge-on-write: re-reads the file on disk first and updates it with
        this instance's fields, rather than blindly overwriting the whole
        file. Two dialogs (e.g. Settings + Token Tagging) can hold their own
        StudioConfig() snapshot at once; without this, whichever saves last
        would silently wipe out the other's unrelated changes.
        """
        on_disk = {}
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
            except Exception:
                on_disk = {}

        on_disk.update({
            "kitsu_url": self.clean_url(self.kitsu_url),
            "kitsu_user": self.kitsu_user,
            "kitsu_password": self.kitsu_password,
            "nas_root": self.nas_root,
            "cache_root": self.cache_root,
            "filename_template": self.filename_template,
            "nas_dir_template": self.nas_dir_template,
            "shot_folder_structure": self.shot_folder_structure,
            "dry_run": self.dry_run,
            "tasks": self.tasks,
            "token_presets": self.token_presets,
            "ingest_presets": self.ingest_presets,
            "active_ingest_preset": self.active_ingest_preset,
            "media_type_configs": self.media_type_configs,
            "preview_enabled_media_types": self.preview_enabled_media_types,
            "copy_workers": self.copy_workers,
            "transfer_mode": self.transfer_mode,
        })

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(on_disk, f, indent=4)
