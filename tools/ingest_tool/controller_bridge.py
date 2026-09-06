"""
ControllerBridge -- the ONLY seam between the framework-agnostic
IngestController and Qt.

The controller emits ControllerEvents from worker threads (its internal
pool). This forwards each one through ``event = QtCore.Signal(object)``.
That single choice -- an ``object`` signal carrying the whole event, never
a ``dict`` keyed by ``id()`` -- is what fixes the bug where the old NAS
check delivered an empty dict on PySide6 and left every row stuck on
"Checking...". PySide6/Shiboken can't marshal a big-int-keyed dict across
a queued connection; it passes a Python object through untouched.

Batch operations (pre-flight, ingest) run on a QThread so the UI stays
live; synchronous edits (set_field, resolve, skip, undo) are called
straight through on the main thread.
"""

from __future__ import annotations

import logging

from Qt import QtCore

logger = logging.getLogger("IngestControllerBridge")


class _Job(QtCore.QThread):
    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.error = ""

    def run(self):
        try:
            self._fn()
        except Exception as e:   # surfaced via job_finished, never crashes the UI
            self.error = str(e)
            logger.exception("[ControllerBridge] background job failed")


class ControllerBridge(QtCore.QObject):
    event        = QtCore.Signal(object)        # ControllerEvent, marshalled to the main thread
    job_started  = QtCore.Signal(str)           # "preflight" | "ingest"
    job_finished = QtCore.Signal(str, str)      # kind, error ("" on success)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._job: _Job | None = None
        controller.subscribe(self._forward)

    # ------------------------------------------------------------------

    def _forward(self, ev) -> None:
        # Called from arbitrary threads. Signal(object) + queued delivery
        # hops it to whichever thread this QObject lives in (the main one).
        self.event.emit(ev)

    def _run(self, kind: str, fn) -> bool:
        if self.busy:
            return False
        self.job_started.emit(kind)
        job = _Job(fn, self)

        def _done():
            err = job.error
            self._job = None
            job.deleteLater()
            self.job_finished.emit(kind, err)

        job.finished.connect(_done)
        self._job = job
        job.start()
        return True

    # ------------------------------------------------------------------
    # Batch ops (threaded)
    # ------------------------------------------------------------------

    def preflight(self, keys=None) -> bool:
        return self._run("preflight", lambda: self.controller.run_preflight(keys))

    def ingest(self, keys=None, *, dry_run=False) -> bool:
        return self._run("ingest", lambda: self.controller.run_ingest(keys, dry_run=dry_run))

    def cancel(self) -> None:
        self.controller.cancel()

    @property
    def busy(self) -> bool:
        return bool(self._job and self._job.isRunning())

    def wait(self, ms: int = 30000) -> None:
        """For shutdown / tests -- block until any running job ends."""
        if self._job is not None:
            self._job.wait(ms)

    # ------------------------------------------------------------------
    # Synchronous edits (main thread) -- thin passthroughs so the UI has
    # one object to talk to.
    # ------------------------------------------------------------------

    def load(self, scan_items, *, replace=False):
        return self.controller.load(scan_items, replace=replace)

    def set_field(self, key, field_name, value):
        self.controller.set_field(key, field_name, value)

    def set_preview(self, key, wanted):
        self.controller.set_preview(key, wanted)

    def set_convert_to_exr(self, key, wanted):
        self.controller.set_convert_to_exr(key, wanted)

    def skip(self, key):
        self.controller.skip(key)

    def include(self, key):
        self.controller.include(key)

    def resolve(self, key, issue_id, action):
        self.controller.resolve(key, issue_id, action)

    def resolve_many(self, keys, issue_kind, action):
        self.controller.resolve_many(keys, issue_kind, action)

    def rename_batch(self, keys, field_name, template):
        return self.controller.rename_batch(keys, field_name, template)

    def rename_cells(self, cell_targets, template):
        return self.controller.rename_cells(cell_targets, template)

    def resolve_rename_template(self, key, template, attr=None):
        item = self.controller.get(key)
        return self.controller.resolve_rename_template(item, template, attr) if item else template

    def remove(self, key):
        self.controller.remove(key)

    def undo(self):
        return self.controller.undo()

    @property
    def can_undo(self):
        return self.controller.can_undo

    @property
    def undo_label(self):
        return self.controller.undo_label
