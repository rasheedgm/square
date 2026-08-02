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
    HEADER_RESIZE_INTERACTIVE, SELECT_ROWS, ALIGN_CENTER
)

# ── Status constants ──
STATUS_NEW            = "New"
STATUS_ALREADY        = "Already Ingested"
STATUS_NEW_VERSION    = "New Version"
STATUS_CONFLICT       = "Conflict"
STATUS_MISSING_FRAMES = "Missing Frames"
STATUS_DISCARDED      = "Discarded"
STATUS_CHECKING       = "Checking..."

STATUS_COLOURS = {
    STATUS_NEW:            ("#065F46", "#D1FAE5"),   # green
    STATUS_ALREADY:        ("#92400E", "#FEF3C7"),   # amber
    STATUS_NEW_VERSION:    ("#1E3A8A", "#DBEAFE"),   # blue
    STATUS_CONFLICT:       ("#7F1D1D", "#FEE2E2"),   # red
    STATUS_MISSING_FRAMES: ("#78350F", "#FFF7ED"),   # orange
    STATUS_DISCARDED:      ("#374151", "#F3F4F6"),   # grey
    STATUS_CHECKING:       ("#1F2937", "#F9FAFB"),   # dark grey
}

# Column indices
COL_INCLUDE  = 0
COL_SRC_NAME = 1
COL_SEQ      = 2
COL_SHOT     = 3
COL_TYPE     = 4
COL_PLATE    = 5
COL_DEST     = 6
COL_FRAMES   = 7
COL_FPS      = 8
COL_RES      = 9
COL_CS       = 10
COL_VERSION  = 11
COL_STATUS   = 12

HEADERS = [
    "",             # checkbox
    "Source",
    "Sequence",
    "Shot",
    "Type",
    "Plate",
    "Destination",
    "Frames",
    "FPS",
    "Resolution",
    "Colorspace",
    "Version",
    "Status",
]


class IngestTableWidget(QtWidgets.QWidget):
    """
    Full ingest review panel: batch toolbar + table.
    """

    # Emitted when table items or statuses change (triggers conflict badge & ingest button update)
    table_changed = QtCore.Signal()
    # Emitted when user clicks "Check Conflicts in Kitsu"
    kitsu_conflict_check_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items_data = []          # list of IngestSequenceItem
        self.item_status  = {}        # id(item) -> status string
        self.item_version = {}        # id(item) -> version int
        self.item_discarded = set()   # id(item) -> discarded
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
        self.item_discarded = set()
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
                target.plate_name    = item.plate_name
                target.media_type    = getattr(item, "media_type", "Plate")
            else:
                # Create new row for newly loaded item
                self.items_data.append(item)
                k = id(item)
                self.item_status[k]  = STATUS_CHECKING
                self.item_version[k] = 1
                existing_map[key_files] = item

        self._run_conflict_detection()
        self._refresh_table()

    def populate_items(self, items):
        """Alias for backward compatibility."""
        self.populate_table(items)

    def apply_version_results(self, results: dict):
        """
        Called from background thread results.
        results: { id(item): (version_num, is_already_ingested) }
        """
        for item in self.items_data:
            key = id(item)
            if key in results:
                ver, already = results[key]
                self.item_version[key] = ver
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

    def mark_kitsu_conflicts(self, conflicting_shot_names: set):
        """Mark rows whose shot name appears in conflicting_shot_names."""
        for item in self.items_data:
            if item.shot_code.upper() in {s.upper() for s in conflicting_shot_names}:
                self.item_status[id(item)] = STATUS_CONFLICT
        self._refresh_table()

    def has_unresolved_conflicts(self) -> bool:
        return any(s == STATUS_CONFLICT for s in self.item_status.values()
                   if id not in self.item_discarded)

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
            status = self.item_status.get(key, STATUS_NEW)
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
        self._table.setColumnWidth(COL_TYPE, 75)
        self._table.setColumnWidth(COL_PLATE, 70)
        self._table.setColumnWidth(COL_DEST, 220)
        self._table.setColumnWidth(COL_FRAMES, 130)
        self._table.setColumnWidth(COL_FPS, 45)
        self._table.setColumnWidth(COL_RES, 90)
        self._table.setColumnWidth(COL_CS, 80)
        self._table.setColumnWidth(COL_VERSION, 120)
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
        self._tmpl_edit.setToolTip(
            "Wildcards: {project} {seq} {shot} {plate} {original} {date} {version}"
        )
        self._tmpl_edit.setMinimumWidth(220)

        # Apply scope dropdown
        self._scope_combo = QtWidgets.QComboBox()
        self._scope_combo.addItems(["Apply to All Rows", "Apply to Selected Rows"])

        # Which column to rename
        self._target_combo = QtWidgets.QComboBox()
        self._target_combo.addItems(["Shot", "Plate", "Sequence"])

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
        caps_btn = QtWidgets.QPushButton("ALL CAPS")
        caps_btn.clicked.connect(lambda: self._apply_case("upper"))
        lower_btn = QtWidgets.QPushButton("lowercase")
        lower_btn.clicked.connect(lambda: self._apply_case("lower"))

        bar_layout.addWidget(caps_btn)
        bar_layout.addWidget(lower_btn)

        bar_layout.addSpacing(10)

        discard_btn = QtWidgets.QPushButton("Discard Selected")
        discard_btn.setStyleSheet("background:#7F1D1D; color:white; padding:4px 8px;")
        discard_btn.clicked.connect(self._on_discard_selected)

        restore_btn = QtWidgets.QPushButton("Re-include Selected")
        restore_btn.clicked.connect(self._on_restore_selected)

        bar_layout.addWidget(discard_btn)
        bar_layout.addWidget(restore_btn)

        return bar

    # ------------------------------------------------------------------
    # Table Population
    # ------------------------------------------------------------------

    def _refresh_table(self):
        self._table.blockSignals(True)
        self._table.setRowCount(0)

        for row_idx, item in enumerate(self.items_data):
            key = id(item)
            status = self.item_status.get(key, STATUS_NEW)
            ver    = self.item_version.get(key, 1)
            discarded = key in self.item_discarded

            if discarded:
                status = STATUS_DISCARDED

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
            self._table.setItem(row_idx, COL_SEQ,   self._mk_cell(item.sequence_code))
            self._table.setItem(row_idx, COL_SHOT,  self._mk_cell(item.shot_code))
            self._table.setItem(row_idx, COL_TYPE,  self._mk_cell(getattr(item, "media_type", "Plate")))
            self._table.setItem(row_idx, COL_PLATE, self._mk_cell(item.plate_name))

            # ── Destination cell (Filename in cell, full destination path in tooltip) ──
            from square_core.config import DEFAULT_FILE_NAME_TEMPLATE, format_dest_filename
            from square_core.nas_manager import NASManager

            tmpl = self._filename_template or DEFAULT_FILE_NAME_TEMPLATE
            mtype = getattr(item, "media_type", "Plate") or "Plate"
            frame_sample = "####" if not item.is_video else None
            dest_filename = format_dest_filename(
                tmpl, self._project_code or "PROJ", item.sequence_code,
                item.shot_code, mtype, item.plate_name, ver,
                frame=frame_sample, ext=item.ext
            )
            dest_dir = NASManager(nas_root=self._nas_root).get_dest_dir(
                self._project_code or "PROJ", item.sequence_code,
                item.shot_code, item.plate_name, version=ver,
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
            self._populate_version_combo(ver_combo, ver, status)
            ver_combo.currentIndexChanged.connect(
                lambda idx, cb=ver_combo, k=key: self._on_version_changed(k, cb)
            )
            self._table.setCellWidget(row_idx, COL_VERSION, ver_combo)

            # ── Status pill ──
            self._table.setItem(row_idx, COL_STATUS, self._mk_status_cell(status))

            # ── Row-level dim/highlight ──
            self._style_row(row_idx, status, discarded)

        self._table.blockSignals(False)
        self._update_status_bar()
        self.table_changed.emit()

    def _mk_cell(self, text, editable=True):
        cell = QtWidgets.QTableWidgetItem(str(text))
        if not editable:
            cell.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
        return cell

    def _mk_status_cell(self, status):
        cell = QtWidgets.QTableWidgetItem(status)
        cell.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
        bg, fg = STATUS_COLOURS.get(status, ("#374151", "#F9FAFB"))
        cell.setBackground(QtGui.QBrush(QtGui.QColor(bg)))
        cell.setForeground(QtGui.QBrush(QtGui.QColor(fg)))
        return cell

    def _populate_version_combo(self, combo, ver_num, status):
        combo.clear()
        if status == STATUS_ALREADY:
            combo.addItem(f"v{ver_num:03d} (exists — skip)")
        elif status == STATUS_NEW_VERSION:
            combo.addItem(f"v{ver_num:03d} (new version)")
            for extra in range(ver_num + 1, ver_num + 4):
                combo.addItem(f"v{extra:03d}")
        else:
            combo.addItem(f"v{ver_num:03d} (new)")

    def _style_row(self, row_idx, status, discarded):
        dim = discarded or status == STATUS_ALREADY
        col_count = self._table.columnCount()
        for col in range(col_count):
            widget = self._table.cellWidget(row_idx, col)
            cell   = self._table.item(row_idx, col)
            if dim and cell and col not in (COL_STATUS, COL_INCLUDE):
                cell.setForeground(QtGui.QBrush(QtGui.QColor("#6B7280")))
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
        self._refresh_table()

    def _on_cell_edited(self, cell_item):
        """
        Called whenever the user edits a table cell.
        Re-syncs data and re-checks conflicts so renaming a plate
        or shot immediately resolves or reveals conflicts.
        """
        col = cell_item.column()
        if col not in (COL_SEQ, COL_SHOT, COL_TYPE, COL_PLATE):
            return
        # Sync the edited value back to the item object first
        row = cell_item.row()
        if row < len(self.items_data):
            item = self.items_data[row]
            text = cell_item.text().strip()
            if col == COL_SEQ:   item.sequence_code = text
            if col == COL_SHOT:  item.shot_code     = text
            if col == COL_TYPE:  item.media_type    = text
            if col == COL_PLATE: item.plate_name    = text
        self._run_conflict_detection()
        self._refresh_table()

    def _on_version_changed(self, key, combo):
        text = combo.currentText()
        import re
        m = re.match(r"v(\d+)", text)
        if m:
            self.item_version[key] = int(m.group(1))
        # Re-check conflicts when version changes — changing version
        # can resolve a (seq, shot, plate, version) collision
        self._run_conflict_detection()
        self._refresh_table()

    def _on_apply_rename(self):
        template = self._tmpl_edit.text().strip()
        if not template:
            return
        target = self._target_combo.currentText().lower()   # "shot", "plate", "sequence"
        scope  = self._scope_combo.currentText()
        all_rows = "All Rows" in scope

        self._sync_edits_from_table()

        import datetime
        today = datetime.date.today().strftime("%Y%m%d")

        items_to_apply = self.items_data if all_rows else self._get_selected_items_in_table()

        for item in items_to_apply:
            new_val = template
            new_val = new_val.replace("{project}",  self._project_code or "PROJ")
            new_val = new_val.replace("{seq}",       item.sequence_code)
            new_val = new_val.replace("{shot}",      item.shot_code)
            new_val = new_val.replace("{plate}",     item.plate_name)
            new_val = new_val.replace("{original}",  item.name)
            new_val = new_val.replace("{date}",      today)
            new_val = new_val.replace("{version}",   f"v{self.item_version.get(id(item), 1):03d}")

            if target == "shot":
                item.shot_code = new_val
            elif target == "plate":
                item.plate_name = new_val
            elif target == "sequence":
                item.sequence_code = new_val

        self._run_conflict_detection()
        self._refresh_table()

    def _apply_case(self, mode):
        self._sync_edits_from_table()
        selected = self._get_selected_items_in_table()
        items_to_apply = selected if selected else self.items_data
        for item in items_to_apply:
            if mode == "upper":
                item.shot_code     = item.shot_code.upper()
                item.plate_name    = item.plate_name.upper()
                item.sequence_code = item.sequence_code.upper()
            else:
                item.shot_code     = item.shot_code.lower()
                item.plate_name    = item.plate_name.lower()
                item.sequence_code = item.sequence_code.lower()
        self._refresh_table()

    def _on_discard_selected(self):
        for item in self._get_selected_items_in_table():
            self.item_discarded.add(id(item))
        self._refresh_table()

    def _on_restore_selected(self):
        for item in self._get_selected_items_in_table():
            self.item_discarded.discard(id(item))
        self._refresh_table()

    # ------------------------------------------------------------------
    # Conflict Detection
    # ------------------------------------------------------------------

    def _run_conflict_detection(self):
        """
        Detect within-table conflicts.

        Conflict = two active (non-discarded, non-already-ingested) rows that
        would write to the SAME destination:
          same (sequence, shot, plate, version) triple.

        Two rows with the same shot but DIFFERENT plate names are valid siblings
        and must NOT be flagged as conflicts.
        """
        id_to_item = {id(item): item for item in self.items_data}

        # Group by (seq, shot, type, plate, version) — collision = true conflict
        slot_map = {}   # (seq, shot, type, plate, version) -> [key, ...]
        for item in self.items_data:
            key = id(item)
            version = self.item_version.get(key, 1)
            media_type = getattr(item, "media_type", "Plate") or "Plate"
            slot = (
                item.sequence_code.upper().strip(),
                item.shot_code.upper().strip(),
                media_type.upper().strip(),
                item.plate_name.upper().strip(),
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
            seq_cell  = self._table.item(row, COL_SEQ)
            shot_cell = self._table.item(row, COL_SHOT)
            plt_cell  = self._table.item(row, COL_PLATE)
            if seq_cell:  item.sequence_code = seq_cell.text().strip()
            if shot_cell: item.shot_code     = shot_cell.text().strip()
            if plt_cell:  item.plate_name    = plt_cell.text().strip()

    def _get_selected_items_in_table(self):
        rows = {index.row() for index in self._table.selectedIndexes()}
        return [self.items_data[r] for r in rows if r < len(self.items_data)]

    def _update_status_bar(self):
        total = len(self.items_data)
        new   = sum(1 for s in self.item_status.values() if s == STATUS_NEW)
        vers  = sum(1 for s in self.item_status.values() if s == STATUS_NEW_VERSION)
        skip  = sum(1 for s in self.item_status.values() if s == STATUS_ALREADY)
        conf  = sum(1 for s in self.item_status.values() if s == STATUS_CONFLICT)
        disc  = len(self.item_discarded)
        self._status_lbl.setText(
            f"{total} items  |  {new} new  |  {vers} new version  |  "
            f"{skip} skip  |  {conf} conflict  |  {disc} discarded"
        )
        if conf > 0:
            self._status_lbl.setStyleSheet("color:#EF4444; font-size:11px; font-weight:bold; padding:2px 4px;")
        else:
            self._status_lbl.setStyleSheet("color:#94A3B8; font-size:11px; padding:2px 4px;")
