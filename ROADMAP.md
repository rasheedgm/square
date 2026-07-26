# Square VFX Pipeline Roadmap & Technical Architecture
**Studio:** Square VFX (Feature Film, Commercials, 3D, Compositing, Prep, Roto)  
**Infrastructure:** Windows Workstations, Local NAS Storage, Remote/Freelance Artists  
**Pipeline Database / Management:** CGWire Kitsu (`gazu` Python API)  
**UI Framework:** PyQt6 / PySide6  

---

## Executive Summary & Architecture Overview

Square VFX operates with a hybrid team: in-house Windows workstations connected to high-speed NAS storage, alongside remote freelancers working independently. 

To streamline production without heavy overhead, **Kitsu** serves as the central source of truth for:
- Project, Sequence, Shot, and Asset structures
- Task assignments and status tracking (To Do, In Progress, Pending Review, Approved, etc.)
- Task versions, low-res preview media, and client/internal comments
- Custom metadata attributes (framerate, resolution, handles, colorspace)

The pipeline tools will be built around a lightweight Python core library (`square_core`) with **PyQt6/PySide6** desktop interfaces and integrations for DCCs like **Foundry Nuke**, **Autodesk Maya/3ds Max**, and standalone helper utilities.

---

## Pipeline Component Breakdown

```
                       +-----------------------------------+
                       |         Kitsu Server (DB)          |
                       +-----------------------------------+
                                    ^         ^
             Metadata & Previews    |         |  Status & Comments
                                    v         v
+------------------+       +-------------------+       +--------------------+
|  Ingest Tool     | ----> | NAS File System   | <---- |  Send to Client    |
| (PyQt Standalone)|       | (Folder Structure)|       | (QC & Delivery)    |
+------------------+       +-------------------+       +--------------------+
                                    ^
                                    |
            Read / Write / Localize |
                                    v
                       +-------------------+
                       |  Artist UI / Nuke |
                       | (Read/Write/Save) |
                       +-------------------+
```

---

## Phase 1: Ingestion & Environment Setup (Plate Ingest Tool)

### 1.1 Key Features & Workflow
- **Folder Scanner:** Drag & drop or browse incoming raw plates (EXR, DPX, ProRes, RED/ARRI raw).
- **Smart Sequence Parsing:** Regex engine to auto-detect Sequence (`SQ010`), Shot (`SH0100`), Plate Code (`PL01`), and Frame Range (`1001-1150`).
- **Metadata Extraction:** Extract EXR/DPX headers using `OpenImageIO` / `ffprobe` / `PyOpenColorIO`:
  - Resolution, Aspect Ratio, Frame Rate (FPS)
  - Color Space (ACEScg, Rec.709, ARRI LogC)
  - Timecode & Frame Range
- **Interactive PyQt UI:**
  - Table grid showing detected sequences, shots, and plate details.
  - Inline editing for overriding sequence/shot codes, frame range, or metadata before commit.
  - Validation indicators (flagging missing frames or unsupported formats).
- **Execution & Ingestion Pipeline:**
  1. **Kitsu Synchronization:** Create Sequence, Shot, and default Tasks (Prep, Roto, Comp, 3D) via `gazu.shot.new_shot()`.
  2. **NAS Directory Generator:** Create standardized NAS workspace structure:
     ```
     X:/projects/{project_name}/
       ├── shots/
       │   └── {seq}/
       │       └── {shot}/
       │           ├── plates/{plate_name}/
       │           │   └── v001/{shot}_{plate_name}_v001.####.exr
       │           ├── work/
       │           │   ├── comp/
       │           │   ├── roto/
       │           │   └── prep/
       │           └── renders/
       │               └── comp/
       └── assets/
     ```
  3. **High-Speed Copy & Verification:** Copy plates to designated NAS folders with xxHash / MD5 checksum verification.
  4. **Low-Res Proxy Generation:** Generate H.264/MP4 preview proxies using `ffmpeg` with burnt-in frame numbers and TC (timecode).
  5. **Kitsu Preview Attachment:** Attach low-res proxy preview to Kitsu shot entry for instant web browser review.

---

## Phase 2: Internal Artist Ecosystem (DCC & Nuke Integration)

### 2.1 Nuke Read & Write Nodes
- **`SquareRead` Node:**
  - Browse shots/assets directly from Kitsu without manual file navigation.
  - Automatically sets color space (OCIO) based on Kitsu/Ingest metadata.
  - Displays version history and allows 1-click update to latest plate or render.
- **`SquareWrite` Node:**
  - Enforces studio output paths and naming conventions automatically:
    `{project}/shots/{seq}/{shot}/renders/{task_type}/v{version}/{shot}_{task_type}_v{version}.####.exr`
  - Auto-injects burnt-in slate metadata (Shot, Artist, Frame Range, Task).
  - Integrated "Publish to Kitsu" toggle: renders EXR to NAS + automatically generates low-res MP4 preview and posts a new version to Kitsu.

### 2.2 Versioning & Workfile Management
- **Kitsu Versioning Concept:**
  - **Workfile Versions (Local/Artist):** Minor versions (e.g. `v001.01`, `v001.02`) stored in work folders for iterative work.
  - **Published Versions (Kitsu/Studio):** Major versions (e.g. `v001`, `v002`) triggered when artist publishes work for lead/supervisor review.
- **Save Script Dialog (PyQt):**
  - Integrated into Nuke / Maya (`File -> Save Workfile...`).
  - Auto-populates current assigned shot/task from Kitsu.
  - Controls major (`v001`) vs minor (`.01`) version numbering.

### 2.3 One-Click Asset & Plate Localization
- **Problem:** Remote freelancers or artists working over slow NAS connections experience playback stutter on heavy EXR sequences.
- **Solution:**
  - One-click **Localize Tool** in PyQt artist panel / Nuke integration.
  - Copies active plates/renders from NAS to fast local NVMe cache (`C:/cache/square/{project}/...`).
  - Re-maps Nuke Read paths to local cache transparently.
  - Sync check: detects if NAS plate has been updated and prompts for cache refresh.

---

## Phase 3: Review & Annotation System

### 3.1 Web & Desktop Review Workflow
- **Kitsu Built-in Web Review:**
  - Kitsu provides native web-based video playback, side-by-side / wipe comparison, and brush annotations out-of-the-box.
- **Desktop Review Player (PyQt + OpenRV / custom MPV player):**
  - Desktop UI for playing uncompressed EXR sequences directly from NAS.
  - Syncs directly with Kitsu API (`gazu.task.add_comment()`):
    - Draw annotations on frames (pen, arrow, text, highlight).
    - Post frame-specific comments directly to the assigned artist’s Kitsu task.
    - Change Task status (e.g., `In Progress` -> `Pending Review` -> `Approved` / `Retake`).

---

## Phase 4: Vendor & Freelancer Management

### 4.1 Packaging & Dispatch Tool
- **Shot Package Creator (PyQt):**
  - Select shot(s) and target tasks (e.g., Outsource Roto / Prep).
  - Automatically collects required plates, frame ranges, 3D tracking cameras, and reference MP4s.
  - Options:
    - **Self-Contained Zip:** Exports bundle with relative path structure for offline freelancers.
    - **Cloud Sync:** Direct sync to Dropbox / Nextcloud / AWS S3 folder mapped per freelancer.
- **Kitsu Vendor Entry:**
  - Automatically updates shot task assignment in Kitsu to Vendor/Freelancer account.
  - Changes status to `Vendor In Progress`.
  - Sets target delivery date and logs transmittal receipt.

### 4.2 Vendor Ingest & QC Validation
- Incoming vendor work validator tool to check frame padding, color space, and resolution before merging back into primary NAS storage and Kitsu DB.

---

## Phase 5: Client Delivery Tool ("Send to Client")

### 5.1 Key Features & Workflow
- **Client Presets & Rules Manager:**
  - Configurable profiles per client (e.g. `Client_Marvel`, `Client_Netflix`, `Client_Commercial`).
  - Standardizes client-specific folder structure, file naming patterns, frame padding, and color space conversions (e.g. Rec.709 MOV vs. ACES EXR).
- **Automated QC & Validation Suite:**
  - **Frame Integrity:** Check for missing frames, corrupted EXR headers, zero-byte files.
  - **Resolution & Aspect Ratio Check:** Verify target delivery resolution (e.g. 3840x2160 UHD).
  - **Audio Sync Check:** Verify TC and audio alignment where applicable.
  - **Slate & Watermark Check:** Ensure correct client slate metadata.
- **Delivery Execution:**
  - Copy validated outputs to `X:/projects/{project}/deliveries/{client_date_batch}/`.
  - Generate PDF / CSV Transmittal Manifest detailing delivered shots, frames, versions, and revision notes.
  - **Kitsu Synchronization:** Update task status to `Sent to Client` / `Delivered`, upload low-res MP4 to Kitsu client delivery log.

---

## Suggested Implementation Phases & Roadmap Timeline

| Phase | Module | Target Features | Complexity |
|:---|:---|:---|:---|
| **Phase 1** | **Core & Ingest** | `square_core`, Kitsu connection (`gazu`), Ingest PyQt UI, NAS folder creation, low-res preview creation | Medium |
| **Phase 2** | **Artist & Nuke Pipeline** | Nuke Read/Write custom knobs, Save Version Manager, 1-Click NAS-to-Local Cache | Medium-High |
| **Phase 3** | **Review System** | Kitsu Web Review integration + PyQt Desktop Review & Annotation Publisher | Medium |
| **Phase 4** | **Vendor & Outsource** | Package builder (Zip/Cloud), Kitsu vendor task assignment, Vendor ingest validator | Medium |
| **Phase 5** | **Send to Client** | Client presets parser, Automated QC engine, Transmittal CSV/PDF report, Kitsu Delivery log | High |

---

## Next Steps & Discussion Points
1. **DCC Priority:** Is Foundry Nuke the main application to start with, or do we also need Maya/3ds Max / Blender tools early on?
2. **Kitsu Hosting:** Will Kitsu be self-hosted on a local server/cloud instance or hosted via CGWire Cloud?
3. **Local Storage Drive Mapping:** What is the standard drive letter for NAS storage across artist workstations (e.g., `X:/` or `Z:/`)?
