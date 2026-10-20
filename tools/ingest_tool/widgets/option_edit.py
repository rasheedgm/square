"""OptionEdit -- a line-edit-shaped combo box: the value is still text, but the
config's options are one click away.

It quacks like the QLineEdit it replaces (`text()`, `setText()`, `textChanged`,
`setPlaceholderText()`) so the dialogs that used a plain edit for a field like
Media Type or Colorspace need only swap the constructor. `editable=True` keeps
free typing (a delivery's own resolution isn't in any list); `editable=False`
is a strict pick-list.
"""

from __future__ import annotations

from Qt import QtCore, QtWidgets


class OptionEdit(QtWidgets.QComboBox):
    textChanged = QtCore.Signal(str)
    committed = QtCore.Signal(str)        # a pick, or typing finished

    def __init__(self, options=(), text: str = "", *, editable: bool = True, parent=None):
        super().__init__(parent)
        self._strict = not editable
        self.setEditable(editable)
        if editable:
            self.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.addItem("")                    # blank = "no value", so a field can be cleared
        self.addItems([str(o) for o in options if str(o)])
        self.setText(text)
        self.currentTextChanged.connect(self.textChanged)
        self.activated.connect(lambda _i: self.committed.emit(self.text()))
        if editable and self.lineEdit() is not None:
            self.lineEdit().editingFinished.connect(lambda: self.committed.emit(self.text()))

    def text(self) -> str:
        return self.currentText()

    def setText(self, text: str) -> None:
        text = "" if text is None else str(text)
        idx = self.findText(text)
        if idx < 0 and self._strict and text:
            self.addItem(text)                  # a value already on the row is always shown
            idx = self.count() - 1
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(text)

    def setPlaceholderText(self, text: str) -> None:
        if self.lineEdit() is not None:
            self.lineEdit().setPlaceholderText(text)
