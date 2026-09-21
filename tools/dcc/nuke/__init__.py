"""Square integration for Nuke (14+).

Deploy: `dcc_launch.py` (via the generated `square_nuke.bat`) puts THIS
package's own directory -- not just the repo root -- on `NUKE_PATH`. Nuke
only auto-runs an `init.py`/`menu.py` sitting directly inside a `NUKE_PATH`
entry, not recursively, so the repo root alone is not enough to find
`menu.py` here. See `docs/nuke_integration.md` for the exact wiring.

    tools/dcc/nuke/
        context.py   the current project / sequence / shot / task
        ops.py       pipeline operations (no `import nuke`) -- unit-testable
        nodes.py     SquareRead / SquareWrite node builders
        panel.py     the Square panel (nukescripts.PythonPanel)
        menu.py      the Square menu (Nuke auto-runs this)
"""
