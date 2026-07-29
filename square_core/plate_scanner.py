import os
import re
from pathlib import Path
from collections import defaultdict

SUPPORTED_IMAGE_EXTS = {".exr", ".dpx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".m4v"}

class IngestSequenceItem:
    """Represents a discovered plate sequence or video file."""

    def __init__(self, name, files, ext, is_video=False):
        self.name = name  # Base prefix or file name
        self.files = sorted(files)
        self.ext = ext.lower()
        self.is_video = is_video
        
        # Inferred details
        self.sequence_code = "SQ010"
        self.shot_code = "SH0100"
        self.plate_name = "PL01"
        
        self.start_frame = 1001
        self.end_frame = 1001
        self.missing_frames = []
        self.frame_count = len(files)
        self.fps = 24.0
        self.resolution = "1920x1080"
        self.colorspace = "ACEScg"
        
        self.parse_frames()
        self.infer_naming()

    def parse_frames(self):
        if self.is_video or not self.files:
            return

        frame_numbers = []
        # Support both .1001.exr and 1001.exr
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
            
            # Detect missing frames in range
            expected_set = set(range(self.start_frame, self.end_frame + 1))
            actual_set = set(frame_numbers)
            self.missing_frames = sorted(list(expected_set - actual_set))

    def infer_naming(self):
        """Uses regex to smart-detect Sequence, Shot, and Plate names from filename/path."""
        text_to_search = self.name + " " + (self.files[0] if self.files else "")
        
        # Match Sequence (e.g. SQ010, seq_01, sq100)
        sq_match = re.search(r"(?i)(?:SQ|seq)[-_]?(\d{2,4})", text_to_search)
        if sq_match:
            self.sequence_code = f"SQ{int(sq_match.group(1)):03d}"
            
        # Match Shot (e.g. SH0100, shot_10, sh100)
        sh_match = re.search(r"(?i)(?:SH|shot)[-_]?(\d{2,4})", text_to_search)
        if sh_match:
            self.shot_code = f"SH{int(sh_match.group(1)):04d}"

        # Match Plate (e.g. PL01, plate_01, main, fg)
        pl_match = re.search(r"(?i)(?:PL|plate)[-_]?(\w+|\d+)", text_to_search)
        if pl_match:
            raw_pl = pl_match.group(1)
            self.plate_name = f"PL{raw_pl.upper()}" if raw_pl.isdigit() else raw_pl.upper()
        else:
            self.plate_name = "MAIN"

    @property
    def frame_range_str(self):
        if self.is_video:
            return "1 (Video File)"
        return f"{self.start_frame}-{self.end_frame} ({self.frame_count} frames)"

    @property
    def has_warnings(self):
        return len(self.missing_frames) > 0


class PlateScanner:
    """Scans incoming media directories for sequences and single files."""

    def __init__(self, search_path):
        self.search_path = Path(search_path)

    def scan(self):
        """Returns a list of IngestSequenceItem objects found in search_path."""
        if not self.search_path.exists():
            return []

        sequence_groups = defaultdict(list)
        single_videos = []

        pattern_dotted = re.compile(r"^(.*?)[._](\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE)
        pattern_standalone = re.compile(r"^(\d+)\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE)

        for root, _, files in os.walk(self.search_path):
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
            item = IngestSequenceItem(base_prefix, file_list, ext, is_video=False)
            items.append(item)

        items.extend(single_videos)
        return items
