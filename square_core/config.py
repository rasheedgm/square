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

# NAS Directory Template
# Formats into: X:/projects/PROJ_NAME/shots/SQ010/SH0100/plates/PL01/v001/
SHOT_DIRECTORY_TEMPLATE = os.path.join(
    "{nas_root}",
    "{project_code}",
    "shots",
    "{sequence_code}",
    "{shot_code}",
    "plates",
    "{plate_name}",
    "v{version:03d}"
)

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
        self.config_path = config_path or Path(__file__).parent.parent / "studio_config.json"
        self.kitsu_url = DEFAULT_KITSU_URL
        self.kitsu_user = DEFAULT_KITSU_USER
        self.kitsu_password = DEFAULT_KITSU_PASSWORD
        self.nas_root = DEFAULT_NAS_ROOT
        self.cache_root = DEFAULT_LOCAL_CACHE_ROOT
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
            "dry_run": self.dry_run
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
