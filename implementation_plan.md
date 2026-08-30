# Implementation Plan - Phase 1: Core VFX Library & Ingest Tool

Build the core Python package (`square_core`) and PyQt6 standalone desktop application (`Ingest Tool`) for **Square VFX Studio**. 
This tool will scan incoming plate folders, extract media metadata, allow interactive editing in a modern dark-mode UI, automatically create Sequences/Shots/Tasks in **Kitsu**, set up structured NAS project folders, copy high-res plates with checksum verification, and generate burnt-in low-res previews for Kitsu.

## User Review Required

> [!IMPORTANT]
> **Conda Environment Location:** We will create an isolated local Conda environment at `d:/projects/square/env` (Python 3.11) containing `Qt.py` (VFX industry standard Qt abstraction layer supporting PyQt6/PySide6/PySide2), `PyQt6` (or `PySide6`), `gazu` (Kitsu Python API), `requests`, `ffmpeg-python`, `xxhash`, and essential pipeline utilities. Using `Qt.py` ensures seamless compatibility across standalone tools and DCC integrations like Nuke and Maya.

> [!NOTE]
> **Live Kitsu & NAS Testing Mode:** The `kitsu_client` and `nas_manager` will include a "Dry Run / Mock Mode" configuration. This allows testing plate scanning, UI interaction, local directory generation, and preview rendering on local drives before connecting to a live studio Kitsu server and NAS storage.

---

## Proposed Architecture & File Structure

```
d:/projects/square/
├── .gitignore
├── ROADMAP.md
├── environment.yml             # Conda environment definition file
├── requirements.txt            # Pip requirements file
├── square_core/                # Core studio library
│   ├── __init__.py
│   ├── config.py               # Studio settings, Kitsu config, default folder templates, Ingest Presets
│   ├── kitsu_client.py         # Gazu API wrapper (connect, create project/seq/shot/task, add preview)
│   ├── plate_scanner.py        # Groups raw files into sequences/videos (frame ranges, missing frames)
│   ├── folder_mapper.py        # Applies a root's saved Path Patterns + manual media-type tags to scanned items
│   ├── path_pattern.py         # Build-by-example full-path tagging engine (see below)
│   ├── token_parser.py         # Chip-splitting primitives shared by the Path Pattern builder UI
│   ├── metadata_extractor.py   # Media inspector (resolution, FPS, colorspace, timecode)
│   ├── nas_manager.py          # Directory structure creator & checksum file copier
│   └── proxy_generator.py      # FFmpeg slate & low-res MP4 preview encoder
└── tools/                      # Studio desktop applications
    └── ingest_tool/            # PyQt Ingestion Application
        ├── __init__.py
        ├── main.py             # App entry point
        ├── ui_main.py          # Main Window layout & controllers
        ├── widgets/
        │   ├── folder_tree_widget.py    # Incoming folder tree; launches Path Pattern tagging on a leaf item
        │   ├── path_pattern_dialog.py   # Chip-based Path Pattern builder + manager dialogs
        │   ├── table_widget.py          # Interactive grid view: review, batch-edit, per-row ingest progress
        │   ├── task_selection_dialog.py # Configurable Kitsu task-type selection before ingest
        │   ├── settings_dialog.py       # Studio config editor (NAS, copy engine, tasks, preview types)
        │   ├── results_dialog.py        # Post-ingest / dry-run results summary
        │   ├── progress_dialog.py       # Real-time ingestion progress & logger
        │   └── crash_dialog.py          # Unhandled-exception dialog
        └── style.qss           # Modern dark-mode QSS stylesheet
```

---

## Key Modules & Responsibilities

### 1. `environment.yml`
- Python 3.11 base
- Packages: `pyqt6`, `gazu`, `requests`, `pillow`, `xxhash`, `ffmpeg-python`, `pyyaml`

### 2. `square_core` Package
- **`config.py`**: Central settings for Kitsu URL, default credentials (or env vars), NAS root path, shot folder template, default task types (`Prep`, `Roto`, `Comp`, `3D`).
- **`kitsu_client.py`**: Interacts with Kitsu via `gazu`.
  - Connect/Login to Kitsu.
  - Fetch/Create Project, Sequences, Shots, and Tasks.
  - Upload low-res MP4 preview proxies to Kitsu task comments.
  - Includes offline/mock fallback for testing without Kitsu server.
- **`plate_scanner.py`**: Scans directory recursively or single folder.
  - Identifies image sequences (`.exr`, `.dpx`, `.png`, `.jpg`, `.tif`) and video files (`.mov`, `.mp4`).
  - Groups frame files into sequence objects with Start Frame, End Frame, and missing frame detection.
  - Deliberately does **not** guess Sequence/Shot/Media Type from naming conventions -- there is no
    universal convention (prefix or none, numeric or with letters, one info type per folder or several
    bundled together) for a hardcoded regex to assume. That's `folder_mapper.py` and
    `path_pattern.py`'s job instead.
- **`path_pattern.py`**: The tagging engine. A studio tags one real example file's whole path (every
  folder plus the filename), piece by piece -- via the Path Pattern builder in the UI -- and that
  becomes a reusable template string (e.g. `<sequence>/<shot>/<media_type>/<sequence>_<shot>_<media_name>.####.<extension>`)
  matched against every other file under the same root. Five placeholder names are canonical and feed
  Sequence/Shot/Media Type/Media Name/Version directly; any other name (camera, shoot date, colorspace, ...)
  is carried as free-form metadata instead of being forced into one of those five. Untagged text is literal
  by default and must match exactly (no silent wildcarding); `*` is an explicit, user-inserted wildcard.
- **`folder_mapper.py`**: Holds the ordered list of Path Patterns saved for one incoming root (tried in
  turn, first match wins -- so a delivery with more than one shape just gets a second pattern) plus a
  lightweight manual per-item media-type override, and applies both on top of whatever `plate_scanner.py`
  discovered to build the final `IngestSequenceItem` list the review table shows.
- **`metadata_extractor.py`**: Reads headers of media files.
  - Extracts width, height, FPS, timecode, channel count.
  - Supports fallback to `ffprobe` / `Pillow` for deep metadata inspection.
- **`nas_manager.py`**: Manages filesystem operations.
  - Generates NAS hierarchy: `{nas_root}/{project}/shots/{seq}/{shot}/plates/{plate}/v001/`
  - High-speed copy with progress updates and xxHash/MD5 checksum verification.
- **`proxy_generator.py`**: Uses `ffmpeg` to generate low-res MP4 files (H.264, Rec.709, 1080p) with frame number & timecode burn-in overlay.

### 3. `tools/ingest_tool` (PyQt UI)
- **Design:** Modern dark-mode UI with sleek color palette (slate dark background, vibrant accent colors, rounded buttons, custom tables).
- **Features:**
  - **Folder Tree & Drag/Drop:** Browse to (or drop) the incoming media root; sequences collapse to one row.
  - **Path Pattern Tagging:** Right-click a real leaf item (sequence/video/image) to build a full-path
    template by tagging its pieces -- drilling into a bundled segment (usually the filename) for its own
    sub-chips, marking a piece a wildcard, or leaving it literal. Saved patterns are tried in order against
    every file under the root; a Patterns manager reorders/edits/removes them. Whole ordered pattern lists
    save/load as reusable Ingest Presets.
  - **Interactive Table View:** Editable Sequence/Shot/Media Type/Media Name columns (individually, in
    batch, or via a rename-template with wildcards), a read-only Extra Tags column for anything a pattern
    captured outside the five built-in fields, per-row live ingest progress, and version/conflict handling
    against Kitsu.
  - **Kitsu & NAS Settings Panel:** Choose target Kitsu Project, NAS Destination, copy engine (parallel
    copy/hardlink/symlink), default shot tasks, and which media types get a preview generated.
  - **Task Selection:** Pick which Kitsu task types to create for this batch before ingest starts.
  - **Execute Ingest Button:** Launches multi-step background worker (Creates Kitsu Shots -> Creates NAS Folders -> Copies Files -> Encodes Proxies -> Uploads to Kitsu), with a true non-destructive Dry-Run mode.
  - **Progress Modal:** Real-time progress bar, item status checklist, and detailed execution log.

---

## Verification Plan

### Automated & Unit Verification
1. **Conda Env Test:** Verify Conda environment creation at `d:/projects/square/env` and python package imports (`PyQt6`, `gazu`, `xxhash`).
2. **Scanner Unit Test:** Run `plate_scanner` against sample frame sequence files and verify frame-range/missing-frame detection; verify a saved Path Pattern correctly tags sequence/shot/media fields on top.
3. **NAS Creator Test:** Verify creation of directory tree and file copy with checksum verification.
4. **Proxy Generator Test:** Test `ffmpeg` video rendering with frame burn-in.

### Manual Verification
1. Launch `tools/ingest_tool/main.py` using local Conda environment python.
2. Test folder drag-and-drop & scanning in PyQt UI.
3. Test inline table editing of sequence/shot codes.
4. Execute ingestion flow in mock/dry-run mode and verify rendered MP4 proxies and generated folder structures.
