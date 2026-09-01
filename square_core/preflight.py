"""
preflight.py -- the pure checks that turn raw facts about an item (its
fields, its ledger match, its NAS slot state, and the other rows in the
batch) into the ``Issue`` list its status is derived from.

Nothing here does I/O. The controller gathers the facts (metadata probe,
hashing, ledger lookup, NAS slot inspection) and calls these to assemble
``item.issues``. Keeping it pure keeps the whole conflict model unit
testable without a NAS or a Kitsu server.
"""

from __future__ import annotations

import re

from square_core.ingest_item import (
    IngestItem, Issue, IssueKind, Severity,
    REQUIRED_FIELDS, REQUIRED_METADATA,
)

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SEPARATORS = re.compile(r"[\s_\-]+")


def _norm(name: str) -> str:
    """Strip separators + case so 'SH0100', 'SH_0100', 'sh-01 00' compare equal."""
    return _SEPARATORS.sub("", (name or "")).strip().upper()


# ---------------------------------------------------------------------------
# Per-item checks
# ---------------------------------------------------------------------------

def field_issues(item: IngestItem, known_media_types=None) -> list[Issue]:
    out: list[Issue] = []

    for f in REQUIRED_FIELDS:
        if not (getattr(item, f) or "").strip():
            out.append(Issue(
                IssueKind.NEEDS_INFO,
                f"{f.replace('_', ' ').title()} is required before this row can ingest.",
                severity=Severity.BLOCK, column=f,
            ))

    for f in REQUIRED_METADATA:
        verified = item.metadata_verified.get(f, False)
        has_value = bool(str(getattr(item, f) or "").strip())
        if not verified and not has_value:
            label = {"fps": "Frame rate", "resolution": "Resolution",
                     "colorspace": "Colorspace"}[f]
            out.append(Issue(
                IssueKind.NEEDS_INFO,
                f"{label} could not be read from the media and hasn't been set. "
                f"Set it before ingesting -- a guessed value would be shipped to Kitsu.",
                severity=Severity.BLOCK, column=f,
            ))

    for f in ("sequence_code", "shot_code", "media_type", "media_name"):
        val = getattr(item, f) or ""
        if val and _ILLEGAL.search(val):
            out.append(Issue(
                IssueKind.ILLEGAL_CHARS,
                f"{f.replace('_', ' ').title()} contains characters that aren't allowed "
                f"in a folder name.",
                severity=Severity.BLOCK, column=f,
            ))

    if known_media_types is not None and item.media_type:
        allowed = {(t or "").strip().lower() for t in known_media_types}
        if item.media_type.strip().lower() not in allowed:
            out.append(Issue(
                IssueKind.NO_DEST_TEMPLATE,
                f"Media type '{item.media_type}' has no destination template configured -- "
                f"a generic versioned path will be used. Add one in Settings, or ignore.",
                severity=Severity.WARN, column="media_type",
            ))

    return out


SLOT_EMPTY = "empty"
SLOT_ALREADY = "already"
SLOT_CONFLICT = "conflict"


def ledger_issue(item: IngestItem, slot_state: str = "") -> Issue | None:
    """
    - "partial": some of these files were ingested before -> warn.
    - "full" but NOT already in this exact target: identical content is
      already on the NAS as another version / another shot. That's a valid
      thing to do on purpose (same plate to two shots, re-version), so it's
      a warning you can Ignore -- not a block.
    - "full" AND already in this exact target: handled by ALREADY_IN_SLOT /
      the Already Ingested status, not here.
    """
    if item.ledger_kind == "partial":
        return Issue(
            IssueKind.PARTIAL_OVERLAP,
            item.ledger_detail or "Some of these files have been ingested before.",
            severity=Severity.WARN,
        )
    if item.ledger_kind == "full" and slot_state != SLOT_ALREADY:
        return Issue(
            IssueKind.DUPLICATE_CONTENT,
            item.ledger_detail or (
                "Identical content has already been ingested (elsewhere, or as another version). "
                "Ingest anyway, version up, or skip."
            ),
            severity=Severity.WARN,
        )
    return None


def slot_issue(item: IngestItem, slot_state: str, slot_detail: str = "") -> Issue | None:
    """
      empty    -> no issue (clean target)
      already  -> ALREADY_IN_SLOT: this exact target already holds this exact
                  content. Status becomes "Already Ingested"; the issue is
                  here so there's still a way forward (version up / overwrite).
      conflict -> the target version folder holds DIFFERENT content -> block.
    """
    if slot_state == SLOT_CONFLICT:
        return Issue(
            IssueKind.DEST_EXISTS_DIFF,
            slot_detail or (
                f"v{item.version:03d} already exists on the NAS with different content. "
                f"Version up, or overwrite it."
            ),
            severity=Severity.BLOCK, column="version",
        )
    if slot_state == SLOT_ALREADY:
        return Issue(
            IssueKind.ALREADY_IN_SLOT,
            slot_detail or (
                f"v{item.version:03d} already holds exactly this content. Nothing to do -- "
                f"or version up / overwrite if you meant to re-deliver."
            ),
            severity=Severity.WARN, column="version",
        )
    return None


# ---------------------------------------------------------------------------
# Cross-item checks (need the whole batch)
# ---------------------------------------------------------------------------

def cross_item_issues(items: list[IngestItem]) -> dict[str, list[Issue]]:
    """
    {item.key: [Issue]} for issues that only exist relative to other rows:
      - dest-collision: two live rows resolve to the same destination folder
      - near-dup-batch: shot/media name near-identical (not equal) to another row
      - case-inconsistent: same code in different case across rows
    Skipped rows and rows with no dest_dir are ignored.
    """
    out: dict[str, list[Issue]] = {it.key: [] for it in items}
    live = [it for it in items if not it.skipped]

    # dest-collision
    by_dest: dict[str, list[IngestItem]] = {}
    for it in live:
        if it.dest_dir:
            by_dest.setdefault(it.dest_dir.lower().rstrip("/\\"), []).append(it)
    for dest, group in by_dest.items():
        if len(group) > 1:
            names = ", ".join(sorted(g.source_name or g.key for g in group))
            for g in group:
                out[g.key].append(Issue(
                    IssueKind.DEST_COLLISION,
                    f"This row and {len(group) - 1} other(s) ({names}) resolve to the same "
                    f"destination folder. Skip all but one, version them up, or rename.",
                    severity=Severity.BLOCK, column="dest_dir",
                    data={"others": [g2.key for g2 in group if g2.key != g.key]},
                ))

    # near-dup + case inconsistency, for shot and media name
    for label, attr in (("shot", "shot_code"), ("sequence", "sequence_code"),
                        ("media name", "media_name")):
        exact: dict[str, set[str]] = {}       # value -> {rows using it verbatim}
        norm_map: dict[str, set[str]] = {}    # normalized -> {actual values}
        for it in live:
            v = (getattr(it, attr) or "").strip()
            if not v:
                continue
            exact.setdefault(v, set()).add(it.key)
            norm_map.setdefault(_norm(v), set()).add(v)

        for it in live:
            v = (getattr(it, attr) or "").strip()
            if not v:
                continue
            siblings = norm_map.get(_norm(v), set())
            if len(siblings) <= 1:
                continue
            others = sorted(siblings - {v})
            # same spelling but different case only -> case-inconsistent
            if all(o.lower() == v.lower() for o in others):
                out[it.key].append(Issue(
                    IssueKind.CASE_INCONSISTENT,
                    f"{label.title()} '{v}' is also written as {', '.join(others)} on other "
                    f"rows -- same name, different case. Pick one spelling.",
                    severity=Severity.WARN, column=attr,
                ))
            else:
                out[it.key].append(Issue(
                    IssueKind.NEAR_DUP_BATCH,
                    f"{label.title()} '{v}' is nearly identical to {', '.join(others)} on other "
                    f"rows (spacing/underscore/case). Likely the same {label} -- fix, or ignore.",
                    severity=Severity.WARN, column=attr,
                ))

    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_issues(
    item: IngestItem,
    slot_state: str,
    slot_detail: str = "",
    cross: list[Issue] | None = None,
    known_media_types=None,
) -> list[Issue]:
    """Everything for one item: field checks + ledger + NAS slot + its cross-item issues."""
    issues = field_issues(item, known_media_types=known_media_types)
    li = ledger_issue(item, slot_state)
    if li:
        issues.append(li)
    si = slot_issue(item, slot_state, slot_detail)
    if si:
        issues.append(si)
    if cross:
        issues.extend(cross)
    return issues
