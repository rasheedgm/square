"""ConfigKey -> an editor widget.

`make_field_editor(fv)` returns a `QWidget` exposing:
  - `get_value()`            -> the edited value (native type)
  - `signal_changed`        -> a Qt signal, emitted on any edit

Scalar kinds render inline; `list` gets a line-per-item box; `dict` and the
structured kinds (`root`, `media_type_registry`, `delivery_registry`) open a
dedicated sub-editor.
"""

from __future__ import annotations

import json

from Qt import QtCore, QtWidgets

from tools.qt_compat import exec_dialog

_BIGNUM = 10 ** 9


class _Base(QtWidgets.QWidget):
    signal_changed = QtCore.Signal()

    def __init__(self, fv, parent=None):
        super().__init__(parent)
        self.fv = fv
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lay = lay

    def get_value(self):
        raise NotImplementedError


class _StrField(_Base):
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self.edit = QtWidgets.QLineEdit("" if fv.value is None else str(fv.value))
        if fv.secret:
            from tools.qt_compat import ECHO_MODE_PASSWORD
            self.edit.setEchoMode(ECHO_MODE_PASSWORD)
        self.edit.textChanged.connect(self.signal_changed)
        self._lay.addWidget(self.edit)
        if fv.kind == "path":
            b = QtWidgets.QPushButton("...")
            b.setFixedWidth(30)
            b.clicked.connect(self._browse)
            self._lay.addWidget(b)

    def _browse(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose", self.edit.text())
        if p:
            self.edit.setText(p)

    def get_value(self):
        return self.edit.text()


class _IntField(_Base):
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self.spin = QtWidgets.QSpinBox()
        self.spin.setRange(int(fv.minimum) if fv.minimum is not None else -_BIGNUM,
                           int(fv.maximum) if fv.maximum is not None else _BIGNUM)
        self.spin.setValue(int(fv.value or 0))
        self.spin.valueChanged.connect(self.signal_changed)
        self._lay.addWidget(self.spin)
        self._lay.addStretch(1)

    def get_value(self):
        return int(self.spin.value())


class _FloatField(_Base):
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setDecimals(3)
        self.spin.setRange(float(fv.minimum) if fv.minimum is not None else -_BIGNUM,
                           float(fv.maximum) if fv.maximum is not None else _BIGNUM)
        self.spin.setValue(float(fv.value or 0.0))
        self.spin.valueChanged.connect(self.signal_changed)
        self._lay.addWidget(self.spin)
        self._lay.addStretch(1)

    def get_value(self):
        return float(self.spin.value())


class _BoolField(_Base):
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self.box = QtWidgets.QCheckBox()
        self.box.setChecked(bool(fv.value))
        self.box.toggled.connect(self.signal_changed)
        self._lay.addWidget(self.box)
        self._lay.addStretch(1)

    def get_value(self):
        return bool(self.box.isChecked())


class _EnumField(_Base):
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self.combo = QtWidgets.QComboBox()
        self.combo.addItems([str(c) for c in fv.choices])
        if fv.value in fv.choices:
            self.combo.setCurrentIndex(list(fv.choices).index(fv.value))
        self.combo.currentIndexChanged.connect(self.signal_changed)
        self._lay.addWidget(self.combo)
        self._lay.addStretch(1)

    def get_value(self):
        return self.fv.choices[self.combo.currentIndex()]


class _ListField(_Base):
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self.text = QtWidgets.QPlainTextEdit("\n".join(str(x) for x in (fv.value or [])))
        self.text.setPlaceholderText("one item per line")
        self.text.setMinimumHeight(120)
        self.text.textChanged.connect(self.signal_changed)
        self._lay.addWidget(self.text)

    def get_value(self):
        items = [ln.strip() for ln in self.text.toPlainText().splitlines() if ln.strip()]
        ik = self.fv.item_kind
        if ik == "int":
            return [int(x) for x in items]
        if ik == "float":
            return [float(x) for x in items]
        if ik == "bool":
            return [x.lower() in ("1", "true", "yes") for x in items]
        return items


class _JsonField(_Base):
    """Generic dict fallback: an 'Edit JSON...' button + a compact preview."""
    def __init__(self, fv, parent=None):
        super().__init__(fv, parent)
        self._value = json.loads(json.dumps(fv.value)) if fv.value is not None else {}
        self.label = QtWidgets.QLabel()
        self.label.setStyleSheet("color:#94A3B8;")
        b = QtWidgets.QPushButton("Edit JSON...")
        b.clicked.connect(self._edit)
        self._lay.addWidget(self.label, 1)
        self._lay.addWidget(b)
        self._sync()

    def _sync(self):
        s = json.dumps(self._value)
        self.label.setText(s if len(s) < 80 else s[:77] + "...")

    def _edit(self):
        dlg = _JsonDialog(self._value, self)
        if exec_dialog(dlg):
            self._value = dlg.value()
            self._sync()
            self.signal_changed.emit()

    def get_value(self):
        return self._value


class _JsonDialog(QtWidgets.QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit JSON")
        self.setMinimumSize(480, 360)
        self.text = QtWidgets.QPlainTextEdit(json.dumps(value, indent=2))
        self.err = QtWidgets.QLabel()
        self.err.setStyleSheet("color:#F87171;")
        from tools.qt_compat import DIALOG_OK, DIALOG_CANCEL
        bb = QtWidgets.QDialogButtonBox(DIALOG_OK | DIALOG_CANCEL)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.text)
        lay.addWidget(self.err)
        lay.addWidget(bb)
        self._parsed = value

    def _ok(self):
        try:
            self._parsed = json.loads(self.text.toPlainText())
        except json.JSONDecodeError as e:
            self.err.setText(str(e))
            return
        self.accept()

    def value(self):
        return self._parsed


_SCALAR = {"str": _StrField, "path": _StrField, "int": _IntField,
           "float": _FloatField, "bool": _BoolField, "enum": _EnumField}


def make_field_editor(fv, parent=None):
    if fv.kind in _SCALAR:
        return _SCALAR[fv.kind](fv, parent)
    if fv.kind == "list":
        return _ListField(fv, parent)
    if fv.kind in ("root", "media_type_registry", "delivery_registry"):
        from .registries import RegistryEditor
        return RegistryEditor(fv, parent)
    return _JsonField(fv, parent)     # dict, template, anything else
