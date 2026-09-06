"""Headless smoke test for the workfile-manager Qt layer."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from Qt import QtWidgets
    _HAVE_QT = True
except Exception:
    _HAVE_QT = False

from tests.test_workfile_manager import _hub


@unittest.skipUnless(_HAVE_QT, "no Qt binding")
class TestWorkfileManagerUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        for m in ("information", "warning", "critical"):
            setattr(QtWidgets.QMessageBox, m, staticmethod(lambda *a, **k: None))

    def test_window_builds_and_navigates_to_a_task(self):
        from tools.workfile_manager.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            win = MainWindow(hub)
            win.project_combo.setCurrentIndex(1)                 # ABC
            self.assertEqual(win.tree.topLevelItemCount(), 1)    # one sequence
            seq = win.tree.topLevelItem(0)
            win.tree.setCurrentItem(seq.child(0))                # SH0100
            self.assertGreaterEqual(win.task_list.count(), 2)
            self.assertFalse(win.new_btn.isEnabled())            # no task selected yet
            win.task_list.setCurrentRow(0)
            self.assertTrue(win.new_btn.isEnabled())
            self.assertIn("Comp", win.task_header.text() + win.task_list.item(0).text())

    def test_new_version_flow(self):
        from tools.workfile_manager import ui_main
        with tempfile.TemporaryDirectory() as td:
            hub, api = _hub(td)
            win = ui_main.MainWindow(hub)
            win.project_combo.setCurrentIndex(1)
            win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
            win.task_list.setCurrentRow(0)

            # drive the dialog without showing it
            orig = ui_main.exec_dialog
            ui_main.exec_dialog = lambda dlg: ui_main.DIALOG_ACCEPTED
            try:
                win._new_workfile()
            finally:
                ui_main.exec_dialog = orig
            self.assertEqual(len(api.workfiles), 1)
            self.assertEqual(win.wf_table.rowCount(), 1)
            # empty-seed: the major is reserved, no minor file on disk yet
            self.assertEqual(win.wf_table.item(0, 0).text(), "v001")
            self.assertEqual(win.wf_table.item(0, 2).text(), "offline")

    def test_publish_output_flow(self):
        from tools.workfile_manager import ui_main
        with tempfile.TemporaryDirectory() as td:
            hub, api = _hub(td)
            win = ui_main.MainWindow(hub)
            win.project_combo.setCurrentIndex(1)
            win.tree.setCurrentItem(win.tree.topLevelItem(0).child(0))
            win.task_list.setCurrentRow(0)

            render = Path(td) / "r"
            render.mkdir()
            for f in (1001, 1002):
                (render / f"c.{f}.exr").write_bytes(b"x" * 20)

            _pub = hub.publish_output
            hub.publish_output = lambda *a, **k: _pub(*a, **{**k, "proxy_dry_run": True})

            orig = ui_main.exec_dialog

            def fake_exec(dlg):
                dlg.media_type.setCurrentText("CompRender")
                dlg.path.setText(str(render))
                dlg._scan()
                return ui_main.DIALOG_ACCEPTED

            ui_main.exec_dialog = fake_exec
            try:
                win._publish_output()
            finally:
                ui_main.exec_dialog = orig
            self.assertEqual(len(api.outputs), 1)
            self.assertGreaterEqual(win.out_table.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
