import os
from Qt import QtWidgets, QtCore, QtGui
from tools.qt_compat import ALIGN_CENTER

class ScannerWidget(QtWidgets.QWidget):
    """Widget for selecting/drag-dropping incoming media folder."""

    scan_requested = QtCore.Signal(str)

    def __init__(self, parent=None):
        super(ScannerWidget, self).__init__(parent)
        self.setAcceptDrops(True)
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Drag and Drop Card
        self.drop_zone = QtWidgets.QFrame()
        self.drop_zone.setObjectName("DropZone")
        
        card_layout = QtWidgets.QVBoxLayout(self.drop_zone)
        card_layout.setAlignment(ALIGN_CENTER)

        self.label_icon = QtWidgets.QLabel("📁")
        self.label_icon.setStyleSheet("font-size: 32px;")
        self.label_icon.setAlignment(ALIGN_CENTER)

        self.label_text = QtWidgets.QLabel("Drag and Drop Incoming Plate Folder Here")
        self.label_text.setStyleSheet("font-size: 15px; font-weight: bold; color: #60A5FA;")
        self.label_text.setAlignment(ALIGN_CENTER)

        self.label_subtext = QtWidgets.QLabel("Supports EXR, DPX, PNG, JPG, MOV, MP4 sequences")
        self.label_subtext.setStyleSheet("font-size: 12px; color: #94A3B8;")
        self.label_subtext.setAlignment(ALIGN_CENTER)

        # Path Selection controls
        browse_layout = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setPlaceholderText("Select folder path...")
        
        self.browse_btn = QtWidgets.QPushButton("Browse...")
        self.browse_btn.clicked.connect(self.on_browse)

        self.scan_btn = QtWidgets.QPushButton("Scan Folder")
        self.scan_btn.setStyleSheet("background-color: #059669; font-weight: bold;")
        self.scan_btn.clicked.connect(self.on_scan)

        browse_layout.addWidget(self.path_edit)
        browse_layout.addWidget(self.browse_btn)
        browse_layout.addWidget(self.scan_btn)

        card_layout.addWidget(self.label_icon)
        card_layout.addWidget(self.label_text)
        card_layout.addWidget(self.label_subtext)
        card_layout.addSpacing(10)
        card_layout.addLayout(browse_layout)

        layout.addWidget(self.drop_zone)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            folder_path = urls[0].toLocalFile()
            if os.path.isdir(folder_path):
                self.path_edit.setText(folder_path)
                self.scan_requested.emit(folder_path)

    def on_browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Incoming Media Folder")
        if folder:
            self.path_edit.setText(folder)
            self.scan_requested.emit(folder)

    def on_scan(self):
        folder = self.path_edit.text().strip()
        if folder and os.path.exists(folder):
            self.scan_requested.emit(folder)
