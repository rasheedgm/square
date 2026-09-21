"""Adds the **Square** menu to Nuke and installs the gizmo callbacks. Nuke runs
this automatically when THIS FILE'S OWN DIRECTORY (`tools/dcc/nuke`, not just
the repo root) is on `NUKE_PATH` -- see `docs/nuke_integration.md`.
"""

import nuke

from tools.dcc.nuke import gizmos

_P = "from tools.dcc.nuke import panel; panel.{}()"
_status_item = None


def build() -> None:
    """Builds the Square menu ONCE, at Nuke start (below) -- the menu's own
    title never changes again. An earlier version renamed the top-level
    "Square" menu itself to show who's signed in, removing and re-adding it
    on every Sign In / Sign Out via `Menu.removeItem(name)`. That lookup
    needs the EXACT current label, and since the label was exactly what
    kept changing, removeItem() started missing -- leaving the old menu in
    place and adding a second "Square" alongside it every time. The login
    status now lives on one ordinary menu ITEM instead (see refresh_status()
    below), mutated in place via MenuItem.setLabel() -- no add/remove of
    anything, so there is nothing left to leave behind or duplicate."""
    global _status_item
    from tools.dcc.nuke import panel

    menu = nuke.menu("Nuke").addMenu("Square")
    menu.addCommand("Save Version…", _P.format("save_version"), "ctrl+alt+s")
    menu.addCommand("Open Version…", _P.format("open_version"), "ctrl+alt+o")
    menu.addSeparator()
    menu.addCommand("SquareWrite", _P.format("create_square_write"))
    menu.addCommand("SquareRead", _P.format("create_square_read"))
    menu.addSeparator()
    menu.addCommand("Render && Publish", _P.format("render_and_publish_selected"))
    menu.addCommand("Publish Output…", _P.format("publish_dialog"))
    menu.addSeparator()
    _status_item = menu.addCommand(panel.status_label(), _P.format("show_status"))
    menu.addCommand("Sign In…", _P.format("sign_in"))
    menu.addCommand("Sign Out", _P.format("sign_out"))


def refresh_status() -> None:
    """Update the status item's label in place -- called once by build()
    (above) and again whenever panel.sign_in() / sign_out() changes the
    login state, or the first command that authenticates from an
    already-cached token. Safe to call before build() has run (e.g. from a
    test): just a no-op."""
    if _status_item is None:
        return
    from tools.dcc.nuke import panel
    _status_item.setLabel(panel.status_label())


build()

# keep every SquareRead / SquareWrite's file path in sync with its Square tab
gizmos.register_callbacks(nuke)
