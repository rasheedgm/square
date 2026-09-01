import os
import shutil
import tempfile
import unittest
from pathlib import Path

from square_core.ingest_item import IngestItem, Status, Stage, Action, IssueKind
from square_core.ingest_controller import IngestController, ControllerConfig
from square_core.ingest_ledger import IngestLedger


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeNAS:
    def __init__(self, root):
        self.root = Path(root)
        self.slot_override = {}      # dest_dir str -> (state, detail)
        self.next_free = {}          # (seq,shot,media) -> version
        self.structures_made = []

    def get_dest_dir(self, code, seq, shot, media, version=1, media_type="", resolution="", dir_template=None):
        return self.root / code / seq / shot / f"{media}_v{version:03d}"

    def dest_names(self, item, version_num, proj_code, filename_template=None):
        out = {}
        for i, f in enumerate(item.source_files):
            out[f] = f"{item.shot_code}_{item.media_name}_v{version_num:03d}.{1001+i:04d}{item.ext}"
        return out

    def inspect_slot(self, dest_dir, item, version_num, proj_code, filename_template=None, hasher=None):
        return self.slot_override.get(str(dest_dir), ("empty", ""))

    def next_free_version(self, item, proj_code, filename_template=None, start=1, dir_template=None):
        return self.next_free.get((item.sequence_code, item.shot_code, item.media_name), start)

    def create_shot_structure(self, dest_dir, structure=None):
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        self.structures_made.append(str(dest_dir))
        return dest_dir

    def copy_sequence(self, item, dest_dir, filename_template=None, version_num=1,
                      proj_code="", progress_callback=None,
                      pool=None, hasher=None, source_hashes=None):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        names = self.dest_names(item, version_num, proj_code, filename_template)
        out = []
        for n, src in enumerate(item.source_files, 1):
            d = dest_dir / names[src]
            d.write_bytes(Path(src).read_bytes())
            out.append(str(d))
            if progress_callback:
                progress_callback(n, len(item.source_files), d.name)
        return out


class FakeRecorder:
    def __init__(self):
        self.records = []
        self.previews = []
        self.dry_run = False

    def ensure_shot(self, project, item):
        return {"id": f"shot-{item.shot_code}", "name": item.shot_code, "data": {}}

    def ensure_tasks(self, shot, task_types):
        return [{"id": f"task-{t}", "name": t} for t in task_types]

    def record_version(self, project, item, preview_meta, *, task_types, comment_text=None):
        from square_core.kitsu_recorder import RecordOutcome
        self.records.append({"key": item.key, "version": item.version, "meta": preview_meta})
        return RecordOutcome(shot_id=f"shot-{item.shot_code}",
                             shot={"id": f"shot-{item.shot_code}", "data": {}},
                             ingest_task={"id": "task-Ingest", "task_type_name": "Ingest"})

    def resolve_ingest_task(self, project, item, task_types):
        from square_core.kitsu_recorder import RecordOutcome
        return RecordOutcome(shot_id=f"shot-{item.shot_code}",
                             shot={"id": f"shot-{item.shot_code}", "data": {}},
                             ingest_task={"id": "task-Ingest", "task_type_name": "Ingest"})

    def attach_preview(self, outcome, item, preview_meta, preview_path):
        self.previews.append({"key": item.key, "path": preview_path})
        outcome.preview_id = "prev-1"
        outcome.has_preview = True
        return "prev-1"


class FakeProxyGen:
    def __init__(self, out="/tmp/proxy.mp4"):
        self.out = out
        self.calls = 0

    def generate_proxy(self, item, dest_name=None):
        self.calls += 1
        return self.out


class FakeExtractor:
    def __init__(self, found=None, backend="oiio"):
        self.found = found if found is not None else {
            "resolution": "1920x1080", "width": 1920, "height": 1080,
            "fps": 24.0, "colorspace": "ACEScg",
        }
        self.backend = backend

    def probe(self, path):
        return dict(self.found), self.backend


PROJECT = {"id": "proj-1", "name": "Show", "code": "SHW"}


# ---------------------------------------------------------------------------

class ControllerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "deliver"
        self.src.mkdir()
        self.nas = FakeNAS(self.tmp / "nas")
        self.ledger = IngestLedger(self.tmp / "ledger.db")
        self.recorder = FakeRecorder()
        self.proxy = FakeProxyGen()
        self.cfg = ControllerConfig(
            nas_root=str(self.tmp / "nas"), project_code="SHW",
            filename_template="{shot}_{name}_v{version:03d}.{frame}{ext}",
            preview_media_types=["Plate"],
            media_type_configs={"Plate": "p", "Ref": "r"},
            task_types=["Ingest", "Comp"], copy_workers=2, ingested_by="me@studio.com",
        )
        self.events = []

    def _ctrl(self):
        c = IngestController(
            self.cfg, PROJECT, nas=self.nas, ledger=self.ledger,
            recorder=self.recorder, proxy_generator=self.proxy, extractor=FakeExtractor(),
        )
        c.subscribe(self.events.append)
        self.addCleanup(c.shutdown)
        return c

    @staticmethod
    def _drain_previews(c, timeout=5):
        import concurrent.futures as _cf
        futs = list(c._preview_futures.values())
        if futs:
            _cf.wait(futs, timeout=timeout)

    def _mkfiles(self, name, n=2, content=b"exr-bytes"):
        out = []
        for i in range(n):
            p = self.src / f"{name}.{1001+i:04d}.exr"
            p.write_bytes(content + str(i).encode())
            out.append(str(p))
        return out

    def _scan(self, name, seq="SQ010", shot="SH0100", mtype="Plate", media="main", n=2, version=1):
        files = self._mkfiles(name, n)
        return IngestItem(
            key=IngestItem.compute_key(files),
            source_files=files, ext=".exr", source_name=name,
            sequence_code=seq, shot_code=shot, media_type=mtype, media_name=media,
            version=version, start_frame=1001, end_frame=1000 + n, frame_count=n,
        )


class TestLoadAndPreflight(ControllerTestBase):
    def test_load_sets_preview_default_from_config(self):
        c = self._ctrl()
        [plate] = c.load([self._scan("a", mtype="Plate")])
        self.assertTrue(plate.preview_default)
        self.assertTrue(plate.preview_wanted)

        [ref] = c.load([self._scan("b", media="r", mtype="Ref")])
        self.assertFalse(ref.preview_default)

    def test_preflight_clean_item_is_new(self):
        c = self._ctrl()
        c.load([self._scan("a")])
        c.run_preflight()
        it = c.items[0]
        self.assertTrue(it.preflight_done)
        self.assertEqual(it.status, Status.NEW)
        self.assertTrue(it.hashes)
        self.assertTrue(it.metadata_verified["colorspace"])

    def test_preflight_marks_slot_conflict(self):
        c = self._ctrl()
        c.load([self._scan("a")])
        it = c.items[0]
        dest = str(self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", 1))
        self.nas.slot_override[dest] = ("conflict", "v001 exists with different content")
        c.run_preflight()
        self.assertEqual(it.status, Status.CONFLICT)
        self.assertTrue(any(i.kind == IssueKind.DEST_EXISTS_DIFF for i in it.blocking_issues))

    def test_slot_already_is_already_ingested(self):
        c = self._ctrl()
        c.load([self._scan("a")])
        it = c.items[0]
        dest = str(self.nas.get_dest_dir("SHW", "SQ010", "SH0100", "main", 1))
        self.nas.slot_override[dest] = ("already", "v001 already holds this content")
        c.run_preflight()
        self.assertEqual(it.status, Status.ALREADY_INGESTED)
        # ... and there is still a way forward
        kinds = {i.kind for i in it.issues}
        self.assertIn(IssueKind.ALREADY_IN_SLOT, kinds)

    def test_same_content_different_shot_is_a_warning_not_a_block(self):
        # the real-world case: ingested to SH0100, now delivering the same
        # frames to SH0200 -- must be ingestable, with a heads-up.
        c = self._ctrl()
        c.load([self._scan("a")])
        c.run_preflight()
        it = c.items[0]
        from square_core.ingest_ledger import LedgerRecord
        self.ledger.record([
            LedgerRecord(file_hash=h, hash_algo=c.hasher.algo, size=1, src_path=s,
                         dest_path=f"/nas/SH0100/{i}", batch_id="b",
                         ingested_at="2026-01-01T00:00:00Z", version=1,
                         shot="SH0100", media_name="main")
            for i, (s, h) in enumerate(it.hashes.items())
        ])
        c.set_field(it.key, "shot_code", "SH0200")     # deliver elsewhere
        self.assertEqual(it.status, Status.WARNING)
        self.assertTrue(it.ingestable)
        dup = next(i for i in it.issues if i.kind == IssueKind.DUPLICATE_CONTENT)
        c.resolve(it.key, dup.id, Action.IGNORE)
        self.assertIn(it.status, (Status.NEW, Status.NEW_VERSION, Status.READY))

    def test_needs_info_when_metadata_unreadable(self):
        c = IngestController(self.cfg, PROJECT, nas=self.nas, ledger=self.ledger,
                             recorder=self.recorder, proxy_generator=self.proxy,
                             extractor=FakeExtractor(found={"resolution": "1920x1080",
                                                            "width": 1920, "height": 1080}))
        c.subscribe(self.events.append)
        c.load([self._scan("a")])
        c.run_preflight()
        it = c.items[0]
        self.assertEqual(it.status, Status.NEEDS_INFO)
        c.set_field(it.key, "colorspace", "ACEScg")
        c.set_field(it.key, "fps", 24.0)
        self.assertEqual(it.status, Status.NEW)


class TestCrossItemInController(ControllerTestBase):
    def test_dest_collision_blocks_both(self):
        c = self._ctrl()
        a = self._scan("a", media="main")
        b = self._scan("b", media="main")   # same seq/shot/media -> same dest
        c.load([a, b])
        c.run_preflight()
        self.assertEqual(a.status, Status.CONFLICT)
        self.assertEqual(b.status, Status.CONFLICT)

    def test_resolving_one_with_skip_clears_the_other(self):
        c = self._ctrl()
        a = self._scan("a", media="main")
        b = self._scan("b", media="main")
        c.load([a, b])
        c.run_preflight()
        iss = next(i for i in a.issues if i.kind == IssueKind.DEST_COLLISION)
        c.resolve(a.key, iss.id, Action.SKIP)
        self.assertEqual(a.status, Status.SKIPPED)
        self.assertEqual(b.status, Status.NEW)   # collision gone

    def test_version_up_moves_off_the_collision(self):
        c = self._ctrl()
        a = self._scan("a", media="main")
        b = self._scan("b", media="main")
        c.load([a, b])
        c.run_preflight()
        self.nas.next_free[("SQ010", "SH0100", "main")] = 2
        iss = next(i for i in b.issues if i.kind == IssueKind.DEST_COLLISION)
        c.resolve(b.key, iss.id, Action.VERSION_UP)
        self.assertEqual(b.version, 2)
        self.assertEqual(b.status, Status.NEW_VERSION)
        self.assertEqual(a.status, Status.NEW)


class TestIngest(ControllerTestBase):
    def test_full_ingest_writes_files_ledger_and_kitsu(self):
        c = self._ctrl()
        c.load([self._scan("a", n=3)])
        c.run_preflight()
        res = c.run_ingest()
        self.assertEqual(res["done"], 1)
        it = c.items[0]
        self.assertEqual(it.status, Status.COMPLETED)
        self.assertTrue(Path(it.ingest_result["dest_dir"]).exists())
        self.assertEqual(len(it.ingest_result["files"]), 3)
        self.assertEqual(self.ledger.count(), 3)
        self.assertEqual(len(self.recorder.records), 1)
        # preview runs off the critical path -- Completed already, proxy trickles in
        self._drain_previews(c)
        self.assertEqual(len(self.recorder.previews), 1)   # Plate -> preview attached
        self.assertEqual(it.preview_state, "done")
        self.assertEqual(it.ingest_result["preview_id"], "prev-1")

    def test_dry_run_touches_nothing_persistent(self):
        c = self._ctrl()
        c.load([self._scan("a")])
        c.run_preflight()
        c.run_ingest(dry_run=True)
        it = c.items[0]
        self.assertEqual(self.ledger.count(), 0)
        self.assertFalse(it.ingested)
        self.assertEqual(it.status, Status.NEW)   # back to ingestable

    def test_conflicted_item_is_not_ingested(self):
        c = self._ctrl()
        c.load([self._scan("a"), self._scan("b", media="main")])   # a,b collide
        c.run_preflight()
        res = c.run_ingest()
        self.assertEqual(res["done"], 0)

    def test_ingest_selected_only(self):
        c = self._ctrl()
        a = self._scan("a", shot="SH0100")
        b = self._scan("b", shot="SH0200", media="bg")
        c.load([a, b])
        c.run_preflight()
        res = c.run_ingest(keys=[a.key])
        self.assertEqual(res["done"], 1)
        self.assertEqual(a.status, Status.COMPLETED)
        self.assertEqual(b.status, Status.NEW)

    def test_preview_default_follows_media_type(self):
        c = self._ctrl()
        [it] = c.load([self._scan("a", mtype="")])       # untagged -> no preview default
        c.run_preflight()
        self.assertFalse(it.preview_wanted)
        c.set_field(it.key, "media_type", "Plate")        # Plate is preview-enabled
        self.assertTrue(it.preview_wanted)
        c.set_field(it.key, "media_type", "Audio")         # not
        self.assertFalse(it.preview_wanted)

    def test_user_toggle_stops_media_type_auto_follow(self):
        c = self._ctrl()
        [it] = c.load([self._scan("a", mtype="Plate")])
        c.run_preflight()
        c.set_preview(it.key, False)                       # explicit off
        c.set_field(it.key, "media_type", "Ref")
        c.set_field(it.key, "media_type", "Plate")
        self.assertFalse(it.preview_wanted)                # stays where the user put it

    def test_preview_off_posts_no_preview(self):
        c = self._ctrl()
        [it] = c.load([self._scan("a")])
        c.run_preflight()
        c.set_preview(it.key, False)
        c.run_ingest()
        self._drain_previews(c)
        self.assertEqual(self.recorder.previews, [])
        self.assertEqual(self.proxy.calls, 0)
        self.assertEqual(it.preview_state, "skipped")

    def test_stage_events_emitted_in_order(self):
        c = self._ctrl()
        c.load([self._scan("a")])
        c.run_preflight()
        self.events.clear()
        c.run_ingest()
        self._drain_previews(c)
        stages = [e.payload.get("stage") for e in self.events if e.kind == "item_stage"]
        self.assertEqual(stages[0], Stage.KITSU_SHOT.value)
        self.assertEqual(stages[-1], Stage.DONE.value)
        self.assertIn(Stage.COPYING.value, stages)
        # preview stages are NOT on the core path any more
        self.assertNotIn(Stage.PREVIEW_MAKE.value, stages)

    def test_ingest_finished_before_previews_finished(self):
        c = self._ctrl()
        c.load([self._scan("a")])
        c.run_preflight()
        self.events.clear()
        c.run_ingest()
        kinds = [e.kind for e in self.events]
        self.assertIn("ingest_finished", kinds)
        self._drain_previews(c)
        # previews_finished is emitted from a daemon thread; give it a beat
        import time
        for _ in range(50):
            if "previews_finished" in [e.kind for e in self.events]:
                break
            time.sleep(0.02)
        all_kinds = [e.kind for e in self.events]
        self.assertLess(all_kinds.index("ingest_finished"), all_kinds.index("previews_finished"))


class TestUndo(ControllerTestBase):
    def test_undo_a_field_edit(self):
        c = self._ctrl()
        [it] = c.load([self._scan("a", shot="SH0100")])
        c.run_preflight()
        c.set_field(it.key, "shot_code", "SH0999")
        self.assertEqual(c.get(it.key).shot_code, "SH0999")
        self.assertTrue(c.can_undo)
        c.undo()
        self.assertEqual(c.get(it.key).shot_code, "SH0100")
        self.assertFalse(c.can_undo)

    def test_undo_a_skip(self):
        c = self._ctrl()
        a = self._scan("a", media="main")
        b = self._scan("b", media="main")
        c.load([a, b])
        c.run_preflight()
        iss = next(i for i in a.issues if i.kind == IssueKind.DEST_COLLISION)
        c.resolve(a.key, iss.id, Action.SKIP)
        self.assertEqual(c.get(a.key).status, Status.SKIPPED)
        c.undo()
        self.assertEqual(c.get(a.key).status, Status.CONFLICT)
        self.assertEqual(c.get(b.key).status, Status.CONFLICT)

    def test_undo_label(self):
        c = self._ctrl()
        [it] = c.load([self._scan("a")])
        c.run_preflight()
        c.set_field(it.key, "media_name", "bg")
        self.assertIn("media_name", c.undo_label)

    def test_batch_resolve_is_one_undo_step(self):
        c = self._ctrl()
        a = self._scan("a", media="main")
        b = self._scan("b", media="main")
        c.load([a, b])
        c.run_preflight()
        c.resolve_many([a.key, b.key], IssueKind.DEST_COLLISION, Action.SKIP)
        self.assertEqual(c.get(a.key).status, Status.SKIPPED)
        self.assertEqual(c.get(b.key).status, Status.SKIPPED)
        c.undo()
        self.assertEqual(c.get(a.key).status, Status.CONFLICT)
        self.assertEqual(c.get(b.key).status, Status.CONFLICT)

    def test_undo_stack_survives_a_session_round_trip(self):
        import tempfile as _t
        from square_core.ingest_session import IngestSession
        c = self._ctrl()
        [it] = c.load([self._scan("a")])
        c.run_preflight()
        c.set_field(it.key, "media_name", "bg")
        path = IngestSession.capture(c).save(Path(_t.mkdtemp()) / "s")

        c2 = self._ctrl()
        IngestSession.load(path).restore_into(c2)
        self.assertTrue(c2.can_undo)
        c2.undo()
        self.assertEqual(c2.get(it.key).media_name, "main")


class TestSummary(ControllerTestBase):
    def test_summary_counts_by_status(self):
        c = self._ctrl()
        c.load([self._scan("a", shot="SH0100"), self._scan("b", shot="SH0200", media="bg")])
        c.run_preflight()
        s = c.summary()
        self.assertEqual(s.get("New"), 2)


if __name__ == "__main__":
    unittest.main()
