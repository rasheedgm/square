import unittest
from Qt import QtWidgets

from tools.ingest_tool.widgets.table_widget import (
    IngestTableWidget, STATUS_CONFLICT, STATUS_NEW, STATUS_DISCARDED,
    STATUS_CHECKING, COL_PROGRESS, COL_VERSION, COL_SHOT, COL_STATUS,
    STAGE_QUEUED, STAGE_COPYING, STAGE_DONE, ROW_HEIGHT,
)
from square_core.plate_scanner import IngestSequenceItem


def _make_item(name, seq, shot, mtype, media_name, path):
    item = IngestSequenceItem(name, [path], ".mov", is_video=True)
    item.sequence_code = seq
    item.shot_code = shot
    item.media_type = mtype
    item.media_name = media_name
    return item


class TestTableWidgetConflicts(unittest.TestCase):
    """
    Regression coverage for a confirmed bug: has_unresolved_conflicts() checked
    `if id not in self.item_discarded` where `id` was Python's builtin function
    (not the row's key), so it never actually excluded discarded rows -- and
    the discard checkbox handler never re-ran conflict detection at all. Net
    effect: unchecking one side of a conflicting pair never cleared the
    "Unresolved conflicts" block on the Ingest button.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_discarding_one_side_resolves_conflict(self):
        item1 = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        item2 = _make_item("b", "SQ010", "SH0100", "Plate", "PL01", "/tmp/b.mov")

        self.table.populate_table([item1, item2])
        self.table._run_conflict_detection()
        self.table._refresh_table()

        self.assertTrue(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.item_status[id(item1)], STATUS_CONFLICT)
        self.assertEqual(self.table.item_status[id(item2)], STATUS_CONFLICT)

        # Discard item2 via the same code path the row checkbox uses.
        self.table._on_checkbox_changed(id(item2), 0)

        self.assertFalse(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.item_status[id(item1)], STATUS_NEW)
        self.assertEqual(self.table.item_status[id(item2)], STATUS_DISCARDED)

    def test_non_conflicting_discarded_row_never_blocks(self):
        item1 = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        self.table.populate_table([item1])
        self.table._run_conflict_detection()
        self.table._refresh_table()

        self.table._on_checkbox_changed(id(item1), 0)
        self.assertFalse(self.table.has_unresolved_conflicts())


class TestTableWidgetProgressAndBatchTools(unittest.TestCase):
    """Per-row live ingest progress + the Media Type / batch-version tools."""

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_progress_bar_updates_without_full_rebuild(self):
        item = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        self.table.populate_table([item])

        bar = self.table._table.cellWidget(0, COL_PROGRESS)
        self.assertEqual(bar.value(), 0)

        self.table.update_ingest_progress(item, STAGE_COPYING, 50)
        bar = self.table._table.cellWidget(0, COL_PROGRESS)
        self.assertEqual(bar.value(), 50)

        self.table.update_ingest_progress(item, STAGE_DONE)
        bar = self.table._table.cellWidget(0, COL_PROGRESS)
        self.assertEqual(bar.value(), 100)

    def test_batch_set_version(self):
        item1 = _make_item("a", "SQ010", "SH0100", "Plate", "PL01", "/tmp/a.mov")
        item2 = _make_item("b", "SQ020", "SH0200", "Plate", "PL02", "/tmp/b.mov")
        self.table.populate_table([item1, item2])

        self.table._batch_version_spin.setValue(7)
        self.table._on_batch_set_version()

        self.assertEqual(self.table.item_version[id(item1)], 7)
        self.assertEqual(self.table.item_version[id(item2)], 7)

    def test_batch_rename_media_type_and_case(self):
        item = _make_item("a", "SQ010", "SH0100", "plate", "PL01", "/tmp/a.mov")
        self.table.populate_table([item])

        self.table._tmpl_edit.setText("BG Plate")
        self.table._target_combo.setCurrentText("Media Type")
        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._on_apply_rename()
        self.assertEqual(item.media_type, "BG Plate")

        self.table._apply_case("upper")
        self.assertEqual(item.media_type, "BG PLATE")


class TestTableAuditFixes(unittest.TestCase):
    """
    Audit findings: the toolbar's discard/re-include buttons skipped conflict
    re-detection (only the row checkbox did it), two of the three batch tools
    ignored the scope dropdown next to them, a "New" row's version dropdown
    offered exactly one option so it couldn't change anything, and the
    summary line never counted Missing Details rows.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def _two_conflicting(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        b = _make_item("b", "SQ010", "SH0100", "Plate", "BG", "/tmp/b.mov")
        self.table.populate_table([a, b])
        self.table._run_conflict_detection()
        self.table._refresh_table()
        return a, b

    def test_discard_button_resolves_a_conflict_like_the_checkbox_does(self):
        _a, _b = self._two_conflicting()
        self.assertTrue(self.table.has_unresolved_conflicts())
        self.table._table.selectRow(1)
        self.table._on_discard_selected()
        self.assertFalse(self.table.has_unresolved_conflicts())

    def test_reinclude_button_recreates_the_conflict_it_was_hiding(self):
        _a, b = self._two_conflicting()
        self.table._on_checkbox_changed(id(b), 0)
        self.assertFalse(self.table.has_unresolved_conflicts())
        self.table._table.selectRow(1)
        self.table._on_restore_selected()
        self.assertTrue(self.table.has_unresolved_conflicts())

    def test_scope_dropdown_is_honoured_by_case_and_version_tools(self):
        a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
        b = _make_item("b", "sq020", "sh0200", "plate", "fg", "/tmp/b.mov")
        self.table.populate_table([a, b])
        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._table.selectRow(0)   # a row is selected, but scope says All

        self.table._apply_case("upper")
        self.assertEqual(b.shot_code, "SH0200")

        self.table._batch_version_spin.setValue(7)
        self.table._on_batch_set_version()
        self.assertEqual(self.table.item_version[id(b)], 7)

    def test_selected_scope_with_nothing_selected_touches_nothing(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._scope_combo.setCurrentText("Apply to Selected Rows")
        self.table._table.clearSelection()
        self.table._apply_case("lower")
        self.assertEqual(a.shot_code, "SH0100")   # untouched, not silently "all"

    def test_new_row_version_dropdown_offers_more_than_one_choice(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        combo = self.table._table.cellWidget(0, COL_VERSION)
        self.assertGreater(combo.count(), 1)
        self.assertTrue(combo.currentText().startswith("v001"))

        combo.setCurrentIndex(2)   # pick v003
        self.assertEqual(self.table.item_version[id(a)], 3)

    def test_summary_counts_missing_details_rows(self):
        good = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        bad = _make_item("b", "", "", "", "", "/tmp/b.mov")
        self.table.populate_table([good, bad])
        self.assertIn("1 missing details", self.table._status_lbl.text())


class TestRenameAndVersionRevalidation(unittest.TestCase):
    """
    Two confirmed rename defects. (1) Every batch tool ends in a table
    rebuild, which cleared the selection -- so "Apply to Selected Rows"
    worked for the first click and silently did nothing on the second.
    (2) A row's version number is resolved against its destination folder on
    the NAS; renaming moves that destination, but the old number stayed put,
    so renaming a row onto a shot that already had a v001 left it reading
    "New / v001" and the ingest would have written into the existing version.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    # -- selection survives a rebuild -------------------------------------

    def test_second_batch_action_still_sees_the_same_selection(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        b = _make_item("b", "SQ020", "SH0200", "Plate", "FG", "/tmp/b.mov")
        self.table.populate_table([a, b])
        self.table._table.selectRow(1)
        self.table._scope_combo.setCurrentText("Apply to Selected Rows")

        self.table._tmpl_edit.setText("X")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()

        self.table._tmpl_edit.setText("Y")
        self.table._target_combo.setCurrentText("Media Name")
        self.table._on_apply_rename()

        self.assertEqual((b.shot_code, b.media_name), ("X", "Y"))
        self.assertEqual((a.shot_code, a.media_name), ("SH0100", "BG"))

    def test_multi_row_selection_survives_intact(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        b = _make_item("b", "SQ020", "SH0200", "Plate", "FG", "/tmp/b.mov")
        c = _make_item("c", "SQ030", "SH0300", "Plate", "MG", "/tmp/c.mov")
        self.table.populate_table([a, b, c])

        # Two non-adjacent rows, the way a ctrl-click leaves them.
        self.table._restore_selection({id(a), id(c)})
        self.table._scope_combo.setCurrentText("Apply to Selected Rows")

        self.table._batch_version_spin.setValue(6)
        self.table._on_batch_set_version()      # rebuilds the table
        self.table._batch_version_spin.setValue(8)
        self.table._on_batch_set_version()      # must still see BOTH rows

        self.assertEqual(self.table.item_version[id(a)], 8)
        self.assertEqual(self.table.item_version[id(c)], 8)
        self.assertEqual(self.table.item_version[id(b)], 1)

    # -- version is re-resolved when the destination moves -----------------

    def _watch_revalidation(self):
        seen = []
        self.table.revalidation_requested.connect(lambda items: seen.append(list(items)))
        return seen

    def test_rename_requests_a_fresh_nas_check_and_holds_the_row_back(self):
        a = _make_item("a", "SQ099", "SH9900", "Plate", "XX", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})
        self.assertTrue(self.table.get_valid_ingest_items())

        seen = self._watch_revalidation()
        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._tmpl_edit.setText("SH0100")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()

        self.assertEqual([i.name for i in seen[0]], ["a"])
        self.assertEqual(self.table._effective_status(a), STATUS_CHECKING)
        # Stale version must not reach the ingest worker.
        self.assertEqual(self.table.get_valid_ingest_items(), [])

        # Results land: the row comes back with the version for its NEW slot.
        self.table.apply_version_results({id(a): (2, False)})
        self.assertEqual(self.table.get_valid_ingest_items(), [(a, 2)])

    def test_hand_edited_cell_revalidates_too(self):
        a = _make_item("a", "SQ099", "SH9900", "Plate", "XX", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})
        seen = self._watch_revalidation()

        self.table._table.item(0, COL_SHOT).setText("SH0100")

        self.assertEqual([i.name for i in seen[0]], ["a"])
        self.assertEqual(self.table.get_valid_ingest_items(), [])

    def test_rename_that_changes_nothing_does_not_trigger_a_recheck(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})
        seen = self._watch_revalidation()

        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._tmpl_edit.setText("SH0100")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()

        self.assertEqual(seen, [])
        self.assertEqual(self.table.get_valid_ingest_items(), [(a, 1)])

    def test_case_fold_also_revalidates(self):
        a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})
        seen = self._watch_revalidation()

        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._apply_case("upper")

        self.assertEqual([i.name for i in seen[0]], ["a"])

    def test_every_documented_rename_token_resolves(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.item_version[id(a)] = 4
        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._target_combo.setCurrentText("Media Type")

        tokens = ["{project}", "{seq}", "{shot}", "{media_name}",
                  "{media_type}", "{original}", "{date}", "{version}"]
        for token in tokens:
            self.table._tmpl_edit.setText(token)
            self.table._on_apply_rename()
            self.assertNotEqual(a.media_type, token, f"{token} was left as literal text")

        # And the retired name really is gone -- {plate} was renamed to
        # {media_name}, so it must read as plain text, not silently expand.
        self.table._tmpl_edit.setText("{plate}")
        self.table._on_apply_rename()
        self.assertEqual(a.media_type, "{plate}")


class TestKitsuPreflightCheck(unittest.TestCase):
    """
    "Check in Kitsu" wires KitsuClient.check_shots() into the table: a shot
    that already exists in Kitsu under a different sequence must block
    ingest exactly like a within-table conflict does, since ingesting would
    otherwise create a duplicate shot or attach media to the wrong one.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_wrong_sequence_finding_blocks_ingest_like_a_conflict(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})
        self.assertTrue(self.table.get_valid_ingest_items())

        self.table.apply_kitsu_check({
            ("SQ010", "SH0100"): {
                "state": "wrong_sequence",
                "message": "Kitsu has 'SH0100' under sequence 'SQ099', not 'SQ010'.",
            }
        })

        self.assertEqual(self.table._effective_status(a), STATUS_CONFLICT)
        self.assertTrue(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.get_valid_ingest_items(), [])
        self.assertEqual(self.table.kitsu_conflict_count(), 1)

    def test_new_shot_finding_is_informational_and_does_not_block(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})

        self.table.apply_kitsu_check({
            ("SQ010", "SH0100"): {"state": "new_shot", "message": "will be created"}
        })

        self.assertFalse(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.get_valid_ingest_items(), [(a, 1)])
        self.assertEqual(self.table.kitsu_conflict_count(), 0)

    def test_discarding_the_flagged_row_clears_the_gate(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_kitsu_check({
            ("SQ010", "SH0100"): {"state": "ambiguous", "message": "exists under several sequences"}
        })
        self.assertTrue(self.table.has_unresolved_conflicts())

        self.table._table.selectRow(0)
        self.table._on_discard_selected()
        self.assertFalse(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.kitsu_conflict_count(), 0)

    def test_renaming_the_row_clears_the_stale_finding(self):
        # The finding was about the shot this row used to point at; after a
        # rename it must not go on blocking ingest for a slot it no longer
        # occupies.
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (1, False)})
        self.table.apply_kitsu_check({
            ("SQ010", "SH0100"): {"state": "wrong_sequence", "message": "wrong sequence"}
        })
        self.assertTrue(self.table.has_unresolved_conflicts())

        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._tmpl_edit.setText("SH0200")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()

        self.assertNotIn(id(a), self.table.kitsu_issues)
        self.assertFalse(self.table.has_unresolved_conflicts())

    def test_status_tooltip_carries_the_kitsu_message(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_kitsu_check({
            ("SQ010", "SH0100"): {"state": "wrong_sequence", "message": "distinctive-message-xyz"}
        })
        cell = self.table._table.item(0, COL_STATUS)
        self.assertIn("distinctive-message-xyz", cell.toolTip())


class TestBatchUndo(unittest.TestCase):
    """
    Undo for the three batch tools that can silently trash many rows in one
    click (Apply Rename, ALL CAPS/lowercase, Set Version). Each restores not
    just the text fields but the version/status/pending-revalidation/Kitsu
    state that went with them, so undoing a rename that had knocked a row
    back to "Checking..." puts it back exactly as resolved, not stuck
    re-querying a slot it no longer occupies.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")
        self.table._scope_combo.setCurrentText("Apply to All Rows")

    def test_undo_button_starts_disabled(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.assertFalse(self.table._undo_btn.isEnabled())

    def test_undo_reverts_a_rename(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])

        self.table._tmpl_edit.setText("SH0200")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()
        self.assertEqual(a.shot_code, "SH0200")
        self.assertTrue(self.table._undo_btn.isEnabled())

        self.table._on_undo()
        self.assertEqual(a.shot_code, "SH0100")
        self.assertFalse(self.table._undo_btn.isEnabled())

    def test_multiple_renames_undo_one_step_at_a_time(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._target_combo.setCurrentText("Media Name")

        for value in ("V1", "V2", "V3"):
            self.table._tmpl_edit.setText(value)
            self.table._on_apply_rename()
        self.assertEqual(a.media_name, "V3")

        self.table._on_undo()
        self.assertEqual(a.media_name, "V2")
        self.table._on_undo()
        self.assertEqual(a.media_name, "V1")
        self.table._on_undo()
        self.assertEqual(a.media_name, "BG")   # back to the original

        # Stack is now empty -- a further click must be a safe no-op.
        self.table._on_undo()
        self.assertEqual(a.media_name, "BG")

    def test_undo_after_case_fold_and_set_version_share_one_stack(self):
        # ALL CAPS now only touches the field Target has picked (see
        # TestCaseFoldRespectsTarget below), so this exercises Shot alone.
        a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.item_version[id(a)] = 1
        self.table._target_combo.setCurrentText("Shot")

        self.table._apply_case("upper")
        self.assertEqual(a.shot_code, "SH0100")

        self.table._batch_version_spin.setValue(9)
        self.table._on_batch_set_version()
        self.assertEqual(self.table.item_version[id(a)], 9)

        self.table._on_undo()   # undoes Set Version
        self.assertEqual(self.table.item_version[id(a)], 1)
        self.table._on_undo()   # undoes ALL CAPS
        self.assertEqual(a.shot_code, "sh0100")

    def test_undo_restores_the_resolved_version_not_just_the_text(self):
        # Renaming a row onto a different slot knocks it back to
        # "Checking..." pending a fresh NAS lookup (see the revalidation
        # tests above). Undo must put back the ALREADY-RESOLVED version and
        # status it had, not leave it stuck pending a check for a slot it no
        # longer points at.
        a = _make_item("a", "SQ099", "SH9900", "Plate", "XX", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (3, False)})
        self.assertEqual(self.table.get_valid_ingest_items(), [(a, 3)])

        self.table._tmpl_edit.setText("SH0100")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()
        self.assertEqual(self.table.get_valid_ingest_items(), [])   # pending, held back

        self.table._on_undo()
        self.assertEqual(a.shot_code, "SH9900")
        self.assertEqual(self.table.item_version[id(a)], 3)
        self.assertEqual(self.table.get_valid_ingest_items(), [(a, 3)])

    def test_undo_restores_a_kitsu_finding_the_rename_had_cleared(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_kitsu_check({
            ("SQ010", "SH0100"): {"state": "wrong_sequence", "message": "wrong seq"}
        })
        self.assertTrue(self.table.has_unresolved_conflicts())

        self.table._tmpl_edit.setText("SH0200")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()
        self.assertFalse(self.table.has_unresolved_conflicts())   # moved off the flagged slot

        self.table._on_undo()
        self.assertTrue(self.table.has_unresolved_conflicts())
        self.assertIn(id(a), self.table.kitsu_issues)

    def test_selected_scope_with_nothing_selected_pushes_no_undo_entry(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._scope_combo.setCurrentText("Apply to Selected Rows")
        self.table._table.clearSelection()

        self.table._tmpl_edit.setText("X")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()

        self.assertEqual(a.shot_code, "SH0100")
        self.assertEqual(len(self.table._undo_stack), 0)
        self.assertFalse(self.table._undo_btn.isEnabled())

    def test_blank_template_pushes_no_undo_entry(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._tmpl_edit.setText("   ")
        self.table._on_apply_rename()
        self.assertEqual(len(self.table._undo_stack), 0)

    def test_loading_a_new_batch_clears_the_undo_history(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._tmpl_edit.setText("X")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()
        self.assertEqual(len(self.table._undo_stack), 1)

        b = _make_item("b", "SQ020", "SH0200", "Plate", "FG", "/tmp/b.mov")
        self.table.populate_table([b])
        self.assertEqual(len(self.table._undo_stack), 0)
        self.assertFalse(self.table._undo_btn.isEnabled())

    def test_undo_history_is_capped(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._target_combo.setCurrentText("Media Name")
        for i in range(25):
            self.table._tmpl_edit.setText(f"V{i}")
            self.table._on_apply_rename()
        self.assertEqual(len(self.table._undo_stack), 20)


class TestCaseFoldRespectsTarget(unittest.TestCase):
    """
    ALL CAPS / lowercase used to unconditionally touch all four fields
    (Shot, Sequence, Media Type, Media Name) no matter what the Target
    dropdown next to them was set to -- picking "Shot" and clicking ALL
    CAPS silently upper-cased the other three fields too. It now dispatches
    on Target exactly like Apply Rename does.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")
        self.table._scope_combo.setCurrentText("Apply to All Rows")

    def test_only_the_targeted_field_changes(self):
        a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._target_combo.setCurrentText("Shot")
        self.table._apply_case("upper")
        self.assertEqual(a.shot_code, "SH0100")
        self.assertEqual((a.sequence_code, a.media_type, a.media_name), ("sq010", "plate", "bg"))

    def test_switching_target_switches_which_field_case_folds(self):
        a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table._target_combo.setCurrentText("Media Name")
        self.table._apply_case("upper")
        self.assertEqual(a.media_name, "BG")
        self.assertEqual(a.shot_code, "sh0100")   # untouched this time

    def test_each_of_the_four_targets_is_wired_up(self):
        for target, field in [
            ("Shot", "shot_code"), ("Sequence", "sequence_code"),
            ("Media Type", "media_type"), ("Media Name", "media_name"),
        ]:
            a = _make_item("a", "sq010", "sh0100", "plate", "bg", "/tmp/a.mov")
            self.table.populate_table([a])
            self.table._target_combo.setCurrentText(target)
            self.table._apply_case("upper")
            self.assertTrue(getattr(a, field).isupper(), f"{target} did not fold {field}")


class TestVersionDropdownAnchoring(unittest.TestCase):
    """
    The dropdown's offered range (v001..detected+3) must anchor to the
    version the NAS check resolved, not to whatever is currently selected --
    anchoring to the live selection meant every pick became the next
    rebuild's "current", so the option list grew by 3 more entries every
    time the dropdown was used.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_picking_the_top_option_does_not_grow_the_list(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (2, False)})
        combo = self.table._table.cellWidget(0, COL_VERSION)
        self.assertEqual(combo.count(), 5)   # v001..v005

        combo.setCurrentIndex(combo.count() - 1)   # pick v005, the top option
        combo_after = self.table._table.cellWidget(0, COL_VERSION)
        self.assertEqual(combo_after.count(), 5, "range grew after a pick instead of staying anchored")

    def test_a_fresh_nas_check_moves_the_anchor(self):
        # The anchor SHOULD move when a genuinely new NAS result lands --
        # only a user pick from the existing list must not move it.
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (2, False)})
        self.assertEqual(self.table._table.cellWidget(0, COL_VERSION).count(), 5)

        self.table.apply_version_results({id(a): (6, False)})
        self.assertEqual(self.table._table.cellWidget(0, COL_VERSION).count(), 9)   # v001..v009


class TestVersionRollbackConflict(unittest.TestCase):
    """
    A row's version can be moved down -- per-row dropdown or batch Set
    Version -- below the version the NAS check resolved. That lower number
    already has a folder on the NAS (the resolved version IS "latest
    existing + 1"), and neither control re-verifies the NAS before ingest,
    so silently allowing it would write into an existing version. It must
    read as a conflict, the same as a duplicate destination within the
    table, and block ingest the same way.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_per_row_dropdown_rollback_is_flagged_and_blocked(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (3, False)})
        self.assertEqual(self.table.get_valid_ingest_items(), [(a, 3)])

        combo = self.table._table.cellWidget(0, COL_VERSION)
        v1_index = [combo.itemText(i).split()[0] for i in range(combo.count())].index("v001")
        combo.setCurrentIndex(v1_index)

        self.assertEqual(self.table._effective_status(a), STATUS_CONFLICT)
        self.assertTrue(self.table.has_unresolved_conflicts())
        self.assertEqual(self.table.get_valid_ingest_items(), [])
        tip = self.table._table.item(0, COL_STATUS).toolTip()
        self.assertIn("v001", tip)
        self.assertIn("v003", tip)

    def test_batch_set_version_rollback_is_flagged_too(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (5, False)})

        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._batch_version_spin.setValue(2)
        self.table._on_batch_set_version()

        self.assertEqual(self.table._effective_status(a), STATUS_CONFLICT)

    def test_moving_back_up_to_or_past_detected_clears_it(self):
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (5, False)})
        self.table._scope_combo.setCurrentText("Apply to All Rows")

        self.table._batch_version_spin.setValue(2)
        self.table._on_batch_set_version()
        self.assertEqual(self.table._effective_status(a), STATUS_CONFLICT)

        self.table._batch_version_spin.setValue(5)
        self.table._on_batch_set_version()
        self.assertNotEqual(self.table._effective_status(a), STATUS_CONFLICT)

    def test_a_fresh_row_never_flagged_before_any_nas_check(self):
        # detected_version defaults to 1, same as item_version -- must not
        # spuriously conflict before check_all_media has even run.
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.assertNotEqual(self.table._effective_status(a), STATUS_CONFLICT)

    def test_undo_restores_the_anchor_along_with_everything_else(self):
        # A rename mid-flight can pick up a fresh NAS result for the new slot
        # before it's undone; undoing must put the OLD anchor back too, or
        # the restored row would misjudge a rollback against the wrong slot's
        # detected version.
        a = _make_item("a", "SQ010", "SH0100", "Plate", "BG", "/tmp/a.mov")
        self.table.populate_table([a])
        self.table.apply_version_results({id(a): (3, False)})   # old slot: detected=3

        self.table._scope_combo.setCurrentText("Apply to All Rows")
        self.table._tmpl_edit.setText("SH9900")
        self.table._target_combo.setCurrentText("Shot")
        self.table._on_apply_rename()
        self.table.apply_version_results({id(a): (1, False)})   # new slot resolves: detected=1

        self.table._on_undo()
        self.assertEqual(self.table.item_detected_version[id(a)], 3)
        self.assertEqual(self.table.item_version[id(a)], 3)


class TestRowHeight(unittest.TestCase):
    """
    Row height used to be whatever Qt auto-sized it to from the tallest
    cell widget's own sizeHint (the Version combo, the progress bar) --
    which varies by platform/DPI/style, and on the reported setup came out
    visibly taller than the row around it. It is now fixed explicitly.
    """

    def setUp(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.table = IngestTableWidget()
        self.table.set_project_code("PROJ")

    def test_rows_are_a_uniform_fixed_height(self):
        items = [
            _make_item(f"i{i}", "SQ010", f"SH0{i}00", "Plate", "BG", f"/tmp/i{i}.mov")
            for i in range(4)
        ]
        self.table.populate_table(items)
        heights = {self.table._table.rowHeight(r) for r in range(4)}
        self.assertEqual(len(heights), 1)
        self.assertEqual(self.table._table.verticalHeader().defaultSectionSize(), ROW_HEIGHT)


if __name__ == "__main__":
    unittest.main()
