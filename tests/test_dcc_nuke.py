"""The Nuke integration's pipeline glue (`ops`), context, and node builders.
The panel/menu (which need a real Nuke) are not covered here."""

import tempfile
import types
import unittest
from pathlib import Path

from square_core.model import Output

from tests.test_workfile_manager import _hub
from tools.dcc.nuke import nodes
from tools.dcc.nuke.context import Target, from_env, to_env
from tools.dcc.nuke.ops import NukeOps, OpsError


def _ops(td):
    hub, api = _hub(td)
    return NukeOps(hub.ctx), api


class TestContext(unittest.TestCase):
    def test_from_env_and_completeness(self):
        env = {"SQUARE_PROJECT": "ABC", "SQUARE_SEQUENCE": "SQ010",
               "SQUARE_SHOT": "SH0100", "SQUARE_TASK": "Comp"}
        t = from_env(env)
        self.assertTrue(t.complete)
        self.assertIn("SQ010/SH0100", t.label())

    def test_from_env_partial_is_incomplete(self):
        self.assertFalse(from_env({"SQUARE_PROJECT": "ABC"}).complete)

    def test_to_env_round_trip_and_clear(self):
        env = {}
        to_env(Target("ABC", "SQ010", "SH0100", "Comp"), env)
        self.assertEqual(env["SQUARE_SHOT"], "SH0100")
        to_env(Target("ABC", "", "", ""), env)
        self.assertNotIn("SQUARE_SHOT", env)


class TestOpsNavigation(unittest.TestCase):
    def test_projects_shots_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.assertEqual(ops.projects(), ["ABC"])
            self.assertEqual(ops.shots_by_sequence("ABC"), {"SQ010": ["SH0100", "SH0110"]})
            self.assertEqual(ops.task_types("ABC", "SQ010", "SH0100"), ["Comp", "Roto"])

    def test_resolve_errors_are_actionable(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            with self.assertRaises(OpsError):
                ops.next_workfile_path(Target("ABC", "SQ010", "NOPE", "Comp"))
            with self.assertRaises(OpsError):
                ops.next_workfile_path(Target("ABC", "SQ010", "SH0100", "Lighting"))


class TestOpsWorkfiles(unittest.TestCase):
    def _t(self):
        return Target("ABC", "SQ010", "SH0100", "Comp")

    def test_next_path_register_and_versions(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            path, rev = ops.next_workfile_path(self._t())
            self.assertEqual(rev, 1)
            self.assertTrue(path.endswith("_comp_main_v001.nk"))

            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("# nuke", encoding="utf-8")
            ops.register_saved(self._t(), path, comment="first")
            self.assertEqual(len(api.workfiles), 1)

            vs = ops.versions(self._t())
            self.assertEqual([w.revision for w in vs], [1])
            self.assertEqual(ops.version_path(self._t(), 1), path)

    def test_software_is_resolved_not_a_bare_name(self):
        # RecordingKitsu records the software string as given; the important
        # part -- that KitsuApi resolves "nuke" before gazu -- is covered in
        # test_kitsu_api. Here just check the call goes through.
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            path, _ = ops.next_workfile_path(self._t())
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("x", encoding="utf-8")
            ops.register_saved(self._t(), path)
            self.assertEqual(api.workfiles[0]["software"], "nuke")


class TestOpsOutputs(unittest.TestCase):
    def _t(self):
        return Target("ABC", "SQ010", "SH0100", "Comp")

    def test_next_output_path_has_hash_padding(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            path, rev = ops.next_output_path(self._t(), "CompRender")
            self.assertEqual(rev, 1)
            self.assertIn(".####.exr", path)
            self.assertIn("/output/comp/v001/", path.replace("\\", "/"))

    def test_publish_render(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            r = Path(td) / "r"; r.mkdir()
            for f in (1001, 1002):
                (r / f"c.{f}.exr").write_bytes(b"x" * 20)
            res = ops.publish_render(self._t(), sorted(str(p) for p in r.iterdir()),
                                     proxy_dry_run=True)
            self.assertEqual(res.version, 1)
            self.assertEqual(len(api.outputs), 1)

    def test_plate_for_read_resolves_path_and_colorspace(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            api.outputs.append({"output_type": "Plate", "revision": 2,
                                "path": "X:/ABC/shots/SQ010/SH0100/plates/main_v002",
                                "representation": "exr", "name": "main"})
            info = ops.plate_for_read(self._t())
            self.assertEqual(info["version"], 2)
            self.assertEqual(info["colorspace"], "ACEScg")     # from the built-in Plate entry

    def test_plate_for_read_without_a_plate_errors(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            with self.assertRaises(OpsError):
                ops.plate_for_read(self._t())


class _FakeKnob:
    def __init__(self):
        self.v = None

    def setValue(self, v):
        self.v = v


class _FakeNode:
    def __init__(self, cls):
        self._cls = cls
        self._knobs = {}

    def __getitem__(self, k):
        return self._knobs.setdefault(k, _FakeKnob())

    def Class(self):
        return self._cls


class _FakeNuke:
    def __init__(self):
        self.created = []

    def createNode(self, cls, inpanel=False):
        n = _FakeNode(cls)
        self.created.append(n)
        return n


class TestPanelImports(unittest.TestCase):
    def test_panel_module_imports_without_nuke(self):
        # panel.py must not `import nuke` at module level
        import importlib

        import tools.dcc.nuke.panel as panel
        importlib.reload(panel)
        self.assertTrue(hasattr(panel, "save_version"))
        self.assertTrue(hasattr(panel, "show"))


class TestNodes(unittest.TestCase):
    def test_square_read_sets_path_range_colorspace(self):
        nk = _FakeNuke()
        n = nodes.square_read(nk, path=r"X:\a\b\plate.####.exr", colorspace="ACEScg",
                              frame_in=1001, frame_out=1096, label="[Square] Plate v001")
        self.assertEqual(n["file"].v, "X:/a/b/plate.####.exr")
        self.assertEqual(n["first"].v, 1001)
        self.assertEqual(n["last"].v, 1096)
        self.assertEqual(n["colorspace"].v, "ACEScg")

    def test_square_write_sets_path_and_dirs(self):
        nk = _FakeNuke()
        n = nodes.square_write(nk, path=r"X:\o\comp.####.exr", colorspace="ACEScg")
        self.assertEqual(n["file"].v, "X:/o/comp.####.exr")
        self.assertTrue(n["create_directories"].v)


if __name__ == "__main__":
    unittest.main()
