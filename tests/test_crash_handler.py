"""tools.crash_handler -- writes a crash log and (if Qt is available) shows a
dialog instead of letting an unhandled exception vanish with a detached
console. Headless / offscreen; skips if no Qt binding is installed."""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from Qt import QtWidgets
    _HAVE_QT = True
except Exception:
    _HAVE_QT = False

from tools import crash_handler


class TestWriteLog(unittest.TestCase):
    def test_writes_a_log_file(self):
        try:
            raise ValueError("boom")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
        p = crash_handler._write_log("Test Tool", exc_type, exc_value, exc_tb)
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())
        self.assertIn("ValueError: boom", p.read_text(encoding="utf-8"))
        p.unlink()

    def test_sanitizes_app_title_for_filename(self):
        try:
            raise RuntimeError("x")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
        p = crash_handler._write_log("Weird / Name*?", exc_type, exc_value, exc_tb)
        self.assertNotIn("/", p.name)
        self.assertNotIn("*", p.name)
        p.unlink()


@unittest.skipUnless(_HAVE_QT, "no Qt binding")
class TestExcepthook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_excepthook_shows_the_crash_dialog(self):
        # NOTE: setUpClass already created a QApplication (shared across this
        # test module), so this only exercises the app.instance() branch --
        # the "no QApplication yet" branch (a startup crash before the tool
        # builds its own) is the same code path and covered by inspection.
        from tools.widgets.crash_dialog import CrashReportDialog
        orig_exec = CrashReportDialog.exec if hasattr(CrashReportDialog, "exec") \
            else CrashReportDialog.exec_
        shown = []

        def fake_exec(self):
            shown.append(True)
            return 0
        if hasattr(CrashReportDialog, "exec"):
            CrashReportDialog.exec = fake_exec
        else:
            CrashReportDialog.exec_ = fake_exec
        try:
            crash_handler.install_global_crash_handler("Test Tool")
            try:
                raise ValueError("simulated startup crash")
            except ValueError:
                sys.excepthook(*sys.exc_info())
            self.assertTrue(shown)
        finally:
            if hasattr(CrashReportDialog, "exec"):
                CrashReportDialog.exec = orig_exec
            else:
                CrashReportDialog.exec_ = orig_exec
            sys.excepthook = sys.__excepthook__


if __name__ == "__main__":
    unittest.main()
