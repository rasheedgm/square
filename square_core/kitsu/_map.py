"""Raw gazu dict -> square_core.model. One direction only -- we build model
objects for callers; writes take plain values, not model objects.

`raw` on every entity keeps the untouched dict so nothing is lost.
"""

from __future__ import annotations

from square_core.model import (
    User, Project, Sequence, Shot, Asset, TaskType, TaskStatus, Task,
    Workfile, Output, PreviewMedia, Comment,
)


def user(d: dict) -> User:
    return User(id=d.get("id", ""), name=d.get("full_name") or d.get("name", ""),
                email=d.get("email", ""), role=d.get("role", ""), raw=d)


def project(d: dict) -> Project:
    res = d.get("resolution") or ""
    return Project(
        id=d.get("id", ""), code=d.get("code") or d.get("name", ""),
        name=d.get("name", ""), status=_status_name(d.get("project_status")),
        production_type=d.get("production_type", "short"),
        fps=float(d.get("fps") or 0) if d.get("fps") else 0.0,
        resolution=res, ratio=d.get("ratio", ""),
        raw=d,
    )


def sequence(d: dict) -> Sequence:
    return Sequence(id=d.get("id", ""), code=d.get("name", ""),
                    project_id=d.get("project_id", ""),
                    episode_id=d.get("parent_id") or d.get("episode_id") or "",
                    episode_code=d.get("episode_name", ""), raw=d)


def shot(d: dict) -> Shot:
    data = d.get("data") or {}
    return Shot(
        id=d.get("id", ""), code=d.get("name", ""),
        sequence_id=d.get("parent_id") or d.get("sequence_id") or "",
        sequence_code=d.get("sequence_name", ""),
        episode_code=d.get("episode_name", ""),
        frame_in=int(data.get("frame_in") or 0),
        frame_out=int(data.get("frame_out") or 0),
        nb_frames=int(d.get("nb_frames") or 0),
        status=d.get("status", ""),
        data=data, raw=d,
    )


def asset(d: dict) -> Asset:
    return Asset(id=d.get("id", ""), code=d.get("name", ""), name=d.get("name", ""),
                 asset_type=d.get("asset_type_name") or d.get("entity_type_name", ""),
                 project_id=d.get("project_id", ""), data=d.get("data") or {}, raw=d)


def task_type(d: dict) -> TaskType:
    return TaskType(id=d.get("id", ""), name=d.get("name", ""),
                    short_name=d.get("short_name", ""),
                    department=(d.get("department_name") or ""),
                    for_entity=d.get("for_entity", ""), raw=d)


def task_status(d: dict) -> TaskStatus:
    return TaskStatus(id=d.get("id", ""), name=d.get("name", ""),
                      short_name=d.get("short_name", ""),
                      is_done=bool(d.get("is_done")), is_retake=bool(d.get("is_retake")),
                      is_wip=bool(d.get("is_wip")), color=d.get("color", ""), raw=d)


def task(d: dict) -> Task:
    return Task(
        id=d.get("id", ""),
        entity_id=d.get("entity_id", ""),
        entity_type=(d.get("entity_type_name") or "").lower(),
        task_type_id=d.get("task_type_id", ""),
        task_type_name=d.get("task_type_name") or _nested_name(d.get("task_type")),
        status=d.get("task_status_name") or _nested_name(d.get("task_status")),
        status_id=d.get("task_status_id", ""),
        assignee_ids=list(d.get("assignees") or []),
        priority=int(d.get("priority") or 0),
        raw=d,
    )


def workfile(d: dict) -> Workfile:
    return Workfile(
        id=d.get("id", ""), entity_id=d.get("entity_id", ""), task_id=d.get("task_id", ""),
        name=d.get("name", "main"), software=_nested_name(d.get("software")),
        revision=int(d.get("revision") or 1), path=d.get("path", ""),
        comment=d.get("comment", ""), created_at=d.get("created_at", ""),
        data=d.get("data") or {}, raw=d,
    )


def output(d: dict) -> Output:
    return Output(
        id=d.get("id", ""), entity_id=d.get("entity_id", ""), task_id=d.get("task_id", ""),
        output_type=d.get("output_type_name") or _nested_name(d.get("output_type")),
        name=d.get("name", "main"), revision=int(d.get("revision") or 1),
        representation=d.get("representation", ""), path=d.get("path", ""),
        source_workfile_id=d.get("source_file_id", ""),
        created_at=d.get("created_at", ""), data=d.get("data") or {}, raw=d,
    )


def preview(d: dict) -> PreviewMedia:
    return PreviewMedia(
        id=d.get("id", ""), task_id=d.get("task_id", ""),
        revision=int(d.get("revision") or 1), path=d.get("path", ""),
        kind=d.get("extension") or "", status=d.get("status", ""),
        data=d.get("data") or {}, raw=d,
    )


def comment(d: dict) -> Comment:
    return Comment(
        id=d.get("id", ""), task_id=d.get("object_id") or d.get("task_id", ""),
        text=d.get("text", ""), author=_nested_name(d.get("person")),
        status_change=_nested_name(d.get("task_status")),
        created_at=d.get("created_at", ""),
        attachments=list(d.get("attachment_files") or []), raw=d,
    )


# --------------------------------------------------------------------------

def _nested_name(v) -> str:
    if isinstance(v, dict):
        return v.get("name") or v.get("full_name") or ""
    return v or "" if isinstance(v, str) else ""


def _status_name(v) -> str:
    return _nested_name(v)
