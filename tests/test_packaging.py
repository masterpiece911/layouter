from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile


class StandaloneTests(unittest.TestCase):
    def test_local_install_uses_selected_python_instead_of_path_python(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            checkout = stage / 'checkout'
            shutil.copytree(root / 'src', checkout / 'src',
                            ignore=shutil.ignore_patterns('__pycache__', '_react_runtime.zip'))
            (checkout / 'scripts').mkdir()
            for name in ('Makefile', 'pyproject.toml', 'LICENSE', 'scripts/build_zipapp.py'):
                shutil.copy2(root / name, checkout / name)
            # A TOML-only invocation never extracts the React runtime. Supply a
            # packaging fixture so this test also runs before the npm build.
            with ZipFile(checkout / 'src/layouter/_react_runtime.zip', 'w') as archive:
                archive.writestr('manifest.json', '{"version": "0.1.0"}')
            # Exercise spaces and shell quoting in both interpreter and install paths.
            interpreter = stage / "Python's directory" / 'python'
            interpreter.parent.mkdir()
            interpreter.symlink_to(sys.executable)
            path_bin = stage / 'bin'
            path_bin.mkdir()
            wrong_python = path_bin / 'python3'
            wrong_python.write_text('#!/bin/sh\necho "No module named tomllib" >&2\nexit 91\n')
            wrong_python.chmod(0o755)
            destination = stage / 'staged root'
            # The runtime is a separately tested build prerequisite; skip npm
            # here while exercising the actual install recipe and full archive.
            subprocess.run(['make', '-o', 'runtime', 'install', f'PYTHON={interpreter}',
                            f'DESTDIR={destination}', 'PREFIX=/opt/layouter'],
                           cwd=checkout, check=True, capture_output=True, text=True)
            binary = destination / 'opt/layouter/bin/layouter'
            env = {**os.environ, 'PATH': str(path_bin)}
            env.pop('PYTHONPATH', None)
            result = subprocess.run([str(binary), '--version'], cwd=stage, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'layouter 0.1.0')
            workflow = stage / 'workflow.toml'
            workflow.write_text('[[workspace]]\nnumber = 2\n')
            result = subprocess.run([str(binary), '--file', str(workflow), '--check'],
                                    cwd=stage, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Valid:', result.stdout)

    def test_built_executable_preserves_cli_exit_codes(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts/build_zipapp.py"), "--core"],
                       cwd=root, check=True, capture_output=True)
        binary = root / "dist/layouter-core"
        with tempfile.TemporaryDirectory() as folder:
            missing = str(Path(folder) / "missing.toml")
            result = subprocess.run([str(binary), "--file", missing, "--check"],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Configuration file does not exist", result.stderr)
            result = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "layouter 0.1.0")
