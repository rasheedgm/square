"""
PatternRuleDialog — manage reusable pattern-based tag rules for the current
incoming root: "tag anything matching this pattern as X, wherever it occurs
in the tree" — instead of tagging one folder/depth at a time. Rules are
saved with the root's sidecar and, via "Save Tagging as Preset", become part
of a reusable Ingest Preset for future batches with the same convention.
"""

from Qt import QtWidgets, QtCore, QtGui
from square_core.folder_mapper import (
    PatternRule, ACTION_LEVEL, ACTION_MEDIA_TYPE, ACTION_TOKEN_PRESET,
    LEVEL_SEQ, LEVEL_SHOT, LEVEL_MEDIA_NAME, LEVEL_MEDIA_TYPE, LEVEL_VERSION,
)
from square_core.config import StudioConfig
from tools.qt_compat import DIALOG_ACCEPTED, HEADER_RESIZE_STRETCH, SELECT_ROWS, TEXT_SELECTABLE_BY_MOUSE

LEVEL_CHOICES = [
    (LEVEL_SEQ,        "Sequence (SEQ)"),
    (LEVEL_SHOT,       "Shot (SHOT)"),
    (LEVEL_MEDIA_NAME, "Media Name (NAME)"),
    (LEVEL_MEDIA_TYPE, "Media Type (TYPE)"),
    (LEVEL_VERSION,    "Version (VER)"),
]

ACTION_CHOICES = [
    ("Tag as Level",              ACTION_LEVEL),
    ("Tag as Media Type",         ACTION_MEDIA_TYPE),
    ("Parse with Token Preset",   ACTION_TOKEN_PRESET),
]


class PatternRuleEditDialog(QtWidgets.QDialog):
    """Single pattern-rule editor with a live match-count preview against the current mapper."""

    def __init__(self, mapper, rule=None, seed_text="", default_target="folder", parent=None):
        super(PatternRuleEditDialog, self).__init__(parent)
        self.setWindowTitle("Pattern Tag Rule")
        self.setMinimumWidth(480)
        self.mapper = mapper
        self.config = StudioConfig()
        self.result_rule = None
        self._build_ui(rule, seed_text, default_target)

    def _build_ui(self, rule, seed_text, default_target="folder"):
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()

        self.name_edit = QtWidgets.QLineEdit(rule.name if rule else "Custom Pattern")
        form.addRow("Rule Name:", self.name_edit)

        self.pattern_edit = QtWidgets.QLineEdit(rule.pattern if rule else seed_text)
        self.pattern_edit.setPlaceholderText(r"e.g. (?i)^SH\d{3,4}$  -- add a (...) capture group to extract just part of the name")
        self.pattern_edit.setToolTip(
            "No prefix is assumed and none is invented -- match whatever your\n"
            "footage actually looks like (letters included, e.g. gfg_010_a).\n"
            "Add a (...) capture group to extract just part of the matched name\n"
            "(e.g. \"gfg_(\\d+_[a-z])\" captures \"010_a\"); without one, the whole\n"
            "matched text is used as the tag value."
        )
        self.pattern_edit.textChanged.connect(self._update_preview)
        form.addRow("Pattern:", self.pattern_edit)

        self.regex_radio = QtWidgets.QRadioButton("Regex")
        self.glob_radio  = QtWidgets.QRadioButton("Glob (*, ?)")
        self.pattern_type_group = QtWidgets.QButtonGroup(self)
        self.pattern_type_group.addButton(self.regex_radio)
        self.pattern_type_group.addButton(self.glob_radio)
        self.regex_radio.setChecked(rule.is_regex if rule else True)
        self.glob_radio.setChecked((not rule.is_regex) if rule else False)
        self.regex_radio.toggled.connect(self._update_preview)
        self.glob_radio.toggled.connect(self._update_preview)
        pat_type_row = QtWidgets.QHBoxLayout()
        pat_type_row.addWidget(self.regex_radio)
        pat_type_row.addWidget(self.glob_radio)
        form.addRow("Pattern Type:", pat_type_row)

        self.target_combo = QtWidgets.QComboBox()
        self.target_combo.addItems(["folder", "file", "both"])
        self.target_combo.setCurrentText(rule.target if rule else default_target)
        self.target_combo.currentTextChanged.connect(self._update_preview)
        form.addRow("Match Against:", self.target_combo)

        self.whole_scope_radio = QtWidgets.QRadioButton("Whole name (safer)")
        self.anywhere_scope_radio = QtWidgets.QRadioButton("Anywhere in name")
        self.whole_scope_radio.setToolTip(
            "The entire name must match. Recommended: a rule meant for exact shot\n"
            "folders like \"SH0100\" won't also catch \"SH0100_ref\" or \"SH0100_edl\"."
        )
        self.anywhere_scope_radio.setToolTip(
            "Matches as a substring anywhere in the name -- useful for things like\n"
            "\"anything with 'ref' in the filename\", but can over-match (e.g. a bare\n"
            "\"sh10\" would also match \"sh10_ref\" and \"sh10_edl\", not just the shot itself)."
        )
        # QRadioButtons only auto-exclude when Qt considers them siblings at the
        # time it matters -- nested layouts assembled after the fact don't
        # reliably qualify, so use an explicit group rather than relying on it.
        self.scope_group = QtWidgets.QButtonGroup(self)
        self.scope_group.addButton(self.whole_scope_radio)
        self.scope_group.addButton(self.anywhere_scope_radio)
        self.whole_scope_radio.setChecked((rule.match_scope if rule else "whole") == "whole")
        self.anywhere_scope_radio.setChecked((rule.match_scope if rule else "whole") == "anywhere")
        self.whole_scope_radio.toggled.connect(self._update_preview)
        self.anywhere_scope_radio.toggled.connect(self._update_preview)
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(self.whole_scope_radio)
        scope_row.addWidget(self.anywhere_scope_radio)
        form.addRow("Match Scope:", scope_row)

        self.any_depth_check = QtWidgets.QCheckBox("Any depth")
        self.any_depth_check.setChecked(True if not rule else (rule.min_depth is None and rule.max_depth is None))
        self.min_depth_spin = QtWidgets.QSpinBox()
        self.min_depth_spin.setRange(1, 50)
        self.max_depth_spin = QtWidgets.QSpinBox()
        self.max_depth_spin.setRange(1, 50)
        self.max_depth_spin.setValue(10)
        if rule and rule.min_depth:
            self.min_depth_spin.setValue(rule.min_depth)
        if rule and rule.max_depth:
            self.max_depth_spin.setValue(rule.max_depth)
        depth_row = QtWidgets.QHBoxLayout()
        depth_row.addWidget(self.any_depth_check)
        depth_row.addWidget(QtWidgets.QLabel("From"))
        depth_row.addWidget(self.min_depth_spin)
        depth_row.addWidget(QtWidgets.QLabel("to"))
        depth_row.addWidget(self.max_depth_spin)

        def _on_any_depth_toggled(checked):
            self.min_depth_spin.setEnabled(not checked)
            self.max_depth_spin.setEnabled(not checked)
            self._update_preview()

        self.any_depth_check.toggled.connect(_on_any_depth_toggled)
        self.min_depth_spin.valueChanged.connect(self._update_preview)
        self.max_depth_spin.valueChanged.connect(self._update_preview)
        self.min_depth_spin.setEnabled(not self.any_depth_check.isChecked())
        self.max_depth_spin.setEnabled(not self.any_depth_check.isChecked())
        form.addRow("Depth Range:", depth_row)

        self.action_combo = QtWidgets.QComboBox()
        for label, value in ACTION_CHOICES:
            self.action_combo.addItem(label, value)
        form.addRow("Action:", self.action_combo)

        self.value_stack = QtWidgets.QStackedWidget()

        level_page = QtWidgets.QWidget()
        level_l = QtWidgets.QHBoxLayout(level_page)
        level_l.setContentsMargins(0, 0, 0, 0)
        self.level_combo = QtWidgets.QComboBox()
        for lvl, label in LEVEL_CHOICES:
            self.level_combo.addItem(label, lvl)
        level_l.addWidget(self.level_combo)
        self.value_stack.addWidget(level_page)

        mtype_page = QtWidgets.QWidget()
        mtype_l = QtWidgets.QHBoxLayout(mtype_page)
        mtype_l.setContentsMargins(0, 0, 0, 0)
        self.mtype_combo = QtWidgets.QComboBox()
        self.mtype_combo.setEditable(True)
        self.mtype_combo.addItems(list(self.config.media_type_configs.keys()))
        mtype_l.addWidget(self.mtype_combo)
        self.value_stack.addWidget(mtype_page)

        token_page = QtWidgets.QWidget()
        token_l = QtWidgets.QHBoxLayout(token_page)
        token_l.setContentsMargins(0, 0, 0, 0)
        self.token_combo = QtWidgets.QComboBox()
        self.token_combo.addItems(list(self.config.token_presets.keys()))
        token_l.addWidget(self.token_combo)
        self.value_stack.addWidget(token_page)

        self.level_combo.currentIndexChanged.connect(self._update_preview)
        self.mtype_combo.currentTextChanged.connect(self._update_preview)
        self.token_combo.currentTextChanged.connect(self._update_preview)
        self.action_combo.currentIndexChanged.connect(self.value_stack.setCurrentIndex)
        self.action_combo.currentIndexChanged.connect(self._update_preview)
        form.addRow("Value:", self.value_stack)

        layout.addLayout(form)

        self.preview_lbl = QtWidgets.QLabel("0 matches")
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        self.preview_lbl.setStyleSheet("color:#60A5FA; font-weight:bold;")
        layout.addWidget(self.preview_lbl)

        btn_row = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("Save Rule")
        ok_btn.setStyleSheet("background-color:#059669; font-weight:bold;")
        ok_btn.clicked.connect(self._on_accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        # Restore existing rule's action/value selection
        if rule:
            idx = {ACTION_LEVEL: 0, ACTION_MEDIA_TYPE: 1, ACTION_TOKEN_PRESET: 2}.get(rule.action, 0)
            self.action_combo.setCurrentIndex(idx)
            self.value_stack.setCurrentIndex(idx)
            if rule.action == ACTION_LEVEL and rule.level:
                li = self.level_combo.findData(rule.level)
                if li >= 0:
                    self.level_combo.setCurrentIndex(li)
            elif rule.action == ACTION_MEDIA_TYPE and rule.media_type:
                self.mtype_combo.setCurrentText(rule.media_type)
            elif rule.action == ACTION_TOKEN_PRESET and rule.token_preset_name:
                self.token_combo.setCurrentText(rule.token_preset_name)

        self._update_preview()

    def _current_rule(self):
        min_d = None if self.any_depth_check.isChecked() else self.min_depth_spin.value()
        max_d = None if self.any_depth_check.isChecked() else self.max_depth_spin.value()
        action = self.action_combo.currentData()
        return PatternRule(
            name=self.name_edit.text().strip() or "Custom Pattern",
            pattern=self.pattern_edit.text(),
            is_regex=self.regex_radio.isChecked(),
            target=self.target_combo.currentText(),
            min_depth=min_d, max_depth=max_d,
            match_scope="whole" if self.whole_scope_radio.isChecked() else "anywhere",
            action=action,
            level=self.level_combo.currentData() if action == ACTION_LEVEL else None,
            media_type=self.mtype_combo.currentText() if action == ACTION_MEDIA_TYPE else None,
            token_preset_name=self.token_combo.currentText() if action == ACTION_TOKEN_PRESET else None,
        )

    def _update_preview(self, *_args):
        rule = self._current_rule()
        try:
            samples = self.mapper.sample_pattern_matches(rule, limit=5) if self.mapper else []
            count = self.mapper.count_pattern_matches(rule) if self.mapper else 0
            if not count:
                self.preview_lbl.setText("0 matches in current tree")
                self.preview_lbl.setStyleSheet("color:#F59E0B; font-weight:bold;")
                return
            lines = [f"{count} match{'es' if count != 1 else ''} in current tree -- extracted value shown:"]
            for name, extracted in samples:
                shown_name = name if len(name) <= 40 else name[:37] + "..."
                lines.append(f"  {shown_name}  →  \"{extracted}\"")
            if count > len(samples):
                lines.append(f"  ... and {count - len(samples)} more")
            self.preview_lbl.setText("\n".join(lines))
            self.preview_lbl.setStyleSheet("color:#60A5FA; font-weight:bold;")
        except Exception as e:
            self.preview_lbl.setText(f"Invalid pattern: {e}")
            self.preview_lbl.setStyleSheet("color:#EF4444; font-weight:bold;")

    def _on_accept(self):
        rule = self._current_rule()
        if not rule.pattern:
            QtWidgets.QMessageBox.warning(self, "Pattern Required", "Enter a pattern to match against.")
            return
        self.result_rule = rule
        self.accept()


class PatternRuleDialog(QtWidgets.QDialog):
    """Lists & manages the pattern rules active for the current incoming root."""

    def __init__(self, mapper, seed_text="", parent=None):
        super(PatternRuleDialog, self).__init__(parent)
        self.setWindowTitle("🏷️ Pattern Tag Rules")
        self.setMinimumSize(560, 360)
        self.mapper = mapper
        self.seed_text = seed_text
        self._build_ui()
        self._refresh_table()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        hdr = QtWidgets.QLabel(
            "Tag anything matching a pattern, anywhere in the tree — reusable across "
            "future batches by saving the tagging as an Ingest Preset."
        )
        hdr.setWordWrap(True)
        hdr.setStyleSheet("color:#94A3B8;")
        layout.addWidget(hdr)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Name", "Pattern", "Target", "Action", "Matches"])
        self.table.horizontalHeader().setSectionResizeMode(1, HEADER_RESIZE_STRETCH)
        self.table.setSelectionBehavior(SELECT_ROWS)
        layout.addWidget(self.table, stretch=1)

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ Add Rule")
        add_btn.clicked.connect(self._on_add)
        edit_btn = QtWidgets.QPushButton("Edit Selected")
        edit_btn.clicked.connect(self._on_edit)
        remove_btn = QtWidgets.QPushButton("Remove Selected")
        remove_btn.setStyleSheet("background:#7F1D1D; color:white;")
        remove_btn.clicked.connect(self._on_remove)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _refresh_table(self):
        rules = self.mapper.get_pattern_rules() if self.mapper else []
        self.table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            count = self.mapper.count_pattern_matches(rule)
            action_label = {
                ACTION_LEVEL:       f"Level: {rule.level}",
                ACTION_MEDIA_TYPE:  f"Type: {rule.media_type}",
                ACTION_TOKEN_PRESET: f"Token Preset: {rule.token_preset_name}",
            }.get(rule.action, rule.action)
            for col, text in enumerate([rule.name, rule.pattern, rule.target, action_label, str(count)]):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(text))

    def _on_add(self):
        dlg = PatternRuleEditDialog(self.mapper, seed_text=self.seed_text, parent=self)
        res = dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
        if res == DIALOG_ACCEPTED and dlg.result_rule:
            self.mapper.add_pattern_rule(dlg.result_rule)
            self.mapper.save()
            self._refresh_table()

    def _on_edit(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        rules = self.mapper.get_pattern_rules()
        idx = rows[0]
        if idx >= len(rules):
            return
        dlg = PatternRuleEditDialog(self.mapper, rule=rules[idx], parent=self)
        res = dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
        if res == DIALOG_ACCEPTED and dlg.result_rule:
            self.mapper.remove_pattern_rule(idx)
            self.mapper.add_pattern_rule(dlg.result_rule)
            self.mapper.save()
            self._refresh_table()

    def _on_remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for idx in rows:
            self.mapper.remove_pattern_rule(idx)
        self.mapper.save()
        self._refresh_table()
