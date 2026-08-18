from Qt import QtWidgets, QtCore, QtGui
from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient
from tools.qt_compat import ECHO_MODE_PASSWORD, HEADER_RESIZE_STRETCH

class SettingsDialog(QtWidgets.QDialog):
    """Settings modal for configuring Kitsu credentials and NAS storage paths."""

    config_saved = QtCore.Signal()

    def __init__(self, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.setWindowTitle("Square VFX - Studio Pipeline Configuration")
        self.setMinimumSize(620, 640)
        self.config = StudioConfig()
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(15)

        # Title Header
        lbl_header = QtWidgets.QLabel("⚙️ Studio & Kitsu Configuration")
        lbl_header.setStyleSheet("font-size: 16px; font-weight: bold; color: #60A5FA;")
        layout.addWidget(lbl_header)

        # Kitsu Credentials Group Box
        kitsu_box = QtWidgets.QGroupBox("Kitsu DB Connection Settings")
        k_layout = QtWidgets.QFormLayout(kitsu_box)
        k_layout.setSpacing(10)

        self.kitsu_url_edit = QtWidgets.QLineEdit(self.config.kitsu_url)
        self.kitsu_url_edit.setPlaceholderText("https://kitsu.squarevfx.com/api")

        self.kitsu_user_edit = QtWidgets.QLineEdit(self.config.kitsu_user)
        self.kitsu_user_edit.setPlaceholderText("pipeline@squarevfx.com")

        self.kitsu_pass_edit = QtWidgets.QLineEdit(self.config.kitsu_password)
        self.kitsu_pass_edit.setEchoMode(ECHO_MODE_PASSWORD)
        self.kitsu_pass_edit.setPlaceholderText("Password")

        self.test_conn_btn = QtWidgets.QPushButton("⚡ Test Live Kitsu Connection")
        self.test_conn_btn.setStyleSheet("background-color: #374151; color: #38BDF8; font-weight: bold;")
        self.test_conn_btn.clicked.connect(self.on_test_connection)

        self.lbl_conn_result = QtWidgets.QLabel("")
        self.lbl_conn_result.setStyleSheet("font-size: 12px; font-weight: bold;")

        k_layout.addRow("Kitsu Server Host URL:", self.kitsu_url_edit)
        k_layout.addRow("Kitsu User Email:", self.kitsu_user_edit)
        k_layout.addRow("Kitsu Password:", self.kitsu_pass_edit)
        k_layout.addRow("", self.test_conn_btn)
        k_layout.addRow("", self.lbl_conn_result)

        layout.addWidget(kitsu_box)

        # NAS Storage Group Box
        nas_box = QtWidgets.QGroupBox("NAS Storage & Local Cache Settings")
        n_layout = QtWidgets.QFormLayout(nas_box)
        n_layout.setSpacing(10)

        self.nas_root_edit = QtWidgets.QLineEdit(self.config.nas_root)
        self.cache_root_edit = QtWidgets.QLineEdit(self.config.cache_root)

        n_layout.addRow("NAS Storage Root:", self.nas_root_edit)
        n_layout.addRow("Local SSD Cache Root:", self.cache_root_edit)

        layout.addWidget(nas_box)

        # Pipeline Naming & Folder Templates Group Box
        tmpl_box = QtWidgets.QGroupBox("Pipeline Naming & Folder Structure Templates")
        t_layout = QtWidgets.QFormLayout(tmpl_box)
        t_layout.setSpacing(10)

        self.filename_tmpl_edit = QtWidgets.QLineEdit(self.config.filename_template)
        self.filename_tmpl_edit.setToolTip("Tokens: {project}, {seq}, {shot}, {type}, {name}, {version}, {frame}, {ext}")

        self.nas_dir_tmpl_edit = QtWidgets.QLineEdit(self.config.nas_dir_template)
        self.nas_dir_tmpl_edit.setToolTip("Tokens: {nas_root}, {project_code}, {sequence_code}, {shot_code}, {plate_type}, {plate_name}, {version}, {resolution}")

        self.shot_struct_edit = QtWidgets.QTextEdit()
        self.shot_struct_edit.setFixedHeight(70)
        self.shot_struct_edit.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
        self.shot_struct_edit.setPlainText("\n".join(self.config.shot_folder_structure))

        t_layout.addRow("File Naming Pattern:", self.filename_tmpl_edit)
        t_layout.addRow("Default Directory Pattern:", self.nas_dir_tmpl_edit)
        t_layout.addRow("Shot Folder Structure:", self.shot_struct_edit)

        layout.addWidget(tmpl_box)

        # Media Types & Path Patterns Group Box
        mt_box = QtWidgets.QGroupBox("Media Types & Storage Path Patterns")
        mt_layout = QtWidgets.QVBoxLayout(mt_box)
        mt_layout.setSpacing(6)

        self.media_types_table = QtWidgets.QTableWidget()
        self.media_types_table.setColumnCount(2)
        self.media_types_table.setHorizontalHeaderLabels(["Media Type", "NAS Directory Path Pattern Template"])
        self.media_types_table.horizontalHeader().setSectionResizeMode(1, HEADER_RESIZE_STRETCH)
        self.media_types_table.setMinimumHeight(120)

        self._populate_media_types_table()

        mt_btn_layout = QtWidgets.QHBoxLayout()
        add_mt_btn = QtWidgets.QPushButton("➕ Add Media Type")
        add_mt_btn.clicked.connect(self._on_add_media_type)
        del_mt_btn = QtWidgets.QPushButton("➖ Remove Selected")
        del_mt_btn.clicked.connect(self._on_remove_media_type)
        mt_btn_layout.addWidget(add_mt_btn)
        mt_btn_layout.addWidget(del_mt_btn)
        mt_btn_layout.addStretch()

        mt_layout.addWidget(self.media_types_table)
        mt_layout.addLayout(mt_btn_layout)
        layout.addWidget(mt_box)

        # Action Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("💾 Save Settings")
        self.save_btn.setStyleSheet("background-color: #059669; font-size: 14px; font-weight: bold;")
        self.save_btn.clicked.connect(self.on_save)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        btn_layout.addWidget(self.save_btn)
        layout.addLayout(btn_layout)

    def _populate_media_types_table(self):
        self.media_types_table.setRowCount(0)
        configs = getattr(self.config, "media_type_configs", {})
        for row_idx, (mtype, tmpl) in enumerate(configs.items()):
            self.media_types_table.insertRow(row_idx)
            item_type = QtWidgets.QTableWidgetItem(mtype)
            item_tmpl = QtWidgets.QTableWidgetItem(tmpl)
            self.media_types_table.setItem(row_idx, 0, item_type)
            self.media_types_table.setItem(row_idx, 1, item_tmpl)

    def _on_add_media_type(self):
        row = self.media_types_table.rowCount()
        self.media_types_table.insertRow(row)
        self.media_types_table.setItem(row, 0, QtWidgets.QTableWidgetItem("NewType"))
        self.media_types_table.setItem(row, 1, QtWidgets.QTableWidgetItem("{nas_root}/{project_code}/shots/{seq}/{shot}/newtype/{name}"))

    def _on_remove_media_type(self):
        selected = self.media_types_table.selectedIndexes()
        rows = {idx.row() for idx in selected}
        for r in sorted(rows, reverse=True):
            self.media_types_table.removeRow(r)

    def on_test_connection(self):
        url = self.kitsu_url_edit.text().strip()
        email = self.kitsu_user_edit.text().strip()
        password = self.kitsu_pass_edit.text().strip()

        self.lbl_conn_result.setText("Connecting to Kitsu...")
        self.lbl_conn_result.setStyleSheet("color: #94A3B8;")

        # Test live connection via KitsuClient in dry_run=False mode
        client = KitsuClient(host=url, email=email, password=password, dry_run=False)
        success = client.connect()

        if success and client.gazu:
            self.lbl_conn_result.setText("✅ Connected Successfully to Kitsu!")
            self.lbl_conn_result.setStyleSheet("color: #10B981; font-weight: bold;")
        else:
            self.lbl_conn_result.setText("❌ Connection Failed. Check URL / Login credentials or server status.")
            self.lbl_conn_result.setStyleSheet("color: #EF4444; font-weight: bold;")

    def on_save(self):
        self.config.kitsu_url = self.kitsu_url_edit.text().strip()
        self.config.kitsu_user = self.kitsu_user_edit.text().strip()
        self.config.kitsu_password = self.kitsu_pass_edit.text().strip()
        self.config.nas_root = self.nas_root_edit.text().strip()
        self.config.cache_root = self.cache_root_edit.text().strip()
        self.config.filename_template = self.filename_tmpl_edit.text().strip()
        self.config.nas_dir_template = self.nas_dir_tmpl_edit.text().strip()

        struct_lines = [
            line.strip() for line in self.shot_struct_edit.toPlainText().splitlines()
            if line.strip()
        ]
        self.config.shot_folder_structure = struct_lines

        configs = {}
        for r in range(self.media_types_table.rowCount()):
            type_item = self.media_types_table.item(r, 0)
            pat_item  = self.media_types_table.item(r, 1)
            if type_item and pat_item and type_item.text().strip():
                configs[type_item.text().strip()] = pat_item.text().strip()
        self.config.media_type_configs = configs

        self.config.save()
        self.config_saved.emit()
        self.accept()
