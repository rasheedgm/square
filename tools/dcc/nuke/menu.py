"""Adds the **Square** menu to Nuke and installs the gizmo callbacks. Nuke runs
this automatically when THIS FILE'S OWN DIRECTORY (`tools/dcc/nuke`, not just
the repo root) is on `NUKE_PATH` -- see `docs/nuke_integration.md`.
"""

import nuke

from tools.dcc.nuke import gizmos

_P = "from tools.dcc.nuke import panel; panel.{}()"

_menu = nuke.menu("Nuke").addMenu("Square")
_menu.addCommand("Save Version…", _P.format("save_version"), "ctrl+alt+s")
_menu.addCommand("Open Version…", _P.format("open_version"), "ctrl+alt+o")
_menu.addSeparator()
_menu.addCommand("SquareWrite", _P.format("create_square_write"))
_menu.addCommand("SquareRead", _P.format("create_square_read"))
_menu.addSeparator()
_menu.addCommand("Render && Publish", _P.format("render_and_publish_selected"))
_menu.addCommand("Publish Output…", _P.format("publish_dialog"))

# keep every SquareRead / SquareWrite's file path in sync with its Square tab
gizmos.register_callbacks(nuke)
