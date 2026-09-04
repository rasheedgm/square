# Square Pipeline — Architecture

Status: **draft for review.** Mark it up inline (`>> AR:` or just edit). This
supersedes the narrow "ingest tool" framing of `ingest_tool_design.md` — the
ingest tool is now tool #1 of a set, and `restructure_plan.md` folds into
Phase A (§12) as its first commits.

**Build status (2026-09-04):** Phase A is **built and merged to `master`**
(PR #2) — `square_core/` model, config (v2 media-type registry), paths, kitsu,
storage, media, context, services (projects / breakdown / media / work /
review), live-verified against Zou 1.0.58. Phase B's **config schema +
config-editor tool** (#2b) is built (`square_core/config/schema.py`,
`tools/config_editor/`). ~178 tests. Remaining: project-setup tool (#2), then
the ingest port (§12 step 6, on `ingest_tools`). Parts marked *(exists)* below
predate the rework and were folded in.

---

## 1. Scope

We are building **the studio pipeline**, not one tool. The pipeline is the code
that carries a project through its whole life:

```
awarded → planned → created (DB + storage + config)
        → client material ingested (shots created)
        → breakdown / takes / roadmap → tasks assigned
        → task started → workfile (managed path + version, save / publish)
                       → output (managed path + version, publish)
                       → task preview → supervisor review (annotate, comment)
                       → revision cycle → approval → automated status advance
                       → next department … → final department output
        → client send (client version, notes, tracking) → shot status
        → final delivery → archival
```

Every stage is a **core service** (the logic) plus a **tool** (the way a human
drives it). Status-driven follow-on effects are left to Kitsu's own automation.

---

## 2. The three layers and the one rule

```
┌──────────────────────────────────────────────────────────────┐
│  tools/          ingest · project-setup · DCC · review player │  UI / entry points
│                  · localize · vendor · deliver · deploy       │  (Qt, Nuke, CLI, …)
└───────────────▲──────────────────────────────────────────────┘
                │  calls services, passes/gets model objects
┌───────────────┴──────────────────────────────────────────────┐
│  square_core/                                                 │
│    services/   projects · breakdown · ingest · work · review  │  orchestration —
│                · delivery · context                           │  owns whole side effects
│    model/      Project Shot Task Workfile Output Preview …    │  light dataclasses
│    paths/ media/ storage/ config/                             │  shared capability
└───────────────▲──────────────────────────────────────────────┘
                │  Kitsu nouns in/out (no gazu above this line)
┌───────────────┴──────────────────────────────────────────────┐
│  square_core/kitsu/    the ONE package that imports gazu      │
│    api.py     reads/writes, version tracking, upload          │
│    auth.py    JWT cache / keyring (non-interactive)           │
│    offline.py no-op stand-in (work-to-NAS without Kitsu)      │
└──────────────────────────────────────────────────────────────┘
```

**The rule:** dependencies point downward only.

- `tools/*` import `square_core` **public API only** (services, model). Never
  `gazu`, never `square_core.kitsu`, never raw Kitsu wire fields.
- `square_core.services` call `square_core.kitsu`; `square_core.model` imports
  nothing. Neither imports `gazu`.
- Only `square_core/kitsu/` imports `gazu`.

**No neutral "backend protocol".** Square is not switching production trackers
in the foreseeable future, so `square_core/kitsu/` speaks Kitsu's own nouns
(task, shot, preview, comment, output-type) and returns light dataclasses or
plain dicts — no invented vocabulary, no two-way mapper, no `InMemoryBackend`.
The value it buys is **one place that knows how we write to Kitsu** (the
`KitsuRecorder` lesson — scattered writes drifted) and **one surface to fix on a
gazu/Zou upgrade**. If a swap ever happens, it's one package to rewrite; that
insurance is close to free. Tests use mocked `square_core.kitsu` for unit work
and a throwaway `ZZ *` project on a real Kitsu for integration.

**Why the layering still matters:** a tool asks for an *outcome* ("create this
project", "publish this output"). Core guarantees the *entire* outcome — Kitsu
records **and** folders **and** config **and** the follow-on writes — so no tool
can create half a project.

---

## 3. What "shared" means — the anti-speculation rule

`square_core` is not a dumping ground for "might be reusable". A capability earns
its place there only when:

1. a **second** consumer actually calls it, **or**
2. it is plainly foundational — project/shot identity, path resolution, the copy
   engine, the single Kitsu access point.

Until then it lives in the tool that needs it. Concretely: most of today's
ingest domain (`ingest_item`, `ingest_controller`, `preflight`, `folder_mapper`,
`ingest_session`) moves *out* of `square_core` into `tools/ingest_tool/core/`
(that is `restructure_plan.md`), and only the thin "commit a resolved delivery"
slice is promoted to `square_core/services/ingest.py` **when vendor-QC (tool #8)
actually needs it**.

This is the same discipline `decisions.md` already states for `PreviewMetadata`
and the ledger: build for one, promote on the second.

---

## 4. Model (`square_core/model/`)

Light dataclasses. No I/O, no gazu, no framework. `square_core/kitsu/` returns
these (or plain dicts for the throwaway cases); services and tools pass them
around. They're a convenience for typed field access in UI code, not a
mandatory abstraction — don't build one where a dict is clearer.

### Entities

| Entity | Key fields | Notes |
|---|---|---|
| `User` | id, name, email, role | current-user handle |
| `Project` | id, code, name, status, production_type, fps, resolution, ratio, root_path | `production_type` `short`/`tvshow`/… |
| `Episode` | id, code, project_ref | Zou-native; only present when `production_type="tvshow"` |
| `Sequence` | id, code, project_ref, episode_ref? | `episode_ref` set only on episodic shows |
| `Shot` | id, code, sequence_ref, frame_in, frame_out, nb_frames, status, data | `data` = namespaced blob (see §6) |
| `Asset` | id, code, name, asset_type, project_ref, status | CG char/prop/env — Phase B, parallel to Shot |
| `TaskType` | id, name, short_name, department, for_entity | "Comp", "Anim", "Ingest" |
| `TaskStatus` | id, name, short_name, is_done, is_retake, is_wip, color | from the project template |
| `Task` | id, entity_ref, task_type_ref, status_ref, assignees, priority, due | the unit of work |
| `Workfile` | entity_ref, task_ref, software, version, path, author, comment, created_at | a working scene |
| `Output` | entity_ref, task_ref, output_type, version, representations, source_workfile, path | a published result |
| `PreviewMedia` | task_ref, revision, path, kind (`video`/`image`), provenance | review media |
| `Comment` | task_ref, text, author, status_change, attachments, annotations, created_at | feedback / notes |
| `Delivery` | project_ref, client, version, date, package_path, items, status | outbound |

### Value objects

| VO | Purpose |
|---|---|
| `EntityRef(type, id, code?)` | lightweight handle passed around instead of full entities |
| `Version(number, minor=0)` | `v003`, `v003.02`; formatting + compare live here |
| `MediaInfo` | resolution, fps, colorspace, frame_range, missing_frames, timecode — *(exists as metadata_extractor output)* |
| `Provenance` | source path/file, dest path/file, checksum(+algo), transfer_mode, who, when, batch_id — generalization of today's `PreviewMetadata` |
| `PathContext` | the field bag every path template consumes (project code, seq, shot, task, type, name, version, ext, frame, resolution, site) |

Operation results are their own small dataclasses (`ProjectCreated`,
`IngestResult`, `PublishResult`, `DeliveryResult`) — what was created/changed,
for the tool to display and for the session to record.

---

## 5. `square_core/kitsu/` — the one Kitsu access point

Not a protocol. One package, the only importer of `gazu`, that every service
calls for anything touching the production DB. It speaks Kitsu's nouns and wraps
`gazu` where `gazu` is awkward, verbose, or version-fragile. Absorbs today's
`kitsu_gateway.py` *(exists)*, `kitsu_recorder.py` *(exists)* and the dead
`kitsu_client.py`.

Rough surface (grows as services need it — not built up front):

```
kitsu/api.py
  # identity  (auth.py owns the JWT cache / keyring; see §9, §13)
  attach(host, session) / current_user()
  # entities
  projects(status=) / project(ref)
  create_project(spec)                                   new_project + apply_project_template
                                                         + set_minimal_file_tree
  sequences(project) / ensure_sequence(project, code)
  shots(project, sequence=) / ensure_shot(sequence, code, **f)
  ensure_asset(project, name, asset_type, **f)
  entity_data(ref) / merge_entity_data(ref, data)        the namespaced data blob
  # tasks
  task_types(for_entity=) / task_statuses()
  tasks(entity=, assignee=, project=) / ensure_task(entity, task_type)
  set_status(task, name, *, comment=None, author=None)   name/short_name match, verified live
  assign(task, users)
  # review
  comment(task, text, *, status=None, attachments=None)
  upload_preview(task, file, *, comment=None, status=None) / set_main_preview(entity, preview)
  preview_data(preview) / merge_preview_data(preview, data)   nests under data["square"] (Zou drops top-level)
  annotations(preview) / update_annotations(preview, additions=, updates=, deletions=)
  # versions — Kitsu is the version store; paths are OURS (see §5.1)
  next_working_revision(task, name="main") -> int
  next_output_revision(entity, output_type, task_type, name="main") -> int
  working_files(task) / last_working_files(task)
  output_files(entity, *, output_type=None) / last_output_file(entity, output_type, task_type)
  record_working_file(task, *, revision, path, software=None, data=None)   # create + PUT our path
  record_output_file(entity, output_type, task_type, *, revision, path, representation="", data=None)
  output_types() / ensure_output_type(name, short_name)
  set_minimal_file_tree(project)   # once, at create — satisfies Zou; output never used
```

`kitsu/offline.py` — the no-op stand-in (from `NullKitsuGateway` *(exists)*) so
"tag + resolve + copy to the NAS without Kitsu" keeps working. Returns minimal
shapes; nothing hits a server.

The ingest tool's `ingest_ledger.db` *(exists)* stays a **temporary tool-local
exception** in `tools/ingest_tool/core/` — its "have these exact bytes gone in"
check predates this design; it folds into `kitsu.api` output queries later.

### 5.1 Paths are ours; Kitsu tracks versions (verified 2026-09-02)

**`square_core/paths/PathResolver` computes every real path** — workfile,
output, render, ingest-landing, delivery — from `ProjectConfig` templates. Full
casing control, per-client delivery conventions, everything.

**Kitsu's `working_files` / `output_files` are the version store.** Verified on
localhost Zou 1.0.58 / gazu 1.2.1:

- `get_next_entity_output_revision(entity, output_type, task_type)` — the next
  version number. Works **without** a file_tree. Same idea for working files via
  `get_working_files_for_task` + max revision.
- `new_working_file` / `new_entity_output_file` **require the project to have a
  file_tree** (they 500 / `"No tree can be found"` without one) and compute a Zou
  path server-side...
- ...but that path is then **overwritable**: `PUT data/working-files/<id>`
  `{path: ...}` and `gazu.files.update_output_file(id, {path, data})` both stick.
  Verified: a record came back with `X:/Show/SQ_010/SH0100/comp/nuke/
  Show_SQ_010_SH0100_comp_v001.nk` — **mixed case preserved verbatim**. The
  `data` blob on the file record is writable too (provenance goes there).
- `get_working_files_for_task`, `all_output_files_for_entity(entity,
  output_type=)`, `get_last_working_files` — "all versions" / "latest" queries,
  return our overridden paths.

**So on save / publish, `square_core/kitsu` does:** `next_*_revision` → create
the record (Kitsu assigns the revision) → immediately `PUT` our resolver's path
+ provenance `data`. `projects.create` sets **one minimal file_tree** purely so
the create calls don't reject; its computed output is never used.

The **Zou file_tree case limit is now moot** — we only need it to exist, not to
be right. (For the record: `file_tree` `style` is `"uppercase"` or
`"lowercase"` per `folder_path` / `file_name` dict, slugified, uniform, no
mixed case, no per-token control. That's why we don't use its output.)

`<Version>` / `<Revision>` fixed 3-digit zero-pad — our resolver matches that.

**Path ownership:**

| Path kind | Built by |
|---|---|
| Workfile / output / render paths | **`square_core/paths/PathResolver`** from `ProjectConfig`; written into the Kitsu file record |
| Version numbers (next / latest / all) | **Kitsu** `working_files` / `output_files` revisions |
| Media type on an ingest / output | **Kitsu output type** — `ensure_output_type("Plate")` |
| Ingest delivery landing paths | `PathResolver` (ingest tool's current resolver folds in here) |
| Shot folder skeleton (the 2D/3D tree) | `storage/layout.py` — Zou never creates folders |
| Client delivery paths / packages | `services/delivery` — Zou/gazu OSS has no delivery API (verified) |
| Colorspace pipeline | `ProjectConfig` |

### 5.2 What Kitsu gives us natively (verified — don't rebuild)

- **Project templates** (`gazu.project_template`, `apply_project_template`) — a
  template bundles task types, task statuses, asset types, **status
  automations**, preview backgrounds. `projects.create` applies one instead of
  seeding each piece.
- **Status automations** are a native Zou feature, carried on the template
  (`add_status_automation_to_project_template`). This is why §7 has **no custom
  automation engine** — it's configured in Kitsu.
- **`production_type`** on a project: `short` / `tvshow` / `feature` /
  `commercial`. `tvshow` is how episodic shows get episodes — the `Episode`
  entity is Zou-native, not something we model separately.
- **Playlists** (`gazu.playlist`) — Zou's review-session grouping; the review
  player (#5) and dailies build on these rather than a home-grown list.
- **`gazu.edit`** — the edit/EDL entity, for `breakdown.import_edl`.

---

## 6. Where truth lives

**One production store: Kitsu.** Reached only through `square_core/kitsu/`. No
parallel pipeline database.

| Fact | Store |
|---|---|
| Tasks, statuses, assignments, schedule, review notes, annotations | Kitsu |
| Project / episode / sequence / shot / asset, frame ranges | Kitsu |
| Workfiles / outputs / **version numbers** | Kitsu — `working_files` / `output_files` revisions + `preview_files` |
| On-disk **paths** (workfile, output, render, ingest, delivery) | **`PathResolver`** (`ProjectConfig` templates); the value is also written into the Kitsu file record + its `data` blob |
| Media type | Kitsu **output type** |
| Task types / statuses / status automation | Kitsu — via a **project template** (§5.2) |
| Naming templates, folder skeleton, ingest media-type map, client presets, colorspace | **ProjectConfig** — `{project_root}/_pipeline/project_config.json` |
| Kitsu host, NAS roots, studio defaults | **StudioConfig** — per install (creds → OS keyring, §13) |

`ProjectConfig` holds the naming/path templates the `PathResolver` consumes, the
folder-skeleton list, per-media-type ingest landing paths, client delivery
presets, and the colorspace convention. It's *configuration*, not production
data. Kept as a NAS file so `storage/layout`, the resolver, and the ingest tool
can read it without a Kitsu round trip. The resolved paths *are* pushed into the
Kitsu file records, so Kitsu still shows correct paths — but `ProjectConfig` is
where they're computed from, single-authored.

`{project_root}/_pipeline/` holds `project_config.json`, the ingest tool's
temporary `ingest_ledger.db`, and later `locks/`. It is not a database of record.

---

## 7. Core services (`square_core/services/`) — the tool-facing API

Each service is a module of use-case functions/classes. They take a
`ProjectContext` (§9), model objects, and orchestrate `kitsu` + `storage` +
`media`. They emit `PipelineEvent`s (same pattern as today's `ControllerEvent`)
so tools can react without polling.

### `projects` — setup & teardown
```
plan(spec) -> Project                # Kitsu project in "planning" status, no storage yet
create(spec) -> ProjectCreated       # kitsu.create_project (new_project + apply_project_template
                                     #   + minimal file_tree) + storage.layout.create_project_tree
                                     #   + write project_config.json
archive(project) -> ArchiveResult    # status → closed/archived, freeze writes, optional cold move
```
`ProjectSpec` is thin — code, name, `production_type`, project-template choice,
NAS root (picked from `StudioConfig`), client, and any per-project overrides.
`create` copies the studio-wide defaults (folder skeleton, path templates,
fps/res) out of `StudioConfig` into the new `project_config.json`, so a later
change to a studio default never moves an existing project's paths.

### `breakdown` — shot list, assets, roadmap
```
create_sequence / create_shot / create_asset
import_edl(project, edl_path) -> [Shot]        # shot list + cut ranges from an edit
set_shot_range(shot, frame_in, frame_out)
build_task_grid(entities, task_template) -> [Task]   # "roadmap": tasks per entity per template
assign(task, users)
```

### `media` — one call for ingest, render, workfile, cache … (`config_and_paths.md` v2)
```
publish(pctx, entity, media_type, task, *, files, name="main", version=None,
        source=None, media_info=None, inputs=(), dry_run=False) -> MediaResult
    # resolve path (paths.media_path(media_type, ctx))
    # → transfer + verify (storage.transfer) -- skipped for files already in place
    # → kitsu.record_media: output_file or working_file per media_type.kitsu_kind,
    #     with our path + Provenance in data["square"]; source_file_id + inputs
    # → if media_type.previewable: deferred media.proxy + kitsu.upload_preview
next_version(pctx, entity, media_type, task) -> int
list_versions(pctx, entity, media_type) / latest(pctx, entity, media_type)
```
Ingest calls `media.publish(..., media_type=<Plate|Ref|…>, source=<delivery files>)`;
Nuke calls `media.publish(..., media_type="CompRender", inputs=[nk_workfile])`.
`work.save_workfile` / `work.publish_output` become thin wrappers.

### `ingest` — client material in (mostly stays in `tools/ingest_tool/core/`)
```
# thin shared slice, promoted only when tool #8 needs it:
commit(pctx, resolved_items, *, dry_run) -> IngestResult
    # per item: ensure shot (breakdown) → media.publish(pctx, shot, item.media_type,
    #           ingest_task, files=item.source_files, source=item.source_files)
    # local ingest_ledger dup-check stays inside the ingest tool for now
```

### `review` — supervisor loop
```
submit(task, preview, *, comment) -> Comment          # kitsu.upload_preview, status → "pending review"
record_note(task, *, text, status, annotations=None)  # kitsu.comment + kitsu.update_annotations
approve(task) / request_changes(task)                 # kitsu.set_status
get_thread(task) -> [Comment]
```

Annotations are pushed to Kitsu **on submit**, not live — verified writable via
`gazu.files.update_preview_annotations(preview, additions, updates, deletions)`,
so they also render in Kitsu's own web player.

**No custom automation engine.** `approve()` just sets the task status.
Downstream effects — shot status rollups, unlocking the next department's task,
retake propagation — are **Kitsu's** native status automation (§5.2), configured
in Kitsu. If Square ever needs a rule Kitsu can't express, it gets added here
then; nothing is built for it now.

### `delivery` — client out
```
build_package(project_ctx, targets, client_preset) -> DeliverablePackage
    # client_preset from ProjectConfig (naming, padding, colorspace, container)
    # collect outputs → transcode to client spec (media.proxy) → QC suite
    #   → transmittal manifest (CSV/PDF) → staged package folder
send(package) -> DeliveryResult                        # upload/handoff + kitsu.comment log
                                                       #   + shot status
```
All of `delivery` is ours — Zou/gazu OSS has no delivery-path or package API
(verified). Paths, presets and the manifest live in `services/delivery` +
`ProjectConfig`; Kitsu gets a comment/status trail for visibility.

### `context` — browse / reverse-lookup / "my work"
```
my_tasks(user) -> [Task]
browse(project, ...) -> tree for tool pickers
entity_for_path(path) -> EntityRef | None              # "what shot/task is this file"
current()  -> ProjectContext | None                    # from cwd / open file / last used
```

---

## 8. Shared capability packages

### `square_core/paths/`
Computes **every** on-disk path (§5.1); Kitsu only assigns version numbers.
**Full spec: [`config_and_paths.md`](config_and_paths.md).**
- `resolver.py` *(built)* — `PathResolver(config)` + `PathContext`. `{token}`
  templates from `ProjectConfig`, case-preserving + slugified, strict on missing
  required tokens, `validate()`. **v2:** ingest + render-output collapse into one
  **`media_types` registry**; `media_path/media_dir/media_file/media_sequence(
  media_type, ctx)` replace the separate `output_*` / `ingest_dest_*` methods.
- `path_pattern.py` / `token_parser.py` *(built)* — build-by-example matcher for
  messy *incoming* delivery folders (ingest); unrelated to the resolver.

### `square_core/media/`
- `scanner.py` — image-sequence + video discovery, frame-range parsing. From
  `plate_scanner.py` *(exists)*; split the scan primitive from the
  ingest-flavoured item it returns.
- `metadata.py` — OIIO / ffprobe header reads → `MediaInfo`. From
  `metadata_extractor.py` *(exists)*.
- `proxy.py` — ffmpeg proxy, slate, burn-in (frame/timecode). From
  `proxy_generator.py` *(exists)*; used by ingest previews, task previews,
  dailies, delivery transcodes.

### `square_core/storage/`
- `transfer.py` — the verified copy engine: `copy_and_hash`, native
  `CopyFileExW`, `copy_sequence`, hash-on-write verify, hardlink/symlink modes.
  Carved out of `nas_manager.py` *(exists)*. Used by ingest, publish, localize,
  vendor-package, delivery.
- `layout.py` — `create_project_tree(root, structure)`,
  `ensure_shot_tree(shot_dir, structure)`.

(No `pipelinedb` — see §6. `ingest_ledger.py` *(exists)* stays tool-local in
`tools/ingest_tool/core/`.)

### `square_core/config/`
Schemas: [`config_and_paths.md`](config_and_paths.md) §4–§5;
key registry + editor: [`config_schema.md`](config_schema.md).
- `pipeline.py` *(built)* — `PipelineConfig`, per install: Kitsu host, named NAS
  roots, Kitsu project-template list, and `project_defaults` — a whole
  `ProjectConfig` minus the per-show values.
- `project.py` *(built)* — `ProjectConfig`, one per project on the NAS
  (`_pipeline/project_config.json`): `roots`, the `media_types` registry,
  `delivery_presets`, folder-structure lists, colorspace, `tools.*` settings.
  Written by `projects.create`. Tools read it **live**, never snapshot it.
- `schema.py` *(built)* — the `ConfigKey` registry; `register()` for tools
  (idempotent / conflict-checked); `resolve()` (project → studio default →
  built-in); `validate()` → `(errors, warnings)`, called by
  `ProjectConfig.check()` / `PipelineConfig.check()` on top of
  `PathResolver.validate()`. The admin **config editor** tool is the only writer.
- `conventions.py` *(built)* — the default shot folder-structure list.

`ProjectConfig.load()` runs `check()` and refuses a broken config.

### `square_core/hashing.py` *(built)* — hash-once cache, shared by transfer / verify.

---

## 9. Composition root

```python
# square_core/context.py
@dataclass
class PipelineContext:
    studio: StudioConfig
    kitsu: KitsuApi          # or kitsu.offline.OfflineApi
    storage: StorageService
    user: User

    @classmethod
    def connect(cls, *, offline: bool = False) -> "PipelineContext":
        studio = StudioConfig.load()
        if offline:
            api = kitsu.offline.OfflineApi()
        else:
            sess = kitsu.auth.cached_session()          # keyring / ~/.square
            if sess is None:
                raise NeedsLogin(studio.kitsu_host)     # tool catches, prompts, retries
            api = kitsu.api.attach(studio.kitsu_host, sess)
        return cls(studio, api, StorageService(studio), api.current_user())

    def project(self, ref: str) -> "ProjectContext":
        project = self.kitsu.project(ref)
        cfg = ProjectConfig.load(project.root_path)
        return ProjectContext(self, project, cfg, PathResolver(cfg))
```

A tool's whole wiring:

```python
try:
    ctx = PipelineContext.connect()
except NeedsLogin as e:
    kitsu.auth.login(*prompt_credentials(e.host))       # shared dialog / stdin
    ctx = PipelineContext.connect()
pctx = ctx.project("ABC")
result = work.publish_output(pctx, task=my_task, source_workfile=wf,
                             output_type="comp", files=frames, media_info=info)
```

No tool ever imports `gazu` or calls `square_core.kitsu` directly — only services do.

---

## 10. Target package tree

```
square_core/
  __init__.py
  context.py            PipelineContext / ProjectContext
  errors.py
  hashing.py                                    (exists)
  model/
    entities.py  refs.py  media.py  provenance.py  results.py
  kitsu/
    api.py             the one gazu importer     (from kitsu_gateway + kitsu_recorder)
    offline.py         no-op stand-in            (from NullKitsuGateway)
    auth.py            JWT cache / keyring (non-interactive)
  services/
    projects.py  breakdown.py  work.py  review.py  delivery.py  context.py
    ingest.py          (thin slice only; added when tool #8 needs it)
  paths/
    resolver.py  templates.py  conventions.py
    path_pattern.py                              (from square_core/, exists)
  media/
    scanner.py          (from plate_scanner.py)
    metadata.py         (from metadata_extractor.py)
    proxy.py            (from proxy_generator.py)
  storage/
    transfer.py         (from nas_manager.py copy engine)
    layout.py           create project / shot folder trees
  config/
    studio.py  project.py  loader.py

tools/
  _shared/
    qt_compat.py                                (from tools/qt_compat.py)
    pipeline_ui/       shared pickers (project/shot/task tree), login, event->Qt bridge
  ingest_tool/
    core/              item.py controller.py preflight.py folder_mapper.py
                       session.py token_parser.py config.py preview_metadata.py
                       ledger.py    (temporary tool-local)
    widgets/  controller_bridge.py  ui_main.py  main.py
                       # dest/slot logic → square_core/paths; recorder → square_core/kitsu
  project_setup/       tool #2
  dcc/                 nuke/  maya/  houdini/  common/     tools #3–4
  review_player/       tool #5
  localize/            tool #6
  vendor_package/      tool #7
  vendor_qc/           tool #8
  deliver/             tool #9
  pipeline_deploy/     deploy.py  rollback_cli.py          (from root + tools/)

tests/
  core/   kitsu/  services/  paths/  media/  storage/
  ingest_tool/  project_setup/  …
  conftest.py
```

---

## 11. Tools inventory

| # | Tool | Lifecycle stage | Core services it drives | Priority |
|---|---|---|---|---|
| 1 | **Ingest tool** *(built)* | client material → shots | `media.publish`, `breakdown`, `storage.transfer` | refactor onto core API |
| 2 | **Project setup / admin** | project created · breakdown · roadmap | `projects.create/archive`, `breakdown.*` | smallest tool that exercises the spine |
| 2b | **Config editor** (admin-only) *(built)* | studio + project config | `config.schema` / `ConfigStore` — the **only** writer of config | done 2026-09-04 (GUI + `--cli`, `config_schema.md`); kills ingest's Settings dialog |
| 3 | **DCC integration** (Nuke first: `SquareRead`/`SquareWrite` + publish panel) | workfile · output · task preview | `work.*`, `review.submit`, `media.proxy` | high |
| 4 | **Workfile / version manager** (DCC-agnostic core, Maya/Houdini hooks) | task started · save · publish | `work.save_workfile`, `work.next_version`, `work.publish_output` | high (shares core with #3) |
| 5 | **Review player** (desktop) | supervisor review · annotate · approve | `review.*` (annotations + status) | high |
| 6 | **Localize tool** | artist performance (NAS ↔ local cache) | `storage.transfer`, `paths`, `context.entity_for_path` | medium |
| 7 | **Vendor package builder** | plates/refs/cameras → freelancer | `delivery.build_package` (internal preset), `breakdown.assign` | medium |
| 8 | **Vendor ingest QC** | freelancer work back in | `ingest` + QC rules (padding, colorspace, res) | medium — triggers promoting `services/ingest.py` |
| 9 | **Send-to-client / delivery** | client send · final delivery · tracking | `delivery.*`, `media.proxy`, QC, manifest | high (closes the loop) |
| 10 | **Pipeline deploy** *(exists)* | infra, not pipeline-core | — (stays `tools/pipeline_deploy/`) | maintenance |
| — | **"My tasks" dashboard / launcher** (tray or web) | task started · status glance | `context.my_tasks`, `work.open`, `review.submit` | glue, later |

---

## 12. Build order

**Phase A — the spine** (core only, then prove it through the ingest tool).
The `restructure_plan.md` moves are the first commits here, not a separate
branch — the restructure and the spine touch the same files.

0. **Restructure** (`restructure_plan.md`): carve `transfer.py` out of
   `nas_manager`, rename the media/paths packages, move the ingest domain into
   `tools/ingest_tool/core/`. No new capability.
1. `square_core/kitsu/` — `api.py` (fold in `kitsu_gateway` + `kitsu_recorder`),
   `offline.py`, `auth.py` (non-interactive: `login()` / `cached_session()`;
   JWT cache + keyring). Add version-tracking
   (`working_files`/`output_files` + path override), output-type,
   project-template, minimal-file_tree calls.
2. `context.py` — `PipelineContext` / `ProjectContext`; `connect()` raises
   `NeedsLogin` for the tool to handle.
3. `config/` split — `StudioConfig` (host, NAS roots, studio-wide defaults) vs
   `ProjectConfig`; `projects.create` copies the relevant defaults in, applies
   a Kitsu project template, sets a minimal file_tree, writes
   `project_config.json`.
4. `paths/resolver.py` — the `media_types` registry + `media_path()` for every
   path kind, from `ProjectConfig` (`config_and_paths.md` v2).
5. `storage/transfer.py` + `storage/layout.py`; `services/media.publish`.
6. Port the ingest tool onto `PipelineContext` + `square_core.kitsu`. This is
   the acceptance test — if ingest gets awkward, the API is wrong. The ingest
   ledger stays where it is; don't migrate it in this phase. Drop the session's
   `config_snapshot` — the tool reads live `ProjectConfig` on resume.

**Phase B — config editor (#2b) *(done 2026-09-04)* + project setup tool (#2)**
`config/schema.py` (the `ConfigKey` registry, `validate()`, `resolve()`) and the
admin **config editor** (`tools/config_editor/`, GUI + `--cli`,
`config_schema.md`) — the only writer of studio / project config — landed first.
Still to do: the project-setup tool (#2) — a tiny GUI over `services/projects` +
`services/breakdown` + task templates that spins up a real show end to end. Then
the ingest port (Phase A step 6).

**Phase C — work / publish + DCC tool (#3/#4)**
`services/work`, path resolution in anger, first managed workfile + first
published output + first task preview. Nuke integration as the first surface.

**Phase D — review + player (#5)**
`services/review`, annotations round-trip, status changes on approve/retake.
Downstream advancement is Kitsu's own status automation — nothing to build here.

**Phase E — delivery + send-to-client (#9)**
`services/delivery`, client presets, QC suite, transmittal manifest. Archival
(`projects.archive`) rides along.

Each phase: **core services first**, unit-tested with `square_core.kitsu` mocked,
then the thinnest tool that proves them, then a live pass against a throwaway
`ZZ *` project on the real Kitsu.

---

## 13. Decisions & open questions

### Resolved 2026-09-02 (also in `decisions.md`)

- **One production store: Kitsu**, reached only through `square_core/kitsu/`. No
  parallel pipeline DB.
- **No neutral backend protocol / no `InMemoryBackend`.** `square_core/kitsu/`
  speaks Kitsu nouns; the "swap someday" insurance is that it's one package.
- **Paths are `square_core/paths/PathResolver`'s job** (from `ProjectConfig`),
  **version numbers are Kitsu's** (`working_files` / `output_files` revisions).
  On save/publish: create the Kitsu file record, then `PUT` our resolved path +
  provenance `data` onto it (verified override works, mixed case preserved). A
  minimal `file_tree` is set once at `projects.create` only so the create calls
  don't reject — its output is never used. **Media type = Kitsu output type.**
- **`projects.create` applies a Kitsu project template** for task types,
  statuses and status automations (§5.2).
- **The ingest `ingest_ledger.db` is a temporary tool-local exception.**
- **`Episode` is Zou-native** (`production_type="tvshow"`); model references it,
  doesn't reinvent it.
- **CG `Asset` is a parallel entity added in Phase B**; asset tasks reuse
  `work` / `review`.
- **No custom automation** — Kitsu status automation, configured in Kitsu.
- **`ProjectConfig` is a NAS JSON** the pipeline core owns. Tools **read it
  live, never snapshot it** — the ingest session's `config_snapshot` is dropped
  in the port. `StudioConfig` holds the studio-wide defaults; `projects.create`
  copies the relevant ones into a new project's `ProjectConfig` so studio-default
  changes never move an existing project.
- **`ProjectSpec` is thin** — code, name, `production_type`, template choice,
  NAS root (from `StudioConfig`), client, per-project overrides.
- **Auth: per-user login, JWT + refresh cached** (OS keyring or
  `~/.square/session.json` 600), shared by every tool + DCC. `kitsu/auth.py` is
  non-interactive; `connect()` raises `NeedsLogin`, the tool prompts once (shared
  dialog / stdin) and retries. Web sessions don't carry over; SSO only if Zou
  SSO is turned on. Farm nodes use a service token.
- **`restructure_plan.md` folds into Phase A** as its first commits, not a
  separate branch.
- **`PathContext` reserves a `site` field**; multi-site resolution implemented later.
- **`square_core.model` / `.services` stay importable on Python 3.9–3.11** with
  no hard OIIO/ffmpeg/gazu import on that path (those are `kitsu`/`media` only).

- **One media-type registry (2026-09-04).** Ingest and render-output are the
  same operation — a versioned set of files of a configured type on an entity.
  `config_and_paths.md` v2: one `media_types` table (studio-named, project-
  overridable), one `PathResolver.media_path(media_type, ctx)`, one
  `services.media.publish(...)` that ingest and Nuke both call. `media_type` →
  Kitsu `output_type` (or `working_file` per `kitsu_kind`). "A version with
  multiple files" = Kitsu's `(entity, output_type, name, revision)` grouping +
  `representation`; dependencies via `source_file_id` + `data["square"]["inputs"]`.
  Delivery stays separate.
- **Config is schema-described and admin-edited only (`config_schema.md`).** A
  `ConfigKey` registry (not JSON Schema); tools `register()` their `tools.<t>.*`
  keys; one admin **config editor** is the only writer; every other tool is
  read-only.

### Still open

1. **Annotation read shape** — deferred to the review player (Phase D). Write is
   confirmed (`update_preview_annotations`); spike the drawing-object schema then.
2. **Config schema details** (`config_schema.md` §7) — v1→v2 migration hook
   location; whether a `working_file` path `PUT` works like `output_file`'s
   (verified) against live Zou; the Kitsu admin-role gate for the editor.

*(Resolved 2026-09-02: `ProjectConfig` + `PathResolver` — `{token}` templates,
case-preserving, pure resolver, Kitsu `data` key `"square"`, we `PUT` our path
onto the Kitsu file record. `projects.create` sets a throwaway minimal
file_tree.)*
