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
        self.output_data = []          # (output, merged-extra) pairs
        self.workfiles = []
        self.previews = []
        self.statuses = []
        self.comments = []
        self._rev = {}

    def merge_output_data(self, output, extra):
        self.output_data.append((output, dict(extra)))

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
        self._rev[key] = max(self._rev.get(key, 0), revision)
        rec = next((o for o in self.outputs if o["output_type"] == output_type_name
                    and o["revision"] == revision and o["name"] == name), None)
        if rec is None:
            rec = {"output_type": output_type_name, "revision": revision, "name": name}
            self.outputs.append(rec)
        rec.update(path=path, representation=representation, data=data or {})
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


def _task(td):
    pctx = _pctx(td)
    shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
    task = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
    return pctx, shot, task


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

    def test_publish_comment_reaches_the_review_previews_comment(self):
        """The comment field a publish (e.g. Nuke's Render/Publish panel) is
        given used to land ONLY on the output file's own comment field --
        never on the review clip a supervisor actually watches and comments
        back on. The version label stays in the preview comment too, so
        scanning a task's review history still shows what version each clip
        is."""
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 20)
            media.publish(pctx, shot, "CompRender", comp, files=[str(exr)],
                          comment="fixed the edge per note #3", proxy_dry_run=True)
            self.assertEqual(len(pctx.kitsu.previews), 1)
            preview_comment = pctx.kitsu.previews[0]["comment"]
            self.assertIn("fixed the edge per note #3", preview_comment)
            self.assertIn("v001", preview_comment)

    def test_publish_works_without_xxhash_and_records_the_algo_used(self):
        """Regression: an embedded DCC interpreter is pure-Python only and
        can't import xxhash (compiled wheels only), so publish() from Nuke
        died with "xxh3_64 requested but the 'xxhash' extension is not
        available". It now uses blake2b there and records the algo actually
        used, so a digest is never compared against one made with another."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 20)
            with patch("square_core.hashing._HAS_XXHASH", False):
                r = media.publish(pctx, shot, "CompRender", comp, files=[str(exr)],
                                  make_review_proxy=False)
            self.assertTrue(r.copied)
            sq = pctx.kitsu.outputs[0]["data"]["square"]
            self.assertEqual(sq["checksum_algo"], "blake2b")
            self.assertTrue(sq["checksum"])

    def test_no_comment_falls_back_to_the_default_preview_label(self):
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 20)
            media.publish(pctx, shot, "CompRender", comp, files=[str(exr)], proxy_dry_run=True)
            self.assertEqual(pctx.kitsu.previews[0]["comment"], "Preview — CompRender v001")

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

    def test_review_proxy_encode_and_upload_are_logged(self):
        """A publish with a review preview blocks the caller for the whole
        encode-then-upload stretch when it's not run through a preview_pool
        (exactly what a DCC calling publish() straight from its own main
        thread does) -- these are the only feedback a caller with no
        progress UI of its own gets that it isn't frozen."""
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 20)
            with self.assertLogs("square.services.media", level="INFO") as cm:
                media.publish(pctx, shot, "CompRender", comp, files=[str(exr)],
                              proxy_dry_run=True)
            joined = "\n".join(cm.output)
            self.assertIn("encoding review proxy", joined)
            self.assertIn("uploading", joined.lower())

    def test_stamp_provenance_failure_does_not_lose_an_uploaded_preview(self):
        """The preview upload + set-main-preview already succeeded by the
        time provenance stamping runs -- its failure must be caught and
        reported, not make the caller think the whole preview failed and
        discard an otherwise-successful upload (make_review_proxy_for()'s
        result.preview would silently come back None instead)."""
        with tempfile.TemporaryDirectory() as td:
            pctx = _pctx(td)
            shot = breakdown.ensure_shot(pctx, "SQ010", "SH0100", create_folders=False)
            comp = breakdown.build_task_grid(pctx, [shot], ["Comp"])[0]
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 20)

            def boom(preview, provenance, on="preview"):
                raise RuntimeError("simulated persistent stamp failure")

            pctx.kitsu.stamp_provenance = boom
            with self.assertLogs("square.services.media", level="WARNING") as cm:
                r = media.publish(pctx, shot, "CompRender", comp, files=[str(exr)],
                                  proxy_dry_run=True)
            self.assertIsNotNone(r.preview)           # the preview itself is NOT lost
            self.assertIn("could not stamp provenance", "\n".join(cm.output))
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
        return _task(td)

    def _save_minor(self, target):
        Path(target.path).parent.mkdir(parents=True, exist_ok=True)
        Path(target.path).write_text(f"# {target.label()}", encoding="utf-8")

    def test_next_save_minor_and_major(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            # first save is always a new major, minor 1
            t1 = work.next_save(pctx, shot, task, bump="minor")
            self.assertEqual((t1.major, t1.minor, t1.is_new_major), (1, 1, True))
            self.assertTrue(t1.path.endswith("_comp_main_v001.001.nk"))
            self._save_minor(t1)
            work.register_major(pctx, shot, task, t1)

            t2 = work.next_save(pctx, shot, task, bump="minor")
            self.assertEqual((t2.major, t2.minor, t2.is_new_major), (1, 2, False))
            self._save_minor(t2)

            t3 = work.next_save(pctx, shot, task, bump="major")
            self.assertEqual((t3.major, t3.minor, t3.is_new_major), (2, 1, True))

    def test_new_workfile_registers_a_major_and_seeds(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "template.nk"
            tmpl.write_text("# nuke template", encoding="utf-8")
            slot = work.new_workfile(pctx, shot, task, template=str(tmpl))
            self.assertEqual((slot.major, slot.minor), (1, 1))
            self.assertEqual(slot.revision, 1)
            self.assertTrue(Path(slot.path).is_file())
            self.assertEqual(Path(slot.path).read_text(encoding="utf-8"), "# nuke template")
            self.assertEqual(len(pctx.kitsu.workfiles), 1)

    def test_new_workfile_copies_up_from_current_latest_minor(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("v1.001", encoding="utf-8")
            work.new_workfile(pctx, shot, task, template=str(tmpl))
            # a WIP minor save on top of v001
            m2 = work.next_save(pctx, shot, task, bump="minor")
            Path(m2.path).write_text("v1.002", encoding="utf-8")

            v2 = work.new_workfile(pctx, shot, task, from_current=True)
            self.assertEqual(v2.major, 2)
            self.assertEqual(Path(v2.path).read_text(encoding="utf-8"), "v1.002")   # latest minor

    def test_workfile_versions_groups_majors_with_disk_minors(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            work.new_workfile(pctx, shot, task, template=str(tmpl))            # v001.001
            for _ in range(2):
                self._save_minor(work.next_save(pctx, shot, task, bump="minor"))  # v001.002, .003
            work.new_workfile(pctx, shot, task, from_current=True)            # v002.001

            vs = work.workfile_versions(pctx, shot, task)
            self.assertEqual([v.major for v in vs], [1, 2])
            self.assertEqual([m.minor for m in vs[0].minors], [3, 2, 1])       # newest first
            self.assertEqual(vs[0].label(), "v001.003")
            self.assertTrue(vs[0].online)

    def test_workfile_names(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            work.new_workfile(pctx, shot, task, template=str(tmpl), name="main")
            work.new_workfile(pctx, shot, task, template=str(tmpl), name="precomp")
            self.assertEqual(work.workfile_names(pctx, task), ["main", "precomp"])

    def test_missing_seed_raises(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            with self.assertRaises(FileNotFoundError):
                work.new_workfile(pctx, shot, task, template=str(Path(td) / "nope.nk"))

    def test_output_lock(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            exr = Path(td) / "c.1001.exr"; exr.write_bytes(b"x" * 40)
            res = work.publish_output(pctx, shot, task, media_type="CompRender",
                                      frames=[str(exr)], make_review_proxy=False)
            self.assertFalse(work.output_locked(res.record))
            work.lock_output(pctx, res.record, reason="approved")
            self.assertEqual(pctx.kitsu.output_data[-1][1], {"locked": True,
                                                             "locked_reason": "approved"})

    def test_explicit_version_replaces_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            a = Path(td) / "a.1001.exr"; a.write_bytes(b"a" * 20)
            b = Path(td) / "b.1001.exr"; b.write_bytes(b"b" * 20)
            work.publish_output(pctx, shot, task, media_type="CompRender", frames=[str(a)],
                                version=2, make_review_proxy=False)
            work.publish_output(pctx, shot, task, media_type="CompRender", frames=[str(b)],
                                version=2, make_review_proxy=False)          # re-render v2
            comps = [o for o in pctx.kitsu.outputs if o["output_type"] == "CompRender"]
            self.assertEqual(len(comps), 1)                                   # not forked
            self.assertEqual(comps[0]["revision"], 2)

    def test_current_workfile_major(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            self.assertEqual(work.current_workfile_major(pctx, task), 0)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            work.new_workfile(pctx, shot, task, template=str(tmpl))
            work.new_workfile(pctx, shot, task, from_current=True)
            self.assertEqual(work.current_workfile_major(pctx, task), 2)

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


class TestOpenScriptVerification(unittest.TestCase):
    """verify_open_script() -- is the script actually open really OUR
    workfile for this shot/task, at some real major/minor? Used before
    trusting Kitsu's "current major" for a Sync render."""

    def _task(self, td):
        return _task(td)

    def test_parse_workfile_version(self):
        self.assertEqual(work.parse_workfile_version("X:/j/ABC_SQ010_SH0100_comp_main_v004.002.nk"),
                         (4, 2))
        self.assertIsNone(work.parse_workfile_version("X:/j/not_our_naming.nk"))

    def test_verify_open_script_matches_a_real_registered_path(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            slot = work.new_workfile(pctx, shot, task, template=str(tmpl))
            self.assertEqual(work.verify_open_script(pctx, shot, task, slot.path),
                             (1, 1))

    def test_verify_open_script_rejects_a_foreign_looking_file(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            # matches the bare _vNNN.NNN. pattern but for a DIFFERENT shot --
            # must not be accepted just because the shape looks right
            foreign = Path(td) / "OTHER_SQ999_SH9999_comp_main_v001.001.nk"
            foreign.write_text("x", encoding="utf-8")
            self.assertIsNone(work.verify_open_script(pctx, shot, task, str(foreign)))

    def test_verify_open_script_rejects_unsaved_or_unrelated_path(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            self.assertIsNone(work.verify_open_script(pctx, shot, task, ""))
            self.assertIsNone(work.verify_open_script(pctx, shot, task, "X:/scratch/untitled.nk"))


class TestRenderedSnapshot(unittest.TestCase):
    """snapshot_rendered_script() (the read-only v{major}.000) and
    register_major_at() (registering an explicit, possibly skipped-ahead,
    major so a "(new)" render's output has a real workfile to point at)."""

    def _task(self, td):
        return _task(td)

    def test_snapshot_is_written_and_made_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            src = Path(td) / "open_script_v001.004.nk"
            src.write_text("state at .004", encoding="utf-8")

            dest = work.snapshot_rendered_script(pctx, shot, task, source_path=str(src), major=1)

            self.assertTrue(Path(dest).name.endswith("v001.000.nk"))
            self.assertEqual(Path(dest).read_text(encoding="utf-8"), "state at .004")
            # a plain write must fail -- read-only actually took effect
            with self.assertRaises(OSError):
                Path(dest).write_text("accidental ctrl+s", encoding="utf-8")

    def test_snapshot_overwrite_goes_writable_then_back_to_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            src1 = Path(td) / "s1.nk"; src1.write_text("first render", encoding="utf-8")
            src2 = Path(td) / "s2.nk"; src2.write_text("second render", encoding="utf-8")

            work.snapshot_rendered_script(pctx, shot, task, source_path=str(src1), major=1)
            dest = work.snapshot_rendered_script(pctx, shot, task, source_path=str(src2), major=1)

            self.assertEqual(Path(dest).read_text(encoding="utf-8"), "second render")
            with self.assertRaises(OSError):
                Path(dest).write_text("still protected", encoding="utf-8")

    def test_register_major_at_explicit_number_skips_ahead(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            work.new_workfile(pctx, shot, task, template=str(tmpl))   # major 1
            self.assertEqual(work.current_workfile_major(pctx, task), 1)

            snap = work.snapshot_rendered_script(
                pctx, shot, task, source_path=str(tmpl), major=6)
            rec = work.register_major_at(pctx, shot, task, 6, snap)

            self.assertEqual(work.current_workfile_major(pctx, task), 6)
            self.assertEqual(rec.revision, 6)
            self.assertEqual(rec.path, snap)

    def test_register_major_at_is_a_no_op_if_already_registered(self):
        with tempfile.TemporaryDirectory() as td:
            pctx, shot, task = self._task(td)
            tmpl = Path(td) / "t.nk"; tmpl.write_text("x", encoding="utf-8")
            slot = work.new_workfile(pctx, shot, task, template=str(tmpl))   # major 1

            before = len(pctx.kitsu.workfiles)
            rec = work.register_major_at(pctx, shot, task, 1, slot.path)
            self.assertEqual(len(pctx.kitsu.workfiles), before)     # nothing new registered
            self.assertEqual(rec.revision, 1)


if __name__ == "__main__":
    unittest.main()
