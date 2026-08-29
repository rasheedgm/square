import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from Qt import QtWidgets

from square_core.plate_scanner import IngestSequenceItem
from tools.ingest_tool.ui_main import IngestWorkerThread
import square_core.proxy_generator as pg_mod
import square_core.kitsu_client as kc_mod


def _make_item(name, ext, media_type, seq="SQ010", shot="SH0100"):
    tmp_file = Path(tempfile.mkdtemp()) / f"{name}{ext}"
    tmp_file.write_text("data")
    item = IngestSequenceItem(name, [str(tmp_file)], ext, is_video=False)
    item.sequence_code = seq
    item.shot_code = shot
    item.media_type = media_type
    item.media_name = name.upper()
    return item


class TestIngestWorkerDryRunIsolation(unittest.TestCase):
    """
    Confirmed bug: IngestWorkerThread always constructed KitsuClient(dry_run=False)
    and called connect()/get_or_create_sequence/... for real regardless of the
    Dry-Run checkbox -- so Dry-Run still created real sequences/shots/tasks/
    previews on the live Kitsu server. dry_run must now propagate all the way
    through, so Dry-Run touches gazu.set_host/log_in zero times.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())

    def _run_worker(self, dry_run, fake_gazu):
        item = _make_item("shot", ".exr", "Plate")
        worker = IngestWorkerThread(
            items_with_versions=[(item, 1)],
            project_data={"id": "1", "name": "T", "code": "TEST"},
            nas_root=str(self.tmp / "nas"), dry_run=dry_run,
            kitsu_host="http://localhost/api", kitsu_user="a", kitsu_pass="b",
            task_types=["Ingest"], transfer_mode="copy", copy_workers=1,
            preview_enabled_media_types=["Plate"],
        )
        with patch.dict("sys.modules", {"gazu": fake_gazu}):
            worker.run()
        return worker

    def test_dry_run_never_touches_live_gazu(self):
        fake_gazu = MagicMock()
        self._run_worker(dry_run=True, fake_gazu=fake_gazu)
        self.assertFalse(fake_gazu.set_host.called)
        self.assertFalse(fake_gazu.log_in.called)

    def test_live_mode_does_connect(self):
        fake_gazu = MagicMock()
        fake_gazu.shot.get_sequence_by_name.return_value = None
        fake_gazu.shot.new_sequence.return_value = {"id": "seq1", "name": "SQ010"}
        fake_gazu.shot.get_shot_by_name.return_value = None
        fake_gazu.shot.new_shot.return_value = {"id": "shot1", "name": "SH0100", "data": {}}
        fake_gazu.task.all_task_types.return_value = []
        fake_gazu.task.all_tasks_for_shot.return_value = []
        fake_gazu.task.new_task_type.return_value = {"id": "tt1", "name": "Ingest"}
        fake_gazu.task.new_task.return_value = {"id": "task1", "name": "Ingest"}
        fake_gazu.task.get_default_task_status.return_value = "todo"

        self._run_worker(dry_run=False, fake_gazu=fake_gazu)
        self.assertTrue(fake_gazu.set_host.called)
        self.assertTrue(fake_gazu.log_in.called)


class TestIngestWorkerTaskAndPreviewConfig(unittest.TestCase):
    """Task-type selection and per-media-type preview gating must actually take effect."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())

    def test_task_types_are_passed_through(self):
        item = _make_item("shot", ".exr", "Plate")
        captured = {}
        orig = kc_mod.KitsuClient.create_default_tasks

        def spy(self, shot, task_types=None):
            captured["task_types"] = task_types
            return orig(self, shot, task_types=task_types)

        worker = IngestWorkerThread(
            items_with_versions=[(item, 1)],
            project_data={"id": "1", "name": "T", "code": "TEST"},
            nas_root=str(self.tmp / "nas"), dry_run=True,
            kitsu_host="x", kitsu_user="a", kitsu_pass="b",
            task_types=["Ingest", "Comp", "Roto"], transfer_mode="copy", copy_workers=1,
            preview_enabled_media_types=["Plate"],
        )
        with patch.object(kc_mod.KitsuClient, "create_default_tasks", spy):
            worker.run()

        self.assertEqual(captured["task_types"], ["Ingest", "Comp", "Roto"])

    def test_preview_only_generated_for_enabled_media_types(self):
        plate_item = _make_item("shot", ".exr", "Plate")
        lut_item = _make_item("grade", ".cube", "LUT")

        proxy_calls = []
        comment_calls = []
        orig_generate = pg_mod.ProxyGenerator.generate_proxy
        orig_add_comment = kc_mod.KitsuClient.add_version_comment
        orig_upload = kc_mod.KitsuClient.upload_preview_proxy

        def tracked_generate(self, item, dest_name=None):
            proxy_calls.append(item.name)
            return orig_generate(self, item, dest_name)

        def tracked_add_comment(self, task, comment):
            comment_calls.append(("text_only", comment))
            return orig_add_comment(self, task, comment)

        def tracked_upload(self, task, path, comment="x"):
            comment_calls.append(("with_preview", comment))
            return orig_upload(self, task, path, comment)

        worker = IngestWorkerThread(
            items_with_versions=[(plate_item, 1), (lut_item, 1)],
            project_data={"id": "1", "name": "T", "code": "TEST"},
            nas_root=str(self.tmp / "nas"), dry_run=True,
            kitsu_host="x", kitsu_user="a", kitsu_pass="b",
            task_types=["Ingest"], transfer_mode="copy", copy_workers=1,
            preview_enabled_media_types=["Plate"],  # LUT deliberately excluded
        )
        with patch.object(pg_mod.ProxyGenerator, "generate_proxy", tracked_generate), \
             patch.object(kc_mod.KitsuClient, "add_version_comment", tracked_add_comment), \
             patch.object(kc_mod.KitsuClient, "upload_preview_proxy", tracked_upload):
            worker.run()

        self.assertEqual(proxy_calls, ["shot"])
        modes = [m for m, _ in comment_calls]
        self.assertIn("with_preview", modes)
        self.assertIn("text_only", modes)

    def test_version_comment_includes_nas_path_and_resolution(self):
        item = _make_item("shot", ".exr", "Plate")
        item.resolution = "3840x2160"
        comment = kc_mod.KitsuClient.build_version_comment(
            item, 2, Path("/nas/proj/SQ010/SH0100"), transfer_mode="copy", checksum="deadbeef"
        )
        self.assertIn("3840x2160", comment)
        self.assertIn("/nas/proj/SQ010/SH0100", comment)
        self.assertIn("v002", comment)
        self.assertIn("deadbeef", comment)


class TestIngestWorkerPerItemFailureIsolation(unittest.TestCase):
    """One item failing (network hiccup, bad file) must not abort the rest of the batch."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.tmp = Path(tempfile.mkdtemp())

    def test_one_failing_item_does_not_block_others(self):
        good_item = _make_item("good", ".exr", "Plate")
        bad_item = _make_item("bad", ".exr", "Plate")

        # Monkeypatch get_or_create_shot to raise for the first item only.
        call_count = {"n": 0}
        orig_get_or_create_shot = kc_mod.KitsuClient.get_or_create_shot

        def flaky_get_or_create_shot(self, project, sequence, shot_name, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated Kitsu failure for first item")
            return orig_get_or_create_shot(self, project, sequence, shot_name, **kwargs)

        worker = IngestWorkerThread(
            items_with_versions=[(bad_item, 1), (good_item, 1)],
            project_data={"id": "1", "name": "T", "code": "TEST"},
            nas_root=str(self.tmp / "nas"), dry_run=True,
            kitsu_host="x", kitsu_user="a", kitsu_pass="b",
            task_types=["Ingest"], transfer_mode="copy", copy_workers=1,
            preview_enabled_media_types=["Plate"],
        )
        results = []
        worker.finished_signal.connect(lambda ok, msg, s: results.append((ok, msg, s)))
        with patch.object(kc_mod.KitsuClient, "get_or_create_shot", flaky_get_or_create_shot):
            worker.run()

        ok, msg, summary = results[0]
        self.assertTrue(ok)  # the batch as a whole still finishes
        statuses = {i["source_name"]: i["status"] for i in summary["items"]}
        self.assertTrue(statuses["bad"].startswith("Error"))
        self.assertEqual(statuses["good"], "Dry-Run Simulated")


if __name__ == "__main__":
    unittest.main()
