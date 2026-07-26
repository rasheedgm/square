from Qt import QtWidgets, QtCore, QtGui

class IngestTableWidget(QtWidgets.QTableWidget):
    """Table widget for reviewing and editing discovered plate sequences."""

    COL_NAME = 0
    COL_SEQ = 1
    COL_SHOT = 2
    COL_PLATE = 3
    COL_FRAMES = 4
    COL_FPS = 5
    COL_COLORSPACE = 6
    COL_STATUS = 7

    HEADERS = [
        "Base Name / File", "Sequence", "Shot Code", "Plate Name",
        "Frame Range", "FPS", "Colorspace", "Status"
    ]

    def __init__(self, parent=None):
        super(IngestTableWidget, self).__init__(parent)
        self.items_data = []
        self.setup_ui()

    def setup_ui(self):
        self.setColumnCount(len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)

    def populate_items(self, items):
        """Populates table with IngestSequenceItem list."""
        self.items_data = items
        self.setRowCount(0)

        for row_idx, item in enumerate(items):
            self.insertRow(row_idx)

            # Name (Non-editable)
            name_item = QtWidgets.QTableWidgetItem(item.name)
            name_item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            self.setItem(row_idx, self.COL_NAME, name_item)

            # Sequence (Editable)
            seq_item = QtWidgets.QTableWidgetItem(item.sequence_code)
            self.setItem(row_idx, self.COL_SEQ, seq_item)

            # Shot Code (Editable)
            shot_item = QtWidgets.QTableWidgetItem(item.shot_code)
            self.setItem(row_idx, self.COL_SHOT, shot_item)

            # Plate Name (Editable)
            plate_item = QtWidgets.QTableWidgetItem(item.plate_name)
            self.setItem(row_idx, self.COL_PLATE, plate_item)

            # Frame Range (Non-editable)
            range_item = QtWidgets.QTableWidgetItem(item.frame_range_str)
            range_item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            self.setItem(row_idx, self.COL_FRAMES, range_item)

            # FPS (Combo/Spin)
            fps_item = QtWidgets.QTableWidgetItem(str(item.fps))
            self.setItem(row_idx, self.COL_FPS, fps_item)

            # Colorspace (Combo)
            cs_item = QtWidgets.QTableWidgetItem(item.colorspace)
            self.setItem(row_idx, self.COL_COLORSPACE, cs_item)

            # Status Badge
            if item.has_warnings:
                status_text = f"⚠️ Missing {len(item.missing_frames)} frames"
                status_item = QtWidgets.QTableWidgetItem(status_text)
                status_item.setForeground(QtGui.QColor("#F59E0B"))
            else:
                status_item = QtWidgets.QTableWidgetItem("Ready")
                status_item.setForeground(QtGui.QColor("#10B981"))

            status_item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
            self.setItem(row_idx, self.COL_STATUS, status_item)

        self.resizeColumnsToContents()

    def get_updated_items(self):
        """Reads modified table entries back into the items list."""
        for row in range(self.rowCount()):
            if row < len(self.items_data):
                item = self.items_data[row]
                item.sequence_code = self.item(row, self.COL_SEQ).text().strip().upper()
                item.shot_code = self.item(row, self.COL_SHOT).text().strip().upper()
                item.plate_name = self.item(row, self.COL_PLATE).text().strip().upper()
                try:
                    item.fps = float(self.item(row, self.COL_FPS).text().strip())
                except ValueError:
                    pass
                item.colorspace = self.item(row, self.COL_COLORSPACE).text().strip()
        return self.items_data
