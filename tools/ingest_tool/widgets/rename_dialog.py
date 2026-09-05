"""
RenameCellsDialog -- batch-set a value across whatever cells the review
table's selection covers (any mix of columns and rows), by one token
template with a live resolved preview -- the same by-example pattern as the
config editor's TemplateBuilderDialog.

Opened from the review table's right-click menu once one or more editable
cells are selected. A token in the template (e.g. "{sequence}") is resolved
per ROW; a cell whose own column isn't a template concept (fps, colorspace,
version) just gets the literal typed value, coerced to that field's type.
"""

from __future__ import annotations

from Qt import QtWidgets, QtCore

from tools.ingest_tool.core.controller import IngestController
from tools.qt_compat import (
    TEXT_SELECTABLE_BY_MOUSE, DIALOG_OK, DIALOG_CANCEL, DIALOG_ACCEPTED, exec_dialog,
    get_qt_enum,
)

_FIELD_LABELS = {
    "sequence_code": "Sequence", "shot_code": "Shot", "media_type": "Media Type",
    "media_name": "Media Name", "fps": "FPS", "resolution": "Resolution",
    "colorspace": "Colorspace", "version": "Version",
}

_PREVIEW_LIMIT = 8


class RenameCellsDialog(QtWidgets.QDialog):
    def __init__(self, bridge, cell_targets, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.cell_targets = list(cell_targets)   # [(key, attr), ...]
        self.setWindowTitle("Rename / Set Value")
        self.setMinimumWidth(520)

        n_rows = len({k for k, _ in self.cell_targets})
        n_fields = len({a for _, a in self.cell_targets})
        scope = QtWidgets.QLabel(
            f"{len(self.cell_targets)} cell(s) across {n_rows} row(s)"
            + (f", {n_fields} field(s)" if n_fields > 1 else "")
        )
        scope.setStyleSheet("color:#94A3B8;")

        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("e.g. {sequence}_{shot}_{media_name}, or a plain value")
        self.edit.setToolTip(
            "Tokens, resolved per row:\n"
            "  {project}      project code\n"
            "  {sequence}     this row's Sequence\n"
            "  {shot}         this row's Shot\n"
            "  {media_type}   this row's Media Type\n"
            "  {media_name}   this row's Media Name\n"
            "  {current}      this CELL's own value right now\n"
            "  {original}     this CELL's value as it first loaded\n"
            "  {source}       scanner's file/folder name, e.g. \"plate\"\n"
            "  {version}      current version, e.g. v003\n"
            "  {date}         today, YYYYMMDD\n"
            "{current}/{original} follow whichever field a cell is in -- "
            "renaming a Shot cell that loaded as \"Fgt10\", {original}_v2 "
            "always gives \"Fgt10_v2\" even after you've edited it, while "
            "{current}_v2 uses whatever it says right now.\n"
            "Add :upper, :lower, :title or :capitalize to case-transform just "
            "that token, e.g. {shot:upper}.\n"
            "Anything else is written literally. A numeric field (FPS, "
            "Version) needs the resolved text to actually be a number."
        )

        palette = QtWidgets.QHBoxLayout()
        palette.setSpacing(4)
        for tok in IngestController.RENAME_TOKENS:
            b = QtWidgets.QPushButton("{%s}" % tok)
            # The app-wide QSS gives every QPushButton min-height:26px +
            # padding:6px 14px + a border (no ":flat" override, unlike the
            # config editor's own QSS), rendering ~40px tall by default --
            # clipped inside a short scroll strip, and a partial override
            # (padding/min-height only) still inherited a muted, hard-to-read
            # default color. Spell out every property explicitly instead, the
            # same small-chip look path_pattern_dialog.py's ChipButton
            # already uses successfully in this app.
            b.setStyleSheet(
                "QPushButton { background-color:#1E293B; color:#CBD5E1;"
                " border:1px solid #334155; border-radius:4px;"
                " min-height:0; padding:3px 10px; margin:0; font-size:11px; }"
                "QPushButton:hover { background-color:#263248; color:#E2E8F0;"
                " border-color:#4B6EAD; }"
            )
            b.clicked.connect(lambda _=False, t=tok: self._insert(t))
            palette.addWidget(b)
        palette.addStretch(1)
        pal_wrap = QtWidgets.QWidget()
        pal_wrap.setLayout(palette)
        pal_scroll = QtWidgets.QScrollArea()
        pal_scroll.setWidgetResizable(True)
        pal_scroll.setFixedHeight(48)
        pal_scroll.setWidget(pal_wrap)

        self.preview = QtWidgets.QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        self.preview.setStyleSheet("font-family: monospace; font-size:11px;")
        self.preview.setTextFormat(get_qt_enum(QtCore.Qt, "TextFormat", "PlainText"))
        prev_scroll = QtWidgets.QScrollArea()
        prev_scroll.setWidgetResizable(True)
        prev_scroll.setFixedHeight(160)
        prev_scroll.setWidget(self.preview)

        buttons = QtWidgets.QDialogButtonBox(DIALOG_OK | DIALOG_CANCEL)
        self._ok = buttons.button(DIALOG_OK)
        self._ok.setText("Apply")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        form = QtWidgets.QFormLayout(self)
        form.addRow(scope)
        form.addRow("Template", self.edit)
        form.addRow("Tokens", pal_scroll)
        form.addRow("Preview", prev_scroll)
        form.addRow(buttons)

        self.edit.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

    # ------------------------------------------------------------------

    def _insert(self, token: str) -> None:
        self.edit.insert("{%s}" % token)
        self.edit.setFocus()

    def _samples(self):
        seen_rows = []
        for key, attr in self.cell_targets:
            if key not in seen_rows:
                seen_rows.append(key)
            if len(seen_rows) > _PREVIEW_LIMIT:
                break
        return [(k, a) for k, a in self.cell_targets if k in seen_rows[:_PREVIEW_LIMIT]]

    def _refresh_preview(self) -> None:
        template = self.edit.text().strip()
        self._ok.setEnabled(bool(template))
        if not template:
            self.preview.setText("(type a template above)")
            return
        lines = []
        for key, attr in self._samples():
            item = self.bridge.controller.get(key)
            if item is None:
                continue
            resolved = self.bridge.resolve_rename_template(key, template, attr)
            label = _FIELD_LABELS.get(attr, attr)
            current = getattr(item, attr, "")
            name = item.source_name or key
            lines.append(f"{name}  ·  {label}: {current}  →  {resolved}")
        shown = len(self._samples())
        total = len(self.cell_targets)
        if total > shown:
            lines.append(f"… and {total - shown} more")
        self.preview.setText("\n".join(lines) or "(nothing selected)")

    def _accept(self) -> None:
        template = self.edit.text().strip()
        if not template:
            return
        self.bridge.rename_cells(self.cell_targets, template)
        self.accept()

    @staticmethod
    def rename_selected(bridge, cell_targets, parent=None) -> bool:
        """True if Apply was clicked (and rename_cells ran)."""
        dlg = RenameCellsDialog(bridge, cell_targets, parent=parent)
        return exec_dialog(dlg) == DIALOG_ACCEPTED
