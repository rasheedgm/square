# Nuke integration (`tools/dcc/nuke/`)

Tool #3. Runs inside Nuke's own Python (14+, i.e. Python 3.9+). It imports
`square_core` directly and authenticates through the shared
`~/.square/kitsu_session.json` (or the OS keyring, when available), like
every other Square tool.

## Install

**Deployed studio:** `tools.pipeline_deploy.deploy` writes
`launchers/square_nuke.bat` → `dcc_launch.py`, which reads the Nuke exe from
`config/studio_config.json` (`dcc.nuke_exe`, or `SQUARE_NUKE_EXE`), prepends
`current/tools/dcc/nuke` (so Nuke runs its `menu.py`) and `current` (imports)
to `NUKE_PATH`, points `SQUARE_DEPS` at the shared `envs/dcc-deps` (pure-Python
`gazu` + `Qt.py`), and launches Nuke.

**From a checkout:** put `tools/dcc/nuke` **and** the repo root on `NUKE_PATH`
(Nuke runs the first `menu.py` on a path entry; the imports need the repo root
on `sys.path`).

The **Square** menu appears and the SquareRead / SquareWrite callbacks are
installed. A session cached by any other Square tool is picked up
automatically (no prompt); otherwise use **Sign In…** right from this menu --
it shows the same login dialog every desktop tool uses (via `Qt.py`, which
just needs a Qt binding already importable in the host -- Nuke ships its own
PySide2/PySide6).

## The Square menu

A status item (`Not signed in` / `Signed in as Jane Doe`) shows who's signed
in -- click it to see the same text again as a message. It refreshes right
after **Sign In…** / **Sign Out**, or the first time any command
successfully authenticates with an already-cached token (never at Nuke
startup itself, which must not block on a Kitsu round trip). This is a
`MenuItem.setLabel()` on the SAME item every time, never a rebuild of the
menu itself: Nuke's `Menu.removeItem(name)` needs the exact current name to
find what to remove, so an earlier version that renamed the top-level
**Square** menu on every login change made that lookup miss and left a
second "Square" menu behind instead of replacing the first.

| Command | |
|---|---|
| **Save Version…** (`Ctrl+Alt+S`) | the Save Version panel |
| **Open Version…** (`Ctrl+Alt+O`) | the Open Version panel |
| **SquareWrite** | a Write node with a Square tab |
| **SquareRead** | a Read node with a Square tab |
| **Render && Publish** | render the selected Write, then open the Publish panel |
| **Publish Output…** | the Publish panel for the selected Write **or Read** |
| *(status item)* | who's signed in -- click to see it as a message too |
| **Sign In…** | the shared Kitsu login dialog |
| **Sign Out** | forgets the cached session (this machine only) |

## Publish

Publishing always goes through the **Publish Output** panel — the cascade +
media type + version (`(new)` = the current workfile major, or re-render an
existing one) + a comment + a *make review preview* toggle, all pre-populated
from the selected node and editable. It publishes:

- a **Write** — its `file` pattern over the script frame range
- a **Read** — its `file` over the Read's range (register an external / delivered
  render as an output version)

A SquareWrite's **Render** button renders over the script range then opens the
panel; untick its **Publish after render** knob to just render (publish later
via *Publish Output…*). A locked target version is refused.

## Context

Every panel and gizmo tab has the same **project → episode → sequence → shot →
task** cascade (episode is shown only for `production_type: tvshow` projects). A
Nuke launched by another Square tool pre-selects from `SQUARE_PROJECT` /
`SQUARE_EPISODE` / `SQUARE_SEQUENCE` / `SQUARE_SHOT` / `SQUARE_TASK`; saving or
opening a version writes those back.

## Versions: major vs minor

A workfile has a **name** (a stream — `main`, `precomp`, `final`, …), a
**major**, and a **minor**. Only **majors** are recorded in Kitsu. **Minors**
are plain `.nk` saves on disk between majors —
`…_comp_main_v001.001.nk`, `…_v001.002.nk`, … The tool globs the version folder
to list them.

- **Save Version panel** — pick the workfile name and *minor up* (WIP save, same
  major) or *major up* (milestone; resets minor to 1 and records the major in
  Kitsu). Shows the destination path before you commit. No comment field —
  comments belong on the task at publish/review time.
- **Open Version panel** — the cascade + name, then a flat version list
  (`v003.002`, `v003.001`, `v002.001 (offline)` …). "Offline" = the file isn't
  on this machine's NAS path. Opening prompts *clear & open here* / *open in a
  new Nuke* if the session already has nodes.

**Version alignment.** Workfile major **N** ⇔ published output **vN**, by
construction (publish uses the workfile major, not "next output revision"; a
re-render replaces vN unless it's locked). Review **previews** float — you may
post several per version — but each preview's comment names the version it
reviews (`Preview — CompRender v003`).

## SquareRead / SquareWrite

Stock Read / Write nodes with a **Square** tab. Its knobs (project … task, media
type, name, version) drive the node's `file`; a `knobChanged` callback keeps it
in sync.

- **SquareWrite** — media type lists only `renderable` types (`CompRender`,
  `Precomp`, …). Version **(new)** = the current workfile major, so the
  published output version always equals the workfile major it came from;
  picking an existing version re-renders it in place (blocked if that version
  is **locked** — reviewed / delivered — with the status knob saying so).
  `Make preview on publish` toggles the review proxy. A **Render & Publish**
  button renders locally and publishes in one step.
- **SquareRead** — media type lists delivery + publish types (plates, elements,
  renders). Resolves the path, colorspace, and frame range for the chosen
  version.

## Layout

```
tools/dcc/nuke/
  context.py   Target + SQUARE_* env round-trip
  ops.py       NukeOps -- every pipeline call, no `import nuke` (unit-tested)
  gizmos.py    SquareRead / SquareWrite: the Square tab + knobChanged callback
  panel.py     Open Version / Save Version panels + menu-command functions
  menu.py      builds the Square menu, registers the callbacks
```

Maya / Houdini reuse `ops.py` and `context.py`; only `gizmos.py` / `panel.py` /
`menu.py` are DCC-specific.
