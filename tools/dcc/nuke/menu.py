"""Adds the **Square** menu to Nuke and installs the gizmo callbacks. Nuke runs
this automatically when THIS FILE'S OWN DIRECTORY (`tools/dcc/nuke`, not just
the repo root) is on `NUKE_PATH` -- see `docs/nuke_integration.md`.
"""

import nuke

from tools.dcc.nuke import gizmos

_P = "from tools.dcc.nuke import panel; panel.{}()"
_current_title = "Square"


def build() -> None:
    """(Re)builds the Square menu: once at Nuke start (below), and again from
    `panel.sign_in()` / `sign_out()` so the top-level label's login status
    stays correct -- Nuke's classic menu API has no live-updating label, so
    this is how "show the signed-in user" actually gets reflected. Only the
    menu ITEMS are rebuilt here; the gizmo knobChanged callbacks are
    registered exactly once, at the bottom of this module, never in here --
    a rebuild calling that again would double-fire every one of them (the
    same class of bug a doubled-plugin-load once caused for xStudio)."""
    global _current_title
    from tools.dcc.nuke import panel

    m = nuke.menu("Nuke")
    try:
        m.removeItem(_current_title)
    except Exception:
        pass          # nothing to remove yet (first build)
    _current_title = panel.menu_title()
    menu = m.addMenu(_current_title)
    menu.addCommand("Save Version…", _P.format("save_version"), "ctrl+alt+s")
    menu.addCommand("Open Version…", _P.format("open_version"), "ctrl+alt+o")
    menu.addSeparator()
    menu.addCommand("SquareWrite", _P.format("create_square_write"))
    menu.addCommand("SquareRead", _P.format("create_square_read"))
    menu.addSeparator()
    menu.addCommand("Render && Publish", _P.format("render_and_publish_selected"))
    menu.addCommand("Publish Output…", _P.format("publish_dialog"))
    menu.addSeparator()
    menu.addCommand("Sign In…", _P.format("sign_in"))
    menu.addCommand("Sign Out", _P.format("sign_out"))


build()

# keep every SquareRead / SquareWrite's file path in sync with its Square tab
gizmos.register_callbacks(nuke)
