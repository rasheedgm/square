"""Headless smoke test for the config editor's Qt layer.

Skips entirely if no Qt binding is installed. Runs with the offscreen platform
plugin and neutered modal pop-ups.
"""

import json
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

from square_core.config import ProjectConfig, PipelineConfig


def _pipeline_and_project(tmp):
    studio = Path(tmp) / "studio_config.json"
    studio.write_text(json.dumps({
        "kitsu_host": "http://localhost/api",
        "nas_roots": {"default": str(Path(tmp) / "nas")},
        "project_defaults": {},
    }), encoding="utf-8")
    root = Path(tmp) / "nas" / "ABC"
    ProjectConfig.from_defaults().save(root)
    return PipelineConfig.load(studio), studio, root


class _User:
    role = "admin"
    email = "admin@example.com"


@unittest.skipUnless(_HAVE_QT, "no Qt binding")
class TestEditorUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        for m in ("information", "warning", "critical"):
            setattr(QtWidgets.QMessageBox, m, staticmethod(lambda *a, **k: None))

    def _store(self, tmp):
        from tools.config_editor.core import ConfigStore
        pc, sp, root = _pipeline_and_project(tmp)
        s = ConfigStore(pc, user=_User(), studio_path=sp)
        s.open_project(root, "ABC")
        return s, root

    def test_scope_pane_builds_and_saves(self):
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            store, root = self._store(td)
            pane = ScopePane("project", store)
            self.assertIn("fps", pane._editors)
            pane._editors["fps"].spin.setValue(30.0)
            self.assertTrue(pane.save())
            self.assertEqual(ProjectConfig.load(root).fps, 30.0)

    def test_template_builder_validates(self):
        from tools.config_editor.widgets.template_builder import TemplateBuilderDialog
        good = TemplateBuilderDialog("plates/{name}_v{version}", is_dir=True)
        self.assertTrue(good._ok.isEnabled())
        self.assertEqual(good.preview.text(), "plates/bg_v003")
        bad = TemplateBuilderDialog("{nope}/x", is_dir=True)
        self.assertFalse(bad._ok.isEnabled())

    def test_registry_editor_roundtrips(self):
        from tools.config_editor.widgets.fields import make_field_editor
        with tempfile.TemporaryDirectory() as td:
            store, _ = self._store(td)
            fv = next(f for f in store.fields("project") if f.key == "media_types")
            ed = make_field_editor(fv)
            v = ed.get_value()
            self.assertIn("_default", v)
            self.assertEqual(v["Plate"]["dir"], "plates/{name}_v{version}")

    def test_roots_editor_is_string_mode(self):
        from tools.config_editor.widgets.fields import make_field_editor
        with tempfile.TemporaryDirectory() as td:
            store, _ = self._store(td)
            fv = next(f for f in store.fields("project") if f.key == "roots")
            ed = make_field_editor(fv)
            v = ed.get_value()
            self.assertEqual(v["project"], "{nas_root}/{project}")


if __name__ == "__main__":
    unittest.main()
