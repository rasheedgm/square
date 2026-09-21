"""Core of the workfile manager: navigation + the work-service actions, driven
through `WorkfileHub` against a fake Kitsu (with `work` / `media` running for
real)."""

import tempfile
import unittest
from pathlib import Path

from square_core.config.pipeline import PipelineConfig
from square_core.context import PipelineContext
from square_core.model import Project, Sequence, Shot, Task
from square_core.services import projects
from square_core.services.projects import ProjectSpec

from tests.test_services_work_review import RecordingKitsu
from tools.workfile_manager.core import SeedMode, WorkfileHub


class _NavKitsu(RecordingKitsu):
    """RecordingKitsu + the read side a workfile manager navigates."""

    def __init__(self):
        super().__init__()
        self._projects: list[Project] = []
        self._shots: list[Shot] = []
        self._tasks: dict[str, list[Task]] = {}

    def current_user(self):
        from square_core.model import User
        return User(id="u1", email="artist@studio.com", role="artist")

    def create_project(self, *, code, name="", production_type="short", **kw):
        p = Project(id=f"p-{code}", code=code, name=name or code)
        self._projects.append(p)
        return p

    def project(self, ident):
        return next((p for p in self._projects if p.code == ident or p.id == ident), None)

    def projects(self, *, status=None):
        return list(self._projects)

    def add_shot(self, seq, code, task_types=("Comp", "Roto")):
        shot = Shot(id=f"s-{code}", code=code, sequence_code=seq)
        self._shots.append(shot)
        self._tasks[shot.id] = [
            Task(id=f"t-{code}-{tt}", entity_id=shot.id, task_type_name=tt, status="Todo")
            for tt in task_types]
        return shot

    def shots(self, project):
        return list(self._shots)

    def sequences(self, project):
        """A dedicated endpoint, like the real KitsuApi.sequences() -- so
        ops.sequences()'s "derive from the shot list" fallback (for a
        backend that genuinely has no such endpoint) is NOT what gets
        exercised by tests using this fake. Real Kitsu has this endpoint;
        a fake without it would make every "just loaded a sequence" test
        look like it also had to fetch every shot in the project first."""
        codes = sorted({s.sequence_code for s in self._shots if s.sequence_code})
        return [Sequence(id=f"seq-{c}", code=c) for c in codes]

    def tasks_for_shot(self, shot):
        return list(self._tasks.get(getattr(shot, "id", shot), []))

    def output_files(self, entity, *, output_type_name=None):
        from square_core.model import Output
        return [Output(output_type=o["output_type"], revision=o["revision"],
                       path=o["path"], representation=o["representation"],
                       name=o["name"], data=o.get("data") or {})
                for o in self.outputs
                if not output_type_name or o["output_type"] == output_type_name]


def _hub(nas):
    cfg = PipelineConfig(nas_roots={"default": nas})
    api = _NavKitsu()
    ctx = PipelineContext(config=cfg, kitsu=api, user=api.current_user())
    projects.create(ctx, ProjectSpec(code="ABC", fps=24.0))
    api.add_shot("SQ010", "SH0100")
    api.add_shot("SQ010", "SH0110")
    return WorkfileHub(ctx), api


class TestNavigation(unittest.TestCase):
    def test_projects_shots_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            self.assertEqual([p.code for p in hub.projects()], ["ABC"])
            shots = hub.shots("ABC")
            self.assertEqual([s.code for s in shots], ["SH0100", "SH0110"])
            rows = hub.tasks_for_shot("ABC", shots[0])
            self.assertEqual({r.task.task_type_name for r in rows}, {"Comp", "Roto"})
            self.assertEqual(rows[0].sequence_code, "SQ010")

    def test_media_type_lists_fall_back_to_builtins(self):
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            self.assertIn("NukeScript", hub.workfile_types("ABC"))
            self.assertIn("CompRender", hub.output_types("ABC"))
            self.assertNotIn("NukeScript", hub.output_types("ABC"))

    def test_software_guess_from_media_type(self):
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            self.assertEqual(hub.software_for("ABC", "NukeScript"), "nuke")
            self.assertEqual(hub.software_for("ABC", "MayaScene"), "maya")


class TestActions(unittest.TestCase):
    def _task(self, hub):
        shot = hub.shots("ABC")[0]
        task = hub.tasks_for_shot("ABC", shot)[0].task
        return shot, task

    def test_new_workfile_empty_then_versions(self):
        with tempfile.TemporaryDirectory() as td:
            hub, api = _hub(td)
            shot, task = self._task(hub)
            slot = hub.new_workfile("ABC", shot, task, media_type="NukeScript",
                                    software="nuke", seed=SeedMode.EMPTY, comment="start")
            self.assertEqual((slot.major, slot.minor), (1, 1))
            self.assertFalse(slot.seeded)
            self.assertEqual(len(api.workfiles), 1)
            vs = hub.workfiles("ABC", shot, task)
            self.assertEqual([v.major for v in vs], [1])

    def test_new_workfile_from_template_and_copy_up(self):
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            shot, task = self._task(hub)
            tmpl = Path(td) / "comp.nk"
            tmpl.write_text("template", encoding="utf-8")
            v1 = hub.new_workfile("ABC", shot, task, media_type="NukeScript",
                                  seed=SeedMode.TEMPLATE, template_path=str(tmpl))
            self.assertTrue(Path(v1.path).is_file())
            v2 = hub.new_workfile("ABC", shot, task, media_type="NukeScript",
                                  seed=SeedMode.CURRENT)
            self.assertEqual(v2.major, 2)
            self.assertEqual(Path(v2.path).read_text(encoding="utf-8"), "template")

    def test_template_seed_without_a_path_or_config_errors(self):
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            shot, task = self._task(hub)
            with self.assertRaises(ValueError):
                hub.new_workfile("ABC", shot, task, media_type="NukeScript",
                                 seed=SeedMode.TEMPLATE)

    def test_publish_output_and_list(self):
        with tempfile.TemporaryDirectory() as td:
            hub, api = _hub(td)
            shot, task = self._task(hub)
            render = Path(td) / "render"
            render.mkdir()
            for f in (1001, 1002, 1003):
                (render / f"comp.{f}.exr").write_bytes(b"x" * 30)
            res = hub.publish_output("ABC", shot, task, media_type="CompRender",
                                     frames=sorted(str(p) for p in render.iterdir()),
                                     proxy_dry_run=True)
            self.assertEqual(res.version, 1)
            self.assertEqual(len(api.outputs), 1)
            self.assertEqual(len(api.previews), 1)          # CompRender is previewable

    def test_launcher_substitution(self):
        with tempfile.TemporaryDirectory() as td:
            hub, _ = _hub(td)
            calls = []
            import tools.workfile_manager.core as core_mod
            orig = core_mod.subprocess.Popen
            core_mod.subprocess.Popen = lambda parts, *a, **k: calls.append(parts)
            try:
                hub.ctx.config  # noqa
                hub._pctx("ABC").config.data.setdefault("tools", {})["workfile_manager"] = {
                    "launchers": {"nuke": "nuke.exe -q -- {file}"}}
                note = hub.open_workfile("ABC", "nuke", r"X:/a/b/comp_v001.nk")
            finally:
                core_mod.subprocess.Popen = orig
            self.assertEqual(calls[0], ["nuke.exe", "-q", "--", "X:/a/b/comp_v001.nk"])
            self.assertIn("launched", note)


if __name__ == "__main__":
    unittest.main()
