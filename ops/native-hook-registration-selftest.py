#!/usr/bin/env python3
"""Required native hook registrations survive deletion and alteration probes."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('safety_hooks', REPO / 'hooks/gate-integrity.py')
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class SafetyHooks(unittest.TestCase):
    def documents(self):
        return {client: json.loads((REPO / f'ops/config/{name}.json').read_text())
                for client, name in [('claude', 'hooks'), ('codex', 'codex-hooks')]}

    def test_every_registration_is_required_and_cannot_be_altered(self):
        counts = {}
        for client, original in self.documents().items():
            block = original.get('hooks', original)
            self.assertEqual(gate.check(original, client), [])
            counts[client] = 0
            for event, groups in block.items():
                for gi, group in enumerate(groups):
                    for hi, hook in enumerate(group.get('hooks', [])):
                        counts[client] += 1
                        name = hook['command'].split('/')[-1].strip(chr(34))
                        for mutation in ('delete', 'matcher', 'command', 'timeout', 'type', 'async', 'event'):
                            with self.subTest(client=client, event=event, hook=name, mutation=mutation):
                                document = copy.deepcopy(original)
                                hooks = document.get('hooks', document)
                                changed = hooks[event][gi]
                                if mutation == 'delete':
                                    del changed['hooks'][hi]
                                elif mutation == 'matcher':
                                    changed['matcher'] = 'NeverMatches'
                                elif mutation == 'event':
                                    hooks['WrongEvent'] = [hooks[event].pop(gi)]
                                elif mutation == 'async':
                                    changed['hooks'][hi]['async'] = True
                                else:
                                    changed['hooks'][hi][mutation] = 'altered'
                                errors = '\n'.join(gate.check(document, client))
                                self.assertIn(f'{client}: required safety hook is not installed:', errors)
                                self.assertIn(name, errors)
                                self.assertIn(event, errors)
        self.assertEqual(counts, {'claude': 16, 'codex': 7})

    def test_claude_uses_project_settings_and_ignores_user_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'hooks').symlink_to(REPO / 'hooks')
            settings = project / '.claude/settings.json'
            settings.parent.mkdir()
            settings.write_text(json.dumps(self.documents()['claude']))
            codex = project / '.codex'
            codex.mkdir()
            (codex / 'hooks.json').write_text(json.dumps(self.documents()['codex']))
            with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(project / 'absent'),
                                         'CODEX_HOME': str(codex)}), \
                    patch.object(gate, 'REPO', project), \
                    patch.object(gate, 'check_baseline', return_value=[]), \
                    patch.object(gate.sys, 'argv', ['gate-integrity.py']), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(), 0)
                settings.unlink()
                self.assertEqual(gate.main(), 1)

    def test_unrelated_hooks_and_group_order_are_preserved(self):
        for client, document in self.documents().items():
            hooks = document.get('hooks', document)
            for groups in hooks.values():
                groups.reverse()
            hooks.setdefault('Stop', []).append({'hooks': [{'command': '/usr/bin/true'}]})
            self.assertEqual(gate.check(document, client), [])

    def test_disabled_hooks_are_rejected(self):
        for client, document in self.documents().items():
            if 'hooks' not in document:
                document = {'hooks': document}
            document['disableAllHooks'] = True
            self.assertIn(f'{client}: hooks are disabled by disableAllHooks', gate.check(document, client))

    def test_missing_command(self):
        document = {'PreToolUse': [{'hooks': [{'command': 'python {{REPO}}/hooks/absent.py'}]}]}
        self.assertTrue(any('missing hook script:' in error for error in gate.check(document, 'codex')))


if __name__ == '__main__':
    unittest.main()
