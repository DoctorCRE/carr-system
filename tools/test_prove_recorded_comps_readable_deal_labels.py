"""Exercise the recorded comps proof CLI with synthetic records and release sources."""
import contextlib
import http.server
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-recorded-comps-readable-deal-labels.py'
SHA = 'a' * 40
ID = '11111111-1111-4111-8111-111111111111'
TABLE = {'type': 'table.v1', 'id': 'comps', 'columns': [
    {'key': 'deal', 'label': 'Deal', 'type': 'text'},
    {'key': 'proposed_on', 'label': 'Proposed on', 'type': 'date'},
    {'key': 'rent', 'label': 'Proposed rent ($/SF/year)', 'type': 'number'}],
    'rows': [{'deal': {'value': 'Synthetic Harbor', 'source': {'record_id': ID}},
              'proposed_on': {'value': None}, 'rent': {'value': 28.5, 'source': {'record_id': ID}}}]}


class ProofTest(unittest.TestCase):
    def invoke(self, mode='pass'):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if self.path == '/release':
                    value = {'env': {'value': 'production'}, 'git_sha': {'value': SHA}, 'worker_version': {'timestamp': '2026-11-01T00:00:00Z'}}
                    if mode == 'identity':
                        value['git_sha']['value'] = 'main'
                    body = json.dumps(value).encode()
                else:
                    prefix = f'/DoctorCRE/carr-system/{SHA}/mcp-server/'
                    if not self.path.startswith(prefix):
                        self.send_error(404)
                        return
                    name = self.path[len(prefix):]
                    if name not in {'src/answer-blocks.js', 'src/answer-blocks.v1.schema.json', 'package-lock.json'}:
                        self.send_error(404)
                        return
                    body = (ROOT / 'mcp-server' / name).read_bytes()
                    if mode == 'producer' and name == 'src/answer-blocks.js':
                        body = body.replace(b'value: row.deal_name', b'value: row.deal_id')
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            folder = Path(temp)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            stack.callback(server.server_close)
            stack.callback(server.shutdown)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            table = json.loads(json.dumps(TABLE))
            if mode == 'uuid':
                table['rows'][0]['deal']['value'] = ID
            turns = [] if mode == 'absent' else [{'at': '2026-10-01T00:00:00Z' if mode == 'old' else '2026-11-02T00:00:00Z', 'answer_blocks': [table]}]
            responses = {'list-doc-conversations': {'conversations': [{'id': ID}]}, 'read-doc-conversation': {'turns': turns}, 'get-deal-room': {'name': 'Different' if mode == 'name' else 'Synthetic Harbor'}}
            wrapper = folder / 'call'
            wrapper.write_text('#!/usr/bin/env python3\nimport json,sys\n' + f'responses={responses!r}\n' + "args=json.loads(sys.argv[3])\nif sys.argv[2]=='list-doc-conversations': assert args=={'limit':10}\nif sys.argv[2]=='read-doc-conversation': assert args['limit']==200\nprint(json.dumps(responses[sys.argv[2]]))\n")
            wrapper.chmod(0o700)
            origin = f'http://127.0.0.1:{server.server_port}'
            harness = '''import runpy,sys,urllib.request
original=urllib.request.OpenerDirector.open
origin=sys.argv[2]
def opened(self,url,*args,**kwargs):
 if isinstance(url,str) and url.startswith('https://raw.githubusercontent.com/'):
  url=origin+url.removeprefix('https://raw.githubusercontent.com')
 return original(self,url,*args,**kwargs)
urllib.request.OpenerDirector.open=opened
sys.argv=[sys.argv[1],'--origin',sys.argv[2],'--call-wrapper',sys.argv[3],'--timeout-seconds','30']
runpy.run_path(sys.argv[0],run_name='__main__')
'''
            result = subprocess.run(['python3', '-c', harness, str(RUNNER), origin, str(wrapper)], capture_output=True, text=True, timeout=35)
            self.assertLessEqual(len(requests), 4)
            return result

    def test_pass(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['natural_read'], 'passed')

    def test_natural_unavailable(self):
        for mode in ('absent', 'old', 'identity'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_contradictions(self):
        for mode in ('uuid', 'name', 'producer'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_deadline_flag(self):
        result = subprocess.run(['python3', str(RUNNER), '--origin', 'http://127.0.0.1', '--call-wrapper', '/missing', '--timeout-seconds', '31'], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
