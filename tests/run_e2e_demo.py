import os
import sys
import shutil
from pathlib import Path

# Force UTF-8 stdout encoding for Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add project root to sys.path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient
from square_core.plate_scanner import PlateScanner
from square_core.nas_manager import NASManager
from square_core.proxy_generator import ProxyGenerator
from tests.create_sample_plates import create_sample_media

def run_e2e_demo():
    print("=" * 75)
    print("      SQUARE VFX INGESTION PIPELINE - SAMPLE FOOTAGE TEST DEMO")
    print("=" * 75)

    # 1. Create sample incoming plates
    create_sample_media()
    incoming_dir = Path("d:/projects/square/test_data/incoming_plates")
    nas_root = Path("d:/projects/square/test_data/studio_nas")

    if os.path.exists(nas_root):
        shutil.rmtree(nas_root)

    # 2. Scan Incoming Folder
    print(f"\n[Step 1] Scanning incoming directory: {incoming_dir}")
    scanner = PlateScanner(incoming_dir)
    items = scanner.scan()
    print(f"-> Discovered {len(items)} media items:\n")

    for idx, item in enumerate(items, 1):
        warn_str = f" [WARNING: Missing frames {item.missing_frames}]" if item.has_warnings else " [OK: Frame sequence complete]"
        print(f"   [{idx}] Item Name: {item.name}")
        print(f"       Sequence: {item.sequence_code} | Shot: {item.shot_code} | Plate: {item.plate_name}")
        print(f"       Range: {item.frame_range_str} | FPS: {item.fps} | Color: {item.colorspace}{warn_str}")

    # 3. Kitsu DB Connection & Setup
    print("\n[Step 2] Connecting to Kitsu Project Management Backend...")
    kitsu = KitsuClient(dry_run=True)
    kitsu.connect()
    projects = kitsu.get_all_projects()
    active_project = projects[0]  # "Feature Film Alpha"
    print(f"-> Active Target Kitsu Project: '{active_project['name']}' (Code: {active_project['code']})")

    # 4. NAS Manager & Proxy Generator Setup
    nas = NASManager(nas_root=str(nas_root), dry_run=False)
    proxy_gen = ProxyGenerator(output_dir=nas_root / "temp_proxies", dry_run=True)

    # 5. Process Ingestion for each item
    print("\n[Step 3] Executing Ingestion Flow for Discovered Plates...")

    for item in items:
        print(f"\n--- Ingesting: {item.sequence_code} / {item.shot_code} / {item.plate_name} ---")

        # A. Kitsu Sync
        seq_obj = kitsu.get_or_create_sequence(active_project["id"], item.sequence_code)
        shot_obj = kitsu.get_or_create_shot(
            active_project["id"], seq_obj["id"], item.shot_code,
            frame_in=item.start_frame, frame_out=item.end_frame, fps=item.fps
        )
        tasks = kitsu.create_default_tasks(shot_obj["id"])
        print(f"  + Kitsu DB Synced: Sequence '{item.sequence_code}', Shot '{item.shot_code}', Tasks: {[t['name'] for t in tasks]}")

        # B. NAS Directory Creation & File Transfer
        dest_dir = nas.get_dest_dir(active_project["code"], item.sequence_code, item.shot_code, item.plate_name)
        nas.create_shot_structure(dest_dir)
        print(f"  + NAS Directory Created: {dest_dir}")

        copied_files = nas.copy_sequence(item, dest_dir)
        sample_hash = nas.calculate_checksum(copied_files[0])
        print(f"  + Copied {len(copied_files)} files to NAS with xxHash Checksum Verification (Hash: {sample_hash})")

        # C. Proxy Video Generation & Kitsu Upload
        proxy_path = proxy_gen.generate_proxy(item)
        print(f"  + Generated Low-Res Preview Proxy: {proxy_path}")

        if tasks:
            preview_res = kitsu.upload_preview_proxy(tasks[0]["id"], proxy_path)
            print(f"  + Uploaded Preview Proxy to Kitsu task '{tasks[0]['name']}'")

    print("\n" + "=" * 75)
    print("      INSPECTING GENERATED NAS STRUCTURE ON DISK:")
    print("=" * 75)
    for root, dirs, files in os.walk(nas_root):
        rel_path = os.path.relpath(root, nas_root)
        if rel_path == ".":
            continue
        print(f" [DIR] {rel_path} ({len(files)} files)")

    print("\n" + "=" * 75)
    print("      SAMPLE FOOTAGE TEST COMPLETED SUCCESSFULLY!")
    print("=" * 75)

if __name__ == "__main__":
    run_e2e_demo()
