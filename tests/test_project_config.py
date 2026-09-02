"""square_core.config.ProjectConfig -- load/save/validate of the per-project
path config."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from square_core.config import ProjectConfig, ConfigError
from square_core.config.project import DEFAULT_PROJECT_CONFIG, SCHEMA_VERSION


class TestFromDefaults(unittest.TestCase):
    def test_default_is_valid(self):
        cfg = ProjectConfig.from_defaults()
        self.assertEqual(cfg.version_pad, 3)
        self.assertEqual(cfg.fps, 24.0)
        self.assertIn("project", cfg.roots)

    def test_overrides_deep_merge(self):
        cfg = ProjectConfig.from_defaults(overrides={"fps": 25.0,
                                                     "colorspace": {"working": "sRGB"}})
        self.assertEqual(cfg.fps, 25.0)
        # untouched colorspace keys survive the deep merge
        self.assertEqual(cfg.colorspace["working"], "sRGB")
        self.assertEqual(cfg.colorspace["delivery"], "Rec.709")

    def test_broken_override_rejected(self):
        with self.assertRaises(ConfigError):
            ProjectConfig.from_defaults(overrides={"templates": {
                "workfile": {"base": "shot", "file": "{shot}.{ext}"},        # no version
                "output": {"base": "shot", "file": "{shot}_v{version}.{ext}"},
            }})

    def test_missing_root_rejected(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["roots"] = {"project": "{nas_root}/{project}"}      # no 'shot'
        cfg = ProjectConfig(data=data)
        self.assertIn("roots.shot is required", cfg.structural_errors())
        with self.assertRaises(ConfigError):
            cfg.check()


class TestTemplateLookup(unittest.TestCase):
    def setUp(self):
        self.cfg = ProjectConfig.from_defaults()

    def test_ingest_template_inherits_file_from_default(self):
        t = self.cfg.ingest_template("Plate")
        self.assertEqual(t["dir"], "plates/{name}_v{version}")
        self.assertIn("{frame}", t["file"])          # inherited from ingest.default
        self.assertEqual(t["base"], "shot")

    def test_ingest_template_unknown_falls_back(self):
        t = self.cfg.ingest_template("NoSuchType")
        self.assertEqual(t["dir"], "input/{media_type}/{name}_v{version}")

    def test_delivery_template_client_overrides_default(self):
        cfg = ProjectConfig.from_defaults(overrides={"delivery_presets": {
            "ACME": {"container": "dpx"}}})
        d = cfg.delivery_template("ACME")
        self.assertEqual(d["container"], "dpx")
        self.assertEqual(d["colorspace"], "Rec.709")     # from default

    def test_template_missing_raises(self):
        with self.assertRaises(ConfigError):
            self.cfg.template("nope")


class TestLoadSave(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ABC"
            cfg = ProjectConfig.from_defaults(overrides={"fps": 30.0})
            written = cfg.save(root)
            self.assertEqual(written, root / "_pipeline" / "project_config.json")
            self.assertTrue(written.exists())

            back = ProjectConfig.load(root)
            self.assertEqual(back.fps, 30.0)
            self.assertEqual(back.data, cfg.data)

    def test_load_missing_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ConfigError):
                ProjectConfig.load(Path(td) / "nope")

    def test_load_bad_json_raises(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "_pipeline" / "project_config.json"
            p.parent.mkdir(parents=True)
            p.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(ConfigError):
                ProjectConfig.load(td)

    def test_load_newer_schema_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
            data["schema_version"] = SCHEMA_VERSION + 1
            p = Path(td) / "_pipeline" / "project_config.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                ProjectConfig.load(td)

    def test_load_broken_templates_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
            data["templates"]["output"] = {"base": "shot", "file": "{shot}.{ext}"}  # no version
            p = Path(td) / "_pipeline" / "project_config.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                ProjectConfig.load(td)




if __name__ == "__main__":
    unittest.main()
