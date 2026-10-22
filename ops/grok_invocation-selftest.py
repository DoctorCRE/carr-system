"""Native Grok hook lifecycle, without starting a model or changing user config."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from hooks import grok_invocation

class GrokBootTests(unittest.TestCase):

    def test_single_retrieval_is_exempt_but_workspace_and_prompt_flags_are_not(self):
        prefix = 'grok --model latest --reasoning-effort high --max-turns 20 --always-approve --sandbox '
        for suffix, expected in (
                ('read-only --output-format streaming-json --single explain', True),
                ('read-only --output-format streaming-json --print explain', True),
                ('workspace --output-format streaming-json --single explain --sandbox read-only', False),
                ('workspace --output-format streaming-json --single --print', False)):
            with self.subTest(suffix=suffix), patch.dict(os.environ, {grok_invocation.READ_ONLY_ENV: '1'}), \
                    patch.object(grok_invocation, '_grok_owner_command', return_value=prefix + suffix):
                self.assertEqual(grok_invocation.bounded_grok_read_only(), expected)

    def test_installed_native_image_is_an_authenticated_grok_owner(self):
        result = subprocess.CompletedProcess([], 0, '1 grok --single task', '')
        with patch.object(grok_invocation.subprocess, 'run', return_value=result), \
                patch.object(grok_invocation, '_process_image', return_value='/opt/grok/bin/grok-native'):
            self.assertTrue(grok_invocation.grok_session())



    def test_native_terminal_fetch_uses_the_same_strict_fetch_grammar(self):
        from lib.rule_boot_gate import classify
        command = str(REPO / 'run.sh') + ' call standing-context \'{"detail":"boot","page":1}\''
        self.assertEqual(classify('run_terminal_command', {'command': command}, str(REPO)), ('fetch', 1))
        self.assertEqual(classify('run_terminal_command', {'command': command + '; touch /tmp/effect'}, str(REPO))[0], 'other')


if __name__ == '__main__':
    unittest.main()
