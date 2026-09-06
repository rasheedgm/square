"""Launcher entry point (deploy generates `square_project_setup.bat` -> this)."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from tools.crash_handler import install_global_crash_handler
install_global_crash_handler("Square Project Setup")

from tools.project_setup.app import main   # if this import itself fails, the hook catches it

if __name__ == "__main__":
    main()
