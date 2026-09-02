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
class PublishResult:
    output: Output
    path: str = ""
    preview: PreviewMedia | None = None
    checksums: dict = field(default_factory=dict)     # dest file -> hash
