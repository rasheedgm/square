"""Adds the **Square** menu to Nuke and installs the gizmo callbacks. Nuke runs
this automatically when THIS FILE'S OWN DIRECTORY (`tools/dcc/nuke`, not just
the repo root) is on `NUKE_PATH` -- see `docs/nuke_integration.md`.
"""

import nuke

from tools.dcc.nuke import gizmos

_P = "from tools.dcc.nuke import panel; panel.{}()"


def _remove_existing(top) -> None:
    """Find and remove whatever Square menu is ACTUALLY there right now --
    not a name this module separately tracked in a Python variable. An
    earlier version trusted its own bookkeeping (`_current_title`) to stay
    exactly in sync with Menu.removeItem()'s required exact-match name; it
    didn't, removeItem() started missing on the very next rename, and a
    second "Square..." menu got added alongside the first instead of
    replacing it. Asking Nuke itself what's on the menu bar sidesteps
    whatever the exact cause of that mismatch was."""
    for item in top.items():
        try:
            name = item.name()
        except Exception:
            continue
        if name == "Square" or name.startswith("Square "):
            top.removeItem(name)


def build() -> None:
    """Rebuilds the WHOLE top-level Square menu: once at Nuke start (below),
    and again from panel.sign_in() / sign_out() (or the first command that
    authenticates from an already-cached token) so its title reflects who's
    signed in -- Nuke's classic menu API has no live-updating label.
    Gizmo knobChanged callbacks are registered exactly once, at the bottom
    of this module, never in here -- a rebuild calling that again would
    double-fire every one of them (the same class of bug a doubled plugin
    load once caused for xStudio)."""
    from tools.dcc.nuke import panel

    top = nuke.menu("Nuke")
    _remove_existing(top)

    menu = top.addMenu(panel.menu_title())
    menu.addCommand("Save Version…", _P.format("save_version"), "ctrl+alt+s")
    menu.addCommand("Open Version…", _P.format("open_version"), "ctrl+alt+o")
    menu.addSeparator()
    menu.addCommand("SquareWrite", _P.format("create_square_write"))
    menu.addCommand("SquareRead", _P.format("create_square_read"))
    menu.addSeparator()
    menu.addCommand("Render && Publish", _P.format("render_and_publish_selected"))
    menu.addCommand("Publish Output…", _P.format("publish_dialog"))
    menu.addSeparator()
    if panel.is_signed_in():
        menu.addCommand("Sign Out", _P.format("sign_out"))
    else:
        menu.addCommand("Sign In…", _P.format("sign_in"))


build()

# keep every SquareRead / SquareWrite's file path in sync with its Square tab
gizmos.register_callbacks(nuke)
