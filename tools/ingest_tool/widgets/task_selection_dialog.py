"""
TaskSelectionDialog — pre-ingest step: choose which Kitsu task types get
created for every shot in this batch. Previously this was entirely
decorative (a checkbox list existed on the "New Project" dialog only, and
its selection was never read back or passed anywhere); the real ingest
worker always created the same hardcoded six tasks regardless of any UI.
"""

from Qt import QtWidgets, QtCore

from tools.qt_compat import DIALOG_ACCEPTED


class TaskSelectionDialog(QtWidgets.QDialog):
    """Lets the user pick task types for this ingest batch before it runs."""

    def __init__(self, default_tasks, kitsu_client=None, parent=None):
        super(TaskSelectionDialog, self).__init__(parent)
        self.setWindowTitle("Select Tasks for This Ingest")
        self.setMinimumWidth(380)
        self._checkboxes = {}
        self._build_ui(default_tasks, kitsu_client)

    def _build_ui(self, default_tasks, kitsu_client):
        layout = QtWidgets.QVBoxLayout(self)

        hdr = QtWidgets.QLabel("Create these Kitsu task types for every shot in this batch:")
        hdr.setWordWrap(True)
        layout.addWidget(hdr)

        # Union of the studio's configured defaults and any task types that already exist
        # live on the Kitsu server, so studio-specific types beyond the defaults show up too.
        all_names = list(dict.fromkeys(default_tasks))
        if kitsu_client and kitsu_client.gazu and kitsu_client.is_connected:
            try:
                for tt in kitsu_client.gazu.task.all_task_types() or []:
                    if tt.get("for_entity", "Shot") == "Shot" and tt["name"] not in all_names:
                        all_names.append(tt["name"])
            except Exception:
                pass

        box = QtWidgets.QGroupBox("Task Types")
        box_layout = QtWidgets.QVBoxLayout(box)
        default_set = set(default_tasks)
        for name in all_names:
            cb = QtWidgets.QCheckBox(name)
            cb.setChecked(name in default_set)
            self._checkboxes[name] = cb
            box_layout.addWidget(cb)
        layout.addWidget(box)

        custom_row = QtWidgets.QHBoxLayout()
        self.custom_edit = QtWidgets.QLineEdit()
        self.custom_edit.setPlaceholderText("Add a custom task type...")
        add_btn = QtWidgets.QPushButton("+ Add")
        add_btn.clicked.connect(self._on_add_custom)
        custom_row.addWidget(self.custom_edit)
        custom_row.addWidget(add_btn)
        layout.addLayout(custom_row)

        btn_row = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self._set_all(True))
        select_none_btn = QtWidgets.QPushButton("Select None")
        select_none_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(select_all_btn)
        btn_row.addWidget(select_none_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        dlg_btns = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("Start Ingest")
        ok_btn.setStyleSheet("background-color:#059669; color:white; font-weight:bold; padding:6px 14px;")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        dlg_btns.addStretch()
        dlg_btns.addWidget(cancel_btn)
        dlg_btns.addWidget(ok_btn)
        layout.addLayout(dlg_btns)

        self._box_layout = box_layout

    def _set_all(self, checked):
        for cb in self._checkboxes.values():
            cb.setChecked(checked)

    def _on_add_custom(self):
        name = self.custom_edit.text().strip()
        if not name or name in self._checkboxes:
            return
        cb = QtWidgets.QCheckBox(name)
        cb.setChecked(True)
        self._checkboxes[name] = cb
        self._box_layout.addWidget(cb)
        self.custom_edit.clear()

    def get_selected_tasks(self):
        return [name for name, cb in self._checkboxes.items() if cb.isChecked()]
