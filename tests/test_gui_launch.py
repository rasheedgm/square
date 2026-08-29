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

def log_step(msg):
    print(msg)
    with open("gui_test_step.log", "a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")

def test_full_gui_flow():
    with open("gui_test_step.log", "w", encoding="utf-8") as f:
        f.write("START\n")

    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)

    # 1. Instantiate Main Window
    window = MainWindow()
    log_step("[GUI Test] MainWindow instantiated successfully.")

    # 2. Instantiate Settings Dialog
    settings_dlg = SettingsDialog(window)
    log_step("[GUI Test] SettingsDialog instantiated successfully.")

    # 3. Instantiate and test Create Project Dialog
    proj_dlg = CreateProjectDialog(window.kitsu, window)
    proj_dlg.name_edit.setText("Test Feature Matrix")
    proj_dlg.code_edit.setText("MTX")
    proj_dlg.on_create()
    
    log_step(f"[GUI Test] Created Project Result: {proj_dlg.created_project}")
    assert proj_dlg.created_project is not None
    assert proj_dlg.created_project.get("name") == "Test Feature Matrix"

    # 4. Test Scan folder and table widget population
    sample_dir = root_dir / "test_data" / "incoming_plates"
    log_step(f"[GUI Test] Starting scan folder: {sample_dir}")
    window.on_scan_folder(str(sample_dir))
    log_step(f"[GUI Test] Table populated with {window.table_widget.rowCount()} rows.")
    assert window.table_widget.rowCount() > 0

    # Reload projects into main window
    window.load_projects()
    log_step(f"[GUI Test] Reloaded Projects Count: {window.project_combo.count()}")

    proj_dlg.close()
    settings_dlg.close()
    window.close()
    log_step("[GUI Test] ALL GUI TESTS PASSED 100% CLEANLY!")

if __name__ == "__main__":
    try:
        test_full_gui_flow()
    except Exception as e:
        import traceback
        err_str = traceback.format_exc()
        log_step(f"ERROR:\n{err_str}")
        sys.exit(1)
