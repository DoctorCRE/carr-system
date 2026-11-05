"""Exercise the proof CLI using only a synthetic wrapper and local HTTP origin."""
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

RUNNER = Path(__file__).with_name('prove-lead-contact-address-membership.py')


class ProofCliTests(unittest.TestCase):
    def run_cli(self, mode='normal', count=1, timeout=5):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = root / 'calls.jsonl'
            wrapper = root / 'wrapper'
            wrapper.write_text(f'''#!{sys.executable}
import json, sys, time
from pathlib import Path
verb, args = sys.argv[2], json.loads(sys.argv[3])
with Path({str(calls)!r}).open('a') as log: log.write(json.dumps([verb,args])+'\\n')
mode={mode!r}
if mode=='auth': print('private@example.test',file=sys.stderr);sys.exit(1)
if mode=='timeout': time.sleep(2)
if verb=='lead-stage-preview':
 print(json.dumps({{'contract':'lead-automation.v1','dry_run':True,'moves':[{{'lead_id':'lead-0','activity_id':'activity-0','evidence_ref':'local-mail:private','strength':'strong','status':'applied'}}] if mode=='contradiction' else []}}))
else:
 detail={{'id':args.get('lead_id'),'email':'first@example.test;second@example.test','correspondence':[{{'id':'activity-0','kind':'email_in','source':'local_mail','detail':{{'match':'unconfirmed' if mode=='contradiction' else 'exact','evidence_ref':'local-mail:private'}}}}], 'correspondence_page':{{'has_more':True,'next_cursor':{{'id':'cursor','occurred_at':'2026-01-01T00:00:00Z'}}}}}}
 print(json.dumps({{'schema_version':'lead-workspace.v1','leads':[{{'id':'lead-'+str(i)}} for i in range({count})], 'detail':detail if 'lead_id' in args else None}}))
''')
            wrapper.chmod(0o700)
            releases = []

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    releases.append(self.path)
                    sha = 'b' if mode == 'release-change' and len(releases) > 1 else 'a'
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({'git_sha': {'value': sha * 40}}).encode())

                def log_message(self, *_args):
                    pass

            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                result = subprocess.run([sys.executable, str(RUNNER), '--origin',
                    f'http://127.0.0.1:{server.server_port}', '--call-wrapper', str(wrapper),
                    '--timeout-seconds', str(timeout)], capture_output=True, text=True, timeout=10)
                logged = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
                return result, logged, releases
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_exact_metadata_without_captured_address_is_not_membership_proof(self):
        result, calls, releases = self.run_cli()
        self.assertEqual(result.returncode, 78, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['eligible_records'], 0)
        self.assertEqual(receipt['exact_activities'], 1)
        self.assertEqual(receipt['release_sha'], 'a' * 40)
        self.assertEqual(releases, ['/release', '/release'])
        self.assertEqual({v for v, _ in calls}, {'lead-board', 'lead-stage-preview'})
        self.assertNotIn('example.test', result.stdout + result.stderr)
        self.assertNotIn('local-mail:private', result.stdout + result.stderr)

    def test_empty_preview_and_board_never_pass(self):
        result, calls, _ = self.run_cli(count=0)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(json.loads(result.stdout)['detail_reads'], 0)

    def test_read_budget_and_two_page_cap(self):
        result, calls, _ = self.run_cli(count=30)
        self.assertEqual(result.returncode, 78)
        details = [args for verb, args in calls if verb == 'lead-board' and 'lead_id' in args]
        self.assertEqual(len(details), 20)
        self.assertTrue(all(sum(d['lead_id'] == x['lead_id'] for x in details) <= 2 for d in details))
        self.assertTrue(all(args.get('workspace') == 'leads' for verb, args in calls if verb == 'lead-board'))

    def test_preview_contradiction_fails(self):
        result, _, _ = self.run_cli('contradiction')
        self.assertEqual(result.returncode, 1)

    def test_authentication_and_timeout_fail_closed_without_retries(self):
        for mode in ('auth', 'timeout'):
            result, calls, _ = self.run_cli(mode, timeout=1)
            self.assertEqual(result.returncode, 78)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('private@example.test', result.stdout + result.stderr)

    def test_release_change_refuses_evidence(self):
        result, _, _ = self.run_cli('release-change')
        self.assertEqual(result.returncode, 78)
        self.assertEqual(json.loads(result.stdout)['reason'], 'release_changed')


if __name__ == '__main__':
    unittest.main()
