"""EntityRef and Version -- the two small value objects passed around instead
of whole entities or bare ints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityRef:
    """A lightweight handle to a production entity.

    `type` is the pipeline's own noun ("project", "episode", "sequence",
    "shot", "asset", "task"). `id` is opaque -- today a Kitsu UUID string.
    `code` is the human-facing code when known (shot "SH0100"), purely for
    logging / display; never rely on it for identity.
    """

    type: str
    id: str
    code: str = ""

    def __str__(self) -> str:
        return f"{self.type}:{self.code or self.id}"

    def as_dict(self) -> dict:
        return {"type": self.type, "id": self.id, "code": self.code}


@dataclass(frozen=True, order=True)
class Version:
    """A pipeline version: a major number, optionally a minor.

    Ordering is (major, minor) so `sorted(versions)` and `max(versions)` work.
    `label()` renders the studio form -- `v003`, or `v003.02` when minor > 0.
    """

    number: int
    minor: int = 0

    def label(self, pad: int = 3, minor_pad: int = 2) -> str:
        base = f"v{self.number:0{pad}d}"
        if self.minor:
            base += f".{self.minor:0{minor_pad}d}"
        return base

    def bump_major(self) -> "Version":
        return Version(self.number + 1, 0)

    def bump_minor(self) -> "Version":
        return Version(self.number, self.minor + 1)

    def __str__(self) -> str:
        return self.label()
