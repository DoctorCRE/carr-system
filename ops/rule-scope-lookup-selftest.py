"""Behavior at the store-backed lookup interface. No live store or model calls."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.rule_scope_lookup import lookup, select_rules


def rule(rid, scope, **extra):
    return dict(id=rid, statement="Full binding text " + rid, scope=scope,
                personal_to=None, **extra)


def catalog(*rules):
    return dict(ok=True, schema="rule-scope-catalog/v1", corpus_digest="a" * 64,
                identity=dict(agent_principal_id="joe-local", runtime_principal="joe-local",
                              sponsoring_human_id="joe"), rules=list(rules))


class LookupTests(unittest.TestCase):
    def test_exact_tool_verb_command_and_path_matches_union(self):
        source = catalog(rule("11111111", {"tools": ["Bash"]}),
                         rule("22222222", {"verbs": ["read-doctrine"]}),
                         rule("33333333", {"bash_patterns": [r"\brun\.sh\b"]}),
                         rule("44444444", {"path_globs": ["*.md"]}))
        result = select_rules(source, dict(hook_event_name="PreToolUse", tool_name="Bash",
                              tool_input=dict(command='./run.sh call read-doctrine \'{}\'')))
        self.assertEqual([r["id"] for r in result["rules"]], ["11111111", "22222222", "33333333"])
        self.assertTrue(result["complete"])
        self.assertEqual(select_rules(source, dict(tool_name="Edit", tool_input=dict(file_path="docs/a.md")))["rules"], [source["rules"][3]])

    def test_missing_or_malformed_scope_is_a_defect_never_delivery(self):
        for scope in ({}, {"section": "all sessions"}, {"tools": "Bash"},
                      {"tools": ["Bash"], "bash_patterns": ["["]}, {"moment": " "}):
            result = select_rules(catalog(rule("11111111", scope)), dict(tool_name="Bash"))
            self.assertEqual(result["rules"], [])
            self.assertEqual(result["data_defects"][0]["rule_id"], "11111111")
            self.assertFalse(result["complete"])

    def test_moment_judgment_sees_current_event_and_full_scope_and_statement(self):
        row = rule("11111111", {"moment": "Before closing a message to Joe"})
        calls = []
        def judge(state, questions):
            calls.append((state, questions))
            return dict(model="jev-1.13.0", answers={key: dict(type="choice", choice="applies", confidence=0.99) for key in questions})
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PreCompact", "Stop"):
            result = select_rules(catalog(row), dict(hook_event_name=event, task="Reply to Joe"), judge=judge)
            self.assertEqual(result["rules"], [row])
            self.assertEqual(calls[-1][0]["event"]["hook_event_name"], event)
            self.assertEqual(calls[-1][0]["rules"]["11111111"], row)

    def test_no_model_available_or_uncertain_is_unresolved_not_a_negative(self):
        row = rule("11111111", {"moment": "Before closing a message to Joe"})
        for judge in (None, lambda *_: {"model": "wrong", "answers": {}},
                      lambda *_: {"model": "jev-1.13.0", "answers": {"bind_11111111": {"type": "choice", "choice": "uncertain", "confidence": 0.9}}}):
            result = select_rules(catalog(row), dict(task="Reply"), judge=judge)
            self.assertEqual(result["rules"], [])
            self.assertEqual(result["unresolved"][0]["rule_id"], row["id"])
            self.assertFalse(result["complete"])

    def test_malformed_choice_is_explicitly_unresolved(self):
        row = rule("11111111", {"moment": "Before replying"})
        for choice in ([], {}):
            with self.subTest(choice=choice):
                result = select_rules(catalog(row), {}, judge=lambda *_: {
                    "model": "jev-1.13.0", "answers": {"bind_11111111": {
                        "type": "choice", "choice": choice, "confidence": 0.99}}})
                self.assertEqual(result["rules"], [])
                self.assertEqual(result["unresolved"], [{
                    "rule_id": row["id"], "reason": "moment_judgment_uncertain"}])
                self.assertFalse(result["complete"])

    def test_personal_rules_cannot_cross_sponsors(self):
        row = rule("11111111", {"tools": ["Bash"]})
        row["personal_to"] = "dell"
        with self.assertRaises(ValueError):
            select_rules(catalog(row), dict(tool_name="Bash"))

    def test_existing_specific_context_scopes_are_judged_and_generic_scopes_are_defects(self):
        row = rule("11111111", {"applies_to": "all attended CARR and DoctorCRE review work"})
        def judged(choice):
            return lambda *_: {"model": "jev-1.13.0", "answers": {"bind_11111111": {"type": "choice", "choice": choice, "confidence": 0.99}}}
        result = select_rules(catalog(row), dict(task="Review the changed code"), judge=judged("applies"))
        self.assertEqual(result["rules"], [row])
        row["scope"] = {"context": "development, all sessions"}
        result = select_rules(catalog(row), dict(task="Review the changed code"), judge=judged("scope_defect"))
        self.assertEqual(result["rules"], [])
        self.assertEqual(result["data_defects"], [{"rule_id": row["id"], "reason": "unusable_scope"}])

    def test_store_is_read_again_after_task_change_compaction_and_outage(self):
        calls = []
        source = catalog(rule("11111111", {"tools": ["Bash"]}))
        def runner(command, **kwargs):
            calls.append(command)
            if len(calls) == 2:
                return subprocess.CompletedProcess(command, 1, "", "credential must not escape")
            return subprocess.CompletedProcess(command, 0, json.dumps(source), "")
        event = dict(hook_event_name="PreToolUse", tool_name="Bash")
        self.assertEqual(len(lookup(event, runner=runner)["rules"]), 1)
        with self.assertRaisesRegex(RuntimeError, "scope_catalog_unavailable"):
            lookup(event, runner=runner)
        self.assertEqual(lookup(dict(hook_event_name="PreCompact"), runner=runner)["rules"], [])
        self.assertEqual(lookup(event, runner=runner)["rules"], source["rules"])
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(json.loads(call[-1]) == {"detail": "scopes"} for call in calls))

    def test_audited_moments_preserve_all_scopes_without_a_top_k_cutoff(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/rule-delivery-eval/scope-backfills.json").read_text())
        self.assertEqual(len(fixture["scopes"]), 211)  # retired 1fcaa63a removed
        source = catalog(*(rule(rid, scope) for rid, scope in fixture["scopes"].items()))
        seen = {}
        def judge(state, questions):
            seen.update(state["rules"])
            return {"model": "jev-1.13.0", "answers": {key: {"type": "choice", "choice": "not_applicable", "confidence": 0.99} for key in questions}}
        result = select_rules(source, dict(hook_event_name="Stop", task="Close this turn"), judge=judge)
        self.assertTrue(result["complete"])
        self.assertEqual({rid: row["scope"] for rid, row in seen.items()}, fixture["scopes"])

    def test_oversized_moment_is_reported_and_exact_text_is_never_truncated(self):
        row = rule("11111111", {"moment": "Before replying"})
        row["statement"] = "x" * 100000
        result = select_rules(catalog(row), dict(hook_event_name="Stop"))
        self.assertEqual(result["unresolved"], [{"rule_id": row["id"], "reason": "moment_input_too_large"}])
        row["scope"] = {"tools": ["Bash"]}
        result = select_rules(catalog(row), dict(tool_name="Bash"))
        self.assertEqual(result["rules"], [row])

    def test_read_paths_are_not_write_moments_and_patch_move_paths_bind(self):
        row = rule("11111111", {"path_globs": ["*.md"]})
        self.assertEqual(select_rules(catalog(row), dict(tool_name="Read", tool_input=dict(file_path="a.md")))["rules"], [])
        patch = "*** Begin Patch\n*** Update File: a.py\n*** Move to: a.md\n*** End Patch"
        self.assertEqual(select_rules(catalog(row), dict(tool_name="apply_patch", tool_input=dict(command=patch)))["rules"], [row])

    def test_moment_answers_cannot_be_mistyped_or_low_confidence(self):
        row = rule("11111111", {"moment": "Before replying"})
        for value in ({"type": "noul", "choice": "applies", "confidence": 0.99},
                      {"type": "choice", "choice": "not_applicable", "confidence": 0.7}):
            result = select_rules(catalog(row), {}, judge=lambda *_: {"model": "jev-1.13.0", "answers": {"bind_11111111": value}})
            self.assertFalse(result["complete"])
            self.assertEqual(result["rules"], [])

    def test_structural_scopes_preserve_routes_on_frozen_tool_calls(self):
        repo = Path(__file__).resolve().parent.parent
        fixture = json.loads((repo / 'ops/fixtures/rule-scope-compatibility.v1.json').read_text())
        source = catalog(*(rule(rid, scope) for rid, scope in fixture['structural_scopes'].items()))
        for entry in fixture['structural_calls']:
            result = select_rules(source, dict(hook_event_name='PreToolUse', **entry['call']))
            self.assertEqual([r['id'] for r in result['rules']], entry['ids'])
            self.assertTrue(result['complete'])
        self.assertEqual(len(fixture['structural_calls']), 418)


if __name__ == '__main__':
    unittest.main()
