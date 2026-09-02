# `ProjectConfig` + `PathResolver`

Settled 2026-09-02. The path/naming spec for the pipeline — `square_core/paths/`
and `square_core/config/`, built in Phase A steps 3–4. Absorbs today's
`square_core/config.py` (`format_dest_filename`, `DEFAULT_*_TEMPLATE`,
`DEFAULT_MEDIA_TYPE_CONFIGS`, `SHOT_FOLDER_STRUCTURE`, the `dest_template_*`
validators) and `nas_manager.py`'s `get_dest_dir` / `dest_names`.

Locked calls are in `decisions.md`; this is the reference detail.

---

## 1. What it does

`PathResolver` answers **"where does X go on disk"** for every path kind, from
`ProjectConfig` templates. It is **pure** — version in, path out; it never calls
Kitsu, never touches the filesystem. "What version is next / already there" is a
Kitsu query (`kitsu.next_output_revision`) or a `storage` disk check, not the
resolver's job.

**Renders write straight to the final `output/` path** (no separate farm
scratch). `work.publish_output` then just verifies the frames and calls
`kitsu.record_output_file` — a copy happens only when an interactive DCC render
sits in a local/temp dir and has to be moved into `output/`.

Path kinds:

| Method | Example result |
|---|---|
| `project_root(ctx)` | `X:/projects/ABC` |
| `shot_dir(ctx)` | `X:/projects/ABC/shots/SQ010/SH0100` |
| `asset_dir(ctx)` | `X:/projects/ABC/assets/char/hero` |
| `workfile_path(ctx)` | `.../SH0100/work/comp/nuke/ABC_SQ010_SH0100_comp_main_v003.nk` |
| `output_dir(ctx)` / `output_path(ctx)` | `.../SH0100/output/comp/v003/exr/` + `ABC_SQ010_SH0100_comp_main_v003.1001.exr` |
| `ingest_dest_dir(ctx)` / `ingest_dest_file(ctx)` | keyed by `ctx.media_type` |
| `delivery_dir(ctx)` / `delivery_file(ctx)` | keyed by `ctx.client` |
| `shot_folders(ctx)` / `asset_folders(ctx)` | `shot_dir` + each skeleton entry |

Returns a **POSIX-separator absolute string** (forward slashes). That string is
what gets written into the Kitsu file record and the ingest ledger; filesystem
callers wrap it in `Path()`.

---

## 2. Token vocabulary

`{token}` style. Case of a substituted value is **preserved** (see §3).

| Canonical | Meaning | Accepted aliases |
|---|---|---|
| `{nas_root}` | NAS root for this project | |
| `{project}` | project code | `{project_code}` |
| `{episode}` | episode code (episodic only; empty ⇒ segment drops) | `{ep}` |
| `{sequence}` | sequence code | `{seq}` `{sequence_code}` |
| `{shot}` | shot code | `{shot_code}` |
| `{asset}` / `{asset_type}` | asset name / type | |
| `{task}` | task-type name (`comp`, `anim`) | `{task_type}` |
| `{department}` | department, if distinct from task | `{dept}` |
| `{software}` | `nuke` `maya` `houdini` | `{dcc}` |
| `{output_type}` | `comp`, `plate`, `cache` … (== media type on ingest) | `{media_type}` `{type}` |
| `{name}` | sub-identifier when a shot/task has **more than one** file of the same type — plate `bg` vs `fg`, comp `main` vs `matte`. Kitsu's `working_file`/`output_file` `name` field. Default `main`, always rendered. | `{media_name}` |
| `{version}` | major version int, zero-padded (`version_pad`, default 3) | |
| `{version_label}` | `v` + padded major, `+ .minor` if minor>0 (`v003`, `v003.02`) | |
| `{minor}` | minor version, padded 2; empty if 0 | |
| `{representation}` | `exr` `mov` `jpg` (Kitsu's repr token) | `{repr}` |
| `{ext}` | file extension without dot | |
| `{frame}` | frame number, padded (`frame_pad`, default 4); empty ⇒ `.{frame}`/`_{frame}` segment drops | |
| `{resolution}` | `3840x2160` | `{res}` |
| `{fps}` | `24` / `23.976` | |
| `{client}` / `{package}` | delivery client / package id | |
| `{date}` | `YYYYMMDD` (delivery/package) | |
| `{user}` | current user login | |
| `{site}` | reserved for multi-site; empty for now | |

Format specs pass through: `{version:04d}` → `0001` (overrides `version_pad`);
`{frame:05d}` likewise.

Unknown token in a template ⇒ `PathResolver.validate()` fails (see §8).

---

## 3. Casing & slugify

- **Case is preserved by default.** Client-delivered `Sh010` stays `Sh010`.
  This is the whole reason we resolve paths ourselves instead of Kitsu's
  file_tree (which only does all-upper / all-lower).
- Every substituted value is **slugified**: spaces → `_`, the Windows-illegal
  set `< > : " / \ | ? *` and control chars stripped, runs of `_` collapsed.
  Literals in the template are left exactly as typed.
- A template block *may opt in* to a case transform:
  `"case": "preserve" | "upper" | "lower"` (default `preserve`). Applied after
  slugify, to the whole rendered `dir` / `file`. This is the escape hatch for a
  client that demands `SHOT_COMP_V001`. **Block-level only** — no per-token
  `{shot:upper}`.

---

## 4. `ProjectConfig` schema

`{project_root}/_pipeline/project_config.json`. Written by `projects.create`
from `StudioConfig.project_defaults` + `ProjectSpec` overrides. Read live by
every tool; never snapshotted.

```jsonc
{
  "schema_version": 1,

  "fps": 24.0,
  "resolution": "3840x2160",
  "aspect_ratio": "2.39",

  "colorspace": {
    "ocio": "aces_1.3",              // config name or path; "" = studio default
    "working": "ACEScg",
    "delivery": "Rec.709",
    "plate_assumed": "ACEScg"        // used when a delivery doesn't declare one
                                    //  (still surfaced as "unverified" — never silent)
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

  "templates": {
    "workfile": {
      "base": "shot",
      "dir":  "work/{task}/{software}",
      "file": "{project}_{sequence}_{shot}_{task}_{name}_v{version}.{ext}"
    },
    "output": {                        // farm + interactive publishes both write here
      "base": "shot",
      "dir":  "output/{output_type}/v{version}/{representation}",
      "file": "{project}_{sequence}_{shot}_{output_type}_{name}_v{version}.{frame}.{ext}"
    }
  },

  "ingest": {
    "default": {
      "base": "shot",
      "dir":  "in/{media_type}/{name}_v{version}",
      "file": "{project}_{sequence}_{shot}_{media_type}_{name}_v{version}.{frame}.{ext}"
    },
    "by_type": {
      "Plate":    { "dir": "plates/{name}_v{version}" },
      "Ref":      { "dir": "ref/{name}_v{version}" },
      "BG Plate": { "dir": "bg_plates/{name}_v{version}" }
      // file + base inherited from "default" unless overridden
    }
  },

  "shot_folder_structure": [
    "2D/comp/render/exr", "2D/comp/render/mov", "2D/comp/workfiles/nuke",
    "3D/animation/workfiles", "3D/matchmove/camera", "in"
    // … the full 2D/3D tree (from today's SHOT_FOLDER_STRUCTURE)
  ],
  "asset_folder_structure": [ "model/workfiles", "surfacing/textures", "…" ],
  "project_folder_structure": [ "shots", "assets", "_delivery", "_pipeline", "editorial" ],

  "delivery_presets": {
    "default": {
      "base": "delivery",
      "dir":  "{client}/{package}",
      "file": "{shot}_{output_type}_v{version}.{frame}.{ext}",
      "case": "preserve",
      "container": "exr", "frame_pad": 4, "colorspace": "Rec.709",
      "slate": true, "burnin": ["shot", "version", "frame", "date"]
    },
    "ACME": {
      "file": "ACME_{shot}_comp_v{version}.{frame}.{ext}",
      "case": "upper", "container": "dpx"
    }
  }
}
```

Rules baked in:
- Every versioned template **must vary by version** — `validate()` renders it at
  v1 and v2 and rejects equal results (this is the real
  Element/LUT/Audio-overwrite bug from the current config).
- `by_type` / client presets inherit missing keys from their `default`.
- A `dir` value is **relative to its `base` root**; an absolute-looking value
  (`{nas_root}/…`) is allowed and used as-is.

---

## 5. `StudioConfig` side

```jsonc
{
  "kitsu_host": "http://kitsu.square.local/api",
  "nas_roots": { "default": "X:/projects", "cache": "L:/localize" },
  "kitsu_project_templates": ["VFX Shots", "VFX Episodic", "Commercial"],

  "project_defaults": {
    // a ProjectConfig minus the per-show values — copied verbatim on create
    "fps": 24.0, "resolution": "3840x2160", "aspect_ratio": "2.39",
    "version_pad": 3, "frame_pad": 4, "slugify": { … },
    "colorspace": { … },
    "roots": { … }, "templates": { … }, "ingest": { … },
    "shot_folder_structure": [ … ], "asset_folder_structure": [ … ],
    "project_folder_structure": [ … ], "delivery_presets": { … }
  }
}
```

`projects.create(spec)`:
1. `kitsu.create_project(spec)` → apply `spec.kitsu_template`, set a minimal file_tree
2. `cfg = ProjectConfig.from_defaults(studio.project_defaults, overrides=spec.overrides)`
3. `cfg.nas_root = studio.nas_roots[spec.nas_root_name or "default"]`
4. write `{project_root}/_pipeline/project_config.json`
5. `storage.layout.create_tree(project_root, cfg.project_folder_structure)`

`ProjectSpec`: `code`, `name`, `production_type`, `kitsu_template`,
`nas_root_name`, `client`, `overrides: dict` (any `ProjectConfig` key).

---

## 6. `PathResolver` API

```python
class PathResolver:
    def __init__(self, config: ProjectConfig): ...

    # roots
    def project_root(self, ctx: PathContext) -> str
    def shot_dir(self, ctx) -> str
    def asset_dir(self, ctx) -> str

    # work / publish
    def workfile_path(self, ctx) -> str
    def output_dir(self, ctx) -> str
    def output_path(self, ctx) -> str          # a frame if ctx.frame set, else the sequence base

    # ingest  (keyed by ctx.media_type, falls back to ingest.default)
    def ingest_dest_dir(self, ctx) -> str
    def ingest_dest_file(self, ctx) -> str      # filename only
    def ingest_sequence_files(self, ctx, frames: list[int]) -> list[str]

    # delivery  (keyed by ctx.client, falls back to delivery_presets.default)
    def delivery_dir(self, ctx) -> str
    def delivery_file(self, ctx) -> str
    def delivery_preset(self, client: str) -> dict     # container/colorspace/slate/burnin

    # skeleton
    def shot_folders(self, ctx) -> list[str]
    def asset_folders(self, ctx) -> list[str]

    # low level / tooling
    def render(self, template: str, ctx, *, case: str = "preserve") -> str
    def validate(self) -> list[str]             # [] == ok
```

### `PathContext` (`square_core/model`)

Plain frozen dataclass. The **service** fills it from the entity dicts it
already holds — the resolver never walks the entity chain itself.

```python
@dataclass(frozen=True)
class PathContext:
    nas_root: str
    project: str
    episode: str = ""
    sequence: str = ""
    shot: str = ""
    asset: str = ""; asset_type: str = ""
    task: str = ""; department: str = ""; software: str = ""
    output_type: str = ""          # media_type is an alias in templates
    name: str = "main"
    version: int = 1; minor: int = 0
    representation: str = ""; ext: str = ""
    resolution: str = ""; fps: str = ""
    frame: int | None = None
    client: str = ""; package: str = ""
    user: str = ""; site: str = ""

    @classmethod
    def for_shot(cls, project, shot, *, task=None, **over) -> "PathContext": ...
    @classmethod
    def for_asset(cls, project, asset, *, task=None, **over) -> "PathContext": ...

    @property
    def media_type(self) -> str: return self.output_type
```

`ProjectContext` (composition root) exposes a shortcut so callers rarely build
one by hand:

```python
pctx.paths.workfile_path(pctx.ctx(shot=sh, task=t, software="nuke", version=3))
```

---

## 7. Rendering rules (`render()`)

0. **Resolve `roots` first**, in dependency order: a root value may contain
   `{<name>_root}` (e.g. `roots.shot` uses `{project_root}`). `validate()`
   rejects a cycle or a reference to a missing root.
1. Resolve `base` root (if any) → prepend.
2. For each `{token}` / `{token:spec}`:
   - map aliases → canonical; `media_type` → `output_type`
   - `version` → padded (`:spec` wins, else `version_pad`); `version_label` →
     `v` + padded `+ (".{minor:02d}" if minor)`; `minor` → padded or `""`
   - `frame` → padded; if `ctx.frame is None`, delete the enclosing
     `.{frame}` / `_{frame}` / `{frame}.` token *with its adjacent separator*
   - value present → slugify (§3) and substitute
   - value empty:
     - **required** for this template (seq/shot for shot paths; task for
       workfile; output_type for output; client for delivery) → raise
       `PathError(token)` — the caller surfaces it as Needs-Info, never guesses
     - **optional** (`representation`, `episode`, `department`, `site`,
       `resolution`) → drop the token and any now-empty path segment
       (`//` → `/`, leading/trailing sep trimmed). `name` is never empty
       (defaults `main`).
3. Apply the block's `case` transform to the whole result.
4. Normalise: collapse `//`, strip trailing `/`, forward slashes.

---

## 8. Validation — `validate()`

Carries forward `dest_template_renders` + `dest_template_versions_safely`:

- every `roots` / `templates` / `ingest.*` / `delivery_presets.*` value renders
  against a probe `PathContext` with every field populated
- every version-bearing template **changes** between v1 and v2
- every `ingest.by_type` entry resolves to a per-version-distinct folder
- no unknown tokens; no `{frame}` in a `dir`; no `base` naming a missing root;
  no `{<name>_root}` cycle
- `ProjectConfig.load()` runs `validate()` and **refuses to load** on error
  (today's code silently falls back to a built-in default — we'd rather fail
  loud, since a bad template means wrong-place writes)

---

## 9. Explicitly *not* the resolver's job

| Concern | Owner |
|---|---|
| "what's the next / latest version number" | `kitsu.next_output_revision` / `next_working_revision` |
| "is this dest folder empty / already has this / conflicts" | `storage` disk check (ingest's `inspect_slot`) |
| creating the folders | `storage.layout` |
| the copy | `storage.transfer` |
| writing the path into Kitsu | `kitsu.record_*_file` |

---

## 10. Migration from `square_core/config.py`

| Today | Becomes |
|---|---|
| `DEFAULT_FILE_NAME_TEMPLATE` | `templates.output.file` / `templates.workfile.file` |
| `SHOT_DIRECTORY_TEMPLATE` | `roots.shot` + `templates.output.dir` |
| `SHOT_RENDER_TEMPLATE` | dropped — renders write to `templates.output` directly |
| `DEFAULT_MEDIA_TYPE_CONFIGS` | `ingest.by_type` |
| `SHOT_FOLDER_STRUCTURE` | `shot_folder_structure` |
| `format_dest_filename()` | `PathResolver.render()` |
| `dest_template_renders` / `_versions_safely` | `PathResolver.validate()` |
| `nas_manager.get_dest_dir` / `dest_names` | `ingest_dest_dir` / `ingest_sequence_files` |
| `StudioConfig` templates/structure fields | `StudioConfig.project_defaults` (copied into `ProjectConfig` on create) |
| `preview_metadata.KITSU_DATA_KEY = "square_ingest"` | `"square"` |

The ingest tool's Path Pattern engine (`path_pattern.py`, `token_parser.py`) is
**separate** — it parses *incoming* delivery folders, not outgoing paths. It
stays as-is under `square_core/paths/`.
