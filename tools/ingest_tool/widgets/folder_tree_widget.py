"""
FolderTreeWidget — Custom QTreeWidget that shows folder/file structure
with image-sequence grouping and smart right-click level tagging.

Key behaviour:
  - Folders expand/collapse normally
  - Image sequences are collapsed to one line: NAME.####.EXT  1001-1015 · 15f
  - Videos and single images appear as file nodes
  - Hidden files (starting with .) are skipped
  - No pill badges on items — tagged folders get coloured text
  - Right-click menu is context-aware (based on ancestor tags)
  - "Analyse Media" button replaces the old "Scan Folder"
"""

import os
import re
from pathlib import Path
from collections import defaultdict

from Qt import QtWidgets, QtCore, QtGui

from square_core.folder_mapper import (
    FolderMapper,
    LEVEL_SEQ, LEVEL_SHOT, LEVEL_MEDIA_NAME, LEVEL_MEDIA_TYPE, LEVEL_VERSION,
    SUPPORTED_IMAGE_EXTS, SUPPORTED_VIDEO_EXTS,
)
from tools.qt_compat import CONTEXT_MENU_CUSTOM, ALIGN_CENTER, EXTENDED_SELECTION

# ── Colours for tagged-folder text ──────────────────────────────────
LEVEL_FG = {
    LEVEL_SEQ:        "#60A5FA",   # blue-400
    LEVEL_SHOT:       "#34D399",   # emerald-400
    LEVEL_MEDIA_NAME: "#FBBF24",   # amber-400
    LEVEL_MEDIA_TYPE: "#A5F3FC",   # cyan-300
    LEVEL_VERSION:    "#C084FC",   # purple-400
}
LEVEL_LABEL = {
    LEVEL_SEQ:        "SEQ",
    LEVEL_SHOT:       "SHOT",
    LEVEL_MEDIA_NAME: "NAME",
    LEVEL_MEDIA_TYPE: "TYPE",
    LEVEL_VERSION:    "VER",
}

# Item data roles (integer literals for Qt5/Qt6 compatibility)
ROLE_PATH       = 256   # Qt.UserRole
ROLE_KIND       = 257   # Qt.UserRole + 1
ROLE_LEVEL      = 258   # Qt.UserRole + 2  — folder level tag for the pill delegate
ROLE_MEDIA_TYPE = 259   # Qt.UserRole + 3  — media type label on seq/video items


class TagPillDelegate(QtWidgets.QStyledItemDelegate):
    """
    Paints a small coloured pill  [ SEQ ] / [ SHOT ] / [ NAME ] / [ TYPE ] / [ VER ]
    on the right side of any folder row that has a level tag.
    Reads the level from item data at ROLE_LEVEL.
    """

    # Pill geometry
    _PILL_H  = 14
    _MARGIN  = 6
    _PAD_X   = 7

    # Background / foreground per level / media type
    _BG = {
        LEVEL_SEQ:        ("#1D4ED8", "#BFDBFE"),
        LEVEL_SHOT:       ("#065F46", "#A7F3D0"),
        LEVEL_MEDIA_NAME: ("#78350F", "#FDE68A"),
        LEVEL_MEDIA_TYPE: ("#164E63", "#A5F3FC"),
        LEVEL_VERSION:    ("#581C87", "#E9D5FF"),
        "SEQ":            ("#1D4ED8", "#BFDBFE"),
        "SHOT":           ("#065F46", "#A7F3D0"),
        "NAME":           ("#78350F", "#FDE68A"),
        "MEDIA":          ("#78350F", "#FDE68A"),
        "TYPE":           ("#164E63", "#A5F3FC"),
        "MEDIA_TYPE":     ("#164E63", "#A5F3FC"),
        "VER":            ("#581C87", "#E9D5FF"),
        "VERSION":        ("#581C87", "#E9D5FF"),
        "PLATE":          ("#78350F", "#FDE68A"),
        "REF":            ("#581C87", "#E9D5FF"),
        "BG PLATE":       ("#164E63", "#A5F3FC"),
        "COMP RENDER":    ("#065F46", "#A7F3D0"),
        "PRECOMP":        ("#1D4ED8", "#BFDBFE"),
    }

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        level = index.data(ROLE_LEVEL)
        mtype = index.data(ROLE_MEDIA_TYPE)

        tag = level or mtype
        if not tag:
            return

        tag_key = str(tag).upper()
        bg_hex, fg_hex = self._BG.get(tag_key, ("#1D4ED8", "#BFDBFE"))
        label = LEVEL_LABEL.get(tag, str(tag).upper())

        painter.save()
        fm     = painter.fontMetrics()
        pw     = fm.horizontalAdvance(label) + self._PAD_X * 2
        ph     = self._PILL_H
        px     = option.rect.right() - pw - self._MARGIN
        py     = option.rect.center().y() - ph // 2
        rect   = QtCore.QRect(px, py, pw, ph)

        painter.setBrush(QtGui.QColor(bg_hex))
        try:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
        except AttributeError:
            painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(rect, 3, 3)

        f = QtGui.QFont(painter.font())
        f.setPixelSize(10)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(fg_hex))
        painter.drawText(rect, ALIGN_CENTER, label)
        painter.restore()

# Image-file regex (grouped into sequences)
RE_DOTTED = re.compile(
    r"^(.*?)[._](\d{3,6})\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE
)
RE_BARE = re.compile(
    r"^(\d{3,6})\.(exr|dpx|png|jpg|jpeg|tif|tiff)$", re.IGNORECASE
)

IMG_EXTS  = {e.lstrip(".") for e in SUPPORTED_IMAGE_EXTS}
VID_EXTS  = {e.lstrip(".") for e in SUPPORTED_VIDEO_EXTS}


def _group_files(folder: Path, files: list):
    """
    Split a list of file names into:
      sequences: [(prefix, ext, frame_list), ...]
      singles:   [(name, kind), ...]   kind = 'video' | 'image' | 'other'
    """
    seq_groups = defaultdict(list)  # (prefix, ext) -> [frame_num, ...]
    singles = []

    for name in files:
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        m_dot  = RE_DOTTED.match(name)
        m_bare = RE_BARE.match(name)

        if m_dot:
            prefix = m_dot.group(1)
            frame  = int(m_dot.group(2))
            fext   = m_dot.group(3).lower()
            seq_groups[(prefix, fext)].append(frame)
        elif m_bare:
            frame = int(m_bare.group(1))
            fext  = m_bare.group(2).lower()
            seq_groups[(folder.name, fext)].append(frame)
        elif ext in VID_EXTS:
            singles.append((name, "video"))
        elif ext in IMG_EXTS:
            singles.append((name, "image"))
        else:
            pass  # skip .json, .txt, etc.

    sequences = []
    for (prefix, ext), frames in seq_groups.items():
        frames.sort()
        sequences.append((prefix, ext, frames))

    return sequences, singles


def _frame_range_str(frames: list) -> str:
    if not frames:
        return ""
    mn, mx, cnt = min(frames), max(frames), len(frames)
    return f"{mn}–{mx}  ·  {cnt}f"


class FolderTreeWidget(QtWidgets.QWidget):
    """
    Left panel: custom folder+file tree with image-sequence grouping
    and smart depth-tagging via right-click.

    Emits: analyse_requested(root_path: str, folder_mapper: FolderMapper)
    """

    # Signal emitted when user clicks Load (is_update=False) or Update (is_update=True)
    # Signal signature: (root_path: str, mapper: FolderMapper, selected_paths: set/None, is_update: bool)
    load_requested = QtCore.Signal(str, object, object, bool)
    scan_requested = load_requested   # back-compat alias

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root_path = None
        self._mapper    = None
        from square_core.config import StudioConfig
        self.config = StudioConfig()
        self.setAcceptDrops(True)
        self.setMinimumWidth(280)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(6)

        # ── Action buttons ──
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(4)

        self._browse_btn = QtWidgets.QPushButton("Browse…")
        self._browse_btn.setToolTip("Select incoming media root folder")
        self._browse_btn.setFixedHeight(28)
        self._browse_btn.clicked.connect(self._on_browse)

        self._preset_combo = QtWidgets.QComboBox()
        self._preset_combo.setFixedHeight(28)
        self._preset_combo.setToolTip("Load or Save Tag Hierarchy Preset")
        self._refresh_preset_combo()
        self._preset_combo.activated.connect(self._on_preset_combo_activated)

        self._clear_btn = QtWidgets.QPushButton("Clear")
        self._clear_btn.setToolTip("Remove all level tags")
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setEnabled(False)
        self._clear_btn.clicked.connect(self._on_clear_tags)

        btn_row.addWidget(self._browse_btn)
        btn_row.addWidget(self._preset_combo, stretch=1)
        btn_row.addWidget(self._clear_btn)
        layout.addLayout(btn_row)

        # ── Path label (only — no blue tag-map text) ──
        self._path_lbl = QtWidgets.QLabel("No folder loaded — browse or drag here")
        self._path_lbl.setStyleSheet(
            "font-size:10px; color:#4B5563; background:transparent;"
        )
        self._path_lbl.setWordWrap(True)
        layout.addWidget(self._path_lbl)

        # Pre-build icons (reused for every item)
        style = QtWidgets.QApplication.style()
        try:
            sp = QtWidgets.QStyle.StandardPixmap
            self._icon_folder   = style.standardIcon(sp.SP_DirIcon)
            self._icon_file     = style.standardIcon(sp.SP_FileIcon)
            self._icon_film     = style.standardIcon(sp.SP_FileDialogDetailedView)
        except AttributeError:
            self._icon_folder   = style.standardIcon(QtWidgets.QStyle.SP_DirIcon)
            self._icon_file     = style.standardIcon(QtWidgets.QStyle.SP_FileIcon)
            self._icon_film     = style.standardIcon(QtWidgets.QStyle.SP_FileDialogDetailedView)

        # ── Tree ──
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        self._tree.setSelectionMode(EXTENDED_SELECTION)
        self._tree.setContextMenuPolicy(CONTEXT_MENU_CUSTOM)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        self._tree.setIndentation(14)
        self._tree.setAnimated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setIconSize(QtCore.QSize(14, 14))
        self._tree.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        # Attach pill delegate — draws level tags on folder rows
        self._pill_delegate = TagPillDelegate(self._tree)
        self._tree.setItemDelegateForColumn(0, self._pill_delegate)
        self._tree.setStyleSheet("""
            QTreeWidget {
                background-color: #131720;
                border: 1px solid #252D3D;
                border-radius: 6px;
                color: #9CA3AF;
                font-size: 12px;
                outline: none;
            }
            QTreeWidget::item {
                height: 22px;
                padding-left: 2px;
            }
            QTreeWidget::item:hover {
                background-color: #1E2740;
                color: #E2E8F0;
            }
            QTreeWidget::item:selected {
                background-color: #1D4ED8;
                color: #FFFFFF;
            }
            QTreeWidget::branch {
                background-color: #131720;
            }
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                border-image: none;
                image: none;
            }
        """)
        layout.addWidget(self._tree, stretch=1)

        # ── Drop hint ──
        self._drop_hint = QtWidgets.QLabel("or drag a folder here")
        self._drop_hint.setAlignment(ALIGN_CENTER)
        self._drop_hint.setStyleSheet(
            "font-size:11px; color:#2D3A52; background:transparent;"
        )
        layout.addWidget(self._drop_hint)

        # ── Load | Update action buttons ──
        load_btn_layout = QtWidgets.QHBoxLayout()
        load_btn_layout.setSpacing(6)

        self._load_btn = QtWidgets.QPushButton("→  Load")
        self._load_btn.setFixedHeight(36)
        self._load_btn.setToolTip("Clear table and load selected items from tree")
        self._load_btn.setStyleSheet(
            "QPushButton {"
            "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "               stop:0 #1D4ED8, stop:1 #2563EB);"
            "  color:white; font-weight:bold; font-size:13px;"
            "  border:none; border-radius:6px;"
            "}"
            "QPushButton:hover {"
            "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "               stop:0 #2563EB, stop:1 #3B82F6);"
            "}"
            "QPushButton:disabled { background:#1A2035; color:#374151; }"
        )

        self._update_btn = QtWidgets.QPushButton("+  Update")
        self._update_btn.setFixedHeight(36)
        self._update_btn.setToolTip("Update/append selected items from tree into table without clearing")
        self._update_btn.setStyleSheet(
            "QPushButton {"
            "  background: #0F766E;"
            "  color:white; font-weight:bold; font-size:13px;"
            "  border:none; border-radius:6px;"
            "}"
            "QPushButton:hover {"
            "  background: #0D9488;"
            "}"
            "QPushButton:disabled { background:#1A2035; color:#374151; }"
        )

        self._load_btn.setEnabled(False)
        self._update_btn.setEnabled(False)

        self._load_btn.clicked.connect(lambda: self._on_load(is_update=False))
        self._update_btn.clicked.connect(lambda: self._on_load(is_update=True))

        load_btn_layout.addWidget(self._load_btn, stretch=1)
        load_btn_layout.addWidget(self._update_btn, stretch=1)

        layout.addLayout(load_btn_layout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def root_path(self):
        return self._root_path

    def load_path(self, path: str):
        self._root_path = path
        self._mapper    = FolderMapper(path)

        self._mapper.load()

        self._path_lbl.setText(path)
        self._path_lbl.setStyleSheet(
            "font-size:10px; color:#64748B; background:transparent;"
        )
        self._clear_btn.setEnabled(True)
        self._load_btn.setEnabled(True)
        self._update_btn.setEnabled(True)
        self._drop_hint.hide()

        self._populate_tree()
        self._update_map_label()

    # ------------------------------------------------------------------
    # Drag & Drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self.load_path(path)

    def _populate_tree(self):
        """Rebuild the tree from scratch using fast os.scandir."""
        self._tree.setUpdatesEnabled(False)
        self._tree.clear()
        try:
            if not self._root_path:
                return
            root_path = Path(self._root_path)
            root_item = self._build_dir_item(root_path, is_root=True)
            if root_item:
                self._tree.addTopLevelItem(root_item)
                root_item.setExpanded(True)
                # Auto-expand 3 levels so sequences inside plate folders are visible
                for i in range(root_item.childCount()):
                    c1 = root_item.child(i)
                    c1.setExpanded(True)
                    for j in range(c1.childCount()):
                        c2 = c1.child(j)
                        if c2.data(0, ROLE_KIND) == "folder":
                            c2.setExpanded(True)
        finally:
            self._tree.setUpdatesEnabled(True)

    def _build_dir_item(self, folder: Path, is_root=False):
        """Recursively build a QTreeWidgetItem for a directory using os.scandir."""
        item = QtWidgets.QTreeWidgetItem()
        name = folder.name if not is_root else folder.name
        item.setText(0, name)
        item.setIcon(0, self._icon_folder)
        item.setData(0, ROLE_PATH, str(folder))
        item.setData(0, ROLE_KIND, "folder")
        item.setToolTip(0, str(folder))

        # Apply level colour + store level for the pill delegate
        level = self._mapper.level_of_path(folder) if self._mapper else None
        self._style_folder_item(item, level)

        # Fast directory scan using os.scandir (avoids per-file stat calls)
        subdirs = []
        file_names = []
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        subdirs.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        file_names.append(entry.name)
        except PermissionError:
            return item

        subdirs.sort(key=lambda p: p.name.lower())
        file_names.sort(key=lambda n: n.lower())

        # Group files: sequences + singles
        sequences, singles = _group_files(folder, file_names)

        # Add sequence items
        for prefix, ext, frames in sorted(sequences, key=lambda x: x[0].lower()):
            seq_item = QtWidgets.QTreeWidgetItem()
            padding  = len(str(max(frames)))
            clean_ext = ext.lstrip('.')
            seq_path = str(folder / f"{prefix}.{clean_ext}")
            mtype    = self._mapper.get_media_type(seq_path) if self._mapper else None
            seq_item.setText(
                0,
                f"{prefix}.{'#' * padding}.{clean_ext}   {_frame_range_str(frames)}"
            )
            seq_item.setIcon(0, self._icon_film)
            seq_item.setData(0, ROLE_PATH, seq_path)
            seq_item.setData(0, ROLE_KIND, "sequence")
            seq_item.setData(0, ROLE_MEDIA_TYPE, mtype)
            seq_item.setToolTip(0, f"{len(frames)} frames  ·  {prefix}.{clean_ext}")
            seq_item.setForeground(
                0, QtGui.QColor("#FBBF24" if mtype else "#5B7AA8")
            )
            item.addChild(seq_item)

        # Add single video / image items
        for name_s, kind in sorted(singles, key=lambda x: x[0].lower()):
            s_item = QtWidgets.QTreeWidgetItem()
            s_path = str(folder / name_s)
            mtype  = self._mapper.get_media_type(s_path) if self._mapper else None
            s_item.setText(0, name_s)
            s_item.setIcon(0, self._icon_film if kind == "video" else self._icon_file)
            s_item.setData(0, ROLE_PATH, s_path)
            s_item.setData(0, ROLE_KIND, kind)
            s_item.setData(0, ROLE_MEDIA_TYPE, mtype)
            s_item.setToolTip(0, s_path)
            clr = "#FBBF24" if mtype else ("#5B7BC4" if kind == "video" else "#4B6A8A")
            s_item.setForeground(0, QtGui.QColor(clr))
            item.addChild(s_item)

        # Recurse into subdirs
        for sub in subdirs:
            child = self._build_dir_item(sub)
            item.addChild(child)

        return item

    def _style_folder_item(self, item, level):
        """Apply foreground colour and store level in ROLE_LEVEL for the delegate."""
        item.setData(0, ROLE_LEVEL, level)   # delegate reads this to draw the pill
        if level and level in LEVEL_FG:
            item.setForeground(0, QtGui.QColor(LEVEL_FG[level]))
        else:
            item.setForeground(0, QtGui.QColor("#94A3B8"))

    def _refresh_item_colours(self):
        """Walk tree and refresh item colours & badges after tag changes."""
        def walk(item):
            kind = item.data(0, ROLE_KIND)
            path_str = item.data(0, ROLE_PATH)
            if path_str and self._mapper:
                p = Path(path_str)
                if kind == "folder":
                    level = self._mapper.level_of_path(p)
                    self._style_folder_item(item, level)
                else:
                    mtype = self._mapper.get_media_type(p)
                    item.setData(0, ROLE_MEDIA_TYPE, mtype)
                    clr = "#FBBF24" if mtype else ("#5B7BC4" if kind == "video" else "#4B6A8A")
                    item.setForeground(0, QtGui.QColor(clr))
            for i in range(item.childCount()):
                walk(item.child(i))

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i))
        self._tree.viewport().update()

    # ------------------------------------------------------------------
    # Context Menu — Smart (ancestor-aware)
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos):
        if not self._mapper:
            return

        item = self._tree.itemAt(pos)
        if not item:
            return

        kind     = item.data(0, ROLE_KIND)
        path_str = item.data(0, ROLE_PATH)
        if not path_str:
            return

        gp = self._tree.viewport().mapToGlobal(pos)

        # ── Media item menu (sequence / video / image) ────────────────
        if kind in ("sequence", "video", "image"):
            self._show_media_context_menu(item, Path(path_str), gp)
            return

        # ── Folder item menu ──────────────────────────────────────────
        if kind != "folder":
            return

        path  = Path(path_str)
        depth = self._mapper.depth_of_path(path)
        if depth <= 0:
            return

        current_level  = self._mapper.level_of_path(path)
        ancestor_levs  = [lvl for lvl, _ in self._mapper.ancestor_levels(path)]
        has_seq_above  = LEVEL_SEQ  in ancestor_levs
        has_shot_above = LEVEL_SHOT in ancestor_levs

        menu = QtWidgets.QMenu(self)
        hdr = menu.addAction(f"  {path.name}  (depth {depth})")
        hdr.setEnabled(False)
        menu.addSeparator()

        available = [
            (LEVEL_SEQ,        "Sequence (SEQ)"),
            (LEVEL_SHOT,       "Shot (SHOT)"),
            (LEVEL_MEDIA_NAME, "Media Name (NAME)"),
            (LEVEL_MEDIA_TYPE, "Media Type (TYPE)"),
            (LEVEL_VERSION,    "Version (VER)"),
        ]

        tag_menu = menu.addMenu("Tag Folder as")
        for level, label in available:
            level_submenu = tag_menu.addMenu(label)

            act_this = level_submenu.addAction("This folder only")
            act_this.setCheckable(True)
            act_this.setChecked(
                self._mapper.get_level_for_folder(path) == level
            )
            act_this.triggered.connect(
                lambda checked=False, p=path, l=level: self._tag_folder(p, l)
            )

            if depth >= 0:
                act_depth = level_submenu.addAction(f"All folders at depth {depth}")
                act_depth.setCheckable(True)
                act_depth.setChecked(
                    self._mapper.get_level(depth) == level
                    and self._mapper.get_level_for_folder(path) is None
                )
                act_depth.triggered.connect(
                    lambda checked=False, d=depth, l=level: self._tag_depth(d, l)
                )

        menu.addSeparator()
        token_act = menu.addAction("🏷️ Tag Name Tokens…")
        token_act.triggered.connect(
            lambda checked=False, i=item, p=path: self._open_token_splitter(i, p)
        )

        menu.addSeparator()
        clear_act = menu.addAction("Clear tag")
        clear_act.setEnabled(current_level is not None)
        clear_act.triggered.connect(
            lambda checked=False, p=path, d=depth: self._clear_tag(p, d)
        )

        gp = self._tree.viewport().mapToGlobal(pos)
        if hasattr(menu, "exec"):
            menu.exec(gp)
        else:
            menu.exec_(gp)

    def _refresh_preset_combo(self):
        """Refreshes the hierarchy preset dropdown list with options + Save action."""
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("Presets ▼")
        for name in self.config.hierarchy_presets.keys():
            self._preset_combo.addItem(f"  {name}")
        self._preset_combo.addItem("💾 Save Tagging as Preset…")
        self._preset_combo.blockSignals(False)

    def _on_preset_combo_activated(self, index):
        text = self._preset_combo.itemText(index).strip()
        if text.startswith("💾 Save"):
            self._on_save_hierarchy_preset()
        elif text.startswith("Presets"):
            return
        else:
            preset_name = text
            self._on_preset_selected(preset_name)

    def _on_preset_selected(self, preset_name):
        """Applies a selected Tag Hierarchy Preset to the tree."""
        if not self._mapper or preset_name not in self.config.hierarchy_presets:
            return

        data = self.config.hierarchy_presets[preset_name]
        level_mappings = data.get("level_mappings", {})

        # Clear existing level tags
        self._mapper.clear_all_levels()

        # Apply level mappings
        for depth_str, rule in level_mappings.items():
            depth = int(depth_str)
            rule_type = rule.get("type", "direct")
            if rule_type == "direct" and "tag" in rule:
                self._mapper.set_level(depth, rule["tag"])

        self._mapper.save()
        self.config.active_hierarchy_preset = preset_name
        self.config.save()

        self._refresh_item_colours()
        self._update_map_label()

    def _on_save_hierarchy_preset(self):
        """Saves current tree folder depth tags as a new Hierarchy Preset."""
        if not self._mapper:
            QtWidgets.QMessageBox.information(self, "Save Preset", "Please load a folder tree first before saving a preset.")
            return

        text, ok = QtWidgets.QInputDialog.getText(self, "Save Tag Hierarchy Preset", "Preset Name:")
        if ok and text.strip():
            preset_name = text.strip()
            level_mappings = {}
            for depth, tag in self._mapper._depth_map.items():
                level_mappings[str(depth)] = {"type": "direct", "tag": tag}

            preset_data = {
                "name": preset_name,
                "level_mappings": level_mappings
            }
            self.config.hierarchy_presets[preset_name] = preset_data
            self.config.active_hierarchy_preset = preset_name
            self.config.save()

            self._refresh_preset_combo()

    def _show_media_context_menu(self, item, path: Path, gp):
        """Context menu for sequence / video / image items aligned with Token Tag Modal."""
        menu = QtWidgets.QMenu(self)

        hdr = menu.addAction(f"  {path.name}")
        hdr.setEnabled(False)
        menu.addSeparator()

        current_type = self._mapper.get_media_type(path) if self._mapper else None

        from square_core.config import StudioConfig
        media_types = list(StudioConfig().media_type_configs.keys())

        for mtype in media_types:
            act = menu.addAction(f"Tag as {mtype}")
            act.setCheckable(True)
            act.setChecked(current_type == mtype)
            act.triggered.connect(
                lambda checked=False, t=mtype, i=item, p=path: self._set_media_type(i, p, t)
            )

        custom_act = menu.addAction("Custom Media Type…")
        custom_act.triggered.connect(
            lambda checked=False, i=item, p=path: self._set_media_type_custom(i, p)
        )

        menu.addSeparator()
        token_act = menu.addAction("🏷️ Tag Name Tokens…")
        token_act.triggered.connect(
            lambda checked=False, i=item, p=path: self._open_token_splitter(i, p)
        )

        has_tags = bool(current_type or (self._mapper and (self._mapper.get_token_rule(path) or self._mapper._item_overrides.get(self._mapper._norm_path(path)))))
        if has_tags:
            menu.addSeparator()
            clr_act = menu.addAction("Clear All Tags on Item")
            clr_act.triggered.connect(
                lambda checked=False, i=item, p=path: self._clear_item_tags(i, p)
            )

        if hasattr(menu, "exec"):
            menu.exec(gp)
        else:
            menu.exec_(gp)

    def _clear_item_tags(self, item, path: Path):
        """Clears all media type, token rules, and item overrides for this specific item."""
        if self._mapper:
            self._mapper.clear_token_rule(path)
            self._mapper.set_media_type(path, None)
            self._mapper.save()
        item.setData(0, ROLE_MEDIA_TYPE, None)
        item.setForeground(0, QtGui.QColor("#5B7AA8"))
        idx = self._tree.indexFromItem(item)
        self._tree.update(idx)
        self._tree.viewport().update()

    def _open_token_splitter(self, item, path: Path):
        """Open the interactive TokenSplitterDialog for this item."""
        from tools.ingest_tool.widgets.token_splitter_dialog import TokenSplitterDialog
        from square_core.token_parser import parse_string_with_token_rule

        existing_rule = self._mapper.get_token_rule(path) if self._mapper else None
        dlg = TokenSplitterDialog(raw_text=path.name, current_rule=existing_rule, parent=self)
        exec_res = dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
        if exec_res == QtWidgets.QDialog.Accepted:
            rule = dlg.current_rule

            if dlg.apply_to_all_level:
                # Apply token rule to ALL items at the same depth across the entire tree
                target_depth = self._mapper.depth_of_path(path) if self._mapper else -1

                def _apply_recursive(tree_item):
                    c_path_str = tree_item.data(0, ROLE_PATH)
                    if c_path_str:
                        c_path = Path(c_path_str)
                        c_depth = self._mapper.depth_of_path(c_path) if self._mapper else -1
                        if c_depth == target_depth or tree_item.parent() == item.parent():
                            if self._mapper:
                                self._mapper.set_token_rule(c_path, rule)
                            res = parse_string_with_token_rule(c_path.name, rule)
                            badge_label = res.get("media_type") or res.get("media_name") or res.get("plate_name") or "TOKEN TAG"
                            tree_item.setData(0, ROLE_MEDIA_TYPE, badge_label)

                    for i in range(tree_item.childCount()):
                        _apply_recursive(tree_item.child(i))

                for i in range(self._tree.topLevelItemCount()):
                    _apply_recursive(self._tree.topLevelItem(i))
            else:
                if self._mapper:
                    self._mapper.set_token_rule(path, rule)
                res = dlg.get_parsed_result()
                badge_label = res.get("media_type") or res.get("media_name") or res.get("plate_name") or "TOKEN TAG"
                item.setData(0, ROLE_MEDIA_TYPE, badge_label)

            if self._mapper:
                self._mapper.save()
            self._refresh_item_colours()

    def _set_media_type(self, item, path: Path, type_name):
        """Assign or clear a media type label on a media tree item."""
        if self._mapper:
            self._mapper.set_media_type(path, type_name)
            self._mapper.save()
        item.setData(0, ROLE_MEDIA_TYPE, type_name)
        # Amber = labelled, muted blue = unlabelled
        clr = "#FBBF24" if type_name else "#5B7AA8"
        item.setForeground(0, QtGui.QColor(clr))
        # Force the tree to repaint this row immediately to update pill badge
        idx = self._tree.indexFromItem(item)
        self._tree.update(idx)
        self._tree.viewport().update()

    def _set_media_type_custom(self, item, path: Path):
        """Open an input dialog to enter a custom media type name."""
        current = self._mapper.get_media_type(path) if self._mapper else None
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Custom Media Type",
            "Enter media type name:",
            text=current or ""
        )
        if ok and text.strip():
            self._set_media_type(item, path, text.strip())

    def _tag_folder(self, path: Path, level: str):
        """Tag one specific folder, clear depth-wide tag for this depth."""
        self._mapper.set_level_for_folder(path, level)
        self._mapper.save()
        self._refresh_item_colours()
        self._update_map_label()

    def _tag_depth(self, depth: int, level: str):
        """Tag all folders at this depth (clears any folder override at this depth)."""
        self._mapper.set_level(depth, level)
        # Clear per-folder overrides at this depth so depth-wide wins
        for key in list(self._mapper._folder_overrides.keys()):
            p = Path(key)
            if self._mapper.depth_of_path(p) == depth:
                self._mapper._folder_overrides.pop(key, None)
        self._mapper.save()
        self._refresh_item_colours()
        self._update_map_label()

    def _clear_tag(self, path: Path, depth: int):
        """Clear folder override, depth tag, token rule, item override, and media type."""
        if self._mapper:
            self._mapper.set_level_for_folder(path, None)
            self._mapper.clear_token_rule(path)
            self._mapper.set_media_type(path, None)
            if depth >= 0:
                self._mapper.set_level(depth, None)
            self._mapper.save()
        self._refresh_item_colours()
        self._update_map_label()

    # ------------------------------------------------------------------
    # Button Handlers
    # ------------------------------------------------------------------

    def _on_browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Incoming Media Root Folder"
        )
        if path:
            self.load_path(path)

    def _on_auto_detect(self):
        if not self._mapper:
            return
        self._mapper.auto_detect()
        self._mapper.save()
        self._refresh_item_colours()
        self._update_map_label()

        depth_summary = "  |  ".join(
            f"depth {d} = {v.upper()}"
            for d, v in sorted(self._mapper._depth_map.items())
        ) or "(none)"

        if self._mapper.has_map():
            QtWidgets.QMessageBox.information(
                self, "Auto-Tag",
                f"Detected:\n{depth_summary}\n\nRight-click to adjust."
            )
        else:
            QtWidgets.QMessageBox.warning(
                self, "Auto-Tag",
                "Could not detect levels from folder names.\n"
                "Right-click folders to tag manually."
            )

    def _on_clear_tags(self):
        if self._mapper:
            self._mapper.clear_all_levels()
            self._refresh_item_colours()
            self._update_map_label()

    def get_selected_file_paths(self):
        """
        Return a set of normalized string paths corresponding to currently
        selected tree items (and their recursive children).
        If no items are selected, returns None (meaning select all).
        """
        selected_items = self._tree.selectedItems()
        if not selected_items:
            return None

        paths = set()

        def _collect(item):
            p = item.data(0, ROLE_PATH)
            if p:
                paths.add(os.path.normcase(os.path.abspath(str(p))))
            for i in range(item.childCount()):
                _collect(item.child(i))

        for item in selected_items:
            _collect(item)

        return paths

    def _on_load(self, is_update=False):
        if self._root_path and self._mapper:
            selected_paths = self.get_selected_file_paths()
            self.load_requested.emit(self._root_path, self._mapper, selected_paths, is_update)

    def _on_analyse(self):
        self._on_load(is_update=False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_map_label(self):
        """No-op — the tag-map text label was removed from the panel."""
        pass
