"""Config keys the workfile manager registers on import (`tools.workfile_manager.*`).

Same pattern as `tools/ingest_tool/core/config_keys.py`: a tool declares its own
`tools.<tool>.*` keys so the config editor surfaces them; nothing here writes
config.
"""

from __future__ import annotations

from square_core.config import schema

# software name -> command template. "{file}" is substituted with the workfile
# path; if absent the path is appended. e.g.
#   {"nuke": "C:/Program Files/Nuke15.0v1/Nuke15.0.exe -q -- {file}",
#    "maya": "C:/Program Files/Autodesk/Maya2024/bin/maya.exe -file {file}"}
schema.register(
    "tools.workfile_manager.launchers", "dict", scope="both", default={},
    description="Per-DCC launch command; {file} is the workfile path "
                "(appended if the token is absent).")

# optional per-media-type starter scene copied in when a workfile is created
# with no current version to copy up from.
schema.register(
    "tools.workfile_manager.templates", "dict", scope="both", default={},
    description="Per-media-type template scene path used to seed a first "
                "workfile version (e.g. {\"NukeScript\": \"X:/pipeline/templates/comp.nk\"}).")


def read(pctx, key: str):
    pdefaults = None
    pipe = getattr(pctx, "pipeline", None)
    if pipe is not None and getattr(pipe, "config", None) is not None:
        pdefaults = getattr(pipe.config, "project_defaults", None)
    return schema.resolve(getattr(pctx.config, "data", None),
                          f"tools.workfile_manager.{key}", pipeline_defaults=pdefaults)
