"""The ingest tool's own domain code: item state, preflight/conflict rules,
the controller, session save/resume, the incoming-folder mapper, and the
tool-local dedupe ledger. No Qt here -- see `tools/ingest_tool/widgets/` and
`tools/ingest_tool/ui_main.py` for the UI layer.
"""

from . import config_keys  # noqa: F401 -- registers tools.ingest.* schema keys
