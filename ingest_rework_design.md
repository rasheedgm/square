# Ingest Tool — Rework Design

Status: **draft for review**. Mark it up inline (`>> AR:` comments or just edit).
Nothing here is built yet.

---

## 1. Why a rewrite, not more patches

- `table_widget.py` is ~1,600 lines of layered state: 12 parallel dicts/sets all
  keyed by `id(item)` (Python object identity). That key can't survive a
  save/reload or a crash-resume, and it's what silently broke the NAS check on
  this laptop (PySide6 can't marshal an `id()`-keyed dict across a Qt signal —
  the sandbox only had PyQt6 so it never surfaced).
- Pre-flight is a pile of overlapping `QThread` workers that terminate each
  other and race.
- Two selection concepts (Include checkbox + discarded set + Qt row selection)
  that overlap and disagree.

Keep, essentially as-is: the **Path Pattern tagging engine** (`path_pattern.py`,
`token_parser.py`, `folder_mapper.py`, the builder dialog), `metadata_extractor.py`,
`proxy_generator.py`, `plate_scanner.py` (minor changes).

Rewrite: `table_widget.py`, `ui_main.py`, the worker/threading layer,
`nas_manager.py`'s check pipeline, `kitsu_client.py`'s version model.

New: an ingest **ledger**, a **session file**, a **PreviewMetadata** object, an
**IngestController**.

`tools/rollback_cli.py` is unrelated to this tool — it's the pipeline-deployment
version switcher for `deploy_studio_pipeline.py` (flips the `current` junction
between `releases/vX.Y.Z`). Leaving it alone.

---

## 2. Architecture

```
square_core/
  ingest_item.py        NEW  one row's full state + stable identity + state machine
  ingest_controller.py  NEW  owns the item list + a ThreadPoolExecutor; runs
                             pre-flight and ingest as stage sequences; the only
                             thing the UI talks to for work
  ingest_ledger.py      NEW  per-project SQLite record of every ingested file (hash-keyed)
  ingest_session.py     NEW  save/load the user-named session file
  preview_metadata.py   NEW  the reusable object stamped onto each Kitsu preview
  kitsu_recorder.py     NEW  one interface for "record this version in Kitsu";
                             today's impl = shot/task/comment/preview; swappable
                             later for gazu output-files/assets
  kitsu_client.py       thin gazu wrapper only (connect, CRUD, upload) — no
                             ingest policy in here anymore
  nas_manager.py        path building + copy engine + slot inspection; check
                             logic moves into the controller
  hashing.py            NEW  hash-once cache keyed by (path, size, mtime),
                             shared by pre-flight / ledger / copy-verify

tools/ingest_tool/
  ui_main.py            rebuilt: thinner, controller-driven
  widgets/
    review_table.py     NEW  replaces table_widget.py — a pure view over
                             controller.items + inline resolve affordances
    detail_panel.py     NEW  selected row: all metadata, conflict explanation,
                             resolution buttons, source file list
    resolve_bar.py      NEW  batch-resolve strip when >1 row selected
    (path_pattern_dialog, settings_dialog, task_selection_dialog,
     results_dialog kept; progress_dialog deleted)
```

**Threading model.** One `IngestController` living on the main thread, owning a
`concurrent.futures.ThreadPoolExecutor`. Per-item updates come back via a single
`Signal(object)` carrying a small dataclass that includes the item's **stable
string id** — never a dict keyed by `id()`. Pre-flight and ingest are the same
machinery: a list of stages run per item through the pool, cancellable.
Tests pin `QT_PREFERRED_BINDING=PySide6` so they run on what the user runs.

---

## 3. Data model

### `IngestItem`

Stable identity: `key = xxh3_64(sorted(abs source paths))` — deterministic,
survives reload and resume. A `uuid4` is also stored in the session for
display/debug.

```
key                 str      content-derived, stable
source_files        [str]
is_video            bool

# tagged / editable
sequence_code       str
shot_code           str
media_type          str
media_name          str
version             int      resolved target version (auto or user-picked)
extra_tags          {str:str}  pattern-captured axes outside the 5 canonical

# grabbed metadata (editable; blank if unreadable — see §7)
start_frame end_frame missing_frames
fps  resolution  colorspace  timecode
metadata_verified   {field: bool}   False = extractor couldn't read it

# derived
dest_dir            str      computed from templates; read-only in UI
sample_dest_file    str      first frame's final path

# per-session decisions
preview_wanted      bool     defaults from config, user-toggleable this session
skipped             bool     user chose "skip" — excluded from ingest
resolutions         {conflict_id: "skip"|"version_up"|"overwrite"|"ignore"}

# status (see §6)
status              Status enum
stage               Stage enum      live pre-flight / ingest stage
stage_pct           int
issues              [Issue]         current warnings/conflicts with detail text
hashes              {path: hex}     filled by pre-flight, reused everywhere
ledger_match        LedgerMatch|None
```

### `PreviewMetadata` (`square_core/preview_metadata.py`)

The reusable object other tools will read back. Serializes to
`preview_file.data["square_ingest"]` (Zou drops unknown top-level keys — this is
the one writable spot; confirmed live).

```python
@dataclass
class PreviewMetadata:
    schema_version: int = 1
    source_path: str = ""        # original delivered location (folder)
    source_sample_file: str = "" # a real delivered filename
    nas_path: str = ""           # ingested destination folder
    nas_sample_file: str = ""    # a real ingested filename
    frame_range: str = ""
    file_count: int = 0
    fps: float | None = None
    resolution: str = ""
    colorspace: str = ""
    checksum: str = ""           # xxh3 of the first file
    checksum_algo: str = "xxh3_64"
    transfer_mode: str = "copy"
    sequence_code: str = ""
    shot_code: str = ""
    media_type: str = ""
    media_name: str = ""
    version: int = 1
    ingested_at: str = ""        # ISO8601 Z
    ingested_by: str = ""        # kitsu user email
    batch_id: str = ""

    def to_kitsu_data(self) -> dict: ...
    @classmethod
    def from_kitsu_data(cls, data: dict) -> "PreviewMetadata | None": ...
```

Same object is written into the shot-data version ledger entry and into the NAS
ledger row, so all three carry one answer.

---

## 4. Ingest ledger — "have these exact files gone in before?"

Per project: `{nas_root}/{project_code}/_pipeline/ingest_ledger.db` (SQLite).

```sql
CREATE TABLE ingested_file (
  file_hash    TEXT NOT NULL,       -- xxh3_64 of full content
  size         INTEGER NOT NULL,
  src_path     TEXT NOT NULL,       -- where it was delivered from
  dest_path    TEXT NOT NULL,       -- where it landed
  seq          TEXT, shot TEXT, media_type TEXT, media_name TEXT,
  version      INTEGER,
  batch_id     TEXT NOT NULL,
  ingested_at  TEXT NOT NULL,
  ingested_by  TEXT,
  PRIMARY KEY (file_hash, dest_path)
);
CREATE INDEX idx_hash ON ingested_file(file_hash);
```

**Pre-flight lookup** (per item): hash every source file once (cached by
path+size+mtime), look each hash up.

- every file's hash already in the ledger → **`Already Ingested`** (status),
  detail = "identical content already at `<dest>`, v3, 2026-08-20 by X".
- some but not all → **`Partial Overlap`** warning, detail lists which files
  match and where. Actions: **Skip** · **Version Up** · **Overwrite same
  version** (re-run the transfer — copy / symlink / hardlink per config — over
  the existing version folder, replacing what's there).
- none → no ledger finding.

Ledger is written **after a verified copy**, one row per file, inside the same
transaction as the Kitsu record so they can't drift.

Hash strategy: full `xxh3_64` always. It's ~GB/s; for a 300×50 MB EXR sequence
that's ~15 s once, and we'd hash on copy-verify anyway. Cache means it's paid
once per session.

---

## 5. Session file

User-named, user-placed, e.g. `~/ingests/showX_2026-09-01.sqingest.json`.
Plain JSON. **No hidden sidecars** — the `.square_ingest_map.json` FolderMapper
sidecar is retired; its patterns move into the session.

```jsonc
{
  "schema_version": 1,
  "saved_at": "2026-09-01T12:00:00Z",
  "app_version": "…",

  "config_snapshot": {           // so a resume is reproducible even if studio
    "nas_root": "…",             // config changed since
    "filename_template": "…",
    "nas_dir_template": "…",
    "media_type_configs": { … },
    "shot_folder_structure": [ … ],
    "preview_enabled_media_types": [ … ],
    "transfer_mode": "copy",
    "copy_workers": 4
  },

  "project": { "id": "…", "name": "…", "code": "…" },
  "task_types": ["Ingest", "Prep", …],   // the last task-selection for this batch
  "dry_run": false,

  "delivery_root": "…",
  "path_patterns": [ … ],        // was the hidden sidecar

  "batch_id": "uuid",            // stamped onto ledger + Kitsu records
  "items": [
    {
      "key": "…", "uuid": "…",
      "source_files": [ … ],
      "sequence_code": "…", "shot_code": "…", "media_type": "…",
      "media_name": "…", "version": 3, "extra_tags": { … },
      "fps": 24.0, "resolution": "3840x2160", "colorspace": "ACEScg",
      "metadata_verified": { "colorspace": false, … },
      "preview_wanted": true,
      "skipped": false,
      "resolutions": { "dest-exists": "version_up" },
      "status": "Ready",
      "ingested": false,          // set true per item as it completes
      "ingest_result": null       // dest paths, checksums, kitsu ids when done
    }
  ],
  "undo_stack": [ … ]            // pre-ingest edit history (§8)
}
```

**Autosave**: to the chosen path on every state change (debounced ~1 s) and
after each item finishes ingesting. Explicit **Save / Save As**.
**On launch**: "Reopen last session? `<path>`" — last path kept in app-level
`QSettings`, not in the session.
**Resume**: items already `"ingested": true` load as locked/`Completed`; the
rest come back exactly where they were, conflicts and all.

---

## 6. Status taxonomy (one enum, no overlaps)

| Status | Meaning | Ingests? |
|---|---|---|
| `Checking` | pre-flight running for this row | no (yet) |
| `Needs Info` | required field blank (seq/shot/type/name, or unreadable colorspace/fps/res) | **blocked** |
| `New` | new shot + new media, nothing in the way | yes |
| `New Version` | shot/media exists, target is a clean next version | yes |
| `Ready` | had conflicts, all resolved | yes |
| `Conflict` | unresolved conflict (see §7) | **blocked** |
| `Warning` | soft issue only (typo-ish, partial overlap) — ingestable but flagged | yes |
| `Already Ingested` | ledger says identical content already in | no (auto) |
| `Skipped` | user chose to skip | no |
| `Check Failed` | pre-flight raised (permission, unreadable path) | **blocked** until re-checked |
| `Ingesting` | in progress | — |
| `Completed` | done this session (or resumed as done) | done |
| `Failed` | ingest raised for this row | can retry |

`Skipped` fully replaces the old Include-checkbox + `Discarded` set + `item_discarded`.
The Include column is gone. Selection = plain Qt row selection, used only to
target batch actions and "Ingest Selected".

---

## 7. Conflicts & resolution

### Conflict types

| id | Trigger | Actions offered |
|---|---|---|
| `dest-exists-diff` | target version folder exists with **different** content | Version Up · Overwrite · Edit |
| `dest-collision` | **two rows in this batch resolve to the same destination path** | Skip one · Version Up one · Edit — **hard block** until only one row owns that path |
| `rollback` | user picked a version ≤ one already on disk with other content | Version Up · Overwrite · Edit |
| `near-dup-batch` | seq/shot/media name near-identical to another row (SH0100 vs SH_0100) | Ignore · Edit |
| `case-inconsistent` | same code in different case across rows (SH0100 / sh0100) | Ignore · Edit |
| `illegal-chars` | field has chars illegal in a path | Edit (must) |
| `no-dest-template` | media_type has no destination template configured | Ignore (uses generic path) · Edit |
| `partial-overlap` | ledger: some files already ingested | Skip · Version Up · Overwrite same version |
| `preview-nonvisual` | preview forced on for Audio/LUT | Ignore · uncheck preview |

Note on `dest-collision`: **hard block** (confirmed) — two items copying to an
identical path is guaranteed data loss during the copy. It clears when only one
row still resolves to that path (skip the other, version one up, or rename a
field). Everything else in the list is warn-only.

**Overwrite** always means: re-run the transfer in the configured mode (copy /
symlink / hardlink) into the target folder, replacing whatever is there. Same
behaviour whether the folder held different content (`dest-exists-diff`) or a
partial set of the same files (`partial-overlap`).

### Actions

- **Skip** — status → `Skipped`, out of the batch. Reversible ("Include").
- **Version Up** — bump `version` to the next free slot, re-check.
- **Overwrite** — proceed into the occupied slot, replacing it. (This is the old
  "Override", renamed.) Confirmation on the batch, not per row.
- **Edit** — just means "go fix the field"; the conflict re-evaluates on change.
- **Ignore** — soft issues only; the warning stays visible in the detail panel
  and tooltip but stops blocking.

### Where resolution lives

- **Inline**: a conflict cell shows a small pill; click → popover with the
  applicable actions for that row.
- **Context menu** on selected rows: Skip / Include, Version Up, Overwrite,
  Ignore Warnings, plus batch field edits (Set Sequence, Set Media Type, …),
  Rename (template), Re-check, Ingest Selected.
- **Resolve bar**: appears above the table when >1 row is selected — the same
  actions as buttons, applied to the whole selection.

---

## 8. Undo (pre-ingest only)

An undo stack of **user edits before ingest**: rename, field edit, version pick,
skip/include, batch operations. `Ctrl+Z` / a toolbar Undo. Stored in the session
so it survives resume.

Once a row is `Completed`, it's locked — no undo, no rollback. (You confirmed:
after ingest, no rollback needed.)

---

## 9. Progress — per row, in the Progress column

No modal. No separate top bar. Stages, in order, shown as `label · %`:

```
Queued
Hashing            (pre-flight; also the ledger + verify hashes, computed once)
Checking conflicts
—— (ingest starts) ——
Waiting            (pool slot)
Creating shot/tasks   (Kitsu; skipped in dry-run writes)
Creating folders      (only if shot new or dest path missing)
Copying  n/N
Verifying             (checksum compare)
Generating preview    (only if preview_wanted)
Uploading preview
Writing metadata      (preview data blob + shot ledger entry + NAS ledger row)
Done   /   Failed
```

Bottom bar while a run is active: `4 / 12 done · 1 failed` + **Cancel**.
Cancel = finish the item in flight, stop before the next, session file lets you
resume. Dry-run still ends with the results dialog (first-class mode, unchanged).

---

## 10. Kitsu recorder

All Kitsu-side "record this ingest" logic behind one class:

```python
class KitsuRecorder:
    def ensure_shot(self, project, item) -> shot
    def ensure_tasks(self, shot, task_types) -> [task]
    def record_version(self, shot, item, result, preview_meta) -> None
        # today: comment on Ingest task + (preview upload + data.square_ingest
        #        stamp) if preview_wanted + versions[] entry in shot.data
        # later: swap for gazu output-files / asset instances without the
        #        controller or worker changing
```

Kitsu **version checking stays deferred** — NAS + ledger are the source of truth
for "what version is this". The recorder only writes.

Already found + fixed live (uncommitted, folds into this):
- `check_shots` mis-reported every existing shot as wrong-sequence (Zou returns
  `sequence_id: None`, link is in `parent_id`).
- `attach_preview_source_metadata` silently stored nothing (must nest under
  `data.square_ingest`).

---

## 11. UI layout

```
┌ header: project ▾   + New   ↻   [Kitsu ●]        Session: showX.sqingest ▾   Settings ┐
├───────────────┬──────────────────────────────────────────────────────────────────────┤
│ Delivery       │  Review table                                                       │
│  (folder tree) │  ☐-free. cols: Source · Seq · Shot · Type · Media · Extra ·          │
│  + Build/Manage│  Dest · Frames · FPS · Res · CS · Preview · Version · Status · Prog  │
│  Path Patterns │  ── resolve bar appears here when >1 row selected ──                 │
│                │                                                                      │
│                ├──────────────────────────────────────────────────────────────────────┤
│                │  Detail panel (selected row): full metadata, conflict explanation,   │
│                │  resolution buttons, source file list, ledger match info             │
├───────────────┴──────────────────────────────────────────────────────────────────────┤
│  12 rows · 9 ready · 2 conflicts · 1 skipped        [Dry Run]  [Ingest Selected] [Ingest All] │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

- `Preview` column: plain checkbox, initial value = config default for that
  media type, user-toggleable for the session.
- `Dest`, `Frames`, `Status`, `Progress` read-only. `Seq/Shot/Type/Media` and
  `FPS/Res/CS/Version` editable.
- Unverified metadata cell (extractor couldn't read it) renders blank with a
  dotted underline; row is `Needs Info` until filled. Bulk "Set Colorspace…"
  etc. in the context menu. **We do not ship a guessed colorspace.**

---

## 12. Testing

- Controller + item state machine: pure Python, no Qt — the bulk of coverage.
- Ledger, session round-trip, PreviewMetadata serialization: pure Python.
- Live Kitsu integration tests (now possible on this machine): a `zz_*`
  throwaway project, real gazu calls, torn down after. Gated behind an env flag
  so they don't run in a Kitsu-less environment.
- Qt view tests: pin `QT_PREFERRED_BINDING=PySide6`. Keep them thin — the view
  has no logic worth testing beyond "renders controller state".
- `nas_manager` copy/verify/transfer-mode: as today.

---

## 13. Phasing

1. **Core** — ✅ DONE. `preview_metadata`, `hashing`, `ingest_ledger`,
   `ingest_item`, `preflight`, `kitsu_recorder` + `kitsu_gateway`,
   `ingest_controller`, `nas_manager` rework API, `metadata_extractor.probe`.
   ~133 new pure-Python tests, all green. Recorder + gateway also smoke-tested
   against live Kitsu (comment path, preview path, `data.square_ingest` stamp
   preserving Zou's keys, two-version shot-data ledger).
2. **Session** — ✅ DONE. `ingest_session.py`: `IngestSession` (`*.sqingest.json`,
   atomic save, config snapshot, schema guard), `SessionAutosaver` (debounced,
   framework-agnostic), recent-session history for the launch prompt. Undo stack
   (pre-ingest edits, capped, survives resume) added to `IngestController`.
   +26 tests.
3. **UI** — ✅ core done. `controller_bridge.py` (the Qt seam —
   `Signal(object)` carrying whole events, the real fix for the stuck-rows
   bug), `widgets/review_table.py` (pure view, inline edits, context-menu
   resolution), rebuilt `ui_main.py` (folder tree → controller → table,
   bottom action bar, session menu + resume prompt, offline fallback).
   Deleted `table_widget.py`, `progress_dialog.py` + their obsolete tests.
   Detail panel is a read-only text pane for now; `resolve_bar` still TODO.
   `conftest.py` pins `QT_PREFERRED_BINDING=PySide6`.
4. **Kitsu live pass** — ✅ done. Full scan→preflight→ingest→verify against
   localhost Kitsu: shots created, version entries in shot.data, files on the
   NAS, ledger rows, and a re-scan of the same delivery correctly reads
   "Already Ingested". Recorder/gateway/preview-stamp also smoke-tested.
5. **Polish** — ✅ mostly done. `widgets/detail_panel.py` (selected row up
   close; per-issue Skip/Version Up/Overwrite/Ignore buttons; batch mode for
   multi-select; inline colorspace/fps/res setters for Needs Info rows) —
   replaces both the text pane and a separate resolve_bar. Keyboard: Ctrl+S
   save, Ctrl+Z undo, Ctrl+O open, F5 re-check. Controller rebuilds on a
   Settings change, keeping loaded rows. Table stretches its last column.
   Still open: dead-code cleanup in `kitsu_client.py` (harmless, its tests
   still pass — left for a follow-up).

Suite: **285 tests green**, ~10s. (Was 369; ~90 old
`table_widget`/`worker`/`concurrency` tests deleted, ~45 new UI tests added.)

## Fixes from real-UI testing (round 1)

1. **Dry run leaked** — it created the Kitsu shot/tasks and copied files. Now
   `_ingest_one` has a pure-simulation branch that computes the plan and
   touches nothing (no Kitsu, no folders, no copy, no ledger). Verified live:
   0 shots / 0 files / 0 ledger rows.
2. **"Already Ingested" was unresolvable** — the status came from a
   content-hash ledger match, so changing shot/version didn't clear it (same
   bytes → same hash). Now `ALREADY_INGESTED` comes from the **NAS slot**
   ("this exact target folder already holds this exact content"). Changing
   shot or version → slot is empty → status clears. It also carries an
   `ALREADY_IN_SLOT` issue so the panel offers Version Up / Overwrite.
3. **Same media to another shot / as another version** — a ledger match to a
   *different* destination is now a `DUPLICATE_CONTENT` **warning** (ingestable,
   Ignorable), not a block. Verified: deliver SH0100's frames to SH0200 →
   Warning → Ignore → New.
4. **Task status** — the Ingest task now moves to **Done** on a successful
   ingest (`ControllerConfig.ingest_task_status`, default "Done", set "" to
   disable). Verified live.
5. **In-cell editor overflowed the row** — the app-wide QSS gives every
   QLineEdit a 26px min-height + padding, which is taller than the 28px row.
   Fixed with a `_CompactEditDelegate` (forces editor geometry = cell rect,
   strips the padding) plus a table-scoped QSS override.
6. **EXR metadata** — `OpenImageIO` was never actually installed here, so EXR
   headers were never read and every EXR row showed resolution/fps/colorspace
   as unknown. Now installed (3.1.17). Resolution + colorspace read from the
   header; **fps and timecode are almost never in an EXR header**, so those
   stay "Needs Info" until set. Colorspace comes back in OIIO's interop-ID
   form (`ACEScg` → `lin_ap1_scene`) — the row still shows it for you to
   confirm/relabel.
7. **Resume dialog leaked into headless tests** — the "reopen last session?"
   `QTimer` fired a modal after the window closed. Now a tracked
   `QTimer` cancelled in `closeEvent`; the smoke test patches `_offer_resume`.
8. **Resume didn't restore the tree view / patterns** — the session file *did*
   store `delivery_root` + `path_patterns`, but `_resume` only rebuilt the
   table, leaving the folder tree empty. Now `_write_session` pulls the live
   root + Path Patterns + active preset from the folder tree, the session
   gained an `active_preset` field, and `FolderTreeWidget.restore(root,
   patterns, preset)` reopens the folder, repopulates the tree, re-applies
   the patterns, and selects the preset. Verified save→fresh-window→resume.
9. **Hidden sidecar scrapped** — `FolderMapper` no longer reads or writes
   `.square_ingest_map.json` (or anything to disk). It's a pure in-memory
   object: Path Patterns + manual media-type tags. The `_table_state`
   "remember the table" feature is gone too — that's the session's job.
   Everything a delivery's tagging needs now round-trips through the
   `*.sqingest.json` session (`path_patterns`, `manual_media_types`,
   `active_preset`) and named presets live in `studio_config.json`. Existing
   sidecar files on disk are now inert.

## Performance rework (copy + preview)

1. **Preview is off the critical path.** `_ingest_core` now finishes — files
   copied+verified, Kitsu version recorded, Ingest task → Done — and the row
   goes `Completed` *before* any ffmpeg runs. Proxy encode + upload +
   metadata-stamp happen on a separate 2-worker `_preview_pool`; the row
   shows "Ingested · preview running…" and flips to plain "Ingested" when the
   proxy lands. `ingest_finished` fires immediately; `previews_finished` later.
   Live-measured: `run_ingest()` returned in **0.8 s**, previews finished at
   **+13.6 s** — a 12-frame real-EXR shot.
   `IngestItem.preview_state`: `"" | pending | running | done | failed |
   skipped`. Resume re-queues previews that were mid-flight
   (`requeue_pending_previews` → `KitsuRecorder.resolve_ingest_task` +
   `attach_preview`).
2. **Verify-on-write, reusing the pre-flight hash.** `_copy_and_hash` streams
   the copy and hashes the bytes *as they're written* (or hashes the dest
   once after a native copy), then compares to the source hash the pre-flight
   already computed and cached. Was: full copy + re-hash source + hash dest =
   3 reads of the source per file; now 1. Same xxh3_64 everywhere
   (`calculate_checksum` switched from xxh64 to match).
3. **One shared copy pool.** `run_ingest` owns a single
   `ThreadPoolExecutor(copy_workers)` that *every* sequence's frame transfers
   submit into, so total concurrent file copies stay at `copy_workers`
   regardless of how many items overlap (was `items × frames`). Item
   orchestration is a separate small pool of blocking threads.
4. **Native `CopyFileExW` on Windows** for the byte move (faster than
   CPython's buffered `shutil` loop for large files), with a stream-copy
   fallback everywhere else.
5. `KitsuRecorder` split: `record_version` (critical path: comment +
   task→Done + shot.data entry, no preview) / `attach_preview` (deferred:
   upload + set_main + stamp + update the version entry). Both pin the task
   to Done so a late preview comment can't revert it.

Preview default now follows the media type: editing `media_type` in the table
re-derives the preview checkbox default unless the user has ticked it by hand
(`preview_user_set`).

## Running it

    env\Scripts\python.exe tools\ingest_tool\main.py      (or run_ingest_tool.bat)

Launches against the Kitsu in `studio_config.json` (localhost here). Offline
is a supported mode — you can still tag, resolve, and ingest to the NAS; only
the Kitsu writes are skipped.

### Phase 1 module map (as built)

| Module | Role |
|---|---|
| `square_core/preview_metadata.py` | `PreviewMetadata` value object; `to_kitsu_data(existing)` merges under `data["square_ingest"]` |
| `square_core/hashing.py` | `FileHasher` — xxh3_64, cache keyed by (path,size,mtime), thread-safe |
| `square_core/ingest_ledger.py` | SQLite `{nas}/{proj}/_pipeline/ingest_ledger.db`; `classify()` → none/partial/full |
| `square_core/ingest_item.py` | `IngestItem` (stable key, derived `status`), `Issue`/`Status`/`Stage`/`Action`/`IssueKind` enums |
| `square_core/preflight.py` | pure checks → `Issue` list: field/needs-info/illegal-chars, ledger, NAS slot, cross-item |
| `square_core/kitsu_gateway.py` | `GazuKitsuGateway` — the primitive gazu ops, verified live |
| `square_core/kitsu_recorder.py` | `KitsuRecorder` — the one place that writes an ingested version to Kitsu |
| `square_core/ingest_controller.py` | `IngestController` — owns items, runs preflight/ingest as staged pool work, emits `ControllerEvent`s (no Qt) |
| `square_core/nas_manager.py` | added `dest_names()`, `inspect_slot(hasher=)`, `next_free_version()` |
| `square_core/metadata_extractor.py` | added `probe()` — only fields actually read, so unverified ones can be flagged |

---

## Decisions locked

- **A.** Partial overlap → Skip · Version Up · **Overwrite same version** (re-run
  transfer in configured mode).
- **B.** `dest-collision` is a hard block; clears when one row owns the path
  (skip / version up / rename). Everything else warn-only.
- **C.** Ledger at `{nas_root}/{project_code}/_pipeline/ingest_ledger.db`.
- **D.** Session file: `*.sqingest.json`.
- **E.** "Ingest All" = every row with status in {`New`, `New Version`, `Ready`,
  `Warning`}. "Ingest Selected" = those, restricted to the current selection.

Next: build in the phase order in §13, starting with the pure-Python core.
