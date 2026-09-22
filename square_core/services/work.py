"""work -- the workfile + output lifecycle for a DCC task.

**Workfiles.** A workfile is a DCC scene saved against a task. It has a *name*
(a stream: `main`, `precomp`, `final`, ...), a *major* version, and a *minor*
version. Only **majors** are recorded in Kitsu (`working_files`, one integer
`revision`); **minors** are plain disk saves between majors -- the tool globs
them from the version directory. `next_save` decides where the next save lands;
`register_major` records a new major in Kitsu after the DCC has written its
first minor.

**Outputs.** A published render / cache -- a Kitsu `output_file`, major only,
through `media.publish`. An output can be *locked* (reviewed / delivered) so a
DCC won't re-render over it.
"""

from __future__ import annotations

import logging
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from square_core.model import Workfile

from . import media
from ._common import entity_coords as _entity_coords
from ._common import task_name as _task_name

logger = logging.getLogger("square.services.work")

DEFAULT_WORKFILE_TYPE = "NukeScript"

_MINOR_IN_NAME = re.compile(r"_v\d+\.(\d+)\.[^.]+$")
_VERSION_IN_NAME = re.compile(r"_v(\d+)\.(\d+)\.[^.]+$")


# ---- value objects --------------------------------------------------

@dataclass
class MinorFile:
    minor: int
    path: str
    online: bool = True            # the file is present at `path`
    modified: float = 0.0          # mtime, when online

    @property
    def is_rendered_snapshot(self) -> bool:
        """Minor 0 is reserved -- see snapshot_rendered_script() -- never an
        artist WIP save."""
        return self.minor == 0


@dataclass
class MajorVersion:
    major: int
    name: str
    record: object = None          # model.Workfile (the Kitsu major)
    comment: str = ""
    minors: list = field(default_factory=list)     # newest minor first

    @property
    def online(self) -> bool:
        return any(m.online for m in self.minors)

    @property
    def latest(self) -> "MinorFile | None":
        return self.minors[0] if self.minors else None

    def label(self) -> str:
        m = self.latest
        return f"v{self.major:03d}.{m.minor:03d}" if m else f"v{self.major:03d}"


@dataclass
class SaveTarget:
    path: str
    major: int
    minor: int
    is_new_major: bool

    def label(self) -> str:
        return f"v{self.major:03d}.{self.minor:03d}"


@dataclass
class WorkfileSlot:
    path: str
    major: int
    minor: int
    record: object = None
    seeded: bool = False
    seeded_from: str = ""

    # backwards-friendly alias
    @property
    def revision(self) -> int:
        return self.major


# ---- paths --------------------------------------------------------

def workfile_path(pctx, entity, task, *, media_type=DEFAULT_WORKFILE_TYPE, major: int,
                  minor: int = 1, software: str = "", name: str = "main") -> str:
    ctx = pctx.ctx(**_entity_coords(entity), task=_task_name(task), software=software,
                   version=major, minor=minor, name=name, ext="")
    return pctx.paths.media_path(media_type, ctx)


def _scan_minors(pctx, entity, task, media_type, name, major) -> list[MinorFile]:
    sample = workfile_path(pctx, entity, task, media_type=media_type, major=major,
                           minor=1, name=name)
    p = Path(sample)
    if not p.parent.exists():
        return []
    # "..._v001.001.nk" -> glob "..._v001.*.nk"
    pattern = re.sub(r"(_v\d+\.)\d+(\.[^.]+)$", r"\1*\2", p.name)
    out: list[MinorFile] = []
    for f in p.parent.glob(pattern):
        m = _MINOR_IN_NAME.search(f.name)
        if m:
            out.append(MinorFile(minor=int(m.group(1)), path=str(f),
                                 online=True, modified=f.stat().st_mtime))
    return sorted(out, key=lambda x: x.minor, reverse=True)


# ---- reading ----------------------------------------------------

def workfile_names(pctx, task) -> list[str]:
    """The workfile streams that exist on this task (always includes 'main')."""
    names = {w.name or "main" for w in pctx.kitsu.working_files(task)}
    names.add("main")
    return sorted(names)


def workfile_versions(pctx, entity, task, *, name: str = "main",
                      media_type: str = DEFAULT_WORKFILE_TYPE) -> list[MajorVersion]:
    """Every major version on this task/name (oldest first), each carrying its
    minor `.nk` files found on disk (newest minor first)."""
    majors = sorted((w for w in pctx.kitsu.working_files(task)
                     if (w.name or "main") == name),
                    key=lambda w: w.revision)
    out = []
    for w in majors:
        out.append(MajorVersion(
            major=w.revision, name=name, record=w, comment=w.comment or "",
            minors=_scan_minors(pctx, entity, task, media_type, name, w.revision)))
    return out


def latest_major(pctx, entity, task, *, name="main",
                 media_type=DEFAULT_WORKFILE_TYPE) -> "MajorVersion | None":
    vs = workfile_versions(pctx, entity, task, name=name, media_type=media_type)
    return vs[-1] if vs else None


def outputs(pctx, entity, media_type: str, *, name: str = "") -> list:
    """Published output versions of `media_type` on `entity`, oldest first.
    Filtered to `name` when given -- Kitsu's own output-files lookup doesn't
    do this itself (every output of a media_type comes back regardless of
    name), so without it, two different name-streams under the same
    media_type on the same shot (e.g. Precomp "fg" and "bg") collide in any
    "existing revisions" dict keyed purely by .revision -- wrong lock
    checks, wrong "already exists" detection. `name=""` (the default)
    returns everything, unfiltered, for callers that genuinely want that."""
    all_ = media.list_versions(pctx, entity, media_type)
    if name:
        all_ = [o for o in all_ if (getattr(o, "name", "") or "main") == name]
    return sorted(all_, key=lambda o: getattr(o, "revision", 0))


# ---- saving --------------------------------------------------

def next_save(pctx, entity, task, *, name: str = "main",
              media_type: str = DEFAULT_WORKFILE_TYPE, bump: str = "minor") -> SaveTarget:
    """Where the next save should land.

    `bump="minor"` -> same major, next minor on disk.
    `bump="major"` -> next Kitsu revision, minor 1 (also used for the first save).
    """
    majors = [w for w in pctx.kitsu.working_files(task) if (w.name or "main") == name]
    if bump == "major" or not majors:
        major = pctx.kitsu.next_working_revision(task, name=name)
        minor, is_new = 1, True
    else:
        major = max(w.revision for w in majors)
        found = _scan_minors(pctx, entity, task, media_type, name, major)
        minor, is_new = ((found[0].minor + 1) if found else 1), False
    path = workfile_path(pctx, entity, task, media_type=media_type, major=major,
                         minor=minor, name=name)
    return SaveTarget(path=path, major=major, minor=minor, is_new_major=is_new)


def register_major(pctx, entity, task, target: SaveTarget, *, name: str = "main",
                   media_type: str = DEFAULT_WORKFILE_TYPE, software: str = "nuke",
                   comment: str = "", inputs=()):
    """Record a new *major* in Kitsu, once the DCC has saved `target.path`."""
    return media.publish(pctx, entity, media_type, task, files=[target.path], name=name,
                         version=target.major, minor=target.minor, software=software,
                         comment=comment, inputs=inputs, make_review_proxy=False)


def new_workfile(pctx, entity, task, *, media_type: str = DEFAULT_WORKFILE_TYPE,
                 software: str = "", template: str = "", from_current: bool = False,
                 name: str = "main", comment: str = "") -> WorkfileSlot:
    """Start a fresh major version. Resolves its path, optionally seeds the file
    (copied up from the current version's latest minor, or from a template), and
    records the major in Kitsu. With no seed the folder is made and the major is
    registered at the resolved path for the DCC to save into."""
    target = next_save(pctx, entity, task, name=name, media_type=media_type, bump="major")

    seed = ""
    if from_current:
        cur = latest_major(pctx, entity, task, name=name, media_type=media_type)
        seed = (cur.latest.path if (cur and cur.latest) else "") or ""
    seed = seed or template

    Path(target.path).parent.mkdir(parents=True, exist_ok=True)
    seeded = False
    if seed:
        if not Path(seed).is_file():
            raise FileNotFoundError(f"workfile seed not found: {seed}")
        if Path(seed).resolve() != Path(target.path).resolve():
            shutil.copy2(seed, target.path)
        seeded = True

    res = register_major(pctx, entity, task, target, name=name, media_type=media_type,
                         software=software or "nuke", comment=comment)
    logger.info("workfile %s %s -> %s", _task_name(task), target.label(), target.path)
    return WorkfileSlot(path=target.path, major=target.major, minor=target.minor,
                        record=res.record, seeded=seeded, seeded_from=seed)


def save_workfile(pctx, entity, task, src_path, *, media_type: str = DEFAULT_WORKFILE_TYPE,
                  software: str = "", name: str = "main", comment: str = "", inputs=()):
    """Register an existing scene file as a new major (the DCC 'Save Version'
    milestone). `src_path` may already be at the resolved location."""
    target = next_save(pctx, entity, task, name=name, media_type=media_type, bump="major")
    if str(Path(src_path)) != str(Path(target.path)):
        Path(target.path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, target.path)
    return register_major(pctx, entity, task, target, name=name, media_type=media_type,
                          software=software or "nuke", comment=comment, inputs=inputs)


# ---- outputs -----------------------------------------------

def publish_output(pctx, entity, task, *, media_type: str, frames, name: str = "main",
                   version: int | None = None, media_info=None, source_workfile=None,
                   comment: str = "", transfer_mode: str = "copy",
                   make_review_proxy: bool | None = None,
                   proxy_dry_run: bool = False) -> "media.MediaResult":
    """Publish rendered frames as `media_type`. `version` pins the output
    revision (pass the source workfile's major so output version == workfile
    major); an existing revision is replaced in place."""
    src_id = getattr(source_workfile, "id", "") if source_workfile else ""
    return media.publish(pctx, entity, media_type, task, files=frames, name=name,
                         version=version, media_info=media_info, comment=comment,
                         transfer_mode=transfer_mode, make_review_proxy=make_review_proxy,
                         proxy_dry_run=proxy_dry_run, source_workfile_id=src_id)


def current_workfile_major(pctx, task, *, name: str = "main") -> int:
    """The highest workfile major registered on this task/name, or 0."""
    return max((w.revision for w in pctx.kitsu.working_files(task)
                if (w.name or "main") == name), default=0)


# ---- rendering: does the open script actually match, and what produced it ---

def parse_workfile_version(path: str) -> "tuple[int, int] | None":
    """(major, minor) parsed from a workfile's own filename, or None if it
    doesn't look like one of ours at all."""
    m = _VERSION_IN_NAME.search(Path(path).name)
    return (int(m.group(1)), int(m.group(2))) if m else None


def verify_open_script(pctx, entity, task, open_path: str, *, name: str = "main",
                       media_type: str = DEFAULT_WORKFILE_TYPE) -> "tuple[int, int] | None":
    """(major, minor) if `open_path` really is THIS shot/task/name's own
    workfile naming, at some major/minor -- reconstructs the expected path
    for the parsed major/minor and requires an exact match, so a script that
    merely matches the bare `_vNNN.NNN.` pattern (wrong shot, hand-renamed,
    a different task's file) is correctly rejected too, not accepted on a
    filename-shape match alone. None if it doesn't parse, or doesn't match
    once reconstructed."""
    if not open_path:
        return None
    parsed = parse_workfile_version(open_path)
    if parsed is None:
        return None
    major, minor = parsed
    expected = workfile_path(pctx, entity, task, media_type=media_type, major=major,
                             minor=minor, name=name)
    try:
        if Path(expected).resolve() != Path(open_path).resolve():
            return None
    except OSError:
        return None
    return major, minor


def snapshot_rendered_script(pctx, entity, task, *, source_path: str, major: int,
                             name: str = "main", media_type: str = DEFAULT_WORKFILE_TYPE) -> str:
    """Copy `source_path` (the actually-open, already-saved script that
    produced a render) to v{major}.000 -- minor 0 is reserved for exactly
    this, never an artist WIP save -- so there's always an exact, current
    record of what really produced this major's render, independent of
    whichever real minor the artist happens to be iterating in when they hit
    Render. Overwritten every render at this major. Read-only on disk once
    written (made writable only for the instant of the overwrite itself) so
    an accidental Ctrl+S on it can't silently corrupt it."""
    dest = workfile_path(pctx, entity, task, media_type=media_type, major=major,
                         minor=0, name=name)
    dest_p = Path(dest)
    dest_p.parent.mkdir(parents=True, exist_ok=True)
    if dest_p.exists():
        try:
            dest_p.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError as e:
            logger.warning("could not make %s writable before overwrite: %s", dest, e)
    shutil.copy2(source_path, dest)
    try:
        dest_p.chmod(stat.S_IREAD)
    except OSError as e:
        logger.warning("could not mark %s read-only: %s", dest, e)
    return dest


def register_major_at(pctx, entity, task, major: int, path: str, *, name: str = "main",
                      media_type: str = DEFAULT_WORKFILE_TYPE, software: str = "nuke",
                      comment: str = "", inputs=()):
    """Register `path` (normally a just-written v{major}.000 snapshot, see
    snapshot_rendered_script()) as an EXPLICIT major -- not the next
    available one -- for when a render, not a Save Version click, is what
    determined the number: a "(new)" render can land output at v6 while the
    workfile was still at v4, and the output's provenance needs a real v6
    working-file record to point at. `minor=0` in the registration matches
    where `path` actually lives -- media.publish() then sees source and
    destination already the same file and just records the Kitsu metadata,
    it doesn't spawn a second copy. A no-op returning the existing record if
    `major` is already registered for this task/name (defensive; the caller
    should already only reach here when it isn't)."""
    existing = next((w for w in pctx.kitsu.working_files(task)
                     if (w.name or "main") == name and w.revision == major), None)
    if existing is not None:
        return existing
    target = SaveTarget(path=path, major=major, minor=0, is_new_major=True)
    res = register_major(pctx, entity, task, target, name=name, media_type=media_type,
                         software=software, comment=comment, inputs=inputs)
    return res.record


def output_locked(output) -> bool:
    """True if an output version must not be re-rendered (reviewed / delivered)."""
    data = getattr(output, "data", None) or {}
    return bool((data.get("square") or {}).get("locked"))


def lock_output(pctx, output, *, reason: str = "reviewed") -> None:
    pctx.kitsu.merge_output_data(output, {"locked": True, "locked_reason": reason})


def make_preview(pctx, frames, out_path, *, fps=24.0, is_video=False,
                 start_frame=None, dry_run=False) -> str:
    from square_core.media import make_proxy

    return make_proxy(frames, out_path, fps=fps, is_video=is_video,
                      start_frame=start_frame, dry_run=dry_run)
