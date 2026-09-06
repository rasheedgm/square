import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.ingest_tool.core.item import (
    IngestItem, Issue, IssueKind, Severity, Action, Status, Stage,
    INGESTABLE_STATUSES,
)


def _item(**kw):
    base = dict(
        key="k1", source_files=["/d/a.1001.exr", "/d/a.1002.exr"], ext=".exr",
        sequence_code="SQ010", shot_code="SH0100", media_type="Plate", media_name="main",
        version=1,
    )
    base.update(kw)
    it = IngestItem(**base)
    # a bare item hasn't been checked yet; most tests want it "checked, clean"
    it.preflight_done = kw.get("preflight_done", True)
    it.metadata_verified = {"resolution": True, "fps": True, "colorspace": True}
    it.resolution, it.fps, it.colorspace = "1920x1080", 24.0, "ACEScg"
    return it


class TestIdentity(unittest.TestCase):
    def test_key_is_stable_and_order_independent(self):
        a = IngestItem.compute_key(["/d/a.1001.exr", "/d/a.1002.exr"])
        b = IngestItem.compute_key(["/d/a.1002.exr", "/d/a.1001.exr"])
        self.assertEqual(a, b)

    def test_key_differs_for_different_files(self):
        self.assertNotEqual(
            IngestItem.compute_key(["/d/a.exr"]),
            IngestItem.compute_key(["/d/b.exr"]),
        )

    def test_from_scan_item_copies_tagged_fields(self):
        class Scan:
            name = "a"; files = ["/d/a.1001.exr"]; ext = ".exr"; is_video = False
            sequence_code = "SQ010"; shot_code = "SH0100"
            media_type = "Plate"; media_name = "bg"; version = 3
            extra_tags = {"camera": "A"}
            start_frame = 1001; end_frame = 1001; missing_frames = []; frame_count = 1
        it = IngestItem.from_scan_item(Scan())
        self.assertEqual(it.shot_code, "SH0100")
        self.assertEqual(it.version, 3)
        self.assertEqual(it.extra_tags, {"camera": "A"})
        self.assertEqual(it.key, IngestItem.compute_key(["/d/a.1001.exr"]))

    def test_from_scan_item_copies_pattern_defaulted_metadata_as_verified(self):
        class Scan:
            name = "a"; files = ["/d/a.1001.exr"]; ext = ".exr"; is_video = False
            sequence_code = "SQ010"; shot_code = "SH0100"
            media_type = "Plate"; media_name = "bg"; version = 1
            extra_tags = {}
            start_frame = 1001; end_frame = 1001; missing_frames = []; frame_count = 1
            fps = 24.0; colorspace = "ACEScg"
            metadata_defaulted = {"fps", "colorspace"}
        it = IngestItem.from_scan_item(Scan())
        self.assertEqual(it.fps, 24.0)
        self.assertEqual(it.colorspace, "ACEScg")
        self.assertTrue(it.metadata_verified["fps"])
        self.assertTrue(it.metadata_verified["colorspace"])
        self.assertNotIn("resolution", it.metadata_verified)   # not defaulted, untouched


class TestProbeMetadata(unittest.TestCase):
    class _FakeExtractor:
        def __init__(self, found, backend="oiio"):
            self.found, self.backend = found, backend

        def probe(self, path):
            return dict(self.found), self.backend

    def test_read_fields_are_marked_verified(self):
        it = _item(preflight_done=False)
        it.metadata_verified = {}
        it.probe_metadata(self._FakeExtractor(
            {"resolution": "3840x2160", "width": 3840, "height": 2160,
             "fps": 25.0, "colorspace": "ACEScg"}))
        self.assertEqual(it.resolution, "3840x2160")
        self.assertTrue(it.metadata_verified["colorspace"])
        self.assertTrue(it.metadata_verified["fps"])

    def test_unread_fields_are_marked_unverified(self):
        it = _item(preflight_done=False)
        it.metadata_verified = {}
        it.probe_metadata(self._FakeExtractor({"resolution": "1998x1080", "width": 1998, "height": 1080}))
        self.assertFalse(it.metadata_verified["colorspace"])
        self.assertFalse(it.metadata_verified["fps"])
        self.assertTrue(it.metadata_verified["resolution"])

    def test_nothing_read_at_all(self):
        it = _item(preflight_done=False)
        it.metadata_verified = {}
        it.probe_metadata(self._FakeExtractor({}, backend=None))
        self.assertFalse(any(it.metadata_verified.values()))
        self.assertEqual(it.metadata_backend, "")

    def test_a_pattern_default_survives_a_failed_real_extraction(self):
        # Priority: real extraction > Path Pattern default > blank/needs-info.
        # A default pre-fills metadata_verified=True; probe_metadata's own
        # "not found" branch uses setdefault, so it must not clobber that.
        it = _item(preflight_done=False)
        it.fps = 24.0
        it.metadata_verified = {"fps": True}
        it.probe_metadata(self._FakeExtractor({"resolution": "1920x1080"}))
        self.assertEqual(it.fps, 24.0)
        self.assertTrue(it.metadata_verified["fps"])

    def test_a_real_extraction_still_overrides_a_pattern_default(self):
        it = _item(preflight_done=False)
        it.fps = 24.0
        it.metadata_verified = {"fps": True}
        it.probe_metadata(self._FakeExtractor({"fps": 23.976}))
        self.assertEqual(it.fps, 23.976)
        self.assertTrue(it.metadata_verified["fps"])


class TestStatusDerivation(unittest.TestCase):
    def test_new_when_clean_v1(self):
        self.assertEqual(_item().status, Status.NEW)

    def test_new_version_when_v_gt_1(self):
        self.assertEqual(_item(version=2).status, Status.NEW_VERSION)

    def test_checking_until_preflight_done(self):
        it = _item(preflight_done=False)
        self.assertEqual(it.status, Status.CHECKING)

    def test_needs_info_when_required_field_blank(self):
        it = _item(shot_code="")
        it.issues = [Issue(IssueKind.NEEDS_INFO, "shot missing", column="shot_code")]
        self.assertEqual(it.status, Status.NEEDS_INFO)

    def test_needs_info_when_colorspace_unverified(self):
        it = _item()
        it.metadata_verified["colorspace"] = False
        it.colorspace = ""
        it.issues = [Issue(IssueKind.NEEDS_INFO, "colorspace unknown", column="colorspace")]
        self.assertEqual(it.status, Status.NEEDS_INFO)

    def test_conflict_when_unresolved_block(self):
        it = _item()
        it.issues = [Issue(IssueKind.DEST_EXISTS_DIFF, "occupied", severity=Severity.BLOCK)]
        self.assertEqual(it.status, Status.CONFLICT)

    def test_ready_after_block_resolved(self):
        it = _item()
        iss = Issue(IssueKind.DEST_EXISTS_DIFF, "occupied", severity=Severity.BLOCK)
        it.issues = [iss]
        it.resolve(iss.id, Action.OVERWRITE)
        self.assertEqual(it.status, Status.READY)

    def test_warning_when_only_warn_issues(self):
        it = _item()
        it.issues = [Issue(IssueKind.NEAR_DUP_BATCH, "SH_0100 vs SH0100", severity=Severity.WARN)]
        self.assertEqual(it.status, Status.WARNING)
        self.assertTrue(it.ingestable)

    def test_warning_cleared_by_ignore(self):
        it = _item()
        iss = Issue(IssueKind.NEAR_DUP_BATCH, "x", severity=Severity.WARN)
        it.issues = [iss]
        it.resolve(iss.id, Action.IGNORE)
        self.assertEqual(it.status, Status.NEW)

    def test_skipped_wins_over_conflict(self):
        it = _item()
        it.issues = [Issue(IssueKind.DEST_EXISTS_DIFF, "x", severity=Severity.BLOCK)]
        it.skipped = True
        self.assertEqual(it.status, Status.SKIPPED)

    def test_already_ingested_from_slot_already(self):
        it = _item()
        it.slot_state = "already"
        self.assertEqual(it.status, Status.ALREADY_INGESTED)
        self.assertFalse(it.ingestable)

    def test_ledger_full_but_different_dest_is_only_a_warning(self):
        it = _item()
        it.ledger_kind = "full"
        it.slot_state = "empty"            # this exact target is free
        # controller would attach a DUPLICATE_CONTENT warn issue; without it
        # the row is just NEW -- the point is it is NOT hard-blocked
        self.assertNotEqual(it.status, Status.ALREADY_INGESTED)
        self.assertTrue(it.ingestable)

    def test_overwrite_beats_already_ingested(self):
        it = _item()
        it.slot_state = "already"
        iss = Issue(IssueKind.ALREADY_IN_SLOT, "x", severity=Severity.WARN)
        it.issues = [iss]
        it.resolve(iss.id, Action.OVERWRITE)
        self.assertNotEqual(it.status, Status.ALREADY_INGESTED)

    def test_check_failed(self):
        it = _item()
        it.check_error = "permission denied"
        self.assertEqual(it.status, Status.CHECK_FAILED)

    def test_ingesting_and_completed(self):
        it = _item()
        it.stage = Stage.COPYING
        self.assertEqual(it.status, Status.INGESTING)
        it.stage = Stage.DONE
        it.ingested = True
        self.assertEqual(it.status, Status.COMPLETED)

    def test_failed_status(self):
        it = _item()
        it.ingest_error = "copy blew up"
        self.assertEqual(it.status, Status.FAILED)

    def test_ingestable_set_matches_enum(self):
        for s in (Status.NEW, Status.NEW_VERSION, Status.READY, Status.WARNING):
            self.assertIn(s, INGESTABLE_STATUSES)
        for s in (Status.CONFLICT, Status.NEEDS_INFO, Status.SKIPPED,
                  Status.ALREADY_INGESTED, Status.CHECK_FAILED):
            self.assertNotIn(s, INGESTABLE_STATUSES)


class TestResolutionsAndSkip(unittest.TestCase):
    def test_skip_action_sets_skipped(self):
        it = _item()
        iss = Issue(IssueKind.DEST_COLLISION, "x", severity=Severity.BLOCK)
        it.issues = [iss]
        it.resolve(iss.id, Action.SKIP)
        self.assertTrue(it.skipped)

    def test_include_undoes_skip_and_resurfaces_issue(self):
        it = _item()
        iss = Issue(IssueKind.DEST_COLLISION, "x", severity=Severity.BLOCK)
        it.issues = [iss]
        it.resolve(iss.id, Action.SKIP)
        it.include()
        self.assertFalse(it.skipped)
        self.assertEqual(it.status, Status.CONFLICT)

    def test_issue_actions_lookup(self):
        iss = Issue(IssueKind.PARTIAL_OVERLAP, "x")
        self.assertEqual(set(iss.actions), {Action.SKIP, Action.VERSION_UP, Action.OVERWRITE})


class TestSerialization(unittest.TestCase):
    def test_round_trip_preserves_status(self):
        it = _item(version=2)
        iss = Issue(IssueKind.DEST_EXISTS_DIFF, "occupied", severity=Severity.BLOCK,
                    column="version", data={"existing": "v002"})
        it.issues = [iss]
        it.resolve(iss.id, Action.VERSION_UP)
        it.stage = Stage.COPYING

        again = IngestItem.from_dict(it.to_dict())
        self.assertEqual(again.version, 2)
        self.assertEqual(again.issues[0].kind, IssueKind.DEST_EXISTS_DIFF)
        self.assertEqual(again.issues[0].data["existing"], "v002")
        self.assertEqual(again.resolutions[iss.id], Action.VERSION_UP)
        self.assertEqual(again.stage, Stage.COPYING)

    def test_round_trip_clean_item(self):
        it = _item()
        again = IngestItem.from_dict(it.to_dict())
        self.assertEqual(again.status, Status.NEW)
        self.assertEqual(again.key, it.key)


if __name__ == "__main__":
    unittest.main()
