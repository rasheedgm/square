# `ProjectConfig` + `PathResolver`

The path / naming spec — `square_core/paths/` and `square_core/config/`.
Companion: [`config_schema.md`](config_schema.md) (the config-key registry the
admin editor and every tool use). Locked calls are in `decisions.md`.

**v2 (2026-09-04):** ingest and render-output are unified into **one media-type
registry**. `templates.output`, `templates.workfile` and `ingest.by_type` are
gone; there is one `media_types` table and one `PathResolver.media_path()`.

---

## 1. One idea: a *media type*

Everything the pipeline creates or takes in — a delivered plate, a comp render,
a Nuke script, a camera cache — is **a versioned set of files of a configured
type, belonging to an entity**. The type is a **media type**: `Plate`,
`CompRender`, `NukeScript`, `Cache`. The studio names and configures them; every
tool goes through the same call:

> "I have files of media type **T**, name **M**, for **shot/asset E**, at
> version **V** — where do they go, what are they called, how is it recorded?"

- **Ingest** does this with `T = Plate` (or `Ref`, `BG Plate`, …), source = a
  vendor delivery.
- **Nuke** does this with `T = CompRender`, source = a local render.
- Same path logic, same Kitsu record. The only differences are *where the files
  came from* and *where the version number comes from* — both handled by the
  service layer, not the resolver.

`media_type` is the **studio-facing** name. Under the hood it maps to Kitsu's
`output_type` (or `working_file` — see `kitsu_kind` below), so there is no
parallel taxonomy: Kitsu's `(entity, output_type, name, revision)` grouping is
what "a version that contains multiple files" *is*.

**Delivery stays separate** — per-client, transcoded, QC'd, manifested — as
`delivery_presets`. **Workfiles** are just media types with
`kitsu_kind: "working"`.

`PathResolver` is **pure**: media type + `PathContext` in, path string out. It
never calls Kitsu, never touches disk. "Next version", "does this slot exist",
"copy the bytes", "write the Kitsu record" all belong elsewhere (§9).

---

## 2. Token vocabulary

`{token}` style. Case of a substituted value is **preserved** (see §3).

| Canonical | Meaning | Accepted aliases |
|---|---|---|
| `{nas_root}` | NAS root for this project (never slugified) | |
| `{project}` | project code | `{project_code}` |
| `{episode}` | episode code (episodic only; empty ⇒ segment drops) | `{ep}` |
| `{sequence}` | sequence code | `{seq}` `{sequence_code}` |
| `{shot}` | shot code | `{shot_code}` |
| `{asset}` / `{asset_type}` | asset name / type | |
| `{task}` | task-type name (`comp`, `anim`) | `{task_type}` |
| `{department}` | department, if distinct from task | `{dept}` |
| `{software}` | `nuke` `maya` `houdini` | `{dcc}` |
| `{media_type}` | the media type — `plate`, `comp_render`, `nuke_script` | `{output_type}` `{type}` |
| `{name}` | sub-identifier when an entity has **more than one** of the same type — plate `bg` / `fg`, comp `main` / `matte`. Kitsu's file `name`. Default `main`, always rendered. | `{media_name}` |
| `{version}` | major version int, zero-padded (`version_pad`, default 3) | |
| `{version_label}` | `v` + padded major, `+ .minor` if minor>0 (`v003`, `v003.02`) | |
| `{minor}` | minor version, padded 2; empty if 0 | |
| `{representation}` | `exr` `mov` `nk` `abc` — the file kind within a version | `{repr}` |
| `{ext}` | file extension without dot | |
| `{frame}` | frame number, padded (`frame_pad`, default 4); empty ⇒ `.{frame}`/`_{frame}` segment drops | |
| `{resolution}` | `3840x2160` | `{res}` |
| `{fps}` | `24` / `23.976` | |
| `{client}` / `{package}` | delivery client / package id | |
| `{date}` | `YYYYMMDD` | |
| `{user}` | current user login | |
| `{site}` | reserved for multi-site; empty for now | |

Format specs pass through: `{version:04d}` → `0001`; `{frame:05d}` likewise.
Unknown token ⇒ `validate()` fails (§8).

---

## 3. Casing & slugify

- **Case is preserved by default.** Client-delivered `Sh010` stays `Sh010` —
  the whole reason we resolve paths ourselves instead of Kitsu's `file_tree`
  (which only does all-upper / all-lower).
- Every substituted value is **slugified**: spaces → `_`, the Windows-illegal
  set `< > : " / \ | ? *` and control chars stripped, runs of `_` collapsed.
  Template literals are left exactly as typed. `{nas_root}` is exempt (a drive
  letter's `:` and the `/` separators must survive).
- A block *may opt in* to `"case": "preserve" | "upper" | "lower"` (default
  `preserve`), applied after slugify to the whole rendered `dir` / `file`. The
  escape hatch for a client that demands `SHOT_COMP_V001`. **Block-level only.**

---

## 4. `ProjectConfig` schema

`{project_root}/_pipeline/project_config.json`. Written by `projects.create`
from `PipelineConfig.project_defaults` + `ProjectSpec` overrides. Read live by
every tool; never snapshotted. Every key is described in
[`config_schema.md`](config_schema.md); `load()` validates against it.

```jsonc
{
  "schema_version": 2,

  "fps": 24.0,
  "resolution": "3840x2160",
  "aspect_ratio": "2.39",
  "colorspace": {
    "ocio": "aces_1.3",
    "working": "ACEScg",
    "delivery": "Rec.709",
    "plate_assumed": "ACEScg"        // used if a delivery doesn't declare one --
                                    //  still surfaced as "unverified", never silent
  },

  "version_pad": 3,
  "frame_pad": 4,
  "slugify": { "spaces_to": "_", "strip": "<>:\"/\\|?*", "collapse": "_" },

  // roots may reference each other with {<name>_root}; resolved in dependency order
  "roots": {
    "project":  "{nas_root}/{project}",
    "shot":     "{project_root}/{episode}/shots/{sequence}/{shot}",
    "asset":    "{project_root}/assets/{asset_type}/{asset}",
    "delivery": "{project_root}/_delivery"
  },

  // ── the media-type registry ──────────────────────────────────────────
  // keyed by the studio's own name. Studio config defines the full set;
  // a project overrides an entry (deep-merged) or adds its own.
  "media_types": {
    "_default": {                    // every entry inherits missing keys from here
      "base": "shot",
      "dir":  "input/{media_type}/{name}_v{version}",
      "file": "{project}_{sequence}_{shot}_{media_type}_{name}_v{version}.{frame}.{ext}",
      "kitsu_kind": "output",        // output | working
      "previewable": false,
      "colorspace": ""               // assumed for this type when a file doesn't declare one
    },
    "Plate":       { "dir": "plates/{name}_v{version}",     "previewable": true, "colorspace": "ACEScg" },
    "Ref":         { "dir": "ref/{name}_v{version}",        "previewable": true },
    "BG Plate":    { "dir": "bg_plates/{name}_v{version}",  "previewable": true, "colorspace": "ACEScg" },
    "Element":     { "dir": "elements/{name}_v{version}" },
    "LUT":         { "dir": "luts/{name}_v{version}" },
    "Audio":       { "dir": "audio/{name}_v{version}" },

    "CompRender":  { "dir": "output/comp/v{version}/{representation}",
                     "file": "{project}_{sequence}_{shot}_comp_{name}_v{version}.{frame}.{ext}",
                     "previewable": true, "colorspace": "ACEScg" },
    "Precomp":     { "dir": "output/precomp/v{version}/{representation}" },
    "Cache":       { "dir": "output/cache/{name}/v{version}", "representation": "abc" },

    "NukeScript":  { "kitsu_kind": "working", "previewable": false,
                     "dir":  "work/comp/nuke",
                     "file": "{project}_{sequence}_{shot}_comp_{name}_v{version}.nk" },
    "MayaScene":   { "kitsu_kind": "working",
                     "dir":  "work/{task}/maya",
                     "file": "{project}_{sequence}_{shot}_{task}_{name}_v{version}.ma" }
  },

  // the folder trees `storage.layout` creates (Zou never makes folders)
  "shot_folder_structure":    [ "2D/comp/render/exr", "3D/matchmove/camera", "input", "…" ],
  "asset_folder_structure":   [ "model/workfiles", "surfacing/textures", "…" ],
  "project_folder_structure": [ "shots", "assets", "_delivery", "_pipeline", "editorial" ],

  "delivery_presets": {
    "_default": {
      "base": "delivery",
      "dir":  "{client}/{package}",
      "file": "{shot}_{media_type}_v{version}.{frame}.{ext}",
      "case": "preserve",
      "container": "exr", "frame_pad": 4, "colorspace": "Rec.709",
      "slate": true, "burnin": ["shot", "version", "frame", "date"]
    },
    "ACME": { "file": "ACME_{shot}_comp_v{version}.{frame}.{ext}", "case": "upper", "container": "dpx" }
  },

  // per-tool settings (schema-registered by each tool; see config_schema.md)
  "tools": {
    "ingest": { "copy_workers": 4, "transfer_mode": "copy",
                "media_types": ["Plate", "Ref", "BG Plate", "Element", "LUT", "Audio"] },
    "nuke":   { "output_types": ["CompRender", "Precomp"], "workfile_type": "NukeScript" }
  }
}
```

### media-type entry

| key | |
|---|---|
| `base` | a `roots` name the `dir` hangs off |
| `dir` | folder template, relative to `base` |
| `file` | filename template |
| `kitsu_kind` | `output` → stored as a Kitsu `output_file`; `working` → a `working_file`. Decides which side of Kitsu's model it lives on (matters for Kitsu's own review UI). |
| `representation` | default representation token, if the type has one fixed kind (`Cache` → `abc`) |
| `previewable` | generate a review proxy on publish |
| `colorspace` | assumed colorspace for this type when a file's header doesn't carry one (still flagged unverified) |
| `case` | optional per-entry case override |

Inheritance: an entry deep-merges over `_default`; a project entry deep-merges
over the studio entry of the same name. `tools.*` entries pick *which* media
types a given tool offers — the registry itself is just resolution config.

Baked-in rules:
- Every media type **must vary by version** — `validate()` renders `dir + file`
  at v1 and v2 and rejects equal results.
- A `dir` value relative to `base`; an absolute-looking value (`{nas_root}/…`)
  is used as-is.

---

## 5. `PipelineConfig` (studio) side

```jsonc
{
  "kitsu_host": "http://kitsu.square.local/api",
  "nas_roots": { "default": "X:/projects", "cache": "L:/localize" },
  "kitsu_project_templates": ["VFX Shots", "VFX Episodic", "Commercial"],

  "project_defaults": {
    // a ProjectConfig minus the per-show values -- copied verbatim on create
    "fps": 24.0, "resolution": "3840x2160", "colorspace": { … },
    "version_pad": 3, "frame_pad": 4, "slugify": { … },
    "roots": { … }, "media_types": { … },
    "shot_folder_structure": [ … ], "asset_folder_structure": [ … ],
    "project_folder_structure": [ … ], "delivery_presets": { … },
    "tools": { … }
  }
}
```

`projects.create(spec)`:
1. `kitsu.create_project(spec)` → apply `spec.kitsu_template`, set a minimal file_tree
2. `cfg = ProjectConfig.from_defaults(studio.project_defaults, overrides=spec.overrides)`
3. `cfg` picks its NAS root from `studio.nas_roots[spec.nas_root or "default"]`
4. write `{project_root}/_pipeline/project_config.json`
5. `storage.layout.create_tree(project_root, cfg.project_folder_structure)`

---

## 6. `PathResolver` API

```python
class PathResolver:
    def __init__(self, config: ProjectConfig): ...

    # roots
    def project_root(self, ctx: PathContext) -> str
    def shot_dir(self, ctx) -> str
    def asset_dir(self, ctx) -> str

    # media -- ONE path for ingest, render, workfile, cache, ...
    def media_dir(self, media_type: str, ctx) -> str
    def media_file(self, media_type: str, ctx) -> str           # filename only
    def media_path(self, media_type: str, ctx) -> str           # dir + file (a frame if ctx.frame, else the seq base)
    def media_sequence(self, media_type: str, ctx, frames) -> list[str]
    def media_entry(self, media_type: str) -> dict               # the resolved config entry

    # delivery (keyed by ctx.client)
    def delivery_dir(self, ctx) -> str
    def delivery_file(self, ctx) -> str
    def delivery_preset(self, client: str) -> dict

    # skeleton
    def shot_folders(self, ctx) -> list[str]
    def asset_folders(self, ctx) -> list[str]

    # low level
    def render(self, template: str, ctx, *, case: str = "preserve") -> str
    def validate(self) -> list[str]                              # [] == ok
```

Convenience on `ProjectContext`:
```python
pctx.paths.media_path("CompRender", pctx.ctx(sequence="SQ010", shot="SH0100",
                                             name="main", version=3,
                                             representation="exr", frame=1001))
```

### `PathContext` (`square_core/model`)

Frozen dataclass. The **service** fills it from the entity dicts it already
holds — the resolver never walks the entity chain.

```python
@dataclass(frozen=True)
class PathContext:
    nas_root: str; project: str
    episode: str = ""; sequence: str = ""; shot: str = ""
    asset: str = ""; asset_type: str = ""
    task: str = ""; department: str = ""; software: str = ""
    media_type: str = ""            # {output_type}/{type} are aliases in templates
    name: str = "main"
    version: int = 1; minor: int = 0
    representation: str = ""; ext: str = ""
    resolution: str = ""; fps: str = ""
    frame: int | None = None
    client: str = ""; package: str = ""; date: str = ""
    user: str = ""; site: str = ""

    def with_(self, **over) -> "PathContext": ...
```

---

## 7. Rendering rules (`render()`)

0. **Resolve `roots` first**, in dependency order: a value may contain
   `{<name>_root}` (`roots.shot` uses `{project_root}`). `validate()` rejects a
   cycle or a missing-root reference. `{nas_root}` is a `PathContext` token, not
   a root reference.
1. Prepend the `base` root, if any.
2. Per `{token}` / `{token:spec}`:
   - map aliases → canonical (`media_type`/`output_type`/`type` → `media_type`)
   - `version` → padded; `version_label`; `minor` → padded or `""`
   - `frame` → padded; if `ctx.frame is None`, delete the enclosing
     `.{frame}` / `_{frame}` token *with its adjacent separator*
   - value present → slugify (§3), except `{nas_root}` (verbatim)
   - value empty:
     - **required** (`sequence`/`shot` for shot paths, `media_type` for media,
       `client` for delivery) → raise `PathError` — never guessed
     - **optional** (`episode`, `department`, `software`, `representation`,
       `resolution`, `fps`, `site`, `minor`, …) → drop the token and any
       now-empty segment. `name` is never empty (defaults `main`).
3. Apply the block's `case`.
4. Normalise: collapse `//`, strip trailing `/`, forward slashes.

---

## 8. Validation — `validate()`

- every `roots` value + every `media_types.*` `dir`/`file` + every
  `delivery_presets.*` renders against a fully-populated probe `PathContext`
- every media type's `dir + file` **changes** between v1 and v2 (the
  Element/LUT-overwrite bug)
- no unknown tokens; no `{frame}` in a `dir`; no `base` naming a missing root;
  no `{<name>_root}` cycle
- schema check (`config_schema.md`): unknown top-level key → warn; a required
  key missing or a type mismatch → error
- `ProjectConfig.load()` runs this and **refuses to load** on any error — a bad
  template means writes land in the wrong place

---

## 9. Explicitly *not* the resolver's job

| Concern | Owner |
|---|---|
| next / latest version number | `kitsu.next_revision(entity, media_type, task)` (dispatches output vs working) |
| "is this slot empty / already has this / conflicts" | `storage` disk check |
| creating folders | `storage.layout` |
| copying / verifying bytes | `storage.transfer` |
| writing the Kitsu record | `kitsu.record_media(...)` — `output_file` or `working_file` per `kitsu_kind`; sets `source_file_id` + `data["square"]["inputs"]` for dependencies |
| deciding a tool's offered types | `tools.<tool>.*` config |

`media.publish(pctx, entity, media_type, task, *, files, name, source=, media_info=, inputs=)`
is the single service call ingest and Nuke both make — it resolves the path,
moves/verifies the files, records the Kitsu media, and (if `previewable`)
trickles a review proxy behind it.

---

## 10. Migration

| v1 / pre-pipeline | v2 |
|---|---|
| `templates.output` | a `media_types` entry, `kitsu_kind: output` |
| `templates.workfile` | a `media_types` entry, `kitsu_kind: working` |
| `ingest.default` / `ingest.by_type` | `media_types._default` / `media_types.<Name>` |
| `DEFAULT_MEDIA_TYPE_CONFIGS` (old config.py) | `media_types` |
| `PathResolver.output_dir/output_path/workfile_path/ingest_dest_*` | `media_dir/media_file/media_path/media_sequence(media_type, ctx)` |
| `PathContext.output_type` | `PathContext.media_type` |
| `kitsu.record_output_file` / `record_working_file` | still the primitives; `kitsu.record_media` / `services.media.publish` dispatch on `kitsu_kind` |
| copy_workers / transfer_mode / preview-enabled types (scattered) | `tools.ingest.*` |
| `schema_version: 1` | `schema_version: 2` (loader migrates: fold `templates`+`ingest` into `media_types`) |

`path_pattern.py` / `token_parser.py` (incoming delivery-folder matching) are
**unchanged and unrelated** — they parse a vendor's folder shape, not outgoing
paths.
