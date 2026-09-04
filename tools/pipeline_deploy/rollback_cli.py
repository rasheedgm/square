"""Interactive version switcher for a deployed Square pipeline.

Lists the releases on the NAS, shows which one `current` points at, and lets
the operator switch. Switching just re-points the `current` junction (delete +
re-create -- never touches the release it pointed at).

    python rollback_cli.py <pipeline-root>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _remove_junction(link: str) -> None:
    """Detach `current`. os.rmdir removes a junction / empty dir without
    recursing into the target; a real populated dir raises instead."""
    if not os.path.lexists(link):
        return
    try:
        os.rmdir(link)
    except OSError as e:
        raise SystemExit(f"[ERROR] {link} is not a junction; refusing to delete it: {e}")


def _make_junction(src: str, link: str) -> None:
    _remove_junction(link)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.abspath(link), os.path.abspath(src)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("[ERROR] mklink /J failed: " + (r.stderr.strip() or r.stdout.strip()))


def _current_target(current: Path) -> str:
    try:
        return os.path.basename(os.path.realpath(current)) if current.exists() else ""
    except OSError:
        return ""


def run(nas_root_path: str | None = None):
    nas_root = Path(nas_root_path
                    or os.environ.get("STUDIO_PIPELINE_ROOT", ".")).resolve()
    releases_dir = nas_root / "releases"
    current = nas_root / "current"

    if not releases_dir.exists():
        sys.exit(f"[ERROR] no releases/ under {nas_root}")
    releases = sorted((d for d in releases_dir.iterdir() if d.is_dir() and d.name.startswith("v")),
                      key=lambda d: d.name)
    if not releases:
        sys.exit(f"[ERROR] no vX.Y.Z releases under {releases_dir}")

    active = _current_target(current)

    print("=" * 66)
    print(f"Square VFX Pipeline - version switcher   ({nas_root})")
    print("=" * 66)
    for i, rel in enumerate(releases, 1):
        when = ""
        info = rel / "release_info.json"
        if info.exists():
            try:
                when = json.loads(info.read_text(encoding="utf-8")).get("deployed_at", "")[:19]
            except Exception:
                pass
        mark = "   <- active" if rel.name == active else ""
        print(f"  {i}. {rel.name:<12} {when}{mark}")

    try:
        choice = input("\nswitch to # (Enter to cancel): ").strip()
    except EOFError:
        choice = ""
    if not choice:
        print("cancelled.")
        return
    try:
        target = releases[int(choice) - 1]
    except (ValueError, IndexError):
        print("invalid choice.")
        return

    _make_junction(str(target), str(current))
    print(f"\n[OK] active production -> {target.name}")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
