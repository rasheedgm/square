"""The Nuke integration's pipeline glue (`ops`), context, and the Square-tab
gizmos (driven with a fake `nuke`). The panels need a real Nuke and aren't
covered here."""

import tempfile
import unittest
from pathlib import Path

from tests.test_workfile_manager import _hub
from tools.dcc.nuke import gizmos
from tools.dcc.nuke.context import Target, from_env, to_env
from tools.dcc.nuke.ops import NEW_VERSION, NukeOps, OpsError


def _ops(td):
    hub, api = _hub(td)
    return NukeOps(hub.ctx), api


def _t():
    return Target("ABC", "", "SQ010", "SH0100", "Comp")


class TestContext(unittest.TestCase):
    def test_env_round_trip_with_episode(self):
        env = {}
        to_env(Target("ABC", "EP01", "SQ010", "SH0100", "Comp"), env)
        self.assertEqual(env["SQUARE_EPISODE"], "EP01")
        t = from_env(env)
        self.assertTrue(t.complete)
        self.assertIn("EP01/SQ010/SH0100", t.label())
        to_env(Target("ABC", "", "", "", ""), env)
        self.assertNotIn("SQUARE_EPISODE", env)


class TestOpsNavigation(unittest.TestCase):
    def test_cascade(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.assertEqual(ops.projects(), ["ABC"])
            self.assertFalse(ops.is_episodic("ABC"))
            self.assertEqual(ops.episodes("ABC"), [])
            self.assertEqual(ops.sequences("ABC"), ["SQ010"])
            self.assertEqual(ops.shots("ABC", "SQ010"), ["SH0100", "SH0110"])
            self.assertEqual(ops.task_types("ABC", "SQ010", "SH0100"), ["Comp", "Roto"])
            self.assertEqual(ops.default_task_for("ABC", "SQ010", "SH0100"), "Comp")

    def test_resolve_errors(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            with self.assertRaises(OpsError):
                ops.next_save(Target("ABC", "", "SQ010", "NOPE", "Comp"))
            with self.assertRaises(OpsError):
                ops.next_save(Target("ABC", "", "SQ010", "SH0100", "Lighting"))


class TestOpsWorkfiles(unittest.TestCase):
    def test_minor_major_save_and_versions(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            t1 = ops.next_save(_t(), bump="minor")
            self.assertEqual((t1.major, t1.minor, t1.is_new_major), (1, 1, True))
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("v1.1", encoding="utf-8")
            ops.register_major(_t(), t1, comment="start")
            self.assertEqual(len(api.workfiles), 1)

            t2 = ops.next_save(_t(), bump="minor")
            self.assertEqual((t2.major, t2.minor), (1, 2))
            Path(t2.path).write_text("v1.2", encoding="utf-8")

            t3 = ops.next_save(_t(), bump="major")
            self.assertEqual((t3.major, t3.minor), (2, 1))

            vs = ops.workfile_versions(_t())
            self.assertEqual([v.major for v in vs], [1])
            self.assertEqual([m.minor for m in vs[0].minors], [2, 1])
            self.assertTrue(vs[0].online)

    def test_workfile_names(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            t1 = ops.next_save(_t(), name="precomp", bump="major")
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t1, name="precomp")
            self.assertIn("precomp", ops.workfile_names(_t()))


class TestOpsOutputs(unittest.TestCase):
    def test_output_types_renderable_only(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            types = ops.output_types(_t())
            self.assertIn("CompRender", types)
            self.assertNotIn("Plate", types)             # not renderable

    def test_resolve_output_path_new_and_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            info = ops.resolve_output_path(_t(), "CompRender", NEW_VERSION)
            self.assertEqual(info["version"], 1)
            self.assertIn(".####.exr", info["path"])
            self.assertFalse(info["locked"])

    def test_output_version_follows_workfile_major(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            # save workfile up to major 3
            for _ in range(3):
                t = ops.next_save(_t(), bump="major")
                Path(t.path).parent.mkdir(parents=True, exist_ok=True)
                Path(t.path).write_text("x", encoding="utf-8")
                ops.register_major(_t(), t)
            info = ops.resolve_output_path(_t(), "CompRender", NEW_VERSION)
            self.assertEqual(info["version"], 3)             # == workfile major

            r = Path(td) / "r"; r.mkdir()
            (r / "c.1001.exr").write_bytes(b"x" * 10)
            res = ops.publish_render(_t(), [str(r / "c.1001.exr")], proxy_dry_run=True)
            self.assertEqual(res.version, 3)

    def test_resolve_output_path_reports_lock(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            api.outputs.append({"output_type": "CompRender", "revision": 1, "name": "main",
                                "representation": "exr", "path": "X:/o/v001",
                                "data": {"square": {"locked": True}}})
            info = ops.resolve_output_path(_t(), "CompRender", "1")
            self.assertTrue(info["locked"])

    def test_publish_render_blocks_locked_next_version(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            api.outputs.append({"output_type": "CompRender", "revision": 1, "name": "main",
                                "representation": "exr", "path": "X:/o/v001",
                                "data": {"square": {"locked": True}}})
            # next_version would be 2 here (rev 1 exists) -> not blocked; lock rev 2
            api.outputs.append({"output_type": "CompRender", "revision": 2, "name": "main",
                                "representation": "exr", "path": "X:/o/v002",
                                "data": {"square": {"locked": True}}})
            r = Path(td) / "r"; r.mkdir()
            (r / "c.1001.exr").write_bytes(b"x" * 10)
            with self.assertRaises(OpsError):
                ops.publish_render(_t(), [str(r / "c.1001.exr")], proxy_dry_run=True)

    def test_resolve_read_path(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            api.outputs.append({"output_type": "Plate", "revision": 3, "name": "main",
                                "representation": "exr",
                                "path": "X:/ABC/SQ010/SH0100/plates/main_v003", "data": {}})
            info = ops.resolve_read_path(_t(), "Plate", "latest")
            self.assertEqual(info["version"], 3)
            self.assertEqual(info["colorspace"], "ACEScg")
            self.assertEqual(info["versions"], [3])


# --------------------------------------------------------------------------
# gizmos, with a fake nuke
# --------------------------------------------------------------------------

class _Knob:
    def __init__(self, name, values=None):
        self._name = name
        self._values = list(values) if values else []
        self._v = self._values[0] if self._values else ""
        self._enabled = True

    def name(self):
        return self._name

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = v

    def setValues(self, vs):
        self._values = [str(x) for x in vs]
        if self._v not in self._values and self._values:
            self._v = self._values[0]

    def values(self):
        return list(self._values)

    def setVisible(self, b):
        pass

    def setEnabled(self, b):
        self._enabled = b


class _Node:
    def __init__(self, cls):
        self._cls = cls
        self._knobs = {}

    def Class(self):
        return self._cls

    def knobs(self):
        return self._knobs

    def __getitem__(self, k):
        return self._knobs.setdefault(k, _Knob(k))

    def addKnob(self, knob):
        self._knobs[knob.name()] = knob


class _FakeNuke:
    def __init__(self):
        self.created = []
        self._this_node = None
        self._this_knob = None

    # node creation / knob factories
    def createNode(self, cls, inpanel=False):
        n = _Node(cls)
        self.created.append(n)
        self._this_node = n
        return n

    def Tab_Knob(self, name, label=None):
        return _Knob(name)

    def String_Knob(self, name, label=None):
        return _Knob(name)

    def Text_Knob(self, name, label=None):
        return _Knob(name)

    def Boolean_Knob(self, name, label=None):
        return _Knob(name)

    def PyScript_Knob(self, name, label=None, command=None):
        return _Knob(name)

    def Enumeration_Knob(self, name, label, values):
        return _Knob(name, values)

    def addKnobChanged(self, fn, nodeClass=None):
        pass

    def thisNode(self):
        return self._this_node

    def thisKnob(self):
        return self._this_knob


class TestGizmos(unittest.TestCase):
    def setUp(self):
        # env target so _populate has something to select
        import os
        self._env = dict(os.environ)
        os.environ.update(SQUARE_PROJECT="ABC", SQUARE_SEQUENCE="SQ010",
                          SQUARE_SHOT="SH0100", SQUARE_TASK="Comp")
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))

    def _wire(self, td):
        ops, api = _ops(td)
        import tools.dcc.nuke.panel as panel_mod
        panel_mod._ops = ops
        self.addCleanup(lambda: setattr(panel_mod, "_ops", None))
        return ops, api

    def test_create_square_write_sets_file_from_env_target(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)
            self.assertEqual(node[gizmos.MARK].value(), "write")
            self.assertEqual(node["sq_project"].value(), "ABC")
            self.assertEqual(node["sq_shot"].value(), "SH0100")
            self.assertEqual(node["sq_media_type"].value(), "CompRender")
            self.assertIn(".####.exr", node["file"].value())
            self.assertIn("/output/comp/v001/", node["file"].value().replace("\\", "/"))

    def test_square_write_knob_change_recomputes_file(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)
            node["sq_shot"].setValue("SH0110")
            nk._this_node, nk._this_knob = node, node["sq_shot"]
            gizmos.on_knob_changed(nk)
            self.assertIn("SH0110", node["file"].value())

    def test_create_square_read_resolves_a_published_plate(self):
        with tempfile.TemporaryDirectory() as td:
            _, api = self._wire(td)
            api.outputs.append({"output_type": "Plate", "revision": 2, "name": "main",
                                "representation": "exr",
                                "path": "X:/ABC/SQ010/SH0100/plates/main_v002", "data": {}})
            nk = _FakeNuke()
            node = gizmos.create_square_read(nk)
            node["sq_media_type"].setValue("Plate")
            nk._this_node, nk._this_knob = node, node["sq_media_type"]
            gizmos.on_knob_changed(nk)
            self.assertIn("main_v002", node["file"].value())
            self.assertEqual(node["colorspace"].value(), "ACEScg")


    def test_square_write_has_a_publish_toggle(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertIn("sq_do_publish", node.knobs())
            self.assertTrue(node["sq_do_publish"].value())      # default on
            self.assertIn("sq_preview", node.knobs())


class TestPanelImports(unittest.TestCase):
    def test_panel_and_gizmos_import_without_nuke(self):
        import tools.dcc.nuke.gizmos as g
        import tools.dcc.nuke.panel as p
        self.assertTrue(hasattr(p, "save_version"))
        self.assertTrue(hasattr(p, "open_version"))
        self.assertTrue(hasattr(p, "publish_dialog"))
        self.assertTrue(hasattr(g, "create_square_write"))

    def test_menu_points_at_publish_dialog(self):
        src = Path("tools/dcc/nuke/menu.py").read_text(encoding="utf-8")
        self.assertIn("publish_dialog", src)

    def test_node_frames_expands_a_read_over_its_range(self):
        from tools.dcc.nuke import panel
        node = _Node("Read")
        node["file"].setValue("X:/sh/plate.####.exr")
        node["first"].setValue("1001")
        node["last"].setValue("1003")
        frames = panel._node_frames(_FakeNuke(), node)
        self.assertEqual(frames, ["X:/sh/plate.1001.exr", "X:/sh/plate.1002.exr",
                                  "X:/sh/plate.1003.exr"])

    def test_node_frames_single_file(self):
        from tools.dcc.nuke import panel
        node = _Node("Read")
        node["file"].setValue("X:/sh/plate_v001.mov")
        self.assertEqual(panel._node_frames(_FakeNuke(), node), ["X:/sh/plate_v001.mov"])


if __name__ == "__main__":
    unittest.main()
