from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class StandaloneTests(unittest.TestCase):
    def test_built_executable_preserves_cli_exit_codes(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts/build_zipapp.py")],
                       cwd=root, check=True, capture_output=True)
        binary = root / "dist/layouter"
        with tempfile.TemporaryDirectory() as folder:
            missing = str(Path(folder) / "missing.toml")
            result = subprocess.run([str(binary), "--file", missing, "--check"],
                                    cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Configuration file does not exist", result.stderr)
            result = subprocess.run([str(binary), "--version"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "layouter 0.1.0")
