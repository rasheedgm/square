# Square Pipeline

The studio pipeline core for Square VFX — a framework-free Python package
(`square_core`) that every tool talks to, with CGWire Kitsu (`gazu`) as the one
production database behind a single access point.

## Layers

```
tools/*  ─▶  square_core (model · services · paths · storage · media · config)  ─▶  square_core/kitsu (the only gazu importer)
```

Tools ask for outcomes (`projects.create(...)`, `work.publish_output(...)`);
core guarantees the whole thing — Kitsu records **and** folders **and** config.

## square_core

| Package | |
|---|---|
| `model/` | value objects — entities, `Version`, `MediaInfo`, `Provenance`, `PathContext` |
| `config/` | `PipelineConfig` (per install) · `ProjectConfig` (per project, on the NAS) |
| `paths/` | `PathResolver` — pure `ProjectConfig` + `PathContext` → path string |
| `kitsu/` | `KitsuApi` · `auth` (per-user JWT cache) · `OfflineApi` |
| `storage/` | verified copy engine · folder-tree creation |
| `media/` | ffmpeg proxy · OIIO/ffprobe metadata · sequence scanner |
| `context.py` | `PipelineContext` / `ProjectContext` |
| `services/` | `projects` · `breakdown` · `work` · `review` |

## Quick start

```python
from square_core.context import PipelineContext
from square_core.services import projects, breakdown
from square_core.services.projects import ProjectSpec

pipeline = PipelineContext.connect()                       # reads studio_config.json
projects.create(pipeline, ProjectSpec(code="DEMO01", fps=24.0))

pctx = pipeline.project("DEMO01")
shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", frame_in=1001, frame_out=1096)
breakdown.build_task_grid(pctx, [shot], ["Ingest", "Comp"])
```

`PipelineContext.connect(offline=True)` for a no-Kitsu run.

## Config

Copy `studio_config.template.json` → `studio_config.json` (or point
`STUDIO_CONFIG_PATH` at it) and fill in your Kitsu host + NAS root(s).
Credentials are **not** in the file — each user logs in once
(`square_core.kitsu.auth.login`) and the JWT caches to the OS keyring or
`~/.square/`.

## Tests

```
env\Scripts\python.exe -m unittest discover -s tests
```

## Deploy to the studio NAS

```
python -m tools.pipeline_deploy.deploy --dest //NAS/pipeline
python -m tools.pipeline_deploy.deploy --dest //NAS/pipeline --rollback v0.1.0
```

Ships a versioned release (`releases/vX.Y.Z/` + a `current` junction flipped
atomically), builds a venv from `requirements.txt`, and writes one launcher
`.bat` per deployed tool plus `square_rollback.bat`.

`config/studio_config.json` is seeded on the first deploy and **never
overwritten** after that. Each deploy refreshes `studio_config.template.json`
alongside it and reports any keys the template added since; `--update-config`
adds just those (existing values untouched, a backup written).

## Docs

- [`docs/pipeline_architecture.md`](docs/pipeline_architecture.md) — layers, the single Kitsu access point, services, tool inventory, build order
- [`docs/config_and_paths.md`](docs/config_and_paths.md) — `ProjectConfig` + `PathResolver` spec
- [`docs/decisions.md`](docs/decisions.md) — locked design calls, with reasons
- [`docs/assumptions.md`](docs/assumptions.md) · [`docs/roadmap.md`](docs/roadmap.md) · [`docs/restructure_plan.md`](docs/restructure_plan.md)
- [`docs/ingest_tool_design.md`](docs/ingest_tool_design.md) — the ingest tool (pilot; lives on the `ingest_tools` branch, ported onto this core next)

## Branches

- `master` — the pipeline core
- `ingest_tools` — the ingest tool (pilot), pending its port onto this core
