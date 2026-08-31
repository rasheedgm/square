"""
IngestTableWidget v2 — Full overhaul with:
- Checkbox discard column
- Version dropdown per row
- Colour-coded status pills
- Conflict detection (within-table and against Kitsu)
- Batch rename toolbar with template wildcards
- Background NAS duplicate check integration
"""

from Qt import QtWidgets, QtCore, QtGui
from tools.qt_compat import (
    ITEM_IS_SELECTABLE, ITEM_IS_ENABLED,
    HEADER_RESIZE_INTERACTIVE, HEADER_RESIZE_FIXED, SELECT_ROWS, ALIGN_CENTER,
    SELECTION_SELECT, SELECTION_ROWS
)

# A row with no cell widgets sizes itself from plain text; the moment ANY row
# gets a combo box or progress bar (every row does -- Version, Progress), an
# unconstrained default lets that widget's own sizeHint (which varies by
# platform/DPI/style) dictate the row height instead. Pinning it keeps every
# row -- and everything in it -- a fixed, predictable size.
ROW_HEIGHT = 28

# ── Status constants ──
STATUS_NEW             = "New"
STATUS_ALREADY         = "Already Ingested"
STATUS_NEW_VERSION     = "New Version"
STATUS_CONFLICT        = "Conflict"
STATUS_MISSING_FRAMES  = "Missing Frames"
STATUS_MISSING_DETAILS = "Missing Details"
STATUS_DISCARDED       = "Discarded"
STATUS_CHECKING        = "Checking..."

STATUS_COLOURS = {
    STATUS_NEW:             ("#065F46", "#D1FAE5"),   # green
    STATUS_ALREADY:         ("#92400E", "#FEF3C7"),   # amber
    STATUS_NEW_VERSION:     ("#1E3A8A", "#DBEAFE"),   # blue
    STATUS_CONFLICT:        ("#7F1D1D", "#FEE2E2"),   # red
    STATUS_MISSING_FRAMES:  ("#78350F", "#FFF7ED"),   # orange
    STATUS_MISSING_DETAILS: ("#991B1B", "#FEE2E2"),   # dark red alert
    STATUS_DISCARDED:       ("#374151", "#F3F4F6"),   # grey
    STATUS_CHECKING:        ("#1F2937", "#F9FAFB"),   # dark grey
}

# Column indices
COL_INCLUDE    = 0
COL_SRC_NAME   = 1
COL_SEQ        = 2
COL_SHOT       = 3
COL_TYPE       = 4
COL_MEDIA_NAME = 5
COL_EXTRA      = 6
COL_DEST       = 7
COL_FRAMES     = 8
COL_FPS        = 9
COL_RES        = 10
COL_CS         = 11
COL_VERSION    = 12
COL_STATUS     = 13
COL_PROGRESS   = 14

HEADERS = [
    "",             # checkbox
    "Source",
    "Sequence",
    "Shot",
    "Media Type",
    "Media Name",
    "Extra Tags",
    "Destination",
    "Frames",
    "FPS",
    "Resolution",
    "Colorspace",
    "Version",
    "Status",
    "Progress",
]

# Ingest pipeline stages, in order, for the per-row progress bar.
STAGE_QUEUED   = "Queued"
STAGE_KITSU    = "Kitsu Sync"
STAGE_COPYING  = "Copying"
STAGE_PROXY    = "Preview"
STAGE_DONE     = "Done"
STAGE_ERROR    = "Error"

STAGE_PERCENT = {
    STAGE_QUEUED:  0,
    STAGE_KITSU:   20,
    STAGE_COPYING: 45,
    STAGE_PROXY:   80,
    STAGE_DONE:    100,
    STAGE_ERROR:   100,
}


class IngestTableWidget(QtWidgets.QWidget):
    """
    Full ingest review panel: batch toolbar + table.
    """

    # Emitted when table items or statuses change (triggers conflict badge & ingest button update)
    table_changed = QtCore.Signal()
    # Emitted when user clicks "Check Conflicts in Kitsu"
    kitsu_conflict_check_requested = QtCore.Signal()
    # Emitted with the items whose destination changed (rename, case fold, or a
    # manual cell edit). Their stored version number was resolved against the
    # OLD destination folder, so it must be re-checked against the NAS before
    # they can be ingested.
    revalidation_requested = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items_data = []          # list of IngestSequenceItem
        self.item_status  = {}        # id(item) -> status string
        self.item_version = {}        # id(item) -> version int
        # id(item) -> version number the last NAS check actually found (the
        # "next available" slot). item_version can move away from this via a
        # manual pick or batch Set Version; this stays put as the anchor for
        # the dropdown's option range and the reference point for detecting
        # a rollback onto an already-used version. Only apply_version_results
        # moves it.
        self.item_detected_version = {}
        self.item_discarded = set()   # id(item) -> discarded
        self.item_progress = {}       # id(item) -> (stage str, percent int) -- live ingest progress
        # id(item) for rows whose destination changed since their version was
        # last resolved against the NAS -- held out of ingest until re-checked.
        self._pending_revalidation = set()
        # id(item) -> {"state", "message", "conflict"} from the last Kitsu
        # pre-flight. Kept apart from item_status so within-table conflict
        # detection, which re-derives statuses, can't quietly erase it.
        self.kitsu_issues = {}
        # Restore points for the batch tools (Apply Rename, ALL CAPS/lowercase,
        # Set Version) -- newest last, capped so a long session can't grow it
        # unbounded. Each entry is {"label", "rows": [per-item snapshot, ...]}.
        self._undo_stack = []
        self._project_code = ""
        self._nas_root = "X:/projects"
        self._filename_template = ""
        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_project_code(self, code: str):
        self._project_code = code

    def set_nas_root(self, nas_root: str):
        self._nas_root = nas_root

    def set_filename_template(self, template: str):
        self._filename_template = template

    def populate_table(self, items):
        """Populate the table with IngestSequenceItem list. Resets all state."""
        self.items_data   = list(items)
        self.item_status  = {id(i): STATUS_CHECKING for i in items}
        self.item_version = {id(i): 1 for i in items}
        self.item_detected_version = {id(i): 1 for i in items}
        self.item_discarded = set()
        self.item_progress = {}
        self._pending_revalidation = set()
        self.kitsu_issues = {}
        # A fresh load starts a new editing session -- nothing to undo into.
        self._undo_stack = []
        self._set_undo_button_state()
        self._refresh_table()

    def update_table(self, new_items):
        """
        Update existing items if present; create new rows for new items;
        keep all unselected/other items already in table.
        """
        existing_map = {}
        for item in self.items_data:
            key_files = tuple(sorted(item.files)) if item.files else (item.name,)
            existing_map[key_files] = item

        for item in new_items:
            key_files = tuple(sorted(item.files)) if item.files else (item.name,)
            if key_files in existing_map:
                # Update existing item metadata
                target = existing_map[key_files]
                target.sequence_code = item.sequence_code
                target.shot_code     = item.shot_code
                target.media_name    = item.media_name
                target.media_type    = getattr(item, "media_type", "Plate")
            else:
                # Create new row for newly loaded item
                self.items_data.append(item)
                k = id(item)
                self.item_status[k]  = STATUS_CHECKING
                self.item_version[k] = 1
                self.item_detected_version[k] = 1
                existing_map[key_files] = item

        self._run_conflict_detection()
        self._refresh_table()

    def update_ingest_progress(self, item, stage: str, percent=None):
        """
        Called from the ingest worker thread (via a queued signal) as each
        item moves through the pipeline (Kitsu Sync -> Copying -> Preview ->
        Done/Error). Updates the row's progress bar directly instead of
        rebuilding the whole table, so it stays smooth across many quick
        per-file ticks.
        """
        key = id(item)
        pct = percent if percent is not None else STAGE_PERCENT.get(stage, 0)
        self.item_progress[key] = (stage, pct)

        row = self._row_for_key(key)
        if row is None:
            return
        bar = self._table.cellWidget(row, COL_PROGRESS)
        if isinstance(bar, QtWidgets.QProgressBar):
            bar.setValue(pct)
            bar.setFormat(f"{stage}  %p%")
            chunk_colour = "#EF4444" if stage == STAGE_ERROR else ("#10B981" if stage == STAGE_DONE else "#3B82F6")
            bar.setStyleSheet(
                "QProgressBar { background:#1A2035; border:none; border-radius:3px; color:#E2E8F0; font-size:10px; }"
                f"QProgressBar::chunk {{ background:{chunk_colour}; border-radius:3px; }}"
            )

    def _row_for_key(self, key):
        for row, item in enumerate(self.items_data):
            if id(item) == key:
                return row
        return None

    def apply_version_results(self, results: dict):
        """
        Called from background thread results.
        results: { id(item): (version_num, is_already_ingested) }
        """
        for item in self.items_data:
            key = id(item)
            if key in results:
                ver, already = results[key]
                self._pending_revalidation.discard(key)
                self.item_version[key] = ver
                self.item_detected_version[key] = ver
                if already:
                    self.item_status[key] = STATUS_ALREADY
                elif ver > 1:
                    self.item_status[key] = STATUS_NEW_VERSION
                elif item.missing_frames:
                    self.item_status[key] = STATUS_MISSING_FRAMES
                else:
                    self.item_status[key] = STATUS_NEW
        self._run_conflict_detection()
        self._refresh_table()

    def apply_kitsu_check(self, report: dict):
        """
        Apply a KitsuClient.check_shots() report, keyed (SEQUENCE, SHOT).

        Only the two states that need a human decision -- the shot already
        living under a different sequence, or under several -- mark the row as
        a conflict. "Will be created" and "matches Kitsu" are recorded for the
        row's tooltip but never block anything.
        """
        from square_core.kitsu_client import KitsuClient

        self.kitsu_issues = {}
        for item in self.items_data:
            key = (
                (item.sequence_code or "").strip().upper(),
                (item.shot_code or "").strip().upper(),
            )
            finding = report.get(key)
            if not finding:
                continue
            self.kitsu_issues[id(item)] = {
                "state": finding.get("state"),
                "message": finding.get("message", ""),
                "conflict": finding.get("state") in KitsuClient.KITSU_CONFLICT_STATES,
            }
        self._run_conflict_detection()
        self._refresh_table()

    def kitsu_conflict_count(self):
        """How many active rows the last Kitsu pre-flight flagged."""
        return sum(
            1 for key, issue in self.kitsu_issues.items()
            if issue.get("conflict") and key not in self.item_discarded
        )

    def has_unresolved_conflicts(self) -> bool:
        # Read through _effective_status so this sees Kitsu pre-flight
        # conflicts too, not just the within-table ones written to item_status.
        return any(
            self._effective_status(item) == STATUS_CONFLICT
            for item in self.items_data
            if id(item) not in self.item_discarded
        )

    def get_valid_ingest_items(self):
        """
        Return (item, version) pairs for rows that are included, not conflicted,
        and have all required fields (Sequence, Shot, Media Type, Name).
        Also syncs any manual edits from the table cells back to items.
        """
        self._sync_edits_from_table()
        result = []
        for item in self.items_data:
            key = id(item)
            if key in self.item_discarded:
                continue
            status = self._effective_status(item)
            seq = (item.sequence_code or "").strip()
            shot = (item.shot_code or "").strip()
            mtype = (getattr(item, "media_type", "") or "").strip()
            name = (item.media_name or "").strip()

            if not (seq and shot and mtype and name):
                continue
            if status in (STATUS_ALREADY, STATUS_DISCARDED, STATUS_CONFLICT, STATUS_MISSING_DETAILS):
                continue
            # Its destination moved after the last NAS lookup, so the version
            # it holds belongs to a different folder -- ingesting now could
            # write on top of an existing version.
            if key in self._pending_revalidation:
                continue
            result.append((item, self.item_version.get(key, 1)))
        return result

    def get_selected_items(self):
        """
        Return (item, version) pairs for rows that are included and not conflicted.
        Also syncs any manual edits from the table cells back to items.
        """
        self._sync_edits_from_table()
        result = []
        for item in self.items_data:
            key = id(item)
            if key in self.item_discarded:
                continue
            status = self._effective_status(item)
            if status in (STATUS_ALREADY, STATUS_DISCARDED):
                continue
            if status == STATUS_CONFLICT:
                continue
            result.append((item, self.item_version.get(key, 1)))
        return result

    def rowCount(self):
        return self._table.rowCount()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(self._build_toolbar())

        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(HEADER_RESIZE_INTERACTIVE)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(SELECT_ROWS)
        self._table.setAlternatingRowColors(True)
        self._table.setColumnWidth(COL_INCLUDE, 28)
        self._table.setColumnWidth(COL_SRC_NAME, 160)
        self._table.setColumnWidth(COL_SEQ, 75)
        self._table.setColumnWidth(COL_SHOT, 80)
        self._table.setColumnWidth(COL_TYPE, 85)
        self._table.setColumnWidth(COL_MEDIA_NAME, 90)
        self._table.setColumnWidth(COL_EXTRA, 140)
        self._table.setColumnWidth(COL_DEST, 220)
        self._table.setColumnWidth(COL_FRAMES, 130)
        self._table.setColumnWidth(COL_FPS, 45)
        self._table.setColumnWidth(COL_RES, 90)
        self._table.setColumnWidth(COL_CS, 80)
        self._table.setColumnWidth(COL_VERSION, 120)
        self._table.setColumnWidth(COL_PROGRESS, 150)

        # Fixed row height so the Version combo / progress bar can't stretch
        # a row to their own sizeHint -- they get resized DOWN to fit the row
        # instead, the same way every other cell already does.
        row_header = self._table.verticalHeader()
        row_header.setDefaultSectionSize(ROW_HEIGHT)
        row_header.setSectionResizeMode(HEADER_RESIZE_FIXED)
        layout.addWidget(self._table, stretch=1)
        # Re-check conflicts whenever the user edits a cell
        self._table.itemChanged.connect(self._on_cell_edited)

        # Status bar
        self._status_lbl = QtWidgets.QLabel("")
        self._status_lbl.setStyleSheet("color:#94A3B8; font-size:11px; padding:2px 4px;")
        layout.addWidget(self._status_lbl)

    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QFrame()
        bar.setStyleSheet("background:#1E293B; border-radius:4px; padding:2px;")
        bar_layout = QtWidgets.QHBoxLayout(bar)
        bar_layout.setContentsMargins(6, 4, 6, 4)
        bar_layout.setSpacing(6)

        # Template input
        self._tmpl_edit = QtWidgets.QLineEdit()
        self._tmpl_edit.setPlaceholderText("Rename template: {seq}_{shot} or {original}")
        # "Wildcard" now means the * in a Path Pattern, so these are called
        # tokens here to keep the two mechanisms apart.
        self._tmpl_edit.setToolTip(
            "Tokens, replaced per row:\n"
            "  {project}     project code\n"
            "  {seq}         this row's Sequence\n"
            "  {shot}        this row's Shot\n"
            "  {media_type}  this row's Media Type\n"
            "  {media_name}  this row's Media Name\n"
            "  {original}    source name as scanned\n"
            "  {version}     current version, e.g. v003\n"
            "  {date}        today, YYYYMMDD\n"
            "Anything else is written literally.\n"
            "Writes into the column picked beside this box, for the rows the\n"
            "scope dropdown selects. The destination filename itself comes\n"
            "from Settings > File Naming Pattern, not from here."
        )
        self._tmpl_edit.setMinimumWidth(220)

        # Apply scope dropdown
        self._scope_combo = QtWidgets.QComboBox()
        self._scope_combo.addItems(["Apply to All Rows", "Apply to Selected Rows"])

        # Which column to rename
        self._target_combo = QtWidgets.QComboBox()
        self._target_combo.addItems(["Shot", "Media Name", "Sequence", "Media Type"])

        apply_btn = QtWidgets.QPushButton("Apply Rename")
        apply_btn.setStyleSheet("background:#2563EB; color:white; font-weight:bold; padding:4px 10px;")
        apply_btn.clicked.connect(self._on_apply_rename)

        bar_layout.addWidget(QtWidgets.QLabel("Template:"))
        bar_layout.addWidget(self._tmpl_edit)
        bar_layout.addWidget(self._target_combo)
        bar_layout.addWidget(self._scope_combo)
        bar_layout.addWidget(apply_btn)

        bar_layout.addSpacing(10)

        # Quick buttons
        case_tip = "Case-folds the field picked above (Target), for the rows Scope selects -- same two dropdowns Apply Rename uses."
        caps_btn = QtWidgets.QPushButton("ALL CAPS")
        caps_btn.setToolTip(case_tip)
        caps_btn.clicked.connect(lambda: self._apply_case("upper"))
        lower_btn = QtWidgets.QPushButton("lowercase")
        lower_btn.setToolTip(case_tip)
        lower_btn.clicked.connect(lambda: self._apply_case("lower"))

        bar_layout.addWidget(caps_btn)
        bar_layout.addWidget(lower_btn)

        bar_layout.addSpacing(10)

        # Batch version control -- version is a number with its own per-row
        # dropdown, so it gets a dedicated "set to N" control rather than
        # being shoved through the free-text rename template.
        self._batch_version_spin = QtWidgets.QSpinBox()
        self._batch_version_spin.setRange(1, 999)
        self._batch_version_spin.setPrefix("v")
        self._batch_version_spin.setToolTip("Batch-set the version number for all/selected rows")
        set_version_btn = QtWidgets.QPushButton("Set Version")
        set_version_btn.clicked.connect(self._on_batch_set_version)
        bar_layout.addWidget(self._batch_version_spin)
        bar_layout.addWidget(set_version_btn)

        bar_layout.addSpacing(6)

        self._undo_btn = QtWidgets.QPushButton("Undo")
        self._undo_btn.setEnabled(False)
        self._undo_btn.setToolTip("Nothing to undo")
        self._undo_btn.clicked.connect(self._on_undo)
        bar_layout.addWidget(self._undo_btn)

        bar_layout.addSpacing(10)

        discard_btn = QtWidgets.QPushButton("Discard Selected")
        discard_btn.setStyleSheet("background:#7F1D1D; color:white; padding:4px 8px;")
        discard_btn.clicked.connect(self._on_discard_selected)

        restore_btn = QtWidgets.QPushButton("Re-include Selected")
        restore_btn.clicked.connect(self._on_restore_selected)

        bar_layout.addWidget(discard_btn)
        bar_layout.addWidget(restore_btn)

        bar_layout.addSpacing(10)

        kitsu_btn = QtWidgets.QPushButton("Check in Kitsu")
        kitsu_btn.setToolTip(
            "Look every row's Sequence / Shot up in Kitsu before ingesting.\n"
            "Flags shots that already exist under a different sequence, which\n"
            "would otherwise create a duplicate shot. Hover a row's Status for\n"
            "the finding."
        )
        kitsu_btn.clicked.connect(self.kitsu_conflict_check_requested.emit)
        bar_layout.addWidget(kitsu_btn)

        return bar

    # ------------------------------------------------------------------
    # Table Population
    # ------------------------------------------------------------------

    def _refresh_table(self):
        # Rebuilding the rows wipes the selection, and every batch tool ends
        # with a refresh -- so "Apply to Selected Rows" only ever worked for
        # the first click; the second silently acted on an empty selection.
        # Selection is remembered by item identity and restored below.
        selected_keys = {id(i) for i in self._get_selected_items_in_table()}

        self._table.blockSignals(True)
        self._table.setRowCount(0)

        for row_idx, item in enumerate(self.items_data):
            key = id(item)
            ver    = self.item_version.get(key, 1)
            discarded = key in self.item_discarded
            status = self._effective_status(item)

            self._table.insertRow(row_idx)

            # ── Checkbox ──
            chk = QtWidgets.QCheckBox()
            chk.setChecked(not discarded)
            chk.stateChanged.connect(lambda state, k=key: self._on_checkbox_changed(k, state))
            cell_w = QtWidgets.QWidget()
            cell_l = QtWidgets.QHBoxLayout(cell_w)
            cell_l.addWidget(chk)
            cell_l.setAlignment(ALIGN_CENTER)
            cell_l.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(row_idx, COL_INCLUDE, cell_w)

            # ── Source name ──
            src = self._mk_cell(item.name, editable=False)
            src.setToolTip("\n".join(item.files[:5]) + ("\n..." if len(item.files) > 5 else ""))
            self._table.setItem(row_idx, COL_SRC_NAME, src)

            # ── Editable columns ──
            self._table.setItem(row_idx, COL_SEQ,        self._mk_cell(item.sequence_code or ""))
            self._table.setItem(row_idx, COL_SHOT,       self._mk_cell(item.shot_code or ""))
            self._table.setItem(row_idx, COL_TYPE,       self._mk_cell(getattr(item, "media_type", "") or ""))
            self._table.setItem(row_idx, COL_MEDIA_NAME, self._mk_cell(getattr(item, "media_name", "") or ""))

            # ── Extra Tags (read-only) -- anything a Path Pattern captured that
            # isn't one of the 5 built-in fields (camera, shoot date, colorspace...) ──
            extra_tags = getattr(item, "extra_tags", None) or {}
            extra_text = ", ".join(f"{k}={v}" for k, v in extra_tags.items())
            extra_cell = self._mk_cell(extra_text, editable=False)
            if extra_text:
                extra_cell.setToolTip(extra_text)
            self._table.setItem(row_idx, COL_EXTRA, extra_cell)

            # ── Destination cell (Filename in cell, full destination path in tooltip) ──
            from square_core.config import DEFAULT_FILE_NAME_TEMPLATE, format_dest_filename
            from square_core.nas_manager import NASManager

            tmpl = self._filename_template or DEFAULT_FILE_NAME_TEMPLATE
            mtype = (getattr(item, "media_type", "") or "").strip()
            mname = (getattr(item, "media_name", "") or "").strip()
            frame_sample = "####" if not item.is_video else None
            dest_filename = format_dest_filename(
                tmpl, self._project_code or "PROJ", item.sequence_code,
                item.shot_code, mtype, media_name=mname, version_num=ver,
                frame=frame_sample, ext=item.ext
            )
            dest_dir = NASManager(nas_root=self._nas_root).get_dest_dir(
                self._project_code or "PROJ", item.sequence_code,
                item.shot_code, mname, version=ver,
                media_type=mtype, resolution=item.resolution
            )
            full_dest_path = str(dest_dir / dest_filename)

            dest_cell = self._mk_cell(dest_filename, editable=False)
            dest_cell.setToolTip(full_dest_path)
            dest_cell.setForeground(QtGui.QColor("#38BDF8"))
            self._table.setItem(row_idx, COL_DEST, dest_cell)

            # ── Read-only info ──
            self._table.setItem(row_idx, COL_FRAMES, self._mk_cell(item.frame_range_str, editable=False))
            self._table.setItem(row_idx, COL_FPS,    self._mk_cell(str(item.fps), editable=False))
            self._table.setItem(row_idx, COL_RES,    self._mk_cell(item.resolution, editable=False))
            self._table.setItem(row_idx, COL_CS,     self._mk_cell(item.colorspace, editable=False))

            # ── Version dropdown ──
            ver_combo = QtWidgets.QComboBox()
            self._populate_version_combo(ver_combo, ver, status, self.item_detected_version.get(key, 1))
            ver_combo.currentIndexChanged.connect(
                lambda idx, cb=ver_combo, k=key: self._on_version_changed(k, cb)
            )
            self._table.setCellWidget(row_idx, COL_VERSION, ver_combo)

            # ── Status pill (Kitsu finding or version-rollback, if any, in the tooltip) ──
            status_cell = self._mk_status_cell(status)
            issue = self.kitsu_issues.get(key)
            rollback_msg = self._version_rollback_message(item)
            if issue and issue.get("message"):
                status_cell.setToolTip(issue["message"])
            elif rollback_msg:
                status_cell.setToolTip(rollback_msg)
            self._table.setItem(row_idx, COL_STATUS, status_cell)

            # ── Live ingest progress bar ──
            stage, percent = self.item_progress.get(key, (STAGE_QUEUED, 0))
            self._table.setCellWidget(row_idx, COL_PROGRESS, self._mk_progress_bar(stage, percent))

            # ── Row-level dim/highlight ──
            self._style_row(row_idx, status, discarded)

        self._table.blockSignals(False)
        self._restore_selection(selected_keys)
        self._update_status_bar()
        self.table_changed.emit()

    def _effective_status(self, item):
        """
        The status actually shown for a row. Discarded wins over everything;
        a row missing any required field reads as Missing Details regardless
        of what the NAS/Kitsu check said. Both the rows and the summary line
        go through this, so the counts can't drift from what's on screen.
        """
        key = id(item)
        if key in self.item_discarded:
            return STATUS_DISCARDED
        required = (
            (item.sequence_code or "").strip(),
            (item.shot_code or "").strip(),
            (getattr(item, "media_type", "") or "").strip(),
            (item.media_name or "").strip(),
        )
        if not all(required):
            return STATUS_MISSING_DETAILS
        # A Kitsu pre-flight conflict outranks the NAS verdict: the row may be
        # a perfectly good "New Version" on disk and still be pointed at the
        # wrong shot in Kitsu.
        issue = self.kitsu_issues.get(key)
        if issue and issue.get("conflict"):
            return STATUS_CONFLICT
        if self._version_rollback_message(item):
            return STATUS_CONFLICT
        return self.item_status.get(key, STATUS_NEW)

    def _version_rollback_message(self, item):
        """
        Set when this row's version was manually moved (per-row dropdown or
        batch Set Version) below the version the last NAS check resolved.
        That lower number already has a folder on the NAS -- the NAS check
        only ever verifies the version it auto-picks, so rolling back to an
        earlier one bypasses that check entirely and would silently write
        into an existing version.
        """
        key = id(item)
        detected = self.item_detected_version.get(key)
        chosen = self.item_version.get(key, 1)
        if detected is not None and chosen < detected:
            return (
                f"v{chosen:03d} already exists on the NAS (next available is "
                f"v{detected:03d}). Ingesting now would write into that existing version."
            )
        return None

    def _mk_cell(self, text, editable=True):
        cell = QtWidgets.QTableWidgetItem(str(text))
        if not editable:
            cell.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
        return cell

    def _mk_progress_bar(self, stage, percent):
        bar = QtWidgets.QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(percent)
        bar.setFormat(f"{stage}  %p%")
        bar.setTextVisible(True)
        chunk_colour = "#EF4444" if stage == STAGE_ERROR else ("#10B981" if stage == STAGE_DONE else "#3B82F6")
        bar.setStyleSheet(
            "QProgressBar { background:#1A2035; border:none; border-radius:3px; color:#E2E8F0; font-size:10px; }"
            f"QProgressBar::chunk {{ background:{chunk_colour}; border-radius:3px; }}"
        )
        return bar

    def _mk_status_cell(self, status):
        cell = QtWidgets.QTableWidgetItem(status)
        cell.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
        bg, fg = STATUS_COLOURS.get(status, ("#374151", "#F9FAFB"))
        cell.setBackground(QtGui.QBrush(QtGui.QColor(bg)))
        cell.setForeground(QtGui.QBrush(QtGui.QColor(fg)))
        return cell

    def _populate_version_combo(self, combo, ver_num, status, detected_ver_num=1):
        """
        Every row offers v001 up to a few past its NAS-DETECTED version, with
        the currently chosen one selected and annotated. The offered range is
        anchored to detected_ver_num (what the last NAS check actually found),
        not to ver_num (today's live selection) -- anchoring to the live
        selection meant every pick became next rebuild's "current", so the
        list grew by 3 more entries each time the dropdown was used. A "New"
        row used to offer exactly one entry, so its own dropdown couldn't
        change the version at all -- only the batch Set Version control could.
        """
        combo.clear()
        if status == STATUS_ALREADY:
            note = "exists — skip"
        elif status == STATUS_NEW_VERSION:
            note = "new version"
        elif status == STATUS_CONFLICT:
            note = "conflict"
        else:
            note = "new"

        current = max(int(ver_num or 1), 1)
        anchor  = max(int(detected_ver_num or 1), 1)
        # Union with {current} so a value picked from outside the normal
        # range (e.g. a batch Set Version jump) still shows up as selected
        # instead of silently snapping to the nearest in-range option.
        options = sorted(set(range(1, anchor + 4)) | {current})
        for v in options:
            combo.addItem(f"v{v:03d}  ({note})" if v == current else f"v{v:03d}")
        combo.setCurrentIndex(options.index(current))

    def _style_row(self, row_idx, status, discarded):
        dim = discarded or status == STATUS_ALREADY
        col_count = self._table.columnCount()
        for col in range(col_count):
            widget = self._table.cellWidget(row_idx, col)
            cell   = self._table.item(row_idx, col)
            if dim and cell and col not in (COL_STATUS, COL_INCLUDE):
                cell.setForeground(QtGui.QBrush(QtGui.QColor("#4B5563")))
            if status == STATUS_CONFLICT and cell and col in (COL_SEQ, COL_SHOT):
                cell.setBackground(QtGui.QBrush(QtGui.QColor("#450A0A")))

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------

    def _on_checkbox_changed(self, key, state):
        if state == 0:  # unchecked
            self.item_discarded.add(key)
        else:
            self.item_discarded.discard(key)
        # Discarding/restoring a row can resolve (or re-create) a conflict with
        # its siblings, so re-derive statuses before refreshing the table.
        self._run_conflict_detection()
        self._refresh_table()

    def _on_cell_edited(self, cell_item):
        """
        Called whenever the user edits a table cell.
        Re-syncs data and re-checks conflicts so renaming a media item
        or shot immediately resolves or reveals conflicts.
        """
        col = cell_item.column()
        if col not in (COL_SEQ, COL_SHOT, COL_TYPE, COL_MEDIA_NAME):
            return
        before = self._snapshot_identities()
        # Sync the edited value back to the item object first
        row = cell_item.row()
        if row < len(self.items_data):
            item = self.items_data[row]
            text = cell_item.text().strip()
            if col == COL_SEQ:        item.sequence_code = text
            if col == COL_SHOT:       item.shot_code     = text
            if col == COL_TYPE:       item.media_type    = text
            if col == COL_MEDIA_NAME: item.media_name    = text
        self._request_revalidation_for_changed(before)
        self._run_conflict_detection()
        self._refresh_table()

    def _on_version_changed(self, key, combo):
        text = combo.currentText()
        import re
        m = re.match(r"v(\d+)", text)
        if m:
            self.item_version[key] = int(m.group(1))
        # Re-check conflicts when version changes — changing version
        # can resolve a (seq, shot, media, version) collision
        self._run_conflict_detection()
        self._refresh_table()

    def _items_in_scope(self):
        """
        Which rows the batch tools act on, per the scope dropdown. All three
        (Apply Rename, ALL CAPS/lowercase, Set Version) go through this --
        the case and version tools used to ignore the dropdown entirely and
        silently act on the selection instead, so "Apply to All Rows" with
        one row highlighted only touched that row.
        """
        if "All Rows" in self._scope_combo.currentText():
            return list(self.items_data)
        return self._get_selected_items_in_table()

    @staticmethod
    def _identity(item):
        """The four fields that decide a row's NAS destination folder."""
        return (
            (item.sequence_code or "").strip(),
            (item.shot_code or "").strip(),
            (getattr(item, "media_type", "") or "").strip(),
            (item.media_name or "").strip(),
        )

    def _snapshot_identities(self):
        return {id(i): self._identity(i) for i in self.items_data}

    def _request_revalidation_for_changed(self, before):
        """
        Any row whose destination moved is holding a version number that was
        resolved against the folder it no longer points at -- e.g. renaming a
        row onto a shot that already has a v001 on the NAS left it reading
        "New / v001", and ingesting would have written straight into that
        existing version. Such rows drop back to "Checking..." and a fresh NAS
        lookup is requested for them.
        """
        changed = [i for i in self.items_data if before.get(id(i)) != self._identity(i)]
        if not changed:
            return
        for item in changed:
            key = id(item)
            self._pending_revalidation.add(key)
            # The finding was about the shot this row used to point at.
            self.kitsu_issues.pop(key, None)
            if key not in self.item_discarded:
                self.item_status[key] = STATUS_CHECKING
        self.revalidation_requested.emit(changed)

    # ------------------------------------------------------------------
    # Undo (Apply Rename / ALL CAPS / lowercase / Set Version)
    # ------------------------------------------------------------------

    def _snapshot_for_undo(self, items):
        """
        Everything one of the three batch tools can change about a row,
        captured before the mutation -- not just the four text fields, but
        also the version and status it had and whether it was mid- or
        pending-revalidation, so Undo puts back a row that was e.g. a
        resolved "New Version" rather than leaving it stuck re-querying the
        NAS for a slot it no longer occupies.
        """
        snaps = []
        for item in items:
            key = id(item)
            snaps.append({
                "item": item,
                "sequence_code": item.sequence_code,
                "shot_code": item.shot_code,
                "media_type": getattr(item, "media_type", ""),
                "media_name": item.media_name,
                "version": self.item_version.get(key),
                "detected_version": self.item_detected_version.get(key),
                "status": self.item_status.get(key),
                "pending": key in self._pending_revalidation,
                "kitsu_issue": self.kitsu_issues.get(key),
            })
        return snaps

    def _push_undo(self, label, items):
        """Record a restore point before a batch tool mutates `items`."""
        if not items:
            return
        self._undo_stack.append({"label": label, "rows": self._snapshot_for_undo(items)})
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)
        self._set_undo_button_state()

    def _set_undo_button_state(self):
        if self._undo_stack:
            self._undo_btn.setEnabled(True)
            self._undo_btn.setToolTip(f"Undo: {self._undo_stack[-1]['label']}")
        else:
            self._undo_btn.setEnabled(False)
            self._undo_btn.setToolTip("Nothing to undo")

    def _on_undo(self):
        """
        Pop the most recent batch action and put every field it touched back
        exactly as it was. Repeatable -- each click undoes one more step.
        """
        if not self._undo_stack:
            return
        entry = self._undo_stack.pop()
        for snap in entry["rows"]:
            item = snap["item"]
            key = id(item)
            item.sequence_code = snap["sequence_code"]
            item.shot_code     = snap["shot_code"]
            item.media_type    = snap["media_type"]
            item.media_name    = snap["media_name"]
            if snap["version"] is not None:
                self.item_version[key] = snap["version"]
            if snap["detected_version"] is not None:
                self.item_detected_version[key] = snap["detected_version"]
            if snap["status"] is not None:
                self.item_status[key] = snap["status"]
            if snap["pending"]:
                self._pending_revalidation.add(key)
            else:
                self._pending_revalidation.discard(key)
            if snap["kitsu_issue"] is not None:
                self.kitsu_issues[key] = snap["kitsu_issue"]
            else:
                self.kitsu_issues.pop(key, None)
        self._run_conflict_detection()
        self._refresh_table()
        self._set_undo_button_state()

    def _on_apply_rename(self):
        template = self._tmpl_edit.text().strip()
        if not template:
            return
        target = self._target_combo.currentText().lower()   # "shot", "media name", "sequence", "media type"

        self._sync_edits_from_table()
        before = self._snapshot_identities()

        import datetime
        today = datetime.date.today().strftime("%Y%m%d")

        items_to_apply = self._items_in_scope()
        self._push_undo(f"Rename: {self._target_combo.currentText()}", items_to_apply)

        for item in items_to_apply:
            new_val = template
            new_val = new_val.replace("{project}",    self._project_code or "PROJ")
            new_val = new_val.replace("{seq}",        item.sequence_code)
            new_val = new_val.replace("{shot}",       item.shot_code)
            new_val = new_val.replace("{media_name}", item.media_name)
            new_val = new_val.replace("{media_type}", getattr(item, "media_type", "") or "")
            new_val = new_val.replace("{original}",   item.name)
            new_val = new_val.replace("{date}",       today)
            new_val = new_val.replace("{version}",    f"v{self.item_version.get(id(item), 1):03d}")

            if target == "shot":
                item.shot_code = new_val
            elif target == "media name":
                item.media_name = new_val
            elif target == "sequence":
                item.sequence_code = new_val
            elif target == "media type":
                item.media_type = new_val

        self._request_revalidation_for_changed(before)
        self._run_conflict_detection()
        self._refresh_table()

    def _apply_case(self, mode):
        """
        Case-folds ONE field -- whichever the Target dropdown has picked --
        for the rows the Scope dropdown selects. Used to unconditionally
        touch all four fields regardless of Target, so picking "Shot" and
        clicking ALL CAPS silently also upper-cased Sequence/Media Type/
        Media Name; it now follows the same target as Apply Rename.
        """
        self._sync_edits_from_table()
        before = self._snapshot_identities()
        target = self._target_combo.currentText().lower()
        items_to_apply = self._items_in_scope()
        self._push_undo(
            f"{'ALL CAPS' if mode == 'upper' else 'lowercase'}: {self._target_combo.currentText()}",
            items_to_apply,
        )
        transform = str.upper if mode == "upper" else str.lower
        for item in items_to_apply:
            if target == "shot":
                item.shot_code = transform(item.shot_code or "")
            elif target == "media name":
                item.media_name = transform(item.media_name or "")
            elif target == "sequence":
                item.sequence_code = transform(item.sequence_code or "")
            elif target == "media type":
                item.media_type = transform(item.media_type or "")
        self._request_revalidation_for_changed(before)
        self._run_conflict_detection()
        self._refresh_table()

    def _on_batch_set_version(self):
        """Batch-set the version number for all/selected rows (version lives in
        item_version, the same dict the per-row dropdown edits, not on the
        item itself -- so this stays consistent with single-row edits)."""
        self._sync_edits_from_table()
        new_version = self._batch_version_spin.value()
        items = self._items_in_scope()
        self._push_undo(f"Set Version v{new_version:03d}", items)
        for item in items:
            self.item_version[id(item)] = new_version
        self._run_conflict_detection()
        self._refresh_table()

    def _on_discard_selected(self):
        self._sync_edits_from_table()
        for item in self._get_selected_items_in_table():
            self.item_discarded.add(id(item))
        # Same reason the row checkbox re-runs detection: discarding one side
        # of a conflict resolves it, and without this the other row stays
        # flagged and keeps the Ingest button blocked.
        self._run_conflict_detection()
        self._refresh_table()

    def _on_restore_selected(self):
        self._sync_edits_from_table()
        for item in self._get_selected_items_in_table():
            self.item_discarded.discard(id(item))
        # Re-including a row can re-create a conflict it was hiding.
        self._run_conflict_detection()
        self._refresh_table()

    # ------------------------------------------------------------------
    # Conflict Detection
    # ------------------------------------------------------------------

    def _run_conflict_detection(self):
        """
        Detect within-table conflicts.

        Conflict = two active (non-discarded, non-already-ingested) rows that
        would write to the SAME destination:
          same (sequence, shot, media_type, media_name, version) triple.

        Two rows with the same shot but DIFFERENT media names are valid siblings
        and must NOT be flagged as conflicts.
        """
        # Group by (seq, shot, type, media_name, version) — collision = true conflict
        slot_map = {}   # (seq, shot, type, media_name, version) -> [key, ...]
        for item in self.items_data:
            key = id(item)
            version = self.item_version.get(key, 1)
            media_type = getattr(item, "media_type", "Plate") or "Plate"
            slot = (
                item.sequence_code.upper().strip(),
                item.shot_code.upper().strip(),
                media_type.upper().strip(),
                item.media_name.upper().strip(),
                version,
            )
            slot_map.setdefault(slot, []).append(key)

        # Determine which keys are truly conflicted
        conflicted = set()
        for slot, keys in slot_map.items():
            active_keys = [
                k for k in keys
                if k not in self.item_discarded
                and self.item_status.get(k) != STATUS_ALREADY
            ]
            if len(active_keys) > 1:
                for k in active_keys:
                    conflicted.add(k)

        # Apply statuses
        for item in self.items_data:
            k = id(item)
            current = self.item_status.get(k)
            if k in conflicted:
                self.item_status[k] = STATUS_CONFLICT
            elif current == STATUS_CONFLICT:
                # Conflict resolved — re-derive status
                ver = self.item_version.get(k, 1)
                if k in self.item_discarded:
                    self.item_status[k] = STATUS_DISCARDED
                elif ver > 1:
                    self.item_status[k] = STATUS_NEW_VERSION
                elif item.missing_frames:
                    self.item_status[k] = STATUS_MISSING_FRAMES
                else:
                    self.item_status[k] = STATUS_NEW

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _sync_edits_from_table(self):
        """Read manual cell edits back into item objects."""
        for row in range(self._table.rowCount()):
            if row >= len(self.items_data):
                break
            item = self.items_data[row]
            seq_cell   = self._table.item(row, COL_SEQ)
            shot_cell  = self._table.item(row, COL_SHOT)
            type_cell  = self._table.item(row, COL_TYPE)
            media_cell = self._table.item(row, COL_MEDIA_NAME)
            if seq_cell:   item.sequence_code = seq_cell.text().strip()
            if shot_cell:  item.shot_code     = shot_cell.text().strip()
            if type_cell:  item.media_type    = type_cell.text().strip()
            if media_cell: item.media_name    = media_cell.text().strip()

    def _get_selected_items_in_table(self):
        rows = {index.row() for index in self._table.selectedIndexes()}
        return [self.items_data[r] for r in rows if r < len(self.items_data)]

    def _restore_selection(self, keys):
        """
        Re-select the rows holding these items after a rebuild. Applied as one
        QItemSelection rather than repeated selectRow() calls -- in the
        table's ExtendedSelection mode each selectRow() would clear the last,
        leaving only one row selected out of a multi-row selection.
        """
        if not keys:
            return
        sel = self._table.selectionModel()
        if sel is None:
            return
        model = self._table.model()
        last_col = max(self._table.columnCount() - 1, 0)
        selection = QtCore.QItemSelection()
        for row, item in enumerate(self.items_data):
            if id(item) in keys and row < self._table.rowCount():
                selection.select(model.index(row, 0), model.index(row, last_col))
        sel.clearSelection()
        if not selection.isEmpty():
            sel.select(selection, SELECTION_SELECT | SELECTION_ROWS)

    def _update_status_bar(self):
        # Counted from the same effective status the rows display -- reading
        # item_status directly used to miss Missing Details entirely, so a
        # row that couldn't be ingested showed a red pill while the summary
        # reported nothing wrong.
        statuses = [self._effective_status(i) for i in self.items_data]
        total   = len(statuses)
        new     = statuses.count(STATUS_NEW)
        vers    = statuses.count(STATUS_NEW_VERSION)
        skip    = statuses.count(STATUS_ALREADY)
        conf    = statuses.count(STATUS_CONFLICT)
        missing = statuses.count(STATUS_MISSING_DETAILS)
        disc    = statuses.count(STATUS_DISCARDED)
        self._status_lbl.setText(
            f"{total} items  |  {new} new  |  {vers} new version  |  {skip} skip  |  "
            f"{conf} conflict  |  {missing} missing details  |  {disc} discarded"
        )
        if conf > 0:
            self._status_lbl.setStyleSheet("color:#EF4444; font-size:11px; font-weight:bold; padding:2px 4px;")
        elif missing > 0:
            self._status_lbl.setStyleSheet("color:#F59E0B; font-size:11px; font-weight:bold; padding:2px 4px;")
        else:
            self._status_lbl.setStyleSheet("color:#94A3B8; font-size:11px; padding:2px 4px;")
