import unittest

from square_core.ingest_item import IngestItem
from square_core.preview_metadata import PreviewMetadata, KITSU_DATA_KEY
from square_core.kitsu_recorder import KitsuRecorder, default_comment, _pick_ingest_task


class FakeGateway:
    def __init__(self):
        self.seqs = {}
        self.shots = {}
        self.tasks_by_shot = {}
        self.comments = []
        self.previews = []
        self.preview_data = {}          # id -> data dict
        self.shot_data_writes = []
        self.set_main_calls = []
        self._id = 0

    def _nid(self, p):
        self._id += 1
        return f"{p}-{self._id}"

    def get_or_create_sequence(self, project, name):
        key = (project["id"], name)
        if key not in self.seqs:
            self.seqs[key] = {"id": self._nid("seq"), "name": name}
        return self.seqs[key]

    def get_or_create_shot(self, project, sequence, name, **fields):
        key = (sequence["id"], name)
        if key not in self.shots:
            self.shots[key] = {"id": self._nid("shot"), "name": name, "data": {}, **fields}
        return self.shots[key]

    def ensure_tasks(self, shot, names):
        got = self.tasks_by_shot.setdefault(shot["id"], {})
        for n in names:
            got.setdefault(n, {"id": self._nid("task"), "name": n, "task_type_id": f"tt-{n}"})
        return list(got.values())

    def add_comment(self, task, text, status=None):
        c = {"id": self._nid("comment"), "task_id": task["id"], "text": text, "status": status}
        self.comments.append(c)
        return c

    def upload_preview(self, task, text, file_path, status=None):
        self.preview_status = status
        p = {"id": self._nid("preview"), "task_id": task["id"], "path": file_path, "data": {"original_width": 1280}}
        self.previews.append(p)
        self.preview_data[p["id"]] = dict(p["data"])
        return p

    def set_main_preview(self, preview):
        self.set_main_calls.append(preview["id"])

    def get_preview_data(self, preview_id):
        return dict(self.preview_data.get(preview_id, {}))

    def update_preview_data(self, preview, data):
        self.preview_data[preview["id"]] = dict(data)
        return {**preview, "data": data}

    def update_shot_data(self, shot, data):
        shot["data"] = data
        self.shot_data_writes.append((shot["id"], data))
        return shot


def _item(**kw):
    base = dict(key="k", source_files=["/d/a.1001.exr"], ext=".exr",
                sequence_code="SQ010", shot_code="SH0100", media_type="Plate",
                media_name="main", version=3, start_frame=1001, end_frame=1096)
    base.update(kw)
    return IngestItem(**base)


def _meta(**kw):
    base = dict(nas_path="/nas/v3", source_path="/deliver", frame_range="1001-1096 (96 frames)",
                file_count=96, resolution="3840x2160", fps=24.0, colorspace="ACEScg",
                checksum="abcd", version=3, media_name="main")
    base.update(kw)
    return PreviewMetadata(**base)


PROJECT = {"id": "proj-1", "name": "Show", "code": "SHW"}


class TestEnsure(unittest.TestCase):
    def test_ensure_shot_creates_seq_and_shot(self):
        gw = FakeGateway()
        rec = KitsuRecorder(gw)
        shot = rec.ensure_shot(PROJECT, _item())
        self.assertTrue(shot["id"].startswith("shot-"))
        self.assertEqual(len(gw.seqs), 1)

    def test_ensure_shot_is_idempotent(self):
        gw = FakeGateway()
        rec = KitsuRecorder(gw)
        a = rec.ensure_shot(PROJECT, _item())
        b = rec.ensure_shot(PROJECT, _item())
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(gw.shots), 1)


class TestRecordVersion(unittest.TestCase):
    """record_version() is the critical-path write: comment + task->Done + shot.data entry. No preview."""

    def setUp(self):
        self.gw = FakeGateway()
        self.rec = KitsuRecorder(self.gw)

    def test_posts_comment_and_moves_task_to_done(self):
        out = self.rec.record_version(PROJECT, _item(), _meta(), task_types=["Ingest", "Prep"])
        self.assertEqual(len(self.gw.comments), 1)
        self.assertEqual(self.gw.comments[0]["status"], "Done")
        self.assertTrue(out.comment_id)
        self.assertEqual(out.task_name, "Ingest")           # preferred over Prep
        self.assertFalse(out.has_preview)                    # not yet
        self.assertEqual(self.gw.previews, [])

    def test_appends_version_entry_without_preview(self):
        self.rec.record_version(PROJECT, _item(version=3), _meta(version=3), task_types=["Ingest"])
        _sid, data = self.gw.shot_data_writes[-1]
        versions = data["media_items"]["main"]["versions"]
        self.assertIn("v003", versions)
        self.assertFalse(versions["v003"]["has_preview"])

    def test_second_version_does_not_wipe_the_first(self):
        self.rec.record_version(PROJECT, _item(version=1), _meta(version=1), task_types=["Ingest"])
        self.rec.record_version(PROJECT, _item(version=2), _meta(version=2), task_types=["Ingest"])
        _sid, data = self.gw.shot_data_writes[-1]
        self.assertEqual(set(data["media_items"]["main"]["versions"]), {"v001", "v002"})

    def test_outcome_carries_runtime_handles_for_deferred_attach(self):
        out = self.rec.record_version(PROJECT, _item(), _meta(), task_types=["Ingest"])
        self.assertIsInstance(out.shot, dict)
        self.assertIsInstance(out.ingest_task, dict)


class TestAttachPreview(unittest.TestCase):
    def setUp(self):
        self.gw = FakeGateway()
        self.rec = KitsuRecorder(self.gw)

    def test_uploads_stamps_and_updates_the_version_entry(self):
        out = self.rec.record_version(PROJECT, _item(version=3), _meta(version=3, nas_path="/nas/v3"),
                                      task_types=["Ingest"])
        pid = self.rec.attach_preview(out, _item(version=3), _meta(version=3, nas_path="/nas/v3"),
                                      "/tmp/p.mp4")
        self.assertTrue(pid)
        self.assertEqual(len(self.gw.previews), 1)
        self.assertEqual(self.gw.set_main_calls, [pid])
        stored = self.gw.preview_data[pid]
        self.assertEqual(stored["original_width"], 1280)                 # Zou key preserved
        self.assertEqual(stored[KITSU_DATA_KEY]["nas_path"], "/nas/v3")
        _sid, data = self.gw.shot_data_writes[-1]
        self.assertTrue(data["media_items"]["main"]["versions"]["v003"]["has_preview"])

    def test_no_task_no_op(self):
        from square_core.kitsu_recorder import RecordOutcome
        self.assertEqual(self.rec.attach_preview(RecordOutcome(), _item(), _meta(), "/tmp/p.mp4"), "")

    def test_resolve_ingest_task_gives_handles_without_writing(self):
        out = self.rec.resolve_ingest_task(PROJECT, _item(), task_types=["Ingest"])
        self.assertIsInstance(out.ingest_task, dict)
        self.assertEqual(self.gw.comments, [])
        self.assertEqual(self.gw.shot_data_writes, [])


class TestDryRun(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        gw = FakeGateway()
        rec = KitsuRecorder(gw, dry_run=True)
        out = rec.record_version(PROJECT, _item(), _meta(), task_types=["Ingest"])
        self.assertTrue(out.dry_run)
        self.assertEqual(rec.attach_preview(out, _item(), _meta(), "/tmp/p.mp4"), "")
        self.assertEqual(gw.shot_data_writes, [])
        self.assertEqual(gw.previews, [])
        self.assertEqual(gw.comments, [])


class TestHelpers(unittest.TestCase):
    def test_pick_ingest_task_preference(self):
        tasks = [{"name": "Comp", "id": "1"}, {"name": "Prep", "id": "2"}, {"name": "Ingest", "id": "3"}]
        self.assertEqual(_pick_ingest_task(tasks)["name"], "Ingest")

    def test_pick_falls_back_to_first(self):
        tasks = [{"name": "Comp", "id": "1"}, {"name": "Roto", "id": "2"}]
        self.assertEqual(_pick_ingest_task(tasks)["name"], "Comp")

    def test_default_comment_mentions_paths_and_version(self):
        text = default_comment(_item(version=4), _meta(nas_path="/nas/x", source_path="/deliver/y"))
        self.assertIn("v004", text)
        self.assertIn("/nas/x", text)
        self.assertIn("/deliver/y", text)


if __name__ == "__main__":
    unittest.main()
