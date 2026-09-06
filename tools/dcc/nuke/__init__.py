"""Square integration for Nuke (14+).

Deploy: put this repo's root on Nuke's `NUKE_PATH` (or add it to `sys.path` in a
studio `init.py`); `menu.py` then adds the **Square** menu on Nuke start.

    tools/dcc/nuke/
        context.py   the current project / sequence / shot / task
        ops.py       pipeline operations (no `import nuke`) -- unit-testable
        nodes.py     SquareRead / SquareWrite node builders
        panel.py     the Square panel (nukescripts.PythonPanel)
        menu.py      the Square menu (Nuke auto-runs this)
"""
