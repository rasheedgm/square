import unittest

from tools.ingest_tool.core.item import IngestItem, IssueKind, Severity, Action
from tools.ingest_tool.core import preflight


def _item(key="k", **kw):
    base = dict(
        key=key, source_files=[f"/d/{key}.1001.exr"], ext=".exr",
        sequence_code="SQ010", shot_code="SH0100", media_type="Plate",
        media_name="main", version=1,
    )
    base.update(kw)
    it = IngestItem(**base)
    it.metadata_verified = {"resolution": True, "fps": True, "colorspace": True}
    it.resolution, it.fps, it.colorspace = "1920x1080", 24.0, "ACEScg"
    it.dest_dir = kw.get("dest_dir", f"/nas/SQ010/SH0100/plates/main_v{base['version']:03d}")
    return it


class TestFieldIssues(unittest.TestCase):
    def test_clean_item_has_none(self):
        self.assertEqual(preflight.field_issues(_item()), [])

    def test_missing_required_field(self):
        issues = preflight.field_issues(_item(shot_code=""))
        self.assertEqual(issues[0].kind, IssueKind.NEEDS_INFO)
        self.assertEqual(issues[0].column, "shot_code")

    def test_unverified_colorspace_blocks(self):
        it = _item()
        it.metadata_verified["colorspace"] = False
        it.colorspace = ""
        issues = preflight.field_issues(it)
        self.assertTrue(any(i.column == "colorspace" and i.severity == Severity.BLOCK for i in issues))

    def test_unverified_but_user_set_colorspace_ok(self):
        it = _item()
        it.metadata_verified["colorspace"] = False
        it.colorspace = "ACEScg"        # user typed it
        issues = preflight.field_issues(it)
        self.assertFalse(any(i.column == "colorspace" for i in issues))

    def test_illegal_chars_block(self):
        issues = preflight.field_issues(_item(shot_code="SH:0100"))
        self.assertTrue(any(i.kind == IssueKind.ILLEGAL_CHARS for i in issues))

    def test_unknown_media_type_warns(self):
        issues = preflight.field_issues(_item(media_type="Foreground"),
                                       known_media_types=["Plate", "Ref"])
        w = [i for i in issues if i.kind == IssueKind.NO_DEST_TEMPLATE]
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0].severity, Severity.WARN)


class TestLedgerAndSlot(unittest.TestCase):
    def test_partial_overlap_warns(self):
        it = _item()
        it.ledger_kind = "partial"
        it.ledger_detail = "3 of 96 files already in as v2"
        iss = preflight.ledger_issue(it)
        self.assertEqual(iss.kind, IssueKind.PARTIAL_OVERLAP)
        self.assertEqual(iss.severity, Severity.WARN)

    def test_full_match_to_this_slot_is_not_a_ledger_issue(self):
        it = _item()
        it.ledger_kind = "full"
        self.assertIsNone(preflight.ledger_issue(it, preflight.SLOT_ALREADY))

    def test_full_match_elsewhere_is_a_duplicate_content_warning(self):
        it = _item()
        it.ledger_kind = "full"
        iss = preflight.ledger_issue(it, preflight.SLOT_EMPTY)
        self.assertEqual(iss.kind, IssueKind.DUPLICATE_CONTENT)
        self.assertEqual(iss.severity, Severity.WARN)

    def test_slot_conflict_blocks_on_version(self):
        iss = preflight.slot_issue(_item(), preflight.SLOT_CONFLICT)
        self.assertEqual(iss.kind, IssueKind.DEST_EXISTS_DIFF)
        self.assertEqual(iss.column, "version")

    def test_slot_empty_is_no_issue(self):
        self.assertIsNone(preflight.slot_issue(_item(), preflight.SLOT_EMPTY))

    def test_slot_already_is_a_warning_with_a_way_forward(self):
        iss = preflight.slot_issue(_item(), preflight.SLOT_ALREADY)
        self.assertEqual(iss.kind, IssueKind.ALREADY_IN_SLOT)
        self.assertEqual(iss.severity, Severity.WARN)
        self.assertIn(Action.VERSION_UP, iss.actions)
        self.assertIn(Action.OVERWRITE, iss.actions)


class TestCrossItem(unittest.TestCase):
    def test_dest_collision_flags_every_row_in_the_group(self):
        a = _item("a", dest_dir="/nas/x/main_v001")
        b = _item("b", dest_dir="/nas/x/main_v001")
        c = _item("c", dest_dir="/nas/x/other_v001")
        out = preflight.cross_item_issues([a, b, c])
        self.assertTrue(any(i.kind == IssueKind.DEST_COLLISION for i in out["a"]))
        self.assertTrue(any(i.kind == IssueKind.DEST_COLLISION for i in out["b"]))
        self.assertEqual(out["c"], [])

    def test_dest_collision_ignores_skipped_rows(self):
        a = _item("a", dest_dir="/nas/x/main_v001")
        b = _item("b", dest_dir="/nas/x/main_v001")
        b.skipped = True
        out = preflight.cross_item_issues([a, b])
        self.assertEqual(out["a"], [])

    def test_dest_collision_is_case_insensitive_on_path(self):
        a = _item("a", dest_dir="/NAS/X/Main_v001")
        b = _item("b", dest_dir="/nas/x/main_v001")
        out = preflight.cross_item_issues([a, b])
        self.assertTrue(any(i.kind == IssueKind.DEST_COLLISION for i in out["a"]))

    def test_near_dup_shot_name(self):
        a = _item("a", shot_code="SH0100", dest_dir="/nas/a")
        b = _item("b", shot_code="SH_0100", dest_dir="/nas/b")
        out = preflight.cross_item_issues([a, b])
        self.assertTrue(any(i.kind == IssueKind.NEAR_DUP_BATCH for i in out["a"]))
        self.assertTrue(any(i.kind == IssueKind.NEAR_DUP_BATCH for i in out["b"]))

    def test_case_only_difference_is_its_own_kind(self):
        a = _item("a", shot_code="SH0100", dest_dir="/nas/a")
        b = _item("b", shot_code="sh0100", dest_dir="/nas/b")
        out = preflight.cross_item_issues([a, b])
        self.assertTrue(any(i.kind == IssueKind.CASE_INCONSISTENT for i in out["a"]))
        self.assertFalse(any(i.kind == IssueKind.NEAR_DUP_BATCH for i in out["a"]))

    def test_identical_names_are_not_flagged(self):
        a = _item("a", shot_code="SH0100", media_name="main", dest_dir="/nas/a")
        b = _item("b", shot_code="SH0100", media_name="bg", dest_dir="/nas/b")
        out = preflight.cross_item_issues([a, b])
        self.assertEqual(out["a"], [])
        self.assertEqual(out["b"], [])


class TestAssemble(unittest.TestCase):
    def test_combines_all_sources(self):
        it = _item(shot_code="")           # needs-info
        it.ledger_kind = "partial"
        it.ledger_detail = "x"
        cross = [preflight.cross_item_issues([it]).get(it.key, [])]  # empty
        issues = preflight.assemble_issues(
            it, preflight.SLOT_CONFLICT, "occupied",
            cross=[], known_media_types=["Plate"],
        )
        kinds = {i.kind for i in issues}
        self.assertIn(IssueKind.NEEDS_INFO, kinds)
        self.assertIn(IssueKind.PARTIAL_OVERLAP, kinds)
        self.assertIn(IssueKind.DEST_EXISTS_DIFF, kinds)


if __name__ == "__main__":
    unittest.main()
