"""Crash-report dialog, shared by every Square desktop tool.

Not tool-specific -- `crash_handler.install_global_crash_handler()` is what
actually wires this into `sys.excepthook`; import that, not this module,
from a tool's `main.py`.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from Qt import QtWidgets

from tools.qt_compat import TEXT_SELECTABLE_BY_MOUSE, CURSOR_POINTING_HAND


class CrashReportDialog(QtWidgets.QDialog):
    """Modal dialog presenting a formatted traceback on an unhandled crash."""

    def __init__(self, app_title, exc_type, exc_value, exc_tb, *,
                 log_path: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{app_title} — Application Error")
        self.setMinimumSize(700, 480)

        self.full_traceback = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        self.log_path = log_path

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        icon = QtWidgets.QLabel("⚠️")
        icon.setStyleSheet("font-size: 28px;")
        header.addWidget(icon)
        text = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("An Unhandled Error Occurred")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #EF4444;")
        subtitle = QtWidgets.QLabel(
            f"{app_title} hit an unexpected error and could not continue. "
            "Copy the traceback below to report it.")
        subtitle.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        subtitle.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(subtitle)
        header.addLayout(text)
        header.addStretch()
        layout.addLayout(header)

        banner = QtWidgets.QLabel(f"<b>Exception:</b> {exc_type.__name__}: {exc_value}")
        banner.setWordWrap(True)
        banner.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        banner.setStyleSheet(
            "background-color:#1F2937;color:#F87171;border:1px solid #374151;"
            "border-radius:6px;padding:10px;font-family:monospace;font-size:12px;")
        layout.addWidget(banner)

        self.tb_edit = QtWidgets.QTextEdit()
        self.tb_edit.setReadOnly(True)
        self.tb_edit.setPlainText(self.full_traceback)
        self.tb_edit.setStyleSheet(
            "QTextEdit{background-color:#111827;color:#E5E7EB;border:1px solid #374151;"
            "border-radius:6px;font-family:Consolas,'Courier New',monospace;"
            "font-size:11px;padding:8px;}")
        layout.addWidget(self.tb_edit)

        if log_path:
            loc = QtWidgets.QLabel(f"Also written to: {log_path}")
            loc.setStyleSheet("color:#6B7280;font-size:11px;")
            loc.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
            layout.addWidget(loc)

        buttons = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton("Copy Traceback")
        copy_btn.setCursor(CURSOR_POINTING_HAND)
        copy_btn.clicked.connect(self._copy)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setCursor(CURSOR_POINTING_HAND)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(copy_btn)
        buttons.addStretch()
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        self._copy_btn = copy_btn

    def _copy(self):
        QtWidgets.QApplication.clipboard().setText(self.full_traceback)
        self._copy_btn.setText("Copied!")
