"""The Nuke integration's pipeline glue (`ops`), context, and the Square-tab
gizmos (driven with a fake `nuke`). The panels need a real Nuke and aren't
covered here -- sign_in()'s dialog flow is the same category (a real Qt
LoginDialog), so it's manual-test-only too; menu_title()/sign_out() are pure
logic and covered below."""

import os
import tempfile
import unittest
from pathlib import Path

from square_core.services import work
from tests.test_workfile_manager import _hub
from tools.dcc.nuke import gizmos
from tools.dcc.nuke.context import Target, from_env, to_env
from tools.dcc.nuke.ops import NEW_VERSION, SYNC_VERSION, NukeOps, OpsError


def _ops(td):
    hub, api = _hub(td)
    return NukeOps(hub.ctx), api


def _ops_with_overrides(td, overrides: dict):
    """Like _ops(), but the project is created with project-config overrides
    -- for cases that depend on non-default media_types entries (e.g. a
    delivery-sourced type marked renderable)."""
    from square_core.config.pipeline import PipelineConfig
    from square_core.context import PipelineContext
    from square_core.services import projects
    from square_core.services.projects import ProjectSpec

    from tests.test_workfile_manager import _NavKitsu

    cfg = PipelineConfig(nas_roots={"default": td})
    api = _NavKitsu()
    ctx = PipelineContext(config=cfg, kitsu=api, user=api.current_user())
    projects.create(ctx, ProjectSpec(code="ABC", fps=24.0, overrides=overrides))
    api.add_shot("SQ010", "SH0100")
    return NukeOps(ctx), api


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

    def test_refresh_forces_a_live_fetch_on_the_next_read(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            calls = _count_calls(api, "output_files")
            ops.output_versions(_t(), "CompRender")
            self.assertEqual(len(calls), 1)
            ops.output_versions(_t(), "CompRender")
            self.assertEqual(len(calls), 1)             # still cached
            ops.refresh(_t())
            ops.output_versions(_t(), "CompRender")
            self.assertEqual(len(calls), 2)             # forced a real re-fetch


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

    def test_output_types_includes_a_renderable_delivery_type(self):
        """A shot can be missing its Plate delivery and need one generated
        straight from Nuke -- renderable is the real gate, not source, so a
        delivery-sourced type marked renderable belongs in the Write node's
        list too, not just source="publish" ones.

        A project's own media_types override is NOT deep-merged against the
        built-in registry at the "which types exist" level (ProjectConfig's
        media_types property is a presence-check, not a merge) -- so the
        override here carries the full built-in registry forward, same as a
        real project_config.json edit needs to, not just the one changed
        leaf."""
        import copy

        from square_core.config.project import DEFAULT_PROJECT_CONFIG
        with tempfile.TemporaryDirectory() as td:
            media_types = copy.deepcopy(DEFAULT_PROJECT_CONFIG["media_types"])
            media_types["Plate"]["renderable"] = True
            ops, _ = _ops_with_overrides(td, {"media_types": media_types})
            types = ops.output_types(_t())
            self.assertIn("Plate", types)
            self.assertIn("CompRender", types)           # still there too

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
            info = ops.resolve_output_path(_t(), "CompRender", SYNC_VERSION)
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


class TestNewVsSyncVersion(unittest.TestCase):
    """NEW_VERSION (always the next-after-highest number, ignoring workfile
    major, never locked -- nothing occupies a fresh number yet) vs
    SYNC_VERSION (the current workfile major, re-rendering in place, refused
    if locked)."""

    def test_new_version_ignores_workfile_major_entirely(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            # workfile is at major 5, but only rev 1-2 have actually been
            # published -- NEW_VERSION must give 3 (next after highest
            # EXISTING output), not 5 (the workfile major -- that's Sync's job)
            for _ in range(5):
                t = ops.next_save(_t(), bump="major")
                Path(t.path).parent.mkdir(parents=True, exist_ok=True)
                Path(t.path).write_text("x", encoding="utf-8")
                ops.register_major(_t(), t)
            # published through the real flow (not api.outputs.append) so the
            # fake's next-revision counter actually advances, same as Kitsu's
            # own next_output_revision would
            r = Path(td) / "r"; r.mkdir()
            for rev in (1, 2):
                f = r / f"c{rev}.1001.exr"; f.write_bytes(b"x" * 10)
                ops.publish_render(_t(), [str(f)], version=str(rev), proxy_dry_run=True)
            for o in api.outputs:
                if o["output_type"] == "CompRender" and o["revision"] == 2:
                    o["data"] = {"square": {"locked": True}}
            info = ops.resolve_output_path(_t(), "CompRender", NEW_VERSION)
            self.assertEqual(info["version"], 3)
            self.assertFalse(info["locked"])   # a fresh number can never already be locked

    def test_sync_falls_back_to_next_version_when_nothing_registered(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.assertEqual(ops.workfile_major(_t()), 0)     # never saved
            info = ops.resolve_output_path(_t(), "CompRender", SYNC_VERSION)
            self.assertEqual(info["version"], 1)

    def test_sync_refuses_a_locked_current_major(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            t = ops.next_save(_t(), bump="major")          # major 1
            Path(t.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t)
            api.outputs.append({"output_type": "CompRender", "revision": 1, "name": "main",
                                "representation": "exr", "path": "X:/o/v001",
                                "data": {"square": {"locked": True}}})
            info = ops.resolve_output_path(_t(), "CompRender", SYNC_VERSION)
            self.assertEqual(info["version"], 1)
            self.assertTrue(info["locked"])

    def test_sync_targets_the_actually_open_script_not_kitsus_latest(self):
        """Regression: (sync) used to always chase work.current_workfile_major()
        (Kitsu's registered latest) regardless of what's actually open --
        wrong the moment two majors exist and the artist still has the older
        one open (e.g. a teammate bumped the major elsewhere, or this is a
        second, older session)."""
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            t1 = ops.next_save(_t(), bump="major")          # major 1
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("v1", encoding="utf-8")
            ops.register_major(_t(), t1)
            t2 = ops.next_save(_t(), bump="major")          # major 2, registered separately
            Path(t2.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t2.path).write_text("v2", encoding="utf-8")
            ops.register_major(_t(), t2)
            self.assertEqual(ops.workfile_major(_t()), 2)   # Kitsu's own "latest"

            info = ops.resolve_output_path(_t(), "CompRender", SYNC_VERSION,
                                           open_script_path=t1.path)
            self.assertEqual(info["version"], 1)            # matches what's actually open

            # no open script given at all -- falls back to Kitsu's latest, same as before
            info2 = ops.resolve_output_path(_t(), "CompRender", SYNC_VERSION)
            self.assertEqual(info2["version"], 2)


class TestNameStreamsAndIdentify(unittest.TestCase):
    def test_name_streams_always_includes_main(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.assertEqual(ops.name_streams(_t(), "Precomp"), ["main"])

    def test_name_streams_lists_published_names(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            api.outputs.append({"output_type": "Precomp", "revision": 1, "name": "fg",
                                "representation": "exr", "path": "X:/o/fg_v001", "data": {}})
            api.outputs.append({"output_type": "Precomp", "revision": 1, "name": "bg",
                                "representation": "exr", "path": "X:/o/bg_v001", "data": {}})
            self.assertEqual(ops.name_streams(_t(), "Precomp"), ["bg", "fg", "main"])

    def test_identify_open_script_matches_the_right_name_stream(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            t = ops.next_save(_t(), name="fg", bump="major")
            Path(t.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t, name="fg")
            ident = ops.identify_open_script(_t(), t.path)
            self.assertEqual(ident, ("fg", 1, 1))

    def test_identify_open_script_none_for_unsaved(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            self.assertIsNone(ops.identify_open_script(_t(), ""))
            self.assertIsNone(ops.identify_open_script(_t(), "X:/scratch/untitled.nk"))


class TestVersionFromOutputPath(unittest.TestCase):
    def test_parses_the_version_directory_segment(self):
        from tools.dcc.nuke.ops import version_from_output_path
        self.assertEqual(version_from_output_path(
            "X:/ABC/SQ010/SH0100/output/comp/v004/exr/x.1001.exr"), 4)
        self.assertEqual(version_from_output_path(
            "X:/ABC/SQ010/SH0100/output/comp/v004/exr/x.####.exr".replace("/", "\\")), 4)

    def test_returns_none_when_not_found(self):
        from tools.dcc.nuke.ops import version_from_output_path
        self.assertIsNone(version_from_output_path(""))
        self.assertIsNone(version_from_output_path("X:/no/version/here.exr"))


class TestRenderedSnapshotIntegration(unittest.TestCase):
    """publish_render()'s source_script_path wiring: a NEW_VERSION render
    auto-registers a major (nothing may be registered at that number yet)
    and writes the read-only v{major}.000 snapshot; a SYNC_VERSION render
    just refreshes .000 at the already-registered major."""

    def _open_script(self, td, text="script state"):
        p = Path(td) / "open_script.nk"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_new_version_render_auto_registers_a_major_and_snapshots(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            src = self._open_script(td)
            before = len(api.workfiles)

            r = Path(td) / "r"; r.mkdir()
            (r / "c.1001.exr").write_bytes(b"x" * 10)
            res = ops.publish_render(_t(), [str(r / "c.1001.exr")], version=NEW_VERSION,
                                     source_script_path=src, proxy_dry_run=True)

            self.assertEqual(res.version, 1)
            self.assertEqual(len(api.workfiles), before + 1)      # a major got registered
            self.assertEqual(ops.workfile_major(_t()), 1)

            # rebuild the exact path the same way ops/work do, via the shot/task
            r_ = ops._resolve(_t())
            snap = work.workfile_path(r_.pctx, r_.shot, r_.task, major=1, minor=0)
            self.assertTrue(Path(snap).is_file())
            self.assertEqual(Path(snap).read_text(encoding="utf-8"), "script state")
            with self.assertRaises(OSError):
                Path(snap).write_text("oops", encoding="utf-8")

    def test_sync_render_refreshes_snapshot_without_registering_again(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            t = ops.next_save(_t(), bump="major")          # major 1, real minor .001
            Path(t.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t.path).write_text("minor .001", encoding="utf-8")
            ops.register_major(_t(), t)
            before = len(api.workfiles)

            src = self._open_script(td, text="edited past .001, rendering now")
            r = Path(td) / "r"; r.mkdir()
            (r / "c.1001.exr").write_bytes(b"x" * 10)
            res = ops.publish_render(_t(), [str(r / "c.1001.exr")], version=SYNC_VERSION,
                                     source_script_path=src, proxy_dry_run=True)

            self.assertEqual(res.version, 1)
            self.assertEqual(len(api.workfiles), before)          # no NEW major registered

            r_ = ops._resolve(_t())
            snap = work.workfile_path(r_.pctx, r_.shot, r_.task, major=1, minor=0)
            self.assertEqual(Path(snap).read_text(encoding="utf-8"),
                             "edited past .001, rendering now")

    def test_snapshot_render_writes_000_without_publishing(self):
        """.000 is the RENDER's own provenance record, not the publish's --
        a render that's never published (or published much later, from a
        different script state) must still get an accurate snapshot at
        render time."""
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            src = self._open_script(td)
            before = len(api.workfiles)

            rev = ops.snapshot_render(_t(), version=NEW_VERSION, source_script_path=src)

            self.assertEqual(rev, 1)
            self.assertEqual(len(api.workfiles), before + 1)
            self.assertEqual(ops.workfile_major(_t()), 1)
            r_ = ops._resolve(_t())
            snap = work.workfile_path(r_.pctx, r_.shot, r_.task, major=1, minor=0)
            self.assertTrue(Path(snap).is_file())
            self.assertEqual(Path(snap).read_text(encoding="utf-8"), "script state")
            # no output was actually published -- snapshot_render() only
            # resolves the version + snapshots .000, it never registers or
            # publishes anything to CompRender itself
            self.assertEqual(ops.output_versions(_t(), "CompRender"), [])

    def test_publish_render_after_snapshot_render_does_not_double_register(self):
        """The render-time snapshot_render() call and a LATER publish_render()
        for the SAME already-resolved version must land on the one working_files
        record, not two."""
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            src = self._open_script(td)
            before = len(api.workfiles)

            rev = ops.snapshot_render(_t(), version=NEW_VERSION, source_script_path=src)
            r = Path(td) / "r"; r.mkdir()
            (r / "c.1001.exr").write_bytes(b"x" * 10)
            res = ops.publish_render(_t(), [str(r / "c.1001.exr")], version=str(rev),
                                     source_script_path=src, proxy_dry_run=True)

            self.assertEqual(res.version, rev)
            self.assertEqual(len(api.workfiles), before + 1)      # still just the one


class TestNameScopedOutputs(unittest.TestCase):
    """Two different name-streams under the same media_type on the same shot
    (e.g. a Precomp "fg" and "bg") must not collide in lock checks or
    version resolution -- the actual bug behind wanting per-stream names
    (sq_name) to work at all."""

    def test_locked_main_does_not_lock_a_different_name_stream(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            api.outputs.append({"output_type": "Precomp", "revision": 1, "name": "main",
                                "representation": "exr", "path": "X:/o/main_v001",
                                "data": {"square": {"locked": True}}})
            api.outputs.append({"output_type": "Precomp", "revision": 1, "name": "fg",
                                "representation": "exr", "path": "X:/o/fg_v001", "data": {}})
            info = ops.resolve_output_path(_t(), "Precomp", "1", name="fg")
            self.assertFalse(info["locked"])
            info_main = ops.resolve_output_path(_t(), "Precomp", "1", name="main")
            self.assertTrue(info_main["locked"])

    def test_next_version_is_independent_per_name(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = _ops(td)
            r = Path(td) / "r"; r.mkdir()
            for rev in (1, 2, 3):
                f = r / f"main{rev}.1001.exr"; f.write_bytes(b"x" * 10)
                ops.publish_render(_t(), [str(f)], media_type="Precomp", version=str(rev),
                                   name="main", proxy_dry_run=True)
            info_fg = ops.resolve_output_path(_t(), "Precomp", NEW_VERSION, name="fg")
            self.assertEqual(info_fg["version"], 1)         # "fg" has nothing published yet
            info_main = ops.resolve_output_path(_t(), "Precomp", NEW_VERSION, name="main")
            self.assertEqual(info_main["version"], 4)


class TestVerifyWorkfileForRender(unittest.TestCase):
    def test_no_open_script_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            reason = ops.verify_workfile_for_render(_t(), open_script_path="")
            self.assertTrue(reason)

    def test_unrelated_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            reason = ops.verify_workfile_for_render(_t(), open_script_path="X:/scratch/untitled.nk")
            self.assertTrue(reason)

    def test_a_real_registered_script_passes(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            t = ops.next_save(_t(), bump="major")
            Path(t.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t)
            reason = ops.verify_workfile_for_render(_t(), open_script_path=t.path)
            self.assertEqual(reason, "")

    def test_sync_warns_when_open_script_is_behind_the_latest_major(self):
        with tempfile.TemporaryDirectory() as td:
            ops, _ = _ops(td)
            t1 = ops.next_save(_t(), bump="major")             # major 1
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t1)
            t2 = ops.next_save(_t(), bump="major")             # major 2
            Path(t2.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t2.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t2)

            # still have v1 open while v2 is Kitsu's latest
            reason = ops.verify_workfile_for_render(_t(), open_script_path=t1.path, sync=True)
            self.assertTrue(reason)
            self.assertIn("v001", reason)
            self.assertIn("v002", reason)

            # not-sync (NEW_VERSION) doesn't care about major alignment at all
            reason = ops.verify_workfile_for_render(_t(), open_script_path=t1.path, sync=False)
            self.assertEqual(reason, "")


# --------------------------------------------------------------------------
# gizmos, with a fake nuke
# --------------------------------------------------------------------------

class _Knob:
    def __init__(self, name, values=None):
        self._name = name
        self._values = list(values) if values else []
        self._v = self._values[0] if self._values else ""
        self._enabled = True
        self._startline = True     # real Nuke's own default for most knob types

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

    def setFlag(self, flag):
        self._startline = True

    def clearFlag(self, flag):
        self._startline = False


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


class _FakeRoot:
    """Stands in for nuke.root() -- only .name() (the open script's path) is
    needed by anything gizmos.py touches."""
    def __init__(self):
        self._name = ""

    def name(self):
        return self._name


class _FakeNuke:
    STARTLINE = 1  # real nuke.STARTLINE's actual value doesn't matter to _Knob's no-op

    def __init__(self):
        self.created = []
        self._this_node = None
        self._this_knob = None
        self._root = _FakeRoot()

    def root(self):
        return self._root

    def allNodes(self, cls=None):
        if cls is None:
            return list(self.created)
        return [n for n in self.created if n.Class() == cls]

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

    def Text_Knob(self, name, label=None, value=""):
        k = _Knob(name)
        if value:
            k.setValue(value)
        return k

    def Boolean_Knob(self, name, label=None):
        return _Knob(name)

    def PyScript_Knob(self, name, label=None, command=None):
        return _Knob(name)

    def Enumeration_Knob(self, name, label, values):
        return _Knob(name, values)

    def EditableEnumeration_Knob(self, name, label, values):
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

    def test_shot_change_cascades_all_the_way_to_media_and_version(self):
        """Regression: changing sq_shot resets sq_task programmatically (via
        _repopulate_next_level), which never raises its own knobChanged in
        real Nuke -- media type / name / version used to stay stale until
        the artist happened to touch media type by hand. Before the fix,
        only _repopulate_next_level ran here and output_files() was never
        called at all until a separate media-type knobChanged came in."""
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)      # seeds for SH0100 already
            calls = _count_calls(api, "output_files")

            node["sq_shot"].setValue("SH0110")
            nk._this_node, nk._this_knob = node, node["sq_shot"]
            gizmos.on_knob_changed(nk)
            self.assertGreaterEqual(len(calls), 1)

    def test_sq_name_dropdown_lists_known_name_streams(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            api.outputs.append({"output_type": "CompRender", "revision": 1, "name": "fg",
                                "representation": "exr", "path": "X:/o/fg_v001", "data": {}})
            node = gizmos.create_square_write(_FakeNuke())
            self.assertIn("fg", node["sq_name"].values())
            self.assertIn("main", node["sq_name"].values())

    def test_sq_name_typed_value_survives_repopulate(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)
            node["sq_name"].setValue("keying")
            nk._this_node, nk._this_knob = node, node["sq_name"]
            gizmos.on_knob_changed(nk)
            self.assertEqual(node["sq_name"].value(), "keying")
            self.assertIn("keying", node["sq_name"].values())

    def test_render_button_starts_its_own_line(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertTrue(node["sq_publish"]._startline)
            self.assertFalse(node["sq_publish_only"]._startline)  # shares Render's line

    def test_dividers_present_between_each_section(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            write_node = gizmos.create_square_write(_FakeNuke())
            for k in ("sq_div1", "sq_div2", "sq_div3", "sq_div4"):
                self.assertIn(k, write_node.knobs())
            read_node = gizmos.create_square_read(_FakeNuke())
            for k in ("sq_div1", "sq_div2"):
                self.assertIn(k, read_node.knobs())
            for k in ("sq_div3", "sq_div4"):
                self.assertNotIn(k, read_node.knobs())     # write-only sections

    def test_write_has_create_read_button_on_its_own_line(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertIn("sq_create_read", node.knobs())
            self.assertTrue(node["sq_create_read"]._startline)

    def test_refresh_button_exists_on_both_kinds(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            self.assertIn("sq_refresh", gizmos.create_square_write(_FakeNuke()).knobs())
            self.assertIn("sq_refresh", gizmos.create_square_read(_FakeNuke()).knobs())

    def test_refresh_node_drops_cache_and_reapplies(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)
            self.assertNotIn("v007", node["sq_version"].values())
            # simulate a publish that happened OUTSIDE this Nuke session --
            # nothing in this session touches ops's cache for it
            api.outputs.append({"output_type": "CompRender", "revision": 7, "name": "main",
                                "representation": "exr", "path": "X:/o/v007", "data": {}})
            nk._this_node = node
            gizmos.refresh_node(nk, node)
            self.assertIn("v007", node["sq_version"].values())

    def test_create_read_version_falls_back_when_file_knob_is_blank(self):
        """Regression: Create Read said the Write "hasn't rendered yet" when
        its `file` knob was blank (blanked on purpose while the resolved
        version is locked) -- the version is still knowable from the version
        knob, and a Read of a locked version is perfectly valid."""
        from tools.dcc.nuke import panel
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            t1 = ops.next_save(_t(), bump="major")           # major 1
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("v1", encoding="utf-8")
            ops.register_major(_t(), t1)
            api.outputs.append({"output_type": "CompRender", "revision": 1, "name": "main",
                                "representation": "exr", "path": "X:/o/v001",
                                "data": {"square": {"locked": True}}})
            nk = _FakeNuke()
            nk._root._name = t1.path
            node = gizmos.create_square_write(nk)
            self.assertEqual(node["file"].value(), "")            # locked -> blanked
            t = Target("ABC", "", "SQ010", "SH0100", "Comp")
            self.assertEqual(
                panel._resolved_write_version(nk, node, t, "CompRender", "main"), 1)

    def test_create_read_version_prefers_the_rendered_path(self):
        from tools.dcc.nuke import panel
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            nk = _FakeNuke()
            node = gizmos.create_square_write(nk)
            node["file"].setValue("Z:/o/comp/v007/exr/x.####.exr")
            t = Target("ABC", "", "SQ010", "SH0100", "Comp")
            self.assertEqual(
                panel._resolved_write_version(nk, node, t, "CompRender", "main"), 7)

    def test_refresh_all_square_nodes_catches_up_every_write_and_read(self):
        """Regression: Minor Up / Major Up / Save Version... change the open
        script via nuke.scriptSaveAs() directly, never touching any node's
        own knobs -- a Write left on (sync) would otherwise keep resolving
        against the major that was open BEFORE the bump."""
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            t1 = ops.next_save(_t(), bump="major")           # major 1
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("v1", encoding="utf-8")
            ops.register_major(_t(), t1)

            nk = _FakeNuke()
            nk._root._name = t1.path
            node = gizmos.create_square_write(nk)
            self.assertIn("v001", node["file"].value().replace("\\", "/"))

            # simulate Major Up: the open script becomes v2, but nothing on
            # the node itself is touched
            t2 = ops.next_save(_t(), bump="major")           # major 2
            Path(t2.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t2.path).write_text("v2", encoding="utf-8")
            ops.register_major(_t(), t2)
            nk._root._name = t2.path

            self.assertIn("v001", node["file"].value().replace("\\", "/"))    # still stale
            gizmos.refresh_all_square_nodes(nk)
            self.assertIn("v002", node["file"].value().replace("\\", "/"))    # caught up

    def test_apply_file_syncs_to_the_actually_open_script_not_kitsus_latest(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            t1 = ops.next_save(_t(), bump="major")           # major 1
            Path(t1.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t1.path).write_text("v1", encoding="utf-8")
            ops.register_major(_t(), t1)
            t2 = ops.next_save(_t(), bump="major")           # major 2, Kitsu's "latest"
            Path(t2.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t2.path).write_text("v2", encoding="utf-8")
            ops.register_major(_t(), t2)

            nk = _FakeNuke()
            nk._root._name = t1.path                          # artist has v1 open
            node = gizmos.create_square_write(nk)
            self.assertEqual(node["sq_version"].value(), SYNC_VERSION)
            self.assertIn("v001", node["file"].value().replace("\\", "/"))

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

    def test_context_knobs_share_one_line_task_onward_do_not(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            for k in ("sq_project", "sq_episode", "sq_shot",
                      "sq_task", "sq_name", "sq_version"):
                self.assertTrue(node[k]._startline, f"{k} should start its own line")
            self.assertFalse(node["sq_sequence"]._startline, "sq_sequence should share Episode's line")
            self.assertFalse(node["sq_media_type"]._startline, "sq_media_type should share Task's line")
            self.assertFalse(node["sq_refresh"]._startline, "sq_refresh should share Version's line")

    def test_write_checkboxes_each_get_their_own_line(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertTrue(node["sq_preview"]._startline)
            self.assertTrue(node["sq_do_publish"]._startline)

    def test_square_write_has_a_direct_publish_button(self):
        """Publish rendered-but-not-yet-published frames without having to
        re-render (sq_publish already does render + optionally publish)."""
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertIn("sq_publish_only", node.knobs())

    def test_square_read_has_no_publish_toggle_or_button(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_read(_FakeNuke())
            self.assertNotIn("sq_do_publish", node.knobs())
            self.assertNotIn("sq_publish_only", node.knobs())

    def test_version_dropdown_offers_both_new_and_sync(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertIn(NEW_VERSION, node["sq_version"].values())
            self.assertIn(SYNC_VERSION, node["sq_version"].values())
            self.assertEqual(node["sq_version"].value(), SYNC_VERSION)  # default

    def test_sq_name_is_free_text_defaulting_to_main(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertEqual(node["sq_name"].value(), "main")
            # a fixed Enumeration_Knob can't be set to a value outside its
            # list -- setValue() on the real String_Knob fake always sticks,
            # which is what proves this is free text, not a dropdown.
            node["sq_name"].setValue("fg")
            self.assertEqual(node["sq_name"].value(), "fg")

    def test_changing_sq_name_re_resolves_the_file_for_that_stream(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            node["sq_name"].setValue("fg")
            nk = _FakeNuke()
            nk._this_node, nk._this_knob = node, node["sq_name"]
            gizmos.on_knob_changed(nk)
            self.assertIn("_fg_", node["file"].value())

    def test_locked_sync_version_blanks_file_and_disables_publish(self):
        with tempfile.TemporaryDirectory() as td:
            ops, api = self._wire(td)
            # register the workfile up to major 1, then lock CompRender v1 --
            # (sync) resolves to the current workfile major, so this must
            # refuse to point Write at a renderable path.
            t = ops.next_save(_t(), bump="major")
            Path(t.path).parent.mkdir(parents=True, exist_ok=True)
            Path(t.path).write_text("x", encoding="utf-8")
            ops.register_major(_t(), t)
            api.outputs.append({"output_type": "CompRender", "revision": 1, "name": "main",
                                "representation": "exr", "path": "X:/o/v001",
                                "data": {"square": {"locked": True}}})
            node = gizmos.create_square_write(_FakeNuke())
            self.assertEqual(node["sq_version"].value(), SYNC_VERSION)
            self.assertEqual(node["file"].value(), "")
            self.assertFalse(node["sq_publish"]._enabled)
            self.assertFalse(node["sq_publish_only"]._enabled)
            self.assertIn("LOCKED", node["sq_status"].value())

    def test_unlocked_version_keeps_publish_buttons_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            self._wire(td)
            node = gizmos.create_square_write(_FakeNuke())
            self.assertTrue(node["sq_publish"]._enabled)
            self.assertTrue(node["sq_publish_only"]._enabled)
            self.assertNotEqual(node["file"].value(), "")


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

    def test_write_publishes_what_was_actually_rendered_not_the_script_range(self):
        """Regression: 12 of 100 frames rendered, then Publish demanded all
        100 and failed on frame 13 ("88 frames missing")."""
        from tools.dcc.nuke import panel
        with tempfile.TemporaryDirectory() as td:
            for f in range(1001, 1013):
                (Path(td) / f"comp.{f}.exr").write_bytes(b"x")
            node = _Node("Write")
            node["file"].setValue(str(Path(td) / "comp.####.exr").replace("\\", "/"))
            frames = panel._node_frames(_FakeNuke(), node)     # no explicit range
            self.assertEqual(len(frames), 12)
            self.assertTrue(frames[0].endswith("comp.1001.exr"))
            self.assertTrue(frames[-1].endswith("comp.1012.exr"))

    def test_a_gap_inside_the_rendered_span_still_counts_as_missing(self):
        from tools.dcc.nuke import panel
        with tempfile.TemporaryDirectory() as td:
            for f in (1001, 1002, 1004):
                (Path(td) / f"comp.{f}.exr").write_bytes(b"x")
            node = _Node("Write")
            node["file"].setValue(str(Path(td) / "comp.####.exr").replace("\\", "/"))
            frames = panel._node_frames(_FakeNuke(), node)
            self.assertEqual(len(frames), 4)                    # 1001..1004
            self.assertEqual([f for f in frames if not panel._exists(f)],
                             [frames[2]])                       # 1003 is the hole

    def test_frames_on_disk_ignores_non_numeric_lookalikes(self):
        from tools.dcc.nuke import panel
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "comp.1001.exr").write_bytes(b"x")
            (Path(td) / "comp.abcd.exr").write_bytes(b"x")
            found = panel._frames_on_disk(str(Path(td) / "comp.####.exr").replace("\\", "/"))
            self.assertEqual(found, [1001])

    def test_node_frames_honors_an_explicit_range_override(self):
        """The Render panel's own (possibly-edited) frame range must be what
        gets published too, not the script's root range -- otherwise a
        custom render range and the frames actually published could differ."""
        from tools.dcc.nuke import panel
        node = _Node("Write")
        node["file"].setValue("X:/sh/comp.####.exr")
        frames = panel._node_frames(_FakeNuke(), node, first=2001, last=2002)
        self.assertEqual(frames, ["X:/sh/comp.2001.exr", "X:/sh/comp.2002.exr"])


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
