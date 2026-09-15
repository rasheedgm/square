"""The config editor main window: one pane whose scope follows the project
picker -- "-- studio only --" edits the studio config, picking a project
edits that project's config. No separate Studio/Project tabs.

All writes go through `core.ConfigStore` (the single writer); this file only
renders and collects.
"""

from __future__ import annotations

import json

from Qt import QtCore, QtWidgets

from square_core.config import ConfigError
from tools.qt_compat import (ALIGN_TOP, FONT_BOLD, FORM_FIELDS_GROW, MSGBOX_ACTION_ROLE,
                             MSGBOX_REJECT_ROLE, MSGBOX_YES, MSGBOX_NO, MSG_WARNING,
                             SIZE_EXPANDING, SIZE_PREFERRED, exec_dialog)
from .core import ConfigStore, NotAuthorized
from .widgets.fields import make_field_editor

_SOURCE_COLOR = {
    "project": "#4ADE80",          # green -- a real project override
    "studio": "#4ADE80",
    "studio-default": "#93C5FD",   # blue -- inherited studio choice
    "builtin": "#94A3B8",          # gray -- shipped default
}


def _fmt(v) -> str:
    s = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= 100 else s[:97] + "..."


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

    def set_scope(self, scope: str) -> None:
        """Switch what this pane edits (studio vs. a project) and redraw.
        Caller is responsible for having already resolved any unsaved
        changes -- this does not check dirty state itself."""
        self.scope = scope
        self.rebuild()

    def rebuild(self):
        self._editors.clear()
        self._touched.clear()
        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(body)
        form.setLabelAlignment(ALIGN_TOP)
        form.setFieldGrowthPolicy(FORM_FIELDS_GROW)

        frozen = (self.scope == "project" and self.store.has_project
                 and self.store.is_frozen)
        if frozen:
            banner = QtWidgets.QLabel(
                "\U0001F512  This project's config is frozen -- every field below was "
                "written explicitly when it was frozen, and no further edits are "
                "allowed here.")
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "color:#F59E0B;font-weight:bold;padding:6px;"
                "border:1px solid #F59E0B;border-radius:4px;")
            form.addRow(banner)

        try:
            fields = self.store.fields(self.scope)
        except RuntimeError:
            fields = []
            form.addRow(QtWidgets.QLabel("Open a project to edit project config."))

        # the actual project's padding, not the schema's bare defaults, so the
        # by-example template builder's preview matches what will really render
        try:
            version_pad = int(self.store.field(self.scope, "version_pad").value)
            frame_pad = int(self.store.field(self.scope, "frame_pad").value)
        except (KeyError, RuntimeError, TypeError, ValueError):
            version_pad, frame_pad = 3, 4

        for fv in fields:
            label = QtWidgets.QLabel(fv.key)
            label.setToolTip(fv.description or fv.key)
            f = label.font(); f.setWeight(FONT_BOLD); label.setFont(f)

            tag = QtWidgets.QLabel(("● override" if fv.overridden else fv.source))
            tag.setStyleSheet(f"color:{_SOURCE_COLOR.get(fv.source, '#94A3B8')};font-size:11px;")

            editor = make_field_editor(fv, version_pad=version_pad, frame_pad=frame_pad)
            if frozen:
                editor.setEnabled(False)
            else:
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
            if not frozen and self.scope == "project" and fv.overridden:
                rb = QtWidgets.QPushButton("reset to studio")
                rb.setFlat(True)
                rb.setStyleSheet("color:#93C5FD;font-size:11px;")
                rb.clicked.connect(lambda _=False, k=fv.key: self._reset(k))
                sub.addWidget(rb)
            elif not frozen and self.scope == "project" and not fv.overridden:
                # the value shown here is only *inherited* (studio-default /
                # builtin) -- editing a sub-editor's table/registry without
                # actually changing a cell never marks the field touched (by
                # design: Save must not silently bake in a value nobody typed),
                # so there was no way to explicitly adopt it into this
                # project's own file. This does that in one click: same
                # mechanism as typing the value in yourself and saving, just
                # without needing to actually change anything first.
                pb = QtWidgets.QPushButton("set override")
                pb.setFlat(True)
                pb.setStyleSheet("color:#93C5FD;font-size:11px;")
                pb.setToolTip(
                    "write this inherited value into the project's own config "
                    "so it stops tracking future studio-default changes")
                pb.clicked.connect(lambda _=False, k=fv.key: self._set_override(k))
                sub.addWidget(pb)
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

    def _set_override(self, key: str):
        """Adopt the currently-inherited (studio-default / builtin) value
        into this project's own config, unchanged. Flushed immediately (not
        just marked touched for the next Save) so the field visibly becomes
        a "project" override right away -- override is presence-based, so
        writing the value in, even unchanged, is what makes it one."""
        self._on_field_changed(key)
        self._flush_into_store()
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
        pending = self.store.pending(self.scope)
        if pending and not self._confirm_pending(pending):
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

    def _confirm_pending(self, pending: dict) -> bool:
        """Show exactly which keys are about to be written and what they're
        changing from/to, before Save actually touches disk."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(MSG_WARNING)
        box.setWindowTitle("Confirm changes")
        box.setText(f"{len(pending)} key(s) will be written:")
        lines = []
        for key in sorted(pending):
            old, new = pending[key]
            lines.append(f"{key}\n  was: {_fmt(old)}\n  now: {_fmt(new)}")
        box.setDetailedText("\n\n".join(lines))
        ok = box.addButton("Save", MSGBOX_ACTION_ROLE)
        box.addButton("Cancel", MSGBOX_REJECT_ROLE)
        box.setDefaultButton(ok)
        exec_dialog(box)
        return box.clickedButton() is ok

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
        self._current_code = ""       # "" == studio only; tracks the LAST successful selection

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
        self._freeze_btn = tb.addAction("Freeze Project", self._freeze_project)
        self._save_btn = tb.addAction("Save", self._save)
        self._revert_btn = tb.addAction("Revert", self._revert)

        self.pane = ScopePane("studio", store)
        self.setCentralWidget(self.pane)
        self.pane.dirtyChanged.connect(self._update_title)

        self._update_title()
        self._update_status()

    # ---- project switching -----------------------------------------

    def _project_changed(self):
        code = self._project_combo.currentData() or ""
        if code == self._current_code:
            return
        if self.pane.dirty:
            choice = self._confirm_switch()
            if choice == "cancel":
                self._select_code(self._current_code, block=True)
                return
            if choice == "save" and not self.pane.save():
                self._select_code(self._current_code, block=True)
                return
            # "discard" (or a successful "save") falls through to the switch
        self._load_scope(code)

    def _load_scope(self, code: str) -> None:
        if code:
            pctx = self.ctx.project(code)
            try:
                self.store.open_project(pctx.project.root_path, code)
            except ConfigError as e:
                QtWidgets.QMessageBox.critical(self, "Project config", str(e))
                self.store.close_project()
                self._select_code(self._current_code, block=True)
                return
            self.pane.set_scope("project")
        else:
            self.store.close_project()
            self.pane.set_scope("studio")
        self._current_code = code
        self._update_title()
        self._update_status()

    def _select_code(self, code: str, *, block: bool = False) -> None:
        i = self._project_combo.findData(code)
        if i < 0:
            return
        if block:
            self._project_combo.blockSignals(True)
        self._project_combo.setCurrentIndex(i)
        if block:
            self._project_combo.blockSignals(False)

    def _confirm_switch(self) -> str:
        """'save' / 'discard' / 'cancel' -- what to do about this pane's
        unsaved changes before switching to a different scope."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(MSG_WARNING)
        box.setWindowTitle("Unsaved changes")
        box.setText("This scope has unsaved changes. Save them before switching?")
        save_btn = box.addButton("Save", MSGBOX_ACTION_ROLE)
        box.addButton("Discard", MSGBOX_ACTION_ROLE)
        cancel_btn = box.addButton("Cancel", MSGBOX_REJECT_ROLE)
        box.setDefaultButton(save_btn)
        exec_dialog(box)
        clicked = box.clickedButton()
        if clicked is save_btn:
            return "save"
        if clicked is cancel_btn:
            return "cancel"
        return "discard"

    # ---- actions ----------------------------------------------------

    def _save(self):
        self.pane.save()
        self._update_title()
        self._update_status()

    def _freeze_project(self):
        if not self.store.has_project:
            QtWidgets.QMessageBox.information(self, "No project open",
                                             "Select a project first.")
            return
        if self.store.is_frozen:
            QtWidgets.QMessageBox.information(self, "Already frozen",
                                             f"{self.store.project_code} is already frozen.")
            return
        resp = QtWidgets.QMessageBox.question(
            self, "Freeze this project",
            f"Write EVERY field's current resolved value into "
            f"{self.store.project_code}'s own config, and lock it against any "
            "further edits?\n\n"
            "Typically used to protect a project that's actively in delivery "
            "from a studio-wide edit landing mid-flight. There is no undo in "
            "this tool -- an admin would need to hand-edit the file to "
            "unfreeze it.\n\n"
            "You'll still need to click Save to write it to disk.",
            MSGBOX_YES | MSGBOX_NO)
        if resp != MSGBOX_YES:
            return
        try:
            self.store.freeze_project()
        except (NotAuthorized, ValueError, RuntimeError) as e:
            QtWidgets.QMessageBox.critical(self, "Freeze failed", str(e))
            return
        self.pane.rebuild()
        self._update_title()

    def _revert(self):
        if self.pane.dirty:
            r = QtWidgets.QMessageBox.question(
                self, "Discard changes?", "There are unsaved changes. Discard them?")
            if r != MSGBOX_YES:
                return
        self.pane.rebuild()
        self._update_title()

    # ---- chrome -------------------------------------------------

    def _update_status(self, *_):
        """Show exactly which file this pane reads/writes, and where its
        `.bak-<timestamp>` lands on save -- same directory, same name."""
        from square_core.config import ProjectConfig

        if self.pane.scope == "studio":
            path = self.store.studio_path
        elif self.store.project_root:
            path = ProjectConfig.path_for(self.store.project_root)
        else:
            path = None
        msg = f"{path}   (backup on save: {path.name}.bak-<timestamp>, same folder)" \
            if path else "No project open."
        if self.store.has_project and self.store.is_frozen:
            msg += "   —   🔒 FROZEN"
        self.statusBar().showMessage(msg)

    def _update_title(self, *_):
        mark = " *" if self.pane.dirty else ""
        self.setWindowTitle(f"Square — Config Editor{mark}")
        frozen = self.store.has_project and self.store.is_frozen
        self._save_btn.setEnabled(self.store.can_write() and not frozen)
        self._freeze_btn.setEnabled(
            self.store.can_write() and self.pane.scope == "project"
            and self.store.has_project and not frozen)
        self._update_status()

    def closeEvent(self, e):
        if self.pane.dirty:
            r = QtWidgets.QMessageBox.question(
                self, "Discard changes?", "There are unsaved changes. Discard them?")
            if r != MSGBOX_YES:
                e.ignore()
                return
        e.accept()
