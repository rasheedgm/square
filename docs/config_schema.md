# Config schema & the config API

Companion to [`config_and_paths.md`](config_and_paths.md). Covers **how config
keys are described, validated, resolved, and edited** — for `PipelineConfig`
(studio) and `ProjectConfig` (project).

Status: **built.** `square_core/config/schema.py` (the registry),
`tools/config_editor/` (GUI + `--cli`). This doc matches the implementation;
deltas from the original draft are noted inline.

---

## 1. Why a schema

Config is JSON on disk, but it is **not** free-form:

- The **config editor** (§5) renders a form — it needs to know each key's type,
  default, allowed values, and which scope it belongs to. Without that it's raw
  JSON editing and every studio breaks their config differently.
- **Validation** — a typo'd key (`nas_root` vs `nas_roots`) or a wrong type
  (`version_pad: "3"`) should fail loud at deploy / project-create, not
  silently at 2am mid-render.
- **Defaults & migration** — a new release adds a key; the schema says its
  default and the loader fills it (see `config_and_paths.md` §10).
- **Tool settings** — a tool declares the settings it needs; the editor shows
  them; an uninstalled tool's keys stay untouched in existing configs.

**We do not use JSON Schema (draft-07 etc.).** It's heavy, its validators are
awkward to render as a Qt form, and we need a small, closed set of field kinds.
A flat descriptor registry is enough.

---

## 2. `ConfigKey` descriptor

```python
@dataclass(frozen=True)
class ConfigKey:
    key: str                      # dotted path, e.g. "version_pad", "tools.ingest.copy_workers"
    kind: str                     # "str"|"int"|"float"|"bool"|"path"|"enum"|"list"|"dict"
                                  #   |"template"|"root"|"media_type_registry"|"delivery_registry"
    scope: str                    # "studio" | "project" | "both"
    default: object
    description: str = ""
    choices: tuple = ()           # enum
    minimum: float | None = None  # int/float
    maximum: float | None = None
    item_kind: str = ""           # for "list": the kind of each item
    required: bool = False        # must be present & non-empty in that scope
    secret: bool = False          # never rendered in a log / editor field as plain text
```

`kind` values `template` / `root` / `media_type_registry` / `delivery_registry`
tell the editor to open the specialised sub-editors (template builder, the
media-type table) instead of a plain field, and tell `validate()` to run the
`PathResolver` checks from `config_and_paths.md` §8.

---

## 3. The registry & `register()`

`square_core/config/schema.py` builds the registry at import:

```python
from square_core.config import schema

schema.register("version_pad", "int", scope="both", default=3, minimum=1, maximum=6,
                description="zero-pad width for {version}")
schema.register("nas_roots", "dict", scope="studio", default={"default": "X:/projects"},
                required=True, description="named NAS roots; a project picks one")
schema.register("media_types", "media_type_registry", scope="both", default={...},
                required=True)
schema.register("colorspace.working", "str", scope="project", default="ACEScg")
```

A **tool** registers its own keys at import, namespaced `tools.<tool>.<key>`:

```python
# tools/ingest_tool/core/__init__.py
schema.register("tools.ingest.copy_workers", "int", scope="project", default=4,
                minimum=1, maximum=32, description="parallel file copies")
schema.register("tools.ingest.transfer_mode", "enum", scope="project",
                default="copy", choices=("copy", "hardlink", "symlink"))
schema.register("tools.ingest.media_types", "list", item_kind="str", scope="project",
                default=[], description="media types the ingest tool offers")
```

- `schema.all()` → `{key: ConfigKey}` (the editor iterates this).
- `schema.for_scope("project")` → keys editable at project level
  (`scope="both"` keys appear in both scopes).
- `register()` is **idempotent for an identical descriptor** (a module can be
  imported twice) and raises `SchemaError` on a *conflict* — two registrations
  disagreeing about one key.
- A key **present in a config file but not in the registry** is kept
  (round-trips untouched) but reported by `validate()` as a **warning** —
  usually a tool that isn't installed here, occasionally a typo. A registered
  `dict` / `root` / `media_type_registry` key is a leaf: `validate()` does not
  walk into it looking for unknown sub-keys.
- The scalar built-ins mirror `DEFAULT_PROJECT_CONFIG` field-for-field;
  `tools.ingest.*` is registered by `schema.py` itself for now and moves to
  `tools/ingest_tool/core/` when that tool is ported.

---

## 4. Resolution & validation

```python
schema.resolve(project_data, "tools.ingest.copy_workers",
               pipeline_defaults=pipeline.project_defaults)   # -> resolved value
```

Resolution order for a `scope="project"` (or `"both"`) key:

1. `ProjectConfig` (the project's own `_pipeline/project_config.json`)
2. `PipelineConfig.project_defaults` (the studio default snapshotted at create —
   note: a later studio-default change does **not** reach an existing project)
3. the `ConfigKey.default`

`scope="studio"` keys resolve `PipelineConfig` → default only.

`schema.validate(data, scope)` → `(errors, warnings)`, called by
`ProjectConfig.check()` and `PipelineConfig.check()`:

- a `required` key missing / empty → **error** (`required` is enforced against
  the *project* config, the one a tool consumes; a `scope="both"` key may be
  absent from `studio_config.json` and supplied by `project_defaults`)
- a present value that violates its `kind` / `choices` / range → **error**
- an unregistered key → **warning**, logged, never fatal
- `check()` additionally runs `PathResolver(cfg).validate()` for the
  render + version-variance checks on `roots` / `media_types` / delivery presets
  (`config_and_paths.md` §8)

---

## 5. The config editor

A dedicated admin tool (`tools/config_editor/`). Write access is gated on the
Kitsu user's role being **`admin` or `manager`** (`core.ADMIN_ROLES`); an
offline session or a plain `user` gets a read-only window.

- **The only writer of config.** Every other tool is read-only. Ingest's old
  Settings dialog goes away.
- `core/editor.py::ConfigStore` is the headless engine (load / effective value
  + provenance / validated edit / atomic save + `.bak`). The Qt layer and the
  `--cli` are both thin shells over it.
- Two tabs: **Studio** (`studio_config.json`, unknown / legacy keys preserved
  on save) and **Project** (`{project_root}/_pipeline/project_config.json`,
  chosen from a Kitsu project list).
- One row per `ConfigKey` from `schema.for_scope(...)`: a field widget by kind,
  the value's **source** (`project` override / `studio-default` / `builtin`),
  and — on a project override — a **reset to studio** action.
- `roots` / `media_types` / `delivery_presets` open a table sub-editor;
  `dir` / `file` / pattern cells open the **by-example template builder**
  (live preview rendered against a sample `PathContext`, token palette).
- Save flushes every field into the `ConfigStore`, runs the full `check()`
  (schema + `PathResolver`), refuses on error, writes atomically, keeps a
  timestamped `.bak`.
- Headless: `python -m tools.config_editor --cli {list|get|set|reset|diff}`.

---

## 6. What lives where

| in `PipelineConfig` (studio) | in `ProjectConfig` (project) |
|---|---|
| `kitsu_host`, `nas_roots`, `kitsu_project_templates` | fps, resolution, aspect_ratio, colorspace |
| `project_defaults` = a full ProjectConfig template | `roots`, `media_types`, `delivery_presets` |
| studio-wide `tools.*` defaults | folder-structure lists |
| | per-project `tools.*` overrides |

`media_types`, `roots`, `templates`, `tools.*` are `scope="both"` — a studio
default that a project may override.

---

## 7. Resolved

1. **Migration hook** — done. `ProjectConfig.load()` runs `_migrate_v1()` in
   memory on a `schema_version < 2` file; `ConfigStore.save_project()` re-writes
   it in v2.
2. **`kitsu_kind: working` + our path** — verified live 2026-09-04: the
   `media.publish` E2E recorded a `NukeScript` working file at our resolver
   path (`work/comp/nuke/…_v001.nk`), same `PUT` override as output files.
3. **Admin check** — role must be `admin` or `manager` (`core.ADMIN_ROLES`).
   An offline / role-less session can read but every `save_*` raises
   `NotAuthorized`.

## 8. Still open

- The GUI groups fields in a flat form per tab; a collapsible grouping by dotted
  prefix (`colorspace.*`, `tools.*`) is a polish item.
- `secret` keys: rendered as a password field, but the CLI `list` masks only on
  a set value — no key is marked `secret` yet (credentials live outside config).
