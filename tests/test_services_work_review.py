"""services.work / services.review with the offline Kitsu + a fake that records
what was published."""

import tempfile
import unittest
from pathlib import Path

from square_core.context import PipelineContext
from square_core.config.pipeline import PipelineConfig
from square_core.kitsu import OfflineApi
from square_core.services import projects, breakdown, media, work, review
from square_core.services.projects import ProjectSpec


class RecordingKitsu(OfflineApi):
    """OfflineApi that remembers publishes / statuses so we can assert."""
    def __init__(self):
        self.outputs = []
        self.workfiles = []
        self.previews = []
        self.statuses = []
        self.comments = []
        self._rev = {}

    def next_output_revision(self, entity, output_type_name, task=None, *, name="main"):
        key = (getattr(entity, "id", entity), output_type_name, name)
        return self._rev.get(key, 0) + 1

    def next_working_revision(self, task, *, name="main"):
        return len([w for w in self.workfiles if w["name"] == name]) + 1

    def record_working_file(self, task, *, revision, path, name="main", software=None, data=None):
        self.workfiles.append({"revision": revision, "path": path, "name": name,
                               "software": software or "", "data": data or {}})
        from square_core.model import Workfile
        return Workfile(revision=revision, path=path, name=name,
                        software=software or "", data=data or {})

    def working_files(self, task):
        from square_core.model import Workfile
        return [Workfile(revision=w["revision"], path=w["path"], name=w["name"],
                         software=w["software"], data=w["data"]) for w in self.workfiles]

    def record_output_file(self, entity, output_type_name, task, *, revision, path,
                           representation="", name="main", comment="", data=None):
        key = (getattr(entity, "id", entity), output_type_name, name)
        self._rev[key] = revision
        rec = {"output_type": output_type_name, "revision": revision, "path": path,
               "representation": representation, "name": name, "data": data or {}}
        self.outputs.append(rec)
        from square_core.model import Output
        return Output(output_type=output_type_name, revision=revision, path=path,
                      representation=representation, name=name, data=data or {})

    def upload_preview(self, task, file_path, *, comment="", status=None):
        self.previews.append({"path": file_path, "comment": comment, "status": status})
        from square_core.model import PreviewMedia
        return PreviewMedia(id=f"prev-{len(self.previews)}", path=file_path)

    def set_status(self, task, status_name, *, comment="", author=None):
        self.statuses.append((status_name, comment))
        return None

    def comment(self, task, text, *, status=None):
        self.comments.append((text, status))
        from square_core.model import Comment
        return Comment(text=text, status_change=status or "")


def _pctx(nas, kitsu=None):
    cfg = PipelineConfig(nas_roots={"default": nas})
    api = kitsu or RecordingKitsu()
    ctx = PipelineContext(config=cfg, kitsu=api, user=api.current_user())
    projects.create(ctx, ProjectSpec(code="ABC", fps=24.0))
    return ctx.project("ABC")


class TestMediaPublish(unittest.TestCase):
    def test_publish_copies_frames_and_records(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", frame_in=1001, frame_out=1003)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]

            render = Path(td) / "scratch"
            render.mkdir()
            frames = []
            for f in (1001, 1002, 1003):
                p = render / f"comp.{f}.exr"
                p.write_bytes(f"frame{f}".encode() * 50)
                frames.append(str(p))

            r = media.publish(pctx, shot, "CompRender", comp, files=frames,
                              proxy_dry_run=True)

            self.assertEqual(r.version, 1)
            self.assertEqual(r.kitsu_kind, "output")
            self.assertIn("/output/comp/v001/exr", r.dir)
            self.assertTrue(r.copied)
            for f in (1001, 1002, 1003):
                self.assertTrue((Path(r.dir) / f"comp.{f}.exr").exists())
            self.assertEqual(len(pctx.kitsu.outputs), 1)
            self.assertEqual(pctx.kitsu.outputs[0]["data"]["square"]["kind"], "publish")
            self.assertEqual(len(pctx.kitsu.previews), 1)      # CompRender is previewable

    def test_second_publish_increments_version(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            src = Path(td) / "f.1001.exr"
            src.write_bytes(b"x" * 100)
            r1 = media.publish(pctx, shot, "CompRender", comp, files=[str(src)],
                               make_review_proxy=False)
            r2 = media.publish(pctx, shot, "CompRender", comp, files=[str(src)],
                               make_review_proxy=False)
            self.assertEqual((r1.version, r2.version), (1, 2))

    def test_workfile_media_type_records_working_file(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            nk = Path(td) / "comp_v001.nk"
            nk.write_bytes(b"nuke script")
            r = media.publish(pctx, shot, "NukeScript", comp, files=[str(nk)])
            self.assertEqual(r.kitsu_kind, "working")
            self.assertTrue(r.files[0].endswith(".nk"))
            self.assertEqual(len(pctx.kitsu.workfiles), 1)

    def test_inputs_recorded_as_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 50)
            r = media.publish(pctx, shot, "CompRender", comp, files=[str(exr)],
                              make_review_proxy=False,
                              inputs=[{"kind": "working", "id": "wf-42"}])
            deps = pctx.kitsu.outputs[0]["data"]["square"]["inputs"]
            self.assertIn({"kind": "working", "id": "wf-42"}, deps)

    def test_pool_and_progress_are_forwarded_to_the_transfer(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            frames = []
            for f in (1001, 1002):
                p = Path(td) / f"c.{f}.exr"; p.write_bytes(b"x" * 20)
                frames.append(str(p))
            calls = []
            with ThreadPoolExecutor(max_workers=2) as shared_pool:
                r = media.publish(pctx, shot, "CompRender", comp, files=frames,
                                  make_review_proxy=False, pool=shared_pool,
                                  progress=lambda done, total: calls.append((done, total)))
            self.assertTrue(r.copied)
            self.assertEqual(calls[-1], (2, 2))

    def test_preview_pool_defers_the_proxy_and_returns_a_future(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "p.1001.exr"; exr.write_bytes(b"x" * 20)
            with ThreadPoolExecutor(max_workers=1) as preview_pool:
                r = media.publish(pctx, shot, "Plate", comp, files=[str(exr)],
                                  name="bg", proxy_dry_run=True, preview_pool=preview_pool)
                self.assertIsNone(r.preview)
                self.assertIsNotNone(r.preview_future)
                r.preview_future.result(timeout=5)
            self.assertEqual(len(pctx.kitsu.previews), 1)


class TestReview(unittest.TestCase):
    def test_submit_record_approve(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            proxy = Path(td) / "p.mp4"
            proxy.write_bytes(b"mp4")

            review.submit(pctx, comp, str(proxy), comment="please review")
            review.record_note(pctx, comp, text="fix the edge", status="Retake")
            review.approve(pctx, comp)

            self.assertEqual(pctx.kitsu.previews[0]["comment"], "please review")
            self.assertIn(("fix the edge", "Retake"), pctx.kitsu.comments)
            self.assertEqual(pctx.kitsu.statuses[-1][0], "Done")


class TestWorkfiles(unittest.TestCase):
    def _task(self, td):
        pctx = _pctx(td)
        shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
        task = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
        return pctx, shot, task

    def test_next_workfile_path_and_revision(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            path, rev = work.next_workfile_path(pctx, shot, task, media_type="NukeScript")
            self.assertEqual(rev, 1)
            self.assertTrue(path.endswith("_comp_main_v001.nk"))
            self.assertIn("/work/comp/nuke/", path.replace("\\", "/"))

    def test_new_workfile_reserves_slot_without_a_seed(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            slot = work.new_workfile(pctx, shot, task, media_type="NukeScript",
                                     software="nuke", comment="start comp")
            self.assertEqual(slot.revision, 1)
            self.assertFalse(slot.seeded)
            self.assertEqual(len(pctx.kitsu.workfiles), 1)
            self.assertEqual(pctx.kitsu.workfiles[0]["path"], slot.path)
            self.assertTrue(Path(slot.path).parent.is_dir())     # folder made
            self.assertFalse(Path(slot.path).exists())           # file left to the DCC

    def test_new_workfile_seeded_from_template(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "template.nk"
            tmpl.write_text("# nuke template", encoding="utf-8")
            slot = work.new_workfile(pctx, shot, task, media_type="NukeScript",
                                     template=str(tmpl))
            self.assertTrue(slot.seeded)
            self.assertTrue(Path(slot.path).is_file())
            self.assertEqual(Path(slot.path).read_text(encoding="utf-8"), "# nuke template")

    def test_new_workfile_copies_up_from_current(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("v1 content", encoding="utf-8")
            work.new_workfile(pctx, shot, task, media_type="NukeScript", template=str(tmpl))
            v2 = work.new_workfile(pctx, shot, task, media_type="NukeScript", from_current=True)
            self.assertEqual(v2.revision, 2)
            self.assertEqual(Path(v2.path).read_text(encoding="utf-8"), "v1 content")
            self.assertNotEqual(v2.path, tmpl)

    def test_versions_and_latest(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            for _ in range(3):
                work.new_workfile(pctx, shot, task, media_type="NukeScript", template=str(tmpl))
            vs = work.versions(pctx, task)
            self.assertEqual([w.revision for w in vs], [1, 2, 3])
            self.assertEqual(work.latest(pctx, task).revision, 3)

    def test_missing_seed_raises(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            with self.assertRaises(FileNotFoundError):
                work.new_workfile(pctx, shot, task, media_type="NukeScript",
                                  template=str(Path(td) / "nope.nk"))

    def test_publish_output_records_source_workfile(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)

            class _WF:
                id = "wf-7"
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 40)
            work.publish_output(pctx, shot, task, media_type="CompRender",
                                frames=[str(exr)], source_workfile=_WF(),
                                make_review_proxy=False)
            deps = pctx.kitsu.outputs[0]["data"]["square"]["inputs"]
            self.assertIn({"kind": "working", "id": "wf-7"}, deps)


if __name__ == "__main__":
    unittest.main()
