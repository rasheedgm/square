"""The Nuke integration's pipeline glue (`ops`), context, and the Square-tab
gizmos (driven with a fake `nuke`). The panels need a real Nuke and aren't
covered here -- sign_in()'s dialog flow is the same category (a real Qt
LoginDialog), so it's manual-test-only too; menu_title()/sign_out() are pure
logic and covered below."""

import os
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


def _count_calls(api, name: str) -> list:
    """Wrap `api.<name>` to append to the returned list on every call --
    counts real Kitsu round trips a fake can't otherwise report."""
    calls = []
    orig = getattr(api, name)

    def wrapped(*a, **k):
        calls.append((a, k))
        return orig(*a, **k)

    setattr(api, name, wrapped)
    return calls


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


class TestOpsCaching(unittest.TestCase):
    """Regression coverage: NukeOps._resolve() (shots + tasks) used to hit
    Kitsu fresh on every single call, and gizmos._populate() chains 5+ calls
    that all need the same shot/task data for one node creation -- the
    actual cause of "Read/Write node creation is slow". Each of these fetches
    the project's full shot list, this shot's task list, or this shot's
    output-file list from Kitsu exactly ONCE per NukeOps instance, however
    many times it's asked for."""

    def test_shots_fetched_once_across_many_calls(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            calls = _count_calls(api, "shots")
            ops.shots("ABC", "SQ010")
            ops.task_types("ABC", "SQ010", "SH0100")
            ops.default_task_for("ABC", "SQ010", "SH0100")   # re-derives task_types itself
            ops.output_types(_t())
            ops.resolve_output_path(_t(), "CompRender", NEW_VERSION)
            self.assertEqual(len(calls), 1)

    def test_tasks_for_shot_fetched_once_across_many_calls(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            calls = _count_calls(api, "tasks_for_shot")
            ops.task_types("ABC", "SQ010", "SH0100")
            ops.default_task_for("ABC", "SQ010", "SH0100")
            ops.next_save(_t())            # need_task=True -> resolves the task too
            self.assertEqual(len(calls), 1)

    def test_output_files_fetched_once_per_creation_then_invalidated_by_publish(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            calls = _count_calls(api, "output_files")
            # the exact chain gizmos._populate() runs for one SquareWrite:
            ops.output_versions(_t(), "CompRender")
            ops.resolve_output_path(_t(), "CompRender", NEW_VERSION)
            self.assertEqual(len(calls), 1)

            r = Path(td) / "r"
            r.mkdir()
            (r / "c.1001.exr").write_bytes(b"x" * 10)
            ops.publish_render(_t(), [str(r / "c.1001.exr")], proxy_dry_run=True)
            # publish_render()'s own lock-check reused the cached list (still
            # 1 fetch) -- but it must invalidate that cache once the publish
            # actually lands, so the NEXT read is a real fetch again, not
            # still serving the pre-publish snapshot
            ops.output_versions(_t(), "CompRender")
            self.assertEqual(len(calls), 2)


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


class TestGizmosLazyCreation(unittest.TestCase):
    """Regression: node creation used to eagerly walk the WHOLE cascade
    (shots, tasks, media types, versions -- 6+ Kitsu round trips minimum,
    several of them repeat fetches of the exact same data) regardless of
    how much of that was actually known from the launch context. It's lazy
    now: creation walks only as far down project -> sequence -> shot ->
    task as SQUARE_PROJECT / _SEQUENCE / _SHOT / _TASK actually specify."""

    def setUp(self):
        self._env = dict(os.environ)
        for k in ("SQUARE_PROJECT", "SQUARE_EPISODE", "SQUARE_SEQUENCE",
                 "SQUARE_SHOT", "SQUARE_TASK"):
            os.environ.pop(k, None)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))

    def _wire(self, td):
        ops, api = _ops(td)
        import tools.dcc.nuke.panel as panel_mod
        panel_mod._ops = ops
        self.addCleanup(lambda: setattr(panel_mod, "_ops", None))
        return ops, api

    def test_creation_with_nothing_seeded_only_fetches_the_project_list(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            shots_calls = _count_calls(api, "shots")
            tasks_calls = _count_calls(api, "tasks_for_shot")
            node = gizmos.create_square_write(_FakeNuke())
            self.assertEqual(node["sq_project"].values(), ["ABC"])
            self.assertEqual(shots_calls, [])
            self.assertEqual(tasks_calls, [])
            self.assertEqual(node["file"].value(), "")

    def test_creation_with_only_project_seeded_stops_after_sequence(self):
        os.environ["SQUARE_PROJECT"] = "ABC"
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            shots_calls = _count_calls(api, "shots")
            node = gizmos.create_square_write(_FakeNuke())
            self.assertEqual(node["sq_project"].value(), "ABC")
            self.assertEqual(node["sq_sequence"].value(), "SQ010")   # loaded (next level)
            self.assertEqual(shots_calls, [])                        # shots not fetched yet
            self.assertEqual(node["sq_shot"].values(), [""])
            self.assertEqual(node["file"].value(), "")               # nothing to resolve yet

    def test_creation_with_full_context_still_resolves_the_whole_node(self):
        os.environ.update(SQUARE_PROJECT="ABC", SQUARE_SEQUENCE="SQ010",
                          SQUARE_SHOT="SH0100", SQUARE_TASK="Comp")
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertEqual(node["sq_shot"].value(), "SH0100")
            self.assertEqual(node["sq_media_type"].value(), "CompRender")
            self.assertIn(".####.exr", node["file"].value())

    def test_project_only_node_loads_shots_lazily_once_sequence_is_picked(self):
        os.environ["SQUARE_PROJECT"] = "ABC"
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)
            self.assertEqual(node["sq_shot"].values(), [""])

            node["sq_sequence"].setValue("SQ010")
            nk._this_node, nk._this_knob = node, node["sq_sequence"]
            gizmos.on_knob_changed(nk)
            self.assertEqual(set(node["sq_shot"].values()), {"SH0100", "SH0110"})
            self.assertEqual(node["sq_task"].values(), [""])   # still not loaded


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

    def test_menu_has_sign_in_and_sign_out(self):
        src = Path("tools/dcc/nuke/menu.py").read_text(encoding="utf-8")
        self.assertIn("sign_in", src)
        self.assertIn("sign_out", src)

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


class TestAccountState(unittest.TestCase):
    """menu_title() / is_signed_in() / sign_out() -- pure logic, no Qt or
    real Nuke needed. sign_in() opens a real LoginDialog and isn't covered
    here, same as the other panels."""

    def setUp(self):
        import tools.dcc.nuke.panel as panel_mod
        self.panel = panel_mod
        self._orig_ops = panel_mod._ops
        panel_mod._ops = None
        self.addCleanup(lambda: setattr(panel_mod, "_ops", self._orig_ops))

        self._td = tempfile.TemporaryDirectory()
        self._old_state_dir = os.environ.get("SQUARE_STATE_DIR")
        os.environ["SQUARE_STATE_DIR"] = self._td.name
        self.addCleanup(self._restore_state_dir)

    def _restore_state_dir(self):
        if self._old_state_dir is None:
            os.environ.pop("SQUARE_STATE_DIR", None)
        else:
            os.environ["SQUARE_STATE_DIR"] = self._old_state_dir
        self._td.cleanup()

    def test_menu_title_plain_when_nothing_cached_and_ops_unset(self):
        self.assertEqual(self.panel.menu_title(), "Square")
        self.assertFalse(self.panel.is_signed_in())

    def test_menu_title_hints_signed_in_from_a_cached_token_alone(self):
        """No live Kitsu call happens here -- menu_title() must never block
        Nuke startup on the network -- so a merely-cached token (whose owner
        we don't know without asking the server) gets a generic hint, not a
        name."""
        from square_core.kitsu import auth
        auth.store_session(self.panel._pipeline_host(),
                           {"access_token": "AT", "refresh_token": ""})
        self.assertEqual(self.panel.menu_title(), "Square — signed in")
        self.assertTrue(self.panel.is_signed_in())

    def test_menu_title_shows_the_real_name_once_ops_is_resolved(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.panel._ops = ops
            self.assertEqual(self.panel.menu_title(), "Square — artist@studio.com")
            self.assertTrue(self.panel.is_signed_in())

    def test_sign_out_forgets_the_session_and_clears_ops(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.panel._ops = ops
        from square_core.kitsu import auth
        host = self.panel._pipeline_host()
        auth.store_session(host, {"access_token": "AT", "refresh_token": "RT"})

        self.panel.sign_out()

        self.assertIsNone(self.panel._ops)
        self.assertIsNone(auth.cached_session(host))
        self.assertEqual(self.panel.menu_title(), "Square")
        self.assertFalse(self.panel.is_signed_in())

    def test_rebuild_menu_is_a_safe_noop_outside_nuke(self):
        self.panel._rebuild_menu()          # must not raise -- no real nuke here


class TestMenuSource(unittest.TestCase):
    """menu.py needs real Nuke just to import (it does `import nuke` and
    calls build() unconditionally at module load, exactly so Nuke auto-runs
    it on startup) -- like the panels, it's checked by reading the source,
    not importing it. menu_title() / is_signed_in() -- the actual decision
    logic build() calls into -- are plain functions in panel.py and get
    real unit coverage in TestAccountState above.

    Regression: an earlier fix avoided renaming the top-level menu at all,
    because Menu.removeItem(name) needs an exact match against a
    SEPARATELY TRACKED Python string that fell out of sync with the real
    current name after the very first rename, leaving a duplicate "Square"
    menu behind instead of replacing it. The rename is back (that's the
    actually-wanted look), but removal now has to ask Nuke itself what's
    currently there (top.items()) instead of trusting tracked state to
    stay in sync -- these checks are what stand in for that regression
    test without a real Nuke to drive it against."""

    def setUp(self):
        self.src = Path("tools/dcc/nuke/menu.py").read_text(encoding="utf-8")

    def test_removes_by_asking_nuke_whats_there_not_a_tracked_name(self):
        self.assertIn(".items()", self.src)
        self.assertIn(".name()", self.src)

    def test_title_is_computed_from_panel_not_hardcoded(self):
        self.assertIn("panel.menu_title()", self.src)
        self.assertNotIn('addMenu("Square")', self.src)

    def test_sign_in_and_sign_out_are_mutually_exclusive(self):
        self.assertIn("panel.is_signed_in()", self.src)
        # both command strings still appear (one on each branch) -- but
        # only ONE add call actually runs for a given state, unlike the
        # old always-show-both version
        self.assertIn('if panel.is_signed_in():', self.src)
        self.assertIn("sign_out", self.src)
        self.assertIn("sign_in", self.src)

    def test_gizmo_callbacks_registered_exactly_once_outside_build(self):
        # register_callbacks() must NOT be called from inside build() --
        # a rebuild (Sign In / Sign Out) would double-fire every
        # knobChanged callback otherwise (the doubled-xStudio-plugin bug)
        build_body = self.src.split("def build()", 1)[1].split("\nbuild()", 1)[0]
        self.assertNotIn("register_callbacks", build_body)
        self.assertIn("register_callbacks", self.src)


if __name__ == "__main__":
    unittest.main()
