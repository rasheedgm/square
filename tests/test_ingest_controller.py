"""tools.ingest_tool.core.controller.IngestController -- ported onto the
pipeline core (PipelineContext + services.media.publish + services.breakdown)."""

import tempfile
import unittest
from pathlib import Path

from square_core.context import PipelineContext
from square_core.config.pipeline import PipelineConfig
from square_core.kitsu import OfflineApi
from square_core.services import projects
from square_core.services.projects import ProjectSpec

from tools.ingest_tool.core.controller import IngestController
from tools.ingest_tool.core.item import IngestItem, Action, Stage
from tools.ingest_tool.core.ledger import IngestLedger


class _TrackingKitsu(OfflineApi):
    """OfflineApi that actually remembers shots/output files, so the
    controller's read-only slot-conflict lookup (`shots()` + `output_files()`)
    has something real to find."""

    def __init__(self):
        self._shots = {}          # code -> Shot
        self.outputs = []         # recorded Output dicts
        self.statuses = []
        self.comments = []
        self._rev = {}

    def ensure_shot(self, project, sequence, code, **kw):
        from square_core.model import Shot
        existing = self._shots.get(code)
        if existing:
            return existing
        shot = Shot(id=f"shot-{code}", code=code,
                   frame_in=kw.get("frame_in") or 0, frame_out=kw.get("frame_out") or 0)
        self._shots[code] = shot
        return shot

    def shots(self, project):
        return list(self._shots.values())

    def next_output_revision(self, entity, output_type_name, task=None, *, name="main"):
        key = (getattr(entity, "id", entity), output_type_name, name)
        return self._rev.get(key, 0) + 1

    def output_files(self, entity, *, output_type_name=None):
        from square_core.model import Output
        out = []
        for rec in self.outputs:
            if rec["entity_id"] != getattr(entity, "id", entity):
                continue
            if output_type_name and rec["output_type"] != output_type_name:
                continue
            out.append(Output(output_type=rec["output_type"], revision=rec["revision"],
                              path=rec["path"], name=rec["name"], data=rec["data"]))
        return out

    def record_output_file(self, entity, output_type_name, task, *, revision, path,
                           representation="", name="main", comment="", data=None):
        from square_core.model import Output
        key = (getattr(entity, "id", entity), output_type_name, name)
        self._rev[key] = revision
        self.outputs.append({"entity_id": getattr(entity, "id", entity),
                             "output_type": output_type_name, "revision": revision,
                             "path": path, "name": name, "data": data or {}})
        return Output(output_type=output_type_name, revision=revision, path=path,
                      representation=representation, name=name, data=data or {})

    def set_status(self, task, status_name, *, comment="", author=None):
        self.statuses.append(status_name)

    def comment(self, task, text, *, status=None):
        self.comments.append(text)
        from square_core.model import Comment
        return Comment(text=text)


def _pctx(nas, kitsu=None):
    cfg = PipelineConfig(nas_roots={"default": nas})
    api = kitsu or _TrackingKitsu()
    ctx = PipelineContext(config=cfg, kitsu=api, user=api.current_user())
    projects.create(ctx, ProjectSpec(code="ABC", fps=24.0))
    return ctx.project("ABC")


def _controller(pctx, ledger_dir):
    ledger = IngestLedger(str(Path(ledger_dir) / "ledger.db"))
    return IngestController(pctx, ledger=ledger, task_types=["Comp"])


def _make_item(td, name="bg", n_frames=3, sequence="SQ010", shot="SH0100",
              media_type="Plate", **overrides):
    files = []
    for f in range(1001, 1001 + n_frames):
        p = Path(td) / f"{name}.{f}.exr"
        p.write_bytes(f"frame{f}".encode() * 20)
        files.append(str(p))
    item = IngestItem(key=IngestItem.compute_key(files), source_files=files, ext=".exr",
                      sequence_code=sequence, shot_code=shot, media_type=media_type,
                      media_name=name, resolution="1920x1080", fps=24.0, colorspace="ACEScg")
    item.metadata_verified = {"resolution": True, "fps": True, "colorspace": True}
    for k, v in overrides.items():
        setattr(item, k, v)
    return item


def _load(controller, items):
    """load() + turn preview off for every row -- the fake frames these tests
    write aren't real images, so ffmpeg can't decode them; tests that care
    about the preview path opt in explicitly."""
    added = controller.load(items)
    for it in added:
        controller.set_preview(it.key, False)
    return added


class TestPreflight(unittest.TestCase):
    def test_new_item_is_ready_after_preflight(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src)])
            controller.run_preflight()
            item = controller.items[0]
            self.assertTrue(item.preflight_done)
            self.assertIn(item.status.value, ("New", "Ready"))
            self.assertTrue(item.dest_dir)
            self.assertIn("plates", item.dest_dir.replace("\\", "/"))

    def test_missing_required_field_needs_info(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, shot_code="")])
            controller.run_preflight()
            self.assertEqual(controller.items[0].status.value, "Needs Info")

    def test_known_media_types_uses_delivery_source_filter(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            self.assertIn("Plate", controller.known_media_types)
            self.assertNotIn("CompRender", controller.known_media_types)   # source=publish


class TestIngest(unittest.TestCase):
    def test_ingest_creates_shot_task_and_output_record(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src)])
            controller.run_preflight()
            result = controller.run_ingest()

            self.assertEqual(result["done"], 1)
            item = controller.items[0]
            self.assertTrue(item.ingested)
            self.assertEqual(item.status.value, "Completed")
            for f in (1001, 1002, 1003):
                self.assertTrue((Path(item.dest_dir) / f"bg.{f}.exr").exists())

            outs = pctx.kitsu.outputs
            self.assertEqual(len(outs), 1)
            self.assertEqual(outs[0]["output_type"], "Plate")
            self.assertEqual(pctx.kitsu.statuses[-1], "Done")

    def test_ledger_records_the_ingested_files(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src)])
            controller.run_preflight()
            controller.run_ingest()
            self.assertEqual(controller.ledger.count(), 3)

    def test_second_delivery_of_identical_content_is_flagged_duplicate(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg")])
            controller.run_preflight()
            controller.run_ingest()

            # a second, byte-identical delivery of the same content to the
            # exact same slot (shot/name/version) -- "nothing to do", i.e.
            # already-in-slot, NOT duplicate-content (that's reserved for a
            # ledger hash match somewhere OTHER than this exact slot)
            with tempfile.TemporaryDirectory() as src2:
                item2 = _make_item(src2, name="bg")
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                self.assertEqual(it2.status.value, "Already Ingested")
                kinds = {i.kind.value for i in it2.issues}
                self.assertIn("already-in-slot", kinds)
                self.assertNotIn("duplicate-content", kinds)

    def test_identical_content_delivered_to_a_different_shot_is_duplicate_content(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg", shot="SH0100")])
            controller.run_preflight()
            controller.run_ingest()

            with tempfile.TemporaryDirectory() as src2:
                item2 = _make_item(src2, name="bg", shot="SH0200")   # same bytes, different shot
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                kinds = {i.kind.value for i in it2.issues}
                self.assertIn("duplicate-content", kinds)

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src)])
            controller.run_preflight()
            controller.run_ingest(dry_run=True)
            item = controller.items[0]
            self.assertFalse(item.ingested)
            self.assertEqual(len(pctx.kitsu.outputs), 0)
            self.assertFalse(Path(item.dest_dir).exists())

    def test_version_up_resolves_a_kitsu_side_conflict(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg")])
            controller.run_preflight()
            controller.run_ingest()

            with tempfile.TemporaryDirectory() as src2:
                # different content, same slot (version 1) -> a real conflict
                item2 = _make_item(src2, name="bg")
                item2.source_files[0] = item2.source_files[0]  # keep, but content differs by tempdir path/bytes below
                for f in item2.source_files:
                    Path(f).write_bytes(b"different-bytes" * 5)
                item2.hashes = {}
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                conflict = next(i for i in it2.issues if i.kind.value == "dest-exists-diff")
                controller.resolve(it2.key, conflict.id, Action.VERSION_UP)
                self.assertEqual(it2.version, 2)
                result = controller.run_ingest()
                self.assertEqual(result["done"], 1)


class TestEvents(unittest.TestCase):
    def test_stage_events_fire_in_order(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src)])
            controller.run_preflight()
            stages = []
            controller.subscribe(lambda ev: stages.append(ev.kind))
            controller.run_ingest()
            self.assertIn("ingest_started", stages)
            self.assertIn("item_stage", stages)
            self.assertIn("ingest_finished", stages)
            self.assertLess(stages.index("ingest_started"), stages.index("ingest_finished"))


if __name__ == "__main__":
    unittest.main()
