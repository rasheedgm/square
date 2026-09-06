"""Headless core for the project-setup tool.

`ProjectAdmin` is a thin orchestration layer over `square_core.services`: it
holds the `PipelineContext`, gates writes on the Kitsu role, and turns a block
of pasted text into breakdown rows. It keeps no state the pipeline already
owns -- projects, shots and task types are read live from Kitsu on every call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from square_core.services import breakdown, projects
from square_core.services.projects import ProjectSpec

__all__ = [
    "ProjectAdmin", "ProjectSpec", "BreakdownRow", "BreakdownOutcome",
    "parse_breakdown", "NotAuthorized", "ADMIN_ROLES",
]

# Creating a project / shots / tasks is an admin action, same gate as the
# config editor.
ADMIN_ROLES = {"admin", "manager"}

PRODUCTION_TYPES = ("short", "tvshow", "feature", "commercial")

_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_INT_RE = re.compile(r"^\d+$")
_NUM_RE = re.compile(r"^\d+(?:\.\d+)?$")


class NotAuthorized(RuntimeError):
    """The current Kitsu user's role may not create projects / breakdown."""


@dataclass
class BreakdownRow:
    sequence: str
    shot: str
    frame_in: int = 1001
    frame_out: int = 1100
    fps: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.sequence) and bool(self.shot)


@dataclass
class BreakdownOutcome:
    row: BreakdownRow
    created: bool = False          # a shot record came back
    error: str = ""


def parse_breakdown(text: str) -> list[BreakdownRow]:
    """Parse pasted lines into `BreakdownRow`s.

    One shot per line. `/` and `,` count as separators, so all of these work::

        SQ010 SH0100
        SQ010 SH0100 1001-1100
        SQ010 SH0100 1001 1100 24
        SQ010, SH0100, 1001-1100, 23.976
        SQ010 / SH0100 / 1001-1100

    Blank lines and lines starting with `#` are skipped. A line that can't be
    read still produces a row -- with `.error` set -- so the UI can show it.
    """
    rows: list[BreakdownRow] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = [t for t in re.split(r"[\s,/]+", line) if t]
        if len(toks) < 2:
            rows.append(BreakdownRow("", "", error=f"need a sequence and a shot: {line!r}"))
            continue

        seq, shot, rest = toks[0], toks[1], toks[2:]
        row = BreakdownRow(sequence=seq, shot=shot)

        # frame range: "1001-1100", or two bare integers
        if rest:
            m = _RANGE_RE.match(rest[0])
            if m:
                row.frame_in, row.frame_out = int(m.group(1)), int(m.group(2))
                rest = rest[1:]
            elif len(rest) >= 2 and _INT_RE.match(rest[0]) and _INT_RE.match(rest[1]):
                row.frame_in, row.frame_out = int(rest[0]), int(rest[1])
                rest = rest[2:]

        # fps: a leftover number
        if rest and _NUM_RE.match(rest[0]):
            row.fps = float(rest[0])
            rest = rest[1:]

        if rest:
            row.error = f"didn't understand: {' '.join(rest)}"
        elif row.frame_out < row.frame_in:
            row.error = f"frame range ends before it starts ({row.frame_in}-{row.frame_out})"
        rows.append(row)
    return rows


class ProjectAdmin:
    def __init__(self, ctx):
        self.ctx = ctx
        self.user = getattr(ctx, "user", None)

    # ---- identity / gate --------------------------------------------

    @property
    def role(self) -> str:
        return (getattr(self.user, "role", "") or "").lower()

    def can_write(self) -> bool:
        return self.role in ADMIN_ROLES

    def _require(self) -> None:
        if not self.can_write():
            who = getattr(self.user, "email", None) or "this user"
            raise NotAuthorized(
                f"{who} (role {self.role or 'none'!r}) may not create projects; "
                "need an admin or manager role in Kitsu.")

    # ---- choices for the UI ---------------------------------------

    def nas_root_names(self) -> list[str]:
        return list(getattr(self.ctx.config, "nas_roots", {}) or {}) or ["default"]

    def kitsu_templates(self) -> list[str]:
        try:
            return list(self.ctx.kitsu.project_templates() or [])
        except Exception:
            return []

    def projects(self) -> list:
        return sorted(self.ctx.kitsu.projects(), key=lambda p: p.code)

    def shot_task_types(self) -> list[str]:
        try:
            tts = self.ctx.kitsu.task_types(for_entity="Shot")
        except Exception:
            return []
        return [t.name for t in tts if getattr(t, "name", "")]

    def project_root_preview(self, code: str, nas_root: str = "default") -> str:
        code = (code or "").strip()
        if not code:
            return ""
        try:
            return self.ctx.config.project_root(code, nas_root)
        except TypeError:                        # older signature (code only)
            return self.ctx.config.project_root(code)

    # ---- actions -------------------------------------------------

    def create_project(self, spec: ProjectSpec):
        self._require()
        return projects.create(self.ctx, spec)

    def existing_shots(self, project_code: str) -> list:
        pctx = self.ctx.project(project_code)
        return pctx.kitsu.shots(pctx.project)

    def add_breakdown(self, project_code: str, rows, *, progress=None) -> list[BreakdownOutcome]:
        """Ensure a sequence + shot for every valid row. Idempotent -- an
        existing shot is updated in place, not duplicated."""
        self._require()
        pctx = self.ctx.project(project_code)
        good = [r for r in rows if r.ok]
        out: list[BreakdownOutcome] = []
        for i, row in enumerate(good, start=1):
            try:
                shot = breakdown.ensure_shot(
                    pctx, row.sequence, row.shot,
                    frame_in=row.frame_in, frame_out=row.frame_out, fps=row.fps,
                )
                out.append(BreakdownOutcome(row, created=shot is not None))
            except Exception as e:               # keep going, report per row
                out.append(BreakdownOutcome(row, error=str(e)))
            if progress:
                progress(i, len(good), row)
        return out

    def build_task_grid(self, project_code: str, task_types, *, shots=None, progress=None) -> list:
        self._require()
        pctx = self.ctx.project(project_code)
        if shots is None:
            shots = pctx.kitsu.shots(pctx.project)
        return breakdown.build_task_grid(pctx, shots, list(task_types), progress=progress)
