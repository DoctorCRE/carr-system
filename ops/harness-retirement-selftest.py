#!/usr/bin/env python3
"""The installed harness keeps safety controls without startup ceremony."""
import json
import runpy
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class HarnessRetirement(unittest.TestCase):
    def test_native_hook_configurations_keep_safety_without_retired_hooks(self):
        retired = ('rule-boot-gate.py', 'delegation-gate.py', 'executor-tier-gate.py',
                   'claude-continuity-hook.py', 'codex-continuity-hook.py')
        for relative in ('ops/config/hooks.json', 'ops/config/codex-hooks.json',
                         '.claude/settings.json', 'claude-tree/settings/user.settings.json',
                         'claude-tree/settings/carr-ai-project.settings.json'):
            with self.subTest(source=relative):
                raw = (ROOT / relative).read_text()
                for script in retired:
                    self.assertFalse(script in raw, script)
        claude = (ROOT / 'ops/config/hooks.json').read_text()
        codex = (ROOT / 'ops/config/codex-hooks.json').read_text()
        for script in ('guard-unattended.py', 'record-home-gate.py', 'github-burst-guard.py',
                       'rule-pack-preuse-reselection.py', 'gate-integrity.py'):
            self.assertIn(script, claude)
            self.assertIn(script, codex)
        for script in ('canonical-edit-gate.py', 'weekend-quiet-gate.py',
                       'costar-lane-gate.py', 'model-floor-gate.py'):
            self.assertIn(script, claude)
        self.assertIn('Bash(git push --force:*)', (ROOT / '.claude/settings.json').read_text())

    def test_config_sync_removes_old_codex_continuity_hooks_and_preserves_other_projects(self):
        installer = runpy.run_path(str(ROOT / 'ops/config-as-code.py'))
        old = {'command': 'python /Users/booko/carr-system/ops/codex-continuity-hook.py'}
        other = {'command': 'python /other/project/keep.py'}
        live = {'hooks': {'SessionStart': [{'hooks': [old, other]}]}, 'keep': True}
        desired = json.loads((ROOT / 'ops/config/codex-hooks.json').read_text())
        actual = installer['merge_codex_carr_hooks'](live, desired)
        self.assertTrue(actual['keep'])
        commands = [h['command'] for groups in actual['hooks'].values()
                    for group in groups for h in group.get('hooks', [])]
        self.assertIn(other['command'], commands)
        self.assertNotIn(old['command'], commands)
        self.assertEqual(installer['merge_codex_carr_hooks'](actual, desired), actual)

    def test_control_catalog_compiles_native_guidance_without_retired_gates(self):
        catalog = runpy.run_path(str(ROOT / 'ops/sync_control_catalog.py'))
        controls = {row['control_key']: row for row in catalog['compile_catalog']()}
        self.assertEqual(controls['delegation_guidance']['enforcement_class'], 'surfacing')
        self.assertNotIn('delegation_tripwire', controls)
        self.assertNotIn('executor_tier', controls)

    def test_native_instructions_deliver_every_core_card(self):
        cards = json.loads((ROOT / 'ops/config/rule-cards.v1.json').read_text())['cards']
        core = [c for c in cards if c['placement'] == 'CORE']
        self.assertEqual(len(core), 12)
        for source in ('CLAUDE.md', 'AGENTS.md'):
            text = (ROOT / source).read_text()
            for card in core:
                self.assertTrue(card['wording'] in text, card['id'])


if __name__ == '__main__':
    unittest.main()
