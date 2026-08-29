"""
Shared pytest fixtures for the Square VFX ingest tool test suite.

Ensures exactly one QApplication exists for the whole test session,
regardless of which test file runs first or whether a single file runs
in isolation. Instantiating any QWidget (a QDialog included) without a
live QApplication is a native Qt abort -- not a catchable Python
exception -- so this has to run before any widget gets constructed
anywhere in the suite.
"""

import os

# Headless by default so the suite runs in CI / sandboxed containers with no
# display; leave alone if the environment already picked a platform (e.g. a
# developer running with a real display for visual debugging).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Qt import QtWidgets  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
