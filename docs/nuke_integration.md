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

The menu's own top-level label shows who's signed in: **Square** when
signed out, **Square — Jane Doe** once known. It refreshes right after
**Sign In…** / **Sign Out**, or the first time any command successfully
authenticates with an already-cached token (never at Nuke startup itself,
which must not block on a Kitsu round trip). Nuke's classic menu API has no
live-updating label, so this is a full remove-and-rebuild of the menu each
time (`menu.build()`) -- but removal asks Nuke what's actually on the menu
bar right now (`Menu.items()` / `.name()`), not a name this module tracked
separately in a Python variable. An earlier version did the latter, that
tracked value fell out of sync with the real current name after the very
first rename, `Menu.removeItem(name)`'s exact-match lookup missed, and a
second "Square" menu got left behind instead of replacing the first.

Only one of **Sign In…** / **Sign Out** is ever present, never both --
whichever matches the current state.

| Command | |
|---|---|
| **Save Version…** (`Ctrl+Alt+S`) | the Save Version panel |
| **Open Version…** (`Ctrl+Alt+O`) | the Open Version panel |
| **Minor Up** | save the currently open script as the next minor, in place -- no picker |
| **Major Up** | save the currently open script as a new major, in place -- no picker |
| **SquareWrite** | a Write node with a Square tab |
| **SquareRead** | a Read node with a Square tab |
| **Render && Publish** | render the selected Write, then open the Publish panel |
| **Publish Output…** | the Publish panel for the selected Write **or Read** |
| **Sign In…** | the shared Kitsu login dialog -- shown only when signed out |
| **Sign Out** | forgets the cached session (this machine only) -- shown only when signed in |

**Minor Up** / **Major Up** act on whatever script is open right now, under
whichever of its known name-streams that script's own path matches (falling
back to `main` if it doesn't match any) -- the fast path for the common case;
**Save Version…** remains for saving under a different project/shot/name than
what's currently open. All three refresh every Square Write/Read node already
in the script afterward, since `nuke.scriptSaveAs()` changes what's open
without going through any node's own knobs: a Write left on `(sync)` would
otherwise keep resolving against the major that was open *before* the bump
until something else happened to touch its knobs.

## Render

A SquareWrite's **Render** button opens the **Render** panel first: the
resolved path, an editable frame range (defaulting to the script range), and
the *Make review preview* / *Publish after render* toggles -- ticking
*Publish after render* also reveals a comment field right there. Confirming
it renders, then -- if publish was requested -- publishes immediately with no
further dialog: **Publish after render** means an uninterrupted render then
publish, not a render followed by a second confirmation asking for the same
thing again (an earlier version of this flow opened the Publish panel
*after* rendering even when publish was already confirmed up front). Untick
*Publish after render* to just render (publish later via *Publish Output…*,
or the node's own direct **Publish** button for frames that are already on
disk). Its **Create Read** button drops a SquareRead pointed at exactly what
it just rendered/published (same shot/task/media type/name/version) -- no
re-navigating the cascade to check your own render. A locked target version
is refused, and refused before you even get to render it — see *Locking*
below.

## Publish

**Publish Output…**, or a SquareWrite/Read's own **Publish** button, opens
the **Publish Output** panel for frames that are already on disk: the
cascade + media type + name + version + a comment + a *make review preview*
toggle, all editable. The version defaults to whatever's **already embedded
in the source's own path** (every output nests under `.../v{version}/...`)
— the frames are already sitting at that number, so that's what gets
published, not a re-resolved guess; the panel says whether that version is
already published (a re-publish) or brand new. `(new)` is still offered
explicitly for claiming a fresh number instead. It publishes:

- a **Write** — its `file` pattern over the script frame range
- a **Read** — its `file` over the Read's range (register an external / delivered
  render as an output version)

Every publish (from the Render panel or this one) snapshots the
currently-open script into that render's `.000` minor (see *Versions*
below) — this is the actual provenance record of "the script that produced
this," independent of whatever major/minor the artist happened to have last
saved to.

## Context

Every panel and gizmo tab has the same **project → episode → sequence → shot →
task** cascade (episode is shown only for `production_type: tvshow` projects). A
Nuke launched by another Square tool pre-selects from `SQUARE_PROJECT` /
`SQUARE_EPISODE` / `SQUARE_SEQUENCE` / `SQUARE_SHOT` / `SQUARE_TASK`; saving or
opening a version writes those back.

## Versions: major vs minor

A workfile has a **name** (a stream — `main`, `precomp`, `final`, — and on a
SquareWrite/SquareRead node it's free text, not a fixed list: a shot can carry
more than one parallel stream under the same media type, e.g. Precomp `fg` /
`bg` / `keying`, not just `main`), a **major**, and a **minor**. Only
**majors** are recorded in Kitsu. **Minors** are plain `.nk` saves on disk
between majors — `…_comp_main_v001.001.nk`, `…_v001.002.nk`, … The tool globs
the version folder to list them.

**Minor `.000` is reserved** — never an artist WIP save. Every render (from
either `(new)` or `(sync)`) overwrites `v{major}.000.nk` with an exact copy of
the actually-open, already-saved script that produced it, then marks the file
read-only on disk so an accidental `Ctrl+S` can't silently drift it away from
the render it documents. The Open Version panel labels it **"(rendered)"**
rather than hiding it — it's a real, openable script, just not one to keep
working in.

- **Save Version panel** — pick the workfile name and *minor up* (WIP save, same
  major) or *major up* (milestone; resets minor to 1 and records the major in
  Kitsu). Shows the destination path before you commit. No comment field —
  comments belong on the task at publish/review time.
- **Open Version panel** — the cascade + name, then a flat version list
  (`v003.002`, `v003.001 (rendered)`, `v002.001 (offline)` …). "Offline" = the
  file isn't on this machine's NAS path. Opening prompts *clear & open here* /
  *open in a new Nuke* if the session already has nodes.

### `(new)` vs `(sync)`

The SquareWrite version dropdown offers two sentinels instead of one:

- **`(sync)`** — the render version always equals the **actually open
  script's own major** (parsed from its path, not just trusted from Kitsu);
  repeated test renders while iterating on the same major just refresh that
  version's frames and its `.000` snapshot in place. If the open script isn't
  a recognized workfile for this shot/task, or Kitsu has no registered
  major yet, it falls back to whatever Kitsu has registered as latest.
  Refused outright if that major is **locked** (see below). This is the
  default, and the closest match to "day to day" comping. This is why it's
  "sync to the workfile I have open," not "sync to whatever Kitsu calls
  latest" — those can differ, e.g. a teammate (or an earlier session of your
  own) bumped the major elsewhere while this script stayed open on an older
  one.
- **`(new)`** — always the next-after-highest version for this
  `(shot, media type, name)`, ignoring the workfile major entirely — genuinely
  new numbers for genuinely new work, never blocked by a lock (nothing occupies
  a fresh number yet). If the workfile's own major isn't already at the number
  it just rendered, Square registers a `working_files` record there
  automatically (jumping straight to that number, not incrementing by one) so
  the output is never left without a matching workfile entry in Kitsu.

Before a SquareWrite actually renders, Square checks that the open script is a
real, saved workfile matching this shot/task/name (not an unsaved scratch
session, and not a file that merely looks right but belongs to a different
shot) — and, for `(sync)` specifically, that its major isn't already behind
the latest one registered in Kitsu. Either problem shows a warning with the
option to render anyway.

**Version alignment.** `(sync)` keeps output version **N** ⇔ workfile major
**N** by construction. `(new)` deliberately breaks that equivalence at render
time and then repairs it by fast-forwarding the workfile record to match.
Review **previews** float — you may post several per version — but each
preview's comment names the version it reviews (`Preview — CompRender v003`).

### Locking

A **locked** output version (reviewed / delivered) can't be re-rendered.
`(sync)` resolving to a locked major refuses at the Write node itself, before
any Kitsu round trip at render time: the node's `file` knob is left blank (so
Nuke's own native Render / farm submit can't silently overwrite it either, not
just Square's own Render/Publish buttons) and both **Render** and **Publish**
buttons are disabled, with the status knob saying which version is locked.
Picking `(new)` — or any other, unlocked version — clears the block.

## SquareRead / SquareWrite

Stock Read / Write nodes with a **Square** tab. Its knobs (project … task, media
type, name, version) drive the node's `file`; a `knobChanged` callback keeps it
in sync. Laid out in sections, divider lines between them:

```
Project
Episode   Seq
Shot
──────────────
Task      Media type
Name
──────────────
Version   [Refresh]
<status>
──────────────          (SquareWrite only from here down)
[x] Make preview
[x] Publish after render
[Render] [Publish]
──────────────
[Create Read]
```

Creating a node is lazy, one cascade level at a time: it costs one Kitsu call
(the project list) unless the launch context (`SQUARE_PROJECT` / `_SEQUENCE`
/ `_SHOT` / `_TASK`) names a project, in which case it walks project →
sequence → shot → task → media type/version, but only as far as that context
actually specifies -- a bare project stops after loading episode + sequence;
picking a sequence by hand loads shots; picking a shot loads tasks; picking a
task loads media types and versions. The usual case (launched from the
workfile manager with a full target already known) still resolves the whole
node in one pass. Changing any knob in that chain re-resolves everything
below it automatically (picking a new shot immediately refreshes media type /
name / version, not only once media type is next touched by hand), plus a
**Refresh** button next to Version for state that changed outside this Nuke
session entirely -- another artist's publish, a new lock -- which no knob
change in this session would otherwise surface. `NukeOps` also caches each
project's shot list, each shot's task list, and each shot's output-file list
for the life of the Nuke session -- the same shot/task data used to get
re-fetched from Kitsu 5-7 times over for a single node, which was the actual
cause of "creating a Read or Write node is slow"; **Refresh** is the explicit
escape hatch from that caching.

- **SquareWrite** — media type lists only `renderable` types (`CompRender`,
  `Precomp`, …); **Name** shares Media type's line and lists known
  name-streams already published under that media type (`main`, `fg`, `bg`,
  …) but accepts any typed value too, since a brand new stream has no Kitsu
  record to list yet. Version is `(new)` / `(sync)` / an explicit existing
  version to re-render in place — see *Versions* above for what each means
  and how locking blocks a render. `Make preview on publish` toggles the
  review proxy. A **Render** button renders locally and (unless *Publish
  after render* is off) publishes in one step; **Publish** publishes frames
  that are already on disk without re-rendering; **Create Read** drops a
  SquareRead pointed at exactly what this Write is currently resolved to.
- **SquareRead** — media type lists delivery + publish types (plates, elements,
  renders); **Name** is the same known-streams-plus-free-text selector.
  Resolves the path, colorspace, and frame range for the chosen version.

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
