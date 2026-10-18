"""The tracked queue catalog advertises the routes and models that actually run."""

import json
import unittest
from pathlib import Path

import grok_wire
import codex_models

ROOT = Path(__file__).resolve().parents[2]
TARGETS = json.loads((Path(__file__).with_name("queue-targets.json")).read_text())["targets"]
CODEX_DESK = json.loads((ROOT / "ops/config/engineering-codex-desk.v1.json").read_text())


class QueueTargetsCatalogTests(unittest.TestCase):
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
