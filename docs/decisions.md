# Decisions

Locked calls, with the short reason. Change one only by adding a new dated
entry that supersedes it — don't silently edit.

## Pipeline architecture (2026-09-02)

Full design in `pipeline_architecture.md`. The load-bearing calls:

- **Three layers, dependencies point down only.** `tools/*` → `square_core`
  (model + services + shared capability) → `square_core/kitsu/` (the only place
  `gazu` is imported). Tools never touch gazu or Kitsu wire fields; they call
  services and pass model objects. Core guarantees whole side effects
  (`projects.create()` = Kitsu project + folders + config in one call).
- **One production store: Kitsu**, reached only through `square_core/kitsu/`.
  **No parallel pipeline database, and no neutral "backend protocol" / no
  `InMemoryBackend`.** Square is not switching trackers in the foreseeable
  future; the "swap someday" insurance is simply that all gazu use is one
  package. `square_core/kitsu/` speaks Kitsu's own nouns. Supersedes the
  ingest-era "NAS + ledger are source of truth" stance (a tool-specific call).
- **Paths vs versions (verified live 2026-09-02):** `square_core/paths/
  PathResolver` computes every on-disk path from `ProjectConfig` templates —
  full casing control (entities keep the case the client sent). **Kitsu owns
  version numbers** via `working_files` / `output_files` revisions
  (`get_next_entity_output_revision` etc. — work without a file_tree). On
  save/publish: create the Kitsu file record (Kitsu assigns the revision), then
  `PUT` our resolved path + provenance `data` onto it — override verified, mixed
  case preserved. `projects.create` sets one throwaway minimal `file_tree` only
  because `new_working_file` / `new_entity_output_file` reject without one; its
  computed output is never used. Zou file_tree's uppercase/lowercase-only limit
  is therefore moot. **Media type = a Kitsu output type.** Shot folder skeleton
  and client-delivery paths/packages are pipeline core (Zou has neither).
- **`projects.create` applies a Kitsu project template** for task types,
  statuses and status automations.
- **`ingest_ledger.db` stays a temporary tool-local exception** in
  `tools/ingest_tool/core/`; not generalized.
- **`Episode` is Zou-native** (`production_type="tvshow"`) — referenced, not
  reinvented. Seq→shot today; episodic not ruled out.
- **CG `Asset` is a parallel entity, added in Phase B.** Asset tasks reuse
  `work` / `review`.
- **No custom automation engine.** Status rollups / downstream unlocking are
  Kitsu's own status automation. Add pipeline-side rules only if Kitsu can't
  express one.
- **`square_core` earns entries on a real second consumer** or if plainly
  foundational. Most ingest domain moves out to `tools/ingest_tool/core/`
  (`restructure_plan.md`) — **folded into Phase A**, not a separate branch.
- **Tools read the pipeline, never their own copy of a fact or convention.**
  Path rules, templates, statuses, version numbers come from `ProjectConfig` /
  core services / Kitsu. A tool keeps only its own *functional* logic (the
  ingest table's conflict resolution, a DCC panel's UI). The ingest tool
  predates this plan and is reworked to it — no duplicate implementations of
  shared rules.
- **Config splits: `StudioConfig`** (per install — Kitsu host, NAS roots,
  credentials in the OS keyring; **plus the studio-wide defaults**: default
  fps/res, the folder-skeleton convention, the path-template set, the list of
  available Kitsu project templates) vs **`ProjectConfig`** (NAS JSON with the
  project — the resolved templates, folder skeleton, ingest media-type paths,
  client presets, colorspace).
- **`projects.create` copies the relevant `StudioConfig` defaults into the new
  project's `ProjectConfig`**, so the project is self-contained and a later
  studio-default change never retroactively moves an existing project's paths.
  `ProjectSpec` is thin: code, name, `production_type`, project-template choice,
  NAS root (from `StudioConfig`), client, plus any per-project overrides.
- **`ProjectConfig` + `PathResolver`** — full spec in `config_and_paths.md`.
  `{token}` path templates (not Kitsu's file_tree tokens); `PathResolver` is
  pure (path in/out, no Kitsu, no FS); **case is preserved** on every token
  (client's `Sh010` stays `Sh010`), values slugified, block-level `case`
  override for demanding clients. `roots` may reference each other. No separate
  `render/` — renders write straight to `output/`. `{name}` (Kitsu's file
  `name`, default `main`) kept for multi-piece shots. `ProjectConfig.load()`
  runs `validate()` and refuses a config whose templates don't vary by version
  (the current silent-fallback is how the Element/LUT overwrite bug shipped).
- **The Kitsu `data` nesting key is `"square"`** — renamed from the ingest
  tool's `"square_ingest"` in the Phase A port. Nothing's in production, no
  migration. Everything the pipeline stamps on a Kitsu record
  (`preview_file` / `working_file` / `output_file` / entity `data`) nests under
  `data["square"]`.
- **Auth: per-user login; JWT + refresh cached** (keyring or
  `~/.square/session.json`, mode 600), shared by every tool + DCC on a
  workstation. `kitsu/auth.py` is **non-interactive** (`login(email, pw)` /
  `cached_session()`); `PipelineContext.connect()` raises `NeedsLogin`, the
  tool prompts (shared dialog in `tools/_shared/`, stdin for CLI) and retries.
  Farm nodes use a service token.

## Architecture

- **Rewrite the ingest tool, don't keep patching it.** It never shipped, so
  there's no compatibility to preserve, and the old version was ~1,600 lines
  of layered state keyed by `id(item)`.
- **Framework-agnostic core; Qt only at a thin seam.** The controller and all
  domain logic are plain Python and emit events; a `ControllerBridge`
  re-emits them as `Signal(object)`. An `id()`-keyed dict across a queued Qt
  signal is what silently broke the old NAS check on PySide6.
- **Tests pin `QT_PREFERRED_BINDING=PySide6`.** Both bindings are installed;
  a machine with only PyQt6 must not be able to pass tests on a binding real
  users never hit.
- **`square_core` is pure and shared.** Tool-specific domain logic lives
  under the tool. See `restructure_plan.md`.

## Ingest — versioning & conflicts

- **Kitsu version-checking is deferred** *(ingest tool, as built)*. NAS layout +
  the SQLite ledger are the source of truth for "what version is this". Kitsu is
  written to, not queried, for versioning. (A preview forces one-media-per-task,
  which breaks a naive Kitsu version model — revisit with output-files / assets.)
  → **Superseded going forward** by the 2026-09-02 pipeline decision: Kitsu is
  the store, behind the backend. The ingest tool keeps its ledger as a
  tool-local exception until it's ported (Phase A+).
- **"Already Ingested" = the exact target folder already holds this exact
  content** (NAS slot match). Identical content ingested *elsewhere* (another
  shot, another version) is a `DUPLICATE_CONTENT` **warning**, not a block —
  delivering the same plate to two shots is a real workflow.
- **`dest-collision` is a hard block.** Two rows resolving to the same
  destination path is guaranteed data loss during the copy.
- **Conflict actions:** Skip · Version Up · Overwrite · Edit · Ignore
  (Ignore is warn-only).
- **Dry run is a pure simulation** — zero side effects: no Kitsu shot/task,
  no folders, no file copy, no ledger row.
- **Never ship a guessed colorspace / fps / resolution.** If the header
  didn't carry it, the cell is blank and the row is blocked ("Needs Info")
  until set. Colorspace is a delivery convention, rarely in an EXR header.

## Ingest — Kitsu writes

- **The Ingest task moves to `Done`** on a successful ingest
  (`ingest_task_status`, configurable, `""` disables).
- **The Kitsu preview carries source + destination provenance** in
  `preview_file.data["square_ingest"]` (namespaced — Zou drops unknown
  top-level keys and also writes media dimensions into `data`).
  → **Key renamed `"square_ingest"` → `"square"` in the Phase A port** (see the
  pipeline section above); the namespacing rationale is unchanged.
- **One comment + one preview per version.** The comment is the
  self-describing record; it's posted even when there's no preview.

## Ingest — session

- **Session is a user-named `*.sqingest.json`** the user places. No hidden
  sidecars. Atomic write, debounced autosave, "reopen last session?" prompt.
- **`FolderMapper` is in-memory only.** The old hidden
  `.square_ingest_map.json` sidecar is gone; Path Patterns + manual tags
  round-trip through the session, named presets through `studio_config.json`.
- ~~**The session carries a config snapshot**~~ → **Superseded 2026-09-02.**
  The session does **not** snapshot config. On resume the tool reads the live
  `ProjectConfig`. Rationale: a JSON snapshot inside the session file is itself
  editable between opens, so it's not a real reproducibility guarantee — and
  after the pipeline rework config lives with the project, versioned there if
  we ever need resume-against-old-config.

## Performance

- **Preview encode + upload run off the critical path.** The row is
  `Completed` the moment files are verified on the NAS and Kitsu has the
  version; previews trickle in behind on a separate pool.
- **Verify copies on write, reusing the pre-flight hash.** The destination
  is hashed as it's written and compared to the source hash pre-flight
  already computed — one read of the source, not three.
- **One shared copy pool** sized to `copy_workers` for the whole batch (was
  `items × frames`).
- **`xxh3_64` everywhere** — pre-flight, verify, ledger. No mixed algos.
- **Native `CopyFileExW` on Windows** for the byte move, stream fallback
  elsewhere.

## Repo

- **`ledger` lives at `{nas_root}/{project_code}/_pipeline/ingest_ledger.db`.**
- **Committed binary test fixtures were purged from history**
  (`git filter-repo`, 2026-09-01). `test_data/` is gitignored; anything that
  ends up there must never be committed. Repo went 656 MB → ~700 KB.
- **PR target for the ingest rework is `ingest_tools`** (all the code lives
  there; `master` is essentially empty).
