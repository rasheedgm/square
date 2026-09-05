import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from tools.crash_handler import install_global_crash_handler
install_global_crash_handler("Square Ingest Tool")

from Qt import QtWidgets
from tools.ingest_tool.ui_main import MainWindow   # if this import itself fails, the hook catches it


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Square VFX Ingest Tool")

    qss_path = Path(__file__).parent / "style.qss"
    if qss_path.exists():
        res_dir = (Path(__file__).parent / "resources").as_posix()
        with open(qss_path, "r", encoding="utf-8") as f:
            qss = f.read().replace("{RESOURCES_DIR}", res_dir)
            app.setStyleSheet(qss)

    window = MainWindow()
    window.show()

    sys.exit(app.exec() if hasattr(app, "exec") else app.exec_())


if __name__ == "__main__":
    main()
