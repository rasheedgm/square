"""square_core.paths.PathResolver -- pure path resolution from ProjectConfig (v2)."""

import copy
import unittest

from square_core.config import ProjectConfig
from square_core.config.project import DEFAULT_PROJECT_CONFIG
from square_core.model import PathContext
from square_core.paths import PathResolver, PathError, slugify


def _cfg(**over):
    return ProjectConfig.from_defaults(overrides=over or None)


def _raw_cfg(**over):
    data = copy.deepcopy(DEFAULT_PROJECT_CONFIG)
    for k, v in over.items():
        data[k] = v
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


class TestRootsAndSkeleton(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver(_cfg())

    def test_project_root(self):
        self.assertEqual(self.r.project_root(_ctx()), "X:/projects/ABC")

    def test_shot_dir_no_episode(self):
        self.assertEqual(self.r.shot_dir(_ctx()), "X:/projects/ABC/shots/SQ010/SH0100")

    def test_shot_dir_episodic(self):
        self.assertEqual(self.r.shot_dir(_ctx(episode="EP02")),
                         "X:/projects/ABC/EP02/shots/SQ010/SH0100")

    def test_shot_folders(self):
        folders = self.r.shot_folders(_ctx())
        self.assertTrue(all(f.startswith("X:/projects/ABC/shots/SQ010/SH0100/") for f in folders))
        self.assertIn("X:/projects/ABC/shots/SQ010/SH0100/input", folders)

    def test_project_root_ref_resolves(self):
        r = PathResolver(_cfg(roots={
            "project": "{nas_root}/{project}",
            "shot": "{project_root}/sh/{sequence}/{shot}",
        }))
        self.assertEqual(r.shot_dir(_ctx()), "X:/projects/ABC/sh/SQ010/SH0100")

    def test_cyclic_roots_raise(self):
        cfg = _raw_cfg(roots={"a": "{b_root}/x", "b": "{a_root}/y",
                              "project": "{nas_root}/{project}", "shot": "{project_root}/s"})
        with self.assertRaises(PathError):
            PathResolver(cfg)


class TestMediaPath(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver(_cfg())

    def test_ingest_type_dir(self):
        self.assertEqual(
            self.r.media_dir("Plate", _ctx(name="bg", version=2)),
            "X:/projects/ABC/shots/SQ010/SH0100/plates/bg_v002")

    def test_unknown_type_uses_default(self):
        self.assertEqual(
            self.r.media_dir("Weird", _ctx(name="x", version=1)),
            "X:/projects/ABC/shots/SQ010/SH0100/input/Weird/x_v001")

    def test_render_output_frame(self):
        p = self.r.media_path("CompRender", _ctx(name="main", version=2,
                                                 representation="exr", ext="exr", frame=1001))
        self.assertEqual(
            p,
            "X:/projects/ABC/shots/SQ010/SH0100/output/comp/v002/exr/"
            "ABC_SQ010_SH0100_comp_main_v002.1001.exr")

    def test_media_dir_drops_file_and_frame(self):
        self.assertEqual(
            self.r.media_dir("CompRender", _ctx(version=2, representation="exr")),
            "X:/projects/ABC/shots/SQ010/SH0100/output/comp/v002/exr")

    def test_optional_representation_collapses(self):
        self.assertEqual(
            self.r.media_dir("CompRender", _ctx(version=2)),
            "X:/projects/ABC/shots/SQ010/SH0100/output/comp/v002")

    def test_workfile_media_type(self):
        p = self.r.media_path("NukeScript", _ctx(name="main", version=3))
        self.assertEqual(
            p, "X:/projects/ABC/shots/SQ010/SH0100/work/comp/nuke/ABC_SQ010_SH0100_comp_main_v003.nk")

    def test_media_sequence(self):
        files = self.r.media_sequence("Plate", _ctx(name="bg", version=1, ext="exr"), [1001, 1002])
        self.assertEqual(len(files), 2)
        self.assertTrue(files[0].endswith("/plates/bg_v001/ABC_SQ010_SH0100_Plate_bg_v001.1001.exr"))
        self.assertTrue(files[1].endswith("_v001.1002.exr"))

    def test_name_appears(self):
        p = self.r.media_path("CompRender", _ctx(name="matte", version=1,
                                                 representation="exr", ext="exr", frame=1001))
        self.assertIn("_matte_v001", p)

    def test_media_type_slugified(self):
        f = self.r.media_file("BG Plate", _ctx(name="bg", version=1, ext="exr", frame=1001))
        self.assertIn("BG_Plate", f)

    def test_missing_required_raises(self):
        with self.assertRaises(PathError):
            self.r.media_path("CompRender", PathContext(nas_root="X:", project="ABC",
                                                        version=1, ext="exr", frame=1001))


class TestTokenRules(unittest.TestCase):
    def setUp(self):
        self.r = PathResolver(_cfg())

    def test_version_pad_default(self):
        self.assertIn("v007", self.r.media_path("NukeScript", _ctx(name="main", version=7)))

    def test_version_spec_override(self):
        r = PathResolver(_cfg(media_types={"CompRender": {
            "dir": "o/v{version:04d}",
            "file": "{shot}_v{version:04d}.{frame}.{ext}"}}))
        self.assertTrue(r.media_dir("CompRender", _ctx(version=5)).endswith("/o/v0005"))

    def test_unknown_token_raises(self):
        cfg = _raw_cfg(media_types={
            "_default": DEFAULT_PROJECT_CONFIG["media_types"]["_default"],
            "X": {"dir": "o", "file": "{shot}_{bogus}.{ext}"}})
        with self.assertRaises(PathError):
            PathResolver(cfg).media_path("X", _ctx(ext="exr"))

    def test_case_upper_on_delivery(self):
        r = PathResolver(_cfg(delivery_presets={"ACME": {
            "case": "upper", "file": "acme_{shot}_v{version}.{ext}"}}))
        f = r.delivery_file(_ctx(client="ACME", shot="sh0100", version=1, ext="dpx"))
        self.assertEqual(f, "ACME_SH0100_V001.DPX")


class TestValidate(unittest.TestCase):
    def test_default_config_is_valid(self):
        self.assertEqual(PathResolver(_cfg()).validate(), [])

    def test_catches_non_versioning_media_type(self):
        cfg = _raw_cfg(media_types={
            "_default": {**DEFAULT_PROJECT_CONFIG["media_types"]["_default"],
                         "dir": "in/{media_type}/{name}", "file": "{shot}_{media_type}.{ext}"},
        })
        errs = PathResolver(cfg).validate()
        self.assertTrue(any("does not vary by version" in e for e in errs))

    def test_catches_frame_in_dir(self):
        cfg = _raw_cfg(media_types={
            "_default": {**DEFAULT_PROJECT_CONFIG["media_types"]["_default"],
                         "dir": "output/v{version}/{frame}"},
        })
        errs = PathResolver(cfg).validate()
        self.assertTrue(any("must not contain {frame}" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
