"""Cross-cutting exception types. Package-specific ones (ConfigError, PathError)
live with their package."""

from __future__ import annotations


class PipelineError(RuntimeError):
    """Base for everything the pipeline raises on purpose."""


class KitsuError(PipelineError):
    """A Kitsu operation failed."""


class NeedsLogin(PipelineError):
    """No usable cached session. The tool should prompt for credentials and
    retry `PipelineContext.connect()`."""

    def __init__(self, host: str = ""):
        super().__init__(f"not logged in to {host}" if host else "not logged in")
        self.host = host
