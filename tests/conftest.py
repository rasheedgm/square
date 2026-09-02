"""Test config for square_core.

The pipeline core has no Qt / GUI surface, so nothing here needs a QApplication.
Live-Kitsu integration checks are opt-in via SQUARE_LIVE_KITSU=1.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
