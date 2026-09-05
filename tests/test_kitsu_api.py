"""square_core.kitsu.KitsuApi -- unit-tested with a fake backend (no gazu)."""

import unittest
from unittest.mock import patch

from square_core.kitsu import api as kitsu_api
from square_core.kitsu.api import KitsuApi
from square_core.kitsu import OfflineApi, auth as kitsu_auth
from square_core.model import Provenance
from square_core.errors import NeedsLogin


class FakeBackend:
    def __init__(self):
        self._id = 0
        self.projects = []
        self.seqs = {}
        self.shots = {}
        self.tasks = {}          # shot_id -> {tt_id: task}
        self.task_types = []
        self.statuses = [
            {"id": "st-todo", "name": "Todo", "short_name": "todo"},
            {"id": "st-done", "name": "Done", "short_name": "done", "is_done": True},
        ]
        self.output_types = []
        self.output_files = []   # list of dicts
        self.working_files = []
        self.comments = []
        self.previews = []
        self.preview_files = {}
        self.file_tree_set = []
        self.applied_templates = []

    def _nid(self, p):
        self._id += 1
        return f"{p}-{self._id}"

    # projects
    def all_projects(self):
        return list(self.projects)

    def get_project(self, ident):
        for p in self.projects:
            if ident in (p["id"], p.get("code"), p.get("name")):
                return p
        return None

    def new_project(self, name, production_type="short", **kw):
        p = {"id": self._nid("proj"), "name": name, "production_type": production_type, **kw}
        self.projects.append(p)
        return p

    def update_project(self, project):
        return project

    def all_project_templates(self):
        return [{"id": "tpl-1", "name": "VFX Shots"}]

    def get_project_template_by_name(self, name):  # used via apply
        return {"id": "tpl-1", "name": name}

    def apply_project_template(self, project, name):
        self.applied_templates.append((project["id"], name))

    def set_minimal_file_tree(self, project):
        self.file_tree_set.append(project["id"])

    # breakdown
    def get_or_create_sequence(self, project, name):
        key = (project["id"], name)
        self.seqs.setdefault(key, {"id": self._nid("seq"), "name": name, "project_id": project["id"]})
        return self.seqs[key]

    def get_or_create_shot(self, project, sequence, name, **f):
        key = (sequence["id"], name)
        self.shots.setdefault(key, {"id": self._nid("shot"), "name": name,
                                    "parent_id": sequence["id"], "nb_frames": 1,
                                    "data": {"frame_in": f.get("frame_in"), "frame_out": f.get("frame_out")}})
        return self.shots[key]

    def all_shots_for_project(self, project):
        return [s for s in self.shots.values() if True]

    def update_shot_data(self, shot, data):
        shot["data"] = data
        return shot

    def update_entity_data(self, entity, data):
        return {"id": entity["id"], "data": data}

    # tasks
    def all_task_types(self):
        return list(self.task_types)

    def all_task_statuses(self):
        return list(self.statuses)

    def all_tasks_for_shot(self, shot):
        return list(self.tasks.get(shot["id"], {}).values())

    def new_task_type(self, name, for_entity="Shot"):
        tt = {"id": self._nid("tt"), "name": name, "for_entity": for_entity}
        self.task_types.append(tt)
        return tt

    def update_task_type(self, tt):
        return tt

    def new_task(self, entity, task_type):
        t = {"id": self._nid("task"), "task_type_id": task_type["id"],
             "task_type_name": task_type["name"], "entity_id": entity["id"],
             "task_status_id": "st-todo"}
        self.tasks.setdefault(entity["id"], {})[task_type["id"]] = t
        return t

    def get_default_task_status(self):
        return self.statuses[0]

    def _task_by_id(self, tid):
        for grid in self.tasks.values():
            for t in grid.values():
                if t["id"] == tid:
                    return t
        return None

    def get_task(self, ident):
        return self._task_by_id(ident if isinstance(ident, str) else ident.get("id"))

    def add_comment(self, task, status, text):
        c = {"id": self._nid("cmt"), "task_id": task["id"], "text": text,
             "task_status": status, "task_status_id": status.get("id")}
        self.comments.append(c)
        # Kitsu: every comment carries a status and posting one moves the task
        real = self._task_by_id(task["id"])
        if real is not None:
            real["task_status_id"] = status.get("id")
        return c

    def assign_task(self, task, person):
        pass

    # previews
    def add_preview(self, task, comment, file_path):
        p = {"id": self._nid("prev"), "task_id": task["id"], "path": file_path,
             "data": {"original_width": 1920}}
        self.previews.append(p)
        self.preview_files[p["id"]] = dict(p)
        return p

    def set_main_preview(self, preview):
        self.main_preview = preview["id"]

    def get_preview_file(self, pid):
        return self.preview_files.get(pid, {})

    def update_preview(self, preview, payload):
        self.preview_files.setdefault(preview["id"], {}).update(payload)
        return self.preview_files[preview["id"]]

    def get_annotations(self, preview):
        return self.preview_files.get(preview["id"], {}).get("annotations") or []

    def update_preview_annotations(self, preview, *, additions, updates, deletions):
        self.preview_files.setdefault(preview["id"], {}).setdefault("annotations", []).extend(additions)
        return {"ok": True}

    # output types / versions
    def all_output_types(self):
        return list(self.output_types)

    def new_output_type(self, name, short_name=""):
        ot = {"id": self._nid("ot"), "name": name, "short_name": short_name or name[:3].lower()}
        self.output_types.append(ot)
        return ot

    def working_files_for_task(self, task):
        return [w for w in self.working_files if w["task_id"] == task["id"]]

    def new_working_file(self, task, *, name="main", revision=0, software=None):
        w = {"id": self._nid("wf"), "task_id": task["id"], "name": name,
             "revision": revision or 1, "path": "zou/computed/path"}
        self.working_files.append(w)
        return w

    def set_working_file_path(self, wf_id, path, data=None):
        for w in self.working_files:
            if w["id"] == wf_id:
                w["path"] = path
                if data is not None:
                    w["data"] = data
                return w
        return {}

    def output_files_for_entity(self, entity, *, output_type=None):
        out = [o for o in self.output_files if o["entity_id"] == entity["id"]]
        if output_type:
            out = [o for o in out if o.get("output_type_id") == output_type["id"]]
        return out

    def next_output_revision(self, entity, output_type, task_type, name="main"):
        revs = [o["revision"] for o in self.output_files
                if o["entity_id"] == entity["id"] and o.get("output_type_id") == output_type["id"]
                and o.get("name", "main") == name]
        return (max(revs) + 1) if revs else 1

    def new_output_file(self, entity, output_type, task_type, *, comment="", name="main",
                        revision=1, representation=""):
        o = {"id": self._nid("of"), "entity_id": entity["id"], "output_type_id": output_type["id"],
             "output_type_name": output_type["name"], "name": name, "revision": revision,
             "representation": representation, "path": "zou/computed"}
        self.output_files.append(o)
        return o

    def update_output_file(self, of_id, path, data=None):
        for o in self.output_files:
            if o["id"] == of_id:
                o["path"] = path
                if data is not None:
                    o["data"] = data
                return o
        return {}


def _api():
    return KitsuApi(FakeBackend(), host="http://kitsu.test")


class TestProjectsAndBreakdown(unittest.TestCase):
    def test_create_project_applies_template_and_file_tree(self):
        api = _api()
        p = api.create_project(code="ABC", name="Alpha", kitsu_template="VFX Shots",
                               production_type="tvshow")
        self.assertEqual(p.code, "ABC")
        self.assertTrue(p.is_episodic)
        self.assertEqual(api._b.applied_templates, [(p.id, "VFX Shots")])
        self.assertIn(p.id, api._b.file_tree_set)

    def test_ensure_shot_and_tasks_idempotent(self):
        api = _api()
        proj = api.create_project(code="ABC")
        seq = api.ensure_sequence(proj, "SQ010")
        shot = api.ensure_shot(proj, seq, "SH0100", frame_in=1001, frame_out=1096)
        self.assertEqual(shot.code, "SH0100")

        t1 = api.ensure_tasks(shot, ["Ingest", "Comp"])
        t2 = api.ensure_tasks(shot, ["Ingest", "Comp"])
        self.assertEqual({t.task_type_name for t in t1}, {"Ingest", "Comp"})
        self.assertEqual([t.id for t in t1], [t.id for t in t2])   # no duplicates


class TestStatusAndReview(unittest.TestCase):
    def test_set_status_matches_by_short_name(self):
        api = _api()
        proj = api.create_project(code="ABC")
        shot = api.ensure_shot(proj, api.ensure_sequence(proj, "S"), "SH")
        [task] = api.ensure_tasks(shot, ["Ingest"])
        api.set_status(task, "done", comment="ingested")
        self.assertEqual(api._b.comments[-1]["task_status"]["id"], "st-done")

    def test_a_later_comment_from_a_stale_task_object_does_not_revert_status(self):
        # Confirmed bug: the ingest flow set "Done", then posted a follow-up
        # comment (and later an async review-proxy comment) built from the
        # task object it fetched BEFORE the status change -- each carried the
        # stale "todo" status_id and flipped the task back. _task_current_status
        # now re-reads the live task instead of trusting the snapshot.
        api = _api()
        proj = api.create_project(code="ABC")
        shot = api.ensure_shot(proj, api.ensure_sequence(proj, "S"), "SH")
        [task] = api.ensure_tasks(shot, ["Ingest"])          # snapshot: status todo

        api.set_status(task, "done", comment="Ingested Plate 'bg'")
        api.comment(task, "Preview v001")                    # same stale `task` object

        self.assertEqual(api._b.get_task(task.id)["task_status_id"], "st-done")

    def test_upload_preview_and_stamp_provenance(self):
        api = _api()
        proj = api.create_project(code="ABC")
        shot = api.ensure_shot(proj, api.ensure_sequence(proj, "S"), "SH")
        [task] = api.ensure_tasks(shot, ["Ingest"])
        prev = api.upload_preview(task, "/tmp/p.mov", comment="v1", status="Done")
        api.set_main_preview(prev)
        api.stamp_provenance(prev, Provenance(kind="ingest", shot_code="SH", version=1), on="preview")
        data = api.preview_data(prev)
        self.assertEqual(data["original_width"], 1920)         # zou's key preserved
        self.assertEqual(data["square"]["shot_code"], "SH")

    def test_annotations_round_trip(self):
        api = _api()
        proj = api.create_project(code="ABC")
        shot = api.ensure_shot(proj, api.ensure_sequence(proj, "S"), "SH")
        [task] = api.ensure_tasks(shot, ["Comp"])
        prev = api.upload_preview(task, "/tmp/p.mov")
        api.update_annotations(prev, additions=[{"frame": 1, "drawing": "x"}])
        self.assertEqual(api.annotations(prev), [{"frame": 1, "drawing": "x"}])


class TestVersions(unittest.TestCase):
    def setUp(self):
        self.api = _api()
        proj = self.api.create_project(code="ABC")
        self.shot = self.api.ensure_shot(proj, self.api.ensure_sequence(proj, "S"), "SH")
        self.tasks = self.api.ensure_tasks(self.shot, ["Comp"])

    def test_ensure_output_type_idempotent(self):
        a = self.api.ensure_output_type("Plate")
        b = self.api.ensure_output_type("plate")
        self.assertEqual(a["id"], b["id"])

    def test_next_output_revision_increments(self):
        tt = self.tasks[0]
        self.assertEqual(self.api.next_output_revision(self.shot, "comp", tt), 1)
        self.api.record_output_file(self.shot, "comp", tt, revision=1,
                                    path="X:/ours/comp/v001/f.exr", representation="exr")
        self.assertEqual(self.api.next_output_revision(self.shot, "comp", tt), 2)

    def test_record_output_file_stores_our_path_and_provenance(self):
        tt = self.tasks[0]
        prov = Provenance(kind="publish", shot_code="SH", version=3)
        out = self.api.record_output_file(
            self.shot, "comp", tt, revision=3, representation="exr",
            path="X:/Show/SQ/SH/output/comp/v003/exr/Show_SQ_SH_comp_v003.1001.exr",
            data=prov.to_kitsu_data(),
        )
        self.assertTrue(out.path.endswith("_comp_v003.1001.exr"))
        self.assertEqual(out.data["square"]["version"], 3)

    def test_record_working_file_our_path(self):
        [task] = self.tasks
        self.assertEqual(self.api.next_working_revision(task), 1)
        wf = self.api.record_working_file(task, revision=1, path="X:/ours/work/v001.nk",
                                          software="nuke")
        self.assertEqual(wf.path, "X:/ours/work/v001.nk")
        self.assertEqual(self.api.next_working_revision(task), 2)


class TestOffline(unittest.TestCase):
    def test_offline_runs_through(self):
        api = OfflineApi()
        p = api.project("ABC")
        seq = api.ensure_sequence(p, "SQ010")
        shot = api.ensure_shot(p, seq, "SH0100", frame_in=1001, frame_out=1010)
        tasks = api.ensure_tasks(shot, ["Ingest"])
        self.assertEqual(tasks[0].task_type_name, "Ingest")
        self.assertEqual(api.next_output_revision(shot, "Plate", tasks[0]), 1)
        out = api.record_output_file(shot, "Plate", tasks[0], revision=1, path="X:/nas/p.exr")
        self.assertEqual(out.path, "X:/nas/p.exr")
        api.set_status(tasks[0], "Done")  # no-op, no raise


class TestConnect(unittest.TestCase):
    """
    Confirmed bug: a cached session that gazu's server rejects (expired past
    refresh, revoked, or just malformed) surfaced as a raw gazu exception
    (e.g. NotAuthenticatedException, or even ParameterException for a
    malformed-token 400) instead of NeedsLogin -- so a tool's `except
    NeedsLogin: show the login dialog` never triggered, and the failure was
    either an unhandled crash or (ingest_tool) a silent fall-through to
    offline mode with no way to re-authenticate. connect() now verifies the
    cached token by calling current_user() and treats any gazu-side failure
    there the same as no token at all.
    """

    def test_raises_needs_login_when_there_is_no_cached_session(self):
        with patch.object(kitsu_auth, "cached_session", return_value=None):
            with self.assertRaises(NeedsLogin):
                kitsu_api.connect("http://example.test")

    def test_raises_needs_login_when_the_cached_token_is_rejected_by_the_server(self):
        import gazu.exception

        class _RejectingBackend:
            def __init__(self, host):
                pass

            def attach(self, session, on_refresh=None):
                return self

            def current_user(self):
                raise gazu.exception.NotAuthenticatedException("auth/authenticated")

        with patch.object(kitsu_auth, "cached_session", return_value={"access_token": "stale"}), \
             patch("square_core.kitsu._gazu.GazuBackend", _RejectingBackend):
            with self.assertRaises(NeedsLogin):
                kitsu_api.connect("http://example.test")

    def test_a_malformed_400_response_is_also_treated_as_needs_login(self):
        # The real-world case that slipped through: a bad/garbage token can
        # get a 400 (ParameterException) from Zou instead of a clean 401.
        import gazu.exception

        class _MalformedTokenBackend:
            def __init__(self, host):
                pass

            def attach(self, session, on_refresh=None):
                return self

            def current_user(self):
                raise gazu.exception.ParameterException(
                    "auth/authenticated", "No additional information")

        with patch.object(kitsu_auth, "cached_session", return_value={"access_token": "garbage"}), \
             patch("square_core.kitsu._gazu.GazuBackend", _MalformedTokenBackend):
            with self.assertRaises(NeedsLogin):
                kitsu_api.connect("http://example.test")

    def test_returns_an_api_when_the_cached_token_is_valid(self):
        class _WorkingBackend:
            def __init__(self, host):
                pass

            def attach(self, session, on_refresh=None):
                return self

            def current_user(self):
                return {"id": "u1", "email": "a@b.com", "full_name": "A B"}

        with patch.object(kitsu_auth, "cached_session", return_value={"access_token": "good"}), \
             patch("square_core.kitsu._gazu.GazuBackend", _WorkingBackend):
            result = kitsu_api.connect("http://example.test")
        self.assertIsInstance(result, KitsuApi)


if __name__ == "__main__":
    unittest.main()
