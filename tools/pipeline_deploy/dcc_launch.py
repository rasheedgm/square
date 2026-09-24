"""Launch an embedded-DCC (xStudio / Nuke) with the Square pipeline wired in.

Stdlib-only and self-contained -- the deploy `.bat`s call it by path with the
pipeline's own Python, the same way `rollback_cli.py` is called.

    python dcc_launch.py xstudio [-- extra args passed to the exe]
    python dcc_launch.py nuke    [-- extra args]

It reads `<pipeline>/config/studio_config.json` for the exe path
(`dcc.xstudio_exe` / `dcc.nuke_exe`; `%SQUARE_XSTUDIO_EXE%` / `%SQUARE_NUKE_EXE%`
override), sets:

* `SQUARE_ROOT`   -> `<pipeline>/current`         (square_core + tools)
* `SQUARE_DEPS`   -> `<pipeline>/envs/dcc-deps`   (pure-python gazu + requests)
* `STUDIO_CONFIG_PATH`
* `PYTHONPYCACHEPREFIX` -> a machine-local folder (`%LOCALAPPDATA%\square\pycache`),
  unless already set -- .pyc files cached locally instead of next to the source
  on the (usually network) pipeline share
* `FFMPEG_BINARY` -> `ffmpeg_exe` from studio_config.json (`%SQUARE_FFMPEG_EXE%`
  overrides), if either is set -- e.g. one shared copy on the NAS, so a review
  proxy encode doesn't depend on ffmpeg being installed on every workstation
* xStudio: `XSTUDIO_PYTHON_PLUGIN_PATH` -> the bundled plugins dir
* Nuke:    prepends the nuke integration dir + repo root to `NUKE_PATH`

...then replaces itself with the DCC process.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PIPELINE_ROOT_ENV = "PIPELINE_ROOT"


def _pipeline_root(argv) -> Path:
    # explicit 2nd positional wins, else $PIPELINE_ROOT, else two dirs up from
    # <root>/current/tools/pipeline_deploy/dcc_launch.py
    if len(argv) > 2 and not argv[2].startswith("-"):
        return Path(argv[2]).resolve()
    env = os.environ.get(_PIPELINE_ROOT_ENV)
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[3]


def _config(root: Path) -> dict:
    p = root / "config" / "studio_config.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[square] {p} unreadable ({e}); continuing without it")
    return {}


def _exe(dcc: str, cfg: dict) -> str:
    override = os.environ.get(f"SQUARE_{dcc.upper()}_EXE")
    if override:
        return override
    return str((cfg.get("dcc") or {}).get(f"{dcc}_exe", "") or "")


def _pycache_prefix() -> str:
    """A machine-local home for compiled .pyc files. The pipeline usually
    lives on a network share, where importing hundreds of small files (the
    DCC's own startup imports square_core, gazu, requests, Qt.py, ...) is
    dominated by SMB round trips -- and a share that's read-only, or a
    stale/missing __pycache__ beside the source, means a recompile every
    launch. Python 3.8+ (Nuke 14 is 3.9, xStudio 3.11) reads this at start-up,
    so it has to be in the environment BEFORE the DCC is exec'd."""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return str(Path(local) / "square" / "pycache")
    try:
        return str(Path.home() / ".square" / "pycache")
    except RuntimeError:
        return ""        # no home directory to be found -- skip, never block a launch


def _ffmpeg_exe(cfg: dict) -> str:
    return os.environ.get("SQUARE_FFMPEG_EXE") or str(cfg.get("ffmpeg_exe", "") or "")


def _prepend(var: str, *paths: str) -> None:
    have = os.environ.get(var, "")
    parts = [p for p in paths if p] + ([have] if have else [])
    os.environ[var] = os.pathsep.join(parts)


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2 or argv[1] not in ("xstudio", "nuke"):
        print("usage: dcc_launch.py {xstudio|nuke} [pipeline_root] [-- exe args]")
        return 2
    dcc = argv[1]

    root = _pipeline_root(argv)
    current = root / "current"
    cfg = _config(root)

    exe = _exe(dcc, cfg)
    if not exe or not Path(exe).exists():
        print(f"[square] {dcc} executable not found: {exe!r}\n"
              f"         set 'dcc.{dcc}_exe' in {root / 'config' / 'studio_config.json'}\n"
              f"         or the SQUARE_{dcc.upper()}_EXE environment variable")
        return 1

    os.environ["SQUARE_ROOT"] = str(current)
    os.environ["SQUARE_DEPS"] = str(root / "envs" / "dcc-deps")
    os.environ.setdefault("STUDIO_CONFIG_PATH", str(root / "config" / "studio_config.json"))

    pycache = _pycache_prefix()
    if pycache:
        os.environ.setdefault("PYTHONPYCACHEPREFIX", pycache)

    ffmpeg = _ffmpeg_exe(cfg)
    if ffmpeg:
        os.environ.setdefault("FFMPEG_BINARY", ffmpeg)

    if dcc == "xstudio":
        os.environ["XSTUDIO_PYTHON_PLUGIN_PATH"] = str(
            current / "tools" / "dcc" / "xstudio" / "plugins")
    else:  # nuke -- needs the integration dir (for menu.py) + repo root (imports)
        _prepend("NUKE_PATH",
                 str(current / "tools" / "dcc" / "nuke"),
                 str(current))
        _prepend("PYTHONPATH", str(current), str(root / "envs" / "dcc-deps"))

    exe_args = argv[argv.index("--") + 1:] if "--" in argv else []
    cmd = [exe, *exe_args]
    print(f"[square] launching {dcc}: {exe}")
    try:
        os.execv(exe, cmd)          # replace this process
    except OSError:
        import subprocess
        return subprocess.call(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
