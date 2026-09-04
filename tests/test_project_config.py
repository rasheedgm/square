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
            ProjectConfig.from_defaults(overrides={"media_types": {
                "_default": {"base": "shot", "dir": "in/{media_type}/{name}",
                             "file": "{shot}_{media_type}.{ext}"}}})       # no version

    def test_partial_root_override_inherits_the_rest(self):
        # a project overriding only 'project' still gets the built-in 'shot'/
        # 'asset'/'delivery' roots -- omission is inheritance, not an error
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["roots"] = {"project": "{nas_root}/{project}/CUSTOM"}
        cfg = ProjectConfig(data=data)
        self.assertEqual(cfg.structural_errors(), [])
        self.assertEqual(cfg.roots["shot"], DEFAULT_PROJECT_CONFIG["roots"]["shot"])
        self.assertIn("CUSTOM", cfg.roots["project"])

    def test_explicitly_blanked_root_rejected(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["roots"] = {"shot": ""}          # an explicit override, not an omission
        cfg = ProjectConfig(data=data)
        self.assertIn("roots.shot is required", cfg.structural_errors())
        with self.assertRaises(ConfigError):
            cfg.check()

    def test_roots_key_entirely_absent_is_fine(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        del data["roots"]
        cfg = ProjectConfig(data=data)
        self.assertEqual(cfg.structural_errors(), [])
        self.assertEqual(cfg.roots, DEFAULT_PROJECT_CONFIG["roots"])

    def test_media_types_key_entirely_absent_is_fine(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        del data["media_types"]
        cfg = ProjectConfig(data=data)
        self.assertEqual(cfg.structural_errors(), [])
        self.assertEqual(cfg.media_type_names(), [])          # no named entries configured
        self.assertEqual(cfg.media_type("anything")["kitsu_kind"], "output")   # _default still resolves

    def test_delivery_presets_key_entirely_absent_is_fine(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        del data["delivery_presets"]
        cfg = ProjectConfig(data=data)
        d = cfg.delivery_template()
        self.assertEqual(d, DEFAULT_PROJECT_CONFIG["delivery_presets"]["_default"])

    def test_scalar_and_list_fields_fall_back_when_absent(self):
        data = {"roots": DEFAULT_PROJECT_CONFIG["roots"],
               "media_types": DEFAULT_PROJECT_CONFIG["media_types"]}   # nothing else at all
        cfg = ProjectConfig(data=data)
        self.assertEqual(cfg.fps, DEFAULT_PROJECT_CONFIG["fps"])
        self.assertEqual(cfg.version_pad, DEFAULT_PROJECT_CONFIG["version_pad"])
        self.assertEqual(cfg.frame_pad, DEFAULT_PROJECT_CONFIG["frame_pad"])
        self.assertEqual(cfg.copy_workers, DEFAULT_PROJECT_CONFIG["copy_workers"])
        self.assertEqual(cfg.colorspace, DEFAULT_PROJECT_CONFIG["colorspace"])
        self.assertEqual(cfg.slugify, DEFAULT_PROJECT_CONFIG["slugify"])
        # the real bug this locks in: project_folder_structure used to fall
        # back to [] instead of the built-in 4-entry list
        self.assertEqual(cfg.project_folder_structure,
                         DEFAULT_PROJECT_CONFIG["project_folder_structure"])
        self.assertEqual(cfg.asset_folder_structure,
                         DEFAULT_PROJECT_CONFIG["asset_folder_structure"])

    def test_partial_colorspace_inherits_siblings(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["colorspace"] = {"working": "sRGB"}       # only one key set
        cfg = ProjectConfig(data=data)
        self.assertEqual(cfg.colorspace["working"], "sRGB")
        self.assertEqual(cfg.colorspace["delivery"], DEFAULT_PROJECT_CONFIG["colorspace"]["delivery"])
        self.assertEqual(cfg.colorspace["ocio"], DEFAULT_PROJECT_CONFIG["colorspace"]["ocio"])


class TestMediaTypeLookup(unittest.TestCase):
    def setUp(self):
        self.cfg = ProjectConfig.from_defaults()

    def test_entry_inherits_file_from_default(self):
        t = self.cfg.media_type("Plate")
        self.assertEqual(t["dir"], "plates/{name}_v{version}")
        self.assertIn("{frame}", t["file"])              # inherited from _default
        self.assertEqual(t["base"], "shot")
        self.assertEqual(t["kitsu_kind"], "output")
        self.assertTrue(t["previewable"])                # from the Plate entry

    def test_unknown_type_uses_default(self):
        t = self.cfg.media_type("NoSuchType")
        self.assertEqual(t["dir"], "input/{media_type}/{name}_v{version}")

    def test_working_kind(self):
        self.assertEqual(self.cfg.media_type("NukeScript")["kitsu_kind"], "working")

    def test_media_type_names_excludes_default(self):
        names = self.cfg.media_type_names()
        self.assertIn("Plate", names)
        self.assertIn("CompRender", names)
        self.assertNotIn("_default", names)

    def test_media_type_names_filtered_by_source(self):
        delivery = self.cfg.media_type_names(source="delivery")
        self.assertIn("Plate", delivery)
        self.assertIn("Ref", delivery)
        self.assertNotIn("CompRender", delivery)      # source=publish (inherited)
        self.assertNotIn("NukeScript", delivery)      # source=work
        self.assertIn("CompRender", self.cfg.media_type_names(source="publish"))
        self.assertIn("NukeScript", self.cfg.media_type_names(source="work"))

    def test_delivery_template_client_overrides_default(self):
        cfg = ProjectConfig.from_defaults(overrides={"delivery_presets": {
            "ACME": {"container": "dpx"}}})
        d = cfg.delivery_template("ACME")
        self.assertEqual(d["container"], "dpx")
        self.assertEqual(d["colorspace"], "Rec.709")     # from _default

    def test_copy_workers_default(self):
        self.assertEqual(self.cfg.copy_workers, 4)

    def test_core_ships_no_tool_config(self):
        self.assertEqual(self.cfg.tools, {})
        self.assertEqual(self.cfg.tool("ingest"), {})    # a tool fills this in when installed


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

    def test_load_broken_media_type_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
            data["media_types"]["_default"]["dir"] = "in/{media_type}/{name}"
            data["media_types"]["_default"]["file"] = "{shot}_{media_type}.{ext}"  # no version
            p = Path(td) / "_pipeline" / "project_config.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ConfigError):
                ProjectConfig.load(td)

    def test_v1_config_migrates_on_load(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = {
                "schema_version": 1,
                "roots": DEFAULT_PROJECT_CONFIG["roots"],
                "templates": {
                    "output": {"base": "shot", "dir": "output/{output_type}/v{version}",
                               "file": "{shot}_{output_type}_v{version}.{frame}.{ext}"},
                    "workfile": {"base": "shot", "dir": "work/{task}",
                                 "file": "{shot}_{task}_v{version}.{ext}"},
                },
                "ingest": {
                    "default": {"base": "shot", "dir": "in/{media_type}/{name}_v{version}",
                                "file": "{shot}_{media_type}_{name}_v{version}.{frame}.{ext}"},
                    "by_type": {"Plate": {"dir": "plates/{name}_v{version}"}},
                },
                "copy_workers": 8,
            }
            p = Path(td) / "_pipeline" / "project_config.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(v1), encoding="utf-8")

            cfg = ProjectConfig.load(td)
            self.assertEqual(cfg.data["schema_version"], SCHEMA_VERSION)
            self.assertNotIn("templates", cfg.data)
            self.assertNotIn("ingest", cfg.data)
            self.assertEqual(cfg.media_type("Plate")["dir"], "plates/{name}_v{version}")
            self.assertEqual(cfg.media_type("Workfile")["kitsu_kind"], "working")
            self.assertEqual(cfg.media_type("Workfile")["source"], "work")
            self.assertEqual(cfg.media_type("Plate")["source"], "delivery")
            self.assertEqual(cfg.copy_workers, 8)              # stayed top-level
            self.assertEqual(cfg.tools, {})

    def test_orphaned_v2_ingest_keys_backfilled_on_load(self):
        """A config saved under schema_version 2 by an EARLIER commit of this
        same codebase (before copy_workers moved top-level and media_types
        gained `source`) must not silently lose copy_workers or misclassify
        every media type as source=publish just because schema_version never
        changed. Regression test for a real bug found in review: _migrate_v1
        only fires for schema_version < 2, so this v2-internal move needs its
        own always-on backfill."""
        with tempfile.TemporaryDirectory() as td:
            old_v2 = {
                "schema_version": 2,
                "roots": DEFAULT_PROJECT_CONFIG["roots"],
                "media_types": {
                    "_default": DEFAULT_PROJECT_CONFIG["media_types"]["_default"],
                    "Plate": {"dir": "plates/{name}_v{version}",
                             "previewable": True, "colorspace": "ACEScg"},
                    "CompRender": DEFAULT_PROJECT_CONFIG["media_types"]["CompRender"],
                },
                "tools": {"ingest": {"copy_workers": 8, "transfer_mode": "copy",
                                     "media_types": ["Plate"]}},
            }
            p = Path(td) / "_pipeline" / "project_config.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps(old_v2), encoding="utf-8")

            cfg = ProjectConfig.load(td)
            self.assertEqual(cfg.copy_workers, 8)
            self.assertEqual(cfg.media_type("Plate")["source"], "delivery")
            self.assertIn("Plate", cfg.media_type_names(source="delivery"))
            # a type never listed in tools.ingest.media_types keeps whatever
            # source it already resolves to (here: the _default's, "publish")
            # -- the backfill only tags what the old data actually told it to
            self.assertEqual(cfg.media_type("CompRender")["source"], "publish")

    def test_backfill_is_a_noop_for_a_config_that_never_had_the_old_shape(self):
        cfg = ProjectConfig.from_defaults()
        untouched = copy.deepcopy(cfg.data)
        from square_core.config.project import _backfill_v2_orphans
        self.assertEqual(_backfill_v2_orphans(cfg.data), untouched)



if __name__ == "__main__":
    unittest.main()
