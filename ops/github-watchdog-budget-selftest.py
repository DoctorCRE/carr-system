#!/usr/bin/env python3
"""Drive the tracked watchdog's real CLI path with synthetic provider replies."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import job_watchdog as watchdog


class WatchdogBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.counter = self.root / 'calls.jsonl'
        self.state = self.root / 'budget.json'
        self.gh = self.root / 'fake-gh'
        (self.root / 'gh').symlink_to(self.gh)
        self.config = {'thresholds': {'command_timeout_seconds': 10}}
        self.env = patch.dict(os.environ, GH_LIMITER_REAL=str(self.gh),
                              CARR_GITHUB_READ_BUDGET=str(self.state),
                              PATH=str(self.root) + os.pathsep + os.environ.get('PATH', ''))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def provider(self, reply):
        self.gh.write_text('#!/usr/bin/env python3\nimport json,sys,time\nfrom pathlib import Path\n'
            f'with Path({str(self.counter)!r}).open("a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n'
            + reply)
        self.gh.chmod(0o700)

    def test_provider_hold_stops_later_watchdog_reads_and_diagnostic_probes(self):
        self.provider('print("HTTP/2.0 403\\nX-RateLimit-Remaining: 0\\nX-RateLimit-Reset: "'
            '+str(int(time.time())+120)+"\\n\\n{}")\n'
            'print("gh: API rate limit exceeded (HTTP 403)",file=sys.stderr)\nsys.exit(1)\n')
        for attempt in range(2):
            with self.assertRaises(RuntimeError) as error:
                watchdog.command(['gh', 'api', 'repos/synthetic/fixture/pulls'], self.config)
            self.assertRegex(str(error.exception), watchdog.RATE_LIMIT)
            self.assertEqual(watchdog.rate_limit_reason(self.config, error.exception), str(error.exception))
            if attempt == 0:
                state = self.state.read_bytes()
                self.assertIn('retry at', str(error.exception))
            else:
                self.assertIn('CARR_GITHUB_LOCAL_HOLD:', str(error.exception))
                self.assertEqual(self.state.read_bytes(), state)
        self.assertEqual(len(self.counter.read_text().splitlines()), 1)

    def test_watchdog_slurp_reads_individual_pages_without_path_changes(self):
        self.provider('from urllib.parse import urlsplit,parse_qs\n'
            'page=parse_qs(urlsplit(sys.argv[2]).query)["page"][0]\n'
            'print("HTTP/2.0 200\\nLink: <https://api.github.com/x?page=2>; rel=\\\"next\\\"\\n\\n[{\\\"id\\\":1}]" '
            'if page=="1" else "HTTP/2.0 200\\nX-RateLimit-Remaining: 20\\n\\n[{\\\"id\\\":2}]")\n')
        result = watchdog.command(['gh', 'api', '--paginate', '--slurp', 'repos/synthetic/fixture/branches'], self.config)
        self.assertEqual(json.loads(result), [[{'id': 1}], [{'id': 2}]])
        calls = [json.loads(line) for line in self.counter.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('--paginate' not in call for call in calls))

    def test_missing_cli_remains_an_environment_finding(self):
        import subprocess
        with patch.object(watchdog.subprocess, 'run', return_value=subprocess.CompletedProcess([], 127, '', 'GitHub CLI unavailable')):
            with self.assertRaises(watchdog.MissingTool):
                watchdog.command(['gh', 'api', 'x'], self.config)


if __name__ == '__main__':
    unittest.main()
