"""`python -m tools.config_editor` -> Qt GUI; `--cli ...` -> headless driver."""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "--cli":
        from tools.config_editor.cli import main as cli_main
        cli_main(argv[1:])
        return
    from tools.config_editor.app import main as gui_main
    gui_main()


if __name__ == "__main__":
    main()
