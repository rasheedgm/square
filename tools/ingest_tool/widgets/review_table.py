"""
IngestReviewTable -- a pure view over IngestController.items.

No ingest logic lives here. It renders each item, pushes cell edits back
through the bridge, and surfaces per-row conflict resolution via a context
menu. Rows update in place from the bridge's ``event`` signal, so a running
pre-flight / ingest streams into the table without a full rebuild.
"""

from __future__ import annotations

from Qt import QtCore, QtWidgets, QtGui

from tools.ingest_tool.core.item import Status, Action, Severity
from tools.ingest_tool.widgets.rename_dialog import RenameCellsDialog
from tools.qt_compat import ALIGN_CENTER, EXTENDED_SELECTION, SELECT_ITEMS

ROW_HEIGHT = 28

# columns
COLS = [
    ("", 26), ("Source", 150), ("Seq", 80), ("Shot", 90), ("Type", 90),
    ("Media", 110), ("Extra", 90), ("Destination", 220), ("Frames", 130),
    ("FPS", 55), ("Res", 90), ("CS", 80), ("Prev", 40), ("→EXR", 50), ("Ver", 46),
    ("Status", 130), ("Progress", 160),
]
(C_SEL, C_SRC, C_SEQ, C_SHOT, C_TYPE, C_MEDIA, C_EXTRA, C_DEST, C_FRAMES,
 C_FPS, C_RES, C_CS, C_PREV, C_CONV, C_VER, C_STATUS, C_PROG) = range(len(COLS))

_EDIT_FIELD = {
    C_SEQ: "sequence_code", C_SHOT: "shot_code", C_TYPE: "media_type",
    C_MEDIA: "media_name", C_FPS: "fps", C_RES: "resolution", C_CS: "colorspace",
    C_VER: "version",
}

_STATUS_STYLE = {
    Status.NEW:              ("#D1FAE5", "#065F46"),
    Status.NEW_VERSION:      ("#DBEAFE", "#1E3A8A"),
    Status.READY:            ("#D1FAE5", "#065F46"),
    Status.WARNING:          ("#FEF3C7", "#92400E"),
    Status.CONFLICT:         ("#FEE2E2", "#7F1D1D"),
    Status.NEEDS_INFO:       ("#FEE2E2", "#991B1B"),
    Status.ALREADY_INGESTED: ("#F3F4F6", "#374151"),
    Status.SKIPPED:          ("#F3F4F6", "#374151"),
    Status.CHECKING:         ("#F9FAFB", "#1F2937"),
    Status.CHECK_FAILED:     ("#B91C1C", "#FFFFFF"),
    Status.INGESTING:        ("#DBEAFE", "#1E3A8A"),
    Status.COMPLETED:        ("#10B981", "#052E16"),
    Status.FAILED:           ("#B91C1C", "#FFFFFF"),
}


class _CompactEditDelegate(QtWidgets.QStyledItemDelegate):
    """Keeps the in-cell editor exactly the size of the cell (the app-wide
    QSS gives QLineEdit a 26px min-height + padding, which overflows a 28px row)."""

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        editor.setStyleSheet("min-height:0; padding:0 4px; margin:0;")
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class IngestReviewTable(QtWidgets.QWidget):
    selection_changed = QtCore.Signal(object)     # list[key]

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._rows: list[str] = []          # row index -> item key
        self._loading = False

        self._table = QtWidgets.QTableWidget(0, len(COLS), self)
        self._table.setHorizontalHeaderLabels([c[0] for c in COLS])
        self._table.verticalHeader().setVisible(False)
        # Cell-level (not whole-row) selection: shift/ctrl-click can pick,
        # say, just the Colorspace cells of a few rows, for the Rename /
        # Set Value context-menu action below.
        self._table.setSelectionBehavior(SELECT_ITEMS)
        self._table.setSelectionMode(EXTENDED_SELECTION)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        for i, (_, w) in enumerate(COLS):
            self._table.setColumnWidth(i, w)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setWordWrap(False)
        self._table.setItemDelegate(_CompactEditDelegate(self._table))
        self._table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)

        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemSelectionChanged.connect(self._on_selection)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._table)

        bridge.event.connect(self._on_event)

    # ------------------------------------------------------------------
    # Events from the controller
    # ------------------------------------------------------------------

    def _on_event(self, ev) -> None:
        if ev.kind == "items_loaded":
            removed = ev.payload.get("removed")
            added = ev.payload.get("added")
            if removed:
                self._remove_rows(removed)          # in place -- no full rebuild
            elif added and not ev.payload.get("replaced") and self._rows_are_a_prefix():
                self._append_rows()
            else:
                self.rebuild()
        elif ev.kind == "items_updated":
            self._update_rows(ev.payload.get("keys", []))
        elif ev.kind == "item_updated" and ev.item is not None:
            self._update_row(ev.item)
        elif ev.kind == "item_stage" and ev.item is not None:
            self._update_progress(ev.item)

    # ------------------------------------------------------------------
    # Build / update
    # ------------------------------------------------------------------

    def rebuild(self) -> None:
        self._table.setUpdatesEnabled(False)
        self._loading = True
        try:
            self._table.setRowCount(0)
            self._rows = []
            self._append_rows()
        finally:
            self._loading = False
            self._table.setUpdatesEnabled(True)

    def _rows_are_a_prefix(self) -> bool:
        """True if the table still shows exactly the controller's first rows
        (so newly loaded rows can just be appended)."""
        items = self.bridge.controller.items
        return (len(self._rows) <= len(items)
                and all(k == items[i].key for i, k in enumerate(self._rows)))

    def _append_rows(self) -> None:
        """Add a row for every controller item the table does not show yet."""
        self._table.setUpdatesEnabled(False)
        self._loading = True
        try:
            for item in self.bridge.controller.items[len(self._rows):]:
                r = self._table.rowCount()
                self._table.insertRow(r)
                self._rows.append(item.key)
                self._table.setRowHeight(r, ROW_HEIGHT)
                self._init_row_widgets(r, item.key)
                self._fill_row(r, item)
        finally:
            self._loading = False
            self._table.setUpdatesEnabled(True)

    def _remove_rows(self, keys) -> None:
        """Drop rows in place. (Every removal used to rebuild the whole table
        -- creating every row's widgets again -- once per removed row.)"""
        gone = set(keys)
        self._table.setUpdatesEnabled(False)
        self._loading = True
        try:
            for r in sorted((i for i, k in enumerate(self._rows) if k in gone), reverse=True):
                self._table.removeRow(r)
            self._rows = [k for k in self._rows if k not in gone]
        finally:
            self._loading = False
            self._table.setUpdatesEnabled(True)

    def _init_row_widgets(self, r: int, key: str) -> None:
        prog = QtWidgets.QProgressBar()
        prog.setTextVisible(True)
        prog.setRange(0, 100)
        prog.setFormat("")
        self._table.setCellWidget(r, C_PROG, prog)

        chk_holder = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(chk_holder)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(ALIGN_CENTER)
        chk = QtWidgets.QCheckBox()
        chk.stateChanged.connect(lambda _s, k=key: self._on_preview_toggle(k))
        h.addWidget(chk)
        self._table.setCellWidget(r, C_PREV, chk_holder)

        conv_holder = QtWidgets.QWidget()
        ch = QtWidgets.QHBoxLayout(conv_holder)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setAlignment(ALIGN_CENTER)
        conv_chk = QtWidgets.QCheckBox()
        conv_chk.stateChanged.connect(lambda _s, k=key: self._on_convert_toggle(k))
        ch.addWidget(conv_chk)
        self._table.setCellWidget(r, C_CONV, conv_holder)

        status = QtWidgets.QLabel()
        status.setAlignment(ALIGN_CENTER)
        status.setContentsMargins(6, 1, 6, 1)
        self._table.setCellWidget(r, C_STATUS, status)

    def _row_for_key(self, key: str) -> int:
        try:
            return self._rows.index(key)
        except ValueError:
            return -1

    def _update_row(self, item) -> None:
        r = self._row_for_key(item.key)
        if r < 0:
            return      # not shown (a stale event for a removed / replaced row)
        self._fill_row(r, item)

    def _update_rows(self, keys) -> None:
        """Refresh several rows in one repaint (a batch action / a check's
        final pass) instead of one repaint per row."""
        self._table.setUpdatesEnabled(False)
        try:
            for key in keys:
                item = self.bridge.controller.get(key)
                r = self._row_for_key(key)
                if item is not None and r >= 0:
                    self._fill_row(r, item)
        finally:
            self._table.setUpdatesEnabled(True)

    def _update_progress(self, item) -> None:
        """Just the progress bar (and the status pill, which can flip with a
        stage) -- the cheap path for the many progress ticks of a run."""
        r = self._row_for_key(item.key)
        if r < 0:
            return
        self._status_label(r, item)
        self._progress_bar(r, item)

    def _cell(self, r, c, text, editable=False, tip=""):
        it = self._table.item(r, c)
        if it is None:
            it = QtWidgets.QTableWidgetItem()
            self._table.setItem(r, c, it)
        it.setText("" if text is None else str(text))
        flags = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled
        if editable:
            flags |= QtCore.Qt.ItemFlag.ItemIsEditable
        it.setFlags(flags)
        if tip:
            it.setToolTip(tip)
        return it

    def _fill_row(self, r: int, item) -> None:
        self._loading = True
        locked = item.status in (Status.COMPLETED, Status.INGESTING)

        self._cell(r, C_SRC, item.source_name or item.key, tip="\n".join(item.source_files))
        self._cell(r, C_SEQ, item.sequence_code, editable=not locked)
        self._cell(r, C_SHOT, item.shot_code, editable=not locked)
        self._cell(r, C_TYPE, item.media_type, editable=not locked)
        self._cell(r, C_MEDIA, item.media_name, editable=not locked)
        self._cell(r, C_EXTRA, ", ".join(f"{k}={v}" for k, v in item.extra_tags.items()) or "—",
                   tip="Pattern-captured tags outside the 5 canonical fields")
        self._cell(r, C_DEST, item.dest_dir, tip=item.dest_dir)
        self._cell(r, C_FRAMES, item.frame_range_str +
                   (f"  ⚠ {len(item.missing_frames)} missing" if item.missing_frames else ""))
        self._metadata_cell(r, C_FPS, item, "fps")
        self._metadata_cell(r, C_RES, item, "resolution")
        self._metadata_cell(r, C_CS, item, "colorspace")
        self._cell(r, C_VER, item.version, editable=not locked)

        holder = self._table.cellWidget(r, C_PREV)
        chk = holder.findChild(QtWidgets.QCheckBox)
        chk.blockSignals(True)
        chk.setChecked(item.preview_wanted)
        chk.setEnabled(not locked)
        chk.setToolTip("Preview differs from the media-type default"
                       if item.preview_wanted != item.preview_default else "Media-type default")
        chk.blockSignals(False)

        conv_holder = self._table.cellWidget(r, C_CONV)
        conv_chk = conv_holder.findChild(QtWidgets.QCheckBox)
        conv_chk.blockSignals(True)
        conv_chk.setChecked(item.convert_to_exr)
        conv_chk.setEnabled(item.is_video and not locked)
        conv_chk.setToolTip(
            "Decode this video to an EXR frame sequence before ingesting"
            if item.is_video else "Only for a single video file"
        )
        conv_chk.blockSignals(False)

        self._status_label(r, item)
        self._progress_bar(r, item)
        self._loading = False

    def _metadata_cell(self, r, c, item, field):
        verified = item.metadata_verified.get(field, False)
        val = getattr(item, field)
        locked = item.status in (Status.COMPLETED, Status.INGESTING)
        cell = self._cell(r, c, "" if val in (None, "") else val, editable=not locked)
        if val in (None, ""):
            cell.setText("set…")
            cell.setForeground(QtGui.QBrush(QtGui.QColor("#B91C1C")))
        elif not verified:
            cell.setForeground(QtGui.QBrush(QtGui.QColor("#B45309")))
            cell.setToolTip("Not read from the media — set/confirm it")
        else:
            cell.setForeground(QtGui.QBrush(QtGui.QColor("#E2E8F0")))
            cell.setToolTip("")

    @staticmethod
    def _set_style(widget, css: str) -> None:
        """Re-parsing a stylesheet is the expensive part of a row refresh --
        only do it when it actually changed."""
        if widget.property("_css") != css:
            widget.setStyleSheet(css)
            widget.setProperty("_css", css)

    def _status_label(self, r, item):
        lbl = self._table.cellWidget(r, C_STATUS)
        bg, fg = _STATUS_STYLE.get(item.status, ("#334155", "#E2E8F0"))
        lbl.setText(item.status.value)
        self._set_style(
            lbl, f"background:{bg}; color:{fg}; border-radius:9px; font-size:11px; font-weight:600;")
        tips = [i.message for i in item.unresolved_issues]
        if item.check_error:
            tips.insert(0, f"Check failed: {item.check_error}")
        if item.ingest_error:
            tips.insert(0, f"Ingest failed: {item.ingest_error}")
        if item.ledger_detail:
            tips.append(item.ledger_detail)
        lbl.setToolTip("\n".join(tips))

    def _progress_bar(self, r, item):
        bar = self._table.cellWidget(r, C_PROG)
        if item.status == Status.CHECKING:
            # the check's own progress ("Reading metadata", "Hashing 3/12") --
            # a row used to just say Checking with nothing moving
            bar.setValue(int(item.check_pct))
            bar.setFormat(f"{item.check_stage}  %p%" if item.check_stage else "")
            self._set_style(bar, self._bar_css("#3B82F6"))
            return
        bar.setValue(int(item.stage_pct))
        label = f"{item.stage.value}  %p%" if item.stage_pct else ""
        colour = "#3B82F6"
        if item.status == Status.COMPLETED:
            colour = "#10B981"
            # preview runs after "Completed" -- show it trickling in
            if item.preview_state in ("pending", "running"):
                label, colour = f"Ingested · preview {item.preview_state}…", "#8B5CF6"
            elif item.preview_state == "failed":
                label, colour = "Ingested · preview failed", "#F59E0B"
            else:
                label = "Ingested"
        elif item.status == Status.FAILED:
            colour = "#EF4444"
        bar.setFormat(label)
        self._set_style(bar, self._bar_css(colour))

    @staticmethod
    def _bar_css(colour: str) -> str:
        return (
            "QProgressBar{background:#1A2035;border:none;border-radius:3px;color:#E2E8F0;font-size:10px}"
            f"QProgressBar::chunk{{background:{colour};border-radius:3px}}"
        )

    # ------------------------------------------------------------------
    # Edits
    # ------------------------------------------------------------------

    def _on_item_changed(self, cell: QtWidgets.QTableWidgetItem) -> None:
        if self._loading:
            return
        r, c = cell.row(), cell.column()
        field = _EDIT_FIELD.get(c)
        if not field or r >= len(self._rows):
            return
        key = self._rows[r]
        value = cell.text().strip()
        if c in (C_FPS, C_RES, C_CS) and value in ("", "set…"):
            return
        try:
            if field == "version":
                value = int(value)
            elif field == "fps":
                value = float(value)
            self.bridge.set_field(key, field, value)
        except (ValueError, KeyError):
            pass
        self._update_row(self.bridge.controller.get(key))

    def _on_preview_toggle(self, key: str) -> None:
        row = self._row_for_key(key)
        if self._loading or row < 0:
            return
        chk = self._table.cellWidget(row, C_PREV).findChild(QtWidgets.QCheckBox)
        self.bridge.set_preview(key, chk.isChecked())

    def _on_convert_toggle(self, key: str) -> None:
        row = self._row_for_key(key)
        if self._loading or row < 0:
            return
        chk = self._table.cellWidget(row, C_CONV).findChild(QtWidgets.QCheckBox)
        self.bridge.set_convert_to_exr(key, chk.isChecked())

    # ------------------------------------------------------------------
    # Selection + context menu
    # ------------------------------------------------------------------

    def selected_keys(self) -> list:
        rows = {ix.row() for ix in self._table.selectedIndexes()}
        return [self._rows[r] for r in sorted(rows) if r < len(self._rows)]

    def selected_cells(self) -> list:
        """[(item_key, attr_name), ...] for whatever cells are selected --
        row order preserved, non-renameable columns (status, checkboxes,
        destination, ...) silently dropped. Feeds the Rename / Set Value
        context-menu action: shift/ctrl-click can pick e.g. just the
        Colorspace cells of a few rows, not the whole row."""
        out = []
        for ix in self._table.selectedIndexes():
            field = _EDIT_FIELD.get(ix.column())
            if field and ix.row() < len(self._rows):
                out.append((self._rows[ix.row()], field))
        return out

    def _on_selection(self) -> None:
        self.selection_changed.emit(self.selected_keys())

    def _on_rename_selected(self, cells) -> None:
        RenameCellsDialog.rename_selected(self.bridge, cells, parent=self)

    def _on_context_menu(self, pos) -> None:
        keys = self.selected_keys()
        if not keys:
            return
        items = [self.bridge.controller.get(k) for k in keys]
        menu = QtWidgets.QMenu(self)

        # collect the union of resolvable issue kinds across the selection
        kinds = {}
        for it in items:
            for iss in it.unresolved_issues:
                for act in iss.actions:
                    kinds.setdefault((iss.kind, act), 0)
                    kinds[(iss.kind, act)] += 1

        if kinds:
            for (kind, act), n in sorted(kinds.items(), key=lambda x: x[0][0].value):
                label = f"{act.value.replace('_', ' ').title()} — {kind.value} ({n})"
                menu.addAction(label, lambda k=kind, a=act: self.bridge.resolve_many(keys, k, a))
            menu.addSeparator()

        cells = self.selected_cells()
        if cells:
            menu.addAction(f"Rename / Set Value… ({len(cells)} cell(s))",
                           lambda: self._on_rename_selected(cells))
            menu.addSeparator()

        any_skipped = any(it.skipped for it in items)
        if any_skipped:
            menu.addAction("Include in batch", lambda: self.bridge.include_many(keys))
        else:
            menu.addAction("Skip", lambda: self.bridge.skip_many(keys))
        menu.addSeparator()
        menu.addAction("Re-check", lambda: self.bridge.preflight(keys))
        menu.addAction("Remove from table", lambda: self.bridge.remove_many(keys))
        menu.exec(self._table.viewport().mapToGlobal(pos))
