import sys
from pathlib import Path

# Add root directory to sys.path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from Qt import QtWidgets
from tools.ingest_tool.ui_main import MainWindow
from tools.ingest_tool.widgets.settings_dialog import SettingsDialog

def test_gui_instantiation():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)

    window = MainWindow()
    print("[GUI Test] MainWindow instantiated successfully with Qt.py!")

    settings_dlg = SettingsDialog(window)
    print("[GUI Test] SettingsDialog instantiated successfully with Qt.py!")
    
    assert window is not None
    assert settings_dlg is not None
    settings_dlg.close()
    window.close()

if __name__ == "__main__":
    test_gui_instantiation()
