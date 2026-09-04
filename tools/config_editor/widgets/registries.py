"""Table editors for the "dict of named entries" config keys:

  - `roots`            -> name -> "{token}" pattern            (string entries)
  - `media_types`      -> name -> {base, dir, file, kitsu_kind, ...}  (dict entries)
  - `delivery_presets` -> name -> {base, dir, file, case, ...}        (dict entries)

One `RegistryEditor` handles all three: string-entry mode is a 2-column
name/pattern grid; dict-entry mode is a grid of the entry's fields. `dir` /
`file` / `pattern` cells open the template builder on double-click.
"""

from __future__ import annotations

import json

from Qt import QtCore, QtWidgets

from tools.qt_compat import (HEADER_RESIZE_INTERACTIVE, HEADER_RESIZE_STRETCH,
                             ITEM_IS_EDITABLE)
from .template_builder import TemplateBuilderDialog

# columns we always surface for a dict-entry registry, in order
_MEDIA_COLS = ["base", "source", "dir", "file", "kitsu_kind", "representation",
               "previewable", "colorspace"]
_DELIVERY_COLS = ["base", "dir", "file", "case", "container", "frame_pad",
                  "colorspace", "slate", "burnin"]
_PATTERN_COLS = {"dir", "file", "pattern"}
_PROTECTED = {"_default"}


class RegistryEditor(QtWidgets.QWidget):
    signal_changed = QtCore.Signal()

    def __init__(self, fv, parent=None, *, version_pad: int = 3, frame_pad: int = 4):
        super().__init__(parent)
        self.fv = fv
        self._kind = fv.kind
        self._version_pad = version_pad
        self._frame_pad = frame_pad
        data = json.loads(json.dumps(fv.value or {}))
        self._string_mode = all(isinstance(v, str) for v in data.values()) and bool(data)
        if self._kind == "root":
            self._string_mode = True

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(220)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.cellChanged.connect(lambda *_: self.signal_changed.emit())
        self.table.cellDoubleClicked.connect(self._maybe_build)
        lay.addWidget(self.table)

        hint = QtWidgets.QLabel(
            "double-click a dir / file / pattern cell to open the template builder. "
            "editing any row here and saving writes the whole table shown, including "
            "rows still at their built-in value -- not just the row you changed.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#64748B;font-size:11px;")
        lay.addWidget(hint)

        row = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add")
        rem = QtWidgets.QPushButton("Remove")
        add.clicked.connect(self._add_row)
        rem.clicked.connect(self._remove_row)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)

        self._columns: list[str] = []
        self._populate(data)

    # ---- build ----------------------------------------------------

    def _entry_columns(self, data: dict) -> list[str]:
        if self._kind == "media_type_registry":
            base = list(_MEDIA_COLS)
        elif self._kind == "delivery_registry":
            base = list(_DELIVERY_COLS)
        else:
            base = []
        for entry in data.values():
            if isinstance(entry, dict):
                for k in entry:
                    if k not in base:
                        base.append(k)
        return base

    def _populate(self, data: dict):
        self.table.blockSignals(True)
        self.table.clear()
        if self._string_mode:
            self._columns = ["pattern"]
            headers = ["Name", "Pattern"]
        else:
            self._columns = self._entry_columns(data)
            headers = ["Name"] + self._columns
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(data))

        for r, (name, entry) in enumerate(data.items()):
            self._set_row(r, name, entry)

        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(HEADER_RESIZE_INTERACTIVE)
        if not self._string_mode and "dir" in self._columns:
            hh.setSectionResizeMode(1 + self._columns.index("dir"), HEADER_RESIZE_STRETCH)
        elif self._string_mode:
            hh.setSectionResizeMode(1, HEADER_RESIZE_STRETCH)
        self.table.blockSignals(False)

    def _set_row(self, r: int, name: str, entry):
        name_item = QtWidgets.QTableWidgetItem(name)
        if name in _PROTECTED:
            name_item.setFlags(name_item.flags() & ~ITEM_IS_EDITABLE)
        self.table.setItem(r, 0, name_item)
        if self._string_mode:
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(entry or "")))
            return
        for c, col in enumerate(self._columns, start=1):
            v = entry.get(col) if isinstance(entry, dict) else None
            cell = "" if v is None else (v if isinstance(v, str) else json.dumps(v))
            self.table.setItem(r, c, QtWidgets.QTableWidgetItem(cell))

    # ---- row ops ------------------------------------------------

    def _add_row(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "New entry", "Name:")
        if not ok or not name.strip():
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._set_row(r, name.strip(), {} if not self._string_mode else "")
        self.signal_changed.emit()

    def _remove_row(self):
        r = self.table.currentRow()
        if r < 0:
            return
        nm = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
        if nm in _PROTECTED:
            QtWidgets.QMessageBox.information(self, "Protected",
                                             f"'{nm}' cannot be removed.")
            return
        self.table.removeRow(r)
        self.signal_changed.emit()

    def _maybe_build(self, r: int, c: int):
        header = (self.table.horizontalHeaderItem(c).text() or "").lower()
        if header not in _PATTERN_COLS:
            return
        item = self.table.item(r, c) or QtWidgets.QTableWidgetItem("")
        new = TemplateBuilderDialog.edit_pattern(
            self, item.text(), title=header, is_dir=(header == "dir"),
            version_pad=self._version_pad, frame_pad=self._frame_pad)
        if new is not None:
            item.setText(new)
            self.table.setItem(r, c, item)
            self.signal_changed.emit()

    # ---- value -------------------------------------------------

    def get_value(self) -> dict:
        out: dict = {}
        for r in range(self.table.rowCount()):
            nm_item = self.table.item(r, 0)
            if not nm_item or not nm_item.text().strip():
                continue
            name = nm_item.text().strip()
            if self._string_mode:
                cell = self.table.item(r, 1)
                out[name] = cell.text().strip() if cell else ""
                continue
            entry: dict = {}
            for c, col in enumerate(self._columns, start=1):
                cell = self.table.item(r, c)
                raw = cell.text().strip() if cell else ""
                if raw == "":
                    continue
                try:
                    entry[col] = json.loads(raw)
                except json.JSONDecodeError:
                    entry[col] = raw
            out[name] = entry
        return out
