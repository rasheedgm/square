"""
FolderTreeWidget — Custom QTreeWidget that shows folder/file structure
with image-sequence grouping and Path Pattern tagging.

Key behaviour:
  - Folders expand/collapse normally; they carry no tag of their own --
    a Path Pattern is built from one real leaf item's whole path (see
    path_pattern_dialog.py) and matched against every file under root.
  - Image sequences are collapsed to one line: NAME.####.EXT  1001-1015 · 15f
  - Videos and single images appear as file nodes
  - Hidden files (starting with .) are skipped
  - Leaf items get a coloured badge once a manual tag or a saved pattern
    identifies them; folders are never coloured.
  - Right-click on a leaf item: quick media-type tags, or build/apply a
    Path Pattern from that item's whole path.
"""

import os
import re
from pathlib import Path
from collections import defaultdict

from Qt import QtWidgets, QtCore, QtGui

from tools.ingest_tool.core.folder_mapper import FolderMapper
from tools.ingest_tool.core import presets as ingest_presets
from square_core.media.scanner import SUPPORTED_IMAGE_EXTS, SUPPORTED_VIDEO_EXTS
from tools.ingest_tool.widgets.path_pattern_dialog import PathPatternBuilderDialog, PathPatternManagerDialog
from tools.qt_compat import CONTEXT_MENU_CUSTOM, ALIGN_CENTER, EXTENDED_SELECTION, SCROLLBAR_AS_NEEDED, DIALOG_ACCEPTED, PEN_STYLE_NO_PEN

# Item data roles (integer literals for Qt5/Qt6 compatibility)
ROLE_PATH       = 256   # Qt.UserRole
ROLE_KIND       = 257   # Qt.UserRole + 1
ROLE_MEDIA_TYPE = 259   # Qt.UserRole + 3  — badge text on tagged/matched leaf items


class TagPillDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a small amber pill on the right side of any leaf row that has an identified tag, from ROLE_MEDIA_TYPE."""

    _PILL_H  = 14
    _MARGIN  = 6
    _PAD_X   = 7
    _BG = "#78350F"
    _FG = "#FDE68A"

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        tag = index.data(ROLE_MEDIA_TYPE)
        if not tag:
            return

        label = str(tag).upper()
        painter.save()
        fm     = painter.fontMetrics()
        pw     = fm.horizontalAdvance(label) + self._PAD_X * 2
        ph     = self._PILL_H
        px     = option.rect.right() - pw - self._MARGIN
        py     = option.rect.center().y() - ph // 2
        rect   = QtCore.QRect(px, py, pw, ph)

        painter.setBrush(QtGui.QColor(self._BG))
        painter.setPen(PEN_STYLE_NO_PEN)
        painter.drawRoundedRect(rect, 3, 3)

        f = QtGui.QFont(painter.font())
        f.setPixelSize(10)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(self._FG))
        painter.drawText(rect, ALIGN_CENTER, label)
        painter.restore()

# Image-file regex (grouped into sequences) -- for tree display only; the
# actual items ingested are always re-derived via PlateScanner, the single
# source of truth for frame grouping.
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
    Left panel: custom folder+file tree with image-sequence grouping and
    Path Pattern tagging via right-click on a leaf item.

    Emits: analyse_requested(root_path: str, folder_mapper: FolderMapper)
    """

    # Signal emitted when user clicks Load (is_update=False) or Update (is_update=True)
    # Signal signature: (root_path: str, mapper: FolderMapper, selected_paths: set/None, is_update: bool)
    load_requested = QtCore.Signal(str, object, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root_path = None
        self._mapper    = None
        self._pctx = None                       # set via set_project(); drives the
                                                # media-type context menu (source="delivery")
        self._presets = ingest_presets.load()
        self.setAcceptDrops(True)
        self.setMinimumWidth(340)
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
        self._preset_combo.setToolTip("Load or Save an Ingest Preset (a saved list of Path Patterns)")
        self._refresh_preset_combo()
        self._preset_combo.activated.connect(self._on_preset_combo_activated)

        self._patterns_btn = QtWidgets.QPushButton("Patterns…")
        self._patterns_btn.setToolTip("Manage the Path Patterns active for this incoming folder")
        self._patterns_btn.setFixedHeight(28)
        self._patterns_btn.setEnabled(False)
        self._patterns_btn.clicked.connect(self._on_manage_patterns)

        self._clear_btn = QtWidgets.QPushButton("Clear")
        self._clear_btn.setToolTip("Remove all Path Patterns and tags")
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setEnabled(False)
        self._clear_btn.clicked.connect(self._on_clear_tags)

        # Give each button a floor at its own natural (unclipped) width, so
        # a narrow panel squeezes the preset combo (which degrades fine,
        # showing "..." on a long preset name) instead of truncating a
        # button's own label into something unreadable.
        for btn in (self._browse_btn, self._patterns_btn, self._clear_btn):
            btn.setMinimumWidth(btn.sizeHint().width())
        self._preset_combo.setMinimumWidth(60)

        btn_row.addWidget(self._browse_btn)
        btn_row.addWidget(self._preset_combo, stretch=1)
        btn_row.addWidget(self._patterns_btn)
        btn_row.addWidget(self._clear_btn)
        layout.addLayout(btn_row)

        # ── Path label ──
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
        self._tree.setHorizontalScrollBarPolicy(SCROLLBAR_AS_NEEDED)
        # Attach pill delegate — draws a tagged/matched badge on leaf rows
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

        self._path_lbl.setText(path)
        self._path_lbl.setStyleSheet(
            "font-size:10px; color:#64748B; background:transparent;"
        )
        self._clear_btn.setEnabled(True)
        self._patterns_btn.setEnabled(True)
        self._load_btn.setEnabled(True)
        self._update_btn.setEnabled(True)
        self._drop_hint.hide()

        self._populate_tree()
        self._refresh_item_colours()

    # ------------------------------------------------------------------
    # Session state (delivery root + Path Patterns)
    # ------------------------------------------------------------------

    def current_patterns(self) -> list:
        """The delivery's Path Patterns as serializable dicts (for the session file)."""
        if not self._mapper:
            return []
        return [p.to_dict() if hasattr(p, "to_dict") else dict(p)
                for p in self._mapper.get_path_patterns()]

    def current_media_types(self) -> dict:
        """Manual per-item media-type overrides {path: type} (for the session file)."""
        return self._mapper.get_media_types() if self._mapper else {}

    def set_project(self, pctx) -> None:
        """The current project's config -- drives the context menu's media-type
        list (`cfg.media_type_names(source="delivery")`), not a hardcoded list."""
        self._pctx = pctx

    def active_preset(self) -> str:
        return self._presets.get("active", "") or ""

    def restore(self, path: str, patterns=None, media_types=None, preset: str = "") -> None:
        """
        Reopen a delivery from a resumed session: load the folder and
        re-apply its Path Patterns + manual media-type tags. No hidden
        sidecar is read -- the session file is the only source.
        """
        if not path or not os.path.isdir(path):
            return
        self.load_path(path)
        if self._mapper:
            if patterns:
                self._mapper.set_path_patterns(patterns)
            if media_types:
                self._mapper.set_media_types(media_types)
        if preset:
            self._presets["active"] = preset
            self._refresh_preset_combo()
        self._refresh_item_colours()

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
        item.setForeground(0, QtGui.QColor("#94A3B8"))

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
            seq_item.setText(
                0,
                f"{prefix}.{'#' * padding}.{clean_ext}   {_frame_range_str(frames)}"
            )
            seq_item.setIcon(0, self._icon_film)
            seq_item.setData(0, ROLE_PATH, seq_path)
            seq_item.setData(0, ROLE_KIND, "sequence")
            seq_item.setToolTip(0, f"{len(frames)} frames  ·  {prefix}.{clean_ext}")
            seq_item.setForeground(0, QtGui.QColor("#5B7AA8"))
            item.addChild(seq_item)

        # Add single video / image items
        for name_s, kind in sorted(singles, key=lambda x: x[0].lower()):
            s_item = QtWidgets.QTreeWidgetItem()
            s_path = str(folder / name_s)
            s_item.setText(0, name_s)
            s_item.setIcon(0, self._icon_film if kind == "video" else self._icon_file)
            s_item.setData(0, ROLE_PATH, s_path)
            s_item.setData(0, ROLE_KIND, kind)
            s_item.setToolTip(0, s_path)
            clr = "#5B7BC4" if kind == "video" else "#4B6A8A"
            s_item.setForeground(0, QtGui.QColor(clr))
            item.addChild(s_item)

        # Recurse into subdirs
        for sub in subdirs:
            child = self._build_dir_item(sub)
            item.addChild(child)

        return item

    def _resolve_item_for_node(self, path: Path, kind: str, scan_cache=None):
        """
        Reconstructs the real IngestSequenceItem a tree leaf node represents
        (with real file paths, not the tree's synthetic display path), by
        re-running PlateScanner on just that node's own folder.
        """
        from square_core.media.scanner import PlateScanner
        folder = path.parent
        if scan_cache is not None:
            key = str(folder)
            if key not in scan_cache:
                scan_cache[key] = PlateScanner(folder).scan()
            candidates = scan_cache[key]
        else:
            candidates = PlateScanner(folder).scan()

        if kind == "sequence":
            target_ext = path.suffix.lstrip(".")
            target_prefix = path.stem
            for c in candidates:
                if not c.is_video and c.name == target_prefix and c.ext.lstrip(".") == target_ext:
                    return c
            return None
        for c in candidates:
            if c.files and Path(c.files[0]).name == path.name:
                return c
        return None

    def _real_key_for(self, path: Path, kind: str, scan_cache=None) -> Path:
        """A sequence tree node's own ROLE_PATH is a SYNTHETIC display path
        (frame digits stripped, e.g. `plate.exr` for `plate.1001.exr`) -- it
        never equals any real file, so tagging/looking-up by it directly is a
        silent no-op once `FolderMapper.build_items()` re-scans for real
        files. Resolve to the real first-frame file (same real item
        `get_selected_file_paths` already resolves to) so the manual tag
        actually reaches the row it was meant for. Falls back to `path`
        unresolved for a plain image/video leaf, which already IS real."""
        real_item = self._resolve_item_for_node(path, kind, scan_cache=scan_cache)
        if real_item and real_item.files:
            return Path(real_item.files[0])
        return path

    def _refresh_item_colours(self):
        """Walk the tree and refresh each leaf item's badge after a tag/pattern change."""
        if not self._mapper:
            return
        scan_cache = {}

        def walk(tree_item):
            kind = tree_item.data(0, ROLE_KIND)
            path_str = tree_item.data(0, ROLE_PATH)
            if path_str and kind in ("sequence", "video", "image"):
                path = Path(path_str)
                real_item = self._resolve_item_for_node(path, kind, scan_cache=scan_cache)
                real_path = Path(real_item.files[0]) if (real_item and real_item.files) else path
                badge = self._mapper.get_media_type(real_path)
                if not badge and real_item and real_item.files:
                    _, extracted = self._mapper.match_relative_path(Path(real_item.files[0]))
                    if extracted:
                        from square_core.paths.path_pattern import split_canonical_and_extra
                        canonical, _extra = split_canonical_and_extra(extracted)
                        badge = canonical.get("media_type") or canonical.get("media_name") or "MATCHED"
                tree_item.setData(0, ROLE_MEDIA_TYPE, badge)
                clr = "#FBBF24" if badge else ("#5B7BC4" if kind == "video" else "#4B6A8A")
                tree_item.setForeground(0, QtGui.QColor(clr))
            for i in range(tree_item.childCount()):
                walk(tree_item.child(i))

        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            walk(root.child(i))
        self._tree.viewport().update()

    # ------------------------------------------------------------------
    # Context Menu — leaf items only (folders carry no direct tag)
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos):
        if not self._mapper:
            return

        item = self._tree.itemAt(pos)
        if not item:
            return

        kind     = item.data(0, ROLE_KIND)
        path_str = item.data(0, ROLE_PATH)
        if not path_str or kind not in ("sequence", "video", "image"):
            return

        gp = self._tree.viewport().mapToGlobal(pos)
        self._show_media_context_menu(item, Path(path_str), gp)

    def _refresh_preset_combo(self):
        """Refreshes the Ingest Preset dropdown list with options + Save action."""
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("Presets ▼")
        for name in self._presets.get("presets", {}).keys():
            self._preset_combo.addItem(f"  {name}")
        self._preset_combo.addItem("💾 Save Tagging as Preset…")
        self._preset_combo.blockSignals(False)

    def _on_preset_combo_activated(self, index):
        text = self._preset_combo.itemText(index).strip()
        if text.startswith("💾 Save"):
            self._on_save_ingest_preset()
        elif text.startswith("Presets"):
            return
        else:
            preset_name = text
            self._on_preset_selected(preset_name)

    def _on_preset_selected(self, preset_name):
        """Applies a saved Ingest Preset (an ordered list of Path Patterns) to the tree."""
        presets = self._presets.get("presets", {})
        if not self._mapper or preset_name not in presets:
            return

        data = presets[preset_name]
        self._mapper.set_path_patterns(data.get("patterns", []))
        self._presets["active"] = preset_name
        ingest_presets.save(self._presets)

        self._refresh_item_colours()

    def _on_save_ingest_preset(self):
        """Saves the current tree's active Path Patterns as a new (or existing) Ingest Preset."""
        if not self._mapper:
            QtWidgets.QMessageBox.information(self, "Save Preset", "Please load a folder tree first before saving a preset.")
            return

        text, ok = QtWidgets.QInputDialog.getText(self, "Save Ingest Preset", "Preset Name:")
        if ok and text.strip():
            self._save_patterns_to_preset(text.strip())

    def _save_patterns_to_preset(self, preset_name: str) -> None:
        """(Over)writes `preset_name` with the mapper's current, full Path
        Pattern list -- as dicts, not bare template strings, or a pattern's
        Defaults for Fields Not in the Path would silently vanish the next
        time this preset is applied."""
        preset_data = {
            "name": preset_name,
            "patterns": [p.to_dict() for p in self._mapper.get_path_patterns()],
        }
        self._presets.setdefault("presets", {})[preset_name] = preset_data
        self._presets["active"] = preset_name
        ingest_presets.save(self._presets)
        self._refresh_preset_combo()

    def _show_media_context_menu(self, item, path: Path, gp):
        """Context menu for sequence / video / image leaf items. Tags are
        stored keyed by the item's REAL first-frame file (resolved once,
        here) -- never the tree's synthetic display path, which never
        matches anything FolderMapper.build_items() looks up later (see
        `_real_key_for`)."""
        menu = QtWidgets.QMenu(self)

        hdr = menu.addAction(f"  {path.name}")
        hdr.setEnabled(False)
        menu.addSeparator()

        kind = item.data(0, ROLE_KIND)
        real_path = self._real_key_for(path, kind) if self._mapper else path
        current_type = self._mapper.get_media_type(real_path) if self._mapper else None

        media_types = (self._pctx.config.media_type_names(source="delivery")
                      if self._pctx else [])

        for mtype in media_types:
            act = menu.addAction(f"Tag as {mtype}")
            act.setCheckable(True)
            act.setChecked(current_type == mtype)
            act.triggered.connect(
                lambda checked=False, t=mtype, i=item, p=real_path: self._set_media_type(i, p, t)
            )

        custom_act = menu.addAction("Custom Media Type…")
        custom_act.triggered.connect(
            lambda checked=False, i=item, p=real_path: self._set_media_type_custom(i, p)
        )

        menu.addSeparator()
        build_act = menu.addAction("🏷️ Build Path Pattern…")
        build_act.triggered.connect(
            lambda checked=False, p=path, k=kind: self._open_path_pattern_builder(p, k)
        )

        if current_type:
            menu.addSeparator()
            clr_act = menu.addAction("Clear Media Type Tag")
            clr_act.triggered.connect(
                lambda checked=False, i=item, p=real_path: self._clear_item_tags(i, p)
            )

        if hasattr(menu, "exec"):
            menu.exec(gp)
        else:
            menu.exec_(gp)

    def _clear_item_tags(self, item, path: Path):
        """Clears the manual media-type tag for this specific item. `path`
        must already be the resolved real-file key (see `_real_key_for`)."""
        if self._mapper:
            self._mapper.set_media_type(path, None)
        self._refresh_item_colours()

    def _open_path_pattern_builder(self, path: Path, kind: str):
        """Opens the Path Pattern builder, seeded from this leaf item's real whole path."""
        if not self._mapper:
            return
        item = self._resolve_item_for_node(path, kind)
        if item is None or not item.files:
            QtWidgets.QMessageBox.warning(self, "Build Path Pattern", "Could not read this item's files.")
            return
        dlg = PathPatternBuilderDialog(self._mapper, item, parent=self)
        res = dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
        if res == DIALOG_ACCEPTED and dlg.result_pattern:
            if dlg.result_replace_index is not None:
                self._mapper.update_path_pattern(dlg.result_replace_index, dlg.result_pattern)
            else:
                self._mapper.add_path_pattern(dlg.result_pattern)
            self._refresh_item_colours()
            self._maybe_sync_active_preset()

    def _on_manage_patterns(self):
        """Open the full ordered list of active Path Patterns for this root."""
        if not self._mapper:
            return
        dlg = PathPatternManagerDialog(self._mapper, parent=self)
        dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
        self._refresh_item_colours()
        if dlg.changed:
            self._maybe_sync_active_preset()

    def _maybe_sync_active_preset(self):
        """A pattern the studio tagged came from (or now feeds into) an
        active Ingest Preset -- ask whether this change should be saved back
        into it, rather than the preset silently drifting out of sync with
        what's actually being applied to this root."""
        active = self.active_preset()
        if not active or not self._mapper:
            return
        r = QtWidgets.QMessageBox.question(
            self, "Update Preset",
            f'Update the "{active}" preset with this change?',
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if r == QtWidgets.QMessageBox.StandardButton.Yes:
            self._save_patterns_to_preset(active)

    def _set_media_type(self, item, path: Path, type_name):
        """Assign or clear a manual media type label on a media tree item."""
        if self._mapper:
            self._mapper.set_media_type(path, type_name)
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

    # ------------------------------------------------------------------
    # Button Handlers
    # ------------------------------------------------------------------

    def _on_browse(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Incoming Media Root Folder"
        )
        if path:
            self.load_path(path)

    def _on_clear_tags(self):
        if self._mapper:
            self._mapper.clear_all()
            self._refresh_item_colours()

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
        scan_cache = {}

        def _collect(item):
            kind = item.data(0, ROLE_KIND)
            p = item.data(0, ROLE_PATH)
            if p:
                if kind == "sequence":
                    # ROLE_PATH here is a synthetic "prefix.ext" display path
                    # with no frame digits -- it never equals any real file on
                    # disk, so it can't match build_items()'s filter_paths
                    # (which checks against the sequence's actual frame
                    # files). Resolve to the real files instead, or a directly
                    # selected sequence silently loads nothing.
                    real_item = self._resolve_item_for_node(Path(p), kind, scan_cache=scan_cache)
                    if real_item and real_item.files:
                        for f in real_item.files:
                            paths.add(os.path.normcase(os.path.abspath(f)))
                    else:
                        paths.add(os.path.normcase(os.path.abspath(str(p))))
                else:
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
