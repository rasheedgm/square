"""square_core.config.schema -- the ConfigKey registry + validate/resolve."""

import copy
import unittest

from square_core.config import schema, ProjectConfig, ConfigError
from square_core.config.schema import ConfigKey, SchemaError
from square_core.config.project import DEFAULT_PROJECT_CONFIG


class TestConfigKey(unittest.TestCase):
    def test_rejects_unknown_kind(self):
        with self.assertRaises(SchemaError):
            ConfigKey("x", "banana")

    def test_rejects_bad_scope(self):
        with self.assertRaises(SchemaError):
            ConfigKey("x", "str", scope="galaxy")

    def test_enum_needs_choices(self):
        with self.assertRaises(SchemaError):
            ConfigKey("x", "enum")

    def test_applies_to(self):
        self.assertTrue(ConfigKey("x", "str", scope="both").applies_to("project"))
        self.assertTrue(ConfigKey("x", "str", scope="both").applies_to("studio"))
        self.assertFalse(ConfigKey("x", "str", scope="project").applies_to("studio"))


class RegistryCase(unittest.TestCase):
    def setUp(self):
        self._saved = schema.all()

    def tearDown(self):
        schema.clear()
        for k, ck in self._saved.items():
            schema._REGISTRY[k] = ck


class TestRegister(RegistryCase):
    def test_idempotent_same_descriptor(self):
        schema.register("tools.demo.n", "int", scope="project", default=1)
        schema.register("tools.demo.n", "int", scope="project", default=1)   # no raise
        self.assertEqual(schema.get("tools.demo.n").default, 1)

    def test_conflict_raises(self):
        schema.register("tools.demo.n", "int", scope="project", default=1)
        with self.assertRaises(SchemaError):
            schema.register("tools.demo.n", "int", scope="project", default=2)

    def test_for_scope(self):
        proj = {ck.key for ck in schema.for_scope("project")}
        studio = {ck.key for ck in schema.for_scope("studio")}
        self.assertIn("fps", proj)                 # scope=both
        self.assertIn("fps", studio)
        self.assertIn("kitsu_host", studio)
        self.assertNotIn("kitsu_host", proj)       # scope=studio
        self.assertIn("delivery_presets", proj)
        self.assertNotIn("delivery_presets", studio)   # scope=project


class TestResolve(RegistryCase):
    def test_project_wins(self):
        v = schema.resolve({"fps": 30.0}, "fps", pipeline_defaults={"fps": 25.0})
        self.assertEqual(v, 30.0)

    def test_falls_back_to_pipeline_defaults(self):
        v = schema.resolve({}, "fps", pipeline_defaults={"fps": 25.0})
        self.assertEqual(v, 25.0)

    def test_falls_back_to_builtin_default(self):
        self.assertEqual(schema.resolve({}, "version_pad"), 3)

    def test_dotted(self):
        data = {"colorspace": {"working": "sRGB"}}
        self.assertEqual(schema.resolve(data, "colorspace.working"), "sRGB")

    def test_unknown_key_resolves_none(self):
        self.assertIsNone(schema.resolve({}, "no.such.key"))


class TestPut(unittest.TestCase):
    def test_creates_intermediate_dicts(self):
        d = {}
        schema.put(d, "tools.ingest.copy_workers", 8)
        self.assertEqual(d, {"tools": {"ingest": {"copy_workers": 8}}})

    def test_overwrites_scalar_with_branch(self):
        d = {"tools": "oops"}
        schema.put(d, "tools.ingest.x", 1)
        self.assertEqual(d["tools"]["ingest"]["x"], 1)


class TestCheckValue(RegistryCase):
    def test_type_mismatch(self):
        ck = schema.get("version_pad")
        self.assertTrue(schema.check_value(ck, "3"))          # str, not int
        self.assertFalse(schema.check_value(ck, 3))

    def test_bool_is_not_int(self):
        self.assertTrue(schema.check_value(schema.get("version_pad"), True))

    def test_range(self):
        ck = schema.get("version_pad")
        self.assertTrue(schema.check_value(ck, 0))            # below min 1
        self.assertTrue(schema.check_value(ck, 99))           # above max 6
        self.assertFalse(schema.check_value(ck, 4))

    def test_enum(self):
        ck = schema.register("tools.demo.mode", "enum", scope="project",
                             default="copy", choices=("copy", "hardlink", "symlink"))
        self.assertFalse(schema.check_value(ck, "copy"))
        self.assertTrue(schema.check_value(ck, "teleport"))

    def test_list_item_kind(self):
        ck = schema.get("shot_folder_structure")
        self.assertFalse(schema.check_value(ck, ["a", "b"]))
        self.assertTrue(schema.check_value(ck, ["a", 3]))


class TestValidate(RegistryCase):
    def test_default_project_config_clean(self):
        errs, warns = schema.validate(DEFAULT_PROJECT_CONFIG, "project")
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

    def test_required_missing_is_error(self):
        errs, _ = schema.validate({}, "studio")     # kitsu_host / nas_roots are still required
        self.assertTrue(any("kitsu_host" in e for e in errs))
        self.assertTrue(any("nas_roots" in e for e in errs))

    def test_media_types_and_roots_are_not_required(self):
        # they fall back to DEFAULT_PROJECT_CONFIG per sub-key when the whole
        # key is absent (ProjectConfig.roots / .media_type()) -- schema-level
        # "required" would fight that, so neither is required here
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        del data["media_types"]
        del data["roots"]
        errs, _ = schema.validate(data, "project")
        self.assertEqual(errs, [])

    def test_studio_scope_does_not_require_both_keys(self):
        # roots/media_types are scope=both but live in project_defaults, not
        # studio_config.json itself
        errs, _ = schema.validate({"kitsu_host": "http://x/api",
                                   "nas_roots": {"default": "X:/p"}}, "studio")
        self.assertEqual(errs, [])

    def test_unknown_key_is_warning_not_error(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["colour_space"] = "typo"
        errs, warns = schema.validate(data, "project")
        self.assertEqual(errs, [])
        self.assertTrue(any("colour_space" in w for w in warns))

    def test_structured_key_not_walked(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        # a weird sub-key inside media_types must NOT be reported as unknown
        data["media_types"]["Plate"]["nonsense"] = 1
        _, warns = schema.validate(data, "project")
        self.assertFalse(any("media_types" in w for w in warns))

    def test_bad_value_is_error(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["fps"] = "twenty-four"
        errs, _ = schema.validate(data, "project")
        self.assertTrue(any("fps" in e for e in errs))


class TestProjectConfigCheckUsesSchema(RegistryCase):
    def test_check_rejects_bad_typed_value(self):
        data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
        data["version_pad"] = "wide"
        with self.assertRaises(ConfigError):
            ProjectConfig(data=data).check()

    def test_check_rejects_out_of_range(self):
        with self.assertRaises(ConfigError):
            ProjectConfig.from_defaults(overrides={"fps": 9999.0})


if __name__ == "__main__":
    unittest.main()
