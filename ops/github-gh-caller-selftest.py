#!/usr/bin/env python3
"""Actual GitHub callers through a synthetic native CLI; no provider access."""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.github_reader import GitHubReader, GitHubUnreadable
from lib.github_rate_limit import GitHubReadBudget


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


class Callers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.records = self.root / 'requests.jsonl'
        self.real = self.root / 'provider'
        (self.root / 'gh').symlink_to(ROOT / 'ops/github-gh.py')
        self.env = patch.dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
            GH_LIMITER_REAL=str(self.real), GH_LIMITER_DIR=str(self.root / 'legacy'),
            CARR_GITHUB_READ_BUDGET=str(self.root / 'state.json'))
        self.env.start()
        self.flakes = module('caller_flakes', 'ops/ci-flakes.py')
        self.backup = module('caller_backup', 'ops/backup-workflow-status.py')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def provider(self, code):
        self.real.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\n'
            'payload=sys.stdin.read() if "--input" in sys.argv else None\n'
            f'with Path({str(self.records)!r}).open("a") as out: out.write(json.dumps({{"argv":sys.argv[1:],"input":payload}})+"\\n")\n'
            + code)
        self.real.chmod(0o700)

    def test_ci_flakes_post_and_backup_patch_keep_their_json_payloads(self):
        self.provider('print("HTTP/2.0 200\\nX-RateLimit-Remaining: 20\\n\\n"+json.dumps({"accepted":json.loads(payload) if payload else None}))\n')
        payload = {'title': 'fixture issue', 'body': 'fixture details'}
        self.assertEqual(self.flakes.gh_api('repos/synthetic/fixture/issues', payload), {'accepted': payload})
        body = {'status': 'completed', 'conclusion': 'success'}
        self.assertEqual(self.backup.api('repos/synthetic/fixture/check-runs/7', method='PATCH', body=body), {'accepted': body})
        rows = [json.loads(line) for line in self.records.read_text().splitlines()]
        self.assertEqual([json.loads(row['input']) for row in rows], [payload, body])
        self.assertEqual(len(rows), 2)

    def test_uncertain_write_is_sent_once_and_never_retried(self):
        self.provider('print("HTTP 502",file=sys.stderr)\nsys.exit(1)\n')
        with self.assertRaises(RuntimeError):
            self.flakes.gh_api('repos/synthetic/fixture/issues', {'title': 'fixture'})
        self.assertEqual(len(self.records.read_text().splitlines()), 1)

    def test_ci_flakes_binary_zip_is_byte_exact(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('fixture.log', 'fixture bytes \r\n')
        payload = data.getvalue() + b'\xff\x80\r\n\n'
        self.provider(f'sys.stdout.buffer.write(b"HTTP/2.0 200\\r\\nContent-Type: application/zip\\r\\n\\r\\n"+{payload!r})\n')
        self.assertEqual(self.flakes.gh_api('repos/synthetic/fixture/actions/runs/7/logs', binary=True), payload)

    def object_pages(self, key):
        self.provider('from urllib.parse import urlsplit,parse_qs\n'
            'page=int(parse_qs(urlsplit(sys.argv[2]).query)["page"][0])\n'
            'link="Link: <https://api.github.com/x?page=2>; rel=\\\"next\\\"\\n" if page==1 else ""\n'
            'print("HTTP/2.0 200\\n"+link+"X-RateLimit-Remaining: 20\\n\\n"+'
            f'json.dumps({{"total_count":2,{key!r}:[{{"id":page,"name":"ops/ci.sh --strict","conclusion":"failure"}}]}}))\n')

    def test_push_floor_telemetry_reads_every_workflow_run_object_page(self):
        self.object_pages('workflow_runs')
        telemetry = module('caller_telemetry', 'ops/push-floor-telemetry.py')
        rows, error = telemetry.runs_since('2026-10-07T00:00:00Z')
        self.assertIsNone(error)
        self.assertEqual([row['id'] for row in rows], [1, 2])

    def test_foundation_python_and_node_page_decoders_keep_object_pages(self):
        self.object_pages('check_runs')
        result = subprocess.run(['gh', 'api', '--paginate', 'repos/synthetic/fixture/commits/head/check-runs'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        pages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual([row['id'] for page in pages for row in page['check_runs']], [1, 2])
        node = subprocess.run(['node', '--input-type=module', '-e',
            'let raw="";for await(const part of process.stdin) raw+=part;'
            'const pages=raw.trim().split(/\\n(?=\\{)/).filter(Boolean).map(JSON.parse);'
            'console.log(JSON.stringify(pages.flatMap(page=>page.check_runs||[]).map(row=>row.id)));'],
            input=result.stdout, capture_output=True, text=True)
        self.assertEqual((node.returncode, json.loads(node.stdout)), (0, [1, 2]))

    def test_foundation_python_caller_keeps_exact_head_checks_across_pages(self):
        foundation = module('caller_foundation', 'ops/foundation-assurance-candidate-rehearsal.py')
        head = 'a' * 40
        checks = [{'id': i+1, 'name': name, 'head_sha': head, 'status': 'completed',
                   'conclusion': 'success', 'completed_at': '2026-10-07T00:00:00Z'}
                  for i, name in enumerate(foundation.CHECKS)]
        self.provider('from urllib.parse import urlsplit,parse_qs\n'
            'page=int(parse_qs(urlsplit(sys.argv[2]).query)["page"][0])\n'
            f'checks={checks!r}\n'
            'link="Link: <https://api.github.com/x?page=2>; rel=\\\"next\\\"\\n" if page==1 else ""\n'
            'print("HTTP/2.0 200\\n"+link+"X-RateLimit-Remaining: 20\\n\\n"+'
            'json.dumps({"total_count":len(checks),"check_runs":checks[:2] if page==1 else checks[2:]}))\n')
        rows = foundation.hosted_checks(head)
        self.assertEqual([row['name'] for row in rows], list(foundation.CHECKS))
        self.assertEqual([row['run_id'] for row in rows], [1, 2, 3])

    def test_object_jq_slurp_semantics_and_page_cap(self):
        self.object_pages('check_runs')
        for flags, expected in ((['--jq', '.check_runs[].id'], '1\n2\n'),
                                (['--slurp', '--jq', '.[].check_runs[].id'], '1\n2\n')):
            result = subprocess.run(['gh', 'api', 'x', '--paginate', *flags], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (0, expected), result.stderr)
        def response(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, 'HTTP/2.0 200\nLink: <https://api.github.com/x?page=2>; rel="next"\n\n{"check_runs":[]}', '')
        reader = GitHubReader(env={}, runner=response)
        with self.assertRaises(GitHubUnreadable) as error:
            reader.api('x', paginate=True, slurp=True, max_pages=1)
        self.assertEqual(error.exception.kind, 'pagination_limit')

    def test_legacy_hold_survives_inflight_old_shim_updates_without_renewal(self):
        directory = self.root / 'legacy'
        directory.mkdir()
        legacy = directory / 'cooldown'
        legacy.write_text('1000 api rate limit exceeded')
        now = [1100]
        budget = GitHubReadBudget({'GH_LIMITER_DIR': str(directory)}, path=self.root/'state.json', clock=lambda: now[0], spacing=0)
        calls = []
        def provider(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '{}', '')
        reader = GitHubReader(env={}, runner=provider, budget=budget)
        for instant, stamp in ((1100, 1000), (1901, 1850)):
            now[0] = instant
            legacy.write_text(f'{stamp} api rate limit exceeded')
            before = legacy.read_bytes()
            with self.assertRaises(GitHubUnreadable) as error:
                reader.api('x')
            self.assertEqual(error.exception.attempts, 0)
            self.assertEqual(legacy.read_bytes(), before)
        self.assertEqual(calls, [])
        now[0] = 2751
        self.assertEqual(reader.api('x'), {})
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
