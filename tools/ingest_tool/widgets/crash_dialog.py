"""
Crash Report Dialog Modal for Square VFX Ingest Tool
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

from Qt import QtWidgets, QtCore, QtGui
from tools.qt_compat import TEXT_SELECTABLE_BY_MOUSE, CURSOR_POINTING_HAND

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QTextEdit = QtWidgets.QTextEdit
QPushButton = QtWidgets.QPushButton
QApplication = QtWidgets.QApplication


class CrashReportDialog(QDialog):
    """Modal dialog presenting formatted stack trace details on unhandled crashes."""

    def __init__(self, exc_type, exc_value, exc_tb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Square VFX — Application Error / Crash Report")
        self.setMinimumSize(700, 480)

        self.exc_type = exc_type
        self.exc_value = exc_value
        self.exc_tb = exc_tb

        # Format full traceback
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        self.full_traceback = "".join(tb_lines)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Warning
        header_layout = QHBoxLayout()
        header_icon = QLabel("⚠️")
        header_icon.setStyleSheet("font-size: 28px;")
        header_layout.addWidget(header_icon)

        header_text = QVBoxLayout()
        title_label = QLabel("An Unhandled Error Occurred")
        title_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #EF4444;")
        subtitle_label = QLabel("The application encountered an unexpected error. You can copy the traceback below to report it.")
        subtitle_label.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        header_text.addWidget(title_label)
        header_text.addWidget(subtitle_label)

        header_layout.addLayout(header_text)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Error Type Banner
        err_banner = QLabel(f"<b>Exception:</b> {self.exc_type.__name__}: {self.exc_value}")
        err_banner.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        err_banner.setStyleSheet("background-color: #1F2937; color: #F87171; border: 1px solid #374151; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 12px;")
        layout.addWidget(err_banner)

        # Traceback Text Box
        self.tb_edit = QTextEdit()
        self.tb_edit.setReadOnly(True)
        self.tb_edit.setPlainText(self.full_traceback)
        self.tb_edit.setStyleSheet(
            "QTextEdit { background-color: #111827; color: #E5E7EB; border: 1px solid #374151; "
            "border-radius: 6px; font-family: 'Consolas', 'Courier New', monospace; font-size: 11px; padding: 8px; }"
        )
        layout.addWidget(self.tb_edit)

        # Action Buttons
        btn_layout = QHBoxLayout()

        self.copy_btn = QPushButton("📋 Copy Traceback")
        self.copy_btn.setCursor(CURSOR_POINTING_HAND)
        self.copy_btn.setStyleSheet(
            "QPushButton { background-color: #3B82F6; color: white; font-weight: bold; border-radius: 6px; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #2563EB; }"
        )
        self.copy_btn.clicked.connect(self._copy_traceback)

        self.save_btn = QPushButton("💾 Save Log...")
        self.save_btn.setCursor(CURSOR_POINTING_HAND)
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #374151; color: white; border-radius: 6px; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #4B5563; }"
        )
        self.save_btn.clicked.connect(self._save_log)

        close_btn = QPushButton("Close")
        close_btn.setCursor(CURSOR_POINTING_HAND)
        close_btn.setStyleSheet(
            "QPushButton { background-color: #1F2937; color: #9CA3AF; border: 1px solid #374151; border-radius: 6px; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #374151; color: white; }"
        )
        close_btn.clicked.connect(self.accept)

        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.save_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _copy_traceback(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.full_traceback)
        self.copy_btn.setText("✅ Copied!")
        QApplication.processEvents()

    def _save_log(self):
        log_dir = Path.home() / ".square" / "logs" / "crashes"
        log_dir.mkdir(parents=True, exist_ok=True)
        filename = f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        log_file = log_dir / filename
        log_file.write_text(self.full_traceback, encoding="utf-8")
        self.save_btn.setText(f"✅ Saved to {filename}!")


def install_global_crash_handler():
    """Installs sys.excepthook to intercept unhandled GUI exceptions."""
    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        # Write crash log to disk
        try:
            log_dir = Path.home() / ".square" / "logs" / "crashes"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"crash_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            log_file.write_text(tb_str, encoding="utf-8")
        except Exception:
            pass

        # Show GUI dialog if QApplication active
        app = QApplication.instance()
        if app:
            dlg = CrashReportDialog(exc_type, exc_value, exc_tb)
            dlg.exec()
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook
