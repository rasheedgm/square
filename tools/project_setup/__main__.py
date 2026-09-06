"""`python -m tools.project_setup` -> Qt GUI."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def main() -> None:
    from tools.crash_handler import install_global_crash_handler
    install_global_crash_handler("Square Project Setup")
    from tools.project_setup.app import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
