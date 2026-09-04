"""Reusable crash handler for Square desktop tools.

A launcher `.bat` starts a tool detached (`start "" python.exe ...`) so an
unhandled exception makes the console flash shut before anyone can read it --
the tool just "fails immediately" with nothing to look at. This installs
`sys.excepthook` so instead:

  - the traceback is always written to `~/.square/logs/crashes/`
  - if Qt is available, a modal `CrashReportDialog` is shown -- even if the
    crash happens *before* the tool's own `QApplication` exists (a
    `PipelineContext.connect()` failure during startup, say), a throwaway one
    is created just to host the dialog

Call this as the FIRST line of a tool's `main.py`, before any other project
import -- an import-time failure is itself an uncaught exception at module
scope, and only reaches this hook if it is already installed:

    from tools.crash_handler import install_global_crash_handler
    install_global_crash_handler("Square Config Editor")

    from tools.config_editor.app import main   # if THIS raises, the hook catches it
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path.home() / ".square" / "logs" / "crashes"


def _write_log(app_title: str, exc_type, exc_value, exc_tb) -> Path | None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "_" for c in app_title) or "square_tool"
        p = _LOG_DIR / f"{safe}_{datetime.now():%Y%m%d_%H%M%S}.log"
        p.write_text("".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
                     encoding="utf-8")
        return p
    except Exception:
        return None


def install_global_crash_handler(app_title: str = "Square") -> None:
    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return

        # always: stderr (visible if the console isn't detached) + a log file
        traceback.print_exception(exc_type, exc_value, exc_tb)
        log_path = _write_log(app_title, exc_type, exc_value, exc_tb)

        try:
            from Qt import QtWidgets
            from tools.widgets.crash_dialog import CrashReportDialog

            app = QtWidgets.QApplication.instance()
            owns_app = app is None
            if owns_app:
                app = QtWidgets.QApplication(sys.argv[:1])
            dlg = CrashReportDialog(app_title, exc_type, exc_value, exc_tb, log_path=log_path)
            dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
            if owns_app:
                app.quit()
        except Exception:
            # no Qt binding installed, or the dialog itself failed -- the
            # traceback is already on stderr and in the log file above
            pass

    sys.excepthook = excepthook
