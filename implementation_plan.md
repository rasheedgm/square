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
│   ├── config.py               # Studio settings, Kitsu config, default folder templates
│   ├── kitsu_client.py         # Gazu API wrapper (connect, create project/seq/shot/task, add preview)
│   ├── plate_scanner.py        # File & sequence scanner (smart regex for SQ/SH/PL & frame ranges)
│   ├── metadata_extractor.py   # Media inspector (resolution, FPS, colorspace, timecode)
│   ├── nas_manager.py          # Directory structure creator & checksum file copier
│   └── proxy_generator.py      # FFmpeg slate & low-res MP4 preview encoder
└── tools/                      # Studio desktop applications
    └── ingest_tool/            # PyQt Ingestion Application
        ├── __init__.py
        ├── main.py             # App entry point
        ├── ui_main.py          # Main Window layout & controllers
        ├── widgets/
        │   ├── scanner_widget.py   # Drag-and-drop & path selector
        │   ├── table_widget.py     # Interactive grid view with inline metadata editing
        │   └── progress_dialog.py  # Real-time ingestion progress & logger
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
  - Uses smart regex matching to infer Sequence (`SQ010`), Shot (`SH0100`), Plate (`PL01`).
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
  - **Path Selector & Drag/Drop Area:** Pick incoming plate folder.
  - **Scan Button:** Triggers `plate_scanner` in background thread (`QThread`) with loading spinner.
  - **Interactive Table View:** Editable columns for Sequence Code, Shot Code, Plate Name, Frame Range, FPS, Resolution, and Colorspace.
  - **Validation Badges:** Visual indicators for warning (e.g. missing frames, unknown shot pattern).
  - **Kitsu & NAS Settings Panel:** Choose target Kitsu Project and NAS Destination folder.
  - **Execute Ingest Button:** Launches multi-step background worker (Creates Kitsu Shots -> Creates NAS Folders -> Copies Files -> Encodes Proxies -> Uploads to Kitsu).
  - **Progress Modal:** Real-time progress bar, item status checklist, and detailed execution log.

---

## Verification Plan

### Automated & Unit Verification
1. **Conda Env Test:** Verify Conda environment creation at `d:/projects/square/env` and python package imports (`PyQt6`, `gazu`, `xxhash`).
2. **Scanner Unit Test:** Run `plate_scanner` against sample frame sequence files and verify detection of frame range, sequence code, shot code.
3. **NAS Creator Test:** Verify creation of directory tree and file copy with checksum verification.
4. **Proxy Generator Test:** Test `ffmpeg` video rendering with frame burn-in.

### Manual Verification
1. Launch `tools/ingest_tool/main.py` using local Conda environment python.
2. Test folder drag-and-drop & scanning in PyQt UI.
3. Test inline table editing of sequence/shot codes.
4. Execute ingestion flow in mock/dry-run mode and verify rendered MP4 proxies and generated folder structures.
