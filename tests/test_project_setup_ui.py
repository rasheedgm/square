"""Headless smoke test for the project-setup Qt layer.

Skips entirely if no Qt binding is installed. Runs offscreen with modal
pop-ups neutered.
"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from Qt import QtWidgets
    _HAVE_QT = True
except Exception:
    _HAVE_QT = False

from square_core.model import Project

from tools.project_setup.core import ProjectAdmin
from tests.test_project_setup import _FakeCtx


@unittest.skipUnless(_HAVE_QT, "no Qt binding")
class TestProjectSetupUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        for m in ("information", "warning", "critical"):
            setattr(QtWidgets.QMessageBox, m, staticmethod(lambda *a, **k: None))

    def _admin(self, td, role="admin"):
        ctx = _FakeCtx(td, role=role)
        ctx.kitsu._projects.append(Project(id="p-ABC", code="ABC", name="A Big Corp"))
        return ProjectAdmin(ctx)

    def test_window_builds_with_three_tabs(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td))
            self.assertEqual(win.tabs.count(), 3)
            self.assertEqual(win.project_combo.count(), 2)   # placeholder + ABC

    def test_new_project_pane_creates_and_signals(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td))
            pane = win.new_pane
            pane.code.setText("NEW")
            pane.name.setText("New Show")
            self.assertIn("NEW", pane.path_hint.text())
            pane._create()
            self.assertIn("NEW", [p.code for p in win.admin.projects()])
            # the combo refreshed and switched to Breakdown
            self.assertEqual(win.current_project(), "NEW")
            self.assertIs(win.tabs.currentWidget(), win.breakdown_pane)

    def test_new_project_rejects_duplicate_code(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td))
            win.new_pane.code.setText("ABC")
            win.new_pane._create()
            self.assertEqual([p.code for p in win.admin.projects()], ["ABC"])   # no dup

    def test_breakdown_parse_then_apply(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td))
            win.project_combo.setCurrentIndex(1)             # ABC
            bp = win.breakdown_pane
            bp.paste.setPlainText("SQ010 SH0100 1001-1100\nSQ010 SH0110\nSQ010")  # last row bad
            bp._parse()
            self.assertEqual(bp.table.rowCount(), 3)
            self.assertTrue(bp.apply_btn.isEnabled())
            self.assertIn("2 shot", bp.apply_btn.text())
            bp._apply()
            shots = win.admin.existing_shots("ABC")
            self.assertEqual({s.code for s in shots}, {"SH0100", "SH0110"})
            self.assertEqual({s.sequence_code for s in shots}, {"SQ010"})
            # the existing-breakdown tree groups them under the sequence
            self.assertEqual(bp.existing.topLevelItem(0).text(0), "SQ010")

    def test_breakdown_remove_row(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td))
            win.project_combo.setCurrentIndex(1)
            bp = win.breakdown_pane
            bp.paste.setPlainText("SQ010 SH0100\nSQ010 SH0110\nSQ010 SH0120")
            bp._parse()
            bp.table.selectRow(1)
            bp._remove_selected_rows()
            self.assertEqual(bp.table.rowCount(), 2)
            self.assertEqual({bp.table.item(r, 1).text() for r in range(2)},
                             {"SH0100", "SH0120"})

    def test_roadmap_build(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td))
            win.project_combo.setCurrentIndex(1)
            win.admin.add_breakdown("ABC", _rows("SQ010 SH0100\nSQ010 SH0110"))
            rp = win.roadmap_pane
            rp.set_project("ABC")
            rp.custom.setText("Paint")
            rp._build()
            self.assertIn("task(s) ensured", rp.result.toPlainText())
            # Comp+Roto pre-checked (they exist on the fake) + Paint = 3 types x 2 shots
            self.assertEqual(len(win.admin.ctx.kitsu.tasks), 6)

    def test_read_only_role_disables_writes(self):
        from tools.project_setup.ui_main import MainWindow
        with tempfile.TemporaryDirectory() as td:
            win = MainWindow(self._admin(td, role="user"))
            self.assertFalse(win.new_pane.create_btn.isEnabled())
            win.project_combo.setCurrentIndex(1)
            win.breakdown_pane.paste.setPlainText("SQ010 SH0100")
            win.breakdown_pane._parse()
            self.assertFalse(win.breakdown_pane.apply_btn.isEnabled())
            self.assertFalse(win.roadmap_pane.build_btn.isEnabled())


def _rows(text):
    from tools.project_setup.core import parse_breakdown
    return parse_breakdown(text)


if __name__ == "__main__":
    unittest.main()
