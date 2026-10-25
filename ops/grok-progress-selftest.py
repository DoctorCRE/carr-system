#!/usr/bin/env python3
"""Exercise Grok progress limits with a synthetic CLI, without provider access."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/room-bridge'))
from grok_wire import run_request


class GrokProgressTests(unittest.TestCase):
    def fake_run(self, script, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / 'grok'
            cli.write_text(f'#!{sys.executable}\nimport json, os, sys, time\n' + script)
            cli.chmod(0o700)
            with patch.dict(os.environ, PATH=directory + os.pathsep + os.defpath):
                return run_request('Synthetic review', require_identity=False, **kwargs)

    end = "print(json.dumps({'type': 'end', 'stopReason': 'end_turn', 'modelUsage': {'grok-4.7-build': {'modelCalls': 1}}}), flush=True)\n"

    def test_turn_limit_is_only_sent_when_requested(self):
        for options, expected in (({}, False), ({'max_turns': 9}, True)):
            result = self.fake_run(
                "assert ('--max-turns' in sys.argv) == " + repr(expected) + "\n"
                + ("assert sys.argv[sys.argv.index('--max-turns') + 1] == '9'\n" if expected else '')
                + "print(json.dumps({'type': 'text', 'data': 'review'}), flush=True)\n"
                + self.end, **options)
            self.assertEqual((result['status'], result.get('result')), ('completed', 'review'))

    def test_slow_stream_completes_beyond_idle_window(self):
        result = self.fake_run(
            "sys.stderr.write('diagnostic' * 20000)\n"
            "for _ in range(6):\n"
            " print(json.dumps({'type': 'text', 'data': 'read '}), flush=True)\n"
            " time.sleep(0.25)\n" + self.end, idle_seconds=1)
        self.assertEqual((result['status'], result['result']), ('completed', 'read ' * 6))

    def test_silence_is_stalled(self):
        for script in ("time.sleep(10)\n", "os.close(1); time.sleep(10)\n",
                       "for _ in range(100):\n sys.stdout.write('x'); sys.stdout.flush(); time.sleep(0.05)\n"):
            started = time.monotonic()
            result = self.fake_run(script, idle_seconds=1)
            self.assertEqual((result['status'], result['code'], result['detail']),
                             ('stalled', 7, 'grok_cli_stalled'))
            self.assertEqual(result['receipt']['status'], 'stalled')
            self.assertLess(time.monotonic() - started, 5)


if __name__ == '__main__':
    unittest.main()
