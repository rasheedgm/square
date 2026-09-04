"""
square_core.model -- the pipeline's value objects.

Light, pure dataclasses. No I/O, no gazu, no Qt, no other square_core import.
`square_core.kitsu` builds these from Kitsu payloads; services and tools pass
them around. They are a convenience for typed field access, not a mandatory
abstraction -- a plain dict is fine where it is clearer.
"""

from __future__ import annotations

from .refs import EntityRef, Version
from .entities import (
    User,
    Project,
    Episode,
    Sequence,
    Shot,
    Asset,
    TaskType,
    TaskStatus,
    Task,
    Workfile,
    Output,
    PreviewMedia,
    Comment,
    Delivery,
)
from .media import MediaInfo
from .provenance import Provenance, KITSU_DATA_KEY
from .paths import PathContext
from .results import ProjectCreated, PublishResult, MediaResult

__all__ = [
    "EntityRef",
    "Version",
    "User",
    "Project",
    "Episode",
    "Sequence",
    "Shot",
    "Asset",
    "TaskType",
    "TaskStatus",
    "Task",
    "Workfile",
    "Output",
    "PreviewMedia",
    "Comment",
    "Delivery",
    "MediaInfo",
    "Provenance",
    "KITSU_DATA_KEY",
    "PathContext",
    "ProjectCreated",
    "PublishResult",
    "MediaResult",
]
