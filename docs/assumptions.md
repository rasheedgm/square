# Assumptions

Things the tools take for granted. If one turns out wrong, it usually means a
design change, not just a bug fix — so flag it here when it breaks.

## Environment

- Artists are on **Windows workstations**; some freelancers work remotely.
- NAS is a **mapped drive** (e.g. `X:\`) on the workstation LAN; freelancers
  may be on a slow link.
- Python **3.11**, isolated env at `./env`. `Qt.py` resolves to **PySide6**
  (PyQt6 also installed).
- **OpenImageIO** is available for EXR/DPX/TIFF header reads. Without it those
  formats fall through to no metadata (resolution/fps/colorspace unknown).
- **ffmpeg** comes from `imageio-ffmpeg` (bundled binary); `ffprobe` may not
  be on PATH, so video metadata falls back to parsing `ffmpeg -i` stderr.
- `xxhash` extension is present (xxh3_64); MD5 is the only fallback.

## Kitsu

- **Self-hosted Kitsu / Zou**, reached via `gazu`. Dev is `localhost`.
- The project's **task statuses include one named/short-named "Done"**.
- `gazu`'s shot/task **list endpoints don't reliably carry `sequence_name` /
  `task_type_name`** — resolve via `parent_id` / the type list instead.
- `update_preview` **drops unknown top-level keys**; only the `data` JSONB
  column is writable for custom fields.
- A Kitsu **preview belongs to a task and is revisioned per preview file**,
  not per shot.

## Deliveries & media

- Incoming media lands on a **browsable path** (a drop folder / vendor mount).
- **Frame numbers are in the filename** (`name.1001.exr` or bare `1001.exr`).
- A delivery may have **more than one folder shape** — handled by an ordered
  list of Path Patterns, first match wins.
- **Colorspace is usually a convention, not header metadata.** OIIO 3.x
  reports it in interop-ID form (`ACEScg` → `lin_ap1_scene`).
- fps / timecode are **rarely in an EXR header** (common in MOV via ffprobe).

## NAS & copy

- Source and destination are usually **different volumes**, so
  hardlink/symlink modes normally fall back to a real copy.
- A **fixed shot folder structure** (the 2D/3D tree in `ProjectConfig`, a
  `StudioConfig` default at create time) is created when a shot is new or its
  destination folder is missing.
- **One person ingests a given delivery at a time.** The slot check has a
  TOCTOU window (check → copy) that we accept rather than lock.
- The `_pipeline/` folder under a project on the NAS is ours to write
  (`project_config.json`, the ingest ledger, later: locks, manifests). It is
  not a database of record — Kitsu is.

## Scale

- A delivery is tens to low-hundreds of media items; a sequence is tens to
  low-thousands of frames. Not "millions of files" scale — in-memory item
  lists and a single SQLite ledger are fine.
