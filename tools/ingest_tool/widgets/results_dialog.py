import os
from pathlib import Path
from Qt import QtWidgets, QtCore, QtGui
from tools.qt_compat import FONT_BOLD, ALIGN_CENTER

class DryRunResultsDialog(QtWidgets.QDialog):
    """
    Modal window presenting the complete execution summary after a Dry-Run or Live Ingest finishes.
    Displays target NAS destination paths, sample file names, resolutions, and file counts.
    """

    def __init__(self, summary: dict, parent=None):
        super().__init__(parent)
        self.summary = summary or {}
        is_dry_run = self.summary.get("is_dry_run", True)
        title = "Dry-Run Simulation Results" if is_dry_run else "Ingestion Results Summary"
        self.setWindowTitle(f"Square VFX — {title}")
        self.resize(940, 540)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        is_dry_run = self.summary.get("is_dry_run", True)
        proj_code = self.summary.get("project_code", "PROJ")
        total_items = self.summary.get("total_items", 0)
        total_files = self.summary.get("total_files", 0)
        items = self.summary.get("items", [])

        # ── Banner Header ──
        banner_frame = QtWidgets.QFrame()
        banner_bg = "#2C1515" if is_dry_run else "#064E3B"
        banner_border = "#EF4444" if is_dry_run else "#10B981"
        banner_frame.setStyleSheet(
            f"QFrame {{ background:{banner_bg}; border:1px solid {banner_border}; border-radius:6px; padding:10px; }}"
        )
        b_layout = QtWidgets.QVBoxLayout(banner_frame)
        b_layout.setSpacing(4)

        icon = "🧪" if is_dry_run else "🚀"
        mode_str = "DRY-RUN SIMULATION (No files overwritten)" if is_dry_run else "LIVE INGEST COMPLETED"
        lbl_title = QtWidgets.QLabel(f"{icon}  {mode_str}")
        lbl_title.setStyleSheet("font-size:15px; font-weight:bold; color:white;")
        b_layout.addWidget(lbl_title)

        transfer_mode = self.summary.get("transfer_mode", "copy")
        task_types = self.summary.get("task_types", [])
        task_str = ", ".join(task_types) if task_types else "(none configured)"
        lbl_sub = QtWidgets.QLabel(
            f"Project: {proj_code}   ·   Items: {total_items}   ·   Files: {total_files} frames   ·   "
            f"Transfer: {transfer_mode}   ·   Tasks: {task_str}"
        )
        lbl_sub.setStyleSheet("font-size:12px; color:#CBD5E1;")
        b_layout.addWidget(lbl_sub)

        layout.addWidget(banner_frame)

        # ── Summary Table ──
        table = QtWidgets.QTableWidget()
        table.setColumnCount(9)
        table.setHorizontalHeaderLabels([
            "Source Item",
            "Seq / Shot",
            "Type",
            "Plate",
            "Ver",
            "Res",
            "Target Destination Directory",
            "Sample Target File",
            "Status"
        ])
        table.setStyleSheet("""
            QTableWidget {
                background-color: #131720;
                border: 1px solid #252D3D;
                gridline-color: #1E2535;
                color: #E2E8F0;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #1A2035;
                color: #94A3B8;
                font-weight: bold;
                border: none;
                padding: 6px;
            }
        """)

        table.setRowCount(len(items))
        for row_idx, item in enumerate(items):
            src_name  = item.get("source_name", "")
            seq_shot  = f"{item.get('sequence_code')} / {item.get('shot_code')}"
            mtype     = item.get("media_type", "Plate")
            plate_n   = item.get("plate_name", "")
            ver_str   = f"v{item.get('version', 1):03d}"
            res_str   = item.get("resolution", "")
            dest_dir  = item.get("dest_dir", "")
            sample_fn = os.path.basename(item.get("sample_dest_file", ""))
            full_fn   = item.get("sample_dest_file", "")

            table.setItem(row_idx, 0, QtWidgets.QTableWidgetItem(src_name))
            table.setItem(row_idx, 1, QtWidgets.QTableWidgetItem(seq_shot))
            table.setItem(row_idx, 2, QtWidgets.QTableWidgetItem(mtype))
            table.setItem(row_idx, 3, QtWidgets.QTableWidgetItem(plate_n))
            table.setItem(row_idx, 4, QtWidgets.QTableWidgetItem(ver_str))
            table.setItem(row_idx, 5, QtWidgets.QTableWidgetItem(res_str))

            dir_item = QtWidgets.QTableWidgetItem(dest_dir)
            dir_item.setToolTip(dest_dir)
            dir_item.setForeground(QtGui.QColor("#38BDF8"))
            table.setItem(row_idx, 6, dir_item)

            file_item = QtWidgets.QTableWidgetItem(sample_fn)
            file_item.setToolTip(full_fn)
            table.setItem(row_idx, 7, file_item)

            status = item.get("status", "")
            status_item = QtWidgets.QTableWidgetItem(status)
            if status.startswith("Error"):
                status_item.setForeground(QtGui.QColor("#EF4444"))
                status_item.setToolTip(status)
            else:
                status_item.setForeground(QtGui.QColor("#10B981"))
            table.setItem(row_idx, 8, status_item)

        table.setColumnWidth(0, 150)
        table.setColumnWidth(1, 110)
        table.setColumnWidth(2, 65)
        table.setColumnWidth(3, 65)
        table.setColumnWidth(4, 50)
        table.setColumnWidth(5, 80)
        table.setColumnWidth(6, 260)
        table.setColumnWidth(7, 180)
        table.setColumnWidth(8, 140)

        layout.addWidget(table, stretch=1)

        # ── Action Buttons ──
        btn_row = QtWidgets.QHBoxLayout()
        btn_open = QtWidgets.QPushButton("📁 Open Destination Folder")
        btn_open.setToolTip("Open the NAS destination root in File Explorer")
        btn_open.setFixedHeight(32)
        btn_open.clicked.connect(self._on_open_folder)

        btn_close = QtWidgets.QPushButton("Close")
        btn_close.setFixedHeight(32)
        btn_close.clicked.connect(self.accept)

        btn_row.addWidget(btn_open)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _on_open_folder(self):
        items = self.summary.get("items", [])
        if items:
            folder = items[0].get("dest_dir", "")
            if folder and os.path.exists(folder):
                os.startfile(folder)
            elif folder:
                parent = str(Path(folder).parent)
                if os.path.exists(parent):
                    os.startfile(parent)
                else:
                    QtWidgets.QMessageBox.information(
                        self, "Dry-Run Destination Path",
                        f"Simulated target path:\n\n{folder}"
                    )
