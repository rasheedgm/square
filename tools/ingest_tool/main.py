import sys
import os
from pathlib import Path

# Ensure square_core is in sys.path
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from Qt import QtWidgets, QtCore
from tools.ingest_tool.ui_main import MainWindow

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Square VFX Ingest Tool")

    # Load QSS Style
    qss_path = Path(__file__).parent / "style.qss"
    if qss_path.exists():
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    window = MainWindow()
    window.show()
    
    # Use exec() for Qt.py / PyQt6 / PySide6 compatibility
    if hasattr(app, "exec"):
        sys.exit(app.exec())
    else:
        sys.exit(app.exec_())

if __name__ == "__main__":
    main()
