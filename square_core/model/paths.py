"""PathContext -- the bag of values a path template consumes.

The *service* fills this from the entity dicts it already holds; `PathResolver`
never walks the entity chain itself (keeps `square_core.paths` free of any
`square_core.kitsu` dependency). See `docs/config_and_paths.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class PathContext:
    nas_root: str
    project: str

    episode: str = ""
    sequence: str = ""
    shot: str = ""
    asset: str = ""
    asset_type: str = ""

    task: str = ""
    department: str = ""
    software: str = ""

    output_type: str = ""            # `media_type` is an alias in templates
    name: str = "main"

    version: int = 1
    minor: int = 0

    representation: str = ""
    ext: str = ""
    resolution: str = ""
    fps: str = ""

    frame: int | None = None

    client: str = ""
    package: str = ""
    date: str = ""
    user: str = ""
    site: str = ""

    @property
    def media_type(self) -> str:
        return self.output_type

    def with_(self, **over) -> "PathContext":
        """A copy with fields overridden -- e.g. `ctx.with_(frame=1001)`."""
        return replace(self, **over)

    @classmethod
    def field_names(cls) -> tuple:
        return tuple(f.name for f in fields(cls))
