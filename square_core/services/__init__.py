"""square_core.services -- the tool-facing use-cases.

Each function takes a `PipelineContext` or `ProjectContext` and orchestrates
`kitsu` + `storage` + `paths` to guarantee a whole outcome (a project is
created with its Kitsu record AND its folders AND its config, or not at all).

Grows as tools need it -- most of ingest stays in the ingest tool for now.
"""

from __future__ import annotations

from . import projects, breakdown, work, review

__all__ = ["projects", "breakdown", "work", "review"]
