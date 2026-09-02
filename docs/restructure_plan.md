# Project restructure plan

**Status:** proposed — not executed. These moves are the **first commits of
Phase A** in [`pipeline_architecture.md`](pipeline_architecture.md) (§12), not a
separate branch — the restructure and the spine touch the same files.

It clears `square_core` to genuinely-shared code so the pipeline core
(`square_core/kitsu/`, `PipelineContext`, services, `paths`/`storage`/`media`
packages) can be built on the shape the architecture doc assumes. **The target
tree in `pipeline_architecture.md` §10 supersedes the one below** where they
differ — notably: one `square_core/kitsu/` package (no `backend/` protocol, no
`InMemoryBackend`); `paths/` owns all path resolution; no `storage/pipelinedb`.

## The principle

`square_core/` is **pure, shared, framework-free**. It holds only things a
*second* tool (Nuke integration, review player, delivery tool, …) would
genuinely reuse. No ingest-specific domain logic, no Qt.

Everything a tool owns — its state model, its orchestration, its UI-facing
config — lives **under that tool's own folder**. *How* to write a version to
Kitsu is shared (`square_core/kitsu/`), because publish and review need the same
thing; only tool-specific *policy* (which task, what comment wording) is thin
and stays in the tool.

Right now `square_core/` has ~19 modules and roughly half of them are the
ingest tool's private business.

## Where each module goes

### Stays in `square_core/` (genuinely shared)

| Module | Notes |
|---|---|
| `hashing.py` | pure util |
| `metadata_extractor.py` → `media/metadata.py` | header reads (OIIO / ffprobe) — every media tool needs this |
| `proxy_generator.py` → `media/proxy.py` | ffmpeg proxy + slate — review, delivery, dailies all want it |
| `plate_scanner.py` → `media/scanner.py` | image-sequence + video discovery, frame parsing. Split the scan primitive from the ingest-flavoured `IngestSequenceItem` it returns |
| `path_pattern.py` → `paths/path_pattern.py` | generic build-by-example path matching engine |
| `kitsu_gateway.py` **+ `kitsu_recorder.py`** → `kitsu/api.py` | the one gazu importer: CRUD, upload, status, **version tracking** (`working_files`/`output_files` + path override), project template, minimal file_tree. `kitsu_recorder`'s "record a version" policy is shared now (publish + review need it), not ingest-only |
| `nas_manager.py` copy engine → `storage/transfer.py` | `_copy_and_hash`, `_transfer_one_file`, `copy_sequence`, verify — every tool that moves files reuses this |
| `nas_manager.py` dest/slot bits → `paths/resolver.py` | `get_dest_dir`, `dest_names`, `inspect_slot`, `next_free_version` — `PathResolver` owns **all** path kinds now, not just ingest |
| `config.py` → `config/studio.py` + `config/project.py` | `StudioConfig` = install (Kitsu host, NAS roots, creds→keyring, studio-wide **defaults**); `ProjectConfig` = per-project NAS JSON (resolved templates, skeleton, ingest media-type paths, client presets, colorspace) written by `projects.create` |

### Moves to `tools/ingest_tool/core/` (ingest domain — pure Python, no Qt)

| From | To |
|---|---|
| `ingest_item.py` | `core/item.py` — `IngestItem`, `Status`/`Issue`/`Action` |
| `ingest_controller.py` | `core/controller.py` — reworked to take a `ProjectContext` + call `square_core.kitsu` (drops its own `KitsuRecorder`/gateway wiring) |
| `ingest_session.py` | `core/session.py` — **drop `config_snapshot`**; read live `ProjectConfig` on resume |
| `ingest_ledger.py` | `core/ledger.py` — temporary tool-local exception, not generalized |
| `preflight.py` | `core/preflight.py` |
| `preview_metadata.py` | `core/preview_metadata.py` — stays in the tool for Phase A; promote to `square_core/kitsu` when publish (Phase C) becomes the second writer |
| `folder_mapper.py` | `core/folder_mapper.py` |
| `token_parser.py` | `core/token_parser.py` (only the path-pattern builder uses it) |
| ingest-only bits of `config.py` | mostly become **`ProjectConfig`** (shared); only a thin ingest-run section (copy workers, transfer mode default, preview-enabled types) stays as `core/config.py` |

`kitsu_recorder.py` does **not** move here — it folds into `square_core/kitsu/api.py` (see the table above). `nas_manager.py`'s dest/slot logic folds into `square_core/paths/resolver.py`.

### Deleted

| Module | Why |
|---|---|
| `square_core/kitsu_client.py` | Dead since the rework. Any still-useful generic helper (`get_or_create_sequence/shot`) folds into `kitsu/api.py`; delete the rest and `tests/test_kitsu_client.py`. |

### `tools/` reorg

| From | To |
|---|---|
| `tools/qt_compat.py` | `tools/_shared/qt_compat.py` — shared by every future Qt tool |
| `tools/rollback_cli.py` | `tools/pipeline_deploy/rollback_cli.py` — it's the deployment version-switcher, nothing to do with ingest |
| `deploy_studio_pipeline.py` (root) | `tools/pipeline_deploy/deploy.py` |

## Target tree

Authoritative version is `pipeline_architecture.md` §10. Summary of the moves:

```
square_core/
  hashing.py                                   (unchanged)
  kitsu/    api.py        <- kitsu_gateway.py + kitsu_recorder.py + kitsu_client survivors
            offline.py    <- NullKitsuGateway
            auth.py       (new — JWT cache / keyring)
  media/    scanner.py    <- plate_scanner.py
            metadata.py   <- metadata_extractor.py
            proxy.py      <- proxy_generator.py
  paths/    resolver.py   (new — all path kinds; + nas_manager dest/slot bits)
            templates.py  <- config.py format helpers
            conventions.py
            path_pattern.py <- path_pattern.py
  storage/  transfer.py   <- nas_manager.py copy engine
            layout.py     (new)
  config/   studio.py  project.py  loader.py   <- config.py split
  model/  services/  context.py                (new — Phase A proper)

tools/
  _shared/  qt_compat.py <- tools/qt_compat.py
  ingest_tool/
    core/   item.py controller.py session.py preflight.py folder_mapper.py
            ledger.py preview_metadata.py token_parser.py config.py
            <- square_core/ingest_*.py, preflight.py, etc.
    widgets/  controller_bridge.py  ui_main.py  main.py
  pipeline_deploy/  deploy.py <- deploy_studio_pipeline.py
                    rollback_cli.py <- tools/rollback_cli.py

tests/  kitsu/ media/ paths/ storage/ services/ core/  ingest_tool/  conftest.py
```

## Migration order (each step green before the next; = the first commits of Phase A)

1. **Carve the copy engine** out of `nas_manager.py` → `square_core/storage/transfer.py`;
   dest/slot bits → `square_core/paths/resolver.py` (skeleton). `nas_manager.py` goes away.
2. **Rename shared media/paths modules** → `square_core/media/`, `square_core/paths/`.
   Import-only churn.
3. **`square_core/kitsu/`** — fold `kitsu_gateway.py` + `kitsu_recorder.py` in as
   `api.py`; `NullKitsuGateway` → `offline.py`; delete `kitsu_client.py` +
   `tests/test_kitsu_client.py`. Add `auth.py`.
4. **Split `config.py`** → `config/studio.py` + `config/project.py`.
5. **Move the ingest modules** `square_core/ingest_*`, `preflight.py`,
   `preview_metadata.py`, `folder_mapper.py`, `token_parser.py` →
   `tools/ingest_tool/core/`. Update imports.
6. **`tools/_shared/` and `tools/pipeline_deploy/`.**
7. **Split `tests/`.**

Steps 1–5 are the bulk of the import churn; one module-group per commit so a
bisect stays useful.

## Open decisions

*(Resolved 2026-09-02 in `decisions.md` / `pipeline_architecture.md`:)*

- **Ledger scope** — ingest-private, kept in `tools/ingest_tool/core/ledger.py`,
  a temporary exception; not generalized into a pipeline DB.
- **`PreviewMetadata`** — stays in `tools/ingest_tool/core/` for Phase A;
  promote to `square_core/kitsu/` when publish (Phase C) becomes the second
  writer.
- **Shot folder structure** — studio-wide convention: lives in `StudioConfig`
  as a default, copied into each project's `ProjectConfig` by `projects.create`.
