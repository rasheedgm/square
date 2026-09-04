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


def _pipeline_and_sparse_project(tmp):
    """A project config with almost nothing in it -- like a hand-placed
    example file -- to prove untouched fields stay resolved-but-unwritten."""
    studio = Path(tmp) / "studio_config.json"
    studio.write_text(json.dumps({
        "kitsu_host": "http://localhost/api",
        "nas_roots": {"default": str(Path(tmp) / "nas")},
        "project_defaults": {},
    }), encoding="utf-8")
    root = Path(tmp) / "nas" / "ABC"
    p = ProjectConfig.path_for(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
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

    def test_untouched_fields_are_not_written_on_save(self):
        """Opening a sparse config shows every field resolved (builtin /
        studio-default), but saving without touching anything must not bake
        that resolved catalogue into the file -- only what was actually
        edited gets written. This is what makes a hand-placed minimal example
        config stay minimal after a save, and 'source: builtin' in the editor
        stay true after that save too."""
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            pc, sp, root = _pipeline_and_sparse_project(td)
            store = ConfigStore(pc, user=_User(), studio_path=sp)
            store.open_project(root, "ABC")
            pane = ScopePane("project", store)

            # the editor resolves everything (media_types, roots, ...)...
            self.assertEqual(store.field("project", "media_types").source, "builtin")
            self.assertGreater(len(pane._editors["media_types"].get_value()), 1)

            # ...but touch only fps...
            pane._editors["fps"].spin.setValue(30.0)
            self.assertTrue(pane.save())

            # ...and the file on disk must still be sparse: fps + schema_version
            # only, nothing else silently baked in
            on_disk = json.loads(ProjectConfig.path_for(root).read_text(encoding="utf-8"))
            self.assertEqual(set(on_disk) - {"schema_version"}, {"fps"})
            self.assertEqual(on_disk["fps"], 30.0)

            # media_types still resolves fully and is still sourced as builtin
            self.assertEqual(store.field("project", "media_types").source, "builtin")

    def test_registry_editor_gets_the_projects_real_padding(self):
        """The by-example template builder must preview against the project's
        actual version_pad/frame_pad, not the schema's bare defaults (3/4) --
        regression test for a review finding where RegistryEditor always used
        the hardcoded defaults regardless of what the project configured."""
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            pc, sp, root = _pipeline_and_project(td)
            ProjectConfig.from_defaults(overrides={"version_pad": 5, "frame_pad": 6}).save(root)
            store = ConfigStore(pc, user=_User(), studio_path=sp)
            store.open_project(root, "ABC")
            pane = ScopePane("project", store)
            self.assertEqual(pane._editors["media_types"]._version_pad, 5)
            self.assertEqual(pane._editors["media_types"]._frame_pad, 6)
            self.assertEqual(pane._editors["roots"]._version_pad, 5)

    def test_studio_scope_untouched_fields_are_not_written(self):
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            # the sparse studio_config.json a user would copy from the template
            # instructions -- just the two required keys
            studio = Path(td) / "studio_config.json"
            studio.write_text(json.dumps({
                "kitsu_host": "http://localhost/api",
                "nas_roots": {"default": str(Path(td) / "nas")},
            }), encoding="utf-8")
            store = ConfigStore(PipelineConfig.load(studio), user=_User(), studio_path=studio)
            pane = ScopePane("studio", store)

            self.assertEqual(store.field("studio", "fps").source, "builtin")
            pane._editors["fps"].spin.setValue(30.0)
            self.assertTrue(pane.save())

            on_disk = json.loads(studio.read_text(encoding="utf-8"))
            self.assertNotIn("media_types", on_disk.get("project_defaults", {}))
            self.assertEqual(on_disk["project_defaults"]["fps"], 30.0)
            self.assertEqual(on_disk["kitsu_host"], "http://localhost/api")

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
