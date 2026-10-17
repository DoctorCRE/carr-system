#!/usr/bin/env python3
"""Focused, offline tests for the synthetic read-only function router."""

import copy
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import ai_read_router


ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "evals" / "ai" / "function-router.v1.json"
ENVELOPE_FIXTURE_PATH = ROOT / "evals" / "ai" / "response-envelope.v1.json"
CANARY = "CARR-SECRET-CANARY-7F4A"
DEFAULT_ENVELOPE = object()
EXPECTED_READ_ROUTES = [
    "find",
    "catch-me-up",
    "who-do-we-know",
    "doctrine-index",
    "search-doctrine",
    "read-doctrine",
    "find-precedent",
    "stale-records",
    "today-triage",
]


class ReadRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = json.loads(FIXTURE_PATH.read_text())
        cls.valid_envelope = json.loads(ENVELOPE_FIXTURE_PATH.read_text())["reference_envelope"]
        cls.suite = ai_read_router._load_bound_suite()

    def route(self, proposal, envelope=DEFAULT_ENVELOPE):
        return ai_read_router.route_read_only(
            proposal, self.valid_envelope if envelope is DEFAULT_ENVELOPE else envelope
        )

    def assert_refused(self, proposal, code, envelope=DEFAULT_ENVELOPE):
        result = self.route(proposal, envelope)
        self.assertEqual(result, {"state": "refused", "violation_codes": [code]})
        self.assertNotIn(CANARY, json.dumps(result, sort_keys=True))

    def test_policy_is_fixed_and_contains_one_selected_route_row_per_tool(self):
        self.assertEqual(hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(), ai_read_router.POLICY_SHA256)
        self.assertEqual([row["tool_name"] for row in self.policy["routes"]], EXPECTED_READ_ROUTES)
        self.assertTrue(all(set(row) == ai_read_router.ROUTE_FIELDS for row in self.policy["routes"]))
        self.assertTrue(all(row["protection"] == "read_only" for row in self.policy["routes"]))
        self.assertNotIn("selected_tool_evidence", self.policy)
        self.assertNotIn("tool_registry", self.policy)
        self.assertNotIn("action_risk_registry", self.policy)
        self.assertNotIn("server_context", self.policy)

    def test_runtime_does_not_pin_or_repin_the_whole_tools_registry(self):
        source = (ROOT / "ops" / "ai_read_router.py").read_text()
        self.assertNotIn("mcp-server/src/tools.js", source)
        self.assertFalse((ROOT / "ops" / "ai-read-router-repin.py").exists())

    def _live_tool_projection(self, selected_names, include_unrelated=False):
        probe = (
            "import { TOOLS } from './mcp-server/src/tools.js';"
            f"const names = {json.dumps(selected_names)};"
            "const registry = {...TOOLS};"
            + ("registry.__unrelated_projection_probe__ = {write:true,fullOnly:true,inputSchema:{type:'object'}};"
               if include_unrelated else "")
            + "process.stdout.write(JSON.stringify(names.map((tool_name) => {"
            "const tool = registry[tool_name];"
            "return tool ? {tool_name,write:Boolean(tool.write),full_only:Boolean(tool.fullOnly),"
            "input_schema:tool.inputSchema} : null;})));"
        )
        run = subprocess.run(
            ["node", "--input-type=module", "-e", probe], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        return json.loads(run.stdout)

    def _live_risk_projection(self, selected_names):
        generated = subprocess.run(
            [sys.executable, "ops/action-risk-registry.py"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        live_risks = json.loads(generated.stdout)["verbs"]
        return [
            ({"tool_name": name, "write": live_risks[name]["write"],
              "protection": live_risks[name]["protection"]} if name in live_risks else None)
            for name in selected_names
        ]

    def _assert_selected_semantics_match(self, policy):
        selected_names = [row["tool_name"] for row in policy["routes"]]
        embedded = [{key: row[key] for key in ai_read_router.TOOL_SEMANTIC_FIELDS}
                    for row in policy["routes"]]
        self.assertEqual(embedded, self._live_tool_projection(selected_names))
        self.assertEqual(
            [{"tool_name": row["tool_name"], "write": row["write"], "protection": row["protection"]}
             for row in policy["routes"]],
            self._live_risk_projection(selected_names),
        )

    def test_selected_route_and_risk_projections_match_current_live_outputs(self):
        self._assert_selected_semantics_match(self.policy)

    def test_unrelated_tools_are_outside_the_selected_semantic_projection(self):
        selected_names = [row["tool_name"] for row in self.policy["routes"]]
        self.assertEqual(
            self._live_tool_projection(selected_names),
            self._live_tool_projection(selected_names, include_unrelated=True),
        )

    def test_selected_name_write_full_only_schema_and_risk_drift_fail_parity(self):
        mutations = (
            ("tool_name", lambda row: row.update(tool_name="not-a-live-selected-tool")),
            ("write", lambda row: row.update(write=True)),
            ("full_only", lambda row: row.update(full_only=True)),
            ("input_schema", lambda row: row["input_schema"].update(required=[])),
            ("protection", lambda row: row.update(protection="NONE")),
        )
        for field, mutate in mutations:
            with self.subTest(field=field):
                drifted = copy.deepcopy(self.policy)
                mutate(drifted["routes"][0])
                with self.assertRaises(AssertionError):
                    self._assert_selected_semantics_match(drifted)

    def test_accepts_normalized_find_with_code_owned_attribution_and_envelope_binding(self):
        proposal = {
            "schema_version": 1,
            "tool_name": "find",
            "arguments": {"query": "synthetic practice"},
        }
        result = self.route(proposal)
        self.assertEqual(result, {
            "state": "accepted",
            "route": {"tool_name": "find", "arguments": {"query": "synthetic practice"}},
            "attribution": ai_read_router.SERVER_CONTEXT,
            "envelope_binding": {
                "suite_digest": self.suite["_digest"],
                "case_id": "AI-GROUND-001",
                "envelope_digest": ai_read_router._canonical_digest(self.valid_envelope),
            },
            "calls_models": False,
            "writes_records": False,
            "allowed_actions": [],
        })

    def test_accepts_each_safe_read_route_with_exact_typed_arguments(self):
        proposals = {
            "find": {"query": "synthetic practice"},
            "catch-me-up": {"ref": "C-001", "limit": 10},
            "who-do-we-know": {"target": "C-001", "max_depth": 2, "limit": 3},
            "doctrine-index": {"content_classes": ["policy", "playbook"]},
            "search-doctrine": {"q": "synthetic doctrine", "content_classes": ["policy"], "limit": 5},
            "read-doctrine": {"document": "synthetic-doctrine", "if_generation_match": 1},
            "find-precedent": {"query": "synthetic ruling", "limit": 4, "since": "2026-01-01"},
            "stale-records": {},
            "today-triage": {},
        }
        self.assertEqual(list(proposals), EXPECTED_READ_ROUTES)
        for tool_name, arguments in proposals.items():
            with self.subTest(tool_name=tool_name):
                result = self.route({
                    "schema_version": 1,
                    "tool_name": tool_name,
                    "arguments": arguments,
                })
                self.assertEqual(result["state"], "accepted")
                self.assertEqual(result["route"], {"tool_name": tool_name, "arguments": arguments})

    def test_accepted_descriptor_is_detached_from_proposal_and_server_values(self):
        proposal = {
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"},
        }
        accepted = self.route(proposal)
        proposal["arguments"]["query"] = CANARY
        self.assertEqual(accepted["route"]["arguments"], {"query": "synthetic"})

        accepted["attribution"]["runtime_principal"] = CANARY
        accepted["envelope_binding"]["case_id"] = CANARY
        later = self.route({
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "later"},
        })
        self.assertEqual(later["attribution"], ai_read_router.SERVER_CONTEXT)
        self.assertEqual(later["envelope_binding"]["case_id"], "AI-GROUND-001")

    def test_refuses_unknown_write_and_sensitive_targets_without_execution(self):
        self.assert_refused({"schema_version": 1, "tool_name": "not-a-real-tool", "arguments": {}}, "router_unknown_tool")
        self.assert_refused({"schema_version": 1, "tool_name": "add-loop", "arguments": {}}, "router_write_target_forbidden")
        self.assert_refused({"schema_version": 1, "tool_name": "read-work-shape", "arguments": {}}, "router_sensitive_target_forbidden")

    def test_refuses_extra_missing_and_wrong_typed_arguments_without_coercion(self):
        self.assert_refused({"schema_version": 1, "tool_name": "find", "arguments": {}}, "router_arguments_missing")
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic", "limit": 1},
        }, "router_arguments_unknown")
        self.assert_refused({
            "schema_version": 1, "tool_name": "who-do-we-know", "arguments": {"target": "C-001", "max_depth": "2"},
        }, "router_arguments_type_invalid")
        self.assert_refused({
            "schema_version": 1, "tool_name": "search-doctrine",
            "arguments": {"q": "synthetic", "content_classes": ["policy", 7]},
        }, "router_arguments_type_invalid")
        self.assert_refused({
            "schema_version": 1, "tool_name": "read-doctrine", "arguments": {},
        }, "router_arguments_missing")
        self.assert_refused({
            "schema_version": 1, "tool_name": "today-triage", "arguments": {"tenant": CANARY},
        }, "router_arguments_unknown")

    def test_refuses_direct_target_and_authority_widening_fields_without_leaking_input(self):
        forbidden = [
            "target", "organization_tenant_id", "tenant_id", "identity", "actor",
            "runtime_principal", "sponsoring_human_id", "profile", "capability",
            "capabilities", "write", "action", "actions", "allowed_actions",
        ]
        for field in forbidden:
            with self.subTest(field=field):
                self.assert_refused({
                    "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"}, field: CANARY,
                }, "router_authority_field_forbidden")

    def test_refuses_unknown_outer_fields_and_bad_proposal_shape(self):
        self.assert_refused({
            "schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"}, "unexpected": CANARY,
        }, "router_proposal_unknown_fields")
        self.assert_refused([], "router_proposal_not_object")
        self.assert_refused({"schema_version": 1, "tool_name": "find"}, "router_proposal_missing_fields")

    def test_fresh_self_consistent_forged_policy_cannot_reach_public_api(self):
        signature = inspect.signature(ai_read_router.route_read_only)
        self.assertEqual(list(signature.parameters), ["proposal", "response_envelope"])
        forged = json.loads(FIXTURE_PATH.read_text())
        forged["routes"][0]["input_schema"]["required"] = []
        with self.assertRaises(TypeError):
            ai_read_router.route_read_only(
                {"schema_version": 1, "tool_name": "find", "arguments": {}}, self.valid_envelope,
                policy=forged,
            )
        self.assert_refused(
            {"schema_version": 1, "tool_name": "find", "arguments": {}}, "router_arguments_missing"
        )

    def test_response_envelope_must_be_actual_valid_and_semantically_passing(self):
        proposal = {"schema_version": 1, "tool_name": "find", "arguments": {"query": "synthetic"}}
        self.assert_refused(proposal, "router_envelope_evidence_invalid", None)
        missing_field = json.loads(json.dumps(self.valid_envelope))
        del missing_field["response"]["metrics"]
        self.assert_refused(proposal, "router_envelope_evidence_invalid", missing_field)
        semantic_failure = json.loads(json.dumps(self.valid_envelope))
        semantic_failure["response"]["answer"] = CANARY
        self.assert_refused(proposal, "router_envelope_evidence_invalid", semantic_failure)
        refused = json.loads(json.dumps(self.valid_envelope))
        refused["response"]["status"] = "refused"
        self.assert_refused(proposal, "router_envelope_evidence_invalid", refused)

    def test_adapter_has_no_execution_or_mcp_import_path(self):
        source = (ROOT / "ops" / "ai_read_router.py").read_text()
        for forbidden in ("mcp-server", "subprocess", "requests", "sqlite3", "callback"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
