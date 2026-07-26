from Qt import QtWidgets, QtCore, QtGui
from tools.qt_compat import ITEM_IS_SELECTABLE, ITEM_IS_ENABLED, HEADER_RESIZE_INTERACTIVE, SELECT_ROWS

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
        self.horizontalHeader().setSectionResizeMode(HEADER_RESIZE_INTERACTIVE)
        self.horizontalHeader().setStretchLastSection(True)
        self.setSelectionBehavior(SELECT_ROWS)
        self.setAlternatingRowColors(True)

    def populate_items(self, items):
        """Populates table with IngestSequenceItem list."""
        self.items_data = items
        self.setRowCount(0)

        for row_idx, item in enumerate(items):
            self.insertRow(row_idx)

            # Name (Non-editable)
            name_item = QtWidgets.QTableWidgetItem(item.name)
            name_item.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
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
            range_item.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
            self.setItem(row_idx, self.COL_FRAMES, range_item)

            # FPS (Non-editable)
            fps_item = QtWidgets.QTableWidgetItem(str(item.fps))
            fps_item.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
            self.setItem(row_idx, self.COL_FPS, fps_item)

            # Colorspace (Non-editable)
            cs_item = QtWidgets.QTableWidgetItem(item.colorspace)
            cs_item.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
            self.setItem(row_idx, self.COL_COLORSPACE, cs_item)

            # Status (Non-editable)
            status_str = "Ready" if not item.missing_frames else f"Warning: {len(item.missing_frames)} missing"
            status_item = QtWidgets.QTableWidgetItem(status_str)
            status_item.setFlags(ITEM_IS_SELECTABLE | ITEM_IS_ENABLED)
            if item.missing_frames:
                status_item.setForeground(QtGui.QBrush(QtGui.QColor(239, 68, 68)))
            else:
                status_item.setForeground(QtGui.QBrush(QtGui.QColor(16, 185, 129)))
            self.setItem(row_idx, self.COL_STATUS, status_item)

    populate_table = populate_items

    def get_selected_items(self):
        """Returns the modified list of IngestSequenceItems from table rows."""
        for row in range(self.rowCount()):
            if row < len(self.items_data):
                item = self.items_data[row]
                item.sequence_code = self.item(row, self.COL_SEQ).text().strip()
                item.shot_code = self.item(row, self.COL_SHOT).text().strip()
                item.plate_name = self.item(row, self.COL_PLATE).text().strip()
        return self.items_data
