import sys
from pathlib import Path

# Add root directory to sys.path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from Qt import QtWidgets
from tools.ingest_tool.ui_main import MainWindow, CreateProjectDialog
from tools.ingest_tool.widgets.settings_dialog import SettingsDialog
from tools.qt_compat import DIALOG_ACCEPTED

def test_full_gui_flow():
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)

    # 1. Instantiate Main Window
    window = MainWindow()
    print("[GUI Test] MainWindow instantiated successfully.")

    # 2. Instantiate Settings Dialog
    settings_dlg = SettingsDialog(window)
    print("[GUI Test] SettingsDialog instantiated successfully.")

    # 3. Instantiate and test Create Project Dialog
    proj_dlg = CreateProjectDialog(window.kitsu, window)
    proj_dlg.name_edit.setText("Test Feature Matrix")
    proj_dlg.code_edit.setText("MTX")
    proj_dlg.on_create()
    
    print(f"[GUI Test] Created Project Result: {proj_dlg.created_project}")
    assert proj_dlg.created_project is not None
    assert proj_dlg.created_project.get("name") == "Test Feature Matrix"

    # 4. Test Scan folder and table widget population
    sample_dir = root_dir / "test_data" / "incoming_plates"
    window.on_scan_folder(str(sample_dir))
    print(f"[GUI Test] Table populated with {window.table_widget.rowCount()} rows.")
    assert window.table_widget.rowCount() > 0

    # Reload projects into main window
    window.load_projects()
    print(f"[GUI Test] Reloaded Projects Count: {window.project_combo.count()}")

    proj_dlg.close()
    settings_dlg.close()
    window.close()
    print("[GUI Test] ALL GUI TESTS PASSED 100% CLEANLY!")

if __name__ == "__main__":
    test_full_gui_flow()
