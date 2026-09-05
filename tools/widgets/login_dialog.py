"""Kitsu login dialog, shared by every desktop tool.

    from square_core.context import PipelineContext
    from square_core.errors import NeedsLogin
    from square_core.config import PipelineConfig
    from tools.qt_compat import exec_dialog
    from tools.widgets.login_dialog import LoginDialog

    try:
        ctx = PipelineContext.connect()
    except NeedsLogin:
        dlg = LoginDialog(PipelineConfig.load().kitsu_host)
        if exec_dialog(dlg):
            ctx = PipelineContext.connect()
"""

from __future__ import annotations

from Qt import QtWidgets

from square_core.kitsu import auth

from tools.qt_compat import DIALOG_OK, DIALOG_CANCEL, ECHO_MODE_PASSWORD


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
