"""BusyProgress -- a small, modal progress dialog for work that runs on the UI
thread (building the folder tree, matching patterns, scanning a root on Load).

Those loops used to run with no sign of life, so a big delivery on a network
share looked like a frozen tool. This shows what is being done and how far it
has got, and keeps the window painting by pumping the event loop (throttled).
It only appears when the work takes longer than `min_ms`, so quick operations
never flash a dialog.
"""

from __future__ import annotations

import time

from Qt import QtCore, QtWidgets


class BusyProgress:
    def __init__(self, parent, title: str, label: str = "Working…", *,
                 cancellable: bool = False, min_ms: int = 250):
        self._dlg = QtWidgets.QProgressDialog(label, "Cancel" if cancellable else None,
                                              0, 0, parent)
        self._dlg.setWindowTitle(title)
        self._dlg.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self._dlg.setMinimumDuration(min_ms)
        self._dlg.setAutoClose(False)
        self._dlg.setAutoReset(False)
        self._dlg.setMinimumWidth(380)
        if not cancellable:
            self._dlg.setCancelButton(None)
        self._cancelled = False
        if cancellable:
            self._dlg.canceled.connect(self._on_cancel)
        self._last_pump = 0.0
        self._dlg.setValue(0)

    def _on_cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def update(self, label: str | None = None, done: int | None = None,
               total: int | None = None) -> None:
        """`total` > 0 gives a real bar ("label  12/480"); without it the bar
        just runs ("label  (12)") -- the count of what's been seen so far."""
        if label is not None:
            if total:
                text = f"{label}   {done or 0} / {total}"
            elif done:
                text = f"{label}   ({done})"
            else:
                text = label
            self._dlg.setLabelText(text)
        if total:
            if self._dlg.maximum() != total:
                self._dlg.setMaximum(total)
            self._dlg.setValue(min(done or 0, total))
        elif self._dlg.maximum() != 0:
            self._dlg.setMaximum(0)
        self.pump()

    def pump(self) -> None:
        now = time.monotonic()
        if now - self._last_pump >= 0.04:
            self._last_pump = now
            QtWidgets.QApplication.processEvents()

    def close(self) -> None:
        try:
            self._dlg.reset()
            self._dlg.close()
            self._dlg.deleteLater()
        except RuntimeError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
