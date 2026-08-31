"""
Path Pattern builder & manager — the one tagging mechanism: tag one real
example file's whole path (every folder plus the filename), piece by piece,
and save the result as a reusable template that gets matched against every
other file under the same root.

PathPatternBuilderDialog is opened on a real leaf item (an image sequence,
video, or single image) selected in the folder tree. It shows one chip per
path segment; a segment that bundles more than one value (a combined
filename, most often) can be drilled into to reveal its own sub-chips,
tagged the same way. Anything left untouched is required to match exactly;
marking a piece a wildcard is how the user explicitly says "ignore this."

PathPatternManagerDialog lists the patterns already saved for the current
root -- order matters, since the first to match a file wins -- and lets the
studio reorder, quick-edit, or remove them.
"""

import html
import re
from pathlib import Path

from Qt import QtWidgets, QtCore
from square_core.path_pattern import (
    PathPattern, CANONICAL_DISPLAY_NAMES, WILDCARD_TOKEN,
    render_placeholder, is_frame_piece_text, seed_filename_segment,
)
from square_core.token_parser import (
    DEFAULT_DELIMITER_CHARS, tokenize_with_separators, merge_token_indices,
)
from tools.qt_compat import HEADER_RESIZE_STRETCH, SELECT_ROWS, TEXT_SELECTABLE_BY_MOUSE, get_qt_enum

# Canonical role -> (background, foreground, short badge label)
_ROLE_COLORS = {
    "sequence":   ("#1E3A8A", "#60A5FA", "SEQ"),
    "shot":       ("#064E3B", "#34D399", "SHOT"),
    "media_type": ("#134E4A", "#2DD4BF", "TYPE"),
    "media_name": ("#78350F", "#FBBF24", "NAME"),
    "version":    ("#581C87", "#C084FC", "VER"),
}
_CUSTOM_BG, _CUSTOM_FG = "#831843", "#F472B6"   # open/custom tags


class ChipButton(QtWidgets.QPushButton):
    """One taggable piece of a path: a whole path segment, or one sub-token within a drilled segment."""

    def __init__(self, index, text, parent=None):
        super().__init__(text, parent)
        self.index = index
        self.raw_text = text
        self.role = None            # None | one of CANONICAL_DISPLAY_NAMES | a custom tag name
        self.is_wildcard = False
        self.is_frame = is_frame_piece_text(text)   # auto-detected frame-number run -- never user-taggable
        self.setCheckable(not self.is_frame)
        self.setEnabled(not self.is_frame)
        self._update_style()

    def set_role(self, role):
        self.is_wildcard = False
        self.role = role
        self._update_style()

    def set_wildcard(self, is_wildcard=True):
        self.role = None
        self.is_wildcard = is_wildcard
        self._update_style()

    def clear_tag(self):
        self.role = None
        self.is_wildcard = False
        self._update_style()

    def rendered_piece(self) -> str:
        """The template syntax this chip contributes: a placeholder, wildcard, frame run, or its own literal text."""
        if self.is_frame:
            return self.raw_text
        if self.is_wildcard:
            return WILDCARD_TOKEN
        if self.role:
            return render_placeholder(self.role)
        return self.raw_text

    def _update_style(self):
        if self.is_frame:
            self.setText(f"{self.raw_text}  [frame #]")
            self.setStyleSheet(
                "QPushButton { background-color:#1F2937; color:#6B7280; border:1px dashed #374151;"
                " border-radius:6px; padding:6px 10px; font-family: monospace; font-size:12px; }"
            )
            return
        if self.is_wildcard:
            self.setText(f"{self.raw_text}  →  * (any)")
            self.setStyleSheet(
                "QPushButton { background-color:#1F2937; color:#9CA3AF; border:2px dashed #6B7280;"
                " border-radius:6px; padding:6px 10px; font-size:12px; }"
                "QPushButton:checked { border-color:#FFFFFF; }"
            )
            return
        if self.role:
            bg, fg, label = _ROLE_COLORS.get(self.role, (_CUSTOM_BG, _CUSTOM_FG, self.role.upper()))
            self.setText(f"{self.raw_text}  [{label}]")
            self.setStyleSheet(
                f"QPushButton {{ background-color:{bg}; color:{fg}; border:2px solid {fg};"
                " border-radius:6px; padding:6px 10px; font-weight:bold; font-size:12px; }"
                "QPushButton:checked { border:3px solid #FFFFFF; }"
            )
            return
        self.setText(self.raw_text)
        self.setStyleSheet(
            "QPushButton { background-color:#374151; color:#F3F4F6; border:1px solid #4B5563;"
            " border-radius:6px; padding:6px 10px; font-size:12px; }"
            "QPushButton:hover { background-color:#4B5563; }"
            "QPushButton:checked { background-color:#1F2937; border:2px solid #38BDF8; color:#38BDF8; }"
        )


class SegmentRow(QtWidgets.QWidget):
    """
    One path segment (one folder, or the filename): either a single
    whole-segment chip, or -- once drilled -- a row of sub-token chips
    plus a Collapse button to undo the split.
    """

    changed = QtCore.Signal()

    def __init__(self, seg_index, text, auto_drill=False, parent=None):
        super().__init__(parent)
        self.seg_index = seg_index
        self.raw_text = text
        self._drilled = False
        self._whole_chip = None
        self._sub_chips = []
        self._sub_seps = []
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._show_whole()
        if auto_drill:
            self.drill()

    def _clear_layout(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _connect(self, btn):
        btn.toggled.connect(lambda _checked=False: self.changed.emit())

    def _show_whole(self):
        self._clear_layout()
        self._whole_chip = ChipButton(0, self.raw_text)
        self._connect(self._whole_chip)
        self._layout.addWidget(self._whole_chip)
        self._drilled = False
        self._sub_chips = []
        self._sub_seps = []

    def _show_sub_row(self, chips_text, seps):
        self._clear_layout()
        self._sub_chips = []
        self._sub_seps = list(seps)
        for i, chip_text in enumerate(chips_text):
            btn = ChipButton(i, chip_text)
            self._connect(btn)
            self._sub_chips.append(btn)
            self._layout.addWidget(btn)
            if i < len(self._sub_seps):
                sep_lbl = QtWidgets.QLabel(self._sub_seps[i])
                sep_lbl.setStyleSheet("color:#6B7280; font-family: monospace; font-size:13px;")
                self._layout.addWidget(sep_lbl)
        collapse_btn = QtWidgets.QToolButton()
        collapse_btn.setText("⊟")
        collapse_btn.setToolTip("Collapse back to one piece")
        collapse_btn.setAutoRaise(True)
        collapse_btn.clicked.connect(self._on_collapse)
        self._layout.addWidget(collapse_btn)
        self._whole_chip = None
        self._drilled = True

    def drill(self, delimiter_chars=DEFAULT_DELIMITER_CHARS):
        if self._drilled or not self.raw_text:
            return
        chips, seps = tokenize_with_separators(self.raw_text, delimiter_chars)
        if len(chips) < 2:
            return
        self._show_sub_row(chips, seps)
        self.changed.emit()

    def merge_selected(self):
        if not self._drilled:
            return
        indices = sorted(i for i, c in enumerate(self._sub_chips) if c.isChecked())
        if len(indices) < 2:
            return
        texts = [c.raw_text for c in self._sub_chips]
        new_texts, new_seps = merge_token_indices(texts, self._sub_seps, indices[0], indices[-1])
        self._show_sub_row(new_texts, new_seps)
        self.changed.emit()

    def _on_collapse(self):
        self._show_whole()
        self.changed.emit()

    def all_chips(self):
        return list(self._sub_chips) if self._drilled else [self._whole_chip]

    def selected_chips(self):
        return [c for c in self.all_chips() if c.isChecked()]

    def can_drill(self):
        return not self._drilled

    def rendered_template(self) -> str:
        if not self._drilled:
            return self._whole_chip.rendered_piece()
        parts = [self._sub_chips[0].rendered_piece()]
        for i, sep in enumerate(self._sub_seps):
            parts.append(sep)
            parts.append(self._sub_chips[i + 1].rendered_piece())
        return "".join(parts)


class PathPatternBuilderDialog(QtWidgets.QDialog):
    """Builds one Path Pattern by tagging a real example item's whole path."""

    def __init__(self, mapper, item, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build Path Pattern")
        self.setMinimumSize(780, 460)
        self.mapper = mapper
        self.item = item
        self.result_pattern = None
        self._segment_rows = []
        self._rel_segments = self._compute_seed_segments()
        self._build_ui()

    def _compute_seed_segments(self):
        folder = Path(self.item.files[0]).parent if self.item.files else self.mapper.root
        rel_folder = self.mapper._relative_posix(folder) or ""
        folder_segs = rel_folder.split("/") if rel_folder else []
        filename_seg = seed_filename_segment(self.item)
        return [s for s in folder_segs if s] + [filename_seg]

    def _add_existing_match_banner(self, layout):
        """
        The builder always starts fresh from the raw example -- it doesn't
        try to reconstruct which chips an existing saved pattern would have
        tagged, since that's ambiguous in general. Without this, tagging a
        file, then reopening the builder on that same file, looked like the
        earlier tag had vanished. This makes the current state visible
        instead: if a saved pattern already matches this exact file, say so
        up front, with what it's currently extracting.
        """
        if not self.item.files:
            return
        matched_pattern, extracted = self.mapper.match_relative_path(Path(self.item.files[0]))
        if matched_pattern is None:
            return
        # This label mixes literal HTML markup with data (the template
        # string, the extracted values) -- both need escaping, or a
        # placeholder like "<sequence>" is parsed as an unknown tag and
        # silently dropped instead of shown.
        safe_template = html.escape(matched_pattern.template)
        shown = ", ".join(f"{html.escape(k)}={html.escape(str(v))}" for k, v in extracted.items()) or "(no tags captured)"
        banner = QtWidgets.QLabel(
            f"✓ This file already matches a saved pattern: "
            f"<span style='font-family:monospace; color:#34D399;'>{safe_template}</span><br>"
            f"Currently extracting: {shown}<br>"
            f"Building a new pattern below adds another one rather than editing this match -- "
            f"use Patterns… to edit or reorder existing ones instead."
        )
        banner.setWordWrap(True)
        banner.setStyleSheet(
            "background:#064E3B; border:1px solid #10B981; border-radius:6px; "
            "padding:8px; color:#D1FAE5; font-size:11px;"
        )
        layout.addWidget(banner)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        example_path = "/".join([self.mapper.root.name] + self._rel_segments)
        hdr = QtWidgets.QLabel(f"Example:  <span style='color:#60A5FA; font-family:monospace;'>{example_path}</span>")
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        self._add_existing_match_banner(layout)

        hint = QtWidgets.QLabel(
            "Select a piece below, then tag it. Drill into a piece that bundles more than one value "
            "(usually the filename). Anything left untouched must match exactly on every other file; "
            "mark a piece a wildcard to ignore its content instead."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#94A3B8; font-size:11px;")
        layout.addWidget(hint)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(96)
        chips_host = QtWidgets.QWidget()
        chips_row = QtWidgets.QHBoxLayout(chips_host)
        chips_row.setSpacing(4)
        last_idx = len(self._rel_segments) - 1
        for i, seg_text in enumerate(self._rel_segments):
            row = SegmentRow(i, seg_text, auto_drill=(i == last_idx))
            row.changed.connect(self._update_preview)
            self._segment_rows.append(row)
            chips_row.addWidget(row)
            if i != last_idx:
                slash = QtWidgets.QLabel("/")
                slash.setStyleSheet("color:#4B5563; font-size:14px; font-weight:bold;")
                chips_row.addWidget(slash)
        chips_row.addStretch()
        scroll.setWidget(chips_host)
        layout.addWidget(scroll)

        act_box = QtWidgets.QGroupBox("Tag Selected Piece(s)")
        act_v = QtWidgets.QVBoxLayout(act_box)
        act_v.setSpacing(6)

        role_row = QtWidgets.QHBoxLayout()
        role_row.setSpacing(6)
        for role in CANONICAL_DISPLAY_NAMES:
            bg, fg, label = _ROLE_COLORS[role]
            btn = QtWidgets.QPushButton(label)
            btn.setStyleSheet(f"background-color:{bg}; color:{fg}; font-weight:bold;")
            btn.clicked.connect(lambda checked=False, r=role: self._tag_selected(r))
            role_row.addWidget(btn)

        custom_btn = QtWidgets.QPushButton("Custom Tag…")
        custom_btn.setStyleSheet(f"background-color:{_CUSTOM_BG}; color:{_CUSTOM_FG}; font-weight:bold;")
        custom_btn.clicked.connect(self._tag_custom)
        role_row.addWidget(custom_btn)
        role_row.addStretch()
        act_v.addLayout(role_row)

        tool_row = QtWidgets.QHBoxLayout()
        tool_row.setSpacing(6)
        drill_btn = QtWidgets.QPushButton("Drill Into Piece")
        drill_btn.clicked.connect(self._on_drill)
        merge_btn = QtWidgets.QPushButton("Merge Selected")
        merge_btn.clicked.connect(self._on_merge)
        wildcard_btn = QtWidgets.QPushButton("Mark Wildcard (*)")
        wildcard_btn.clicked.connect(self._mark_wildcard)
        clear_btn = QtWidgets.QPushButton("Clear Tag")
        clear_btn.clicked.connect(self._clear_selected)
        tool_row.addWidget(drill_btn)
        tool_row.addWidget(merge_btn)
        tool_row.addWidget(wildcard_btn)
        tool_row.addWidget(clear_btn)
        tool_row.addStretch()
        act_v.addLayout(tool_row)

        layout.addWidget(act_box)

        prev_box = QtWidgets.QGroupBox("Live Preview")
        prev_layout = QtWidgets.QVBoxLayout(prev_box)
        self.preview_lbl = QtWidgets.QLabel()
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        self.preview_lbl.setStyleSheet("font-family: monospace; font-size:11px;")
        # Explicit, not left to Qt's HTML auto-detection: this text is built
        # from template pieces like "<sequence>" that Qt's rich-text
        # heuristic could otherwise decide to parse as a tag and drop.
        self.preview_lbl.setTextFormat(get_qt_enum(QtCore.Qt, "TextFormat", "PlainText"))
        prev_layout.addWidget(self.preview_lbl)
        layout.addWidget(prev_box)

        btn_row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Add Pattern")
        save_btn.setStyleSheet("background-color:#059669; font-weight:bold;")
        save_btn.clicked.connect(self._on_accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        self._update_preview()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _selected_chips(self):
        chips = []
        for row in self._segment_rows:
            chips.extend(row.selected_chips())
        return chips

    def _tag_selected(self, role):
        for chip in self._selected_chips():
            chip.set_role(role)
            chip.setChecked(False)
        self._update_preview()

    def _tag_custom(self):
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Custom Tag", "Tag name (e.g. camera, date, colorspace):"
        )
        if not ok:
            return
        name = re.sub(r"[^A-Za-z0-9_]", "", text.strip())
        if not name:
            QtWidgets.QMessageBox.warning(self, "Custom Tag", "Enter a valid tag name (letters, digits, underscore).")
            return
        if name in CANONICAL_DISPLAY_NAMES:
            QtWidgets.QMessageBox.warning(
                self, "Custom Tag", f'"{name}" is one of the 5 built-in fields -- use its own button instead.'
            )
            return
        self._tag_selected(name)

    def _mark_wildcard(self):
        for chip in self._selected_chips():
            chip.set_wildcard(True)
            chip.setChecked(False)
        self._update_preview()

    def _clear_selected(self):
        for chip in self._selected_chips():
            chip.clear_tag()
            chip.setChecked(False)
        self._update_preview()

    def _on_drill(self):
        for row in self._segment_rows:
            if row.can_drill() and row.selected_chips():
                row.drill()

    def _on_merge(self):
        for row in self._segment_rows:
            if len(row.selected_chips()) >= 2:
                row.merge_selected()

    # ------------------------------------------------------------------
    # Preview / accept
    # ------------------------------------------------------------------

    def _current_template(self) -> str:
        return "/".join(row.rendered_template() for row in self._segment_rows)

    def _update_preview(self):
        template = self._current_template()
        count, total, samples = self.mapper.preview_pattern(template, limit=6)
        lines = [f"Pattern:  {template}", "", f"{count} of {total} item(s) under this root match:"]
        for rel, extracted in samples:
            shown_path = rel if len(rel) <= 60 else "…" + rel[-57:]
            if extracted is None:
                lines.append(f"  ✗  {shown_path}")
            else:
                shown = ", ".join(f"{k}={v}" for k, v in extracted.items()) or "(no tags captured)"
                lines.append(f"  ✓  {shown_path}  →  {shown}")
        if total > len(samples):
            lines.append(f"  … and {total - len(samples)} more")
        self.preview_lbl.setText("\n".join(lines))

    def _on_accept(self):
        template = self._current_template()
        if not template:
            return
        default_name = template if len(template) <= 60 else template[:57] + "..."
        name, ok = QtWidgets.QInputDialog.getText(self, "Pattern Name", "Name this pattern:", text=default_name)
        if not ok:
            return
        self.result_pattern = PathPattern(template=template, name=name.strip() or template)
        self.accept()


class PathPatternManagerDialog(QtWidgets.QDialog):
    """Lists, reorders, quick-edits, and removes the Path Patterns saved for the current root."""

    def __init__(self, mapper, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Path Patterns")
        self.setMinimumSize(680, 380)
        self.mapper = mapper
        self._build_ui()
        self._refresh_table()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        hdr = QtWidgets.QLabel(
            "Tried top to bottom — the first pattern that matches a file wins. Add an exception "
            "(a different shape for one item) as its own pattern rather than editing this one."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet("color:#94A3B8;")
        layout.addWidget(hdr)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["#", "Template", "Matches"])
        self.table.horizontalHeader().setSectionResizeMode(1, HEADER_RESIZE_STRETCH)
        self.table.setSelectionBehavior(SELECT_ROWS)
        layout.addWidget(self.table, stretch=1)

        btn_row = QtWidgets.QHBoxLayout()
        up_btn = QtWidgets.QPushButton("▲ Move Up")
        up_btn.clicked.connect(self._on_move_up)
        down_btn = QtWidgets.QPushButton("▼ Move Down")
        down_btn.clicked.connect(self._on_move_down)
        edit_btn = QtWidgets.QPushButton("Edit Text…")
        edit_btn.clicked.connect(self._on_edit)
        remove_btn = QtWidgets.QPushButton("Remove")
        remove_btn.setStyleSheet("background:#7F1D1D; color:white;")
        remove_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _refresh_table(self):
        patterns = self.mapper.get_path_patterns()
        self.table.setRowCount(len(patterns))
        for row, pattern in enumerate(patterns):
            count, total, _ = self.mapper.preview_pattern(pattern.template, limit=0)
            for col, text in enumerate([str(row + 1), pattern.template, f"{count} / {total}"]):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    def _selected_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return rows[0] if rows else None

    def _on_move_up(self):
        idx = self._selected_row()
        if idx is not None and idx > 0:
            self.mapper.move_path_pattern(idx, idx - 1)
            self.mapper.save()
            self._refresh_table()
            self.table.selectRow(idx - 1)

    def _on_move_down(self):
        idx = self._selected_row()
        if idx is not None and idx < self.table.rowCount() - 1:
            self.mapper.move_path_pattern(idx, idx + 1)
            self.mapper.save()
            self._refresh_table()
            self.table.selectRow(idx + 1)

    def _on_edit(self):
        idx = self._selected_row()
        if idx is None:
            return
        current = self.mapper.get_path_patterns()[idx]
        text, ok = QtWidgets.QInputDialog.getText(self, "Edit Pattern", "Template:", text=current.template)
        if ok and text.strip():
            self.mapper.update_path_pattern(idx, PathPattern(template=text.strip(), name=current.name))
            self.mapper.save()
            self._refresh_table()

    def _on_remove(self):
        idx = self._selected_row()
        if idx is not None:
            self.mapper.remove_path_pattern(idx)
            self.mapper.save()
            self._refresh_table()
