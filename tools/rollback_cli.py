"""
Interactive Version Switcher / Rollback Tool for Square VFX Studio Pipeline
"""

import os
import sys
import json
import shutil
from pathlib import Path


def run_interactive_rollback(nas_root_path=None):
    if not nas_root_path:
        nas_root_path = os.environ.get("STUDIO_PIPELINE_ROOT", "//NAS/pipeline")

    nas_root = Path(nas_root_path).resolve()
    releases_dir = nas_root / "releases"
    current_junction = nas_root / "current"

    if not releases_dir.exists():
        print(f"[ERROR] Releases directory does not exist at {releases_dir}")
        return

    # Scan all vX.Y.Z releases
    releases = [d for d in releases_dir.iterdir() if d.is_dir() and d.name.startswith("v")]
    releases.sort(key=lambda d: d.name)

    if not releases:
        print(f"[ERROR] No release versions found under {releases_dir}")
        return

    # Determine current active release target
    current_target = None
    if current_junction.is_symlink():
        try:
            current_target = current_junction.readlink().name
        except Exception:
            pass
    elif (current_junction / "release_info.json").exists():
        try:
            data = json.loads((current_junction / "release_info.json").read_text(encoding="utf-8"))
            current_target = f"v{data.get('version')}"
        except Exception:
            pass

    print("======================================================================")
    print("[Rollback] Square VFX Pipeline — Interactive Version Switcher")
    print("======================================================================")
    print(f"Studio Pipeline Root: {nas_root}")
    if current_target:
        print(f"Active Production Version: {current_target}")
    print("\nAvailable Releases on NAS:")

    for idx, rel in enumerate(releases, 1):
        rel_info = ""
        info_file = rel / "release_info.json"
        if info_file.exists():
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
                dep_time = data.get("deployed_at", "")[:19].replace("T", " ")
                rel_info = f" (Deployed: {dep_time})"
            except Exception:
                pass

        tag_marker = " [CURRENT ACTIVE]" if rel.name == current_target else ""
        print(f"  [{idx}] {rel.name}{rel_info}{tag_marker}")

    print("======================================================================")
    try:
        choice = input(f"\nSelect a version number to activate (1-{len(releases)}) or 'q' to cancel: ").strip()
    except EOFError:
        print("Non-interactive mode detected. Exiting.")
        return

    if choice.lower() in ("q", "quit", "exit", ""):
        print("Rollback cancelled.")
        return

    if not choice.isdigit() or not (1 <= int(choice) <= len(releases)):
        print("Invalid choice. Operation cancelled.")
        return

    selected_release = releases[int(choice) - 1]
    selected_version = selected_release.name

    print(f"\n[Rollback] Switching active production to: {selected_version}...")

    # Clean up current junction/link
    if current_junction.exists() or current_junction.is_symlink():
        try:
            if current_junction.is_dir() and not current_junction.is_symlink():
                shutil.rmtree(current_junction)
            else:
                os.remove(current_junction) if not current_junction.is_dir() else shutil.rmtree(current_junction)
        except Exception:
            shutil.rmtree(current_junction, ignore_errors=True)

    # Re-link or copy
    link_mode = "copy"
    try:
        os.symlink(selected_release, current_junction, target_is_directory=True)
        link_mode = "symlink"
    except Exception:
        try:
            import subprocess
            cmd = f'cmd /c mklink /J "{current_junction}" "{selected_release}"'
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
            if res.returncode == 0:
                link_mode = "junction"
        except Exception:
            shutil.copytree(selected_release, current_junction)

    print("\n======================================================================")
    print(f"[SUCCESS] Active Production is now switched to {selected_version} (mode: {link_mode})!")
    print("======================================================================\n")


if __name__ == "__main__":
    nas_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_interactive_rollback(nas_arg)
