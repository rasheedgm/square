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

# Naming Convention Regex Templates
REGEX_SEQUENCE = r"(?i)(?:SQ|seq)[-_]?(\d{3,4})"
REGEX_SHOT = r"(?i)(?:SH|shot)[-_]?(\d{3,4})"
REGEX_PLATE = r"(?i)(?:PL|plate)[-_]?(\w+|\d+)"
REGEX_FRAME = r"\.(\d{4,7})\.(exr|dpx|png|jpg|jpeg|tif|tiff)$"

# Default File Naming Template
# Variables: {project}, {seq}, {shot}, {type}, {name}, {version}, {frame}, {ext}
DEFAULT_FILE_NAME_TEMPLATE = "{seq}_{shot}_{type}_{name}_v{version:03d}.{frame}{ext}"


def format_dest_filename(template, proj_code, sequence_code, shot_code, media_type, plate_name, version_num, frame=None, ext=".exr"):
    mtype = (media_type or "Plate").strip()
    pname = (plate_name or "PL01").strip()
    clean_ext = ext if (ext and ext.startswith(".")) else (f".{ext}" if ext else ".exr")

    res = template or DEFAULT_FILE_NAME_TEMPLATE
    res = res.replace("{project}", proj_code or "PROJ")
    res = res.replace("{seq}", sequence_code or "SQ010")
    res = res.replace("{shot}", shot_code or "SH0100")
    res = res.replace("{type}", mtype)
    res = res.replace("{name}", pname)
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
            except Exception as e:
                print(f"[StudioConfig] Error loading config: {e}")

    def save(self):
        data = {
            "kitsu_url": self.clean_url(self.kitsu_url),
            "kitsu_user": self.kitsu_user,
            "kitsu_password": self.kitsu_password,
            "nas_root": self.nas_root,
            "cache_root": self.cache_root,
            "filename_template": self.filename_template,
            "nas_dir_template": self.nas_dir_template,
            "shot_folder_structure": self.shot_folder_structure,
            "dry_run": self.dry_run
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
