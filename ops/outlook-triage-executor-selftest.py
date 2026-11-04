#!/usr/bin/env python3
"""Synthetic contract checks. No Microsoft transport or mailbox access."""
import copy
import json
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))


class ExecutorContractTests(unittest.TestCase):
    def test_00_unverified_capability_performs_zero_mutations(self):
        from outlook_triage_capability import select_candidate

        contract = json.loads((ROOT / "spec/outlook/triage-executor-contract.v1.json").read_text())
        mutations = []
        for candidate in contract["candidates"]:
            try:
                selected = select_candidate(contract, candidate["id"])
                mutations.append(selected)
            except ValueError:
                pass
        self.assertEqual(mutations, [])

    def setUp(self):
        self.contract = json.loads((ROOT / "spec/outlook/triage-executor-contract.v1.json").read_text())

    def qualified_fixture(self):
        contract = copy.deepcopy(self.contract)
        contract["read_state_policy"] = {
            "source_decision": "fba2c07d",
            "preserve_read_state": True,
            "move_eligibility": "any_read_state",
        }
        candidate = contract["candidates"][0]
        candidate.update(status="sanctioned", lifecycle="verified")
        candidate["verified_read_policy"] = copy.deepcopy(contract["read_state_policy"])
        candidate["evidence"] = {key: "synthetic://fixture/receipt" for key in
                                 contract["selection"]["required_evidence"]}
        return contract, candidate

    def select(self, contract, candidate):
        from outlook_triage_capability import select_candidate
        return select_candidate(contract, candidate["id"])

    def test_refused_candidate_can_never_be_selected(self):
        contract, candidate = self.qualified_fixture()
        candidate["status"] = "refused"
        with self.assertRaisesRegex(ValueError, "capability_not_verified"):
            self.select(contract, candidate)

    def test_read_state_not_required_under_current_policy(self):
        contract, candidate = self.qualified_fixture()
        self.assertEqual(self.contract["read_state_policy"], contract["read_state_policy"])
        self.assertEqual(contract["read_state_policy"]["move_eligibility"], "any_read_state")
        self.assertNotIn("atomic_read_condition", candidate["evidence"])
        self.assertEqual(self.select(contract, candidate), candidate["id"])

    def test_required_read_refuses_without_atomic_condition(self):
        contract, candidate = self.qualified_fixture()
        contract["read_state_policy"]["move_eligibility"] = "read_only"
        candidate["verified_read_policy"] = copy.deepcopy(contract["read_state_policy"])
        with self.assertRaisesRegex(ValueError, "missing_capability_evidence"):
            self.select(contract, candidate)
        candidate["evidence"]["atomic_read_condition"] = "synthetic://atomic-move"
        self.assertEqual(self.select(contract, candidate), candidate["id"])

    def test_policy_change_invalidates_prior_verification(self):
        contract, candidate = self.qualified_fixture()
        contract["read_state_policy"]["source_decision"] = "synthetic-new-policy"
        with self.assertRaisesRegex(ValueError, "stale_policy_evidence"):
            self.select(contract, candidate)

    def test_every_required_guarantee_is_enforced(self):
        contract, candidate = self.qualified_fixture()
        for key in contract["selection"]["required_evidence"]:
            for invalid in (None, False, "", "   "):
                with self.subTest(key=key, invalid=invalid):
                    broken = copy.deepcopy(contract)
                    broken["candidates"][0]["evidence"][key] = invalid
                    with self.assertRaisesRegex(ValueError, "missing_capability_evidence"):
                        self.select(broken, candidate)

    def lifecycle_edge(self, source, target, event):
        edge = [source, target, event]
        self.assertIn(edge, self.contract["lifecycle"]["transitions"])
        contract, candidate = self.qualified_fixture()
        for state in (source, target):
            candidate["lifecycle"] = state
            if state == "verified":
                self.assertEqual(self.select(contract, candidate), candidate["id"])
            else:
                with self.assertRaisesRegex(ValueError, "^capability_not_verified$"):
                    self.select(contract, candidate)

    def test_lifecycle_all_required_evidence(self):
        self.lifecycle_edge("unverified", "verified", "all_required_evidence")

    def test_lifecycle_prohibited(self):
        self.lifecycle_edge("unverified", "refused", "prohibited")

    def test_lifecycle_disable_or_policy_change(self):
        self.lifecycle_edge("verified", "disabled", "disable_or_policy_change")

    def test_lifecycle_evidence_invalidated(self):
        self.lifecycle_edge("verified", "unverified", "evidence_invalidated")

    def test_lifecycle_reverify_requested(self):
        self.lifecycle_edge("disabled", "unverified", "reverify_requested")

    def test_lifecycle_refused_selection_attempt(self):
        self.lifecycle_edge("refused", "refused", "selection_attempt")

    def test_lifecycle_chart_matches_future_contract(self):
        lifecycle = self.contract["lifecycle"]
        chart = (ROOT / "spec/outlook/triage-executor-lifecycle.mmd").read_text()
        edges = re.findall(r"^\s*(\w+) --> (\w+): ([^\n]+)$", chart, re.MULTILINE)
        labels = {
            "all_required_evidence": "Sanctioned and every required proof present",
            "prohibited": "Interface prohibited",
            "disable_or_policy_change": "Operator disables or policy changes",
            "evidence_invalidated": "Evidence invalidated",
            "reverify_requested": "Reverification requested",
            "selection_attempt": "Selection attempted",
        }
        self.assertCountEqual(edges, [(source, target, labels[event])
                                     for source, target, event in lifecycle["transitions"]])
        self.assertEqual(set(lifecycle["states"]), {state for edge in edges for state in edge[:2]})
        self.assertIn("Future lifecycle specification", lifecycle["scope"])
        self.assertIn("Future lifecycle specification", chart)

    def test_unknown_candidate_refuses(self):
        from outlook_triage_capability import select_candidate
        contract, _ = self.qualified_fixture()
        with self.assertRaisesRegex(ValueError, "^unknown_or_duplicate_candidate$"):
            select_candidate(contract, "unknown")

    def test_duplicate_candidate_refuses(self):
        contract, candidate = self.qualified_fixture()
        contract["candidates"].append(copy.deepcopy(candidate))
        with self.assertRaisesRegex(ValueError, "^unknown_or_duplicate_candidate$"):
            self.select(contract, candidate)

    def test_unsupported_contract_refuses(self):
        contract, candidate = self.qualified_fixture()
        contract["schema"] = "outlook-triage-executor-contract/v2"
        with self.assertRaisesRegex(ValueError, "^unsupported_contract$"):
            self.select(contract, candidate)

    def test_preserve_read_state_is_mandatory(self):
        for value in (None, False, "true", 0, 1):
            contract, candidate = self.qualified_fixture()
            contract["read_state_policy"]["preserve_read_state"] = value
            candidate["verified_read_policy"] = copy.deepcopy(contract["read_state_policy"])
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_read_policy$"):
                self.select(contract, candidate)

    def test_invalid_move_eligibility_refuses(self):
        for value in (None, "required", False, 0, 1, [], {}):
            contract, candidate = self.qualified_fixture()
            contract["read_state_policy"]["move_eligibility"] = value
            candidate["verified_read_policy"] = copy.deepcopy(contract["read_state_policy"])
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "^invalid_read_policy$"):
                self.select(contract, candidate)

    def test_missing_policy_fields_refuse(self):
        for key in ("move_eligibility", "preserve_read_state", "source_decision"):
            contract, candidate = self.qualified_fixture()
            del contract["read_state_policy"][key]
            candidate["verified_read_policy"] = copy.deepcopy(contract["read_state_policy"])
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "^invalid_read_policy$"):
                self.select(contract, candidate)

    def test_legacy_contradictory_read_controls_refuse(self):
        for key, value in (("unread_may_move", False), ("unread_may_move", True),
                           ("require_read_before_move", True), ("read_condition", "required")):
            contract, candidate = self.qualified_fixture()
            contract["read_state_policy"][key] = value
            candidate["verified_read_policy"] = copy.deepcopy(contract["read_state_policy"])
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "^invalid_read_policy$"):
                self.select(contract, candidate)

    def test_committed_contract_keeps_every_transport_unavailable(self):
        self.assertEqual(self.contract["server"]["type"], "Exchange Online")
        self.assertEqual(self.contract["server"]["source_decision"], "fba2c07d")
        self.assertEqual(self.contract["open_fact"]["id"], "employer_permitted_interface")
        for candidate in self.contract["candidates"]:
            with self.subTest(candidate=candidate["id"]):
                self.assertIn(candidate["status"], ("unverified", "refused"))
                self.assertTrue(candidate["needs_evidence"])
                self.assertEqual(candidate["evidence"], {})

    def test_ci_discovers_executor_selftest_like_outlook_siblings(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ci_gates", ROOT / "ops/ci-gates.py")
        gates = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gates)
        self.assertIn("ops/outlook-triage-executor-selftest.py", gates.gate_programs())


if __name__ == "__main__":
    unittest.main()
