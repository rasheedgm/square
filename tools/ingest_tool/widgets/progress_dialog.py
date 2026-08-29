from Qt import QtWidgets, QtCore, QtGui

class IngestProgressDialog(QtWidgets.QDialog):
    """Progress modal for ingestion execution."""

    def __init__(self, parent=None):
        super(IngestProgressDialog, self).__init__(parent)
        self.setWindowTitle("Square Pipeline - Ingestion Progress")
        self.setMinimumSize(600, 400)
        self.setup_ui()

    def setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        self.label_status = QtWidgets.QLabel("Initializing Ingestion Pipeline...")
        self.label_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #60A5FA;")

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)

        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)

        layout.addWidget(self.label_status)
        layout.addWidget(self.progress_bar)
        layout.addWidget(QtWidgets.QLabel("Execution Log:"))
        layout.addWidget(self.log_text)
        layout.addWidget(self.close_btn)

    def log(self, message):
        """Appends log message to text area."""
        self.log_text.append(message)
        # Scroll to bottom
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def update_progress(self, percent, message):
        """Updates progress bar and status text."""
        self.progress_bar.setValue(percent)
        self.label_status.setText(message)
        self.log(f"[{percent}%] {message}")

    def finish(self, success=True, message="Ingestion Completed!"):
        self.progress_bar.setValue(100)
        self.label_status.setText(message)
        if success:
            self.label_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #10B981;")
        else:
            self.label_status.setStyleSheet("font-size: 14px; font-weight: bold; color: #EF4444;")
        self.close_btn.setEnabled(True)
