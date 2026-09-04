"""GUI entry point: connect to Kitsu (prompt for login once if needed), build a
`ConfigStore`, show the main window."""

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
from square_core.kitsu import auth

from tools.qt_compat import DIALOG_OK, DIALOG_CANCEL, ECHO_MODE_PASSWORD, exec_dialog
from tools.config_editor.core import ConfigStore
from tools.config_editor.ui_main import MainWindow

_QSS = """
QMainWindow, QDialog, QWidget { background:#0F1117; color:#E2E8F0;
    font-family:'Segoe UI','Roboto',sans-serif; font-size:13px; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
    background:#161B27; border:1px solid #2A3446; border-radius:4px; padding:3px; }
QTableWidget { gridline-color:#2A3446; }
QHeaderView::section { background:#1B2233; padding:4px; border:0; }
QPushButton { background:#243047; border:1px solid #33405A; border-radius:4px;
    padding:5px 12px; }
QPushButton:hover { background:#2C3B57; }
QPushButton:flat { background:transparent; border:0; }
QTabBar::tab { background:#161B27; padding:7px 16px; }
QTabBar::tab:selected { background:#243047; }
QToolBar { background:#131720; border-bottom:1px solid #2A3446; spacing:6px; padding:4px; }
"""


class LoginDialog(QtWidgets.QDialog):
    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to Kitsu")
        self.setMinimumWidth(360)
        self.host = QtWidgets.QLineEdit(host)
        self.email = QtWidgets.QLineEdit()
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(ECHO_MODE_PASSWORD)
        self.err = QtWidgets.QLabel()
        self.err.setStyleSheet("color:#F87171;")
        self.err.setWordWrap(True)

        bb = QtWidgets.QDialogButtonBox(DIALOG_OK | DIALOG_CANCEL)
        bb.accepted.connect(self._try)
        bb.rejected.connect(self.reject)

        form = QtWidgets.QFormLayout(self)
        form.addRow("Host", self.host)
        form.addRow("Email", self.email)
        form.addRow("Password", self.pw)
        form.addRow("", self.err)
        form.addRow(bb)

    def _try(self):
        try:
            auth.login(self.host.text().strip(), self.email.text().strip(),
                       self.pw.text())
        except Exception as e:                       # gazu raises various types
            self.err.setText(f"Login failed: {e}")
            return
        self.accept()


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
    app.setApplicationName("Square Config Editor")
    app.setStyleSheet(_QSS)

    ctx = _connect()
    store = ConfigStore(ctx.config, user=ctx.user)
    win = MainWindow(ctx, store)
    win.show()
    sys.exit(app.exec() if hasattr(app, "exec") else app.exec_())


if __name__ == "__main__":
    main()
