"""Replay registered disposable-PostgreSQL responses through the actual proof CLI."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-email-next-step-closed-date.py'


class ProofCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / 'registered.json'
        result = subprocess.run(['node', '--test', '--test-name-pattern=email next-step proof fixtures',
                                 'mcp-server/test/email-deal-progress-postgres.test.mjs'], cwd=ROOT,
                                env={**os.environ, 'CARR_EMAIL_NEXT_STEP_FIXTURE': str(path)},
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.data = json.loads(path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @contextmanager
    def fixture(self, scenario):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                value = {'ok': True, 'env': {'value': 'production'},
                         'git_sha': {'value': ('b' if scenario == 'release-change' and len(requests) > 1 else 'a') * 40}}
                if scenario == 'bad-release':
                    value['ok'] = False
                self.wfile.write(json.dumps(value).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'data.json').write_text(json.dumps(self.data))
                wrapper = root / 'wrapper'
                wrapper.write_text('#!' + sys.executable + '\n' + '''import json,pathlib,sys,time
root=pathlib.Path(__file__).parent
data=json.loads((root/'data.json').read_text())
verb=sys.argv[2]
assert sys.argv[1]=='call'
assert sys.argv[3]=='-'
args=json.load(sys.stdin)
with (root/'calls.jsonl').open('a') as stream:
    stream.write(json.dumps([verb,args])+'\\n')
''' + f'scenario={scenario!r}\n' + '''
if scenario=='timeout': time.sleep(31)
if scenario=='transport': sys.exit(1)
if scenario=='auth':
    print('TOOL ERROR '+json.dumps({'error':'not_authenticated'}),file=sys.stderr)
    sys.exit(1)
if verb=='deal-room-board':
    assert args=={'workspace':'all'}
    value={**data['closedBoard'],'deals':[{'id':data['activeRoom']['id']}],
           'closed_deals':[{'id':data['deal']}], 'lost_deals':[]}
    if scenario=='unavailable': value.update(deals=[],closed_deals=[])
elif verb=='get-deal-room':
    assert set(args)=={'deal'}
    value=data['closedRoom'] if args['deal']==data['deal'] else data['activeRoom']
elif verb=='preview-email-next-step':
    assert set(args)=={'deal','activity_id','evidence_quote','text','next_date','owner'}
    room=data['closedRoom'] if args['deal']==data['deal'] else data['activeRoom']
    source=next(a for a in room['activities'] if a['id']==args['activity_id'])
    assert args['evidence_quote'] in source['detail']
    if args['deal']==data['deal']:
        if scenario=='contradiction':
            print(json.dumps(data['active']))
            sys.exit(0)
        print('local-verb identity -> synthetic\\nTOOL ERROR '+json.dumps(data['refusal']),file=sys.stderr)
        sys.exit(1)
    value=data['active']
    assert value['text']==args['text'] and value['next_date']==args['next_date']
else: raise AssertionError('write or unknown verb')
print(json.dumps(value))
''')
                wrapper.chmod(0o700)
                yield f'http://127.0.0.1:{server.server_port}', wrapper, root, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_scenarios(self):
        for scenario, expected in [('pass', 0), ('contradiction', 1), ('unavailable', 78),
                                   ('timeout', 78), ('release-change', 78), ('auth', 78),
                                   ('transport', 78), ('bad-release', 78)]:
            with self.subTest(scenario=scenario), self.fixture(scenario) as (origin, wrapper, root, requests):
                result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin,
                                         '--call-wrapper', str(wrapper), '--timeout-seconds',
                                         '30'],
                                        capture_output=True, text=True, timeout=35)
                self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt['schema'], 'email-next-step-closed-date-proof.v1')
                self.assertLessEqual(receipt['requests'], 24)
                self.assertLessEqual(receipt['inspected_records'], 10)
                self.assertNotIn(self.data['deal'], result.stdout)
                self.assertNotIn('floor plan', result.stdout)
                if expected == 0:
                    self.assertEqual(receipt['assertions'], ['closed_date_preview_refused',
                                                           'active_preview_proposed', 'release_identity_stable'])
                    self.assertEqual(requests, ['/release', '/release'])
                if scenario == 'release-change':
                    self.assertEqual(receipt['reason'], 'release_changed')
                print(json.dumps({'scenario': scenario, 'exit': result.returncode, 'receipt': receipt}))

    def test_cli_contract(self):
        result = subprocess.run([sys.executable, str(RUNNER), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        for flag in ('--origin', '--call-wrapper', '--timeout-seconds'):
            self.assertIn(flag, result.stdout)
        result = subprocess.run([sys.executable, str(RUNNER), '--origin', 'http://example.test',
                                 '--call-wrapper', '/nonexistent'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
