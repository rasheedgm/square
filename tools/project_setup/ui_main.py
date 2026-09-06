"""The project-setup main window.

Three tabs, each a thin form over one core service:

* **New Project**  -> `ProjectAdmin.create_project` (`services.projects.create`)
* **Breakdown**    -> `ProjectAdmin.add_breakdown` (`services.breakdown.ensure_shot`)
* **Roadmap**      -> `ProjectAdmin.build_task_grid` (`services.breakdown.build_task_grid`)

This file only renders and collects; every write goes through `core.ProjectAdmin`,
which gates on the Kitsu role and calls `square_core.services`.
"""

from __future__ import annotations

from Qt import QtCore, QtGui, QtWidgets

from tools.qt_compat import (ALIGN_TOP, CHECK_CHECKED, CHECK_UNCHECKED, CURSOR_WAIT,
                             FONT_BOLD, FORM_FIELDS_GROW, HEADER_RESIZE_STRETCH,
                             ITEM_IS_EDITABLE, ITEM_IS_USER_CHECKABLE,
                             ORIENTATION_HORIZONTAL, SIZE_EXPANDING, SIZE_PREFERRED,
                             WINDOW_MODAL)

from .core import (PRODUCTION_TYPES, BreakdownRow, NotAuthorized, ProjectAdmin,
                   ProjectSpec, parse_breakdown)

_OK = "#4ADE80"
_WARN = "#F59E0B"
_ERR = "#F87171"
_MUTED = "#94A3B8"

_NONE_TEMPLATE = "— none —"
_ERR_BRUSH = QtGui.QBrush(QtGui.QColor(_ERR))
_OK_BRUSH = QtGui.QBrush(QtGui.QColor(_MUTED))


def _bold(label: QtWidgets.QLabel) -> QtWidgets.QLabel:
    f = label.font()
    f.setWeight(FONT_BOLD)
    label.setFont(f)
    return label


# ---------------------------------------------------------------------------
# New Project
# ---------------------------------------------------------------------------

class NewProjectPane(QtWidgets.QWidget):
    """Fill a `ProjectSpec`, call `projects.create`, show what landed."""

    projectCreated = QtCore.Signal(str)          # project code

    def __init__(self, admin: ProjectAdmin, parent=None):
        super().__init__(parent)
        self.admin = admin

        self.code = QtWidgets.QLineEdit()
        self.code.setPlaceholderText("ABC")
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("(defaults to the code)")

        self.production_type = QtWidgets.QComboBox()
        self.production_type.addItems(PRODUCTION_TYPES)

        self.template = QtWidgets.QComboBox()
        self.template.addItem(_NONE_TEMPLATE, "")
        for t in self.admin.kitsu_templates():
            self.template.addItem(t, t)

        self.nas_root = QtWidgets.QComboBox()
        self.nas_root.addItems(self.admin.nas_root_names())

        self.fps = QtWidgets.QDoubleSpinBox()
        self.fps.setRange(0.0, 240.0)
        self.fps.setDecimals(3)
        self.fps.setSpecialValueText("inherit studio default")
        self.resolution = QtWidgets.QLineEdit()
        self.resolution.setPlaceholderText("e.g. 3840x2160  (blank = studio default)")

        self.path_hint = QtWidgets.QLabel()
        self.path_hint.setStyleSheet(f"color:{_MUTED};")
        self.create_btn = QtWidgets.QPushButton("Create Project")
        self.create_btn.clicked.connect(self._create)

        self.result = QtWidgets.QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("The new project's Kitsu record, NAS root, "
                                       "folders and config path show up here.")

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(ALIGN_TOP)
        form.setFieldGrowthPolicy(FORM_FIELDS_GROW)
        form.addRow(_bold(QtWidgets.QLabel("Code")), self.code)
        form.addRow("Name", self.name)
        form.addRow("Production type", self.production_type)
        form.addRow("Kitsu template", self.template)
        form.addRow("NAS root", self.nas_root)
        form.addRow("FPS", self.fps)
        form.addRow("Resolution", self.resolution)
        form.addRow("", self.path_hint)
        form.addRow("", self.create_btn)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(_bold(QtWidgets.QLabel("Result")))
        outer.addWidget(self.result, 1)

        self.code.textChanged.connect(self._refresh_hint)
        self.nas_root.currentIndexChanged.connect(self._refresh_hint)
        self._refresh_hint()
        if not self.admin.can_write():
            self.create_btn.setEnabled(False)

    # ----

    def _refresh_hint(self, *_):
        root = self.admin.project_root_preview(self.code.text(), self.nas_root.currentText())
        self.path_hint.setText(f"Will create at:  {root}" if root else "")

    def _spec(self) -> ProjectSpec:
        return ProjectSpec(
            code=self.code.text().strip(),
            name=self.name.text().strip(),
            production_type=self.production_type.currentText(),
            kitsu_template=self.template.currentData() or "",
            nas_root=self.nas_root.currentText() or "default",
            fps=self.fps.value() or None,
            resolution=self.resolution.text().strip(),
        )

    def _create(self):
        spec = self._spec()
        if not spec.code:
            QtWidgets.QMessageBox.warning(self, "Missing code", "A project needs a code.")
            return
        existing = {p.code.lower() for p in self.admin.projects()}
        if spec.code.lower() in existing:
            QtWidgets.QMessageBox.warning(
                self, "Already exists", f"A project {spec.code!r} is already in Kitsu.")
            return

        self.create_btn.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtGui.QCursor(CURSOR_WAIT))
        try:
            created = self.admin.create_project(spec)
        except NotAuthorized as e:
            QtWidgets.QMessageBox.critical(self, "Not allowed", str(e))
            return
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Create failed", f"{type(e).__name__}: {e}")
            self.result.setPlainText(f"FAILED: {e}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.create_btn.setEnabled(self.admin.can_write())

        proj = created.project
        lines = [
            f"Project:   {proj.code}  ({proj.name})",
            f"Kitsu id:  {proj.id}",
            f"NAS root:  {getattr(proj, 'root_path', '') or '(none)'}",
            f"Config:    {created.config_path}",
            f"Folders:   {len(created.folders_created)} created",
        ]
        if created.kitsu_template:
            lines.append(f"Template:  {created.kitsu_template}")
        lines += ["", *(f"  {p}" for p in created.folders_created)]
        self.result.setPlainText("\n".join(lines))
        self.projectCreated.emit(proj.code)


# ---------------------------------------------------------------------------
# Breakdown
# ---------------------------------------------------------------------------

_BD_COLS = ("Sequence", "Shot", "In", "Out", "FPS", "Status")


class BreakdownPane(QtWidgets.QWidget):
    """Paste / edit shot rows, then `ensure_shot` each one."""

    breakdownChanged = QtCore.Signal()

    def __init__(self, admin: ProjectAdmin, parent=None):
        super().__init__(parent)
        self.admin = admin
        self._project_code = ""
        self._can_write = self.admin.can_write()

        self.existing = QtWidgets.QTreeWidget()
        self.existing.setHeaderLabels(["Sequence / Shot", "Frames"])

        self.paste = QtWidgets.QPlainTextEdit()
        self.paste.setPlaceholderText(
            "One shot per line:\n"
            "  SQ010 SH0100 1001-1100\n"
            "  SQ010 SH0110 1001 1200 23.976\n"
            "  SQ020, SH0200\n"
            "'/' and ',' also separate. Blank lines and #comments are skipped.")
        self.paste.setMaximumHeight(150)
        parse_btn = QtWidgets.QPushButton("Parse ↓")
        parse_btn.clicked.connect(self._parse)
        add_row_btn = QtWidgets.QPushButton("+ Row")
        add_row_btn.clicked.connect(lambda: self._append_row(BreakdownRow("", "")))
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(lambda: (self.table.setRowCount(0), self._revalidate()))

        self.table = QtWidgets.QTableWidget(0, len(_BD_COLS))
        self.table.setHorizontalHeaderLabels(_BD_COLS)
        self.table.horizontalHeader().setSectionResizeMode(HEADER_RESIZE_STRETCH)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._revalidate_later)

        self.apply_btn = QtWidgets.QPushButton("Create / update shots")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setEnabled(False)

        tools_row = QtWidgets.QHBoxLayout()
        tools_row.addWidget(parse_btn)
        tools_row.addWidget(add_row_btn)
        tools_row.addWidget(clear_btn)
        tools_row.addStretch(1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(_bold(QtWidgets.QLabel("Add shots")))
        right.addWidget(self.paste)
        right.addLayout(tools_row)
        right.addWidget(self.table, 1)
        right.addWidget(self.apply_btn)
        right_w = QtWidgets.QWidget()
        right_w.setLayout(right)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(_bold(QtWidgets.QLabel("Existing breakdown")))
        left.addWidget(self.existing, 1)
        left_w = QtWidgets.QWidget()
        left_w.setLayout(left)

        split = QtWidgets.QSplitter(ORIENTATION_HORIZONTAL)
        split.addWidget(left_w)
        split.addWidget(right_w)
        split.setSizes([320, 520])

        outer = QtWidgets.QVBoxLayout(self)
        outer.addWidget(split)

    # ----

    def set_project(self, code: str):
        self._project_code = code or ""
        self.refresh_existing()
        self._revalidate()

    def refresh_existing(self):
        self.existing.clear()
        if not self._project_code:
            return
        try:
            shots = self.admin.existing_shots(self._project_code)
        except Exception as e:
            self.existing.addTopLevelItem(QtWidgets.QTreeWidgetItem([f"(load failed: {e})", ""]))
            return
        by_seq: dict[str, list] = {}
        for s in shots:
            seq = getattr(s, "sequence_code", "") or "(no sequence)"
            by_seq.setdefault(seq, []).append(s)
        for seq in sorted(by_seq):
            top = QtWidgets.QTreeWidgetItem([seq, ""])
            for s in sorted(by_seq[seq], key=lambda s: s.code):
                fr = ""
                if getattr(s, "frame_in", None) is not None and getattr(s, "frame_out", None):
                    fr = f"{s.frame_in}-{s.frame_out}"
                top.addChild(QtWidgets.QTreeWidgetItem([s.code, fr]))
            self.existing.addTopLevelItem(top)
        self.existing.expandAll()

    # ---- the editable table ----

    def _parse(self):
        rows = parse_breakdown(self.paste.toPlainText())
        if not rows:
            return
        self.table.setRowCount(0)
        for r in rows:
            self._append_row(r)
        self.paste.clear()
        self._revalidate()

    def _append_row(self, r: BreakdownRow):
        self.table.blockSignals(True)
        row = self.table.rowCount()
        self.table.insertRow(row)
        vals = (r.sequence, r.shot, str(r.frame_in), str(r.frame_out),
                "" if r.fps is None else str(r.fps))
        for col, val in enumerate(vals):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(val))
        status = QtWidgets.QTableWidgetItem("")
        status.setFlags(status.flags() & ~ITEM_IS_EDITABLE)
        self.table.setItem(row, 5, status)
        self.table.blockSignals(False)
        self._revalidate()

    def _rows_from_table(self) -> list[BreakdownRow]:
        out: list[BreakdownRow] = []
        for row in range(self.table.rowCount()):
            def cell(c):
                it = self.table.item(row, c)
                return it.text().strip() if it else ""
            seq, shot = cell(0), cell(1)
            if not seq and not shot:
                continue
            r = BreakdownRow(sequence=seq, shot=shot)
            try:
                r.frame_in = int(cell(2) or r.frame_in)
                r.frame_out = int(cell(3) or r.frame_out)
            except ValueError:
                r.error = "frame in/out must be whole numbers"
            fps = cell(4)
            if fps and not r.error:
                try:
                    r.fps = float(fps)
                except ValueError:
                    r.error = "fps must be a number"
            if not r.error:
                if not seq or not shot:
                    r.error = "sequence and shot are both required"
                elif r.frame_out < r.frame_in:
                    r.error = "frame range ends before it starts"
            out.append(r)
        return out

    def _revalidate_later(self, *_):
        QtCore.QTimer.singleShot(0, self._revalidate)

    def _revalidate(self):
        rows = self._rows_from_table()
        good = 0
        self.table.blockSignals(True)
        for i, r in enumerate(rows):
            item = self.table.item(i, 5)
            if item is None:
                continue
            if r.error:
                item.setText(r.error)
                item.setForeground(_ERR_BRUSH)
            else:
                item.setText("ready")
                item.setForeground(_OK_BRUSH)
                good += 1
        self.table.blockSignals(False)
        self.apply_btn.setText(
            f"Create / update {good} shot(s)" if good else "Create / update shots")
        self.apply_btn.setEnabled(bool(good) and bool(self._project_code) and self._can_write)

    # ---- apply ----

    def _apply(self):
        rows = [r for r in self._rows_from_table() if r.ok]
        if not rows or not self._project_code:
            return
        dlg = QtWidgets.QProgressDialog("Creating shots…", "Cancel", 0, len(rows), self)
        dlg.setWindowModality(WINDOW_MODAL)

        def progress(done, total, row):
            dlg.setValue(done)
            dlg.setLabelText(f"{row.sequence} / {row.shot}  ({done}/{total})")
            QtWidgets.QApplication.processEvents()

        try:
            outcomes = self.admin.add_breakdown(self._project_code, rows, progress=progress)
        except NotAuthorized as e:
            dlg.close()
            QtWidgets.QMessageBox.critical(self, "Not allowed", str(e))
            return
        except Exception as e:
            dlg.close()
            QtWidgets.QMessageBox.critical(self, "Breakdown failed", f"{type(e).__name__}: {e}")
            return
        dlg.setValue(len(rows))

        failed = [o for o in outcomes if o.error]
        made = sum(1 for o in outcomes if o.created and not o.error)
        self.table.blockSignals(True)
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 5)
            if it and it.text() == "ready":
                it.setText("done")
        self.table.blockSignals(False)
        msg = f"{made} shot(s) created / updated."
        if failed:
            msg += "\n\nFailed:\n" + "\n".join(
                f"  {o.row.sequence}/{o.row.shot}: {o.error}" for o in failed)
        QtWidgets.QMessageBox.information(self, "Breakdown", msg)
        self.refresh_existing()
        self.breakdownChanged.emit()


# ---------------------------------------------------------------------------
# Roadmap
# ---------------------------------------------------------------------------

_DEFAULT_TASKS = ["Ingest", "Prep", "Roto", "Matchmove", "Comp"]


class RoadmapPane(QtWidgets.QWidget):
    """Pick task types, generate a task per (shot, task_type)."""

    def __init__(self, admin: ProjectAdmin, parent=None):
        super().__init__(parent)
        self.admin = admin
        self._project_code = ""
        self._n_shots = 0
        self._can_write = self.admin.can_write()

        self.list = QtWidgets.QListWidget()
        self.list.itemChanged.connect(self._update_summary)
        self.custom = QtWidgets.QLineEdit()
        self.custom.setPlaceholderText("add more, comma-separated: Paint, Tracking")
        self.custom.textChanged.connect(self._update_summary)

        self.summary = QtWidgets.QLabel("")
        self.summary.setStyleSheet(f"color:{_MUTED};")
        self.build_btn = QtWidgets.QPushButton("Build task grid")
        self.build_btn.clicked.connect(self._build)
        self.build_btn.setEnabled(False)

        self.result = QtWidgets.QPlainTextEdit()
        self.result.setReadOnly(True)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addWidget(_bold(QtWidgets.QLabel("Task types for every shot")))
        outer.addWidget(self.list, 1)
        outer.addWidget(self.custom)
        outer.addWidget(self.summary)
        outer.addWidget(self.build_btn)
        outer.addWidget(_bold(QtWidgets.QLabel("Result")))
        outer.addWidget(self.result, 1)

    # ----

    def set_project(self, code: str):
        self._project_code = code or ""
        self._n_shots = 0
        if self._project_code:
            try:
                self._n_shots = len(self.admin.existing_shots(self._project_code))
            except Exception:
                self._n_shots = 0
        self._reload_types()
        self._update_summary()

    def _reload_types(self):
        self.list.blockSignals(True)
        self.list.clear()
        have = self.admin.shot_task_types()
        for n in list(dict.fromkeys([*have, *_DEFAULT_TASKS])):
            it = QtWidgets.QListWidgetItem(n)
            it.setFlags(it.flags() | ITEM_IS_USER_CHECKABLE)
            it.setCheckState(CHECK_CHECKED if n in have else CHECK_UNCHECKED)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.build_btn.setEnabled(bool(self._project_code) and self._can_write)

    def _selected_types(self) -> list[str]:
        picked = [self.list.item(i).text() for i in range(self.list.count())
                  if self.list.item(i).checkState() == CHECK_CHECKED]
        extra = [t.strip() for t in self.custom.text().split(",") if t.strip()]
        return list(dict.fromkeys([*picked, *extra]))

    def _update_summary(self, *_):
        if not self._project_code:
            self.summary.setText("Pick a project.")
            return
        n_types = len(self._selected_types())
        self.summary.setText(f"{self._n_shots} shot(s) × {n_types} task type(s) "
                             f"= up to {self._n_shots * n_types} tasks")

    def _build(self):
        types = self._selected_types()
        if not types or not self._project_code:
            QtWidgets.QMessageBox.warning(self, "Nothing to do", "Pick at least one task type.")
            return
        dlg = QtWidgets.QProgressDialog("Building task grid…", "Cancel",
                                       0, max(self._n_shots, 1), self)
        dlg.setWindowModality(WINDOW_MODAL)

        def progress(done, total, shot):
            dlg.setMaximum(total)
            dlg.setValue(done)
            dlg.setLabelText(f"{getattr(shot, 'code', '')}  ({done}/{total})")
            QtWidgets.QApplication.processEvents()

        try:
            tasks = self.admin.build_task_grid(self._project_code, types, progress=progress)
        except NotAuthorized as e:
            dlg.close()
            QtWidgets.QMessageBox.critical(self, "Not allowed", str(e))
            return
        except Exception as e:
            dlg.close()
            QtWidgets.QMessageBox.critical(self, "Build failed", f"{type(e).__name__}: {e}")
            return
        dlg.setValue(dlg.maximum())

        by_type: dict[str, int] = {}
        for t in tasks:
            key = getattr(t, "task_type_name", "?") or "?"
            by_type[key] = by_type.get(key, 0) + 1
        lines = [f"{len(tasks)} task(s) ensured across the shot grid:", ""]
        lines += [f"  {name:<16} {n}" for name, n in sorted(by_type.items())]
        self.result.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, admin: ProjectAdmin, parent=None):
        super().__init__(parent)
        self.admin = admin
        self.setWindowTitle("Square — Project Setup")
        self.resize(920, 720)

        self.project_combo = QtWidgets.QComboBox()
        self._reload_projects()
        self.project_combo.currentIndexChanged.connect(self._project_changed)

        who = getattr(admin.user, "email", "?") or "?"
        role = getattr(admin.user, "role", "?") or "?"
        badge = QtWidgets.QLabel(f"  {who} · {role}")
        if not admin.can_write():
            badge.setText(badge.text() + "  (read-only — need admin/manager)")
            badge.setStyleSheet(f"color:{_WARN};")

        tb = self.addToolBar("main")
        tb.setMovable(False)
        tb.addWidget(QtWidgets.QLabel("Project: "))
        tb.addWidget(self.project_combo)
        tb.addAction("↻ Refresh", self._hard_refresh)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(SIZE_EXPANDING, SIZE_PREFERRED)
        tb.addWidget(spacer)
        tb.addWidget(badge)

        self.new_pane = NewProjectPane(admin)
        self.breakdown_pane = BreakdownPane(admin)
        self.roadmap_pane = RoadmapPane(admin)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.new_pane, "New Project")
        self.tabs.addTab(self.breakdown_pane, "Breakdown")
        self.tabs.addTab(self.roadmap_pane, "Roadmap")
        self.setCentralWidget(self.tabs)

        self.new_pane.projectCreated.connect(self._on_project_created)
        self.breakdown_pane.breakdownChanged.connect(
            lambda: self.roadmap_pane.set_project(self.current_project()))

        self._project_changed()

    # ----

    def _reload_projects(self):
        self.project_combo.blockSignals(True)
        keep = self.project_combo.currentData()
        self.project_combo.clear()
        self.project_combo.addItem("— pick a project —", "")
        try:
            for p in self.admin.projects():
                self.project_combo.addItem(f"{p.code}  ({p.name})", p.code)
        except Exception as e:
            self.project_combo.addItem(f"(load failed: {e})", "")
        if keep:
            idx = self.project_combo.findData(keep)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)

    def current_project(self) -> str:
        return self.project_combo.currentData() or ""

    def _project_changed(self, *_):
        code = self.current_project()
        self.breakdown_pane.set_project(code)
        self.roadmap_pane.set_project(code)

    def _on_project_created(self, code: str):
        self._reload_projects()
        idx = self.project_combo.findData(code)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)     # fires _project_changed
        self.tabs.setCurrentWidget(self.breakdown_pane)

    def _hard_refresh(self):
        self._reload_projects()
        self._project_changed()
