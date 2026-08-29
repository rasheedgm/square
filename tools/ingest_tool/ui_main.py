import os
import sys
import logging
from pathlib import Path

from Qt import QtCore, QtWidgets, QtGui

from square_core import __version__
from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient
from square_core.plate_scanner import MediaScanner, PlateScanner
from square_core.folder_mapper import FolderMapper
from square_core.nas_manager import NASManager
from square_core.proxy_generator import ProxyGenerator

from tools.ingest_tool.widgets.scanner_widget import ScannerWidget
from tools.ingest_tool.widgets.folder_tree_widget import FolderTreeWidget
from tools.ingest_tool.widgets.table_widget import IngestTableWidget
from tools.ingest_tool.widgets.progress_dialog import IngestProgressDialog
from tools.ingest_tool.widgets.settings_dialog import SettingsDialog
from tools.ingest_tool.widgets.results_dialog import DryRunResultsDialog
from tools.qt_compat import FONT_BOLD, ALIGN_CENTER, ORIENTATION_HORIZONTAL, DIALOG_ACCEPTED

logger = logging.getLogger("IngestMainUI")


class CreateProjectDialog(QtWidgets.QDialog):
    """Dialog to create a new project in Kitsu with customizable Task Types."""

    def __init__(self, kitsu_client, parent=None):
        super(CreateProjectDialog, self).__init__(parent)
        self.setWindowTitle("Create New Kitsu Project")
        self.setMinimumWidth(400)
        self.kitsu = kitsu_client
        self.created_project = None
        self.task_checkboxes = {}
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Feature Film Avatar")

        self.code_edit = QtWidgets.QLineEdit()
        self.code_edit.setPlaceholderText("e.g. AVT")

        form.addRow("Project Name:", self.name_edit)
        form.addRow("Project Code:", self.code_edit)

        layout.addLayout(form)

        tasks_group = QtWidgets.QGroupBox("Project Task Types Pipeline")
        tasks_layout = QtWidgets.QVBoxLayout()

        default_tasks = ["Ingest", "Prep", "Roto", "Matchmove", "3D", "Comp"]
        for t_name in default_tasks:
            cb = QtWidgets.QCheckBox(t_name)
            cb.setChecked(True)
            self.task_checkboxes[t_name] = cb
            tasks_layout.addWidget(cb)

        tasks_group.setLayout(tasks_layout)
        layout.addWidget(tasks_group)

        btn_box = QtWidgets.QHBoxLayout()
        self.create_btn = QtWidgets.QPushButton("Create Project")
        self.create_btn.setStyleSheet("background-color: #059669; font-weight: bold;")
        self.create_btn.clicked.connect(self.on_create)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        btn_box.addStretch()
        btn_box.addWidget(self.cancel_btn)
        btn_box.addWidget(self.create_btn)
        layout.addLayout(btn_box)

    def get_selected_tasks(self):
        return [t_name for t_name, cb in self.task_checkboxes.items() if cb.isChecked()]

    def on_create(self):
        name = self.name_edit.text().strip()
        code = self.code_edit.text().strip().upper()
        if name and code:
            self.created_project = self.kitsu.create_project(name, code)
            self.accept()


# ---------------------------------------------------------------------------
# Background Workers
# ---------------------------------------------------------------------------

class NASCheckWorker(QtCore.QThread):
    """Background thread: check all items against NAS for version/duplicate status."""

    progress_signal = QtCore.Signal(int, int)      # done, total
    results_ready   = QtCore.Signal(dict)           # {id(item): (ver, already)}

    def __init__(self, items, nas_root, proj_code, dry_run):
        super().__init__()
        self.items     = items
        self.nas_root  = nas_root
        self.proj_code = proj_code
        self.dry_run   = dry_run

    def run(self):
        nas = NASManager(nas_root=self.nas_root, dry_run=self.dry_run)
        results = nas.check_all_plates(
            self.items,
            self.proj_code,
            progress_callback=lambda done, total: self.progress_signal.emit(done, total)
        )
        self.results_ready.emit(results)


class IngestWorkerThread(QtCore.QThread):
    """Background worker: copy files + push to Kitsu."""

    progress_signal = QtCore.Signal(int, str)
    finished_signal = QtCore.Signal(bool, str, object)

    def __init__(self, items_with_versions, project_data, nas_root,
                 dry_run=True, kitsu_host=None, kitsu_user=None, kitsu_pass=None):
        super(IngestWorkerThread, self).__init__()
        self.items_with_versions = items_with_versions   # list of (item, version_int)
        self.project_data = project_data
        self.nas_root     = nas_root
        self.dry_run      = dry_run
        self.kitsu_host   = kitsu_host
        self.kitsu_user   = kitsu_user
        self.kitsu_pass   = kitsu_pass

    def run(self):
        try:
            total = len(self.items_with_versions)
            if total == 0:
                self.finished_signal.emit(True, "No items to ingest.")
                return

            kitsu = KitsuClient(
                host=self.kitsu_host,
                email=self.kitsu_user,
                password=self.kitsu_pass,
                dry_run=False
            )
            kitsu.connect()

            nas       = NASManager(nas_root=self.nas_root, dry_run=self.dry_run)
            proxy_gen = ProxyGenerator(dry_run=self.dry_run)

            proj_data = self.project_data or {"id": "11111111-1111-1111-1111-111111111111",
                                               "name": "Feature Film Alpha", "code": "FFA"}
            proj_code = proj_data.get("code", "PROJ")

            from square_core.config import DEFAULT_FILE_NAME_TEMPLATE, format_dest_filename

            summary = {
                "is_dry_run": self.dry_run,
                "project_code": proj_code,
                "total_items": total,
                "total_files": sum(len(item.files) for item, _ in self.items_with_versions),
                "items": []
            }

            for idx, (item, version_num) in enumerate(self.items_with_versions):
                step_pct = int((idx / total) * 100)
                dest_dir = nas.get_dest_dir(
                    proj_code, item.sequence_code,
                    item.shot_code, item.plate_name,
                    version=version_num,
                    media_type=getattr(item, "media_type", "Plate"),
                    resolution=getattr(item, "resolution", "1920x1080")
                )

                # Kitsu sync
                self.progress_signal.emit(step_pct,
                    f"Syncing Kitsu: {item.shot_code} / {item.plate_name} v{version_num:03d}")
                seq_obj  = kitsu.get_or_create_sequence(proj_data, item.sequence_code)
                shot_obj = kitsu.get_or_create_shot(
                    proj_data, seq_obj, item.shot_code,
                    plate_name=item.plate_name,
                    frame_in=item.start_frame, frame_out=item.end_frame,
                    fps=item.fps, resolution=item.resolution,
                    colorspace=item.colorspace, nas_path=str(dest_dir)
                )
                tasks = kitsu.create_default_tasks(shot_obj)

                # File copy
                self.progress_signal.emit(step_pct + 20,
                    f"Copying {len(item.files)} files → v{version_num:03d}")
                nas.create_shot_structure(dest_dir)
                tmpl = getattr(self, "filename_template", None) or DEFAULT_FILE_NAME_TEMPLATE
                copied = nas.copy_sequence(item, dest_dir, filename_template=tmpl, version_num=version_num, proj_code=proj_code)

                # Proxy + upload
                self.progress_signal.emit(step_pct + 50, "Generating preview...")
                proxy_path = proxy_gen.generate_proxy(item)
                if proxy_path and tasks:
                    ingest_task = next(
                        (t for t in tasks
                         if (t.get("name") or t.get("task_type_name")) in ("Ingest", "Prep")),
                        tasks[0]
                    )
                    task_name = ingest_task.get("name") or "Ingest"
                    self.progress_signal.emit(step_pct + 70,
                        f"Uploading preview to '{task_name}' & setting thumbnail...")
                    kitsu.upload_preview_proxy(
                        ingest_task, proxy_path,
                        comment=f"Plate Ingest v{version_num:03d} ({item.plate_name})"
                    )

                sample_fn = format_dest_filename(
                    tmpl, proj_code, item.sequence_code, item.shot_code,
                    getattr(item, "media_type", "Plate"), item.plate_name,
                    version_num, frame="1001" if not item.is_video else None, ext=item.ext
                )
                summary["items"].append({
                    "source_name": item.name,
                    "sequence_code": item.sequence_code,
                    "shot_code": item.shot_code,
                    "media_type": getattr(item, "media_type", "Plate"),
                    "plate_name": item.plate_name,
                    "version": version_num,
                    "resolution": item.resolution,
                    "frame_count": len(item.files),
                    "dest_dir": str(dest_dir),
                    "sample_dest_file": str(dest_dir / sample_fn),
                    "status": "Dry-Run Simulated" if self.dry_run else "Ingested Successfully"
                })

            msg = f"Dry-Run completed for {total} items." if self.dry_run else f"Successfully ingested {total} items."
            self.finished_signal.emit(True, msg, summary)

        except Exception as e:
            logger.error(f"Ingestion worker failed: {e}")
            self.finished_signal.emit(False, f"Ingestion Error: {str(e)}", None)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    """Main Application Window for Square VFX Ingest Tool."""

    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle(f"Square VFX Ingest Tool v{__version__}")
        self.resize(1280, 780)

        self.config   = StudioConfig()
        self.kitsu    = KitsuClient(
            host=self.config.kitsu_url,
            email=self.config.kitsu_user,
            password=self.config.kitsu_password,
            dry_run=False
        )
        self.is_kitsu_live = self.kitsu.connect()
        self.project_data  = None
        self._nas_check_worker = None

        self.setup_ui()
        self.load_projects()

    def setup_ui(self):
        central = QtWidgets.QWidget()
        central.setStyleSheet("background-color: #0F1117;")
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Header bar ──
        header_frame = QtWidgets.QFrame()
        header_frame.setFixedHeight(52)
        header_frame.setStyleSheet(
            "QFrame { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 #131C2E, stop:1 #0F1117);"
            " border-bottom: 1px solid #1E2D4A; }"
        )
        header_h = QtWidgets.QHBoxLayout(header_frame)
        header_h.setContentsMargins(16, 0, 16, 0)
        header_h.setSpacing(12)

        logo_lbl = QtWidgets.QLabel("⬡")
        logo_lbl.setStyleSheet(
            "font-size:20px; color:#3B82F6; background:transparent;"
        )
        title_lbl = QtWidgets.QLabel("SQUARE VFX — PLATE INGEST")
        title_lbl.setFont(QtGui.QFont("Segoe UI", 11, FONT_BOLD))
        title_lbl.setStyleSheet(
            "color:#E2E8F0; letter-spacing:1px; background:transparent;"
        )

        self.status_indicator = QtWidgets.QLabel()
        self.status_indicator.setStyleSheet("background:transparent;")
        self.update_status_indicator()

        self.settings_btn = QtWidgets.QPushButton("Settings")
        self.settings_btn.setFixedHeight(28)
        self.settings_btn.clicked.connect(self.on_open_settings)

        header_h.addWidget(logo_lbl)
        header_h.addWidget(title_lbl)
        header_h.addStretch()
        header_h.addWidget(self.status_indicator)
        header_h.addWidget(self.settings_btn)
        main_layout.addWidget(header_frame)

        # ── Project bar ──
        proj_frame = QtWidgets.QFrame()
        proj_frame.setFixedHeight(46)
        proj_frame.setStyleSheet(
            "QFrame { background:#131720; border-bottom:1px solid #1E2535; }"
        )
        proj_h = QtWidgets.QHBoxLayout(proj_frame)
        proj_h.setContentsMargins(12, 0, 12, 0)
        proj_h.setSpacing(8)

        proj_lbl = QtWidgets.QLabel("Project:")
        proj_lbl.setStyleSheet("color:#64748B; font-size:12px; background:transparent;")

        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.setMinimumWidth(240)
        self.project_combo.setFixedHeight(30)
        self.project_combo.currentIndexChanged.connect(self.on_project_changed)

        self.new_proj_btn = QtWidgets.QPushButton("+ New Project")
        self.new_proj_btn.setFixedHeight(30)
        self.new_proj_btn.clicked.connect(self.on_create_new_project)

        self.refresh_btn = QtWidgets.QPushButton("↻ Refresh")
        self.refresh_btn.setFixedHeight(30)
        self.refresh_btn.clicked.connect(self.load_projects)

        self.dry_run_check = QtWidgets.QCheckBox("Dry-Run")
        self.dry_run_check.setToolTip("Dry-Run: simulate transfer & path generation without copying files")
        self.dry_run_check.setChecked(self.config.dry_run)
        self.dry_run_check.setStyleSheet("color:#64748B; background:transparent;")
        self.dry_run_check.stateChanged.connect(self._update_ingest_btn_style)

        proj_h.addWidget(proj_lbl)
        proj_h.addWidget(self.project_combo)
        proj_h.addWidget(self.new_proj_btn)
        proj_h.addWidget(self.refresh_btn)
        proj_h.addStretch()
        proj_h.addWidget(self.dry_run_check)
        main_layout.addWidget(proj_frame)

        # ── NAS check thin progress bar ──
        self._check_bar = QtWidgets.QProgressBar()
        self._check_bar.setVisible(False)
        self._check_bar.setFixedHeight(3)
        self._check_bar.setTextVisible(False)
        self._check_bar.setStyleSheet(
            "QProgressBar { background:#131720; border:none; border-radius:0; }"
            "QProgressBar::chunk { background:#3B82F6; }"
        )
        main_layout.addWidget(self._check_bar)

        # ── Splitter: folder tree (left) + table (right) ──
        content = QtWidgets.QWidget()
        content.setStyleSheet("background:#0F1117;")
        content_layout = QtWidgets.QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.main_splitter = QtWidgets.QSplitter(ORIENTATION_HORIZONTAL)
        self.main_splitter.setHandleWidth(4)
        self.main_splitter.setStyleSheet(
            "QSplitter::handle { background:#1E2535; }"
            "QSplitter::handle:hover { background:#3B82F6; }"
        )

        self.folder_tree = FolderTreeWidget()
        self.folder_tree.load_requested.connect(self.on_load_media)

        self.table_widget = IngestTableWidget()
        self.table_widget.table_changed.connect(self._update_conflict_badge)

        self.main_splitter.addWidget(self.folder_tree)
        self.main_splitter.addWidget(self.table_widget)
        self.main_splitter.setSizes([340, 940])
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 2)
        content_layout.addWidget(self.main_splitter)
        main_layout.addWidget(content, stretch=1)

        # ── Bottom action bar ──
        bottom_frame = QtWidgets.QFrame()
        bottom_frame.setFixedHeight(54)
        bottom_frame.setStyleSheet(
            "QFrame { background:#131720; border-top:1px solid #1E2535; }"
        )
        bottom_h = QtWidgets.QHBoxLayout(bottom_frame)
        bottom_h.setContentsMargins(16, 0, 16, 0)
        bottom_h.setSpacing(12)

        self.conflict_badge = QtWidgets.QLabel()
        self.conflict_badge.setStyleSheet(
            "color:#EF4444; font-weight:bold; font-size:12px; background:transparent;"
        )
        self.conflict_badge.setVisible(False)

        self.ingest_btn = QtWidgets.QPushButton("▶  Start Dry Run")
        self.ingest_btn.setFixedHeight(38)
        self.ingest_btn.setMinimumWidth(180)
        self.ingest_btn.setFont(QtGui.QFont("Segoe UI", 11, FONT_BOLD))
        self._update_ingest_btn_style()
        self.ingest_btn.clicked.connect(self.on_start_ingest)

        bottom_h.addWidget(self.conflict_badge)
        bottom_h.addStretch()
        bottom_h.addWidget(self.ingest_btn)
        main_layout.addWidget(bottom_frame)

    def _update_ingest_btn_style(self):
        is_dry_run = self.dry_run_check.isChecked()
        if is_dry_run:
            self.ingest_btn.setText("▶  Start Dry Run")
            self.ingest_btn.setStyleSheet(
                "QPushButton {"
                "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "               stop:0 #DC2626, stop:1 #EF4444);"
                "  color: white; border: none; border-radius: 6px;"
                "}"
                "QPushButton:hover {"
                "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "               stop:0 #EF4444, stop:1 #F87171);"
                "}"
                "QPushButton:disabled { background:#1A2035; color:#374151; }"
            )
        else:
            self.ingest_btn.setText("🚀  Start Ingestion")
            self.ingest_btn.setStyleSheet(
                "QPushButton {"
                "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "               stop:0 #059669, stop:1 #10B981);"
                "  color: white; border: none; border-radius: 6px;"
                "}"
                "QPushButton:hover {"
                "  background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                "               stop:0 #10B981, stop:1 #34D399);"
                "}"
                "QPushButton:disabled { background:#1A2035; color:#374151; }"
            )

    # ------------------------------------------------------------------
    # Project Management
    # ------------------------------------------------------------------

    def update_status_indicator(self):
        if self.is_kitsu_live:
            self.status_indicator.setText("Connected to Kitsu")
            self.status_indicator.setStyleSheet("color:#10B981; font-weight:bold;")
        else:
            self.status_indicator.setText("Offline / Mock Mode")
            self.status_indicator.setStyleSheet("color:#F59E0B; font-weight:bold;")

    def load_projects(self):
        self.project_combo.clear()
        for proj in self.kitsu.get_all_projects():
            self.project_combo.addItem(
                f"{proj.get('name')} [{proj.get('code')}]", proj
            )

    def on_project_changed(self, index):
        if index >= 0:
            self.project_data = self.project_combo.itemData(index)
            code = (self.project_data or {}).get("code", "")
            self.table_widget.set_project_code(code)
            self.table_widget.set_nas_root(self.config.nas_root)
            self.table_widget.set_filename_template(self.config.filename_template)

    def on_create_new_project(self):
        dialog = CreateProjectDialog(self.kitsu, self)
        res = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
        if res == DIALOG_ACCEPTED and dialog.created_project:
            self.load_projects()
            target_id = dialog.created_project.get("id")
            for idx in range(self.project_combo.count()):
                proj = self.project_combo.itemData(idx)
                if proj and proj.get("id") == target_id:
                    self.project_combo.setCurrentIndex(idx)
                    break

    def on_open_settings(self):
        dialog = SettingsDialog(self)
        res = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
        if res == DIALOG_ACCEPTED:
            self.config = StudioConfig()
            self.kitsu  = KitsuClient(
                host=self.config.kitsu_url,
                email=self.config.kitsu_user,
                password=self.config.kitsu_password,
                dry_run=False
            )
            self.is_kitsu_live = self.kitsu.connect()
            self.table_widget.set_nas_root(self.config.nas_root)
            self.table_widget.set_filename_template(self.config.filename_template)
            self.table_widget._refresh_table()
            self.update_status_indicator()
            self.load_projects()

    # ------------------------------------------------------------------
    # Loading / Updating Media
    # ------------------------------------------------------------------

    def on_load_media(self, root_path, folder_mapper=None, selected_paths=None, is_update=False):
        """Called when FolderTreeWidget emits load_requested."""
        try:
            if folder_mapper and folder_mapper.has_map():
                items = folder_mapper.build_items(filter_paths=selected_paths)
                folder_mapper.save_table_state(items)
            elif folder_mapper and folder_mapper._table_state and not is_update:
                items = folder_mapper.get_saved_table_items()
            else:
                scanner = MediaScanner(root_path)
                items   = scanner.scan()
                if selected_paths:
                    filtered = []
                    for item in items:
                        item_paths = {os.path.normcase(os.path.abspath(f)) for f in item.files}
                        if item_paths.intersection(selected_paths):
                            filtered.append(item)
                    items = filtered

            if is_update:
                self.table_widget.update_table(items)
            else:
                self.table_widget.populate_table(items)

            current_items = self.table_widget.items_data
            if folder_mapper:
                folder_mapper.save_table_state(current_items)

            if current_items and self.project_data:
                self._start_nas_check(current_items)
        except Exception as e:
            logger.error(f"[IngestMainUI] Error in on_load_media: {e}", exc_info=True)
            with open("gui_test_step.log", "a", encoding="utf-8") as f:
                import traceback
                f.write(f"ON_LOAD_MEDIA_ERROR:\n{traceback.format_exc()}\n")
            raise e

    def on_scan_folder(self, root_path, folder_mapper=None, selected_paths=None, is_update=False):
        """Alias for backward compatibility."""
        self.on_load_media(root_path, folder_mapper, selected_paths, is_update)

    def _start_nas_check(self, items):
        """Launch background NAS duplicate/version check."""
        if self._nas_check_worker and self._nas_check_worker.isRunning():
            self._nas_check_worker.terminate()

        proj_code = (self.project_data or {}).get("code", "PROJ")
        self._nas_check_worker = NASCheckWorker(
            items=items,
            nas_root=self.config.nas_root,
            proj_code=proj_code,
            dry_run=self.dry_run_check.isChecked()
        )
        total = len(items)
        self._check_bar.setMaximum(total)
        self._check_bar.setValue(0)
        self._check_bar.setVisible(True)

        self._nas_check_worker.progress_signal.connect(
            lambda done, tot: self._check_bar.setValue(done)
        )
        self._nas_check_worker.results_ready.connect(self._on_nas_check_done)
        self._nas_check_worker.start()

    def _on_nas_check_done(self, results):
        self._check_bar.setVisible(False)
        self.table_widget.apply_version_results(results)
        self._update_conflict_badge()

    def _update_conflict_badge(self):
        has_conflicts = self.table_widget.has_unresolved_conflicts()
        has_items     = bool(self.table_widget.get_selected_items())
        if has_conflicts:
            self.conflict_badge.setText("Unresolved conflicts — fix before ingesting")
            self.conflict_badge.setVisible(True)
            self.ingest_btn.setEnabled(False)
        else:
            self.conflict_badge.setVisible(False)
            self.ingest_btn.setEnabled(has_items)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def on_start_ingest(self):
        if self.table_widget.has_unresolved_conflicts():
            QtWidgets.QMessageBox.critical(
                self, "Conflicts Exist",
                "Please resolve all shot name conflicts before ingesting."
            )
            return

        valid_items = self.table_widget.get_valid_ingest_items()
        if not valid_items:
            QtWidgets.QMessageBox.warning(
                self, "Missing Details or Nothing to Ingest",
                "No valid items ready to ingest.\n\n"
                "Please ensure all rows marked for ingestion have Sequence, Shot, Media Type, and Name filled in."
            )
            return

        items_with_versions = valid_items

        self.progress_dialog = IngestProgressDialog(self)
        self.progress_dialog.show()

        self.worker = IngestWorkerThread(
            items_with_versions=items_with_versions,
            project_data=self.project_data,
            nas_root=self.config.nas_root,
            dry_run=self.dry_run_check.isChecked(),
            kitsu_host=self.config.kitsu_url,
            kitsu_user=self.config.kitsu_user,
            kitsu_pass=self.config.kitsu_password
        )
        self.worker.progress_signal.connect(self.progress_dialog.update_progress)
        self.worker.finished_signal.connect(self.on_ingest_finished)
        self.worker.start()

    def on_ingest_finished(self, success, message, summary=None):
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.close()
        if success:
            if summary:
                dlg = DryRunResultsDialog(summary, self)
                dlg.exec() if hasattr(dlg, "exec") else dlg.exec_()
            else:
                QtWidgets.QMessageBox.information(self, "Ingestion Complete", message)
        else:
            QtWidgets.QMessageBox.critical(self, "Ingestion Failed", message)

    def closeEvent(self, event):
        if self._nas_check_worker and self._nas_check_worker.isRunning():
            self._nas_check_worker.quit()
            self._nas_check_worker.wait(1000)
        super(MainWindow, self).closeEvent(event)
