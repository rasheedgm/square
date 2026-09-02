"""PipelineContext / ProjectContext + services.projects / services.breakdown,
driven by the offline Kitsu stand-in."""

import tempfile
import unittest
from pathlib import Path

from square_core.context import PipelineContext, ProjectContext
from square_core.config.pipeline import PipelineConfig
from square_core.config import ProjectConfig
from square_core.kitsu import OfflineApi
from square_core.services import projects, breakdown
from square_core.services.projects import ProjectSpec


def _pipeline(nas_root: str) -> PipelineContext:
    cfg = PipelineConfig(nas_roots={"default": nas_root})
    api = OfflineApi()
    return PipelineContext(config=cfg, kitsu=api, user=api.current_user())


class TestPipelineContext(unittest.TestCase):
    def test_connect_offline(self):
        ctx = PipelineContext.connect(offline=True)
        self.assertTrue(ctx.offline)
        self.assertEqual(ctx.user.name, "offline")

    def test_project_falls_back_to_defaults_without_config_file(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = _pipeline(td)
            pctx = ctx.project("ABC")
            self.assertIsInstance(pctx, ProjectContext)
            self.assertEqual(pctx.code, "ABC")
            self.assertEqual(pctx.project.root_path, f"{Path(td).as_posix()}/ABC")

    def test_path_context_prefilled(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pipeline(td).project("ABC")
            pc = pctx.ctx(sequence="SQ010", shot="SH0100", task="comp", version=3)
            self.assertEqual(pc.project, "ABC")
            self.assertEqual(pc.shot, "SH0100")
            self.assertEqual(pc.nas_root, Path(td).as_posix())


class TestProjectsCreate(unittest.TestCase):
    def test_create_writes_config_and_folders(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = _pipeline(td)
            result = projects.create(ctx, ProjectSpec(code="ABC", name="Alpha", fps=25.0))

            root = Path(td) / "ABC"
            self.assertEqual(result.project.code, "ABC")
            self.assertTrue((root / "_pipeline" / "project_config.json").exists())
            self.assertTrue((root / "shots").is_dir())
            self.assertTrue((root / "_delivery").is_dir())

            loaded = ProjectConfig.load(root)
            self.assertEqual(loaded.fps, 25.0)
            self.assertIn(str(root / "shots"), result.folders_created)

    def test_create_applies_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = _pipeline(td)
            projects.create(ctx, ProjectSpec(
                code="XYZ", overrides={"colorspace": {"working": "sRGB"}}))
            cfg = ProjectConfig.load(Path(td) / "XYZ")
            self.assertEqual(cfg.colorspace["working"], "sRGB")


class TestBreakdown(unittest.TestCase):
    def test_ensure_shot_creates_folder_skeleton(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = _pipeline(td)
            projects.create(ctx, ProjectSpec(code="ABC"))
            pctx = ctx.project("ABC")
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100",
                                         frame_in=1001, frame_out=1050)
            self.assertEqual(shot.code, "SH0100")
            shot_dir = Path(td) / "ABC" / "shots" / "SQ010" / "SH0100"
            self.assertTrue(shot_dir.is_dir())
            # a skeleton subdir got made
            self.assertTrue((shot_dir / "input").is_dir())

    def test_build_task_grid(self):
        with tempfile.TemporaryDirectory() as td:
            ctx = _pipeline(td)
            projects.create(ctx, ProjectSpec(code="ABC"))
            pctx = ctx.project("ABC")
            shots = [breakdown.ensure_shot(pctx, "SQ010", f"SH{n:04d}", create_folders=False)
                     for n in (100, 200)]
            tasks = breakdown.build_task_grid(pctx, shots, ["Ingest", "Comp"])
            self.assertEqual(len(tasks), 4)


if __name__ == "__main__":
    unittest.main()
