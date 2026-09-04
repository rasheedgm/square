"""tools.pipeline_deploy.deploy.write_launchers -- the generated .bat files must
not detach the console (or a startup crash is invisible) and must pause on a
non-zero exit so the user can read it."""

import tempfile
import unittest
from pathlib import Path

from tools.pipeline_deploy.deploy import write_launchers


class TestLauncherBat(unittest.TestCase):
    def _generate(self):
        with tempfile.TemporaryDirectory() as td:
            release = Path(td) / "release"
            (release / "tools" / "config_editor").mkdir(parents=True)
            (release / "tools" / "config_editor" / "main.py").write_text("", encoding="utf-8")
            launchers = Path(td) / "launchers"
            write_launchers(launchers, release)
            return (launchers / "square_config_editor.bat").read_text(encoding="utf-8")

    def test_not_detached(self):
        bat = self._generate()
        self.assertNotIn("start \"\"", bat)

    def test_pauses_on_error(self):
        bat = self._generate()
        self.assertIn("if errorlevel 1", bat)
        self.assertIn("pause", bat)

    def test_runs_the_deployed_entry_point(self):
        bat = self._generate()
        self.assertIn(r"%PIPELINE_ROOT%\current\tools\config_editor\main.py", bat)

    def test_rollback_launcher_always_written(self):
        with tempfile.TemporaryDirectory() as td:
            release = Path(td) / "release"          # no tools/ at all
            launchers = Path(td) / "launchers"
            write_launchers(launchers, release)
            self.assertTrue((launchers / "square_rollback.bat").exists())

    def test_rollback_launcher_runs_by_path_not_dash_m(self):
        # `-m tools.pipeline_deploy.rollback_cli` needs `tools` importable
        # from the cwd/PYTHONPATH, which a double-clicked .bat never sets up
        # -- this is exactly what broke ("No module named 'tools'")
        with tempfile.TemporaryDirectory() as td:
            launchers = Path(td) / "launchers"
            write_launchers(launchers, Path(td) / "release")
            bat = (launchers / "square_rollback.bat").read_text(encoding="utf-8")
            invoke_lines = [ln for ln in bat.splitlines() if ln.startswith('"%PYTHON_EXE%"')]
            self.assertEqual(len(invoke_lines), 1)
            self.assertNotIn("-m tools", invoke_lines[0])
            self.assertIn(r"%PIPELINE_ROOT%\current\tools\pipeline_deploy\rollback_cli.py",
                          invoke_lines[0])


if __name__ == "__main__":
    unittest.main()
