"""The config editor main window: a Studio pane and a Project pane, each a form
of one row per `ConfigKey`, plus a project picker and Save / Revert.

All writes go through `core.ConfigStore` (the single writer); this file only
renders and collects.
"""

from __future__ import annotations

from Qt import QtCore, QtWidgets

from square_core.config import ConfigError
from tools.qt_compat import (ALIGN_TOP, FONT_BOLD, FORM_FIELDS_GROW, MSGBOX_YES,
                             SIZE_EXPANDING, SIZE_PREFERRED)
from .core import ConfigStore, NotAuthorized
from .widgets.fields import make_field_editor

_SOURCE_COLOR = {
    "project": "#4ADE80",          # green -- a real project override
    "studio": "#4ADE80",
    "studio-default": "#93C5FD",   # blue -- inherited studio choice
    "builtin": "#94A3B8",          # gray -- shipped default
}


class ScopePane(QtWidgets.QWidget):
    dirtyChanged = QtCore.Signal(bool)

    def __init__(self, scope: str, store: ConfigStore, parent=None):
        super().__init__(parent)
        self.scope = scope
        self.store = store
        self._editors: dict[str, object] = {}
        self._touched: set[str] = set()      # keys actually edited since the last rebuild
        self._dirty = False

        outer = QtWidgets.QVBoxLayout(self)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll)
        self.rebuild()

    # ----

    def rebuild(self):
        self._editors.clear()
        self._touched.clear()
        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(body)
        form.setLabelAlignment(ALIGN_TOP)
        form.setFieldGrowthPolicy(FORM_FIELDS_GROW)

        try:
            fields = self.store.fields(self.scope)
        except RuntimeError:
            fields = []
            form.addRow(QtWidgets.QLabel("Open a project to edit project config."))

        for fv in fields:
            label = QtWidgets.QLabel(fv.key)
            label.setToolTip(fv.description or fv.key)
            f = label.font(); f.setWeight(FONT_BOLD); label.setFont(f)

            tag = QtWidgets.QLabel(("● override" if fv.overridden else fv.source))
            tag.setStyleSheet(f"color:{_SOURCE_COLOR.get(fv.source, '#94A3B8')};font-size:11px;")

            editor = make_field_editor(fv)
            editor.signal_changed.connect(lambda k=fv.key: self._on_field_changed(k))
            self._editors[fv.key] = editor

            cell = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(1)
            v.addWidget(editor)
            sub = QtWidgets.QHBoxLayout()
            sub.addWidget(tag)
            if fv.description:
                d = QtWidgets.QLabel(fv.description)
                d.setStyleSheet("color:#64748B;font-size:11px;")
                sub.addWidget(d)
            sub.addStretch(1)
            if self.scope == "project" and fv.overridden:
                rb = QtWidgets.QPushButton("reset to studio")
                rb.setFlat(True)
                rb.setStyleSheet("color:#93C5FD;font-size:11px;")
                rb.clicked.connect(lambda _=False, k=fv.key: self._reset(k))
                sub.addWidget(rb)
            v.addLayout(sub)
            form.addRow(label, cell)

        self._scroll.setWidget(body)
        self._set_dirty(False)

    def _on_field_changed(self, key: str):
        self._touched.add(key)
        self._set_dirty(True)

    def _set_dirty(self, on: bool):
        self._dirty = on
        self.dirtyChanged.emit(on)

    def _reset(self, key: str):
        self._flush_into_store()      # keep other unsaved edits
        self.store.reset(key)         # then drop this one back to the studio default
        self.rebuild()

    # ----

    def _flush_into_store(self):
        """Push only the fields the user actually edited into the store (in
        memory). A field never touched keeps showing its resolved value
        (`builtin` / `studio-default`) but must NOT be written to disk on
        save -- that would turn "inherits the shipped default" into a
        permanent, unnecessary override the moment anyone hits Save."""
        errs = []
        for key in self._touched:
            editor = self._editors.get(key)
            if editor is None:
                continue
            try:
                self.store.set(self.scope, key, editor.get_value())
            except (ValueError, KeyError) as e:
                errs.append(f"{key}: {e}")
        return errs

    def save(self) -> bool:
        errs = self._flush_into_store()
        if errs:
            QtWidgets.QMessageBox.warning(self, "Invalid values", "\n".join(errs))
            return False
        try:
            if self.scope == "studio":
                path, bak = self.store.save_studio()
            else:
                path, bak = self.store.save_project()
        except (ConfigError, NotAuthorized, RuntimeError) as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))
            return False
        note = f"Wrote {path}"
        if bak:
            note += f"\nBackup: {bak.name}"
        QtWidgets.QMessageBox.information(self, "Saved", note)
        self.rebuild()
        return True

    @property
    def dirty(self) -> bool:
        return self._dirty


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ctx, store: ConfigStore):
        super().__init__()
        self.ctx = ctx
        self.store = store
        self.setWindowTitle("Square — Config Editor")
        self.resize(880, 720)

        self._project_combo = QtWidgets.QComboBox()
        self._project_combo.addItem("— studio only —", "")
        for p in sorted(ctx.kitsu.projects(), key=lambda p: p.code):
            self._project_combo.addItem(f"{p.code}  ({p.name})", p.code)
        self._project_combo.currentIndexChanged.connect(self._project_changed)

        who = getattr(ctx.user, "email", "?")
        role = getattr(ctx.user, "role", "?")
        badge = QtWidgets.QLabel(f"  {who} · {role}")
        if not store.can_write():
            badge.setText(badge.text() + "  (read-only — need admin/manager)")
            badge.setStyleSheet("color:#F59E0B;")

        tb = self.addToolBar("main")
        tb.setMovable(False)
        tb.addWidget(QtWidgets.QLabel("Project: "))
        tb.addWidget(self._project_combo)
        tb.addWidget(badge)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(SIZE_EXPANDING, SIZE_PREFERRED)
        tb.addWidget(spacer)
        self._save_btn = tb.addAction("Save", self._save)
        self._revert_btn = tb.addAction("Revert", self._revert)

        self.tabs = QtWidgets.QTabWidget()
        self.studio_pane = ScopePane("studio", store)
        self.project_pane = ScopePane("project", store)
        self.tabs.addTab(self.studio_pane, "Studio")
        self.tabs.addTab(self.project_pane, "Project")
        self.tabs.currentChanged.connect(self._update_status)
        self.setCentralWidget(self.tabs)

        for pane in (self.studio_pane, self.project_pane):
            pane.dirtyChanged.connect(self._update_title)
        self._update_title()
        self._update_status()

    # ----

    def _current_pane(self) -> ScopePane:
        return self.tabs.currentWidget()

    def _project_changed(self):
        code = self._project_combo.currentData()
        if self._any_dirty() and not self._confirm_discard():
            return
        if code:
            pctx = self.ctx.project(code)
            try:
                self.store.open_project(pctx.project.root_path, code)
            except ConfigError as e:
                QtWidgets.QMessageBox.critical(self, "Project config", str(e))
                self.store.close_project()
        else:
            self.store.close_project()
        self.project_pane.rebuild()
        self._update_title()
        self._update_status()

    def _save(self):
        self._current_pane().save()
        self._update_title()
        self._update_status()

    def _update_status(self, *_):
        """Show exactly which file the active tab reads/writes, and where its
        `.bak-<timestamp>` lands on save -- same directory, same name."""
        from square_core.config import ProjectConfig

        if self.tabs.currentWidget() is self.studio_pane:
            path = self.store.studio_path
        elif self.store.project_root:
            path = ProjectConfig.path_for(self.store.project_root)
        else:
            path = None
        msg = f"{path}   (backup on save: {path.name}.bak-<timestamp>, same folder)" \
            if path else "No project open."
        self.statusBar().showMessage(msg)

    def _revert(self):
        if self._any_dirty() and not self._confirm_discard():
            return
        self.studio_pane.rebuild()
        self.project_pane.rebuild()

    def _any_dirty(self) -> bool:
        return self.studio_pane.dirty or self.project_pane.dirty

    def _confirm_discard(self) -> bool:
        r = QtWidgets.QMessageBox.question(
            self, "Discard changes?", "There are unsaved changes. Discard them?")
        return r == MSGBOX_YES

    def _update_title(self, *_):
        mark = " *" if self._any_dirty() else ""
        self.setWindowTitle(f"Square — Config Editor{mark}")
        self._save_btn.setEnabled(self.store.can_write())

    def closeEvent(self, e):
        if self._any_dirty() and not self._confirm_discard():
            e.ignore()
        else:
            e.accept()
