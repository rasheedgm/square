"""
path_pattern.py — full-path, build-by-example tagging.

A PathPattern is a single template string covering an entire relative path
(every folder plus the filename), e.g.:

    <sequence>/<shot>/<media_type>/<sequence>_<shot>_<media_name>.####.<extension>

Built by tagging one real example path, not guessed from hardcoded rules --
every real delivery has a different shape, so a studio saves one template
per shape it actually receives (see FolderMapper's ordered pattern list).

Syntax, evaluated independently within each "/"-separated segment:
    <name>   an open, user-named placeholder. Five names are canonical and
             feed the studio schema directly (sequence, shot, media_type,
             media_name, version); any other name (camera, date,
             colorspace, resolution, ...) is carried as free-form metadata
             instead of being forced into one of those five -- real
             deliveries routinely carry axes that aren't any of them.
    *        an explicit wildcard: matches anything, captures nothing.
             Only appears where the user put one -- untagged text is
             literal by default and must match exactly; a silent
             "anything goes" default is exactly what caused false-positive
             matches (e.g. a bare "sh10" also matching "sh10_ref") before.
    #+       a frame-number run (matches one or more digits). Only ever
             appears in a filename segment, auto-inserted where an image
             sequence's varying frame digits were found in the
             representative example -- never something the user tags by
             hand, and never confused with a literal value to extract.
    anything else is literal and must match exactly (case-insensitive).

Each segment matches independently and wholly (anchored, no partial or
substring matching within a segment) -- so a pattern's segment count must
equal the candidate path's segment count exactly. A delivery with more than
one shape (e.g. one item skips a folder its siblings have) needs more than
one saved PathPattern; FolderMapper tries its ordered list in turn and
takes the first match, so exceptions get their own pattern instead of
forcing one template to describe every shape at once.
"""

import os
import re

# Canonical role names <-> the IngestSequenceItem attributes they feed.
# Kept short, matching the studio's own by-example naming, so a saved
# template string reads exactly like "<sequence>/<shot>/...".
CANONICAL_DISPLAY_TO_ATTR = {
    "sequence":   "sequence_code",
    "shot":       "shot_code",
    "media_type": "media_type",
    "media_name": "media_name",
    "version":    "version",
}
CANONICAL_ATTR_TO_DISPLAY = {v: k for k, v in CANONICAL_DISPLAY_TO_ATTR.items()}
CANONICAL_DISPLAY_NAMES = list(CANONICAL_DISPLAY_TO_ATTR.keys())

# Recognized but discarded: the real file's own extension (item.ext) is
# already known correctly from the scanned file itself, so tagging a chip
# <extension> -- as the studio's own worked example did -- is harmless but
# adds nothing; keeping it out of extra_tags avoids a redundant entry on
# every single row.
IGNORED_DISPLAY_NAMES = {"extension"}

WILDCARD_TOKEN = "*"

_PIECE_RE = re.compile(r"<([A-Za-z0-9_]+)>|(\*)|(#+)")
# Deliberately no separator group before the digit run -- a "bare" numbered
# sequence (e.g. "1001.exr", no prefix at all) has nothing before the frame
# digits, so requiring one would miss it. An empty prefix is a valid match.
_FRAME_IN_NAME_RE = re.compile(r"^(.*?)(\d+)(\.[^.]+)$")


def render_placeholder(name: str) -> str:
    return f"<{name}>"


def render_frame(width: int) -> str:
    return "#" * max(1, width)


def is_frame_piece_text(text: str) -> bool:
    return bool(re.fullmatch(r"#+", text or ""))


def seed_filename_segment(item) -> str:
    """
    Builds the human-readable, taggable seed string for a leaf item's own
    filename segment -- the last piece of the path a Path Pattern is built
    from. For an image sequence, the varying frame-number run is replaced
    with a run of '#' the same length as the real example (never something
    the user has to notice or tag by hand); a video or single image with no
    frame run keeps its real filename untouched.
    """
    if not item.files:
        return item.name
    real_name = os.path.basename(item.files[0])
    if getattr(item, "is_video", False):
        return real_name
    m = _FRAME_IN_NAME_RE.match(real_name)
    if not m:
        return real_name
    prefix, digits, ext = m.groups()
    return f"{prefix}{render_frame(len(digits))}{ext}"


def _compile_segment(segment_template: str):
    """Compiles one "/"-free path segment's template into (regex, [(group_name, display_name), ...])."""
    parts = []
    names = []
    pos = 0
    for m in _PIECE_RE.finditer(segment_template):
        if m.start() > pos:
            parts.append(re.escape(segment_template[pos:m.start()]))
        if m.group(1) is not None:
            # Synthetic group names (not the placeholder's own name) avoid a
            # "redefinition of group name" regex error if the same tag name
            # is ever reused twice within one segment.
            group_name = f"g{len(names)}"
            names.append((group_name, m.group(1)))
            parts.append(f"(?P<{group_name}>.+?)")
        elif m.group(2) is not None:
            parts.append(r".*?")
        else:
            parts.append(r"\d+")
        pos = m.end()
    if pos < len(segment_template):
        parts.append(re.escape(segment_template[pos:]))
    pattern = "^" + "".join(parts) + "$"
    return re.compile(pattern, re.IGNORECASE), names


class PathPattern:
    """One saved full-path template, compiled lazily on first match."""

    def __init__(self, template="", name="", defaults=None):
        self.template = template or ""
        self.name = name or self.template
        # value to use for a token this pattern doesn't capture at all for a
        # given path -- e.g. every delivery under this root is "Plate" even
        # though the folder structure never spells out a media-type segment
        self.defaults = dict(defaults or {})
        self._compiled = None

    def to_dict(self):
        d = {"name": self.name, "template": self.template}
        if self.defaults:
            d["defaults"] = dict(self.defaults)
        return d

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, str):
            return cls(template=data)
        if not data:
            return cls(template="")
        return cls(template=data.get("template", ""), name=data.get("name", ""),
                   defaults=data.get("defaults") or {})

    def _compile(self):
        if self._compiled is None:
            self._compiled = [_compile_segment(seg) for seg in self.template.split("/")]
        return self._compiled

    @property
    def segment_count(self) -> int:
        return len(self.template.split("/")) if self.template else 0

    def match(self, rel_path_posix: str):
        """
        Returns {display_name: captured_text} if the whole relative path
        (POSIX-style, "/" separated, no leading/trailing slash) matches this
        template segment-for-segment, else None. Canonical names sit
        alongside any custom ones in the same dict -- see
        split_canonical_and_extra to divide them for the studio schema.
        """
        if not self.template or not rel_path_posix:
            return None
        candidate_segments = rel_path_posix.split("/")
        compiled = self._compile()
        if len(candidate_segments) != len(compiled):
            return None
        extracted = {}
        for (rx, names), seg in zip(compiled, candidate_segments):
            m = rx.match(seg)
            if not m:
                return None
            for group_name, display_name in names:
                extracted[display_name] = m.group(group_name)
        for token, value in self.defaults.items():
            extracted.setdefault(token, value)
        return extracted


def match_first(patterns, rel_path_posix: str):
    """Tries each PathPattern in order, returns (pattern, extracted_dict) for the first hit, else (None, None)."""
    for pattern in patterns:
        result = pattern.match(rel_path_posix)
        if result is not None:
            return pattern, result
    return None, None


def explode_segment_template(segment_template: str, real_segment: str, delimiter_chars="_.-"):
    """
    Reverse of the build step: given one segment's template and the real text
    it matched, work out the chip breakdown that produced it, so reopening
    the builder on an already-tagged file can show the tagging exactly as it
    was made rather than a blank slate.

    Returns (chips, seps, roles) where roles[i] is None (plain literal),
    WILDCARD_TOKEN, or a placeholder name; chip text is the *real* matched
    value for a placeholder/wildcard (so the user sees their own file's
    values), the literal text otherwise. Returns None when the template
    can't be reversed faithfully -- the caller then falls back to a fresh,
    untagged breakdown rather than showing something subtly wrong.

    Reconstruction is driven by the template rather than by re-tokenizing
    the real text, since a placeholder name can itself contain the
    delimiter (<media_name>) and would otherwise be split in half.
    """
    pieces = []
    pos = 0
    for m in _PIECE_RE.finditer(segment_template):
        if m.start() > pos:
            pieces.append(("lit", segment_template[pos:m.start()]))
        if m.group(1) is not None:
            pieces.append(("ph", m.group(1)))
        elif m.group(2) is not None:
            pieces.append(("wild", m.group(2)))
        else:
            pieces.append(("frame", m.group(3)))
        pos = m.end()
    if pos < len(segment_template):
        pieces.append(("lit", segment_template[pos:]))
    if not pieces:
        return None

    # Pull the real values out of this segment: every placeholder AND
    # wildcard captures, so a wildcard chip can still show the text it's
    # choosing to ignore.
    parts, group_names = [], []
    for kind, val in pieces:
        if kind == "lit":
            parts.append(re.escape(val))
        elif kind == "ph":
            group_names.append(f"g{len(group_names)}")
            parts.append(f"(?P<{group_names[-1]}>.+?)")
        elif kind == "wild":
            group_names.append(f"g{len(group_names)}")
            parts.append(f"(?P<{group_names[-1]}>.*?)")
        else:
            parts.append(r"\d+")
    try:
        matched = re.compile("^" + "".join(parts) + "$", re.IGNORECASE).match(real_segment)
    except re.error:
        return None
    if not matched:
        return None

    chips, seps, roles = [], [], []
    pending = ""

    def push(text, role):
        nonlocal pending
        if chips:
            seps.append(pending)
        pending = ""
        chips.append(text)
        roles.append(role)

    delim_class = re.escape(delimiter_chars)
    group_idx = 0
    for kind, val in pieces:
        if kind == "lit":
            for run in re.findall(f"[{delim_class}]+|[^{delim_class}]+", val):
                if run[0] in delimiter_chars:
                    if chips:
                        pending += run
                    else:
                        push(run, None)   # leading delimiters: keep as their own literal chip
                else:
                    push(run, None)
        elif kind == "ph":
            push(matched.group(group_names[group_idx]), val)
            group_idx += 1
        elif kind == "wild":
            push(matched.group(group_names[group_idx]), WILDCARD_TOKEN)
            group_idx += 1
        else:
            push(val, None)   # the "####" run itself; the UI locks it as the frame piece
    if pending:
        push(pending, None)   # trailing delimiters

    # Only hand back a breakdown that renders exactly back to the template --
    # anything else would silently misrepresent the saved pattern.
    rendered = []
    for i, (text, role) in enumerate(zip(chips, roles)):
        if i:
            rendered.append(seps[i - 1])
        if role == WILDCARD_TOKEN:
            rendered.append(WILDCARD_TOKEN)
        elif role:
            rendered.append(render_placeholder(role))
        else:
            rendered.append(text)
    if "".join(rendered) != segment_template:
        return None
    return chips, seps, roles


def split_canonical_and_extra(extracted: dict):
    """
    Splits a match() result into (canonical_fields, extra_tags).
    canonical_fields uses IngestSequenceItem attribute names and only ever
    contains the five studio-schema keys; extra_tags keeps every other
    placeholder name verbatim, for whatever axis this delivery needs
    (camera, shoot date, colorspace, ...) that isn't one of the five.
    """
    canonical = {}
    extra = {}
    for display_name, value in extracted.items():
        if display_name in IGNORED_DISPLAY_NAMES:
            continue
        attr = CANONICAL_DISPLAY_TO_ATTR.get(display_name)
        if attr:
            canonical[attr] = value
        else:
            extra[display_name] = value
    return canonical, extra
