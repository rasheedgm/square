"""
TokenSplitterDialog — Interactive Visual Chip Splitter & Token Rule Tagging Modal
Allows artists to inspect filename/foldername tokens, merge chips, assign roles,
preview results in real-time, and save/load Token Presets.
"""

from Qt import QtWidgets, QtCore, QtGui
from square_core.token_parser import (
    TokenRule,
    split_text_into_tokens,
    parse_string_with_token_rule,
)
from square_core.config import StudioConfig
from tools.qt_compat import TOOLBUTTON_INSTANT_POPUP

QDialog = QtWidgets.QDialog
QVBoxLayout = QtWidgets.QVBoxLayout
QHBoxLayout = QtWidgets.QHBoxLayout
QLabel = QtWidgets.QLabel
QPushButton = QtWidgets.QPushButton
QToolButton = QtWidgets.QToolButton
QMenu = QtWidgets.QMenu
QComboBox = QtWidgets.QComboBox
QGroupBox = QtWidgets.QGroupBox
QFormLayout = QtWidgets.QFormLayout
QLineEdit = QtWidgets.QLineEdit


class TokenChipButton(QPushButton):
    """Interactive chip button representing a single or merged string token."""

    def __init__(self, index, text, parent=None):
        super(TokenChipButton, self).__init__(text, parent)
        self.token_index = index
        self.token_text = text
        self.role_tag = None  # "sequence_code" | "shot_code" | "plate_name" | "version" | "media_type"
        self.fixed_value = None  # literal value override, e.g. media_type="Ref" regardless of token_text
        self.setCheckable(True)
        self.update_chip_style()

    def set_role_tag(self, role, fixed_value=None):
        self.role_tag = role
        self.fixed_value = fixed_value if role else None
        self.update_chip_style()

    def update_chip_style(self):
        # Color badges per role
        badge_colors = {
            "sequence_code": ("#1E3A8A", "#60A5FA", "SEQ"),
            "shot_code":     ("#064E3B", "#34D399", "SHOT"),
            "media_name":    ("#78350F", "#FBBF24", "MEDIA"),
            "plate_name":    ("#78350F", "#FBBF24", "MEDIA"),
            "version":       ("#581C87", "#C084FC", "VER"),
            "media_type":    ("#134E4A", "#2DD4BF", "TYPE"),
        }

        if self.role_tag in badge_colors:
            bg, fg, label = badge_colors[self.role_tag]
            shown_text = f"{self.token_text} → {self.fixed_value}" if self.fixed_value else self.token_text
            display = f"{shown_text}  [{label}]"
            style = f"""
                QPushButton {{
                    background-color: {bg};
                    color: {fg};
                    border: 2px solid {fg};
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: bold;
                    font-size: 12px;
                }}
                QPushButton:checked {{
                    border: 3px solid #FFFFFF;
                }}
            """
        else:
            display = self.token_text
            style = """
                QPushButton {
                    background-color: #374151;
                    color: #F3F4F6;
                    border: 1px solid #4B5563;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #4B5563;
                }
                QPushButton:checked {
                    background-color: #1F2937;
                    border: 2px solid #38BDF8;
                    color: #38BDF8;
                }
            """
        self.setText(display)
        self.setStyleSheet(style)


class TokenSplitterDialog(QDialog):
    """Visual interactive token tagging modal."""

    def __init__(self, raw_text="SQ010_SH0100_Plate_v001.mov", current_rule=None, parent=None):
        super(TokenSplitterDialog, self).__init__(parent)
        self.setWindowTitle("🏷️ Interactive Token Tagging & Splitter")
        self.setMinimumSize(680, 520)

        self.raw_text = raw_text
        self.config = StudioConfig()
        self.delimiter = "_"
        self.chip_buttons = []
        if current_rule:
            self.current_rule = TokenRule.from_dict(current_rule) if isinstance(current_rule, dict) else current_rule
        else:
            self.current_rule = TokenRule(name="Custom Token Rule", delimiter=self.delimiter)

        self.setup_ui()
        self.build_chips()
        self.restore_rule_to_chips()

    def restore_rule_to_chips(self):
        """Restores role tags and merged ranges from self.current_rule to the chips."""
        if not self.current_rule:
            return

        for range_item in self.current_rule.merged_ranges:
            if isinstance(range_item, (list, tuple)) and len(range_item) == 2:
                start_idx, end_idx = range_item
                if 0 <= start_idx < len(self.chip_buttons) and 0 <= end_idx < len(self.chip_buttons):
                    merged_tokens = [self.chip_buttons[i].token_text for i in range(start_idx, end_idx + 1)]
                    merged_text = self.delimiter.join(merged_tokens)
                    first_btn = self.chip_buttons[start_idx]
                    first_btn.token_text = merged_text
                    first_btn.update_chip_style()
                    for i in range(start_idx + 1, end_idx + 1):
                        self.chip_buttons[i].setVisible(False)

        for role, indices in self.current_rule.mapping.items():
            if isinstance(indices, (list, tuple)):
                role_fixed = self.current_rule.fixed_values.get(role, {})
                for idx in indices:
                    if 0 <= idx < len(self.chip_buttons):
                        self.chip_buttons[idx].set_role_tag(role, fixed_value=role_fixed.get(str(idx)))

        self.update_preview()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Header Title & Raw Target Text
        lbl_header = QLabel(f"Target Text:  <span style='color: #60A5FA; font-family: monospace; font-size: 14px;'><b>{self.raw_text}</b></span>")
        lbl_header.setStyleSheet("font-size: 14px;")
        layout.addWidget(lbl_header)

        # Preset Controls Bar
        preset_box = QHBoxLayout()
        preset_box.addWidget(QLabel("Token Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("-- Select Token Preset --")
        for name in self.config.token_presets.keys():
            self.preset_combo.addItem(name)
        self.preset_combo.currentTextChanged.connect(self.on_preset_selected)
        preset_box.addWidget(self.preset_combo, 1)

        self.save_preset_btn = QPushButton("💾 Save Token Preset...")
        self.save_preset_btn.setStyleSheet("background-color: #059669; color: white; font-weight: bold;")
        self.save_preset_btn.clicked.connect(self.on_save_preset)
        preset_box.addWidget(self.save_preset_btn)

        layout.addLayout(preset_box)

        # Token Chips Container
        chips_group = QGroupBox("Interactive Tokens (Click tokens to select, or click 'Merge Selected' to join)")
        self.chips_layout = QHBoxLayout(chips_group)
        self.chips_layout.setSpacing(8)
        layout.addWidget(chips_group)

        # Action Buttons Layout (Merge & Role Assignment)
        act_box = QGroupBox("Assign Role Tag to Selected Tokens")
        act_layout = QHBoxLayout(act_box)
        act_layout.setSpacing(8)

        self.btn_merge = QPushButton("🔗 Merge Selected")
        self.btn_merge.setStyleSheet("background-color: #4B5563; font-weight: bold;")
        self.btn_merge.clicked.connect(self.on_merge_selected)
        act_layout.addWidget(self.btn_merge)

        self.btn_tag_sq = QPushButton("Tag as SEQ")
        self.btn_tag_sq.setStyleSheet("background-color: #1E3A8A; color: #60A5FA; font-weight: bold;")
        self.btn_tag_sq.clicked.connect(lambda: self.assign_role_to_selected("sequence_code"))
        act_layout.addWidget(self.btn_tag_sq)

        self.btn_tag_sh = QPushButton("Tag as SHOT")
        self.btn_tag_sh.setStyleSheet("background-color: #064E3B; color: #34D399; font-weight: bold;")
        self.btn_tag_sh.clicked.connect(lambda: self.assign_role_to_selected("shot_code"))
        act_layout.addWidget(self.btn_tag_sh)

        # Dropdown button for Media Name and Media Types
        self.btn_tag_media = QToolButton()
        self.btn_tag_media.setText("Tag Media Name / Type ▼")
        self.btn_tag_media.setPopupMode(TOOLBUTTON_INSTANT_POPUP)
        self.btn_tag_media.setStyleSheet("background-color: #78350F; color: #FBBF24; font-weight: bold; padding: 4px 8px;")

        media_menu = QMenu(self)
        act_name = media_menu.addAction("Tag as Media Name (Plate/Element)")
        act_name.triggered.connect(lambda: self.assign_role_to_selected("plate_name"))

        act_mtype = media_menu.addAction("Tag as Media Type (Type)")
        act_mtype.triggered.connect(lambda: self.assign_role_to_selected("media_type"))

        media_menu.addSeparator()

        mtypes = list(self.config.media_type_configs.keys()) if hasattr(self, "config") and self.config else ["Plate", "Ref", "BG Plate", "Comp Render", "Precomp", "Element", "LUT", "Audio", "Matte"]
        for mt in mtypes:
            act = media_menu.addAction(f"Tag as {mt}")
            act.triggered.connect(lambda checked=False, t=mt: self.assign_role_to_selected("media_type", fixed_value=t))

        self.btn_tag_media.setMenu(media_menu)
        act_layout.addWidget(self.btn_tag_media)

        self.btn_tag_ver = QPushButton("Tag as VER")
        self.btn_tag_ver.setStyleSheet("background-color: #581C87; color: #C084FC; font-weight: bold;")
        self.btn_tag_ver.clicked.connect(lambda: self.assign_role_to_selected("version"))
        act_layout.addWidget(self.btn_tag_ver)

        self.btn_clear = QPushButton("Clear Role")
        self.btn_clear.clicked.connect(lambda: self.assign_role_to_selected(None))
        act_layout.addWidget(self.btn_clear)

        layout.addWidget(act_box)

        # Live Results Preview Panel
        prev_box = QGroupBox("Live Parsed Output Preview")
        p_layout = QFormLayout(prev_box)
        p_layout.setSpacing(8)

        self.lbl_prev_seq = QLabel("-")
        self.lbl_prev_seq.setStyleSheet("color: #60A5FA; font-weight: bold;")
        self.lbl_prev_sh = QLabel("-")
        self.lbl_prev_sh.setStyleSheet("color: #34D399; font-weight: bold;")
        self.lbl_prev_pl = QLabel("-")
        self.lbl_prev_pl.setStyleSheet("color: #FBBF24; font-weight: bold;")
        self.lbl_prev_ver = QLabel("-")
        self.lbl_prev_ver.setStyleSheet("color: #C084FC; font-weight: bold;")
        self.lbl_prev_type = QLabel("-")
        self.lbl_prev_type.setStyleSheet("color: #2DD4BF; font-weight: bold;")

        p_layout.addRow("Sequence Code:", self.lbl_prev_seq)
        p_layout.addRow("Shot Code:", self.lbl_prev_sh)
        p_layout.addRow("Plate Name:", self.lbl_prev_pl)
        p_layout.addRow("Version Number:", self.lbl_prev_ver)
        p_layout.addRow("Media Type:", self.lbl_prev_type)

        layout.addWidget(prev_box)

        # Dialog Buttons
        self.apply_to_all_level = False
        btn_dialog_layout = QHBoxLayout()

        self.btn_apply_level = QPushButton("⚡ Apply to All at This Level")
        self.btn_apply_level.setStyleSheet("background-color: #3B82F6; font-size: 13px; font-weight: bold;")
        self.btn_apply_level.clicked.connect(self.on_apply_level)

        self.btn_apply = QPushButton("✅ Apply Tags to Item")
        self.btn_apply.setStyleSheet("background-color: #059669; font-size: 13px; font-weight: bold;")
        self.btn_apply.clicked.connect(self.accept)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        btn_dialog_layout.addStretch()
        btn_dialog_layout.addWidget(self.btn_cancel)
        btn_dialog_layout.addWidget(self.btn_apply_level)
        btn_dialog_layout.addWidget(self.btn_apply)

        layout.addLayout(btn_dialog_layout)

    def on_apply_level(self):
        self.apply_to_all_level = True
        self.accept()

    def build_chips(self):
        # Clear existing chips
        for btn in self.chip_buttons:
            self.chips_layout.removeWidget(btn)
            btn.deleteLater()
        self.chip_buttons.clear()

        tokens = split_text_into_tokens(self.raw_text, self.delimiter)
        for idx, token in enumerate(tokens):
            btn = TokenChipButton(idx, token, self)
            self.chip_buttons.append(btn)
            self.chips_layout.addWidget(btn)

        self.update_preview()

    def get_selected_chip_indices(self):
        return [btn.token_index for btn in self.chip_buttons if btn.isChecked()]

    def on_merge_selected(self):
        indices = self.get_selected_chip_indices()
        if len(indices) < 2:
            return
        indices.sort()
        start_idx, end_idx = indices[0], indices[-1]

        # Combine text of range
        merged_tokens = [self.chip_buttons[i].token_text for i in range(start_idx, end_idx + 1)]
        merged_text = self.delimiter.join(merged_tokens)

        # Replace chips
        first_btn = self.chip_buttons[start_idx]
        first_btn.token_text = merged_text
        first_btn.setChecked(True)
        first_btn.update_chip_style()

        # Hide merged subsequent buttons
        for i in range(start_idx + 1, end_idx + 1):
            self.chip_buttons[i].setVisible(False)

        self.current_rule.merged_ranges.append([start_idx, end_idx])
        self.update_preview()

    def assign_role_to_selected(self, role_name, fixed_value=None):
        selected_indices = self.get_selected_chip_indices()
        if not selected_indices:
            return

        # Update mapping dict in current_rule
        if role_name:
            self.current_rule.mapping[role_name] = selected_indices
            if fixed_value is not None:
                role_fixed = self.current_rule.fixed_values.setdefault(role_name, {})
                for idx in selected_indices:
                    role_fixed[str(idx)] = fixed_value
            else:
                # A plain (non-fixed) re-tag of this role replaces any earlier fixed-value
                # overrides for it -- otherwise a stale "Tag as Ref" would linger invisibly.
                self.current_rule.fixed_values.pop(role_name, None)
        else:
            # Clear role
            for key, val in list(self.current_rule.mapping.items()):
                if any(idx in selected_indices for idx in val):
                    del self.current_rule.mapping[key]
                    self.current_rule.fixed_values.pop(key, None)

        # Update chip styles
        for btn in self.chip_buttons:
            if btn.token_index in selected_indices:
                btn.set_role_tag(role_name, fixed_value=fixed_value)
                btn.setChecked(False)

        self.update_preview()

    def update_preview(self):
        res = parse_string_with_token_rule(self.raw_text, self.current_rule)
        self.lbl_prev_seq.setText(res.get("sequence_code") or "-")
        self.lbl_prev_sh.setText(res.get("shot_code") or "-")
        self.lbl_prev_pl.setText(res.get("plate_name") or "-")
        self.lbl_prev_ver.setText(str(res.get("version")) if res.get("version") is not None else "-")
        self.lbl_prev_type.setText(res.get("media_type") or "-")

    def get_parsed_result(self):
        return parse_string_with_token_rule(self.raw_text, self.current_rule)

    def on_save_preset(self):
        text, ok = QtWidgets.QInputDialog.getText(self, "Save Token Preset", "Preset Name:")
        if ok and text.strip():
            preset_name = text.strip()
            self.current_rule.name = preset_name
            self.config.token_presets[preset_name] = self.current_rule.to_dict()
            self.config.save()

            if self.preset_combo.findText(preset_name) < 0:
                self.preset_combo.addItem(preset_name)
            self.preset_combo.setCurrentText(preset_name)

    def on_preset_selected(self, preset_name):
        if preset_name in self.config.token_presets:
            data = self.config.token_presets[preset_name]
            self.current_rule = TokenRule.from_dict(data)

            # Rebuild chips from scratch (undoing any merges from a previously-selected
            # preset) then restore this preset's merges, roles, AND fixed-value overrides
            # in one consistent pass -- same logic used at dialog construction time.
            self.build_chips()
            self.restore_rule_to_chips()
