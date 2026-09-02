"""square_core.paths.PathResolver -- pure path resolution from ProjectConfig."""

import unittest

import copy

from square_core.config import ProjectConfig
from square_core.config.project import DEFAULT_PROJECT_CONFIG
from square_core.model import PathContext
from square_core.paths import PathResolver, PathError, slugify


def _cfg(**over):
    """A validated config with `over` deep-merged over the defaults."""
    return ProjectConfig.from_defaults(overrides=over or None)


def _raw_cfg(**over):
    """An UNvalidated config -- for testing what validate() / the resolver do
    with a broken one."""
    data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
    data.update(over)
    return ProjectConfig(data=data)


def _ctx(**kw):
    base = dict(nas_root="X:/projects", project="ABC", sequence="SQ010", shot="SH0100")
    base.update(kw)
    return PathContext(**base)


class TestSlugify(unittest.TestCase):
    def test_preserves_case(self):
        self.assertEqual(slugify("Sh010"), "Sh010")

    def test_spaces_and_illegal(self):
        self.assertEqual(slugify("BG Plate"), "BG_Plate")
        self.assertEqual(slugify('a:b/c*d'), "abcd")

    def test_collapses_repeats(self):
        self.assertEqual(slugify("a__b"), "a_b")


class TestBasicPaths(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver(_cfg())

    def test_project_root(self):
        self.assertEqual(self.r.project_root(_ctx()), "X:/projects/ABC")

    def test_shot_dir_no_episode(self):
        self.assertEqual(self.r.shot_dir(_ctx()), "X:/projects/ABC/shots/SQ010/SH0100")

    def test_shot_dir_episodic(self):
        self.assertEqual(
            self.r.shot_dir(_ctx(episode="EP02")),
            "X:/projects/ABC/shots/SQ010/SH0100".replace("/shots", "/EP02/shots"),
        )

    def test_workfile_path(self):
        p = self.r.workfile_path(_ctx(task="comp", software="nuke", version=3, ext="nk"))
        self.assertEqual(
            p,
            "X:/projects/ABC/shots/SQ010/SH0100/work/comp/nuke/ABC_SQ010_SH0100_comp_main_v003.nk",
        )

    def test_output_path_with_frame(self):
        p = self.r.output_path(_ctx(output_type="comp", version=2, representation="exr",
                                    ext="exr", frame=1001, name="main"))
        self.assertEqual(
            p,
            "X:/projects/ABC/shots/SQ010/SH0100/output/comp/v002/exr/"
            "ABC_SQ010_SH0100_comp_main_v002.1001.exr",
        )

    def test_output_dir_drops_file_and_frame(self):
        d = self.r.output_dir(_ctx(output_type="comp", version=2, representation="exr"))
        self.assertEqual(d, "X:/projects/ABC/shots/SQ010/SH0100/output/comp/v002/exr")

    def test_output_dir_optional_representation_collapses(self):
        d = self.r.output_dir(_ctx(output_type="comp", version=2))
        self.assertEqual(d, "X:/projects/ABC/shots/SQ010/SH0100/output/comp/v002")

    def test_name_appears(self):
        p = self.r.output_path(_ctx(output_type="comp", version=1, name="matte",
                                    representation="exr", ext="exr", frame=1001))
        self.assertIn("_matte_v001", p)


class TestTokenRules(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver(_cfg())

    def test_version_pad_default(self):
        self.assertIn("v007", self.r.workfile_path(_ctx(task="c", version=7, ext="nk")))

    def test_version_spec_override(self):
        cfg = _cfg(templates={"output": {
            "base": "shot", "dir": "o/v{version:04d}",
            "file": "{shot}_v{version:04d}.{frame}.{ext}"}})
        r = PathResolver(cfg)
        self.assertTrue(r.output_dir(_ctx(output_type="c", version=5)).endswith("/v0005"))

    def test_media_type_slugified_in_filename(self):
        f = self.r.ingest_dest_file(_ctx(output_type="BG Plate", name="bg", version=1,
                                         ext="exr", frame=1001))
        self.assertIn("BG_Plate", f)

    def test_missing_required_raises(self):
        with self.assertRaises(PathError):
            self.r.output_path(_ctx(version=1, ext="exr", frame=1001))   # no output_type

    def test_unknown_token_raises(self):
        cfg = _raw_cfg(templates={"output": {"base": "shot", "dir": "o",
                                             "file": "{shot}_{bogus}.{ext}"}})
        with self.assertRaises(PathError):
            PathResolver(cfg).output_path(_ctx(output_type="c", ext="exr"))

    def test_case_upper_on_delivery(self):
        cfg = _cfg(delivery_presets={"ACME": {"case": "upper",
                                              "file": "acme_{shot}_v{version}.{ext}"}})
        r = PathResolver(cfg)
        f = r.delivery_file(_ctx(client="ACME", shot="sh0100", version=1, ext="dpx"))
        self.assertEqual(f, "ACME_SH0100_V001.DPX")


class TestIngestAndSkeleton(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver(_cfg())

    def test_ingest_dest_dir_by_type(self):
        d = self.r.ingest_dest_dir(_ctx(output_type="Plate", name="bg", version=2))
        self.assertEqual(d, "X:/projects/ABC/shots/SQ010/SH0100/plates/bg_v002")

    def test_ingest_dest_dir_unknown_type_falls_back(self):
        d = self.r.ingest_dest_dir(_ctx(output_type="Weird", name="x", version=1))
        self.assertEqual(d, "X:/projects/ABC/shots/SQ010/SH0100/input/Weird/x_v001")

    def test_ingest_sequence_files(self):
        files = self.r.ingest_sequence_files(
            _ctx(output_type="Plate", name="bg", version=1, ext="exr"), [1001, 1002])
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].endswith("_v001.1001.exr"))
        self.assertTrue(files[1].endswith("_v001.1002.exr"))

    def test_shot_folders(self):
        folders = self.r.shot_folders(_ctx())
        self.assertTrue(all(f.startswith("X:/projects/ABC/shots/SQ010/SH0100/") for f in folders))
        self.assertIn("X:/projects/ABC/shots/SQ010/SH0100/input", folders)


class TestRootsReference(unittest.TestCase):
    def test_project_root_ref_resolves(self):
        cfg = _cfg(roots={
            "project": "{nas_root}/{project}",
            "shot": "{project_root}/sh/{sequence}/{shot}",
        })
        r = PathResolver(cfg)
        self.assertEqual(r.shot_dir(_ctx()), "X:/projects/ABC/sh/SQ010/SH0100")

    def test_cyclic_roots_raise(self):
        cfg = _raw_cfg(roots={"a": "{b_root}/x", "b": "{a_root}/y",
                              "project": "{nas_root}/{project}", "shot": "{project_root}/s"})
        with self.assertRaises(PathError):
            PathResolver(cfg)


class TestValidate(unittest.TestCase):
    def test_default_config_is_valid(self):
        self.assertEqual(PathResolver(_cfg()).validate(), [])

    def test_catches_non_versioning_template(self):
        cfg = ProjectConfig(data={
            **ProjectConfig().data,
            "templates": {
                "workfile": {"base": "shot", "file": "{shot}_{task}.{ext}"},
                "output": {"base": "shot", "dir": "output/{output_type}",
                           "file": "{shot}_{output_type}.{ext}"},   # no version anywhere
            },
        })
        errs = PathResolver(cfg).validate()
        self.assertTrue(any("does not vary by version" in e for e in errs))

    def test_catches_frame_in_dir(self):
        cfg = ProjectConfig(data={
            **ProjectConfig().data,
            "templates": {
                "workfile": {"base": "shot", "file": "{shot}_v{version}.{ext}"},
                "output": {"base": "shot", "dir": "output/v{version}/{frame}",
                           "file": "{shot}_v{version}.{frame}.{ext}"},
            },
        })
        errs = PathResolver(cfg).validate()
        self.assertTrue(any("must not contain {frame}" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
