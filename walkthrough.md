# Walkthrough - Phase 1: Core Library & Ingest Tool Completed

**Studio:** Square VFX  
**Status:** Completed & Verified  
**Git Commit:** `2314fe4` (`Phase 1: Implement square_core package, PyQt6 Ingest Tool with Qt.py, and unit test suite`)

> **Note:** This is a point-in-time record of the initial Phase 1 build. The
> tagging approach described below (regex-guessed Sequence/Shot/Plate at
> scan time) and a few other details have since been reworked -- see
> `implementation_plan.md` for the current architecture. Corrected below
> only where this doc asserted something that's no longer true; left as a
> historical record otherwise.

---

## Accomplishments

### 1. Isolated Conda Environment (`d:/projects/square/env`)
- Built dedicated Python 3.11 environment containing:
  - **`Qt.py`**: VFX industry standard Qt abstraction layer.
  - **`PyQt6`**: Desktop UI engine.
  - **`gazu`**: CGWire Kitsu Python API.
  - **`xxhash`**: High-performance checksum engine for file verification.
  - **`ffmpeg-python` & `Pillow`**: Media inspection and proxy preview renderer.

### 2. Core VFX Library (`square_core`)
- **`config.py`**: Studio configuration manager with NAS paths (`X:/projects`), Kitsu API endpoints, and folder templates.
- **`kitsu_client.py`**: Kitsu wrapper with gazu API + Mock/Dry-Run support for creating projects, sequences, shots, default tasks (`Prep`, `Roto`, `Matchmove`, `3D`, `Comp`), and uploading preview MP4s.
- **`plate_scanner.py`**: Sequence & video scanner -- groups frame files into sequence objects with start/end frame parsing and missing frame detection. Sequence/Shot/Media Name are no longer guessed here via regex; that's `folder_mapper.py` + `path_pattern.py`'s job now (build-by-example Path Pattern tagging, not a hardcoded naming convention).
- **`metadata_extractor.py`**: Reads width, height, resolution, FPS, timecode, and color space metadata from headers.
- **`nas_manager.py`**: Generates NAS directory structures (`{nas_root}/{project}/shots/{seq}/{shot}/plates/{media_name}/v001/`) and executes file copies with **xxHash** checksum verification.
- **`proxy_generator.py`**: Low-res H.264 MP4 proxy generator (static slate image fallback when the source can't be decoded). No burnt-in frame counter/timecode overlay is actually implemented.

### 3. Desktop Application (`tools/ingest_tool`)
- **Built with `Qt.py`** for full cross-binding portability.
- **Dark Studio Theme (`style.qss`)**: Slate dark theme with rounded cards, custom headers, and vibrant badges.
- **Folder Tree Widget**: Drag-and-drop zone + file browser for incoming media folders, with Path Pattern tagging on real leaf items (current tagging workflow documented in `implementation_plan.md`).
- **Interactive Table Widget**: Editable grid showing detected sequences, shots, media names, frame ranges, missing frame alerts, FPS, and colorspace.
- **Progress Modal & Threaded Worker**: Multi-step background execution keeping the UI smooth and responsive.

---

## Verification Results

### Automated Unit Tests (`d:/projects/square/env/python.exe -m unittest tests/test_ingest.py`)
```
....
----------------------------------------------------------------------
Ran 4 tests in 0.080s

OK
[Test] Scanner discovered 3 items:
  - MYPROJ_SQ010_SH0100_PL01: SQ010 | SH0100 | PL01 (1001-1010 (10 frames))
  - MYPROJ_SQ020_SH0200_PL02: SQ020 | SH0200 | PL02 (1001-1005 (4 frames - missing frame 1003 detected))
  - BRANDX_SH0050_MAIN.mov: SQ010 | SH0050 | MAIN (1 (Video File))

[Test] Verified xxHash checksum match: 70f2ec0fa1358def
```

---

## How to Run the Ingest Tool

To launch the standalone PyQt Ingest Tool:
```powershell
d:\projects\square\env\python.exe d:\projects\square\tools\ingest_tool\main.py
```
