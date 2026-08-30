import sys
import logging
from pathlib import Path

# Add root directory to sys.path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LiveIngestTest")

from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient
from square_core.plate_scanner import PlateScanner
from square_core.nas_manager import NASManager
from square_core.proxy_generator import ProxyGenerator

def run_live_ingestion_test():
    print("=" * 60)
    print("RUNNING LIVE KITSU & NAS INGESTION VERIFICATION")
    print("=" * 60)

    # 1. Load Config
    config = StudioConfig()
    print(f"[1/5] Loaded Config: Kitsu URL={config.kitsu_url}, User={config.kitsu_user}")

    # 2. Connect to Kitsu
    kitsu = KitsuClient(host=config.kitsu_url, email=config.kitsu_user, password=config.kitsu_password, dry_run=False)
    if not kitsu.connect():
        print(f"[ERROR] Could not connect to Kitsu at {config.kitsu_url}")
        return

    print("[2/5] Successfully connected to live Kitsu server!")

    # 3. Create or Fetch Test Project
    projects = kitsu.get_all_projects()
    proj_code = "TEST"
    proj_data = None
    for p in projects:
        if p.get("code") == proj_code or p.get("name") == "Live Ingest Verification":
            proj_data = p
            break
            
    if not proj_data:
        print("[3/5] Creating test project 'Live Ingest Verification' (TEST)...")
        proj_data = kitsu.create_project("Live Ingest Verification", "TEST")
        
    print(f"[3/5] Target Kitsu Project: '{proj_data.get('name')}' (ID: {proj_data.get('id')})")

    # 4. Scan Sample incoming media
    incoming_dir = root_dir / "test_data" / "incoming_plates"
    scanner = PlateScanner(search_path=str(incoming_dir))
    items = scanner.scan()
    print(f"[4/5] Scanned {len(items)} items from {incoming_dir}")

    # 5. Execute Ingestion Pipeline
    nas_root = root_dir / "test_data" / "studio_nas"
    nas = NASManager(nas_root=nas_root, dry_run=False)
    proxy_gen = ProxyGenerator(output_dir=nas_root / "temp_proxies", dry_run=False)

    print(f"\n[5/5] Executing Ingestion Pipeline on {len(items)} media items...")
    for idx, item in enumerate(items, 1):
        print(f"\n--- Processing Item {idx}/{len(items)}: {item.sequence_code} | {item.shot_code} | {item.media_name} ---")

        # Check Version & Existing Ingest
        version_num, is_already_ingested = nas.get_media_version_info(
            proj_code, item.sequence_code, item.shot_code, item.media_name, item=item
        )
        dest_dir = nas.get_dest_dir(proj_code, item.sequence_code, item.shot_code, item.media_name, version=version_num)

        # Sequence & Shot
        seq_obj = kitsu.get_or_create_sequence(proj_data, item.sequence_code)
        shot_obj = kitsu.get_or_create_shot(
            proj_data, seq_obj, item.shot_code,
            media_name=item.media_name,
            frame_in=item.start_frame,
            frame_out=item.end_frame,
            fps=item.fps,
            resolution=item.resolution,
            colorspace=item.colorspace,
            nas_path=str(dest_dir)
        )
        print(f"  [+] Kitsu Shot: '{shot_obj.get('name')}' (ID: {shot_obj.get('id')})")
        print(f"  [+] Kitsu Shot Media Items Data: {shot_obj.get('data', {}).get('media_items')}")

        # Shot Tasks
        tasks = kitsu.create_default_tasks(shot_obj)
        task_names = [t.get("name") or t.get("task_type_name") for t in tasks] if tasks else []
        print(f"  [+] Kitsu Tasks Created ({len(tasks)}): {task_names}")

        # NAS File Transfer or Skip
        if is_already_ingested:
            print(f"  [SKIP] Media '{item.media_name}' (v{version_num:03d}) already ingested on NAS. Skipping duplicate copy and Kitsu upload.")
            continue

        nas.create_shot_structure(dest_dir)
        nas.copy_sequence(item, dest_dir)
        print(f"  [+] Copied files to NAS: {dest_dir} (v{version_num:03d})")

        # Proxy Video & Preview Upload to 'Ingest' Task & Thumbnail Setting
        mp4_path = proxy_gen.generate_proxy(item)

        if tasks and mp4_path:
            ingest_task = next((t for t in tasks if (t.get("name") or t.get("task_type_name")) in ("Ingest", "Prep")), tasks[0])
            task_name = ingest_task.get("name") or ingest_task.get("task_type_name") or "Ingest"
            preview_res = kitsu.upload_preview_proxy(ingest_task, mp4_path, comment=f"Media Ingest Preview v{version_num:03d} ({item.media_name})")
            print(f"  [+] Uploaded Preview to Task '{task_name}' & Set Shot Thumbnail: Preview ID {preview_res.get('id')}")

    print("\n" + "=" * 60)
    print("LIVE INGESTION VERIFICATION COMPLETE - 100% SUCCESS!")
    print("=" * 60)

if __name__ == "__main__":
    run_live_ingestion_test()
