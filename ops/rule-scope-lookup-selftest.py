"""Behavior at the store-backed lookup interface. No live store or model calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.rule_scope_lookup import lookup, select_rules
from lib.rule_delivery_preuse import digest
from ops.rule_scope_replay import FrozenSelection


def rule(rid, scope, **extra):
    return dict(id=rid, statement="Full binding text " + rid, scope=scope,
                personal_to=None, **extra)


def catalog(*rules):
    return dict(ok=True, schema="rule-scope-catalog/v1", corpus_digest="a" * 64,
                identity=dict(agent_principal_id="joe-local", runtime_principal="joe-local",
                              sponsoring_human_id="joe"), rules=list(rules))


class LookupTests(unittest.TestCase):
    def test_replay_excludes_only_confirmed_retired_structural_and_moment_rules(self):
        event = dict(hook_event_name='PreToolUse', tool_name='Bash', task='Synthetic replay')
        historical_ids = ['0f38532e', '11111111', '1fcaa63a', '204391be', '4a77c35d']
        fixture = {'structural_scopes': {rid: {'tools': ['Bash']}
                                        for rid in ('0f38532e', '11111111')},
                   'moment_decisions': {digest(event): historical_ids}}
        documents = {
            'ops/fixtures/rule-scope-compatibility.v1.json': fixture,
            'ops/config/rule-selection-corpus.v1.json': {
                'rules': [dict(id=rid, statement='Synthetic rule ' + rid) for rid in historical_ids]},
            'ops/config/rule-retirements.v1.json': {'schema': 'rule-retirements/v1',
                                                 'rules': {'0f38532e': '2026-10-17',
                                                           '1fcaa63a': '2026-10-16'}},
            'ops/fixtures/rule-delivery-eval/scope-backfills.json': {'scopes': {}}}
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            for relative, document in documents.items():
                target = root/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(document))
            replay = FrozenSelection(root)
            result = replay.select(event)
        self.assertTrue(result['complete'])
        self.assertEqual([row['id'] for row in result['rules']], ['11111111', '204391be', '4a77c35d'])
        self.assertEqual(replay.fixture, fixture)
        self.assertNotEqual([row['id'] for row in result['rules']],
                            replay.retirements.active(historical_ids + ['22222222']))

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


    def test_deterministic_selection_is_complete_without_unpicked_moments(self):
        known = rule("11111111", {"tools": ["Bash"]})
        moment = rule("22222222", {"moment": "Before reviewing code"})
        moment["statement"] = "x" * 100000
        result = select_rules(catalog(known, moment), dict(tool_name="Bash"))
        self.assertEqual(result["rules"], [known])
        self.assertEqual(result["unresolved"], [])
        self.assertTrue(result["complete"])
        self.assertEqual(result["selection_mode"], "deterministic")
        self.assertEqual(result["models"], [])


    def test_personal_rules_cannot_cross_sponsors(self):
        row = rule("11111111", {"tools": ["Bash"]})
        row["personal_to"] = "dell"
        with self.assertRaises(ValueError):
            select_rules(catalog(row), dict(tool_name="Bash"))


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


    def test_read_paths_are_not_write_moments_and_patch_move_paths_bind(self):
        row = rule("11111111", {"path_globs": ["*.md"]})
        self.assertEqual(select_rules(catalog(row), dict(tool_name="Read", tool_input=dict(file_path="a.md")))["rules"], [])
        patch = "*** Begin Patch\n*** Update File: a.py\n*** Move to: a.md\n*** End Patch"
        self.assertEqual(select_rules(catalog(row), dict(tool_name="apply_patch", tool_input=dict(command=patch)))["rules"], [row])


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
