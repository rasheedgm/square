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
- **Defaults, not migration** — a new release adds a key; the schema says its
  default and an absent key just resolves to it (§3.1) — no transform step,
  no migration code (`decisions.md` "No migration before v1.0").
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
schema.register("media_types", "media_type_registry", scope="both", default={})
schema.register("colorspace.working", "str", scope="project", default="ACEScg")
```

A **tool** registers its own keys at import, namespaced `tools.<tool>.<key>`:

```python
# tools/ingest_tool/core/__init__.py
schema.register("tools.ingest.copy_workers", "int", scope="project", default=4,
                minimum=1, maximum=32, description="parallel file copies")
schema.register("tools.ingest.transfer_mode", "enum", scope="project",
                default="copy", choices=("copy", "hardlink", "symlink"))
```

`copy_workers` here is illustrative only — the real one is a **core** key
(`square_core/config/schema.py`, top-level `copy_workers`) because
`services.media.publish` uses it for every transfer, not just ingest's. A
tool never registers its own "which media types do I offer" list either — see
§3.1.

### 3.1 Absence means "use the code default," not "invalid"

Only `kitsu_host` and `nas_roots` (`scope="studio"`) are `required=True` —
genuinely site-specific, no sensible universal default. **Every other key,
including `roots` and `media_types`, is optional.** When a key is absent from
a config file, `ProjectConfig` resolves it from `DEFAULT_PROJECT_CONFIG`,
**merged per sub-key** (`ProjectConfig.roots`, `.colorspace`, `.slugify`,
`.media_type(name)`, `.delivery_template()` — not the raw `self.data.get(...)`
a naive reader might reach for):

- `data["roots"]` missing entirely → all four built-in roots apply.
  `data["roots"] = {"project": "..."}` (only one key) → `shot`/`asset`/
  `delivery` still come from the built-in; only `project` is overridden.
- `data["media_types"]` missing entirely → `media_type_names()` is `[]` (no
  *named* type is configured), but `media_type(anything)` still resolves —
  `_default` merges the built-in `_default` first, so it can never actually
  go missing. `_default` is the one entry the config editor won't let you
  remove (it can still be edited); every other named entry (`Plate`,
  `CompRender`, ...) is exactly what the file lists — a project that never
  mentions `Plate` simply doesn't offer it, no auto-resurrection.
- Same shape for `delivery_template()`, `colorspace`, `slugify`, and every
  scalar (`fps`, `version_pad`, `frame_pad`, `copy_workers`, the three
  folder-structure lists).

This is why `studio_config.template.json` can be **the full reference** (every
key `DEFAULT_PROJECT_CONFIG` supports, under `project_defaults` — see
`config_and_paths.md`) while a real `studio_config.json` only needs
`kitsu_host` + `nas_roots` to be valid: nothing else is required *because* it
already has a code default, and the template exists to show what that default
is, not because any of it needs to be copied in.

`structural_errors()` follows the same rule: a key that is **present** with a
value that breaks resolution (wrong type, or an override that blanks a
required root) is an error; a key that is simply **absent** never is.

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
- The scalar built-ins mirror `DEFAULT_PROJECT_CONFIG` field-for-field.
  **No `tools.*` key is a built-in** — `square_core` ships no tool. Each desktop
  tool registers its own `tools.<tool>.*` when installed; `tools: {}` in a
  config until then. A tool never keeps its own list of media types either — it
  filters the one `media_types` registry by `source` (`cfg.media_type_names(
  source="delivery")` for ingest, `"publish"` for a DCC publish panel).

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
- Save flushes only the fields **actually edited** since the pane was last
  built into the `ConfigStore` (per-key "touched" tracking) — a field the
  editor is merely showing at its resolved `builtin` value is never written
  just because Save was pressed. Then it runs the full `check()` (schema +
  `PathResolver`), refuses on error, writes atomically, keeps a timestamped
  `.bak`. This is what keeps the file matching what's actually configured:
  open a sparse config, look around, save without touching anything, and the
  file on disk is unchanged.
  **Exception: `roots` / `media_types` / `delivery_presets`.** Touching is
  tracked per *key*, not per row/cell — editing one row in a registry table
  marks the whole key touched, and its saved value is the entire table shown
  (every row, including ones still at their built-in value), not a diff of
  just the row you changed. This is deliberate, not an oversight: a named
  entry that isn't in the file at all (e.g. `Plate`) is simply not offered —
  it does **not** fall back to a built-in `Plate` the way `_default` does (see
  §3.1) — so a partial "only the changed row" save would silently drop every
  other visible-but-untouched entry from the project's catalogue. The table
  editor says as much inline.
- The status bar always names the exact file the active tab reads/writes and
  where its `.bak-<timestamp>` will land (same folder, same name).
- Headless: `python -m tools.config_editor --cli {list|get|set|reset|diff}` --
  same one-key-at-a-time behavior; `set` only ever touches the key you name.

---

## 6. What lives where

| only in `studio_config.json` (top level) | a project setting (`scope="both"`) |
|---|---|
| `kitsu_host`, `nas_roots`, `kitsu_project_templates` | fps, resolution, aspect_ratio, colorspace, `copy_workers` |
| `project_defaults` — where the studio's copy of every `scope="both"` key lives | `roots`, `media_types`, `delivery_presets` |
| | folder-structure lists, `tools.<tool>.*` |

`scope="studio"` keys are the three at top left. **Everything else is
`scope="both"`** — a project setting. In `studio_config.json` those live under
`project_defaults` (copied into each new project); the config editor's Studio
tab reads and writes them there, not at the file's top level. `square_core`
registers **no `tools.*` key** — a desktop tool `schema.register()`s its own
`tools.<tool>.*` descriptors when it is installed.

---

## 7. Resolved

1. **Migration hook** — superseded 2026-09-04, see `decisions.md` "No
   migration before v1.0". `ProjectConfig.load()` had a `_migrate_v1()` fold
   for a `schema_version < 2` file; it's been removed. Nothing has shipped, so
   there's no old-shape data to fold — `load()` now requires
   `schema_version == SCHEMA_VERSION` exactly and rejects anything else.
   Migration code gets written only when a real migration is actually needed.
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
