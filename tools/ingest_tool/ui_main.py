import sys
import logging
from pathlib import Path

from Qt import QtCore, QtWidgets, QtGui

from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient
from square_core.plate_scanner import PlateScanner
from square_core.nas_manager import NASManager
from square_core.proxy_generator import ProxyGenerator

from tools.ingest_tool.widgets.scanner_widget import ScannerWidget
from tools.ingest_tool.widgets.table_widget import IngestTableWidget
from tools.ingest_tool.widgets.progress_dialog import IngestProgressDialog
from tools.ingest_tool.widgets.settings_dialog import SettingsDialog
from tools.qt_compat import DIALOG_ACCEPTED, FONT_BOLD, ORIENTATION_HORIZONTAL

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

        # Task Selection Section
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


class IngestWorkerThread(QtCore.QThread):
    """Background worker for non-blocking ingestion execution."""

    progress_signal = QtCore.Signal(int, str)
    finished_signal = QtCore.Signal(bool, str)

    def __init__(self, items, project_data, nas_root, dry_run=True, kitsu_host=None, kitsu_user=None, kitsu_pass=None):
        super(IngestWorkerThread, self).__init__()
        self.items = items
        self.project_data = project_data
        self.nas_root = nas_root
        self.dry_run = dry_run
        self.kitsu_host = kitsu_host
        self.kitsu_user = kitsu_user
        self.kitsu_pass = kitsu_pass

    def run(self):
        try:
            total_items = len(self.items)
            if total_items == 0:
                self.finished_signal.emit(True, "No items to ingest.")
                return

            kitsu = KitsuClient(
                host=self.kitsu_host,
                email=self.kitsu_user,
                password=self.kitsu_pass,
                dry_run=False
            )
            kitsu.connect()

            nas = NASManager(nas_root=self.nas_root, dry_run=self.dry_run)
            proxy_gen = ProxyGenerator(dry_run=self.dry_run)

            proj_data = self.project_data or {"id": "11111111-1111-1111-1111-111111111111", "name": "Feature Film Alpha", "code": "FFA"}
            proj_code = proj_data.get("code", "PROJ")

            for idx, item in enumerate(self.items):
                step_pct = int((idx / total_items) * 100)
                
                # 1. Version Detection & Skipping Check
                version_num, is_already_ingested = nas.get_plate_version_info(
                    proj_code, item.sequence_code, item.shot_code, item.plate_name, item=item
                )
                dest_dir = nas.get_dest_dir(proj_code, item.sequence_code, item.shot_code, item.plate_name, version=version_num)

                # 2. Sync Sequence & Shot with Kitsu DB
                msg = f"Syncing Kitsu Shot: '{item.shot_code}' (Plate '{item.plate_name}', v{version_num:03d})..."
                self.progress_signal.emit(step_pct, msg)
                
                seq_obj = kitsu.get_or_create_sequence(proj_data, item.sequence_code)
                shot_obj = kitsu.get_or_create_shot(
                    proj_data, seq_obj, item.shot_code,
                    plate_name=item.plate_name,
                    frame_in=item.start_frame,
                    frame_out=item.end_frame,
                    fps=item.fps,
                    resolution=item.resolution,
                    colorspace=item.colorspace,
                    nas_path=str(dest_dir)
                )
                tasks = kitsu.create_default_tasks(shot_obj)

                # 3. Handle File Transfers and Preview Uploads
                if is_already_ingested:
                    msg = f"Plate '{item.plate_name}' (v{version_num:03d}) already ingested. Skipping duplicate copy and Kitsu upload."
                    self.progress_signal.emit(step_pct + 40, msg)
                    continue

                # Copy Files to NAS for NEW Ingestion Version
                msg = f"Creating NAS folder: {dest_dir} (v{version_num:03d})"
                self.progress_signal.emit(step_pct + 10, msg)
                nas.create_shot_structure(dest_dir)

                msg = f"Copying {len(item.files)} plate files to v{version_num:03d}..."
                self.progress_signal.emit(step_pct + 20, msg)
                nas.copy_sequence(item, dest_dir)

                # Generate and Upload Proxy Preview for NEW Ingestion Version
                msg = f"Encoding low-res MP4 preview card..."
                self.progress_signal.emit(step_pct + 30, msg)
                proxy_path = proxy_gen.generate_proxy(item)

                if proxy_path and tasks:
                    ingest_task = next((t for t in tasks if (t.get("name") or t.get("task_type_name")) in ("Ingest", "Prep")), tasks[0])
                    task_name = ingest_task.get("name") or ingest_task.get("task_type_name") or "Ingest"
                    
                    msg = f"Uploading preview to task '{task_name}' & setting Shot Thumbnail..."
                    self.progress_signal.emit(step_pct + 40, msg)
                    kitsu.upload_preview_proxy(ingest_task, proxy_path, comment=f"Plate Ingest Preview v{version_num:03d} ({item.plate_name})")

            self.finished_signal.emit(True, "All plates ingested successfully!")

        except Exception as e:
            logger.error(f"Ingestion worker failed: {e}")
            self.finished_signal.emit(False, f"Ingestion Error: {str(e)}")


class MainWindow(QtWidgets.QMainWindow):
    """Main Application Window for Ingest Tool."""

    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle("Square VFX Ingest Tool")
        self.resize(1100, 700)

        self.config = StudioConfig()
        self.kitsu = KitsuClient(
            host=self.config.kitsu_url,
            email=self.config.kitsu_user,
            password=self.config.kitsu_password,
            dry_run=False
        )
        self.is_kitsu_live = self.kitsu.connect()

        self.items = []
        self.project_data = None
        self.setup_ui()
        self.load_projects()

    def setup_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)

        # Header Bar
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("SQUARE VFX - PLATE INGESTION & PIPELINE ENGINE")
        title.setFont(QtGui.QFont("Segoe UI", 12, FONT_BOLD))

        self.status_indicator = QtWidgets.QLabel()
        self.update_status_indicator()

        self.settings_btn = QtWidgets.QPushButton("⚙️ Settings")
        self.settings_btn.clicked.connect(self.on_open_settings)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.status_indicator)
        header.addWidget(self.settings_btn)
        main_layout.addLayout(header)

        # Project Bar
        proj_layout = QtWidgets.QHBoxLayout()
        proj_label = QtWidgets.QLabel("Target Project:")

        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.setMinimumWidth(220)
        self.project_combo.currentIndexChanged.connect(self.on_project_changed)

        self.new_proj_btn = QtWidgets.QPushButton("➕ New Project")
        self.new_proj_btn.clicked.connect(self.on_create_new_project)

        self.refresh_btn = QtWidgets.QPushButton("🔄 Refresh")
        self.refresh_btn.clicked.connect(self.load_projects)

        proj_layout.addWidget(proj_label)
        proj_layout.addWidget(self.project_combo)
        proj_layout.addWidget(self.new_proj_btn)
        proj_layout.addWidget(self.refresh_btn)
        proj_layout.addStretch()

        self.dry_run_check = QtWidgets.QCheckBox("Dry-Run NAS Files (No Copy)")
        self.dry_run_check.setChecked(self.config.dry_run)
        proj_layout.addWidget(self.dry_run_check)

        main_layout.addLayout(proj_layout)

        # Main Splitter
        splitter = QtWidgets.QSplitter(ORIENTATION_HORIZONTAL)

        self.scanner_widget = ScannerWidget()
        self.scanner_widget.scan_requested.connect(self.on_scan_folder)

        self.table_widget = IngestTableWidget()

        splitter.addWidget(self.scanner_widget)
        splitter.addWidget(self.table_widget)
        splitter.setSizes([300, 800])

        main_layout.addWidget(splitter)

        # Bottom Action Bar
        bottom_bar = QtWidgets.QHBoxLayout()
        self.ingest_btn = QtWidgets.QPushButton("🚀 Start Ingestion Process")
        self.ingest_btn.setFont(QtGui.QFont("Segoe UI", 11, FONT_BOLD))
        self.ingest_btn.setStyleSheet("background-color: #2563EB; color: white; padding: 10px 20px;")
        self.ingest_btn.clicked.connect(self.on_start_ingest)

        bottom_bar.addStretch()
        bottom_bar.addWidget(self.ingest_btn)
        main_layout.addLayout(bottom_bar)

    def update_status_indicator(self):
        if self.is_kitsu_live:
            self.status_indicator.setText("🟢 Connected to Kitsu")
            self.status_indicator.setStyleSheet("color: #10B981; font-weight: bold;")
        else:
            self.status_indicator.setText("🟠 Offline / Mock Mode")
            self.status_indicator.setStyleSheet("color: #F59E0B; font-weight: bold;")

    def load_projects(self):
        self.project_combo.clear()
        projects = self.kitsu.get_all_projects()
        for proj in projects:
            display_name = f"{proj.get('name')} [{proj.get('code')}]"
            self.project_combo.addItem(display_name, proj)

    def on_project_changed(self, index):
        if index >= 0:
            self.project_data = self.project_combo.itemData(index)

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
            self.kitsu = KitsuClient(
                host=self.config.kitsu_url,
                email=self.config.kitsu_user,
                password=self.config.kitsu_password,
                dry_run=False
            )
            self.is_kitsu_live = self.kitsu.connect()
            self.update_status_indicator()
            self.load_projects()

    def on_scan_folder(self, folder_path):
        scanner = PlateScanner(search_path=folder_path)
        self.items = scanner.scan()
        self.table_widget.populate_table(self.items)

    def on_start_ingest(self):
        items = self.table_widget.get_selected_items()
        if not items:
            QtWidgets.QMessageBox.warning(self, "No Selection", "Please scan and select items to ingest.")
            return

        proj_data = self.project_data
        nas_root = self.config.nas_root
        dry_run = self.dry_run_check.isChecked()

        self.progress_dialog = IngestProgressDialog(self)
        self.progress_dialog.show()

        self.worker = IngestWorkerThread(
            items, proj_data, nas_root,
            dry_run=dry_run,
            kitsu_host=self.config.kitsu_url,
            kitsu_user=self.config.kitsu_user,
            kitsu_pass=self.config.kitsu_password
        )
        self.worker.progress_signal.connect(self.progress_dialog.update_progress)
        self.worker.finished_signal.connect(self.on_ingest_finished)
        self.worker.start()

    def on_ingest_finished(self, success, message):
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.close()

        if success:
            QtWidgets.QMessageBox.information(self, "Ingestion Complete", message)
        else:
            QtWidgets.QMessageBox.critical(self, "Ingestion Failed", message)
