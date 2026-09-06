# Nuke integration (`tools/dcc/nuke/`)

Tool #3. Runs inside Nuke's own Python (14+, i.e. Python 3.9+). It imports
`square_core` directly and authenticates through the shared
`~/.square/session.json`, like every other Square tool.

## Install

Put the repo root on Nuke's path so `menu.py` runs on start (`NUKE_PATH`, or
`sys.path.insert(0, ...)` in a studio `~/.nuke/init.py`). The **Square** menu
appears and the SquareRead / SquareWrite callbacks are installed.

A user must have signed in once through any Square tool so the JWT is cached;
Nuke doesn't prompt for a password.

## The Square menu

| Command | |
|---|---|
| **Save Version…** (`Ctrl+Alt+S`) | the Save Version panel |
| **Open Version…** (`Ctrl+Alt+O`) | the Open Version panel |
| **SquareWrite** | a Write node with a Square tab |
| **SquareRead** | a Read node with a Square tab |
| **Publish Selected Write** | publish the selected Write's rendered frames |

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
  Kitsu). Shows the destination path before you commit.
- **Open Version panel** — the cascade + name, then a flat version list
  (`v003.002`, `v003.001`, `v002.001 (offline)` …). "Offline" = the file isn't
  on this machine's NAS path. Opening prompts *clear & open here* / *open in a
  new Nuke* if the session already has nodes.

## SquareRead / SquareWrite

Stock Read / Write nodes with a **Square** tab. Its knobs (project … task, media
type, name, version) drive the node's `file`; a `knobChanged` callback keeps it
in sync.

- **SquareWrite** — media type lists only `renderable` types (`CompRender`,
  `Precomp`, …). Version defaults to **(new)**; picking an existing version that
  is **locked** (reviewed / delivered) blocks the render and the status knob
  says so. `Make preview on publish` toggles the review proxy.
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
