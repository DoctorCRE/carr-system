#!/usr/bin/env python3
# ci: selftest
import hashlib
import importlib.util
import json
import sys
import unittest
import tempfile
import io
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import rule_recall
from lib import rule_routes


class RecallContract(unittest.TestCase):
    def test_caller_authored_proof_files_never_remove_a_rule_from_boot(self):
        """Both review bypasses: every receipt is one reused boot snapshot, or a
        genuine complete boot copied into the observations, over a synthetic
        200-turn frame. Committed JSON cannot show the replacement route ran."""
        spec = importlib.util.spec_from_file_location("eval_ops", ROOT / "ops/rule_delivery_eval.py")
        evaluation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluation)
        rid, statement, moment = "24e10ee8", "Binding text", "2026-10-05T12:00:00Z"
        boot_rules = [{"id": rid, "statement": statement}]
        for name, receipt_for in (
                ("reused unrelated boot receipt",
                 lambda ref: {"receipt_id": "unrelated-boot-snapshot", "observed_at": moment,
                              "rules": boot_rules}),
                ("copied complete boot",
                 lambda ref: {"receipt_id": "one-boot", "observed_at": moment,
                              "schema": "rule-recall-boot-observation/v1", "rules": boot_rules})):
            with self.subTest(bypass=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                def put(path, value):
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(value))
                    return {"path": path, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
                route = {"routes": [{"kind": "trigger", "tools": ["Write"]}]}
                put("ops/config/rule-classes.v1.json", {"rules": {rid: {
                    "class": "b", "always_on": False, "chars": 12, "summary": "Test", "when": "Test"}}})
                put("ops/config/rule-selection-corpus.v1.json", {"rules": boot_rules})
                put("ops/config/rule-routes.v1.json", {"rules": {rid: route}})
                cases = json.loads((ROOT / "ops/fixtures/rule-delivery-eval/cases.v2.json").read_text())
                fixture = put("ops/fixtures/rule-delivery-eval/cases.v2.json", cases)
                def row(ref, requires):
                    receipt = receipt_for(ref)
                    return {"source_ref": ref, "requires": requires, "event_at": moment,
                            "receipt_id": receipt["receipt_id"], "receipt": receipt}
                benchmark = [row(c["id"], rid in c["gold"]) for c in cases["cases"] if c["split"] == "test"]
                real = [row(f"turn-{i}", i < 10) for i in range(200)]
                frame = put("frame.json", {"schema": "rule-recall-native-frame/v1", "turns": [
                    {k: r[k] for k in ("source_ref", "event_at", "receipt")} for r in real]})
                common = {"schema": "rule-recall-observations/v1", "rule_id": rid,
                          "statement_sha256": hashlib.sha256(statement.encode()).hexdigest(),
                          "route_sha256": hashlib.sha256(json.dumps(
                              route, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
                binding = {k: common[k] for k in ("statement_sha256", "route_sha256")}
                binding["benchmark"] = put("benchmark.json", dict(
                    common, source="benchmark", observations=benchmark, fixture_sha256=fixture["sha256"]))
                binding["real_turns"] = put("real.json", dict(
                    common, source="real_turns", observations=real, sampling_frame=frame))
                put("ops/config/rule-recall-policy.v1.json",
                    {"schema": "rule-recall-policy/v1", "proofs": {rid: binding}})
                self.assertIn(rid, evaluation.boot_always_on_ids(str(root)))

    def test_hook_internal_error_does_not_allow_an_effect(self):
        spec = importlib.util.spec_from_file_location("recall_hook", ROOT / "hooks/rule-boot-gate.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        output = io.StringIO()
        with patch("lib.rule_boot_gate.verdict", side_effect=ValueError("invalid state")), \
                patch("sys.stdin", io.StringIO(json.dumps({"tool_name": "Write", "hook_event_name": "PreToolUse"}))), \
                patch("sys.stdout", output), patch.object(hook, "log"):
            hook.main()
        self.assertEqual(json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_boot_without_length_evidence_cannot_unlock(self):
        spec = importlib.util.spec_from_file_location("boot_missing_length", ROOT / "ops/rule-boot-gate-selftest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            case = module.Case(str(ROOT), folder)
            case.stub("a", pages=1)
            case.arm()
            answer = case.boot(1)
            answer.pop("total_chars", None)
            case.fetch_cmd(module.abs_cmd(1), 1, boot=answer)
            self.assertTrue(module.denied(case.call(*module.READ)))

    def test_boot_cannot_escape_after_repeated_holds_or_outage(self):
        spec = importlib.util.spec_from_file_location("boot_test_cases", ROOT / "ops/rule-boot-gate-selftest.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            case = module.Case(str(ROOT), folder)
            case.stub("a", pages=3)
            case.arm()
            for _ in range(8):
                self.assertTrue(module.denied(case.call(*module.READ)))
            pre, _ = case.fetch(1, answer="error")
            self.assertFalse(module.denied(pre))
            self.assertTrue(module.denied(case.call(*module.READ)))
            for page in (1, 2, 3):
                case.fetch(page)
            self.assertIsNone(case.call(*module.READ))
    def test_generator_retains_unproven_action_rule(self):
        spec = importlib.util.spec_from_file_location("boot_sync", ROOT / "ops/sync-rule-boot-classes.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        doc = {"rules": {"abcdef12": {"class": "b", "always_on": False,
               "chars": 360, "summary": "Test event rule", "when": "before test event"}}}
        self.assertIn('"on": true', module.render(doc))
        self.assertGreater(module.estimate(doc)[0], 2800)

    def test_receipt_does_not_count_a_selector_match_as_delivery(self):
        summary = rule_recall.delivery_counts([{"at": "2026-10-05T12:00:00Z",
            "matched": ["abcdef12"], "delivered": [], "overflow": ["abcdef12"]}],
            ["abcdef12"], "2026-10-05T13:00:00Z", 14)
        self.assertEqual(summary["counts"]["abcdef12"], 0)

    def test_known_full_text_receipt_formats_are_counted(self):
        for schema in ("rule-jit-trigger-delivery/v1", "rule-delivery-preuse-reselection/v1"):
            with self.subTest(schema=schema):
                self.assertEqual(rule_recall.delivered_ids({"schema": schema,
                    "rules": [{"id": "abcdef12", "statement": "Binding text"}]}), ["abcdef12"])

    def test_eval_grades_availability_without_erasing_jit_labels(self):
        spec = importlib.util.spec_from_file_location("recall_eval", ROOT / "evals/rule-delivery/run_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        case = {"id": "example", "split": "test", "input_sha256": "input"}
        obs = module.observe(case, [], boot_ids={"abcdef12"})
        self.assertEqual(obs["available"], ["abcdef12"])
        self.assertEqual(obs["delivered"], [])

    def test_health_cannot_clear_from_missing_or_invalid_logs(self):
        self.assertFalse(rule_recall.delivery_counts([], ["abcdef12"],
                         "2026-10-05T13:00:00Z", 14)["readable"])

    def audit_counts(self, pages):
        """Feed boot pages, as (context fields, page) pairs, to the audit."""
        spec = importlib.util.spec_from_file_location("recall_audit", ROOT / "tools/rule-recall-audit.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            with path.open("w") as handle:
                for n, (fields, boot) in enumerate(pages):
                    if boot is None:
                        row = {"type": "system", "subtype": "compact_boundary"}
                    else:
                        result = json.dumps({"ok": True, "rule_boot": boot})
                        row = {"type": "user", "message": {"content": [
                            {"type": "tool_result", "content": result}]}}
                    handle.write(json.dumps({**row, "sessionId": "s1", "uuid": f"u{n}",
                                             "timestamp": "2026-10-05T12:00:00Z", **fields}) + "\n")
            result = module.audit(["abcdef12", "abcdef13"], [], [path], "2026-10-05T13:00:00Z")
        return result["deliveries_30d"]["counts"]

    @staticmethod
    def boot_pages(texts, total=None):
        total = sum(len(t.encode("utf-16-le")) // 2 for t in texts) if total is None else total
        return [{"schema": "carr-rule-boot/v1", "digest": "sha256:" + "a" * 64, "page": n,
                 "pages_total": len(texts), "total_chars": total, "text": text}
                for n, text in enumerate(texts, 1)]

    def test_audit_counts_a_complete_boot_in_one_context(self):
        pages = self.boot_pages(["### abcdef12\nFirst rule ✓\n\n", "### abcdef13\nSecond rule\n"])
        self.assertEqual(self.audit_counts([({}, p) for p in pages]), {"abcdef12": 1, "abcdef13": 1})

    def test_audit_does_not_promote_headings_without_their_text(self):
        pages = self.boot_pages(["### abcdef12\n", "### abcdef13\n"], total=10000)
        self.assertEqual(self.audit_counts([({}, p) for p in pages]), {"abcdef12": 0, "abcdef13": 0})

    def test_audit_does_not_join_pages_from_different_contexts(self):
        one, two = self.boot_pages(["### abcdef12\nFirst\n\n", "### abcdef13\nSecond\n"])
        for split in ([({}, one), ({"agentId": "sub", "isSidechain": True}, two)],
                      [({}, one), ({}, None), ({}, two)],
                      [({}, one), ({}, dict(two, total_chars=two["total_chars"] + 1))]):
            with self.subTest(split=split):
                self.assertEqual(self.audit_counts(split), {"abcdef12": 0, "abcdef13": 0})

    def test_published_audit_carries_only_opaque_section_fields(self):
        """Doctrine section keys, titles and document names can identify a
        person; the committed audit publishes stable IDs and counts only."""
        spec = importlib.util.spec_from_file_location("recall_report", ROOT / "tools/rule-recall-report.py")
        report = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(report)
        section = {"id": "094e3ad4-6ab9-4c69-8cd6-af702e456a19", "slug": "profile-audit-2026-07-20",
                   "key": "facebook-example-person", "title": "Facebook — Example Person",
                   "version": 1, "observed_read_calls": 0, "status": "no_observed_read_request"}
        self.assertEqual(report.public_sections([section]), [{
            "id": section["id"], "version": 1, "observed_read_calls": 0,
            "status": "no_observed_read_request"}])
        folder = ROOT / "out/orch/rulerecall"
        data = json.loads((folder / "rule-data.json").read_text())
        self.assertNotIn("unread_documents", data)
        for row in data["sections"]:
            self.assertEqual(set(row), report.PUBLIC_SECTION_FIELDS)
        page = (folder / "dead-rules.html").read_text()
        self.assertNotIn("Document / section", page)
        self.assertNotIn(section["slug"], page + json.dumps(data))

    def test_missing_event_routes_deliver_before_the_call(self):
        routes = rule_routes.load_routes(ROOT)
        for rid, tool, args in [
            ("2a3ff869", "Bash", {"command": "./run.sh call update-document-status '{}'"}),
            ("4dd44fd7", "mcp__carr__update_document_status", {}),
            ("14181e60", "Write", {"file_path": "DNA/test.md"}),
            ("4a53ff82", "Edit", {"file_path": "lib/test.py"}),
            ("5409731b", "Write", {"file_path": "migrations/test.sql"}),
            ("24e10ee8", "Bash", {"command": "git -C /tmp/example commit -F /tmp/message"}),
        ]:
            with self.subTest(rule=rid):
                self.assertIn(rid, rule_routes.matched_rule_ids(routes, tool, args))


if __name__ == "__main__":
    unittest.main()
