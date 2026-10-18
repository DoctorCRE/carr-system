#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class BuilderAgentGateTests(unittest.TestCase):
    def launch(self, agent, prompt):
        settings = json.loads((REPO / '.claude/settings.json').read_text())
        command = next(hook['command'] for entry in settings['hooks']['PreToolUse']
                       if entry.get('matcher') == 'Agent' for hook in entry['hooks']
                       if 'builder-agent-gate.py' in hook['command'])
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                ['sh', '-c', command], text=True, capture_output=True, check=True,
                input=json.dumps({'tool_name': 'Agent', 'tool_input': {
                    'subagent_type': agent, 'prompt': prompt}}),
                env={**os.environ, 'HOME': home, 'CLAUDE_PROJECT_DIR': str(REPO)},
            )
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout else None

    def test_build_launch_requires_poteto_agent(self):
        for agent in ('general-purpose', 'claude'):
            for prompt in ('Write code to fix dispatch.', 'Writing code for the hook.',
                           'Open a PR for the fix.', 'Make a commit.', 'Create a branch.',
                           'Review dispatch, then write code and open a PR.',
                           'Review PR 42 and commit the changes.'):
                with self.subTest(agent=agent, prompt=prompt):
                    result = self.launch(agent, prompt)
                    self.assertIsNotNone(result)
                    self.assertEqual(result['permissionDecision'], 'deny')
                    self.assertIn('pstack:poteto-agent', result['permissionDecisionReason'])

    def test_read_only_and_poteto_launches_pass(self):
        for agent, prompt in (
            ('Explore', 'Find code for PR 42 on the repair branch.'),
            ('Plan', 'Plan writing code for the next commit.'),
            ('review', 'Review PR 42.'), ('search', 'Search branch history.'),
            ('general-purpose', 'Review PR 42.'),
            ('claude', 'Search for the commit that introduced the bug.'),
            ('general-purpose', 'Explain the dispatcher.'),
            ('pstack:poteto-agent', 'Write code and open a PR.'),
        ):
            with self.subTest(agent=agent, prompt=prompt):
                self.assertIsNone(self.launch(agent, prompt))


if __name__ == '__main__':
    unittest.main()
