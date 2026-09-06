"""Adds the **Square** menu to Nuke. Nuke runs this automatically when this
repo's root is on `NUKE_PATH` (or added to `sys.path` by a studio `init.py`).
"""

import nuke

_M = "from tools.dcc.nuke import panel; panel.{}()"

_menu = nuke.menu("Nuke").addMenu("Square")
_menu.addCommand("Workfile Panel", _M.format("show"), "shift+alt+s")
_menu.addSeparator()
_menu.addCommand("Save Version", _M.format("save_version"), "ctrl+alt+s")
_menu.addCommand("Open Version…", _M.format("open_version"))
_menu.addSeparator()
_menu.addCommand("Add Write for Output", _M.format("add_write_for_output"))
_menu.addCommand("Publish Render", _M.format("publish_render"))
_menu.addCommand("Load Plate", _M.format("load_plate"))
