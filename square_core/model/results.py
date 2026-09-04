"""Operation result objects -- what a service call created / changed, for the
tool to display and the session to record. More get added as services land.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .entities import Project, Output, PreviewMedia


@dataclass
class ProjectCreated:
    project: Project
    config_path: str = ""
    folders_created: list = field(default_factory=list)
    kitsu_template: str = ""


@dataclass
class MediaResult:
    """The result of `services.media.publish` -- one versioned media (of a
    configured media type) landed on disk and recorded in Kitsu."""
    media_type: str
    name: str
    version: int
    kitsu_kind: str = "output"                        # output | working
    record: Output | None = None                     # the Kitsu file record
    dir: str = ""
    files: list = field(default_factory=list)         # dest file paths
    preview: PreviewMedia | None = None
    checksums: dict = field(default_factory=dict)     # dest file -> hash
    copied: bool = False                              # False = files were already in place


# kept as an alias so existing `work.publish_output` callers don't break
PublishResult = MediaResult
