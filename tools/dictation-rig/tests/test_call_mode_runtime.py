"""Runtime checks without recording, accessibility actions, or SDK dependencies."""
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"


class CallModeRuntimeTests(unittest.TestCase):
    def test_post_call_runs_with_repository_venv_python(self):
        sys.path.insert(0, str(BIN_DIR))
        import post_call

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            python = root / ".venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$2/worker-argv"\n'
                'printf \'{"state":"awaiting_context"}\\n\'\n'
            )
            python.chmod(0o700)
            entry = root / "tools" / "dictation-rig" / "bin" / "post_call.py"
            with patch.object(post_call, "__file__", str(entry)):
                status = post_call.run_session(root)
            self.assertEqual(status, {"state": "awaiting_context"})
            self.assertEqual((root / "worker-argv").read_text().splitlines(), [str(entry), str(root)])

    def test_companion_import_and_state_endpoint_without_sdk(self):
        code = '''
import importlib.util, pathlib, sys
from http.server import ThreadingHTTPServer
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec("typesafe_sdk") is None
spec = importlib.util.spec_from_file_location("call_mode", pathlib.Path(sys.argv[1]) / "call-mode.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = pathlib.Path(sys.argv[2])
module.CAPTURE_CONFIG = root / "config.json"
module.LOG_PATH = root / "companion.log"
state = module.current_state
module.current_state = lambda: state(root)
assert "typesafe_sdk" not in sys.modules
server = ThreadingHTTPServer(("127.0.0.1", 0), module.CallModeHandler)
print(server.server_port, flush=True)
server.serve_forever()
'''
        with tempfile.TemporaryDirectory() as tmp:
            process = subprocess.Popen(
                [sys.executable, "-S", "-u", "-c", code, str(BIN_DIR), tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                ready, _, _ = select.select([process.stdout], [], [], 5)
                self.assertTrue(ready, "companion did not finish startup within five seconds")
                port = process.stdout.readline().strip()
                if not port:
                    self.fail(process.stderr.read())
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as response:
                    self.assertEqual(json.load(response), {"state": "idle", "local_partner": None, "sessions": []})
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
