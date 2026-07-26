from Qt import QtWidgets, QtCore, QtGui
from square_core.config import StudioConfig
from square_core.kitsu_client import KitsuClient

class SettingsDialog(QtWidgets.QDialog):
    """Settings modal for configuring Kitsu credentials and NAS storage paths."""

    config_saved = QtCore.Signal()

    def __init__(self, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.setWindowTitle("Square VFX - Studio Pipeline Configuration")
        self.setMinimumSize(520, 420)
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
        self.kitsu_pass_edit.setEchoMode(QtWidgets.QLineEdit.Password)
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
        
        self.config.save()
        self.config_saved.emit()
        self.accept()
