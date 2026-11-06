"""Exercise the recorded comps proof CLI with synthetic records and release sources."""
import contextlib
import http.server
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from proof_release_fixture import ReleaseFixtureHandler

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
    def invoke(self, mode='pass', timeout_seconds=30):
        requests = []
        class Handler(ReleaseFixtureHandler):
            def release_GET(self):
                requests.append(self.path)
                if (self.headers.get('User-Agent') != 'doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)'
                        or self.headers.get('x-doctorcre-release-reader') != '1'):
                    self.send_error(403)
                    return
                if mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', '/forbidden')
                    self.end_headers()
                    return
                if mode == 'http_error':
                    self.send_error(503)
                    return
                if self.path == '/release':
                    value = json.loads(json.dumps(fixture['release']))
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
                if mode == 'oversized':
                    body += b' ' * 1048576
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
            fixture_script = r"""
import {docConversationTools} from './mcp-server/src/doc-conversation.js';
import {TOOLS} from './mcp-server/src/tools.js';
import {buildRelease} from './mcp-server/src/release.js';
import {validateAnswerBlocks} from './mcp-server/src/answer-blocks.js';
const input=JSON.parse(process.argv[1]);
class ToolError extends Error {}
const docs=docConversationTools({ToolError});
for(const turn of input.turns) validateAnswerBlocks(turn.answer_blocks,ToolError);
const client={query:async(sql,args)=>{
 if(sql.includes('ops.list_doc_conversations')) return {rows:[{listed:{ok:true,conversations:[{id:input.id}]}}]};
 if(sql.includes('ops.doc_conversation_facts')) return {rows:[{facts:{ok:true,identity:{id:input.id},turns:input.turns}}]};
 if(sql.includes('v_ref_index')) return {rows:[{subject_type:'deal',subject_id:input.id}]};
 if(sql.includes('from v_deal_room_board b')) return {rows:[{id:input.id,name:input.name}]};
 return {rows:[]};
}};
const actor={id:input.id};
const responses={
 'list-doc-conversations':await docs['list-doc-conversations'].handler(client,actor,{limit:10}),
 'read-doc-conversation':await docs['read-doc-conversation'].handler(client,actor,{conversation_id:input.id,limit:200}),
 'get-deal-room':await TOOLS['get-deal-room'].handler(client,actor,{deal:input.id}),
};
const release=await buildRelease({env:{CARR_ENV:'production',GIT_SHA:'a'.repeat(40),CF_VERSION_METADATA:{id:'fixture',timestamp:'2026-11-01T00:00:00Z'}},sql:async()=>[],verbCount:1});
console.log(JSON.stringify({responses,release}));
"""
            generated = subprocess.run(['node', '--input-type=module', '-e', fixture_script,
                                        json.dumps({'id': ID, 'turns': turns, 'name': 'Different' if mode == 'name' else 'Synthetic Harbor'})],
                                       cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            fixture = json.loads(generated.stdout)
            responses = fixture['responses']
            wrapper = folder / 'call'
            wrapper.write_text('#!/usr/bin/env python3\nimport json,sys\n' + f'responses={responses!r}\n' + "args=json.loads(sys.argv[3])\nif sys.argv[2]=='list-doc-conversations': assert args=={'limit':10}\nif sys.argv[2]=='read-doc-conversation': assert args['limit']==200\nprint(json.dumps(responses[sys.argv[2]]))\n")
            if mode in ('timeout', 'wrapper_error'):
                child = ("from pathlib import Path; import time; "
                         f"Path({str(folder / 'started')!r}).touch(); "
                         "time.sleep(3); "
                         f"Path({str(folder / 'escaped')!r}).touch()")
                wrapper.write_text(
                    '#!/usr/bin/env python3\nimport subprocess,sys,time\n'
                    + f'child=subprocess.Popen([sys.executable,"-c",{child!r}])\n'
                    + f'started={str(folder / "started")!r}\n'
                    + 'from pathlib import Path\n'
                    + 'while not Path(started).exists(): time.sleep(0.01)\n'
                    + ('sys.exit(3)\n' if mode == 'wrapper_error' else 'child.wait()\n'))
            wrapper.chmod(0o700)
            origin = f'http://127.0.0.1:{server.server_port}'
            harness = '''import runpy,sys,urllib.request
original=urllib.request.OpenerDirector.open
origin=sys.argv[2]
def opened(self,url,*args,**kwargs):
 if isinstance(url,urllib.request.Request) and url.full_url.startswith('https://raw.githubusercontent.com/'):
  url=urllib.request.Request(origin+url.full_url.removeprefix('https://raw.githubusercontent.com'),headers=dict(url.header_items()),method=url.get_method())
 return original(self,url,*args,**kwargs)
urllib.request.OpenerDirector.open=opened
sys.argv=[sys.argv[1],'--origin',sys.argv[2],'--call-wrapper',sys.argv[3],'--timeout-seconds',sys.argv[4]]
runpy.run_path(sys.argv[0],run_name='__main__')
'''
            result = subprocess.run(['python3', '-c', harness, str(RUNNER), origin, str(wrapper), str(timeout_seconds)], capture_output=True, text=True, timeout=35)
            if mode in ('timeout', 'wrapper_error'):
                self.assertTrue((folder / 'started').exists(), result.stdout + result.stderr)
                time.sleep(3.2)
                self.assertFalse((folder / 'escaped').exists(),
                                 'wrapper descendant performed work after proof exit')
            self.assertLessEqual(len(requests), 4, requests)
            return result

    def test_pass(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['natural_read'], 'passed')

    def test_http_refusals(self):
        for mode in ('redirect', 'http_error', 'oversized'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

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
                expected = {'uuid': 'natural.deal-readable', 'name': 'natural.canonical-deal-name', 'producer': 'synthetic.deal-name'}[mode]
                self.assertEqual(json.loads(result.stdout)['predicate'], expected)

    def test_named_table_predicates(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('comps_proof', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        cases = [
            ('natural.deal-column', lambda t: t['columns'][0].update(label='Wrong')),
            ('natural.proposed-on-column', lambda t: t['columns'][1].update(type='text')),
            ('natural.rent-column', lambda t: t['columns'][2].update(label='Wrong')),
            ('natural.row-budget', lambda t: t.update(rows=[])),
            ('natural.deal-readable', lambda t: t['rows'][0]['deal'].update(value='')),
            ('natural.deal-readable', lambda t: t['rows'][0]['deal'].update(value=ID)),
            ('natural.deal-source', lambda t: t['rows'][0]['deal']['source'].update(record_id='bad')),
            ('natural.rent-source', lambda t: t['rows'][0]['rent']['source'].update(record_id='bad')),
            ('natural.proposed-on-value', lambda t: t['rows'][0]['proposed_on'].update(value='bad')),
        ]
        for predicate, mutate in cases:
            with self.subTest(predicate=predicate):
                table = json.loads(json.dumps(TABLE))
                mutate(table)
                with self.assertRaises(runner.ProofFailed) as failure:
                    runner.check_table(table)
                self.assertEqual(str(failure.exception), predicate)

    def test_timeout_terminates_wrapper_descendants(self):
        result = self.invoke('timeout', timeout_seconds=2)
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_wrapper_error_terminates_descendants(self):
        result = self.invoke('wrapper_error')
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_deadline_flag(self):
        result = subprocess.run(['python3', str(RUNNER), '--origin', 'http://127.0.0.1', '--call-wrapper', '/missing', '--timeout-seconds', '31'], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
