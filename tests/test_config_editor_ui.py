"""Headless smoke test for the config editor's Qt layer.

Skips entirely if no Qt binding is installed. Runs with the offscreen platform
plugin and neutered modal pop-ups.
"""

import json
import os
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from Qt import QtWidgets
    _HAVE_QT = True
except Exception:
    _HAVE_QT = False

from square_core.config import ProjectConfig, PipelineConfig
from square_core.config.project import SCHEMA_VERSION


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
    p.write_text(json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8")
    return PipelineConfig.load(studio), studio, root


def _pipeline_and_project_with_studio_recipe(tmp):
    """A studio whose `project_defaults.delivery_presets` differs from the
    built-in, and a project whose own file genuinely never wrote a
    `delivery_presets` key (like a real project created before that studio
    preset existed) -- it must show the studio's version (source:
    studio-default) without that actually being written into the project's
    own file. Uses a sparse hand-placed-style file, like
    `_pipeline_and_sparse_project`, NOT `ProjectConfig.from_defaults().save()`
    -- that bakes every DEFAULT_PROJECT_CONFIG key (delivery_presets included)
    into the file, which would make delivery_presets look like an *override*
    instead of an absent key."""
    studio = Path(tmp) / "studio_config.json"
    studio.write_text(json.dumps({
        "kitsu_host": "http://localhost/api",
        "nas_roots": {"default": str(Path(tmp) / "nas")},
        "project_defaults": {
            "delivery_presets": {"ACME": {"container": "dpx"}},
        },
    }), encoding="utf-8")
    root = Path(tmp) / "nas" / "ABC"
    p = ProjectConfig.path_for(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8")
    return PipelineConfig.load(studio), studio, root


class _User:
    role = "admin"
    email = "admin@example.com"


class _FakeCtx:
    """Just enough of PipelineContext for MainWindow: a project list and a
    way to resolve one's root_path -- MainWindow never touches Kitsu beyond
    this."""
    def __init__(self, projects: dict[str, str]):
        # {code: root_path}
        self._roots = projects
        self.user = _User()
        self.kitsu = types.SimpleNamespace(
            projects=lambda: [types.SimpleNamespace(code=c, name=c) for c in projects])

    def project(self, code):
        return types.SimpleNamespace(project=types.SimpleNamespace(root_path=self._roots[code]))


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
            pane._confirm_pending = lambda pending: True
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
            pane._confirm_pending = lambda pending: True

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
            pane._confirm_pending = lambda pending: True

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

    def test_generic_dict_fields_get_a_table_not_raw_json(self):
        """nas_roots / slugify are plain {name: value} dicts -- same table
        shape as roots, just headed 'Value' (not 'Pattern', since these
        aren't {token} templates) and with no template-builder double-click."""
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.widgets.fields import make_field_editor
        from tools.config_editor.widgets.registries import RegistryEditor
        with tempfile.TemporaryDirectory() as td:
            pc, sp, root = _pipeline_and_project(td)
            store = ConfigStore(pc, user=_User(), studio_path=sp)

            fv = store.field("studio", "nas_roots")
            ed = make_field_editor(fv)
            self.assertIsInstance(ed, RegistryEditor)
            self.assertTrue(ed._string_mode)
            self.assertEqual(ed.table.horizontalHeaderItem(1).text(), "Value")
            self.assertEqual(ed.get_value(), {"default": str(Path(td) / "nas")})

            store.open_project(root, "ABC")
            fv2 = store.field("project", "slugify")
            ed2 = make_field_editor(fv2)
            self.assertIsInstance(ed2, RegistryEditor)
            v = ed2.get_value()
            self.assertEqual(v["spaces_to"], "_")

    def test_project_defaults_is_not_its_own_redundant_field(self):
        """Every scope=both key already writes into project_defaults
        individually -- showing the whole container too would just be one
        giant duplicate 'Edit JSON...' button."""
        from tools.config_editor.core import ConfigStore
        with tempfile.TemporaryDirectory() as td:
            pc, sp, root = _pipeline_and_project(td)
            store = ConfigStore(pc, user=_User(), studio_path=sp)
            keys = {f.key for f in store.fields("studio")}
            self.assertNotIn("project_defaults", keys)

    def test_roots_editor_is_string_mode(self):
        from tools.config_editor.widgets.fields import make_field_editor
        with tempfile.TemporaryDirectory() as td:
            store, _ = self._store(td)
            fv = next(f for f in store.fields("project") if f.key == "roots")
            ed = make_field_editor(fv)
            v = ed.get_value()
            self.assertEqual(v["project"], "{nas_root}/{project}")

    def test_editing_the_shot_root_pattern_resolves_project_root_reference(self):
        """Regression: double-clicking the built-in 'shot' root's pattern
        ('{project_root}/{episode}/shots/{sequence}/{shot}') threw "unknown
        token {project_root}" and permanently disabled OK -- {project_root}
        is a root-to-root reference resolved by
        square_core.paths.resolve_roots(), not a PathContext token, so the
        template builder's preview must expand it first, the same way
        PathResolver does at runtime."""
        from tools.config_editor.widgets.template_builder import TemplateBuilderDialog
        roots = {"project": "{nas_root}/{project}",
                 "shot": "{project_root}/{episode}/shots/{sequence}/{shot}"}
        dlg = TemplateBuilderDialog(roots["shot"], is_dir=True, root_context=roots)
        self.assertEqual(dlg.err.text(), "")
        self.assertTrue(dlg._ok.isEnabled())
        self.assertIn("ABC", dlg.preview.text())          # the sample project rendered in

    def test_template_builder_without_root_context_is_unaffected(self):
        """A media_type / delivery_preset pattern never has root_context --
        must behave exactly as before (no regression for the common case)."""
        from tools.config_editor.widgets.template_builder import TemplateBuilderDialog
        dlg = TemplateBuilderDialog("plates/{name}_v{version}", is_dir=True)
        self.assertTrue(dlg._ok.isEnabled())

    def test_set_override_writes_the_inherited_value_and_shows_as_override(self):
        """The whole feature: adopt an inherited studio-default value into
        the project's own file without changing it. Presence-based override
        means it correctly shows as a real override right away, and (unlike
        the old value-comparison check) it stays that way after a save +
        rebuild, not just until the next redraw."""
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            pc, sp, root = _pipeline_and_project_with_studio_recipe(td)
            store = ConfigStore(pc, user=_User(), studio_path=sp)
            store.open_project(root, "ABC")
            pane = ScopePane("project", store)
            pane._confirm_pending = lambda pending: True

            fv = store.field("project", "delivery_presets")
            self.assertEqual(fv.source, "studio-default")
            self.assertFalse(fv.overridden)

            # Save with nothing touched: still absent from the project's file
            self.assertTrue(pane.save())
            self.assertNotIn("delivery_presets", ProjectConfig.load(root).data)

            pane._set_override("delivery_presets")
            self.assertTrue(store.field("project", "delivery_presets").overridden)
            self.assertTrue(pane.save())

            on_disk = ProjectConfig.load(root)
            self.assertIn("delivery_presets", on_disk.data)
            self.assertEqual(on_disk.delivery_template("ACME")["container"], "dpx")
            # still shows as an override after the save + rebuild -- doesn't revert
            self.assertTrue(store.field("project", "delivery_presets").overridden)

    def test_frozen_project_shows_banner_and_disables_editors(self):
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            store, root = self._store(td)
            store.freeze_project()
            pane = ScopePane("project", store)
            self.assertFalse(pane._editors["fps"].isEnabled())

    def test_save_confirms_pending_changes_before_writing(self):
        """'save should show what is being changed' -- Save must not write
        silently once there's a real diff to show."""
        from tools.config_editor.core import ConfigStore
        from tools.config_editor.ui_main import ScopePane
        with tempfile.TemporaryDirectory() as td:
            store, root = self._store(td)
            pane = ScopePane("project", store)
            pane._editors["fps"].spin.setValue(30.0)
            pane._on_field_changed("fps")

            seen = {}
            pane._confirm_pending = lambda pending: seen.update(pending) or False
            self.assertFalse(pane.save())              # cancelled -- nothing written
            self.assertIn("fps", seen)
            self.assertNotEqual(ProjectConfig.load(root).fps, 30.0)

            pane._confirm_pending = lambda pending: True
            self.assertTrue(pane.save())
            self.assertEqual(ProjectConfig.load(root).fps, 30.0)


@unittest.skipUnless(_HAVE_QT, "no Qt binding")
class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        for m in ("information", "warning", "critical", "question"):
            setattr(QtWidgets.QMessageBox, m, staticmethod(lambda *a, **k: None))

    def _window(self, tmp):
        from tools.config_editor.core import ConfigStore
        pc, sp, root = _pipeline_and_project(tmp)
        ctx = _FakeCtx({"ABC": str(root)})
        store = ConfigStore(pc, user=_User(), studio_path=sp)
        from tools.config_editor.ui_main import MainWindow
        return MainWindow(ctx, store), root

    def test_starts_on_studio_scope(self):
        with tempfile.TemporaryDirectory() as td:
            win, _ = self._window(td)
            self.assertEqual(win.pane.scope, "studio")
            self.assertFalse(win.store.has_project)

    def test_picking_a_project_switches_to_project_scope(self):
        with tempfile.TemporaryDirectory() as td:
            win, root = self._window(td)
            win._select_code("ABC")
            self.assertEqual(win.pane.scope, "project")
            self.assertTrue(win.store.has_project)
            self.assertEqual(win.store.project_code, "ABC")

    def test_switch_with_unsaved_changes_cancel_stays_put(self):
        with tempfile.TemporaryDirectory() as td:
            win, _ = self._window(td)
            win._select_code("ABC")
            win.pane._editors["fps"].spin.setValue(30.0)
            win.pane._on_field_changed("fps")

            win._confirm_switch = lambda: "cancel"
            win._project_combo.setCurrentIndex(0)          # try to go back to studio-only
            self.assertEqual(win.pane.scope, "project")     # blocked -- stayed on the project
            self.assertEqual(win.store.project_code, "ABC")

    def test_switch_with_unsaved_changes_discard_proceeds(self):
        with tempfile.TemporaryDirectory() as td:
            win, _ = self._window(td)
            win._select_code("ABC")
            win.pane._editors["fps"].spin.setValue(30.0)
            win.pane._on_field_changed("fps")

            win._confirm_switch = lambda: "discard"
            win._project_combo.setCurrentIndex(0)
            self.assertEqual(win.pane.scope, "studio")
            self.assertFalse(win.store.has_project)

    def test_switch_with_unsaved_changes_save_persists_then_proceeds(self):
        with tempfile.TemporaryDirectory() as td:
            win, root = self._window(td)
            win._select_code("ABC")
            win.pane._editors["fps"].spin.setValue(30.0)
            win.pane._on_field_changed("fps")
            win.pane._confirm_pending = lambda pending: True

            win._confirm_switch = lambda: "save"
            win._project_combo.setCurrentIndex(0)
            self.assertEqual(win.pane.scope, "studio")
            self.assertEqual(ProjectConfig.load(root).fps, 30.0)

    def test_freeze_button_disabled_on_studio_scope_and_when_frozen(self):
        with tempfile.TemporaryDirectory() as td:
            win, _ = self._window(td)
            self.assertFalse(win._freeze_btn.isEnabled())   # studio scope

            win._select_code("ABC")
            win._update_title()
            self.assertTrue(win._freeze_btn.isEnabled())

            win.store.freeze_project()
            win._update_title()
            self.assertFalse(win._freeze_btn.isEnabled())
            self.assertFalse(win._save_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
