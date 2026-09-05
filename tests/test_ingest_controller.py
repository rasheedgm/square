"""tools.ingest_tool.core.controller.IngestController -- ported onto the
pipeline core (PipelineContext + services.media.publish + services.breakdown)."""

import tempfile
import time
import unittest
from unittest.mock import patch
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
        if comment:
            self.comments.append(comment)

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


def _controller(pctx, ledger_dir, **kw):
    ledger = IngestLedger(str(Path(ledger_dir) / "ledger.db"))
    return IngestController(pctx, ledger=ledger, task_types=["Comp"], **kw)


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


def _make_video_item(td, name="clip", sequence="SQ010", shot="SH0100",
                     media_type="Plate", **overrides):
    p = Path(td) / f"{name}.mov"
    p.write_bytes(b"not-really-a-mov" * 20)
    item = IngestItem(key=IngestItem.compute_key([str(p)]), source_files=[str(p)], ext=".mov",
                      is_video=True, sequence_code=sequence, shot_code=shot, media_type=media_type,
                      media_name=name, resolution="1920x1080", fps=24.0, colorspace="ACEScg")
    item.metadata_verified = {"resolution": True, "fps": True, "colorspace": True}
    for k, v in overrides.items():
        setattr(item, k, v)
    return item


def _fake_converter(n_frames=3, out_dirs=None):
    """Stands in for square_core.media.convert.video_to_exr_sequence -- writes
    N dummy .exr frames instead of actually invoking ffmpeg, and (optionally)
    records every out_dir it was given so a test can check cleanup after."""
    def _convert(video_path, out_dir, *, start_frame=1001):
        if out_dirs is not None:
            out_dirs.append(out_dir)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(video_path).stem
        written = []
        for i in range(n_frames):
            f = out_dir / f"{stem}.{start_frame + i:04d}.exr"
            f.write_bytes(f"frame{i}".encode() * 20)
            written.append(str(f))
        return sorted(written)
    return _convert


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

    def test_duplicate_content_does_not_offer_overwrite(self):
        # There's nothing local to overwrite -- the ledger match is against
        # some OTHER destination, not this row's own (empty) slot.
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg", shot="SH0100")])
            controller.run_preflight()
            controller.run_ingest()

            with tempfile.TemporaryDirectory() as src2:
                item2 = _make_item(src2, name="bg", shot="SH0200")
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                dup = next(i for i in it2.issues if i.kind.value == "duplicate-content")
                self.assertNotIn(Action.OVERWRITE, dup.actions)
                self.assertIn(Action.IGNORE, dup.actions)

    def test_version_up_on_duplicate_content_stays_resolved(self):
        # Confirmed bug: Version Up bumped the version but the row went
        # straight back to Warning -- the duplicate-content resolution was
        # unconditionally dropped as if it were a slot-scoped issue, but the
        # ledger hash match is unaffected by which version number is picked.
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg", shot="SH0100")])
            controller.run_preflight()
            controller.run_ingest()

            with tempfile.TemporaryDirectory() as src2:
                item2 = _make_item(src2, name="bg", shot="SH0200")
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                dup = next(i for i in it2.issues if i.kind.value == "duplicate-content")
                controller.resolve(it2.key, dup.id, Action.VERSION_UP)

                self.assertEqual(it2.version, 2)
                self.assertNotEqual(it2.status.value, "Warning")
                self.assertTrue(it2.ingestable)

    def test_ignore_on_duplicate_content_makes_it_ingestable_without_touching_version(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg", shot="SH0100")])
            controller.run_preflight()
            controller.run_ingest()

            with tempfile.TemporaryDirectory() as src2:
                item2 = _make_item(src2, name="bg", shot="SH0200")
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                dup = next(i for i in it2.issues if i.kind.value == "duplicate-content")
                controller.resolve(it2.key, dup.id, Action.IGNORE)

                self.assertEqual(it2.version, 1)   # untouched
                self.assertTrue(it2.ingestable)

    def test_undo_after_resolving_a_blocking_conflict_restores_the_original_unresolved_state(self):
        # Reported bug: after resolving a row that was NOT ingestable
        # (Ingest goes ready/enabled), Undo didn't put it back -- ingestable
        # stayed true instead of reverting with the row's other fields.
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="bg")])
            controller.run_preflight()
            controller.run_ingest()

            with tempfile.TemporaryDirectory() as src2:
                item2 = _make_item(src2, name="bg")   # different content, same slot -> BLOCKING conflict
                for f in item2.source_files:
                    Path(f).write_bytes(b"different-bytes" * 5)
                item2.hashes = {}
                _load(controller, [item2])
                controller.run_preflight()
                it2 = controller.get(item2.key)
                self.assertFalse(it2.ingestable)
                self.assertEqual(it2.status.value, "Conflict")

                conflict = next(i for i in it2.issues if i.kind.value == "dest-exists-diff")
                controller.resolve(it2.key, conflict.id, Action.VERSION_UP)
                self.assertTrue(controller.get(item2.key).ingestable)
                self.assertEqual(controller.get(item2.key).version, 2)

                self.assertTrue(controller.undo())
                restored = controller.get(item2.key)
                self.assertFalse(restored.ingestable)
                self.assertEqual(restored.status.value, "Conflict")
                self.assertEqual(restored.version, 1)

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


class TestVideoToExrConversion(unittest.TestCase):
    """Feature: a delivered single video file can optionally be decoded to an
    EXR frame sequence before ingesting, instead of the video going in as-is."""

    def test_unconverted_video_ingests_as_a_single_file(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work, converter=_fake_converter())
            _load(controller, [_make_video_item(src)])
            controller.run_preflight()
            controller.run_ingest()
            item = controller.items[0]
            self.assertTrue(item.ingested)
            self.assertEqual(len(item.ingest_result["files"]), 1)
            self.assertTrue(item.ingest_result["files"][0].endswith(".mov"))

    def test_convert_to_exr_ingests_the_decoded_frame_sequence(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            out_dirs = []
            controller = _controller(pctx, work, converter=_fake_converter(n_frames=4, out_dirs=out_dirs))
            _load(controller, [_make_video_item(src, convert_to_exr=True, start_frame=1001)])
            controller.run_preflight()
            result = controller.run_ingest()

            self.assertEqual(result["done"], 1)
            item = controller.items[0]
            self.assertTrue(item.ingested)
            self.assertEqual(len(item.ingest_result["files"]), 4)
            self.assertTrue(all(f.endswith(".exr") for f in item.ingest_result["files"]))
            for f in (1001, 1002, 1003, 1004):
                self.assertTrue((Path(item.dest_dir) / f"clip.{f:04d}.exr").exists())

            # the scratch directory used to hold the decoded frames is
            # cleaned up once the (disabled-preview, so synchronous) publish
            # is done with it -- nothing left behind on disk.
            self.assertEqual(len(out_dirs), 1)
            self.assertFalse(Path(out_dirs[0]).exists())

    def test_conversion_failure_is_reported_as_an_ingest_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)

            def _boom(video_path, out_dir, *, start_frame=1001):
                raise RuntimeError("ffmpeg exploded")

            controller = _controller(pctx, work, converter=_boom)
            _load(controller, [_make_video_item(src, convert_to_exr=True)])
            controller.run_preflight()
            result = controller.run_ingest()

            self.assertEqual(result["failed"], 1)
            item = controller.items[0]
            self.assertFalse(item.ingested)
            self.assertIn("ffmpeg exploded", item.ingest_error)

    def test_dry_run_shows_the_would_be_exr_destination_without_converting(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            calls = []
            controller = _controller(pctx, work, converter=lambda *a, **k: calls.append(1) or [])
            _load(controller, [_make_video_item(src, convert_to_exr=True, start_frame=1001)])
            controller.run_preflight()
            controller.run_ingest(dry_run=True)

            item = controller.items[0]
            self.assertFalse(calls)   # the real (fake) converter was never invoked
            self.assertTrue(item.ingest_result["files"][0].endswith("clip.1001.exr"))


class TestRenameBatch(unittest.TestCase):
    """Feature: batch-rename a field across rows using a token template,
    e.g. {sequence}_{shot}_{media_name} -- the port had dropped this."""

    def test_template_tokens_are_substituted_per_row(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, name="a", sequence="SQ010", shot="SH0100", version=2)
            b = _make_item(src, name="b", sequence="SQ020", shot="SH0200", version=3)
            _load(controller, [a, b])

            n = controller.rename_batch([a.key, b.key], "media_name", "{sequence}_{shot}_{version}")
            self.assertEqual(n, 2)
            self.assertEqual(controller.get(a.key).media_name, "SQ010_SH0100_v002")
            self.assertEqual(controller.get(b.key).media_name, "SQ020_SH0200_v003")

    def test_renaming_a_different_field_writes_there_not_media_name(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, sequence="SQ010", shot="SH0100")
            _load(controller, [a])

            controller.rename_batch([a.key], "shot", "SH{shot}_RENAMED")
            item = controller.get(a.key)
            self.assertEqual(item.shot_code, "SHSH0100_RENAMED")
            self.assertEqual(item.media_name, "bg")   # untouched (default name from _make_item)

    def test_source_token_is_the_scanner_group_name(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, name="a")
            a.source_name = "scanned_orig"
            _load(controller, [a])

            controller.rename_batch([a.key], "media_name", "{source}_renamed")
            self.assertEqual(controller.get(a.key).media_name, "scanned_orig_renamed")

    def test_blank_template_is_a_noop(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src)
            _load(controller, [a])
            before = controller.get(a.key).media_name

            label_before = controller.undo_label
            n = controller.rename_batch([a.key], "media_name", "   ")
            self.assertEqual(n, 0)
            self.assertEqual(controller.get(a.key).media_name, before)
            self.assertEqual(controller.undo_label, label_before)   # a no-op must not pollute the undo stack

    def test_unknown_field_raises(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src)
            _load(controller, [a])
            with self.assertRaises(ValueError):
                controller.rename_batch([a.key], "not_a_field", "{sequence}")

    def test_rename_is_one_undo_step_for_the_whole_batch(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, name="a")
            b = _make_item(src, name="b")
            _load(controller, [a, b])
            label_before = controller.undo_label

            controller.rename_batch([a.key, b.key], "media_name", "renamed_{media_name}")
            self.assertEqual(controller.get(a.key).media_name, "renamed_a")
            self.assertEqual(controller.get(b.key).media_name, "renamed_b")
            self.assertNotEqual(controller.undo_label, label_before)   # exactly one new step

            self.assertTrue(controller.undo())
            self.assertEqual(controller.get(a.key).media_name, "a")
            self.assertEqual(controller.get(b.key).media_name, "b")
            self.assertEqual(controller.undo_label, label_before)   # one click undid both rows


class TestRenameCells(unittest.TestCase):
    """rename_cells is the general primitive behind rename_batch: it targets
    exact (key, attr) pairs, so a selection spanning different COLUMNS (e.g.
    some rows' Colorspace cells and other rows' FPS cells at once) can be
    set in a single pass with one undo step."""

    def test_different_cells_can_target_different_fields_in_one_call(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, name="a")
            b = _make_item(src, name="b")
            _load(controller, [a, b])

            n = controller.rename_cells(
                [(a.key, "colorspace"), (b.key, "fps")], "24")
            self.assertEqual(n, 2)
            self.assertEqual(controller.get(a.key).colorspace, "24")
            self.assertEqual(controller.get(b.key).fps, 24.0)

    def test_fps_and_version_are_coerced_to_numbers(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src)
            _load(controller, [a])

            controller.rename_cells([(a.key, "fps")], "23.976")
            self.assertEqual(controller.get(a.key).fps, 23.976)
            controller.rename_cells([(a.key, "version")], "7")
            self.assertEqual(controller.get(a.key).version, 7)

    def test_numeric_field_set_marks_metadata_verified(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src)
            a.metadata_verified = {}
            _load(controller, [a])

            controller.rename_cells([(a.key, "colorspace")], "ACEScg")
            self.assertTrue(controller.get(a.key).metadata_verified["colorspace"])

    def test_an_uncoercible_numeric_value_skips_that_cell_without_crashing(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src)
            _load(controller, [a])
            before = controller.get(a.key).fps

            n = controller.rename_cells([(a.key, "fps")], "not-a-number")
            self.assertEqual(n, 0)
            self.assertEqual(controller.get(a.key).fps, before)

    def test_resolve_rename_template_does_not_mutate_the_item(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, sequence="SQ010", shot="SH0100")
            _load(controller, [a])

            resolved = controller.resolve_rename_template(a, "{sequence}_{shot}")
            self.assertEqual(resolved, "SQ010_SH0100")
            self.assertEqual(controller.get(a.key).media_name, "bg")   # unchanged


class TestRenameCaseModifiers(unittest.TestCase):
    """{token:upper} / {token:lower} / {token:title} / {token:capitalize} --
    a case transform applied to that one token's resolved value."""

    def test_upper_and_lower_modifiers(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, sequence="sq010", shot="SH0100")
            _load(controller, [a])

            self.assertEqual(
                controller.resolve_rename_template(a, "{sequence:upper}_{shot:lower}"),
                "SQ010_sh0100",
            )

    def test_title_and_capitalize_modifiers(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, name="background plate")
            _load(controller, [a])

            self.assertEqual(
                controller.resolve_rename_template(a, "{media_name:title}"),
                "Background Plate",
            )
            self.assertEqual(
                controller.resolve_rename_template(a, "{media_name:capitalize}"),
                "Background plate",
            )

    def test_plain_token_without_a_modifier_is_unaffected(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="SH0100")
            _load(controller, [a])
            self.assertEqual(controller.resolve_rename_template(a, "{shot}"), "SH0100")

    def test_unknown_modifier_falls_back_to_the_plain_value(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="SH0100")
            _load(controller, [a])
            self.assertEqual(controller.resolve_rename_template(a, "{shot:nonsense}"), "SH0100")

    def test_unknown_token_is_left_literal(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="SH0100")
            _load(controller, [a])
            self.assertEqual(
                controller.resolve_rename_template(a, "{shot}_{not_a_real_token}"),
                "SH0100_{not_a_real_token}",
            )

    def test_case_modifier_applies_when_actually_renaming(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="SH0100")
            _load(controller, [a])

            controller.rename_batch([a.key], "media_name", "{shot:upper}_final")
            self.assertEqual(controller.get(a.key).media_name, "SH0100_final")


class TestIngestPreviewOutcome(unittest.TestCase):
    """The review proxy runs off the critical path on its own pool; when it
    lands (or fails), the outcome has to make it back onto the row -- it was
    stuck on 'pending' forever otherwise."""

    def _load_wanting_preview(self, controller, src):
        added = controller.load([_make_item(src, name="bg")])
        for it in added:
            it.preview_wanted = True   # _load() would turn it off; we want it on
        return added[0]

    def _wait(self, item, timeout=5):
        end = time.time() + timeout
        while item.preview_state in ("pending", "running") and time.time() < end:
            time.sleep(0.02)

    def test_a_finished_proxy_flips_the_row_to_done_with_its_id(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            it = self._load_wanting_preview(controller, src)
            controller.run_preflight()

            fake = type("P", (), {"id": "prev-xyz"})()
            with patch("square_core.services.media._review_proxy", return_value=fake):
                controller.run_ingest()
                self._wait(it)

            self.assertEqual(it.preview_state, "done")
            self.assertEqual(it.ingest_result["preview_id"], "prev-xyz")

    def test_a_failing_proxy_flips_the_row_to_failed(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            it = self._load_wanting_preview(controller, src)
            controller.run_preflight()

            with patch("square_core.services.media._review_proxy",
                       side_effect=RuntimeError("ffmpeg exploded")):
                controller.run_ingest()
                self._wait(it)

            self.assertEqual(it.preview_state, "failed")
            self.assertTrue(it.ingested)   # ingest itself still succeeded


class TestResumePendingPreviews(unittest.TestCase):
    """A session saved while a review proxy was still encoding/uploading (or
    after it failed) re-attempts it on resume -- the Kitsu version already
    exists, so this only re-runs the proxy."""

    def _ingested_item_mid_preview(self, controller, src, preview_state="pending"):
        _load(controller, [_make_item(src, name="bg")])
        controller.run_preflight()
        controller.run_ingest()
        it = controller.items[0]
        it.preview_wanted = True
        it.preview_state = preview_state
        return it

    def _wait(self, item, timeout=5):
        end = time.time() + timeout
        while item.preview_state in ("pending", "running") and time.time() < end:
            time.sleep(0.02)

    def test_pending_preview_is_re_run_and_marked_done(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            it = self._ingested_item_mid_preview(controller, src)

            fake_preview = type("P", (), {"id": "prev-1"})()
            with patch("square_core.services.media.make_review_proxy_for",
                       return_value=fake_preview) as mk:
                controller.run_pending_previews()
                self._wait(it)

            mk.assert_called_once()
            self.assertEqual(it.preview_state, "done")
            self.assertEqual(it.ingest_result["preview_id"], "prev-1")

    def test_a_failed_preview_is_retried_and_a_new_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            it = self._ingested_item_mid_preview(controller, src, preview_state="failed")

            with patch("square_core.services.media.make_review_proxy_for",
                       side_effect=RuntimeError("ffmpeg still broken")):
                controller.run_pending_previews()
                self._wait(it)

            self.assertEqual(it.preview_state, "failed")

    def test_a_done_or_never_wanted_preview_is_left_alone(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src, name="a"), _make_item(src, name="b")])
            controller.run_preflight()
            controller.run_ingest()
            controller.items[0].preview_state = "done"
            controller.items[0].preview_wanted = True
            controller.items[1].preview_state = ""      # never wanted one

            with patch("square_core.services.media.make_review_proxy_for") as mk:
                controller.run_pending_previews()
                time.sleep(0.1)
            mk.assert_not_called()

    def test_a_row_that_never_ingested_is_not_requeued(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            _load(controller, [_make_item(src)])
            controller.run_preflight()   # NOT ingested
            controller.items[0].preview_wanted = True
            controller.items[0].preview_state = "pending"

            with patch("square_core.services.media.make_review_proxy_for") as mk:
                controller.run_pending_previews()
                time.sleep(0.1)
            mk.assert_not_called()


class TestRenameCurrentAndOriginal(unittest.TestCase):
    """
    {current} and {original} are per-CELL, not per-item: they resolve
    against whichever field a cell actually is, so the same template works
    no matter which column it's applied to.
      {current}  -- that cell's own value right now
      {original} -- that cell's value the moment this row first loaded
    (distinct from {source}, the scanner's fixed file/folder group name.)
    """

    def test_original_and_current_agree_before_any_edit(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="Fgt10")
            _load(controller, [a])

            self.assertEqual(controller.resolve_rename_template(a, "{original}", "shot_code"), "Fgt10")
            self.assertEqual(controller.resolve_rename_template(a, "{current}", "shot_code"), "Fgt10")

    def test_original_stays_fixed_after_an_edit_while_current_follows_it(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="Fgt10")
            _load(controller, [a])

            controller.set_field(a.key, "shot_code", "Fgt10_edited")
            edited = controller.get(a.key)
            self.assertEqual(controller.resolve_rename_template(edited, "{original}", "shot_code"), "Fgt10")
            self.assertEqual(controller.resolve_rename_template(edited, "{current}", "shot_code"), "Fgt10_edited")

    def test_original_and_current_follow_whichever_field_the_cell_is_in(self):
        # The exact case that motivated this: renaming the Shot cell (loaded
        # as "Fgt10") with "{original}_{current}" should read that cell's
        # OWN field, not some fixed item-level concept.
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="Fgt10", name="bg")
            _load(controller, [a])

            self.assertEqual(
                controller.resolve_rename_template(a, "{original}_{current}", "shot_code"),
                "Fgt10_Fgt10",
            )
            self.assertEqual(
                controller.resolve_rename_template(a, "{original}_{current}", "media_name"),
                "bg_bg",
            )

    def test_current_and_original_via_rename_cells_target_their_own_attr(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="Fgt10")
            _load(controller, [a])

            controller.rename_cells([(a.key, "shot_code")], "{current}_v2")
            self.assertEqual(controller.get(a.key).shot_code, "Fgt10_v2")

    def test_original_survives_being_overwritten_more_than_once(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="Fgt10")
            _load(controller, [a])

            controller.rename_cells([(a.key, "shot_code")], "{current}_v2")
            controller.rename_cells([(a.key, "shot_code")], "{current}_v3")
            item = controller.get(a.key)
            self.assertEqual(item.shot_code, "Fgt10_v2_v3")
            self.assertEqual(controller.resolve_rename_template(item, "{original}", "shot_code"), "Fgt10")

    def test_without_an_attr_current_and_original_resolve_empty_not_crash(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as work:
            pctx = _pctx(work)
            controller = _controller(pctx, work)
            a = _make_item(src, shot="Fgt10")
            _load(controller, [a])
            self.assertEqual(controller.resolve_rename_template(a, "[{current}][{original}]"), "[][]")


if __name__ == "__main__":
    unittest.main()
