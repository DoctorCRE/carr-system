#!/usr/bin/env python3
"""Focused deterministic tests for the D2 action-risk disposition contract."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "ops" / "action-risk-guardrails.py"
SPEC = importlib.util.spec_from_file_location("action_risk_guardrails", MODULE_PATH)
assert SPEC and SPEC.loader
action_risk_guardrails = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action_risk_guardrails)

ARTIFACT_PATH = ROOT / "control-room" / "contracts" / "action-risk-dispositions.v1.json"
REGISTRY_PATH = ROOT / "control-room" / "contracts" / "action-risk-registry.v1.json"
CANARY = "CARR-SECRET-CANARY-7F4A"


class ActionRiskDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifact = json.loads(ARTIFACT_PATH.read_text())
        cls.registry = json.loads(REGISTRY_PATH.read_text())

    def validate(self, artifact=None, registry=None, registry_bytes=None):
        return action_risk_guardrails.validate_dispositions(
            self.artifact if artifact is None else artifact,
            self.registry if registry is None else registry,
            REGISTRY_PATH.read_bytes() if registry_bytes is None else registry_bytes,
        )

    def assert_invalid(self, artifact, expected):
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, expected):
            self.validate(artifact)

    def test_baseline_is_exactly_covered_and_set_lead_is_the_only_selected_remediation(self):
        result = self.validate()
        self.assertEqual({k: result[k] for k in ("status", "selected_slice", "rows")},
                         {"status": "valid", "selected_slice": "set-lead", "rows": 13})
        self.assertEqual(self.artifact["contract"], "carr-action-risk-dispositions")
        self.assertEqual(self.artifact["version"], "1.0.0")
        self.assertEqual(self.artifact["status"], "phase1_guardrails")
        rows = self.artifact["dispositions"]
        self.assertEqual({row["tool_name"] for row in rows}, action_risk_guardrails.BASELINE_NONE_ROWS)
        self.assertEqual([row["tool_name"] for row in rows if row["selected_slice"]], ["set-lead"])
        self.assertNotIn(CANARY, json.dumps(self.artifact, sort_keys=True))

    def test_every_baseline_row_has_a_closed_owner_target_control_and_evidence_category(self):
        for row in self.artifact["dispositions"]:
            with self.subTest(tool_name=row["tool_name"]):
                expected = action_risk_guardrails.EXPECTED_ROWS[row["tool_name"]]
                self.assertEqual(
                    (row["owner"], row["target_control"], row["evidence_category"],
                     row["evidence"], row["priority"], row["selected_slice"]),
                    expected,
                )
                self.assertNotEqual(row["evidence_category"], "pending_shape_analysis")

    def test_registry_digest_is_bound_and_stale_bytes_refuse(self):
        stale_binding = copy.deepcopy(self.artifact)
        stale_binding["action_risk_registry"]["sha256"] = "0" * 64
        self.assert_invalid(stale_binding, "registry digest")
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, "registry digest"):
            self.validate(registry_bytes=b"stale")

    def test_validation_reports_historical_coverage_without_reclassifying_verbs(self):
        self.assertEqual(self.registry["status"], "historical_phase1_baseline")
        self.assertEqual(self.registry["provenance"]["last_artifact_change"],
                         "e1dcf5126b56c0ca762152ca6b7de31d3c3213cb")
        self.assertEqual(self.validate()["coverage"], "historical_phase1_baseline")
        self.assertEqual(self.validate()["current_handler_inventory"],
                         "mcp-server/src/scac-mutation-registry.current.generated.js")

    def test_missing_unknown_duplicate_and_unowned_baseline_rows_refuse(self):
        missing = copy.deepcopy(self.artifact)
        missing["dispositions"].pop()
        self.assert_invalid(missing, "baseline coverage")

        unknown = copy.deepcopy(self.artifact)
        unknown["dispositions"][-1]["tool_name"] = "unknown-tool"
        self.assert_invalid(unknown, "owner or control")

        duplicate = copy.deepcopy(self.artifact)
        duplicate["dispositions"][-1] = copy.deepcopy(duplicate["dispositions"][0])
        self.assert_invalid(duplicate, "duplicates")

        unowned = copy.deepcopy(self.artifact)
        unowned["dispositions"][0]["owner"] = ""
        self.assert_invalid(unowned, "owner")

    def test_selected_row_can_leave_none_only_when_runtime_protection_and_evidence_match(self):
        current_none = copy.deepcopy(self.registry)
        current_none["verbs"]["set-lead"]["protection"] = "NONE"
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, "selected runtime protection"):
            self.validate(registry=current_none)

        missing_base_version = copy.deepcopy(self.registry)
        missing_base_version["verbs"]["set-lead"]["base_version_required"] = False
        with self.assertRaisesRegex(action_risk_guardrails.DispositionError, "selected base_version"):
            self.validate(registry=missing_base_version)

        bad_evidence = copy.deepcopy(self.artifact)
        selected = next(row for row in bad_evidence["dispositions"] if row["tool_name"] == "set-lead")
        selected["evidence"] = "unverified"
        self.assert_invalid(bad_evidence, "owner or control")

        wrong_owner = copy.deepcopy(self.artifact)
        wrong_owner["dispositions"][0]["owner"] = "unowned"
        self.assert_invalid(wrong_owner, "owner or control")


if __name__ == "__main__":
    unittest.main()
