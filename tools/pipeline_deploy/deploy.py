"""
Square VFX Studio Pipeline — Deployment

Ships a versioned release onto the studio NAS:

    releases/vX.Y.Z/    a frozen copy of square_core/ (+ tools/ if present)
    current             a junction/symlink -> the active release
    config/             studio_config.json (from the template, kept on rollback)
    envs/               a Python venv built from requirements.txt
    launchers/          one .bat per deployed tool + square_rollback.bat

`current` is flipped atomically, so a rollback is instant.

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
# link / rollback
# --------------------------------------------------------------------------

def link_or_copy(source_dir, link_dir) -> str:
    src = os.path.abspath(str(source_dir))
    link = os.path.abspath(str(link_dir))

    if os.path.lexists(link):
        try:
            os.unlink(link) if os.path.islink(link) else shutil.rmtree(link, ignore_errors=True)
        except Exception:
            shutil.rmtree(link, ignore_errors=True)

    try:
        os.symlink(src, link, target_is_directory=True)
        print(f"[deploy] symlink  {link} -> {src}")
        return "symlink"
    except Exception:
        pass
    try:
        r = subprocess.run(f'cmd /c mklink /J "{link}" "{src}"', capture_output=True,
                           text=True, shell=True)
        if r.returncode == 0:
            print(f"[deploy] junction {link} -> {src}")
            return "junction"
    except Exception:
        pass
    print(f"[deploy] links restricted; copying -> {link}")
    shutil.copytree(src, link)
    return "copy"


def rollback(nas_root_path, version_tag: str):
    nas_root = Path(nas_root_path).resolve()
    if not version_tag.startswith("v"):
        version_tag = "v" + version_tag
    target = nas_root / "releases" / version_tag
    if not target.exists():
        avail = [d.name for d in (nas_root / "releases").iterdir() if d.is_dir()] \
            if (nas_root / "releases").exists() else []
        sys.exit(f"[ERROR] release {version_tag} not found. available: {avail}")
    link_or_copy(target, nas_root / "current")
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
# deploy
# --------------------------------------------------------------------------

def deploy(nas_root_path, target_tool=None, deploy_env=False):
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
        if current.exists() or current.is_symlink():
            try:
                os.unlink(current) if current.is_symlink() else shutil.rmtree(current, ignore_errors=True)
            except Exception:
                pass
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

    # current pointer
    link_or_copy(release_dir, current)

    # config (only if absent -- rollback must not clobber a studio's edits)
    target_config = config_dir / "studio_config.json"
    if not target_config.exists():
        tmpl = repo_root / "studio_config.template.json"
        if tmpl.exists():
            shutil.copy2(tmpl, target_config)
            print(f"[deploy] seeded {target_config} from template "
                  f"(fill in kitsu_host + nas_roots; credentials are per-user, not in this file)")

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
    p.add_argument("--rollback", "-r", metavar="VERSION", help="flip 'current' to a past release")
    a = p.parse_args()
    if a.rollback:
        rollback(a.dest, a.rollback)
    else:
        deploy(a.dest, target_tool=a.tool, deploy_env=a.include_env)


if __name__ == "__main__":
    _main()
