import os
import json
from pathlib import Path

DEFAULT_KITSU_URL = os.getenv("KITSU_URL", "http://localhost/api")
DEFAULT_KITSU_USER = os.getenv("KITSU_USER", "admin@example.com")
DEFAULT_KITSU_PASSWORD = os.getenv("KITSU_PASSWORD", "12345678")

# NAS Storage Settings
DEFAULT_NAS_ROOT = os.getenv("SQUARE_NAS_ROOT", "X:/projects")

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

# Default File Naming Template
# Variables: {project}, {seq}, {shot}, {type}, {name}, {version}, {frame}, {ext}
DEFAULT_FILE_NAME_TEMPLATE = "{seq}_{shot}_{type}_{name}_v{version:03d}.{frame}{ext}"


def format_dest_filename(template, proj_code, sequence_code, shot_code, media_type, media_name=None, version_num=1, frame=None, ext=".exr"):
    mtype = (media_type or "").strip()
    mname = (media_name or "").strip()
    clean_ext = ext if (ext and ext.startswith(".")) else (f".{ext}" if ext else ".exr")

    res = template or DEFAULT_FILE_NAME_TEMPLATE
    res = res.replace("{project}", proj_code or "PROJ")
    res = res.replace("{seq}", sequence_code or "")
    res = res.replace("{shot}", shot_code or "")
    res = res.replace("{media_type}", mtype)
    res = res.replace("{type}", mtype)
    res = res.replace("{media_name}", mname)
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
# Formats into: X:/projects/PROJ_NAME/shots/SQ010/SH0100/input/{media_type}_{media_name}/v{version:03d}/{resolution}
SHOT_DIRECTORY_TEMPLATE = os.path.join(
    "{nas_root}",
    "{project_code}",
    "shots",
    "{sequence_code}",
    "{shot_code}",
    "input",
    "{media_type}_{media_name}",
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


# Ingest Presets — the reusable, saveable unit of "how do I tag this
# delivery": an ordered list of Path Pattern template strings (see
# path_pattern.py), tried in order with the first match winning, so a whole
# incoming-folder convention -- including its exceptions -- can be captured
# once and re-applied to every future batch that follows it.
#
# Deliberately shipped empty: there is no universal delivery shape (folder
# depth, naming convention, prefix or none) for a default preset to assume.
# A real one is only meaningful once a studio builds and saves it for their
# own footage via the Path Pattern builder ("Build Path Pattern...").
DEFAULT_INGEST_PRESETS = {}

DEFAULT_MEDIA_TYPE_CONFIGS = {
    # Every entry MUST include {version} -- without it, re-ingesting the same
    # media at v2 writes into the exact same folder as v1 and overwrites it.
    # Element/LUT/Audio/Matte used to omit it entirely (only 5 of these 9
    # templates versioned on disk); fixed to match the other five.
    "Plate": "{nas_root}/{project_code}/shots/{seq}/{shot}/plates/{media_name}_v{version:03d}",
    "Ref": "{nas_root}/{project_code}/shots/{seq}/{shot}/ref/{media_name}_v{version:03d}",
    "BG Plate": "{nas_root}/{project_code}/shots/{seq}/{shot}/bg_plates/{media_name}_v{version:03d}",
    "Comp Render": "{nas_root}/{project_code}/shots/{seq}/{shot}/comp/{media_name}_v{version:03d}",
    "Precomp": "{nas_root}/{project_code}/shots/{seq}/{shot}/precomp/{media_name}_v{version:03d}",
    "Element": "{nas_root}/{project_code}/shots/{seq}/{shot}/elements/{media_name}_v{version:03d}",
    "LUT": "{nas_root}/{project_code}/shots/{seq}/{shot}/luts/{media_name}_v{version:03d}",
    "Audio": "{nas_root}/{project_code}/shots/{seq}/{shot}/audio/{media_name}_v{version:03d}",
    "Matte": "{nas_root}/{project_code}/shots/{seq}/{shot}/mattes/{media_name}_v{version:03d}"
}

# The kwargs get_dest_dir() actually passes to a directory template's
# .format() call -- used to sanity-check a template before trusting it.
_DEST_TEMPLATE_PROBE_KWARGS = dict(
    nas_root="X", project_code="X", sequence_code="X", shot_code="X",
    seq="X", shot="X", media_type="X", type="X", media_name="X", name="X",
    version=1, resolution="X",
)


def dest_template_renders(template):
    """
    True if `template` successfully formats with the keys get_dest_dir()
    provides. Used to reject a persisted nas_dir_template that still uses
    retired placeholder names (e.g. {plate_type}/{plate_name} from before
    the plate-to-media rename) instead of silently round-tripping it through
    load()/save() forever -- get_dest_dir()'s own except-and-fall-back for a
    template that fails to render is a last resort, not something a stale
    saved setting should be allowed to trigger on every single ingest.
    """
    if not template:
        return False
    try:
        template.format(**_DEST_TEMPLATE_PROBE_KWARGS)
        return True
    except Exception:
        return False


def dest_template_versions_safely(template):
    """
    True if `template` both renders AND actually varies by version -- a
    per-media-type template with no {version} placeholder at all (a real
    persisted config was found with exactly this for Element/LUT/Audio/
    Matte) makes every version alias onto the same folder, so ingesting v2
    silently overwrites v1.
    """
    if not dest_template_renders(template):
        return False
    v1 = template.format(**{**_DEST_TEMPLATE_PROBE_KWARGS, "version": 1})
    v2 = template.format(**{**_DEST_TEMPLATE_PROBE_KWARGS, "version": 2})
    return v1 != v2


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
        self.filename_template = DEFAULT_FILE_NAME_TEMPLATE
        self.nas_dir_template = SHOT_DIRECTORY_TEMPLATE
        self.shot_folder_structure = list(SHOT_FOLDER_STRUCTURE)
        self.tasks = DEFAULT_SHOT_TASKS
        self.dry_run = True

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
                    self.filename_template = data.get("filename_template", self.filename_template)
                    loaded_dir_template = data.get("nas_dir_template", self.nas_dir_template)
                    if dest_template_versions_safely(loaded_dir_template):
                        self.nas_dir_template = loaded_dir_template
                    else:
                        print(
                            f"[StudioConfig] Saved nas_dir_template doesn't render with the current "
                            f"placeholder names (expects {{media_type}}/{{media_name}}, not e.g. "
                            f"{{plate_type}}/{{plate_name}}), or doesn't vary by {{version}} -- either "
                            f"way every version would land in the same folder, so ignoring it and "
                            f"using the built-in default instead: {SHOT_DIRECTORY_TEMPLATE}"
                        )
                        self.nas_dir_template = SHOT_DIRECTORY_TEMPLATE
                    self.shot_folder_structure = data.get("shot_folder_structure", self.shot_folder_structure)
                    self.dry_run = data.get("dry_run", self.dry_run)
                    self.tasks = data.get("tasks", self.tasks)

                    loaded_type_configs = data.get("media_type_configs", {})
                    fixed_type_configs = dict(self.media_type_configs)
                    for mtype, mtmpl in loaded_type_configs.items():
                        if dest_template_versions_safely(mtmpl):
                            fixed_type_configs[mtype] = mtmpl
                        elif mtype in DEFAULT_MEDIA_TYPE_CONFIGS:
                            print(
                                f"[StudioConfig] Saved media_type_configs['{mtype}'] doesn't vary by "
                                f"{{version}} -- every version would overwrite the same folder. Using "
                                f"the built-in default for '{mtype}' instead: "
                                f"{DEFAULT_MEDIA_TYPE_CONFIGS[mtype]}"
                            )
                            fixed_type_configs[mtype] = DEFAULT_MEDIA_TYPE_CONFIGS[mtype]
                        else:
                            # A custom type we have no built-in default for -- keep the
                            # studio's own choice, but make sure it's not silently unsafe.
                            print(
                                f"[StudioConfig] media_type_configs['{mtype}'] = {mtmpl!r} does not "
                                f"vary by {{version}} -- every ingested version of this media type "
                                f"will land in the same folder and overwrite the last. If that isn't "
                                f"intentional, add {{version}} to it in Settings."
                            )
                            fixed_type_configs[mtype] = mtmpl
                    self.media_type_configs = fixed_type_configs
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
            "filename_template": self.filename_template,
            "nas_dir_template": self.nas_dir_template,
            "shot_folder_structure": self.shot_folder_structure,
            "dry_run": self.dry_run,
            "tasks": self.tasks,
            "ingest_presets": self.ingest_presets,
            "active_ingest_preset": self.active_ingest_preset,
            "media_type_configs": self.media_type_configs,
            "preview_enabled_media_types": self.preview_enabled_media_types,
            "copy_workers": self.copy_workers,
            "transfer_mode": self.transfer_mode,
        })

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(on_disk, f, indent=4)
