"""Production entities as light dataclasses.

Every entity carries `raw` -- the untouched payload it was built from (a Kitsu
dict today) -- so a caller that needs a field we do not model can still reach
it, and `square_core.kitsu` can round-trip without losing anything. `raw` is
excluded from equality and repr so two entities compare on their modelled
fields.

None of these have behaviour beyond a couple of derived-field helpers.
Construction from a Kitsu payload lives in `square_core.kitsu`, not here --
`model` imports nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .refs import EntityRef

_raw = field(default_factory=dict, repr=False, compare=False)
_data = field(default_factory=dict)
_list: Any = field(default_factory=list)


@dataclass
class User:
    id: str
    name: str = ""
    email: str = ""
    role: str = ""
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("person", self.id, self.name)


@dataclass
class Project:
    id: str
    code: str
    name: str = ""
    status: str = ""
    production_type: str = "short"      # short | tvshow | feature | commercial
    fps: float = 0.0
    resolution: str = ""
    ratio: str = ""
    root_path: str = ""                 # its NAS home -- {nas_root}/{code}
    raw: dict = _raw

    @property
    def is_episodic(self) -> bool:
        return self.production_type == "tvshow"

    def ref(self) -> EntityRef:
        return EntityRef("project", self.id, self.code)


@dataclass
class Episode:
    id: str
    code: str
    project_id: str = ""
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("episode", self.id, self.code)


@dataclass
class Sequence:
    id: str
    code: str
    project_id: str = ""
    episode_id: str = ""               # set only on episodic shows
    episode_code: str = ""
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("sequence", self.id, self.code)


@dataclass
class Shot:
    id: str
    code: str
    sequence_id: str = ""
    sequence_code: str = ""
    episode_code: str = ""
    frame_in: int = 0
    frame_out: int = 0
    nb_frames: int = 0
    status: str = ""
    data: dict = _data                 # the namespaced pipeline blob lives under data["square"]
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("shot", self.id, self.code)


@dataclass
class Asset:
    id: str
    code: str
    name: str = ""
    asset_type: str = ""
    project_id: str = ""
    status: str = ""
    data: dict = _data
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("asset", self.id, self.code)


@dataclass
class TaskType:
    id: str
    name: str
    short_name: str = ""
    department: str = ""
    for_entity: str = ""               # "Shot" | "Asset" | "Sequence" | ...
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("task-type", self.id, self.name)


@dataclass
class TaskStatus:
    id: str
    name: str
    short_name: str = ""
    is_done: bool = False
    is_retake: bool = False
    is_wip: bool = False
    color: str = ""
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("task-status", self.id, self.name)


@dataclass
class Task:
    id: str
    entity_id: str = ""
    entity_type: str = ""              # "shot" | "asset" | ...
    task_type_id: str = ""
    task_type_name: str = ""
    status: str = ""                   # status name/short_name for display
    status_id: str = ""
    assignee_ids: list = _list
    priority: int = 0
    raw: dict = _raw

    def ref(self) -> EntityRef:
        return EntityRef("task", self.id, self.task_type_name)

    def entity_ref(self) -> EntityRef:
        return EntityRef(self.entity_type or "shot", self.entity_id)


@dataclass
class Workfile:
    id: str = ""
    entity_id: str = ""
    task_id: str = ""
    name: str = "main"
    software: str = ""
    revision: int = 1
    path: str = ""
    author: str = ""
    comment: str = ""
    created_at: str = ""
    data: dict = _data
    raw: dict = _raw


@dataclass
class Output:
    id: str = ""
    entity_id: str = ""
    task_id: str = ""
    output_type: str = ""
    name: str = "main"
    revision: int = 1
    representation: str = ""           # exr | mov | jpg ...
    path: str = ""
    source_workfile_id: str = ""
    created_at: str = ""
    data: dict = _data
    raw: dict = _raw


@dataclass
class PreviewMedia:
    id: str = ""
    task_id: str = ""
    revision: int = 1
    path: str = ""
    kind: str = ""                     # "video" | "image"
    status: str = ""
    data: dict = _data
    raw: dict = _raw


@dataclass
class Comment:
    id: str = ""
    task_id: str = ""
    text: str = ""
    author: str = ""
    status_change: str = ""           # the task status this comment set, if any
    created_at: str = ""
    attachments: list = _list
    raw: dict = _raw


@dataclass
class Delivery:
    id: str = ""
    project_id: str = ""
    client: str = ""
    version: int = 1
    date: str = ""
    package_path: str = ""
    items: list = _list
    status: str = ""
    raw: dict = _raw
