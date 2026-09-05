"""Shared pytest fixtures for the whole square_core + tools test suite.

The pipeline core itself has no Qt / GUI surface (live-Kitsu integration
checks are opt-in via SQUARE_LIVE_KITSU=1), but the desktop tools
(config_editor, ingest_tool) do, so this still needs to guarantee exactly one
QApplication exists for the whole session, regardless of which test file
happens to run first. Instantiating any QWidget (a QDialog included) without
a live QApplication is a native Qt abort -- not a catchable Python exception
-- so this has to run before any widget gets constructed anywhere in the
suite.
"""

import os

# Headless by default so the suite runs in CI / sandboxed containers with no
# display; leave alone if the environment already picked a platform (e.g. a
# developer running with a real display for visual debugging).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Pin the Qt binding for tests to the one the shipped tools run on. Both
# PySide6 and PyQt6 can be installed at once and Qt.py would pick PySide6
# anyway, but making it explicit means a machine with only PyQt6 can't
# silently pass tests on a binding real users never hit -- which is exactly
# how the id()-keyed-dict signal bug slipped through in the ingest tool
# before this rework.
os.environ.setdefault("QT_PREFERRED_BINDING", "PySide6")

from Qt import QtWidgets  # noqa: E402

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
