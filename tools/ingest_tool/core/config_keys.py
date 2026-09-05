"""The ingest tool's own `tools.ingest.*` config keys.

Registered with `square_core.config.schema` at import so the admin config
editor surfaces them (studio + project scope), and read back through
`read(pctx, key)` at ingest time. Imported for its side effect from
`tools/ingest_tool/core/__init__.py`; the config editor imports it too so
the keys exist even when the ingest tool itself isn't running.
"""

from __future__ import annotations

from square_core.config import schema

DEFAULT_TASK_TYPES = ["Ingest", "Prep", "Roto", "Matchmove", "Comp"]

schema.register(
    "tools.ingest.task_types", "list", item_kind="str", scope="both",
    default=list(DEFAULT_TASK_TYPES),
    description="Task types created on every shot an ingest touches "
                "(the ingest record lands on the first that exists).",
)
schema.register(
    "tools.ingest.task_status", "str", scope="both", default="Done",
    description="Status set on the ingest task after a successful ingest. "
                "Empty string leaves the status unchanged.",
)
schema.register(
    "tools.ingest.transfer_mode", "enum", scope="both", default="copy",
    choices=("copy", "hardlink", "symlink"),
    description="How delivered files reach the NAS. hardlink / symlink need "
                "the delivery and the NAS on the same filesystem.",
)


def read(pctx, key: str):
    """The effective value of `tools.ingest.<key>` for this project --
    project config, then PipelineConfig.project_defaults, then the schema
    default."""
    pdefaults = None
    pipe = getattr(pctx, "pipeline", None)
    if pipe is not None and getattr(pipe, "config", None) is not None:
        pdefaults = getattr(pipe.config, "project_defaults", None)
    return schema.resolve(getattr(pctx.config, "data", None),
                          f"tools.ingest.{key}", pipeline_defaults=pdefaults)
