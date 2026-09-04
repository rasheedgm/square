"""By-example template builder.

Edit a `{token}` pattern (a `dir` or `file` line of a media type / root /
delivery preset) with a live preview rendered against a sample `PathContext`,
and a palette of the tokens you can drop in.
"""

from __future__ import annotations

from Qt import QtWidgets

from tools.qt_compat import TEXT_SELECTABLE_BY_MOUSE, DIALOG_OK, DIALOG_CANCEL, exec_dialog
from square_core.model import PathContext
from square_core.paths import render_tokens, PathError

# a representative context for the preview
_SAMPLE = dict(
    nas_root="X:/projects", project="ABC", episode="EP01", sequence="SQ010",
    shot="SH0100", asset="hero_prop", asset_type="prop", task="comp",
    department="comp", software="nuke", media_type="Plate", name="bg",
    representation="exr", version=3, frame=1001, ext="exr",
    client="ACME", package="v03_review", resolution="3840x2160",
)

_PALETTE = ["project", "episode", "sequence", "shot", "asset", "asset_type",
            "task", "software", "media_type", "name", "representation",
            "version", "frame", "ext", "client", "package", "resolution"]


class TemplateBuilderDialog(QtWidgets.QDialog):
    def __init__(self, pattern: str, *, title: str = "Template", is_dir: bool = True,
                 version_pad: int = 3, frame_pad: int = 4, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {title}")
        self.setMinimumWidth(560)
        self._is_dir = is_dir
        self._vp, self._fp = version_pad, frame_pad

        self.edit = QtWidgets.QLineEdit(pattern)
        self.preview = QtWidgets.QLabel()
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(TEXT_SELECTABLE_BY_MOUSE)
        self.err = QtWidgets.QLabel()
        self.err.setStyleSheet("color:#F87171;")
        self.err.setWordWrap(True)

        palette = QtWidgets.QHBoxLayout()
        palette.setSpacing(4)
        for tok in _PALETTE:
            b = QtWidgets.QPushButton("{%s}" % tok)
            b.setFlat(True)
            b.clicked.connect(lambda _=False, t=tok: self._insert(t))
            palette.addWidget(b)
        palette.addStretch(1)
        pal_wrap = QtWidgets.QWidget()
        pal_wrap.setLayout(palette)
        pal_scroll = QtWidgets.QScrollArea()
        pal_scroll.setWidgetResizable(True)
        pal_scroll.setFixedHeight(44)
        pal_scroll.setWidget(pal_wrap)

        buttons = QtWidgets.QDialogButtonBox(DIALOG_OK | DIALOG_CANCEL)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(DIALOG_OK)

        form = QtWidgets.QFormLayout(self)
        form.addRow("Pattern", self.edit)
        form.addRow("Tokens", pal_scroll)
        form.addRow("Preview", self.preview)
        form.addRow("", self.err)
        form.addRow(buttons)

        self.edit.textChanged.connect(self._refresh)
        self._refresh()

    # ----

    def _insert(self, token: str) -> None:
        self.edit.insert("{%s}" % token)
        self.edit.setFocus()

    def _render(self, pattern: str) -> str:
        ctx = PathContext(**{k: v for k, v in _SAMPLE.items()
                             if k in PathContext.field_names()})
        if self._is_dir:
            ctx = ctx.with_(frame=None)
        return render_tokens(pattern, ctx, version_pad=self._vp, frame_pad=self._fp,
                             optional=("episode", "representation", "frame"))

    def _refresh(self) -> None:
        pattern = self.edit.text().strip()
        try:
            rendered = self._render(pattern)
            self.preview.setText(rendered or "(empty)")
            self.err.setText("")
            self._ok.setEnabled(bool(pattern))
        except PathError as e:
            self.preview.setText("")
            self.err.setText(str(e))
            self._ok.setEnabled(False)

    def _accept(self) -> None:
        try:
            self._render(self.edit.text().strip())
        except PathError:
            return
        self.accept()

    def value(self) -> str:
        return self.edit.text().strip()

    @staticmethod
    def edit_pattern(parent, pattern, **kw) -> str | None:
        dlg = TemplateBuilderDialog(pattern, parent=parent, **kw)
        if exec_dialog(dlg):
            return dlg.value()
        return None
