"""
Square VFX Pipeline Engine — Generic Studio Deployment Script

Usage:
    python deploy_studio_pipeline.py --dest D:\nas_pipeline_test
    python deploy_studio_pipeline.py --dest //NAS/pipeline
    python deploy_studio_pipeline.py --dest //NAS/pipeline --tool ingest_tool
"""

import os
import sys
import json
import shutil
import datetime
import argparse
from pathlib import Path

# Ensure square_core is in sys.path
repo_root = Path(__file__).parent.resolve()
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from square_core import __version__


def create_junction_or_copy(source_dir, link_dir):
    """
    Creates an instant, 0-byte Directory Link / Junction ('current' -> 'releases/v1.0.0').
    Falls back to file copy if network share permissions restrict symlinks.
    """
    source_path = os.path.abspath(str(source_dir))
    link_path   = os.path.abspath(str(link_dir))

    # Clean up existing link or folder at link_path WITHOUT following symlinks
    if os.path.lexists(link_path):
        try:
            if os.path.islink(link_path):
                os.unlink(link_path)
            elif os.path.isdir(link_path):
                shutil.rmtree(link_path)
            else:
                os.remove(link_path)
        except Exception:
            try:
                shutil.rmtree(link_path, ignore_errors=True)
            except Exception:
                pass

    # 1. Try native os.symlink
    try:
        os.symlink(source_path, link_path, target_is_directory=True)
        print(f"[Deploy] Created instant Directory Symlink: {link_path} -> {source_path}")
        return "symlink"
    except Exception:
        pass

    # 2. Try Windows NTFS Junction (mklink /J)
    try:
        import subprocess
        cmd = f'cmd /c mklink /J "{link_path}" "{source_path}"'
        res = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if res.returncode == 0:
            print(f"[Deploy] Created instant Windows Junction: {link_path} -> {source_path}")
            return "junction"
    except Exception:
        pass

    # 3. Fallback to copy if network share restricts symlinks/junctions
    print(f"[Deploy] Network share restricted links. Falling back to copy: {link_path}")
    shutil.copytree(source_path, link_path)
    return "copy"


def rollback_release(nas_root_path, version_tag):
    """Atomically reverts active production junction 'current' to a specified release version."""
    nas_root = Path(nas_root_path).resolve()
    if not version_tag.startswith("v"):
        version_tag = f"v{version_tag}"

    releases_dir = nas_root / "releases"
    target_release_dir = releases_dir / version_tag
    current_junction = nas_root / "current"

    if not target_release_dir.exists():
        print(f"[ERROR] Cannot rollback: Release '{version_tag}' does not exist at {target_release_dir}")
        available = [d.name for d in releases_dir.iterdir() if d.is_dir()] if releases_dir.exists() else []
        print(f"        Available releases on NAS: {available}")
        sys.exit(1)

    print("======================================================================")
    print(f"[Rollback] Reverting Active Studio Production to: {version_tag}")
    print("======================================================================")

    link_type = create_junction_or_copy(target_release_dir, current_junction)
    print(f"[SUCCESS] Active Studio Production successfully reverted to {version_tag} (mode: {link_type})!")
    print("======================================================================\n")


def create_launcher_script(launchers_dir, tool_name="ingest_tool", version_str="1.0.0"):
    """Generates clean studio workstation launcher batch files."""
    launchers_dir.mkdir(parents=True, exist_ok=True)
    clean_name = tool_name[:-5] if tool_name.endswith("_tool") else tool_name
    bat_path = launchers_dir / f"square_{clean_name}.bat"

    # Clean up obsolete legacy launchers (e.g. square_ingest_tool.bat)
    for old_file in launchers_dir.glob("square_*.bat"):
        if old_file.name != bat_path.name and "ingest" in old_file.name:
            try:
                old_file.unlink()
                print(f"[Deploy] Removed legacy launcher: {old_file.name}")
            except Exception:
                pass

    bat_content = f"""@echo off
title Square VFX - {clean_name.replace('_', ' ').title()} Ingest Tool v{version_str}
echo Launching Square VFX {clean_name.replace('_', ' ').title()} Ingest Tool v{version_str}...

set PIPELINE_ROOT=%~dp0..
set STUDIO_CONFIG_PATH=%PIPELINE_ROOT%\\config\\studio_config.json

set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\Scripts\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python.exe

set APP_MAIN=%PIPELINE_ROOT%\\current\\tools\\{tool_name}\\main.py

start "" "%PYTHON_EXE%" "%APP_MAIN%" %*
"""
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"[Deploy] Created Launcher: {bat_path}")


def create_rollback_launcher_script(launchers_dir):
    """Generates an interactive rollback batch file in launchers/."""
    launchers_dir.mkdir(parents=True, exist_ok=True)
    bat_path = launchers_dir / "square_rollback.bat"

    bat_content = """@echo off
title Square VFX - Pipeline Version Switcher
echo Launching Interactive Version Switcher...

set PIPELINE_ROOT=%~dp0..
set STUDIO_CONFIG_PATH=%PIPELINE_ROOT%\\config\\studio_config.json

set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=%PIPELINE_ROOT%\\envs\\win_x64_python311\\Scripts\\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python.exe

set ROLLBACK_MAIN=%PIPELINE_ROOT%\\current\\tools\\rollback_cli.py

"%PYTHON_EXE%" "%ROLLBACK_MAIN%" "%PIPELINE_ROOT%"
pause
"""
    bat_path.write_text(bat_content, encoding="utf-8")
    print(f"[Deploy] Created Rollback Launcher: {bat_path}")


def deploy(nas_root_path, target_tool=None, deploy_env=False):
    nas_root = Path(nas_root_path).resolve()
    version_tag = f"v{__version__}"

    releases_dir = nas_root / "releases"
    target_release_dir = releases_dir / version_tag
    current_junction   = nas_root / "current"
    config_dir         = nas_root / "config"
    envs_dir           = nas_root / "envs"
    launchers_dir      = nas_root / "launchers"

    print("======================================================================")
    print(f"[Deploy] Square VFX Pipeline Deployer v{__version__}")
    print(f"         Destination Studio Root: {nas_root}")
    print(f"         Target Release:          {version_tag}")
    print("======================================================================")

    # 1. Create base NAS directory structure
    releases_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    envs_dir.mkdir(parents=True, exist_ok=True)
    launchers_dir.mkdir(parents=True, exist_ok=True)

    # 1b. Create Studio Python Virtual Environment if missing or explicitly requested
    target_env = envs_dir / "win_x64_python311"
    if not target_env.exists() or deploy_env:
        print(f"[Deploy] Creating Studio Python Environment -> {target_env}...")
        if target_env.exists():
            shutil.rmtree(target_env, ignore_errors=True)

        try:
            import subprocess
            subprocess.run([sys.executable, "-m", "venv", str(target_env)], check=True)
            pip_exe = target_env / "Scripts" / "pip.exe"
            if not pip_exe.exists():
                pip_exe = target_env / "python.exe"

            req_file = repo_root / "requirements.txt"
            if req_file.exists():
                print(f"[Deploy] Installing dependencies from {req_file}...")
                subprocess.run([
                    str(pip_exe), "install", "-r", str(req_file),
                    "--trusted-host", "pypi.org",
                    "--trusted-host", "files.pythonhosted.org",
                    "--trusted-host", "pypi.python.org"
                ], check=True)
                print(f"[Deploy] Python Environment created & dependencies installed successfully.")
        except Exception as e:
            print(f"[WARN] Failed to automatically create venv: {e}")

    # 2. Deploy Code into releases/vX.Y.Z
    if target_tool:
        # Deploy single tool inside existing release
        tool_src = repo_root / "tools" / target_tool
        tool_dst = target_release_dir / "tools" / target_tool

        if not tool_src.exists():
            print(f"[ERROR] Tool '{target_tool}' not found at {tool_src}")
            sys.exit(1)

        print(f"[Deploy] Deploying tool '{target_tool}' to {tool_dst}...")
        if tool_dst.exists():
            shutil.rmtree(tool_dst)

        shutil.copytree(tool_src, tool_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        # Unlink current junction first to unlock release directory
        if current_junction.exists() or current_junction.is_symlink():
            try:
                os.remove(current_junction) if current_junction.is_symlink() else shutil.rmtree(current_junction, ignore_errors=True)
            except Exception:
                pass

        # Deploy full pipeline release (clean square_core + clean tools)
        try:
            target_release_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        # Copy square_core
        shutil.copytree(
            repo_root / "square_core",
            target_release_dir / "square_core",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )

        # Copy tools directory
        shutil.copytree(
            repo_root / "tools",
            target_release_dir / "tools",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )

        # Write release metadata
        meta = {
            "version": __version__,
            "deployed_at": datetime.datetime.now().isoformat(),
            "source_path": str(repo_root)
        }
        (target_release_dir / "release_info.json").write_text(json.dumps(meta, indent=4), encoding="utf-8")

    # 3. Update 'current' junction / link
    print(f"[Deploy] Updating active production link 'current' -> {version_tag}...")
    link_type = create_junction_or_copy(target_release_dir, current_junction)
    print(f"[Deploy] Active production pointer updated (mode: {link_type}).")

    # 4. Deploy default studio_config.json if not present
    target_config = config_dir / "studio_config.json"
    if not target_config.exists():
        src_config = repo_root / "studio_config.json"
        if src_config.exists():
            shutil.copy2(src_config, target_config)
            print(f"[Deploy] Created default studio config at: {target_config}")

    # 5. Generate launchers
    create_launcher_script(launchers_dir, tool_name="ingest_tool", version_str=__version__)
    create_rollback_launcher_script(launchers_dir)

    print("\n======================================================================")
    print(f"[SUCCESS] Studio Deployment Complete!")
    print(f"          Root Directory:      {nas_root}")
    print(f"          Active Production:   {current_junction}")
    print(f"          Launcher Script:     {launchers_dir / 'square_ingest.bat'}")
    print("======================================================================\n")


if __name__ == "__main__":
    default_dest = os.environ.get("STUDIO_PIPELINE_ROOT", "D:\\nas_pipeline_test")
    parser = argparse.ArgumentParser(description="Square VFX Studio Pipeline Deployment Script")
    parser.add_argument("--dest", "-d", default=default_dest, help="Target studio NAS pipeline root directory")
    parser.add_argument("--tool", "-t", help="Deploy specific tool only (e.g. ingest_tool, publish_tool)")
    parser.add_argument("--include-env", "-e", action="store_true", help="Force copy/re-deploy Python virtual environment to envs/")
    parser.add_argument("--rollback", "-r", metavar="VERSION", help="Revert active production 'current' to a previous release version (e.g. v1.0.0)")
    args = parser.parse_args()

    if args.rollback:
        rollback_release(nas_root_path=args.dest, version_tag=args.rollback)
    else:
        deploy(nas_root_path=args.dest, target_tool=args.tool, deploy_env=args.include_env)
