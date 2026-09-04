"""
Square VFX Studio Pipeline — Deployment

Ships a versioned release onto the studio NAS:

    releases/vX.Y.Z/    a frozen copy of square_core/ (+ tools/ if present)
    current             an NTFS junction -> the active release
    config/             studio_config.json  -- NEVER overwritten after the first
                        deploy; studio_config.template.json -- the reference,
                        refreshed each deploy. Missing keys are reported (or
                        added with --update-config).
    envs/               a Python venv built from requirements.txt
    launchers/          one .bat per deployed tool + square_rollback.bat

`current` is a junction, flipped by deleting it (os.rmdir -- detaches the
junction, never touches the release it points at) and re-creating it, so a
rollback is instant. Junctions need a local NTFS path: deploy to a path the
workstations map locally, not a raw \\NAS share.

    python -m tools.pipeline_deploy.deploy --dest //NAS/pipeline
    python -m tools.pipeline_deploy.deploy --dest //NAS/pipeline --rollback v0.1.0
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from square_core import __version__  # noqa: E402


# --------------------------------------------------------------------------
# the `current` junction
# --------------------------------------------------------------------------

def remove_junction(link) -> None:
    """Detach `current` if it exists. os.rmdir removes a junction / symlink /
    empty dir WITHOUT ever recursing into the target -- a populated real dir
    raises instead of being deleted, so a release can't be nuked by accident."""
    link = str(link)
    if not os.path.lexists(link):
        return
    try:
        os.rmdir(link)
    except OSError as e:
        raise SystemExit(
            f"[ERROR] {link} is not a junction (won't delete a real directory tree): {e}"
        )


def make_junction(source_dir, link_dir) -> None:
    src = os.path.abspath(str(source_dir))
    link = os.path.abspath(str(link_dir))
    remove_junction(link)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, src],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            "[ERROR] mklink /J failed: "
            + (r.stderr.strip() or r.stdout.strip() or "unknown")
            + f"\n        A junction needs a local NTFS path -- '{link}' or '{src}' "
              "looks like a network share. Deploy to a path the workstations map locally."
        )
    print(f"[deploy] junction  {link} -> {src}")


def rollback(nas_root_path, version_tag: str):
    nas_root = Path(nas_root_path).resolve()
    if not version_tag.startswith("v"):
        version_tag = "v" + version_tag
    target = nas_root / "releases" / version_tag
    if not target.exists():
        avail = [d.name for d in (nas_root / "releases").iterdir() if d.is_dir()] \
            if (nas_root / "releases").exists() else []
        sys.exit(f"[ERROR] release {version_tag} not found. available: {avail}")
    make_junction(target, nas_root / "current")
    print(f"[OK] active production -> {version_tag}")


# --------------------------------------------------------------------------
# launchers
# --------------------------------------------------------------------------

_LAUNCHER = """@echo off
title Square VFX - {title} v{ver}
set PIPELINE_ROOT=%~dp0..
set STUDIO_CONFIG_PATH=%PIPELINE_ROOT%\\config\\studio_config.json
set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\Scripts\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python.exe
start "" "%PYTHON_EXE%" "{entry}" %*
"""

_ROLLBACK_LAUNCHER = """@echo off
title Square VFX - Pipeline Version Switcher
set PIPELINE_ROOT=%~dp0..
set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\Scripts\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python.exe
"%PYTHON_EXE%" -m tools.pipeline_deploy.rollback_cli "%PIPELINE_ROOT%"
pause
"""


def write_launchers(launchers_dir: Path, release_dir: Path):
    launchers_dir.mkdir(parents=True, exist_ok=True)
    tools_dir = release_dir / "tools"
    found = []
    if tools_dir.exists():
        for main_py in sorted(tools_dir.glob("*/main.py")):
            name = main_py.parent.name                       # e.g. "ingest_tool"
            clean = name[:-5] if name.endswith("_tool") else name
            bat = launchers_dir / f"square_{clean}.bat"
            bat.write_text(_LAUNCHER.format(
                title=clean.replace("_", " ").title(), ver=__version__,
                entry=r"%PIPELINE_ROOT%\current\tools\{}\main.py".format(name),
            ), encoding="utf-8")
            found.append(bat.name)
    (launchers_dir / "square_rollback.bat").write_text(_ROLLBACK_LAUNCHER, encoding="utf-8")
    print(f"[deploy] launchers: {found + ['square_rollback.bat']}")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def _reconcile_config(config_dir: Path, update: bool) -> None:
    """The deployed studio_config.json is authoritative and is NEVER
    overwritten -- it holds the studio's real Kitsu host / NAS roots / edits.

    Deploy the template alongside it as a reference, then compare TOP-LEVEL
    keys: report any the template has that the live config lacks (a setting
    added in a newer release). With --update-config, add just those missing
    keys (template values), back up the old file, and touch nothing else."""
    tmpl_src = repo_root / "studio_config.template.json"
    if not tmpl_src.exists():
        return
    template = json.loads(tmpl_src.read_text(encoding="utf-8"))

    ref = config_dir / "studio_config.template.json"
    shutil.copy2(tmpl_src, ref)                       # the reference copy IS refreshed

    live = config_dir / "studio_config.json"
    if not live.exists():
        shutil.copy2(tmpl_src, live)
        print(f"[deploy] seeded {live} -- fill in kitsu_host + nas_roots "
              f"(credentials are per-user JWTs, not in this file)")
        return

    try:
        current = json.loads(live.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[deploy] WARN: {live} is not valid JSON ({e}); left untouched")
        return

    missing = [k for k in template if k not in current]
    if not missing:
        print(f"[deploy] {live.name}: up to date, unchanged")
        return

    if not update:
        print(f"[deploy] NOTE: {live.name} is missing key(s) added in a newer "
              f"template: {', '.join(missing)}")
        print(f"        compare against {ref.name}, or re-run with --update-config "
              f"to add them (existing values are never changed)")
        return

    backup = live.with_suffix(f".json.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(live, backup)
    for k in missing:
        current[k] = template[k]
    live.write_text(json.dumps(current, indent=4) + "\n", encoding="utf-8")
    print(f"[deploy] {live.name}: added {', '.join(missing)} (backup: {backup.name})")


# --------------------------------------------------------------------------
# deploy
# --------------------------------------------------------------------------

def deploy(nas_root_path, target_tool=None, deploy_env=False, update_config=False):
    nas_root = Path(nas_root_path).resolve()
    version_tag = f"v{__version__}"
    releases_dir = nas_root / "releases"
    release_dir = releases_dir / version_tag
    current = nas_root / "current"
    config_dir = nas_root / "config"
    envs_dir = nas_root / "envs"
    launchers_dir = nas_root / "launchers"

    print("=" * 66)
    print(f"[deploy] Square VFX Pipeline  v{__version__}  ->  {nas_root}")
    print("=" * 66)

    for d in (releases_dir, config_dir, envs_dir, launchers_dir):
        d.mkdir(parents=True, exist_ok=True)

    # venv
    target_env = envs_dir / "win_x64_python311"
    if not target_env.exists() or deploy_env:
        print(f"[deploy] building venv -> {target_env}")
        if target_env.exists():
            shutil.rmtree(target_env, ignore_errors=True)
        try:
            subprocess.run([sys.executable, "-m", "venv", str(target_env)], check=True)
            pip = target_env / "Scripts" / "pip.exe"
            req = repo_root / "requirements.txt"
            if pip.exists() and req.exists():
                subprocess.run([str(pip), "install", "-r", str(req)], check=True)
        except Exception as e:
            print(f"[WARN] venv build failed: {e}")

    # code
    if target_tool:
        src = repo_root / "tools" / target_tool
        if not src.exists():
            sys.exit(f"[ERROR] tool {target_tool!r} not found at {src}")
        dst = release_dir / "tools" / target_tool
        print(f"[deploy] tool {target_tool} -> {dst}")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        remove_junction(current)                     # detach before writing into releases/
        release_dir.mkdir(parents=True, exist_ok=True)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(repo_root / "square_core", release_dir / "square_core",
                        dirs_exist_ok=True, ignore=ignore)
        if (repo_root / "tools").exists():
            shutil.copytree(repo_root / "tools", release_dir / "tools",
                            dirs_exist_ok=True, ignore=ignore)
        (release_dir / "release_info.json").write_text(json.dumps({
            "version": __version__,
            "deployed_at": datetime.datetime.now().isoformat(),
            "source_path": str(repo_root),
        }, indent=2), encoding="utf-8")

    # point `current` at this release
    make_junction(release_dir, current)

    _reconcile_config(config_dir, update=update_config)

    write_launchers(launchers_dir, release_dir)

    print("=" * 66)
    print(f"[OK] deployed. active: {current}")
    print("=" * 66)


def _main():
    default_dest = os.environ.get("STUDIO_PIPELINE_ROOT", r"D:\nas_pipeline_test")
    p = argparse.ArgumentParser(description="Square VFX Studio Pipeline deployment")
    p.add_argument("--dest", "-d", default=default_dest, help="studio NAS pipeline root")
    p.add_argument("--tool", "-t", help="deploy one tool into the current release only")
    p.add_argument("--include-env", "-e", action="store_true", help="rebuild the venv")
    p.add_argument("--update-config", action="store_true",
                   help="add any missing keys from the template to studio_config.json "
                        "(existing values untouched; backup written)")
    p.add_argument("--rollback", "-r", metavar="VERSION", help="flip 'current' to a past release")
    a = p.parse_args()
    if a.rollback:
        rollback(a.dest, a.rollback)
    else:
        deploy(a.dest, target_tool=a.tool, deploy_env=a.include_env,
               update_config=a.update_config)


if __name__ == "__main__":
    _main()
