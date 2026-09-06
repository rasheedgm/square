"""The workfile-manager main window.

Left: a sequence/shot tree.  Middle: the selected shot's task list.  Right: for
the selected task, its saved workfile versions and its published outputs, with
the actions to start the next version, open it in the DCC, and publish a render.

Rendering + collection only; every write goes through `core.WorkfileHub`.
"""

from __future__ import annotations

from pathlib import Path

from Qt import QtCore, QtGui, QtWidgets

from tools.qt_compat import (CURSOR_WAIT, DIALOG_ACCEPTED, FONT_BOLD,
                             HEADER_RESIZE_STRETCH, ITEM_IS_EDITABLE,
                             NO_EDIT_TRIGGERS, ORIENTATION_HORIZONTAL,
                             SELECT_ROWS, SIZE_EXPANDING, SIZE_PREFERRED,
                             USER_ROLE, exec_dialog)

from .core import SeedMode, WorkfileHub

_MUTED = "#94A3B8"
_WARN = "#F59E0B"


def _bold(w):
    f = w.font(); f.setWeight(FONT_BOLD); w.setFont(f)
    return w


def _ro_table(cols):
    t = QtWidgets.QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.horizontalHeader().setSectionResizeMode(HEADER_RESIZE_STRETCH)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(NO_EDIT_TRIGGERS)
    t.setSelectionBehavior(SELECT_ROWS)
    return t


def _buttons(accept_text, on_accept, dlg):
    row = QtWidgets.QHBoxLayout()
    cancel = QtWidgets.QPushButton("Cancel")
    cancel.clicked.connect(dlg.reject)
    ok = QtWidgets.QPushButton(accept_text)
    ok.setDefault(True)
    ok.clicked.connect(on_accept)
    row.addStretch(1)
    row.addWidget(cancel)
    row.addWidget(ok)
    return row


def _cell(text):
    it = QtWidgets.QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~ITEM_IS_EDITABLE)
    return it


# ---------------------------------------------------------------------------
# dialogs
# ---------------------------------------------------------------------------

class NewWorkfileDialog(QtWidgets.QDialog):
    def __init__(self, hub: WorkfileHub, project_code: str, has_current: bool, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.project_code = project_code
        self.setWindowTitle("New Workfile Version")
        self.setMinimumWidth(420)

        self.media_type = QtWidgets.QComboBox()
        self.media_type.addItems(hub.workfile_types(project_code) or ["NukeScript"])
        self.media_type.currentTextChanged.connect(self._sync_software)

        self.software = QtWidgets.QLineEdit()

        self.seed = QtWidgets.QComboBox()
        self.seed.addItem("Empty — the DCC creates the file", SeedMode.EMPTY)
        if has_current:
            self.seed.addItem("Copy up from the current version", SeedMode.CURRENT)
        self.seed.addItem("From a template file…", SeedMode.TEMPLATE)
        self.seed.currentIndexChanged.connect(self._sync_template_row)

        self.template = QtWidgets.QLineEdit()
        self.template.setPlaceholderText("(defaults to the configured template for this type)")
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        tmpl_row = QtWidgets.QHBoxLayout()
        tmpl_row.addWidget(self.template, 1)
        tmpl_row.addWidget(browse)
        self._tmpl_holder = QtWidgets.QWidget()
        self._tmpl_holder.setLayout(tmpl_row)

        self.comment = QtWidgets.QLineEdit()

        form = QtWidgets.QFormLayout(self)
        form.addRow("Media type", self.media_type)
        form.addRow("Software", self.software)
        form.addRow("Seed", self.seed)
        form.addRow("Template", self._tmpl_holder)
        form.addRow("Comment", self.comment)
        form.addRow(_buttons("Create", self.accept, self))

        self._sync_software(self.media_type.currentText())
        self._sync_template_row()

    def _sync_software(self, media_type):
        self.software.setText(self.hub.software_for(self.project_code, media_type))

    def _sync_template_row(self, *_):
        self._tmpl_holder.setVisible(self.seed.currentData() == SeedMode.TEMPLATE)

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Template scene")
        if path:
            self.template.setText(path)

    def values(self) -> dict:
        return dict(media_type=self.media_type.currentText(),
                    software=self.software.text().strip(),
                    seed=self.seed.currentData(),
                    template_path=self.template.text().strip(),
                    comment=self.comment.text().strip())


class PublishOutputDialog(QtWidgets.QDialog):
    def __init__(self, hub: WorkfileHub, project_code: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Publish Output")
        self.setMinimumWidth(460)

        self.media_type = QtWidgets.QComboBox()
        self.media_type.addItems(hub.output_types(project_code) or ["CompRender"])

        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText("a rendered frame, a movie, or a folder of frames")
        pick_file = QtWidgets.QPushButton("File…")
        pick_file.clicked.connect(self._pick_file)
        pick_dir = QtWidgets.QPushButton("Folder…")
        pick_dir.clicked.connect(self._pick_dir)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(pick_file)
        row.addWidget(pick_dir)
        holder = QtWidgets.QWidget(); holder.setLayout(row)

        self.count = QtWidgets.QLabel("")
        self.count.setStyleSheet(f"color:{_MUTED};")
        self.comment = QtWidgets.QLineEdit()

        form = QtWidgets.QFormLayout(self)
        form.addRow("Media type", self.media_type)
        form.addRow("Source", holder)
        form.addRow("", self.count)
        form.addRow("Comment", self.comment)
        form.addRow(_buttons("Publish", self._accept, self))
        self._frames: list[str] = []

    _IMG_EXTS = {".exr", ".dpx", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}
    _MOV_EXTS = {".mov", ".mp4", ".mxf", ".avi"}

    def _pick_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Rendered file")
        if path:
            self.path.setText(path)
            self._scan()

    def _pick_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder of frames")
        if path:
            self.path.setText(path)
            self._scan()

    def _scan(self):
        p = Path(self.path.text().strip())
        self._frames = []
        if p.is_dir():
            self._frames = sorted(
                str(f) for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in self._IMG_EXTS | self._MOV_EXTS)
        elif p.is_file():
            self._frames = [str(p)]
        self.count.setText(f"{len(self._frames)} file(s)" if self._frames
                           else "nothing found at that path")

    def _accept(self):
        self._scan()
        if not self._frames:
            QtWidgets.QMessageBox.warning(self, "No files", "Point at a file or a folder of frames.")
            return
        self.accept()

    def values(self) -> dict:
        return dict(media_type=self.media_type.currentText(), frames=self._frames,
                    comment=self.comment.text().strip())


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, hub: WorkfileHub, parent=None):
        super().__init__(parent)
        self.hub = hub
        self.setWindowTitle("Square — Workfile Manager")
        self.resize(1040, 720)

        self._project = ""
        self._shot = None
        self._task = None

        self.project_combo = QtWidgets.QComboBox()
        self._reload_projects()
        self.project_combo.currentIndexChanged.connect(self._on_project)

        who = getattr(hub.user, "email", "?") or "?"
        badge = QtWidgets.QLabel(f"  {who}")

        tb = self.addToolBar("main")
        tb.setMovable(False)
        tb.addWidget(QtWidgets.QLabel("Project: "))
        tb.addWidget(self.project_combo)
        tb.addAction("↻ Refresh", self._refresh)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(SIZE_EXPANDING, SIZE_PREFERRED)
        tb.addWidget(spacer)
        tb.addWidget(badge)

        # left: shot tree
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Sequence / Shot"])
        self.tree.currentItemChanged.connect(self._on_shot)

        # middle: task list
        self.task_list = QtWidgets.QListWidget()
        self.task_list.currentRowChanged.connect(self._on_task)
        mid = QtWidgets.QVBoxLayout()
        mid.addWidget(_bold(QtWidgets.QLabel("Tasks")))
        mid.addWidget(self.task_list, 1)
        mid_w = QtWidgets.QWidget(); mid_w.setLayout(mid)

        # right: the task panel
        self.right = self._build_right_panel()

        split = QtWidgets.QSplitter(ORIENTATION_HORIZONTAL)
        split.addWidget(self.tree)
        split.addWidget(mid_w)
        split.addWidget(self.right)
        split.setSizes([260, 220, 560])
        self.setCentralWidget(split)

        self._set_task(None)
        self._on_project()

    # ---- right panel ----

    def _build_right_panel(self) -> QtWidgets.QWidget:
        self.task_header = _bold(QtWidgets.QLabel("Pick a task"))

        self.wf_table = _ro_table(["v", "software", "comment", "file"])
        self.new_btn = QtWidgets.QPushButton("New Version…")
        self.new_btn.clicked.connect(self._new_workfile)
        self.open_btn = QtWidgets.QPushButton("Open")
        self.open_btn.clicked.connect(self._open_workfile)
        self.reveal_btn = QtWidgets.QPushButton("Reveal")
        self.reveal_btn.clicked.connect(self._reveal_workfile)
        wf_row = QtWidgets.QHBoxLayout()
        for b in (self.new_btn, self.open_btn, self.reveal_btn):
            wf_row.addWidget(b)
        wf_row.addStretch(1)

        self.out_type = QtWidgets.QComboBox()
        self.out_type.currentTextChanged.connect(lambda *_: self._refresh_outputs())
        self.out_table = _ro_table(["v", "representation", "path"])
        self.publish_btn = QtWidgets.QPushButton("Publish Output…")
        self.publish_btn.clicked.connect(self._publish_output)
        self.out_reveal_btn = QtWidgets.QPushButton("Reveal")
        self.out_reveal_btn.clicked.connect(self._reveal_output)
        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(QtWidgets.QLabel("Type:"))
        out_row.addWidget(self.out_type, 1)
        out_row.addWidget(self.publish_btn)
        out_row.addWidget(self.out_reveal_btn)

        v = QtWidgets.QVBoxLayout()
        v.addWidget(self.task_header)
        v.addWidget(_bold(QtWidgets.QLabel("Workfiles")))
        v.addWidget(self.wf_table, 1)
        v.addLayout(wf_row)
        v.addSpacing(8)
        v.addWidget(_bold(QtWidgets.QLabel("Outputs")))
        v.addLayout(out_row)
        v.addWidget(self.out_table, 1)
        w = QtWidgets.QWidget(); w.setLayout(v)
        return w

    # ---- project / navigation ----

    def _reload_projects(self):
        self.project_combo.blockSignals(True)
        keep = self.project_combo.currentData()
        self.project_combo.clear()
        self.project_combo.addItem("— pick a project —", "")
        try:
            for p in self.hub.projects():
                self.project_combo.addItem(f"{p.code}  ({p.name})", p.code)
        except Exception as e:
            self.project_combo.addItem(f"(load failed: {e})", "")
        if keep:
            i = self.project_combo.findData(keep)
            if i >= 0:
                self.project_combo.setCurrentIndex(i)
        self.project_combo.blockSignals(False)

    def _on_project(self, *_):
        self._project = self.project_combo.currentData() or ""
        self._reload_shots()

    def _reload_shots(self):
        self.tree.clear()
        self._set_task(None)
        if not self._project:
            return
        with _wait():
            try:
                shots = self.hub.shots(self._project)
            except Exception as e:
                self.tree.addTopLevelItem(QtWidgets.QTreeWidgetItem([f"(load failed: {e})"]))
                return
        by_seq: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for s in shots:
            seq = s.sequence_code or "(no sequence)"
            if seq not in by_seq:
                by_seq[seq] = QtWidgets.QTreeWidgetItem([seq])
                self.tree.addTopLevelItem(by_seq[seq])
            child = QtWidgets.QTreeWidgetItem([s.code])
            child.setData(0, USER_ROLE, s)
            by_seq[seq].addChild(child)
        self.tree.expandAll()

    def _on_shot(self, cur, _prev):
        shot = cur.data(0, USER_ROLE) if cur else None
        self._shot = shot
        self.task_list.clear()
        self._set_task(None)
        if shot is None:
            return
        with _wait():
            self._task_rows = self.hub.tasks_for_shot(self._project, shot)
        for r in self._task_rows:
            self.task_list.addItem(f"{r.task.task_type_name}   [{r.task.status or '—'}]")
        if not self._task_rows:
            self.task_list.addItem("(no tasks — build the grid in the project-setup tool)")

    def _on_task(self, row):
        rows = getattr(self, "_task_rows", [])
        self._set_task(rows[row].task if 0 <= row < len(rows) else None)

    def _set_task(self, task):
        self._task = task
        on = task is not None
        for b in (self.new_btn, self.open_btn, self.reveal_btn,
                  self.publish_btn, self.out_reveal_btn):
            b.setEnabled(on)
        self.out_type.setEnabled(on)
        if not on:
            self.task_header.setText("Pick a task")
            self.wf_table.setRowCount(0)
            self.out_table.setRowCount(0)
            return
        self.task_header.setText(
            f"{self._shot.sequence_code or ''}/{self._shot.code}  ·  {task.task_type_name}")
        self._reload_out_types()
        self._refresh_workfiles()
        self._refresh_outputs()

    # ---- workfiles ----

    def _refresh_workfiles(self):
        self.wf_table.setRowCount(0)
        if not self._task:
            return
        with _wait():
            wfs = self.hub.workfiles(self._project, self._task)
        for w in wfs:
            r = self.wf_table.rowCount()
            self.wf_table.insertRow(r)
            self.wf_table.setItem(r, 0, _cell(f"v{w.revision:03d}"))
            self.wf_table.setItem(r, 1, _cell(w.software or ""))
            self.wf_table.setItem(r, 2, _cell(w.comment or ""))
            item = _cell(Path(w.path).name or w.path)
            item.setToolTip(w.path)
            item.setData(USER_ROLE, w.path)
            self.wf_table.setItem(r, 3, item)
        if wfs:
            self.wf_table.selectRow(self.wf_table.rowCount() - 1)

    def _selected_workfile_path(self) -> str:
        r = self.wf_table.currentRow()
        it = self.wf_table.item(r, 3) if r >= 0 else None
        return it.data(USER_ROLE) if it else ""

    def _new_workfile(self):
        if not self._task:
            return
        has_current = bool(self.hub.workfiles(self._project, self._task))
        dlg = NewWorkfileDialog(self.hub, self._project, has_current, self)
        if exec_dialog(dlg) != DIALOG_ACCEPTED:
            return
        vals = dlg.values()
        try:
            with _wait():
                slot = self.hub.new_workfile(self._project, self._shot, self._task, **vals)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "New workfile failed", f"{type(e).__name__}: {e}")
            return
        self._refresh_workfiles()
        msg = f"Reserved v{slot.revision:03d}\n{slot.path}"
        if not slot.seeded:
            msg += "\n\nThe file isn't on disk yet — open it in the DCC and save."
        QtWidgets.QMessageBox.information(self, "New workfile", msg)

    def _open_workfile(self):
        path = self._selected_workfile_path()
        if not path:
            return
        r = self.wf_table.currentRow()
        software = self.wf_table.item(r, 1).text() if r >= 0 else ""
        try:
            note = self.hub.open_workfile(self._project, software, path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Open failed", f"{type(e).__name__}: {e}")
            return
        self.statusBar().showMessage(note, 6000)

    def _reveal_workfile(self):
        path = self._selected_workfile_path()
        if path:
            self.hub.reveal(str(Path(path).parent))

    # ---- outputs ----

    def _reload_out_types(self):
        self.out_type.blockSignals(True)
        keep = self.out_type.currentText()
        self.out_type.clear()
        self.out_type.addItems(self.hub.output_types(self._project) or ["CompRender"])
        i = self.out_type.findText(keep)
        if i >= 0:
            self.out_type.setCurrentIndex(i)
        self.out_type.blockSignals(False)

    def _refresh_outputs(self):
        self.out_table.setRowCount(0)
        if not self._task or not self.out_type.currentText():
            return
        with _wait():
            outs = self.hub.outputs(self._project, self._shot, self.out_type.currentText())
        for o in outs:
            r = self.out_table.rowCount()
            self.out_table.insertRow(r)
            self.out_table.setItem(r, 0, _cell(f"v{getattr(o, 'revision', 0):03d}"))
            self.out_table.setItem(r, 1, _cell(getattr(o, "representation", "") or ""))
            item = _cell(getattr(o, "path", "") or "")
            item.setData(USER_ROLE, getattr(o, "path", "") or "")
            self.out_table.setItem(r, 2, item)
        if outs:
            self.out_table.selectRow(self.out_table.rowCount() - 1)

    def _publish_output(self):
        if not self._task:
            return
        dlg = PublishOutputDialog(self.hub, self._project, self)
        i = dlg.media_type.findText(self.out_type.currentText())
        if i >= 0:
            dlg.media_type.setCurrentIndex(i)
        if exec_dialog(dlg) != DIALOG_ACCEPTED:
            return
        vals = dlg.values()
        try:
            with _wait():
                res = self.hub.publish_output(self._project, self._shot, self._task, **vals)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Publish failed", f"{type(e).__name__}: {e}")
            return
        self._reload_out_types()
        i = self.out_type.findText(vals["media_type"])
        if i >= 0:
            self.out_type.setCurrentIndex(i)
        self._refresh_outputs()
        note = f"Published {vals['media_type']} v{res.version:03d}"
        if getattr(res, "preview", None) is not None:
            note += " + review proxy"
        QtWidgets.QMessageBox.information(self, "Publish", f"{note}\n{res.dir}")

    def _reveal_output(self):
        r = self.out_table.currentRow()
        it = self.out_table.item(r, 2) if r >= 0 else None
        path = it.data(USER_ROLE) if it else ""
        if path:
            p = Path(path)
            self.hub.reveal(str(p if p.is_dir() else p.parent))

    # ---- misc ----

    def _refresh(self):
        self.hub.clear_cache()
        self._reload_projects()
        self._on_project()


class _wait:
    """`with _wait():` — busy cursor around a blocking Kitsu call."""

    def __enter__(self):
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(CURSOR_WAIT))

    def __exit__(self, *exc):
        QtWidgets.QApplication.restoreOverrideCursor()
        return False
