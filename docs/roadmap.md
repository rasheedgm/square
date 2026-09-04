# Roadmap

Rough. Bullets only — scope and detail get decided when we pick one up.
Nothing here is committed.

The big picture — the whole studio pipeline, its layering, core services, and
the ~10 tools that ride on it — is in
[`pipeline_architecture.md`](pipeline_architecture.md). This file is just the
near-term task list.

## Pipeline core — build order (see architecture doc §12)

- **Phase A:** restructure (`restructure_plan.md`, folded in as the first
  commits) + the spine — `square_core/kitsu/` (single gazu access point +
  version tracking via `working_files`/`output_files` + non-interactive JWT
  auth), `PipelineContext` (`NeedsLogin`), config split
  (`StudioConfig` defaults → copied into per-project `ProjectConfig`),
  `paths/resolver` (owns all paths), `storage/transfer`+`layout`; port the
  ingest tool onto it (drop the session config snapshot)
- **Phase B:** project-setup tool — `services/projects` + `services/breakdown`
- **Phase C:** work/publish core + Nuke integration
- **Phase D:** review core + player (spike Zou annotation JSON here)
- **Phase E:** delivery core + send-to-client

## Ingest tool — near term

*(restructure, config split, dead-code delete, credential handling — now part of
Phase A above)*

- `resolve_bar` as a proper widget; polish the detail panel
- Undo UI (stack exists, no button yet beyond the toolbar)
- Multi-user safety: a lock file on the destination slot during copy
- Preview speed on real footage: OpenImageIO decode, optional GPU encode
  (nvenc/qsv), `-preset ultrafast` for review proxies — hardware dependent
- "Ingest same media to N shots" as an explicit action (duplicate a row)
- Package the tool for deployment (the deploy script exists, exercise it)

## Ingest tool — later

- Burn-in on proxies (frame counter / timecode / slate)
- Partial-overlap handling: ingest only the new frames of a sequence

## Other pipeline tools (wishlist detail; canonical list is architecture doc §11)

- **Nuke integration**: `SquareRead` / `SquareWrite` nodes (browse Kitsu,
  auto colorspace, enforced output paths, publish-to-Kitsu toggle)
- **Save-version manager**: major (`v001`) vs minor (`v001.01`) workfile
  versioning, in Nuke / Maya
- **Localize tool**: one-click NAS → local NVMe cache, transparent path
  remap, staleness check
- **Desktop review player**: EXR playback from NAS + Kitsu annotation /
  status publisher (pen, arrow, frame comments)
- **Vendor package builder**: collect plates + cameras + refs for a shot,
  export self-contained zip or cloud-sync per freelancer; auto-assign the
  Kitsu task to the vendor
- **Vendor ingest QC**: validate incoming vendor work (frame padding,
  colorspace, resolution) before merging back
- **Send-to-client tool**: per-client presets (naming, padding, colorspace,
  container), automated QC suite, transmittal manifest (CSV/PDF), Kitsu
  delivery log

## Cross-cutting

- Shared `square_core` matures as the second tool lands (media, kitsu,
  transfer, paths)
- CI: run the suite on PySide6, gate PRs
- A real dev Kitsu seed / fixture project for integration tests
