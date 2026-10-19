"""The tracked queue catalog advertises the routes and models that actually run."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import grok_wire
import codex_models
import desks

ROOT = Path(__file__).resolve().parents[2]
TARGETS = json.loads((Path(__file__).with_name("queue-targets.json")).read_text())["targets"]
CODEX_DESK = json.loads((ROOT / "ops/config/engineering-codex-desk.v1.json").read_text())


class QueueTargetsCatalogTests(unittest.TestCase):
    def test_luna_installer_registers_the_automatic_target_without_room_fanout(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "desks.json"
            process = subprocess.run(
                ["/bin/zsh", str(ROOT / "bin/install-luna-desk.sh")],
                env={**os.environ, "CARR_HERMES_DESKS": str(registry_path)},
                capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            entry = desks.Registry(registry_path).resolve(TARGETS["luna"]["desk"])
            self.assertEqual(entry["kind"], "codex-session")
            self.assertEqual(entry["family"], "luna")
            self.assertEqual(entry["effort"], "low")
            self.assertEqual(entry["sandbox"], "workspace-write")
            self.assertIsNone(entry.get("room_seat"))
            self.assertEqual(TARGETS["luna"]["capabilities"], ["read", "repo-write", "record-write"])

    def test_grok_is_advertised_as_the_grok_desk_at_the_model_it_requests(self):
        grok = TARGETS["grok"]
        self.assertEqual((grok["adapter"], grok["assignee"], grok["desk"]),
                         ("desk", "desk:grok-desk", "grok-desk"))
        self.assertEqual(grok["effective_model"].lower().replace(" ", "-"), grok_wire.MODEL)

    def test_engineering_codex_desk_names_sol_family_without_a_version(self):
        self.assertEqual(CODEX_DESK["family"], "sol")
        self.assertNotIn("model", CODEX_DESK)
        self.assertEqual(codex_models.family_default(None, TARGETS["sol"]["effective_model"]),
                         CODEX_DESK["family"])


if __name__ == "__main__":
    unittest.main()
