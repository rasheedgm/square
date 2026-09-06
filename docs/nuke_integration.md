# Nuke integration (`tools/dcc/nuke/`)

Tool #3. Runs inside Nuke's own Python (14+, i.e. Python 3.9+). It imports
`square_core` directly and authenticates through the shared
`~/.square/session.json`, like every other Square tool.

## Install

Put the repo root on Nuke's path so `menu.py` runs on start. Either:

* add the repo root to `NUKE_PATH`, **or**
* in a studio `~/.nuke/init.py`:

  ```python
  import sys
  sys.path.insert(0, r"<repo-root-or-deployed-square>")
  ```

Nuke then runs `tools/dcc/nuke/menu.py` and the **Square** menu appears.

A user must have signed in once through any Square tool (config editor, ingest,
project setup, workfile manager) so the JWT is cached; the panel does not prompt
for a password itself.

## The Square menu

| Command | What it does |
|---|---|
| **Workfile Panel** (`shift+alt+s`) | dockable panel: pick project / sequence-shot / task, then the buttons below |
| **Save Version** (`ctrl+alt+s`) | `scriptSaveAs` to the next `NukeScript` version's resolved path, then register it (`work.save_workfile`) |
| **Open Version…** | pick a saved version and open it |
| **Add Write for Output** | drop a Write node pointed at the next `CompRender` version's render location (`name.####.exr`), colour-managed from the media type |
| **Publish Render** | take the selected Write node's rendered frames over the script's frame range and `work.publish_output` them as a `CompRender` version (+ its review proxy) |
| **Load Plate** | `SquareRead`: a Read node on the shot's latest published `Plate`, with its colorspace and the shot frame range |

## Context

A Nuke launched by another Square tool inherits its coordinates from
`SQUARE_PROJECT` / `SQUARE_SEQUENCE` / `SQUARE_SHOT` / `SQUARE_TASK`. The panel
reads those on open and writes them back when you save or open a version, so a
render subprocess or farm submit sees the same target.

## Layout

```
tools/dcc/nuke/
  context.py   Target + SQUARE_* env round-trip
  ops.py       NukeOps -- every pipeline call, no `import nuke` (unit-tested)
  nodes.py     square_read / square_write builders
  panel.py     the panel + the menu-command functions (lazy `import nuke`)
  menu.py      builds the Square menu (Nuke auto-runs this)
```

Maya / Houdini integrations reuse `ops.py` and `context.py` unchanged; only
`nodes.py` / `panel.py` / `menu.py` are DCC-specific.
