"""tools.pipeline_deploy.deploy.write_launchers -- the generated .bat files must
not detach the console (or a startup crash is invisible) and must pause on a
non-zero exit so the user can read it."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pipeline_deploy.deploy import build_dcc_deps, write_launchers


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

    def test_dcc_launchers_written_when_the_integration_ships(self):
        with tempfile.TemporaryDirectory() as td:
            release = Path(td) / "release"
            (release / "tools" / "dcc" / "xstudio").mkdir(parents=True)
            (release / "tools" / "dcc" / "nuke").mkdir(parents=True)
            launchers = Path(td) / "launchers"
            write_launchers(launchers, release)
            for dcc in ("xstudio", "nuke"):
                bat = (launchers / f"square_{dcc}.bat").read_text(encoding="utf-8")
                self.assertIn(r"tools\pipeline_deploy\dcc_launch.py", bat)
                self.assertIn(f'dcc_launch.py" {dcc} ', bat)
                self.assertIn("if errorlevel 1", bat)

    def test_no_dcc_launcher_without_the_integration(self):
        with tempfile.TemporaryDirectory() as td:
            release = Path(td) / "release"
            (release / "tools" / "config_editor").mkdir(parents=True)
            (release / "tools" / "config_editor" / "main.py").write_text("", encoding="utf-8")
            launchers = Path(td) / "launchers"
            write_launchers(launchers, release)
            self.assertFalse((launchers / "square_xstudio.bat").exists())


class TestBuildDccDeps(unittest.TestCase):
    """Regression coverage for a real crash: gazu declares a hard dependency
    on pywin32 (for its unused events.py live-notification client), which
    has no pure-Python wheel at all -- resolving gazu's full tree under
    --only-binary=:all: fails outright unless --no-deps is also passed, so
    requirements-dcc.txt is the one place the actual runtime dependency list
    lives. Missing --no-deps here would have made every deploy fail (or,
    before --only-binary was added, silently install whatever wheel matched
    the DEPLOYING machine's Python -- unrelated to the embedded DCC
    interpreter's own ABI -- which is exactly how a urllib3 release using
    Python 3.10+-only syntax ended up inside Nuke 14's Python 3.9 and
    crashed on the very first import)."""

    def test_install_command_is_no_deps_and_pure_python_only(self):
        with tempfile.TemporaryDirectory() as td:
            envs_dir = Path(td) / "envs"
            envs_dir.mkdir()
            with patch("tools.pipeline_deploy.deploy.subprocess.run") as run:
                build_dcc_deps(envs_dir, rebuild=True)
            self.assertTrue(run.called)
            cmd = run.call_args[0][0]
            self.assertIn("--no-deps", cmd)
            self.assertIn("--only-binary=:all:", cmd)
            for pair in (("--platform", "any"), ("--implementation", "py"), ("--abi", "none")):
                self.assertIn(pair[0], cmd)
                self.assertEqual(cmd[cmd.index(pair[0]) + 1], pair[1])
            self.assertIn("--target", cmd)
            self.assertEqual(cmd[cmd.index("--target") + 1], str(envs_dir / "dcc-deps"))

    def test_skips_the_build_when_the_folder_exists_and_not_rebuilding(self):
        with tempfile.TemporaryDirectory() as td:
            envs_dir = Path(td) / "envs"
            (envs_dir / "dcc-deps").mkdir(parents=True)
            with patch("tools.pipeline_deploy.deploy.subprocess.run") as run:
                build_dcc_deps(envs_dir, rebuild=False)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
