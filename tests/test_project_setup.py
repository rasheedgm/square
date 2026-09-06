"""Core of the project-setup tool: the breakdown-text parser and the
`ProjectAdmin` orchestration layer (role gate + service calls)."""

import tempfile
import types
import unittest
from pathlib import Path

from square_core.model import Project, Sequence, Shot, Task, TaskType, User

from tools.project_setup.core import (BreakdownRow, NotAuthorized, ProjectAdmin,
                                      ProjectSpec, parse_breakdown)


# ---------------------------------------------------------------------------
# fakes -- only the edges (Kitsu + PipelineConfig) are faked; projects.create
# and breakdown.* run for real against them.
# ---------------------------------------------------------------------------

class _FakeKitsu:
    def __init__(self):
        self._projects: list[Project] = []
        self.templates = ["Feature Film", "Episodic"]
        self.shot_types = ["Comp", "Roto"]
        self._seqs: dict[str, Sequence] = {}
        self._shots: dict[str, Shot] = {}
        self.tasks: list[tuple[str, str]] = []
        self.raise_on_shot: set[str] = set()

    def current_user(self):
        return User(id="u", email="a@b.com", role="admin")

    def project_templates(self):
        return list(self.templates)

    def projects(self, *, status=None):
        return list(self._projects)

    def project(self, ref):
        return next((p for p in self._projects if p.code == ref), None)

    def create_project(self, *, code, name="", production_type="short",
                       kitsu_template="", fps=None, resolution=""):
        p = Project(id=f"p-{code}", code=code, name=name or code,
                    production_type=production_type, fps=fps or 0.0, resolution=resolution)
        self._projects.append(p)
        return p

    def task_types(self, *, for_entity=None):
        return [TaskType(id=f"tt-{n}", name=n, for_entity="Shot") for n in self.shot_types]

    def ensure_sequence(self, project, code):
        return self._seqs.setdefault(code, Sequence(id=f"seq-{code}", code=code))

    def ensure_shot(self, project, sequence, code, *, frame_in=1001, frame_out=1100,
                    fps=None, nb_frames=None):
        if code in self.raise_on_shot:
            raise RuntimeError(f"kitsu blew up on {code}")
        sh = self._shots.get(code)
        if sh is None:
            sh = Shot(id=f"shot-{code}", code=code, frame_in=frame_in, frame_out=frame_out)
            self._shots[code] = sh
        else:
            sh.frame_in, sh.frame_out = frame_in, frame_out
        return sh

    def shots(self, project):
        return list(self._shots.values())

    def ensure_tasks(self, shot, names):
        out = []
        for n in names or []:
            self.tasks.append((shot.code, n))
            out.append(Task(id=f"task-{shot.code}-{n}", task_type_name=n, entity_id=shot.id))
        return out


class _FakePaths:
    def __init__(self, base):
        self.base = base

    def shot_dir(self, ctx):
        return str(Path(self.base) / ctx.get("sequence", "") / ctx.get("shot", ""))


class _FakeProjectContext:
    def __init__(self, kitsu, project, base):
        self.kitsu = kitsu
        self.project = project
        self.config = types.SimpleNamespace(fps=24.0, shot_folder_structure=[])
        self.paths = _FakePaths(base)
        self.pipeline = types.SimpleNamespace(nas_root=base)

    def ctx(self, **kw):
        return dict(kw)

    path_context = ctx


class _FakeCtx:
    """Stands in for a PipelineContext."""

    def __init__(self, base, role="admin"):
        self.user = User(id="u1", email="admin@studio.com", role=role)
        self.kitsu = _FakeKitsu()
        self.config = types.SimpleNamespace(
            nas_roots={"default": base, "fast": str(Path(base) / "fast")},
            project_defaults={},
            project_root=lambda code, nas_root="default": (
                f"{self.config.nas_roots.get(nas_root, self.config.nas_roots['default'])}/{code}"),
        )
        self._base = base

    def project(self, code):
        proj = self.kitsu.project(code) or Project(id=f"p-{code}", code=code, name=code)
        return _FakeProjectContext(self.kitsu, proj, f"{self._base}/{code}")


# ---------------------------------------------------------------------------
# parse_breakdown
# ---------------------------------------------------------------------------

class TestParseBreakdown(unittest.TestCase):
    def test_sequence_and_shot_only(self):
        (r,) = parse_breakdown("SQ010 SH0100")
        self.assertEqual((r.sequence, r.shot), ("SQ010", "SH0100"))
        self.assertEqual((r.frame_in, r.frame_out), (1001, 1100))
        self.assertIsNone(r.fps)
        self.assertTrue(r.ok)

    def test_dash_range(self):
        (r,) = parse_breakdown("SQ010 SH0100 1001-1240")
        self.assertEqual((r.frame_in, r.frame_out), (1001, 1240))

    def test_two_int_range_and_fps(self):
        (r,) = parse_breakdown("SQ010 SH0100 1001 1200 23.976")
        self.assertEqual((r.frame_in, r.frame_out), (1001, 1200))
        self.assertEqual(r.fps, 23.976)

    def test_comma_and_slash_separators(self):
        rows = parse_breakdown("SQ010, SH0100, 1001-1100\nSQ020 / SH0200 / 1001-1050 / 25")
        self.assertEqual([x.shot for x in rows], ["SH0100", "SH0200"])
        self.assertEqual(rows[1].fps, 25.0)

    def test_blank_and_comment_lines_skipped(self):
        rows = parse_breakdown("# header\n\nSQ010 SH0100\n\n   \n# trailing")
        self.assertEqual(len(rows), 1)

    def test_missing_shot_is_an_error_row(self):
        (r,) = parse_breakdown("SQ010")
        self.assertFalse(r.ok)
        self.assertIn("sequence and a shot", r.error)

    def test_reversed_range_flagged(self):
        (r,) = parse_breakdown("SQ010 SH0100 1100-1001")
        self.assertFalse(r.ok)
        self.assertIn("before it starts", r.error)

    def test_trailing_junk_flagged(self):
        (r,) = parse_breakdown("SQ010 SH0100 1001-1100 24 extra bits")
        self.assertFalse(r.ok)
        self.assertIn("didn't understand", r.error)


# ---------------------------------------------------------------------------
# ProjectAdmin
# ---------------------------------------------------------------------------

class TestProjectAdminGate(unittest.TestCase):
    def test_artist_role_cannot_write(self):
        with tempfile.TemporaryDirectory() as td:
            admin = ProjectAdmin(_FakeCtx(td, role="user"))
            self.assertFalse(admin.can_write())
            with self.assertRaises(NotAuthorized):
                admin.create_project(ProjectSpec(code="ABC"))

    def test_manager_role_can_write(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(ProjectAdmin(_FakeCtx(td, role="manager")).can_write())


class TestProjectAdminChoices(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.admin = ProjectAdmin(_FakeCtx(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_nas_root_names(self):
        self.assertEqual(self.admin.nas_root_names(), ["default", "fast"])

    def test_templates_and_task_types_passthrough(self):
        self.assertEqual(self.admin.kitsu_templates(), ["Feature Film", "Episodic"])
        self.assertEqual(self.admin.shot_task_types(), ["Comp", "Roto"])

    def test_project_root_preview_uses_named_root(self):
        got = self.admin.project_root_preview("ABC", "fast").replace("\\", "/")
        self.assertTrue(got.endswith("/fast/ABC"), got)
        self.assertEqual(self.admin.project_root_preview("", "default"), "")


class TestCreateProject(unittest.TestCase):
    def test_creates_kitsu_record_config_and_folders(self):
        with tempfile.TemporaryDirectory() as td:
            admin = ProjectAdmin(_FakeCtx(td))
            created = admin.create_project(ProjectSpec(code="ABC", name="A Big Corp", fps=24.0))
            self.assertEqual(created.project.code, "ABC")
            self.assertTrue(Path(created.config_path).is_file())
            self.assertIn("ABC", [p.code for p in admin.projects()])


class TestAddBreakdown(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.admin = ProjectAdmin(_FakeCtx(self._td.name))
        self.admin.ctx.kitsu._projects.append(Project(id="p-ABC", code="ABC", name="ABC"))

    def tearDown(self):
        self._td.cleanup()

    def test_two_rows_two_shots(self):
        rows = parse_breakdown("SQ010 SH0100 1001-1100\nSQ010 SH0110 1001-1050")
        seen = []
        out = self.admin.add_breakdown("ABC", rows, progress=lambda *a: seen.append(a))
        self.assertEqual(len(out), 2)
        self.assertTrue(all(o.created and not o.error for o in out))
        self.assertEqual({s.code for s in self.admin.existing_shots("ABC")}, {"SH0100", "SH0110"})
        self.assertEqual(seen[-1][:2], (2, 2))         # progress reached 2/2

    def test_error_rows_are_skipped_not_sent(self):
        rows = parse_breakdown("SQ010 SH0100\nSQ010\nSQ010 SH0110")   # middle row bad
        out = self.admin.add_breakdown("ABC", rows)
        self.assertEqual(len(out), 2)

    def test_idempotent_second_run_updates_in_place(self):
        rows = parse_breakdown("SQ010 SH0100 1001-1100")
        self.admin.add_breakdown("ABC", rows)
        self.admin.add_breakdown("ABC", parse_breakdown("SQ010 SH0100 1001-1200"))
        shots = self.admin.existing_shots("ABC")
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0].frame_out, 1200)

    def test_per_row_failure_is_reported_and_loop_continues(self):
        self.admin.ctx.kitsu.raise_on_shot.add("SH0110")
        rows = parse_breakdown("SQ010 SH0100\nSQ010 SH0110\nSQ010 SH0120")
        out = self.admin.add_breakdown("ABC", rows)
        by_shot = {o.row.shot: o for o in out}
        self.assertTrue(by_shot["SH0100"].created)
        self.assertIn("blew up", by_shot["SH0110"].error)
        self.assertTrue(by_shot["SH0120"].created)

    def test_artist_cannot_add_breakdown(self):
        admin = ProjectAdmin(_FakeCtx(self._td.name, role="user"))
        with self.assertRaises(NotAuthorized):
            admin.add_breakdown("ABC", parse_breakdown("SQ010 SH0100"))


class TestBuildTaskGrid(unittest.TestCase):
    def test_task_per_shot_per_type(self):
        with tempfile.TemporaryDirectory() as td:
            admin = ProjectAdmin(_FakeCtx(td))
            admin.ctx.kitsu._projects.append(Project(id="p-ABC", code="ABC", name="ABC"))
            admin.add_breakdown("ABC", parse_breakdown("SQ010 SH0100\nSQ010 SH0110"))
            tasks = admin.build_task_grid("ABC", ["Comp", "Roto", "Paint"])
            self.assertEqual(len(tasks), 6)
            self.assertEqual(len(admin.ctx.kitsu.tasks), 6)


if __name__ == "__main__":
    unittest.main()
