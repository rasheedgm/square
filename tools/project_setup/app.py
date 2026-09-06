"""GUI entry point: connect to Kitsu (prompt for login once if needed), build a
`ProjectAdmin`, show the main window."""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from Qt import QtWidgets

from square_core.config import PipelineConfig
from square_core.context import PipelineContext
from square_core.errors import NeedsLogin

from tools.qt_compat import exec_dialog
from tools.widgets.login_dialog import LoginDialog
from tools.project_setup.core import ProjectAdmin
from tools.project_setup.ui_main import MainWindow

_QSS = """
QMainWindow, QDialog, QWidget { background:#0F1117; color:#E2E8F0;
    font-family:'Segoe UI','Roboto',sans-serif; font-size:13px; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QTableWidget, QTreeWidget, QListWidget {
    background:#161B27; border:1px solid #2A3446; border-radius:4px; padding:3px; }
QTableWidget, QTreeWidget { gridline-color:#2A3446; }
QHeaderView::section { background:#1B2233; padding:4px; border:0; }
QPushButton { background:#243047; border:1px solid #33405A; border-radius:4px;
    padding:5px 12px; }
QPushButton:hover { background:#2C3B57; }
QPushButton:disabled { color:#64748B; background:#1A2130; }
QTabBar::tab { background:#161B27; padding:7px 16px; }
QTabBar::tab:selected { background:#243047; }
QToolBar { background:#131720; border-bottom:1px solid #2A3446; spacing:6px; padding:4px; }
"""


def _connect() -> PipelineContext:
    try:
        return PipelineContext.connect()
    except NeedsLogin:
        host = PipelineConfig.load().kitsu_host
        dlg = LoginDialog(host)
        if not exec_dialog(dlg):
            raise SystemExit(0)
        return PipelineContext.connect()


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Square Project Setup")
    app.setStyleSheet(_QSS)

    ctx = _connect()
    win = MainWindow(ProjectAdmin(ctx))
    win.show()
    sys.exit(app.exec() if hasattr(app, "exec") else app.exec_())


if __name__ == "__main__":
    main()
