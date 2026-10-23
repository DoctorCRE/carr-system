"""Synthetic capture outages must remain visible without releasing recordings."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN_DIR))
SPEC = importlib.util.spec_from_file_location("capture_outcomes_bridge", BIN_DIR / "capture-bridge.py")
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class CaptureOutcomesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Path(self.tmp.name) / "synthetic-session"
        self.session.mkdir()
        self.cfg = {"base_url": "https://capture.invalid", "device_id": "synthetic", "token": "fixture"}
        self.log = patch.object(bridge, "LOG_PATH", Path(self.tmp.name) / "capture.log")
        self.log.start()
        (self.session / ".capture.json").write_text(json.dumps({"session_token": "fixture-session"}))
        (self.session / "mic.caf").write_bytes(b"synthetic audio fixture")

    def tearDown(self) -> None:
        self.log.stop()
        self.tmp.cleanup()

    def test_poll_distinguishes_verified_pending_from_unavailable(self) -> None:
        with patch.object(bridge, "http_request", return_value=(200, {"state": "recording", "meeting_record": None})):
            self.assertEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        with patch.object(bridge, "http_request", side_effect=bridge.BridgeNetworkError("synthetic outage")):
            self.assertNotEqual(bridge.poll_cmd(self.cfg, self.session), 0)
        self.assertTrue((self.session / ".capture.json").exists())
        self.assertTrue((self.session / "mic.caf").exists())
        self.assertFalse((self.session / "ingested.json").exists())


if __name__ == "__main__":
    unittest.main()
