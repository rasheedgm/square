"""
IngestItem -- one row of the review table, as a plain value object.

Design rules learned from the previous version:

- ONE object holds a row's whole state. No parallel dicts keyed by
  ``id(item)`` (that key can't survive a save/reload and silently broke the
  old NAS check on PySide6).

- ``status`` is DERIVED, never stored. The old code kept a status string
  next to ~12 other dicts/sets and they drifted constantly. Here the status
  is a pure function of the item's facts (issues, skipped, ledger match,
  ingest outcome), computed on read.

- Identity is content-derived: ``key`` is a stable hash of the item's
  sorted source paths, so the same delivery reloads to the same key and a
  session file can round-trip.

No I/O in here. Scanning, metadata probing, hashing and NAS/Kitsu checks
are the controller's job; they hand their results to the item via the
setters below.
"""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum


# ---------------------------------------------------------------------------
# Enums (str-valued so they serialize straight into the session JSON)
# ---------------------------------------------------------------------------

class Status(str, Enum):
    CHECKING          = "Checking"
    NEEDS_INFO        = "Needs Info"
    NEW               = "New"
    NEW_VERSION       = "New Version"
    READY             = "Ready"
    CONFLICT          = "Conflict"
    WARNING           = "Warning"
    ALREADY_INGESTED  = "Already Ingested"
    SKIPPED           = "Skipped"
    CHECK_FAILED      = "Check Failed"
    INGESTING         = "Ingesting"
    COMPLETED         = "Completed"
    FAILED            = "Failed"


# Statuses that "Ingest All" acts on.
INGESTABLE_STATUSES = frozenset({
    Status.NEW, Status.NEW_VERSION, Status.READY, Status.WARNING,
})


class Stage(str, Enum):
    QUEUED            = "Queued"
    HASHING           = "Hashing"
    CHECKING          = "Checking conflicts"
    WAITING           = "Waiting"
    CONVERTING        = "Converting to EXR"
    KITSU_SHOT        = "Creating shot/tasks"
    FOLDERS           = "Creating folders"
    COPYING           = "Copying"
    VERIFYING         = "Verifying"
    PREVIEW_MAKE      = "Generating preview"
    PREVIEW_UPLOAD    = "Uploading preview"
    METADATA          = "Writing metadata"
    DONE              = "Done"
    FAILED            = "Failed"


class Severity(str, Enum):
    BLOCK = "block"   # must be resolved before this row can ingest
    WARN  = "warn"    # ingestable as-is, but flagged


class Action(str, Enum):
    SKIP        = "skip"
    VERSION_UP  = "version_up"
    OVERWRITE   = "overwrite"
    IGNORE      = "ignore"          # warn-only: acknowledge and stop flagging
    EDIT        = "edit"            # not a stored resolution -- "go fix a field"


class IssueKind(str, Enum):
    NEEDS_INFO        = "needs-info"          # a required field / unverified metadata
    DEST_EXISTS_DIFF  = "dest-exists-diff"    # target version folder holds different content
    DEST_COLLISION    = "dest-collision"      # two rows resolve to the same path
    ROLLBACK          = "rollback"            # picked a version <= one already on disk
    ALREADY_IN_SLOT   = "already-in-slot"     # this exact target already holds this exact content
    DUPLICATE_CONTENT = "duplicate-content"   # ledger: identical content ingested elsewhere / another version
    PARTIAL_OVERLAP   = "partial-overlap"     # ledger: some files already ingested
    NEAR_DUP_BATCH    = "near-dup-batch"      # name near-identical to another row
    CASE_INCONSISTENT = "case-inconsistent"   # same code, different case across rows
    ILLEGAL_CHARS     = "illegal-chars"       # field has path-illegal characters
    NO_DEST_TEMPLATE  = "no-dest-template"    # media_type has no configured template
    PREVIEW_NONVISUAL = "preview-nonvisual"   # preview forced on for audio/LUT


# Which actions each issue kind offers (EDIT is always implicitly available).
ISSUE_ACTIONS: dict[IssueKind, tuple[Action, ...]] = {
    IssueKind.NEEDS_INFO:        (),
    IssueKind.DEST_EXISTS_DIFF:  (Action.VERSION_UP, Action.OVERWRITE),
    IssueKind.DEST_COLLISION:    (Action.SKIP, Action.VERSION_UP),
    IssueKind.ROLLBACK:          (Action.VERSION_UP, Action.OVERWRITE),
    IssueKind.ALREADY_IN_SLOT:   (Action.VERSION_UP, Action.OVERWRITE),
    # No OVERWRITE here: this content matched the ledger somewhere OTHER
    # than this row's own (empty) target slot -- there's nothing local to
    # overwrite. Ignore is the "yes, I know, proceed anyway" action.
    IssueKind.DUPLICATE_CONTENT: (Action.SKIP, Action.VERSION_UP, Action.IGNORE),
    IssueKind.PARTIAL_OVERLAP:   (Action.SKIP, Action.VERSION_UP, Action.OVERWRITE),
    IssueKind.NEAR_DUP_BATCH:    (Action.IGNORE,),
    IssueKind.CASE_INCONSISTENT: (Action.IGNORE,),
    IssueKind.ILLEGAL_CHARS:     (),
    IssueKind.NO_DEST_TEMPLATE:  (Action.IGNORE,),
    IssueKind.PREVIEW_NONVISUAL: (Action.IGNORE,),
}

_DEFAULT_SEVERITY: dict[IssueKind, Severity] = {
    IssueKind.NEEDS_INFO:        Severity.BLOCK,
    IssueKind.DEST_EXISTS_DIFF:  Severity.BLOCK,
    IssueKind.DEST_COLLISION:    Severity.BLOCK,
    IssueKind.ROLLBACK:          Severity.BLOCK,
    IssueKind.ILLEGAL_CHARS:     Severity.BLOCK,
    IssueKind.ALREADY_IN_SLOT:   Severity.WARN,
    IssueKind.DUPLICATE_CONTENT: Severity.WARN,
    IssueKind.PARTIAL_OVERLAP:   Severity.WARN,
    IssueKind.NEAR_DUP_BATCH:    Severity.WARN,
    IssueKind.CASE_INCONSISTENT: Severity.WARN,
    IssueKind.NO_DEST_TEMPLATE:  Severity.WARN,
    IssueKind.PREVIEW_NONVISUAL: Severity.WARN,
}

# Illegal in a Windows path component (also covers the POSIX-hostile ones).
_ILLEGAL_PATH_CHARS = set('<>:"/\\|?*')

REQUIRED_FIELDS = ("sequence_code", "shot_code", "media_type", "media_name")
# Metadata that must be known (read from the file or set by the user) before
# ingest -- shipping a guessed colorspace to Kitsu is worse than none.
REQUIRED_METADATA = ("resolution", "fps", "colorspace")


@dataclass
class Issue:
    kind: IssueKind
    message: str
    severity: Severity = Severity.BLOCK
    column: str = ""                      # the table column this is about, if any
    data: dict = field(default_factory=dict)   # kind-specific detail (other row key, existing names, ...)

    @property
    def id(self) -> str:
        # Stable within an item: one issue per (kind, column).
        return f"{self.kind.value}:{self.column}" if self.column else self.kind.value

    @property
    def actions(self) -> tuple[Action, ...]:
        return ISSUE_ACTIONS.get(self.kind, ())


# ---------------------------------------------------------------------------
# IngestItem
# ---------------------------------------------------------------------------

@dataclass
class IngestItem:
    # identity / source
    key: str
    source_files: list[str]
    ext: str = ""
    is_video: bool = False
    source_name: str = ""                 # the scanner's group name, for display

    # tagged / editable
    sequence_code: str = ""
    shot_code: str = ""
    media_type: str = ""
    media_name: str = ""
    version: int = 1
    extra_tags: dict = field(default_factory=dict)
    # snapshot of the renameable fields' values the moment this row first
    # entered the controller (controller.load()) -- {original} in a rename
    # template reads from here, {current} reads the live field instead, so
    # the same template means "as loaded" vs "right now" regardless of which
    # field it's applied to.
    original_values: dict = field(default_factory=dict)

    # frame info (from the scanner)
    start_frame: int = 1001
    end_frame: int = 1001
    missing_frames: list[int] = field(default_factory=list)
    frame_count: int = 0

    # grabbed metadata + which of it was actually read from the file
    fps: float | None = None
    resolution: str = ""
    colorspace: str = ""
    timecode: str = ""
    width: int = 0
    height: int = 0
    metadata_verified: dict = field(default_factory=dict)   # {field: bool}
    metadata_backend: str = ""

    # derived (filled by the controller from templates)
    dest_dir: str = ""
    sample_dest_file: str = ""

    # per-session decisions
    preview_wanted: bool = False
    preview_default: bool = False         # what config says for this media type
    preview_user_set: bool = False        # user ticked/unticked it -> stop auto-following media_type
    skipped: bool = False
    convert_to_exr: bool = False          # is_video only: decode to an EXR sequence before ingesting

    # check results
    preflight_done: bool = False
    check_error: str = ""                 # non-empty => Check Failed
    issues: list[Issue] = field(default_factory=list)
    resolutions: dict = field(default_factory=dict)         # {issue_id: Action}
    hashes: dict = field(default_factory=dict)              # {source_path: digest}
    ledger_kind: str = ""                 # "", "none", "partial", "full"
    ledger_detail: str = ""
    slot_state: str = ""                  # "", "empty", "already", "conflict" -- THIS version's target folder

    # ingest outcome
    stage: Stage = Stage.QUEUED
    stage_pct: int = 0
    ingested: bool = False
    ingest_error: str = ""
    ingest_result: dict = field(default_factory=dict)       # dest paths, checksums, kitsu ids
    # preview runs off the critical path: "" | pending | running | done | failed | skipped
    preview_state: str = ""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @staticmethod
    def compute_key(files) -> str:
        norm = sorted(os.path.normcase(os.path.abspath(f)) for f in files)
        digest = hashlib.sha1("\n".join(norm).encode("utf-8", "surrogatepass")).hexdigest()
        return digest[:16]

    @classmethod
    def from_scan_item(cls, scan_item) -> "IngestItem":
        """Adapt a plate_scanner.IngestSequenceItem into an IngestItem."""
        files = list(getattr(scan_item, "files", []) or [])
        item = cls(
            key=cls.compute_key(files) if files else hashlib.sha1(
                getattr(scan_item, "name", "").encode()).hexdigest()[:16],
            source_files=files,
            ext=getattr(scan_item, "ext", ""),
            is_video=bool(getattr(scan_item, "is_video", False)),
            source_name=getattr(scan_item, "name", ""),
            sequence_code=getattr(scan_item, "sequence_code", "") or "",
            shot_code=getattr(scan_item, "shot_code", "") or "",
            media_type=getattr(scan_item, "media_type", "") or "",
            media_name=getattr(scan_item, "media_name", "") or "",
            version=int(getattr(scan_item, "version", 1) or 1),
            extra_tags=dict(getattr(scan_item, "extra_tags", {}) or {}),
            start_frame=getattr(scan_item, "start_frame", 1001),
            end_frame=getattr(scan_item, "end_frame", 1001),
            missing_frames=list(getattr(scan_item, "missing_frames", []) or []),
            frame_count=getattr(scan_item, "frame_count", len(files)),
        )
        # A Path Pattern's fps/resolution/colorspace default (for a delivery
        # whose files never carry that metadata) counts as verified, exactly
        # like a real extraction would -- probe_metadata() still overwrites
        # it later if the file itself actually yields a real value.
        for f in getattr(scan_item, "metadata_defaulted", ()) or ():
            value = getattr(scan_item, f, None)
            if f == "fps":
                item.fps = value
            else:
                setattr(item, f, value)
            item.metadata_verified[f] = True
        return item

    # ------------------------------------------------------------------
    # Metadata probe (called by the controller during pre-flight)
    # ------------------------------------------------------------------

    def probe_metadata(self, extractor=None) -> None:
        """
        Fill fps/resolution/colorspace/timecode/width/height from the first
        source file, recording per-field whether it was really read
        (metadata_verified[field] = True) or is still unknown.
        """
        if extractor is None:
            from square_core.media.metadata import MetadataExtractor
            extractor = MetadataExtractor
        found, backend = ({}, None)
        if self.source_files:
            found, backend = extractor.probe(self.source_files[0])
        self.metadata_backend = backend or ""

        for f in ("width", "height", "resolution", "fps", "colorspace", "timecode"):
            if f in found and found[f] not in (None, ""):
                setattr(self, f, found[f])
                self.metadata_verified[f] = True
            else:
                self.metadata_verified.setdefault(f, False)

    # ------------------------------------------------------------------
    # Resolutions
    # ------------------------------------------------------------------

    def resolve(self, issue_id: str, action: Action) -> None:
        if action == Action.SKIP:
            self.skipped = True
        self.resolutions[issue_id] = action

    def unresolve(self, issue_id: str) -> None:
        self.resolutions.pop(issue_id, None)

    def clear_resolutions(self) -> None:
        self.resolutions.clear()

    def include(self) -> None:
        """Undo a skip."""
        self.skipped = False
        # drop any SKIP resolutions so their issues re-surface
        for iid, act in list(self.resolutions.items()):
            if act == Action.SKIP:
                self.resolutions.pop(iid)

    def is_resolved(self, issue_id: str) -> bool:
        return issue_id in self.resolutions

    def overwrite_ok(self) -> bool:
        """True if the user accepted an OVERWRITE for any blocking dest issue."""
        return any(a == Action.OVERWRITE for a in self.resolutions.values())

    # ------------------------------------------------------------------
    # Derived state
    # ------------------------------------------------------------------

    @property
    def unresolved_issues(self) -> list[Issue]:
        return [i for i in self.issues if not self.is_resolved(i.id)]

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.unresolved_issues if i.severity == Severity.BLOCK]

    @property
    def warning_issues(self) -> list[Issue]:
        return [i for i in self.unresolved_issues if i.severity == Severity.WARN]

    def missing_fields(self) -> list[str]:
        out = [f for f in REQUIRED_FIELDS if not (getattr(self, f) or "").strip()]
        for f in REQUIRED_METADATA:
            if not self.metadata_verified.get(f) and not (str(getattr(self, f) or "")).strip():
                out.append(f)
        return out

    @property
    def status(self) -> Status:
        if self.ingested:
            return Status.COMPLETED
        if self.ingest_error:
            return Status.FAILED
        if self.stage not in (Stage.QUEUED, Stage.DONE, Stage.FAILED):
            return Status.INGESTING
        if self.skipped:
            return Status.SKIPPED
        if self.check_error:
            return Status.CHECK_FAILED
        if not self.preflight_done:
            return Status.CHECKING
        # "Already Ingested" means THIS exact target folder already holds THIS
        # exact content -- a genuine no-op. A ledger match to some OTHER
        # destination (same bytes ingested elsewhere / as another version) is
        # only a warning, surfaced as a DUPLICATE_CONTENT issue instead.
        if self.slot_state == "already" and not self.overwrite_ok():
            return Status.ALREADY_INGESTED

        blocks = self.blocking_issues
        if blocks:
            # A missing required field / unverified colorspace reads as
            # "Needs Info" even if there's also a real conflict -- you can't
            # meaningfully resolve a conflict on a row whose identity isn't
            # filled in yet.
            if any(b.kind == IssueKind.NEEDS_INFO for b in blocks):
                return Status.NEEDS_INFO
            return Status.CONFLICT
        if self.warning_issues:
            return Status.WARNING

        had_resolved_block = any(
            self.is_resolved(i.id) and i.severity == Severity.BLOCK for i in self.issues
        )
        if had_resolved_block:
            return Status.READY
        if self.version > 1 or self.ledger_kind in ("partial",):
            return Status.NEW_VERSION
        return Status.NEW

    @property
    def ingestable(self) -> bool:
        return self.status in INGESTABLE_STATUSES

    @property
    def files(self) -> list[str]:
        """Alias so NASManager helpers (written against the scanner item) work on an IngestItem too."""
        return self.source_files

    @property
    def frame_range_str(self) -> str:
        if self.is_video:
            return "1 (Video File)"
        return f"{self.start_frame}-{self.end_frame} ({self.frame_count} frames)"

    # ------------------------------------------------------------------
    # Serialization (session file)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["issues"] = [
            {
                "kind": i.kind.value, "message": i.message,
                "severity": i.severity.value, "column": i.column, "data": i.data,
            }
            for i in self.issues
        ]
        d["stage"] = self.stage.value
        d["resolutions"] = {k: (v.value if isinstance(v, Action) else v)
                            for k, v in self.resolutions.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "IngestItem":
        d = dict(d)
        issues = [
            Issue(
                kind=IssueKind(i["kind"]), message=i.get("message", ""),
                severity=Severity(i.get("severity", "block")),
                column=i.get("column", ""), data=i.get("data", {}) or {},
            )
            for i in d.pop("issues", []) or []
        ]
        stage = Stage(d.pop("stage", Stage.QUEUED.value))
        resolutions = {k: Action(v) for k, v in (d.pop("resolutions", {}) or {}).items()}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        item = cls(**{k: v for k, v in d.items() if k in known})
        item.issues = issues
        item.stage = stage
        item.resolutions = resolutions
        return item
