"""
Square VFX Ingest Tool -- main window.

Thin shell: it wires the folder tree, the review table, and the bottom
action bar to a single IngestController (through a ControllerBridge), and
owns the session (save / open / autosave / resume). All the real work --
pre-flight, conflict model, ingest, ledger, Kitsu -- lives in
tools.ingest_tool.core / square_core.
"""

from __future__ import annotations

import os
import logging

from Qt import QtCore, QtWidgets, QtGui

from square_core import __version__
from square_core.context import PipelineContext
from square_core.config import PipelineConfig
from square_core.errors import NeedsLogin
from square_core.services import projects as projects_service
from square_core.services.projects import ProjectSpec
from square_core.media.scanner import PlateScanner

from tools.qt_compat import (FONT_BOLD, ORIENTATION_HORIZONTAL, DIALOG_ACCEPTED,
                             exec_dialog, MSGBOX_YES)
from tools.widgets.login_dialog import LoginDialog
from tools.ingest_tool.controller_bridge import ControllerBridge
from tools.ingest_tool.core.controller import IngestController
from tools.ingest_tool.core.ledger import IngestLedger, NullLedger
from tools.ingest_tool.core.session import (
    IngestSession, SessionAutosaver, SESSION_SUFFIX, remember_session, last_session,
)
from tools.ingest_tool.widgets.folder_tree_widget import FolderTreeWidget
from tools.ingest_tool.widgets.review_table import IngestReviewTable
from tools.ingest_tool.widgets.detail_panel import DetailPanel
from tools.ingest_tool.widgets.task_selection_dialog import TaskSelectionDialog
from tools.ingest_tool.widgets.results_dialog import DryRunResultsDialog

logger = logging.getLogger("IngestMainUI")

_DEFAULT_TASK_TYPES = ["Ingest", "Prep", "Roto", "Matchmove", "Comp"]


class CreateProjectDialog(QtWidgets.QDialog):
    """Create a new project -- one call, `services.projects.create`: the
    Kitsu project, its file_tree, `project_config.json`, and the folder
    skeleton, together."""

    def __init__(self, ctx: PipelineContext, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create New Project")
        self.setMinimumWidth(380)
        self._ctx = ctx
        self.created_code: str | None = None

        form = QtWidgets.QFormLayout(self)
        self.name_edit = QtWidgets.QLineEdit()
        self.code_edit = QtWidgets.QLineEdit()
        self.err = QtWidgets.QLabel()
        self.err.setStyleSheet("color:#F87171;")
        form.addRow("Project Name:", self.name_edit)
        form.addRow("Project Code:", self.code_edit)
        form.addRow("", self.err)

        btns = QtWidgets.QHBoxLayout()
        ok = QtWidgets.QPushButton("Create")
        ok.clicked.connect(self._create)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(cancel); btns.addWidget(ok)
        form.addRow(btns)

    def _create(self):
        name = self.name_edit.text().strip()
        code = self.code_edit.text().strip().upper()
        if not (name and code):
            return
        try:
            projects_service.create(self._ctx, ProjectSpec(code=code, name=name))
            self.created_code = code
            self.accept()
        except Exception as e:
            self.err.setText(f"Failed: {e}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Square VFX — Media Ingest  v{__version__}")
        self.resize(1360, 820)

        self.ctx: PipelineContext | None = None
        self.pctx = None                       # ProjectContext, once a project is chosen
        self.controller: IngestController | None = None
        self.bridge: ControllerBridge | None = None
        self.session_path: str | None = None
        self._autosaver: SessionAutosaver | None = None
        self._delivery_root = ""
        self._path_patterns: list = []

        self.ctx = self._connect()
        self._build_ui()
        self._load_projects()
        self._resume_timer = QtCore.QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._offer_resume)
        self._resume_timer.start(200)

    # ------------------------------------------------------------------
    # Kitsu / auth
    # ------------------------------------------------------------------

    def _connect(self) -> PipelineContext:
        try:
            return PipelineContext.connect()
        except NeedsLogin:
            host = PipelineConfig.load().kitsu_host
            dlg = LoginDialog(host, self)
            if exec_dialog(dlg):
                try:
                    return PipelineContext.connect()
                except Exception as e:
                    logger.warning("[IngestMainUI] connect after login failed: %s", e)
        except Exception as e:
            logger.warning("[IngestMainUI] Kitsu connect failed, running offline: %s", e)
        self._kitsu_error = "not signed in"
        return PipelineContext.connect(offline=True)

    @property
    def is_kitsu_live(self) -> bool:
        return not self.ctx.offline

    def _logout(self):
        if self._any_unsaved_work():
            r = QtWidgets.QMessageBox.question(
                self, "Log Out", "There are unsaved rows. Log out anyway?")
            if r != MSGBOX_YES:
                return
        from square_core.kitsu import auth
        auth.forget(self.ctx.config.kitsu_host)
        self.ctx = self._connect()
        self._refresh_user_badge()
        self._load_projects()

    def _any_unsaved_work(self) -> bool:
        return bool(self.controller and self.controller.items and not self.session_path)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- top bar ----
        top = QtWidgets.QFrame()
        top.setFixedHeight(46)
        tl = QtWidgets.QHBoxLayout(top)
        tl.setContentsMargins(12, 0, 12, 0)

        title = QtWidgets.QLabel("SQUARE VFX — MEDIA INGEST")
        title.setFont(QtGui.QFont("Segoe UI", 11, FONT_BOLD))

        tl.addWidget(title)
        tl.addSpacing(16)
        tl.addWidget(QtWidgets.QLabel("Project:"))
        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.setMinimumWidth(240)
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        tl.addWidget(self.project_combo)

        self.new_proj_btn = QtWidgets.QPushButton("+ New")
        self.new_proj_btn.clicked.connect(self._on_new_project)
        tl.addWidget(self.new_proj_btn)
        refresh = QtWidgets.QPushButton("↻")
        refresh.clicked.connect(self._load_projects)
        tl.addWidget(refresh)

        tl.addStretch()

        # who's signed in, gated write access is Kitsu-role driven elsewhere
        # (the config editor); here it's just "who is this ingest attributed to"
        self.user_lbl = QtWidgets.QLabel()
        tl.addWidget(self.user_lbl)
        self.logout_btn = QtWidgets.QPushButton("Log Out")
        self.logout_btn.clicked.connect(self._logout)
        tl.addWidget(self.logout_btn)
        self._refresh_user_badge()

        self.session_btn = QtWidgets.QToolButton()
        self.session_btn.setText("Session ▾")
        self.session_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.session_btn.setMenu(self._session_menu())
        tl.addWidget(self.session_btn)
        root.addWidget(top)

        # ---- splitter: tree | (table + detail) ----
        split = QtWidgets.QSplitter(ORIENTATION_HORIZONTAL)
        self.folder_tree = FolderTreeWidget()
        self.folder_tree.load_requested.connect(self._on_load_requested)
        split.addWidget(self.folder_tree)

        right = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.table = IngestReviewTable(self._dummy_bridge_placeholder())
        self.table.selection_changed.connect(self._on_selection)
        right.addWidget(self.table)

        self.detail = DetailPanel(self._stub_bridge)
        self.detail.setMinimumHeight(150)
        right.addWidget(self.detail)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)

        split.addWidget(right)
        split.setSizes([320, 1040])
        root.addWidget(split, stretch=1)

        # ---- bottom bar ----
        bottom = QtWidgets.QFrame()
        bottom.setFixedHeight(52)
        bl = QtWidgets.QHBoxLayout(bottom)
        bl.setContentsMargins(14, 0, 14, 0)

        self.summary_lbl = QtWidgets.QLabel("No media loaded.")
        bl.addWidget(self.summary_lbl)
        bl.addStretch()

        self.undo_btn = QtWidgets.QPushButton("Undo")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._on_undo)
        bl.addWidget(self.undo_btn)

        self.dry_btn = QtWidgets.QPushButton("Dry Run")
        self.dry_btn.clicked.connect(lambda: self._start_ingest(dry_run=True))
        bl.addWidget(self.dry_btn)

        self.ingest_sel_btn = QtWidgets.QPushButton("Ingest Selected")
        self.ingest_sel_btn.clicked.connect(lambda: self._start_ingest(selected_only=True))
        bl.addWidget(self.ingest_sel_btn)

        self.ingest_all_btn = QtWidgets.QPushButton("Ingest All")
        self.ingest_all_btn.setStyleSheet("font-weight:bold;")
        self.ingest_all_btn.clicked.connect(lambda: self._start_ingest())
        bl.addWidget(self.ingest_all_btn)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bl.addWidget(self.cancel_btn)

        root.addWidget(bottom)
        self._set_ingest_enabled(False)

        for seq, fn in (("Ctrl+S", self._save_session), ("Ctrl+Z", self._on_undo),
                        ("Ctrl+O", self._open_session), ("F5", self._recheck_all)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            sc.activated.connect(fn)

    def _refresh_user_badge(self):
        who = getattr(self.ctx.user, "email", "") or getattr(self.ctx.user, "name", "offline")
        if self.is_kitsu_live:
            self.user_lbl.setText(f"● {who}")
            self.user_lbl.setStyleSheet("color:#10B981; font-weight:bold;")
            self.user_lbl.setToolTip(self.ctx.config.kitsu_host)
        else:
            self.user_lbl.setText("● Offline")
            self.user_lbl.setStyleSheet("color:#F59E0B; font-weight:bold;")
            self.user_lbl.setToolTip(getattr(self, "_kitsu_error", "Kitsu unreachable"))
        self.logout_btn.setEnabled(self.is_kitsu_live)

    def _recheck_all(self):
        if self.bridge and self.controller and self.controller.items:
            self.bridge.preflight()

    def _dummy_bridge_placeholder(self):
        """
        The table needs a bridge at construction. Before a project is
        chosen there's no controller, so hand it a tiny stub that only has
        an `event` signal and an empty `controller.items`; _rebuild_controller
        swaps in the real one.
        """
        class _Stub(QtCore.QObject):
            event = QtCore.Signal(object)

            class _C:
                items = []

                def get(self, k):
                    return None
            controller = _C()

            def resolve_many(self, *a): pass
            def rename_batch(self, *a): return 0
            def rename_cells(self, *a): return 0
            def resolve_rename_template(self, key, template, attr=None): return template
            def skip(self, *a): pass
            def include(self, *a): pass
            def preflight(self, *a): pass
            def remove(self, *a): pass
            def set_field(self, *a): pass
            def set_preview(self, *a): pass
            def set_convert_to_exr(self, *a): pass
        self._stub_bridge = _Stub()
        return self._stub_bridge

    # ------------------------------------------------------------------
    # Controller lifecycle
    # ------------------------------------------------------------------

    def _rebuild_controller(self) -> None:
        if not self.pctx:
            return
        self.folder_tree.set_project(self.pctx)
        root = self.pctx.project.root_path
        ledger = IngestLedger.for_project(self.pctx.pipeline.nas_root, self.pctx.code) \
            if root else NullLedger()
        from tools.ingest_tool.core import config_keys
        self.controller = IngestController(
            self.pctx, ledger=ledger,
            task_types=config_keys.read(self.pctx, "task_types") or list(_DEFAULT_TASK_TYPES),
            ingest_task_status=config_keys.read(self.pctx, "task_status"),
            transfer_mode=config_keys.read(self.pctx, "transfer_mode"),
            ingested_by=getattr(self.ctx.user, "email", ""),
        )
        self._attach_bridge()

    def _attach_bridge(self) -> None:
        """(Re)wire a fresh ControllerBridge to the table, detail panel and window."""
        self.bridge = ControllerBridge(self.controller, self)
        self.bridge.event.connect(self._on_controller_event)
        self.bridge.job_started.connect(self._on_job_started)
        self.bridge.job_finished.connect(self._on_job_finished)

        self.table.bridge = self.bridge
        self.bridge.event.connect(self.table._on_event)
        self.table.rebuild()

        self.detail.bridge = self.bridge
        self.bridge.event.connect(self.detail._on_event)
        self.detail.set_selection([])

        self._autosaver = SessionAutosaver(self._write_session, delay=1.0)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def _load_projects(self):
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        plist = []
        if self.is_kitsu_live:
            try:
                plist = self.ctx.kitsu.projects()
            except Exception as e:
                logger.error("[IngestMainUI] project list failed: %s", e)
        for p in sorted(plist, key=lambda p: p.code):
            self.project_combo.addItem(f"{p.name} [{p.code}]", p.code)
        self.project_combo.blockSignals(False)
        if self.project_combo.count():
            self._on_project_changed(0)

    def _on_project_changed(self, idx):
        if idx < 0:
            return
        code = self.project_combo.itemData(idx)
        if not code:
            return
        try:
            self.pctx = self.ctx.project(code)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Project", f"Could not open {code}:\n{e}")
            return
        self._rebuild_controller()
        self._update_summary()

    def _on_new_project(self):
        if not self.is_kitsu_live:
            QtWidgets.QMessageBox.information(self, "New Project", "Kitsu is offline.")
            return
        dlg = CreateProjectDialog(self.ctx, self)
        if exec_dialog(dlg) == DIALOG_ACCEPTED and dlg.created_code:
            self._load_projects()

    # ------------------------------------------------------------------
    # Loading media
    # ------------------------------------------------------------------

    def _on_load_requested(self, root_path, mapper, selected_paths, is_update):
        if not self.controller:
            QtWidgets.QMessageBox.information(self, "Load", "Choose a project first.")
            return
        try:
            if mapper and mapper.has_map():
                scan_items = mapper.build_items(filter_paths=selected_paths)
                self._path_patterns = [p.to_dict() if hasattr(p, "to_dict") else p
                                       for p in mapper.get_path_patterns()]
            else:
                scan_items = PlateScanner(root_path).scan()
                if selected_paths:
                    scan_items = [
                        s for s in scan_items
                        if {os.path.normcase(os.path.abspath(f)) for f in s.files} & set(selected_paths)
                    ]
            self._delivery_root = root_path
            self.controller.load(scan_items, replace=not is_update)
            self.bridge.preflight()
        except Exception as e:
            logger.exception("[IngestMainUI] load failed")
            QtWidgets.QMessageBox.critical(self, "Load", f"Could not load media:\n{e}")

    # ------------------------------------------------------------------
    # Controller events
    # ------------------------------------------------------------------

    def _on_controller_event(self, ev):
        if self._autosaver and ev.kind in (
            "item_updated", "items_loaded", "preflight_finished", "ingest_finished", "undo",
        ):
            self._autosaver.mark_dirty()
        if ev.kind in ("preflight_finished", "ingest_finished", "items_loaded", "item_updated", "undo"):
            self._update_summary()

    def _on_job_started(self, kind):
        self.cancel_btn.setVisible(True)
        self._set_ingest_enabled(False)
        self.summary_lbl.setText(f"{kind.title()} running…")

    def _on_job_finished(self, kind, error):
        self.cancel_btn.setVisible(False)
        self._update_summary()
        if error:
            QtWidgets.QMessageBox.warning(self, kind.title(), f"{kind} error:\n{error}")
        if kind == "ingest" and not error:
            self._show_results()

    def _on_cancel(self):
        if self.bridge:
            self.bridge.cancel()

    # ------------------------------------------------------------------
    # Selection / detail
    # ------------------------------------------------------------------

    def _on_selection(self, keys):
        self.detail.set_selection(keys or [])

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def _start_ingest(self, *, dry_run=False, selected_only=False):
        if not self.controller:
            return
        keys = self.table.selected_keys() if selected_only else None
        targets = self.controller.ingestable_items(keys)
        if not targets:
            QtWidgets.QMessageBox.information(
                self, "Ingest", "Nothing ready to ingest (resolve conflicts / fill missing info first)."
            )
            return

        task_names = list(dict.fromkeys(self.controller.task_types))
        if self.is_kitsu_live:
            try:
                for tt in self.pctx.kitsu.task_types(for_entity="Shot"):
                    if tt.name not in task_names:
                        task_names.append(tt.name)
            except Exception:
                pass
        dlg = TaskSelectionDialog(task_names or _DEFAULT_TASK_TYPES, parent=self)
        if exec_dialog(dlg) != DIALOG_ACCEPTED:
            return
        self.controller.task_types = dlg.get_selected_tasks() or task_names

        if self._autosaver:
            self._autosaver.flush()
        self.bridge.ingest(keys, dry_run=dry_run)

    def _show_results(self):
        items = []
        for it in self.controller.items:
            if it.status.value in ("Completed", "Failed") or it.ingest_result:
                items.append({
                    "source_name": it.source_name, "sequence_code": it.sequence_code,
                    "shot_code": it.shot_code, "media_type": it.media_type,
                    "media_name": it.media_name, "version": it.version,
                    "resolution": it.resolution, "frame_count": len(it.source_files),
                    "dest_dir": it.ingest_result.get("dest_dir", ""),
                    "sample_dest_file": "", "status": it.status.value + (
                        f": {it.ingest_error}" if it.ingest_error else ""),
                })
        summary = {
            "is_dry_run": any(i.ingest_result.get("dry_run") for i in self.controller.items),
            "project_code": self.pctx.code if self.pctx else "",
            "total_items": len(items), "total_files": sum(len(i.source_files) for i in self.controller.items),
            "task_types": self.controller.task_types, "transfer_mode": "copy",
            "items": items,
        }
        dlg = DryRunResultsDialog(summary, self)
        exec_dialog(dlg)

    # ------------------------------------------------------------------
    # Undo / summary / button state
    # ------------------------------------------------------------------

    def _on_undo(self):
        if self.bridge and self.bridge.undo():
            self._update_summary()

    def _update_summary(self):
        if not self.controller or not self.controller.items:
            self.summary_lbl.setText("No media loaded.")
            self._set_ingest_enabled(False)
            self.undo_btn.setEnabled(False)
            return
        s = self.controller.summary()
        total = len(self.controller.items)
        ready = len(self.controller.ingestable_items())
        parts = [f"{total} rows", f"{ready} ready"]
        for k in ("Conflict", "Needs Info", "Skipped", "Already Ingested", "Completed", "Failed"):
            if s.get(k):
                parts.append(f"{s[k]} {k.lower()}")
        self.summary_lbl.setText("  ·  ".join(parts))
        self.undo_btn.setEnabled(self.bridge.can_undo)
        self.undo_btn.setText(f"Undo: {self.bridge.undo_label}" if self.bridge.can_undo else "Undo")
        self._set_ingest_enabled(ready > 0 and not self.bridge.busy)

    def _set_ingest_enabled(self, on):
        for b in (self.dry_btn, self.ingest_sel_btn, self.ingest_all_btn):
            b.setEnabled(on)

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def _session_menu(self):
        m = QtWidgets.QMenu(self)
        m.addAction("Save Session\tCtrl+S", self._save_session)
        m.addAction("Save Session As…", self._save_session_as)
        m.addAction("Open Session…", self._open_session)
        return m

    def _write_session(self):
        if not (self.controller and self.session_path):
            return
        sess = IngestSession.capture(
            self.controller,
            delivery_root=self.folder_tree.root_path or self._delivery_root,
            path_patterns=self.folder_tree.current_patterns() or self._path_patterns,
            manual_media_types=self.folder_tree.current_media_types(),
            active_preset=self.folder_tree.active_preset(),
        )
        sess.save(self.session_path)
        remember_session(self.session_path)

    def _save_session(self):
        if not self.session_path:
            return self._save_session_as()
        self._write_session()
        self.statusBar().showMessage(f"Saved {self.session_path}", 3000)

    def _save_session_as(self):
        if not self.controller:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Ingest Session", "", f"Ingest Session (*{SESSION_SUFFIX})"
        )
        if not path:
            return
        self.session_path = path
        self._write_session()
        self.statusBar().showMessage(f"Saved {self.session_path}", 3000)

    def _open_session(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Ingest Session", "", f"Ingest Session (*{SESSION_SUFFIX})"
        )
        if path:
            self._resume(path)

    def _offer_resume(self):
        prev = last_session()
        if not prev:
            return
        r = QtWidgets.QMessageBox.question(
            self, "Resume Session",
            f"Reopen your last ingest session?\n\n{prev}",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if r == QtWidgets.QMessageBox.StandardButton.Yes:
            self._resume(prev)

    def _resume(self, path):
        try:
            sess = IngestSession.load(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Open Session", f"Could not load:\n{e}")
            return
        try:
            self.pctx = self.ctx.project(sess.project_code)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Open Session", f"Could not reconnect to project {sess.project_code!r}:\n{e}")
            return
        idx = self.project_combo.findData(sess.project_code)
        if idx >= 0:
            self.project_combo.blockSignals(True)
            self.project_combo.setCurrentIndex(idx)
            self.project_combo.blockSignals(False)
        self._rebuild_controller()
        self.controller.task_types = list(sess.task_types) or self.controller.task_types
        sess.restore_into(self.controller)
        self._delivery_root = sess.delivery_root
        self._path_patterns = sess.path_patterns
        self.session_path = path
        remember_session(path)
        # bring the delivery folder + its Path Patterns + manual tags back
        self.folder_tree.restore(sess.delivery_root, sess.path_patterns,
                                 sess.manual_media_types, sess.active_preset)
        self.table.rebuild()
        self._update_summary()
        # re-check the rows that hadn't finished
        pending = [i.key for i in self.controller.items if not i.ingested]
        if pending:
            self.bridge.preflight(pending)
        # and re-attempt any review proxy that was still in flight when saved
        if self.is_kitsu_live:
            self.controller.run_pending_previews()

    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if getattr(self, "_resume_timer", None):
            self._resume_timer.stop()
        if self._autosaver:
            self._autosaver.flush()
            self._autosaver.stop()
        if self.bridge:
            self.bridge.cancel()
            self.bridge.wait(3000)
        if self.controller:
            self.controller.shutdown()
        super().closeEvent(event)
