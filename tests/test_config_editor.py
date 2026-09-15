"""tools.config_editor.core.editor.ConfigStore -- the only writer of config."""

import json
import tempfile
import unittest
from pathlib import Path

from square_core.config import ProjectConfig, PipelineConfig, ConfigError
from tools.config_editor.core import ConfigStore, NotAuthorized


class _User:
    def __init__(self, role, email="a@b.c"):
        self.role = role
        self.email = email


def _pipeline(tmp, project_defaults=None):
    studio = Path(tmp) / "studio_config.json"
    studio.write_text(json.dumps({
        "kitsu_url": "http://localhost/api",
        "kitsu_user": "admin@example.com",          # legacy key -- must survive a save
        "nas_roots": {"default": str(Path(tmp) / "nas")},
        "project_defaults": project_defaults or {},
    }), encoding="utf-8")
    return PipelineConfig.load(studio), studio


def _project(tmp, code="ABC", overrides=None, defaults=None):
    root = Path(tmp) / "nas" / code
    ProjectConfig.from_defaults(defaults, overrides=overrides).save(root)
    return root


class TestReads(unittest.TestCase):
    def test_studio_fields(self):
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td)
            store = ConfigStore(pc, user=_User("admin"), studio_path=sp)
            keys = {f.key for f in store.fields("studio")}
            self.assertIn("kitsu_host", keys)
            self.assertIn("fps", keys)                 # scope=both shows in studio
            self.assertNotIn("delivery_presets", keys)  # project-only

    def test_project_value_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td, project_defaults={"version_pad": 4})
            # a sparse project file -- only fps was ever actually written
            root = Path(td) / "nas" / "ABC"
            p = ProjectConfig.path_for(root)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"schema_version": 2, "fps": 25.0}), encoding="utf-8")
            store = ConfigStore(pc, user=_User("manager"), studio_path=sp)
            store.open_project(root, "ABC")

            fps = store.field("project", "fps")
            self.assertEqual(fps.value, 25.0)
            self.assertEqual(fps.source, "project")
            self.assertTrue(fps.overridden)

            # absent from the project's own file -> tracks the studio
            # default live, whatever its value happens to be
            vp = store.field("project", "version_pad")
            self.assertEqual(vp.value, 4)
            self.assertEqual(vp.source, "studio-default")
            self.assertFalse(vp.overridden)

            fp = store.field("project", "frame_pad")
            self.assertEqual(fp.source, "builtin")

    def test_presence_alone_makes_an_override_even_if_the_value_matches(self):
        """The whole point of "set override" (and a frozen project's full
        bake): writing the CURRENT value unchanged into the project's own
        file must still show as an override -- override means "this key is
        explicitly present", not "this key's value differs"."""
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td, project_defaults={"version_pad": 4})
            root = Path(td) / "nas" / "ABC"
            p = ProjectConfig.path_for(root)
            p.parent.mkdir(parents=True, exist_ok=True)
            # version_pad written explicitly, but with the SAME value the
            # studio default already resolves to
            p.write_text(json.dumps({"schema_version": 2, "version_pad": 4}), encoding="utf-8")
            store = ConfigStore(pc, user=_User("manager"), studio_path=sp)
            store.open_project(root, "ABC")

            vp = store.field("project", "version_pad")
            self.assertEqual(vp.value, 4)
            self.assertEqual(vp.source, "project")
            self.assertTrue(vp.overridden)


class TestFreeze(unittest.TestCase):
    def _sparse_store(self, td, role="admin", project_defaults=None):
        pc, sp = _pipeline(td, project_defaults=project_defaults)
        root = Path(td) / "nas" / "ABC"
        p = ProjectConfig.path_for(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")   # genuinely sparse
        s = ConfigStore(pc, user=_User(role), studio_path=sp)
        s.open_project(root, "ABC")
        return s, root

    def test_freeze_writes_every_field_and_sets_the_flag(self):
        with tempfile.TemporaryDirectory() as td:
            s, root = self._sparse_store(td, project_defaults={"version_pad": 4})
            self.assertFalse(s.field("project", "version_pad").overridden)

            s.freeze_project()
            self.assertTrue(s.is_frozen)
            vp = s.field("project", "version_pad")
            self.assertEqual(vp.value, 4)
            self.assertTrue(vp.overridden)          # now explicitly present
            self.assertEqual(s.field("project", "fps").value, 24.0)
            self.assertTrue(s.field("project", "fps").overridden)

            # still requires an explicit save to hit disk
            self.assertNotIn("_frozen", json.loads(
                ProjectConfig.path_for(root).read_text(encoding="utf-8")))
            s.save_project()
            on_disk = json.loads(ProjectConfig.path_for(root).read_text(encoding="utf-8"))
            self.assertTrue(on_disk["_frozen"])
            self.assertEqual(on_disk["version_pad"], 4)

    def test_frozen_project_refuses_further_edits(self):
        with tempfile.TemporaryDirectory() as td:
            s, _ = self._sparse_store(td)
            s.freeze_project()
            with self.assertRaises(NotAuthorized):
                s.set("project", "fps", 30.0)
            with self.assertRaises(NotAuthorized):
                s.reset("fps")

    def test_freeze_twice_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            s, _ = self._sparse_store(td)
            s.freeze_project()
            with self.assertRaises(ValueError):
                s.freeze_project()

    def test_frozen_flag_is_not_a_normal_field(self):
        with tempfile.TemporaryDirectory() as td:
            s, _ = self._sparse_store(td)
            s.freeze_project()
            keys = {fv.key for fv in s.fields("project")}
            self.assertNotIn("_frozen", keys)


class TestEdits(unittest.TestCase):
    def _store(self, td, role="admin", pd=None, ov=None):
        pc, sp = _pipeline(td, project_defaults=pd)
        _project(td, "ABC", overrides=ov)
        s = ConfigStore(pc, user=_User(role), studio_path=sp)
        s.open_project(Path(td) / "nas" / "ABC", "ABC")
        return s

    def test_set_validates(self):
        with tempfile.TemporaryDirectory() as td:
            s = self._store(td)
            with self.assertRaises(ValueError):
                s.set("project", "version_pad", "wide")      # not an int
            with self.assertRaises(ValueError):
                s.set("project", "fps", 9999.0)              # over maximum

    def test_set_then_save_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            s = self._store(td)
            s.set("project", "fps", 30.0)
            self.assertEqual(s.pending("project"), {"fps": (24.0, 30.0)})
            path, bak = s.save_project()
            self.assertTrue(path.exists())
            self.assertIsNotNone(bak)                        # existing file backed up
            self.assertEqual(ProjectConfig.load(path.parent.parent).fps, 30.0)

    def test_reset_drops_override(self):
        with tempfile.TemporaryDirectory() as td:
            s = self._store(td, pd={"version_pad": 4}, ov={"version_pad": 6})
            self.assertEqual(s.field("project", "version_pad").value, 6)
            self.assertTrue(s.reset("version_pad"))
            self.assertEqual(s.field("project", "version_pad").value, 4)  # back to studio
            self.assertFalse(s.reset("version_pad"))

    def test_save_studio_preserves_legacy_keys(self):
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td)
            s = ConfigStore(pc, user=_User("admin"), studio_path=sp)
            s.set("studio", "kitsu_host", "http://kitsu.local/api")
            s.save_studio()
            back = json.loads(sp.read_text(encoding="utf-8"))
            self.assertEqual(back["kitsu_host"], "http://kitsu.local/api")
            self.assertEqual(back["kitsu_user"], "admin@example.com")     # untouched

    def test_studio_scope_both_key_goes_into_project_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td)
            s = ConfigStore(pc, user=_User("admin"), studio_path=sp)
            # a scope=both key is a project setting -> lives under project_defaults
            s.set("studio", "fps", 48.0)
            s.save_studio()
            back = json.loads(sp.read_text(encoding="utf-8"))
            self.assertEqual(back["project_defaults"]["fps"], 48.0)
            self.assertNotIn("fps", {k for k in back if k != "project_defaults"})
            self.assertEqual(s.field("studio", "fps").value, 48.0)

    def test_save_rejects_config_that_breaks_paths(self):
        with tempfile.TemporaryDirectory() as td:
            s = self._store(td)
            # a media_types._default whose file no longer varies by version
            s.set("project", "media_types",
                  {"_default": {"base": "shot", "dir": "in/{name}",
                                "file": "{shot}_{name}.{ext}", "kitsu_kind": "output"}})
            with self.assertRaises(ConfigError):
                s.save_project()


class TestAuth(unittest.TestCase):
    def test_plain_user_cannot_save(self):
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td)
            _project(td, "ABC")
            s = ConfigStore(pc, user=_User("user"), studio_path=sp)
            s.open_project(Path(td) / "nas" / "ABC", "ABC")
            s.set("project", "fps", 30.0)
            self.assertFalse(s.can_write())
            with self.assertRaises(NotAuthorized):
                s.save_project()

    def test_offline_session_cannot_save(self):
        with tempfile.TemporaryDirectory() as td:
            pc, sp = _pipeline(td)
            s = ConfigStore(pc, user=None, studio_path=sp)
            with self.assertRaises(NotAuthorized):
                s.save_studio()


if __name__ == "__main__":
    unittest.main()
