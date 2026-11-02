"""Import the launchd entry points without machine settings or external effects."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class LaunchdImports(unittest.TestCase):
    def test_entry_points(self):
        scripts = Path(__file__).resolve().parents[1] / "bin"
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}, clear=True):
            sys.path.insert(0, str(scripts))
            try:
                with patch("socket.socket.connect", side_effect=AssertionError("network during import")), patch(
                    "subprocess.Popen", side_effect=AssertionError("process during import")
                ):
                    for filename in ("call_budget.py", "capture-bridge.py", "call-mode.py", "phone_intake.py"):
                        with self.subTest(script=filename):
                            name = "launchd_" + filename.replace("-", "_").removesuffix(".py")
                            spec = importlib.util.spec_from_file_location(name, scripts / filename)
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[name] = module
                            spec.loader.exec_module(module)
                            print("IMPORTED " + filename)
            finally:
                sys.path.remove(str(scripts))


if __name__ == "__main__":
    unittest.main()
