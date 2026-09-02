"""services.work / services.review with the offline Kitsu + a fake that records
what was published."""

import tempfile
import unittest
from pathlib import Path

from square_core.context import PipelineContext
from square_core.config.pipeline import PipelineConfig
from square_core.kitsu import OfflineApi
from square_core.services import projects, breakdown, work, review
from square_core.services.projects import ProjectSpec


class RecordingKitsu(OfflineApi):
    """OfflineApi that remembers publishes / statuses so we can assert."""
    def __init__(self):
        self.outputs = []
        self.previews = []
        self.statuses = []
        self.comments = []
        self._rev = {}

    def next_output_revision(self, entity, output_type_name, task=None, *, name="main"):
        key = (getattr(entity, "id", entity), output_type_name, name)
        return self._rev.get(key, 0) + 1

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


class TestPublishOutput(unittest.TestCase):
    def test_publish_copies_frames_and_records_output(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", frame_in=1001, frame_out=1003)
            tasks = breakdown.build_task_grid(pctx, [shot], ["Comp"])
            comp = tasks[0]

            render = Path(td) / "scratch"
            render.mkdir()
            frames = []
            for f in (1001, 1002, 1003):
                p = render / f"comp.{f}.exr"
                p.write_bytes(f"frame{f}".encode() * 50)
                frames.append(str(p))

            result = work.publish_output(
                pctx, shot, comp, output_type="comp", frames=frames,
                sequence="SQ010", shot="SH0100", representation="exr",
                make_review_proxy=True, proxy_dry_run=True,
            )

            self.assertEqual(result.output.revision, 1)
            self.assertIn("/output/comp/v001/exr", result.path)
            for f in (1001, 1002, 1003):
                self.assertTrue((Path(result.path) / f"comp.{f}.exr").exists())
            self.assertEqual(len(pctx.kitsu.outputs), 1)
            self.assertEqual(pctx.kitsu.outputs[0]["data"]["square"]["kind"], "publish")
            self.assertEqual(len(pctx.kitsu.previews), 1)      # dry-run proxy uploaded

    def test_second_publish_increments_version(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            src = Path(td) / "f.1001.exr"
            src.write_bytes(b"x" * 100)
            r1 = work.publish_output(pctx, shot, comp, output_type="comp", frames=[str(src)],
                                     sequence="SQ010", shot="SH0100", make_review_proxy=False)
            r2 = work.publish_output(pctx, shot, comp, output_type="comp", frames=[str(src)],
                                     sequence="SQ010", shot="SH0100", make_review_proxy=False)
            self.assertEqual((r1.output.revision, r2.output.revision), (1, 2))


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


if __name__ == "__main__":
    unittest.main()
