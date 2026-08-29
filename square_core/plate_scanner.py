import os
import re
from pathlib import Path
from collections import defaultdict

SUPPORTED_IMAGE_EXTS = {".exr", ".dpx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".m4v"}

class IngestSequenceItem:
    """Represents a discovered media sequence or video file."""

    def __init__(self, name, files, ext, is_video=False):
        self.name = name  # Base prefix or file name
        self.files = sorted(files)
        self.ext = ext.lower()
        self.is_video = is_video

        self.sequence_code = ""
        self.shot_code     = ""
        self.media_name    = ""
        self.media_type    = ""
        self.version       = 1

        self.start_frame   = 1001
        self.end_frame     = 1001
        self.missing_frames = []
        self.frame_count   = len(files)
        self.width         = 1920
        self.height        = 1080
        self.fps           = 24.0
        self.resolution    = "1920x1080"
        self.colorspace    = "ACEScg"
        self.timecode      = "01:00:00:00"

        self.parse_frames()
        self.extract_metadata()

    @property
    def plate_name(self):
        return self.media_name

    @plate_name.setter
    def plate_name(self, value):
        self.media_name = value

    def parse_frames(self):
        if self.is_video or not self.files:
            return

        frame_numbers = []
        pattern = re.compile(r"(?:[._]|^)(\d+)\." + re.escape(self.ext.lstrip(".")) + r"$", re.IGNORECASE)

        for filepath in self.files:
            filename = os.path.basename(filepath)
            match = pattern.search(filename)
            if match:
                frame_numbers.append(int(match.group(1)))

        if frame_numbers:
            frame_numbers.sort()
            self.start_frame = frame_numbers[0]
            self.end_frame = frame_numbers[-1]
            
            expected_set = set(range(self.start_frame, self.end_frame + 1))
            actual_set = set(frame_numbers)
            self.missing_frames = sorted(list(expected_set - actual_set))

    def infer_naming(self):
        """Disabled auto pattern matching as instructed."""
        pass

    def extract_metadata(self):
        """
        Reads real resolution/fps/colorspace/timecode from one representative
        file (the first frame of a sequence, or the video file itself).
        Failures are non-fatal -- the hardcoded defaults set in __init__
        stay in place if the file can't be inspected (missing, unreadable,
        no backend installed for its format).
        """
        if not self.files:
            return
        try:
            from square_core.metadata_extractor import MetadataExtractor
            meta = MetadataExtractor.extract_metadata(self.files[0])
        except Exception:
            return
        if not meta:
            return
        self.width = meta.get("width", self.width)
        self.height = meta.get("height", self.height)
        self.resolution = meta.get("resolution", self.resolution)
        self.fps = meta.get("fps", self.fps)
        self.colorspace = meta.get("colorspace", self.colorspace)
        self.timecode = meta.get("timecode", self.timecode)

    @property
    def frame_range_str(self):
        if self.is_video:
            return "1 (Video File)"
        return f"{self.start_frame}-{self.end_frame} ({self.frame_count} frames)"

    @property
    def has_warnings(self):
        return len(self.missing_frames) > 0


class PlateScanner:
    """Scans incoming media directories for sequences and single files with deep directory robustness."""

    def __init__(self, root_path=None, search_path=None):
        target = root_path if root_path is not None else search_path
        self.search_path = Path(target) if target else Path(".")

    def scan(self):
        """Returns a list of IngestSequenceItem objects found in search_path."""
        if not self.search_path.exists():
            return []

        sequence_groups = defaultdict(list)
        single_videos = []

        pattern_dotted = re.compile(r"^(.*?)[._](\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE)
        pattern_standalone = re.compile(r"^(\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE)

        root_path_str = str(self.search_path.resolve())
        if os.name == 'nt' and not root_path_str.startswith('\\\\?\\') and len(root_path_str) > 240:
            root_path_str = '\\\\?\\' + root_path_str

        for root, dirs, files in os.walk(root_path_str, onerror=lambda err: None, followlinks=False):
            folder_name = os.path.basename(root)
            for file in files:
                filepath = os.path.join(root, file)
                ext = os.path.splitext(file)[1].lower()

                if ext in SUPPORTED_VIDEO_EXTS:
                    single_videos.append(IngestSequenceItem(file, [filepath], ext, is_video=True))
                elif ext in SUPPORTED_IMAGE_EXTS:
                    match_dotted = pattern_dotted.match(file)
                    match_standalone = pattern_standalone.match(file)

                    if match_dotted and match_dotted.group(1):
                        base_prefix = match_dotted.group(1)
                    elif match_standalone:
                        base_prefix = folder_name
                    else:
                        base_prefix = file

                    group_key = (root, base_prefix, ext)
                    sequence_groups[group_key].append(filepath)

        items = []
        for (root, base_prefix, ext), file_list in sequence_groups.items():
            items.append(IngestSequenceItem(base_prefix, file_list, ext, is_video=False))

        items.extend(single_videos)
        return items
