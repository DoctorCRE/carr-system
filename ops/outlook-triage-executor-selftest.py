#!/usr/bin/env python3
"""Synthetic contract checks. No Microsoft transport or mailbox access."""
import copy
import json
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
        self.assertFalse(contract["read_state_policy"]["require_read_before_move"])
        self.assertTrue(contract["read_state_policy"]["unread_may_move"])
        self.assertNotIn("atomic_read_condition", candidate["evidence"])
        self.assertEqual(self.select(contract, candidate), candidate["id"])

    def test_required_read_refuses_without_atomic_condition(self):
        contract, candidate = self.qualified_fixture()
        contract["read_state_policy"].update(require_read_before_move=True, read_condition="required")
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

    def test_lifecycle_transitions_and_disable_do_not_auto_reenable(self):
        contract, candidate = self.qualified_fixture()
        for state in ("unverified", "disabled", "refused"):
            candidate["lifecycle"] = state
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.select(contract, candidate)
        candidate["lifecycle"] = "verified"
        self.assertEqual(self.select(contract, candidate), candidate["id"])
        candidate["lifecycle"] = "unverified"
        with self.assertRaises(ValueError):
            self.select(contract, candidate)

    def test_unknown_duplicate_and_unsupported_contract_refuse(self):
        from outlook_triage_capability import select_candidate
        contract, candidate = self.qualified_fixture()
        with self.assertRaises(ValueError):
            select_candidate(contract, "unknown")
        contract["candidates"].append(copy.deepcopy(candidate))
        with self.assertRaises(ValueError):
            self.select(contract, candidate)
        contract["schema"] = "outlook-triage-executor-contract/v2"
        with self.assertRaises(ValueError):
            self.select(contract, candidate)

    def test_malformed_or_inconsistent_read_policy_refuses(self):
        for value in (None, "false", 0, 1):
            contract, candidate = self.qualified_fixture()
            contract["read_state_policy"]["require_read_before_move"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.select(contract, candidate)
        for key, value in (("preserve_read_state", False), ("read_condition", "required")):
            contract, candidate = self.qualified_fixture()
            contract["read_state_policy"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
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
