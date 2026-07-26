import os
import sys
import logging
from Qt import QtWidgets, QtCore, QtGui

from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient
from square_core.plate_scanner import PlateScanner
from square_core.nas_manager import NASManager
from square_core.proxy_generator import ProxyGenerator

from tools.ingest_tool.widgets.scanner_widget import ScannerWidget
from tools.ingest_tool.widgets.table_widget import IngestTableWidget
from tools.ingest_tool.widgets.progress_dialog import IngestProgressDialog
from tools.ingest_tool.widgets.settings_dialog import SettingsDialog

logger = logging.getLogger("IngestMainUI")


from tools.qt_compat import DIALOG_ACCEPTED

class CreateProjectDialog(QtWidgets.QDialog):
    """Dialog to create a new project in Kitsu."""

    def __init__(self, kitsu_client, parent=None):
        super(CreateProjectDialog, self).__init__(parent)
        self.setWindowTitle("Create New Kitsu Project")
        self.setMinimumWidth(380)
        self.kitsu = kitsu_client
        self.created_project = None
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
                dry_run=self.dry_run
            )
            kitsu.connect()

            nas = NASManager(nas_root=self.nas_root, dry_run=self.dry_run)
            proxy_gen = ProxyGenerator(dry_run=self.dry_run)

            proj_data = self.project_data or {"id": "11111111-1111-1111-1111-111111111111", "name": "Feature Film Alpha", "code": "FFA"}
            proj_code = proj_data.get("code", "PROJ")

            for idx, item in enumerate(self.items):
                step_pct = int((idx / total_items) * 100)
                
                # 1. Sync with Kitsu
                msg = f"Connecting to Kitsu: Sequence '{item.sequence_code}', Shot '{item.shot_code}'..."
                self.progress_signal.emit(step_pct, msg)
                
                seq_obj = kitsu.get_or_create_sequence(proj_data, item.sequence_code)
                dest_dir = nas.get_dest_dir(proj_code, item.sequence_code, item.shot_code, item.plate_name)
                
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

                # 2. Create NAS Folders & Copy Files
                msg = f"Creating NAS folder: {dest_dir}"
                self.progress_signal.emit(step_pct + 10, msg)
                nas.create_shot_structure(dest_dir)

                msg = f"Copying {len(item.files)} plate files..."
                self.progress_signal.emit(step_pct + 20, msg)
                nas.copy_sequence(item, dest_dir)

                # 3. Generate Low-Res Proxy Video
                msg = f"Encoding low-res MP4 preview for Kitsu..."
                self.progress_signal.emit(step_pct + 30, msg)
                proxy_path = proxy_gen.generate_proxy(item)

                # 4. Upload Proxy to Kitsu Task
                if proxy_path and tasks:
                    comp_task = tasks[-1]  # Comp or first task
                    msg = f"Uploading preview to Kitsu task '{comp_task['name']}'..."
                    self.progress_signal.emit(step_pct + 40, msg)
                    kitsu.upload_preview_proxy(comp_task, proxy_path)

            self.finished_signal.emit(True, "All plates ingested successfully!")

        except Exception as e:
            logger.error(f"Ingestion worker failed: {e}")
            self.finished_signal.emit(False, f"Ingestion Error: {str(e)}")


class MainWindow(QtWidgets.QMainWindow):
    """Main Application Window for Ingest Tool."""

    def __init__(self):
        super(MainWindow, self).__init__()
        self.setWindowTitle("Square VFX - Plate Ingest Tool")
        self.resize(1100, 750)

        self.config = StudioConfig()
        self.kitsu = KitsuClient(
            host=self.config.kitsu_url,
            email=self.config.kitsu_user,
            password=self.config.kitsu_password,
            dry_run=False
        )
        self.is_kitsu_live = self.kitsu.connect()

        self.discovered_items = []
        self.setup_ui()
        self.load_projects()

    def setup_ui(self):
        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)
        layout = QtWidgets.QVBoxLayout(main_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Header Bar
        header = QtWidgets.QFrame()
        header.setObjectName("HeaderWidget")
        header_layout = QtWidgets.QHBoxLayout(header)
        
        title_layout = QtWidgets.QVBoxLayout()
        lbl_title = QtWidgets.QLabel("SQUARE VFX - INGEST PIPELINE")
        lbl_title.setObjectName("HeaderTitle")
        
        status_str = f"🟢 Connected to Kitsu ({self.config.kitsu_url})" if self.is_kitsu_live else f"🟡 Kitsu Offline / Unreachable ({self.config.kitsu_url})"
        lbl_sub = QtWidgets.QLabel(f"Smart Plate Ingestion • {status_str}")
        lbl_sub.setObjectName("HeaderSubtitle")
        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_sub)

        self.dry_run_check = QtWidgets.QCheckBox("Dry-Run NAS Files")
        self.dry_run_check.setChecked(self.config.dry_run)
        self.dry_run_check.setStyleSheet("font-weight: bold; color: #F59E0B;")

        self.settings_btn = QtWidgets.QPushButton("⚙️ Settings")
        self.settings_btn.setStyleSheet("background-color: #374151; font-weight: bold;")
        self.settings_btn.clicked.connect(self.on_open_settings)

        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.dry_run_check)
        header_layout.addWidget(self.settings_btn)
        layout.addWidget(header)

        # Project & NAS Settings Card
        settings_box = QtWidgets.QGroupBox("Project & Storage Settings")
        settings_layout = QtWidgets.QHBoxLayout(settings_box)

        settings_layout.addWidget(QtWidgets.QLabel("Target Kitsu Project:"))
        self.project_combo = QtWidgets.QComboBox()
        self.project_combo.setMinimumWidth(220)
        settings_layout.addWidget(self.project_combo)

        self.new_proj_btn = QtWidgets.QPushButton("➕ New Project")
        self.new_proj_btn.setStyleSheet("background-color: #059669; font-weight: bold;")
        self.new_proj_btn.clicked.connect(self.on_create_new_project)
        settings_layout.addWidget(self.new_proj_btn)

        settings_layout.addSpacing(20)

        settings_layout.addWidget(QtWidgets.QLabel("NAS Storage Root:"))
        self.nas_root_edit = QtWidgets.QLineEdit(self.config.nas_root)
        settings_layout.addWidget(self.nas_root_edit)

        layout.addWidget(settings_box)

        # Scanner Drop Widget
        self.scanner_widget = ScannerWidget()
        self.scanner_widget.scan_requested.connect(self.on_scan_requested)
        layout.addWidget(self.scanner_widget)

        # Scanned Items Table
        table_box = QtWidgets.QGroupBox("Scanned Media & Plate Metadata")
        table_layout = QtWidgets.QVBoxLayout(table_box)
        self.table_widget = IngestTableWidget()
        table_layout.addWidget(self.table_widget)
        layout.addWidget(table_box)

        # Action Bottom Bar
        bottom_bar = QtWidgets.QHBoxLayout()
        self.item_count_label = QtWidgets.QLabel("0 items detected")
        self.item_count_label.setStyleSheet("color: #94A3B8; font-weight: bold;")

        self.ingest_btn = QtWidgets.QPushButton("🚀 Start Ingestion Process")
        self.ingest_btn.setObjectName("IngestButton")
        self.ingest_btn.setEnabled(False)
        self.ingest_btn.clicked.connect(self.on_start_ingest)

        bottom_bar.addWidget(self.item_count_label)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.ingest_btn)
        layout.addLayout(bottom_bar)

    def load_projects(self):
        projects = self.kitsu.get_all_projects()
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(f"{p['name']} ({p.get('code', 'PRJ')})", p)

    def on_create_new_project(self):
        dialog = CreateProjectDialog(self.kitsu, self)
        res = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
        if res == DIALOG_ACCEPTED and dialog.created_project:
            self.load_projects()
            # Select the newly created project in dropdown
            target_id = dialog.created_project.get("id")
            target_name = dialog.created_project.get("name")
            for i in range(self.project_combo.count()):
                data = self.project_combo.itemData(i)
                if data and (data.get("id") == target_id or data.get("name") == target_name):
                    self.project_combo.setCurrentIndex(i)
                    break

    def on_open_settings(self):
        dialog = SettingsDialog(self)
        dialog.config_saved.connect(self.on_config_updated)
        if hasattr(dialog, "exec"):
            dialog.exec()
        else:
            dialog.exec_()

    def on_config_updated(self):
        self.config.load()
        self.nas_root_edit.setText(self.config.nas_root)
        self.dry_run_check.setChecked(self.config.dry_run)
        
        # Re-connect Kitsu client with updated URL/credentials
        self.kitsu = KitsuClient(
            host=self.config.kitsu_url,
            email=self.config.kitsu_user,
            password=self.config.kitsu_password,
            dry_run=False
        )
        self.is_kitsu_live = self.kitsu.connect()
        self.load_projects()

    def on_scan_requested(self, folder_path):
        scanner = PlateScanner(folder_path)
        items = scanner.scan()
        self.discovered_items = items
        self.table_widget.populate_items(items)

        count = len(items)
        self.item_count_label.setText(f"{count} sequence/media items detected")
        self.ingest_btn.setEnabled(count > 0)

    def on_start_ingest(self):
        items = self.table_widget.get_updated_items()
        if not items:
            return

        proj_data = self.project_combo.currentData() or {"id": "11111111-1111-1111-1111-111111111111", "code": "FFA"}
        nas_root = self.nas_root_edit.text().strip()
        dry_run = self.dry_run_check.isChecked()

        # Launch progress modal
        self.progress_dialog = IngestProgressDialog(self)
        self.progress_dialog.show()

        # Launch background worker thread
        self.worker = IngestWorkerThread(
            items, proj_data, nas_root,
            dry_run=dry_run,
            kitsu_host=self.config.kitsu_url,
            kitsu_user=self.config.kitsu_user,
            kitsu_pass=self.config.kitsu_password
        )
        self.worker.progress_signal.connect(self.progress_dialog.update_progress)
        self.worker.finished_signal.connect(self.progress_dialog.finish)
        self.worker.start()
