"""square_core.paths -- path resolution.

- `resolver.PathResolver` -- OUTGOING paths: `ProjectConfig` + `PathContext`
  -> a path string. Pure (no Kitsu, no filesystem). See `docs/config_and_paths.md`.
- `path_pattern` -- INCOMING delivery folders: build-by-example matching of a
  vendor's folder shape. Unrelated to the resolver.
"""

from __future__ import annotations

from .resolver import PathResolver, PathError, slugify, render_tokens
from . import path_pattern, token_parser

__all__ = [
    "PathResolver",
    "PathError",
    "slugify",
    "render_tokens",
    "path_pattern",
    "token_parser",
]
