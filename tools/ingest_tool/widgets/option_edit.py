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


class _OpenListOnClick(QtCore.QObject):
    """Clicking into an editable combo's text part opens its list -- when the
    field is empty or isn't focused yet. Once it has focus AND holds text, a
    click just places the caret, so a custom value can still be edited."""

    def __init__(self, combo):
        super().__init__(combo)
        self._combo = combo

    def eventFilter(self, obj, ev):
        if (ev.type() == QtCore.QEvent.Type.MouseButtonPress
                and (not obj.hasFocus() or not obj.text()) and self._combo.count()):
            QtCore.QTimer.singleShot(0, self._combo.showPopup)
        return False


def make_choosable(combo: QtWidgets.QComboBox) -> None:
    """Make an EDITABLE combo behave like a dropdown you can also type into:
    the field opens the list when first clicked, and what is typed filters the
    choices in a popup (anywhere in the text, any case) instead of silently
    completing inline."""
    if not combo.isEditable() or combo.lineEdit() is None:
        return
    combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
    completer = combo.completer()
    if completer is not None:
        completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
    opener = _OpenListOnClick(combo)
    combo.lineEdit().installEventFilter(opener)
    combo._open_list_on_click = opener          # keep a reference


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
        make_choosable(self)
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
