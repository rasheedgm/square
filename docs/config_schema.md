# Config schema & the config API

Companion to [`config_and_paths.md`](config_and_paths.md). Covers **how config
keys are described, validated, resolved, and edited** — for `PipelineConfig`
(studio) and `ProjectConfig` (project).

Status: **draft for review.** Built in Phase B alongside the config-editor tool.

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

- `schema.all()` → the whole registry (the editor iterates this).
- `schema.for_scope("project")` → keys editable at project level.
- Registering the same key twice with a different descriptor is an error
  (catches two tools claiming one key).
- A key **present in a config file but not in the registry** is kept
  (round-trips untouched) but flagged by `validate()` as `unknown` — usually a
  tool that isn't installed here, occasionally a typo.

---

## 4. Resolution & validation

```python
config.get(pctx.config, "tools.ingest.copy_workers")   # -> resolved value
```

Resolution order for a `scope="project"` (or `"both"`) key:

1. `ProjectConfig` (the project's own `_pipeline/project_config.json`)
2. `PipelineConfig.project_defaults` (the studio default snapshotted at create —
   note: a later studio-default change does **not** reach an existing project)
3. the `ConfigKey.default`

`scope="studio"` keys resolve `PipelineConfig` → default only.

`ProjectConfig.check()` / `PipelineConfig.check()`:

- every `required` key for that scope is present and non-empty → else `ConfigError`
- every present key's value matches its `kind` / `choices` / range → else `ConfigError`
- `unknown` keys → a warning line, not an error
- `media_type_registry` / `template` / `root` keys → run the `PathResolver`
  render + version-variance checks (`config_and_paths.md` §8)

---

## 5. The config editor

A dedicated admin tool (`tools/config_editor/`). Gated on the current user
being a Kitsu **studio manager / admin**.

- **The only writer of config.** Every other tool is read-only. Ingest's old
  Settings dialog goes away.
- Two panes: **Studio** (`studio_config.json` on the deployed NAS) and
  **Project** (`{project_root}/_pipeline/project_config.json`, pick a project).
- Renders one field per `ConfigKey` from `schema.all()`, grouped by the dotted
  prefix (`colorspace.*`, `tools.ingest.*`). `media_types` opens the media-type
  table (name, base, dir, file, kitsu_kind, previewable, colorspace); templates
  open the by-example builder.
- A project field shows the **effective** value and where it came from
  (project override / studio default / built-in), with a "reset to studio"
  action.
- Save runs `check()` and refuses on error; writes atomically; keeps a
  timestamped `.bak` (same as the deploy `--update-config` flow).

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

## 7. Open

1. **Migration hook** — v1→v2 folds `templates` + `ingest` into `media_types`.
   Where: `ProjectConfig.load()` on a `schema_version < 2` file, in memory, with
   a one-line log; the editor re-saves it in v2 on next edit. Confirm.
2. **`kitsu_kind: working` + our path** — Kitsu's `working_files` have their own
   file_tree path convention; we override it the same way we override
   `output_file` paths (`config_and_paths.md` — verified for output files;
   confirm the working-file `PUT path` works the same way against live Zou).
3. **Admin check** — `gazu` exposes a user's role; confirm "studio manager" is
   the gate, and how a non-Kitsu (offline) editor session is handled (probably:
   editor requires Kitsu).
