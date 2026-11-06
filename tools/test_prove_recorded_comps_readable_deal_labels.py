"""Exercise the recorded comps proof CLI with synthetic records and release sources."""
import contextlib
import http.client
import http.server
import hashlib
import base64
import io
import tarfile
import json
import os
from pathlib import Path
import subprocess
import shutil
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
    def invoke(self, mode='pass', timeout_seconds=30, denied_reads=False, malformed_reader_headers=False, extra_marked_read=False):
        requests = []
        archive = io.BytesIO()
        package = ROOT / 'mcp-server/node_modules/@cfworker/json-schema'
        with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
            bundle.add(package, arcname='package')
        dependency = archive.getvalue()
        if mode in ('archive_path', 'archive_link', 'archive_size'):
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
                entry = tarfile.TarInfo('package/../../escaped' if mode == 'archive_path' else 'package/file')
                if mode == 'archive_link':
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = '/tmp/outside'
                if mode == 'archive_size':
                    entry.size = 1048577
                bundle.addfile(entry, io.BytesIO(b'x' * entry.size))
            dependency = archive.getvalue()
        if mode == 'archive_invalid':
            dependency = b'not a tarball'

        lock = json.loads((ROOT / 'mcp-server/package-lock.json').read_text())
        lock['packages']['node_modules/@cfworker/json-schema']['integrity'] = (
            'sha512-' + base64.b64encode(hashlib.sha512(dependency).digest()).decode())
        if mode == 'transitive':
            lock['packages']['node_modules/@cfworker/json-schema']['dependencies'] = {'unexpected': '1.0.0'}
        if mode == 'dependency_url':
            lock['packages']['node_modules/@cfworker/json-schema']['resolved'] = 'https://untrusted.invalid/archive.tgz'
        class Handler(ReleaseFixtureHandler):
            def release_GET(self):
                if (self.headers.get_all('User-Agent') != ['doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)']
                        or self.headers.get_all('x-doctorcre-release-reader') != ['1']):
                    self.send_error(403)
                    return
                requests.append(self.path)
                if mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', '/forbidden')
                    self.end_headers()
                    return
                if mode == 'http_error':
                    self.send_error(503)
                    return
                if self.path == '/@cfworker/json-schema/-/json-schema-4.1.1.tgz':
                    if mode == 'dependencies':
                        self.send_error(503)
                        return
                    body = dependency if mode != 'integrity' else b'corrupted'
                elif self.path == '/release':
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
                    body = (json.dumps(lock).encode() if name == 'package-lock.json'
                            else (ROOT / 'mcp-server' / name).read_bytes())
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
            if mode in ('uuid', 'optimized_uuid'):
                table['rows'][0]['deal']['value'] = ID
            turns = [] if mode in ('absent', 'dependencies') else [{'at': '2026-10-01T00:00:00Z' if mode == 'old' else '2026-11-02T00:00:00Z', 'answer_blocks': [table]}]
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
            if mode == 'shape':
                del responses['get-deal-room']['name']
            if mode == 'envelope':
                responses = {verb: {'content': [{'type': 'text', 'text': json.dumps(value)}]} for verb, value in responses.items()}
            wrapper = folder / 'call'
            wrapper.write_text('#!/usr/bin/env python3\nimport json,sys\n' + f'responses={responses!r}\n' + "args=json.loads(sys.argv[3])\nif sys.argv[2]=='list-doc-conversations': assert args=={'limit':10}\nif sys.argv[2]=='read-doc-conversation': assert args['limit']==200\nprint(json.dumps(responses[sys.argv[2]]))\n")
            if mode in ('timeout', 'wrapper_error', 'deadline_disarm'):
                child = ("from pathlib import Path; import os, signal; "
                         f"ready=Path({str(folder / 'started')!r}); "
                         "pending=ready.with_suffix('.tmp'); pending.write_text(str(os.getpid())); "
                         "pending.replace(ready); signal.pause()")
                wrapper.write_text(
                    '#!/usr/bin/env python3\nimport os,signal,subprocess,sys,time\n'
                    + f'child=subprocess.Popen([sys.executable,"-c",{child!r}])\n'
                    + f'started={str(folder / "started")!r}\n'
                    + 'from pathlib import Path\n'
                    + 'while not Path(started).exists(): time.sleep(0.01)\n'
                    + ('sys.exit(3)\n' if mode in ('wrapper_error', 'deadline_disarm') else 'os.kill(os.getppid(), signal.SIGALRM)\nchild.wait()\n'))
            wrapper.chmod(0o700)
            origin = f'http://127.0.0.1:{server.server_port}'
            harness = '''import runpy,sys,urllib.request
original=urllib.request.OpenerDirector.open
origin=sys.argv[2]
extra_marked_read=sys.argv[5]=='True'
def opened(self,url,*args,**kwargs):
 if isinstance(url,urllib.request.Request) and url.full_url.startswith('https://registry.npmjs.org/'):
  url=urllib.request.Request(origin+url.full_url.removeprefix('https://registry.npmjs.org'),headers=dict(url.header_items()),method=url.get_method())
 if isinstance(url,urllib.request.Request) and url.full_url.startswith('https://raw.githubusercontent.com/'):
  url=urllib.request.Request(origin+url.full_url.removeprefix('https://raw.githubusercontent.com'),headers=dict(url.header_items()),method=url.get_method())
 return original(self,url,*args,**kwargs)
urllib.request.OpenerDirector.open=opened
sys.argv=[sys.argv[1],'--origin',sys.argv[2],'--call-wrapper',sys.argv[3],'--timeout-seconds',sys.argv[4]]
try:
 runpy.run_path(sys.argv[0],run_name='__main__')
finally:
 if extra_marked_read:
  from lib.release_reader import read
  response=read(origin+'/release',timeout=5)
  assert response.status==200
'''
            if mode in ('stalled_evaluation', 'late_launch'):
                child = ('from pathlib import Path; import os,time; '
                         f'Path({str(folder / "pid")!r}).write_text(str(os.getpid())); time.sleep(8)')
                harness = ('import subprocess,time\nreal_popen=subprocess.Popen\n'
                           'def launch(command,*args,**kwargs):\n'
                           f' if command[0]=="node": command=[sys.executable,"-c",{child!r}]\n'
                           ' process=real_popen(command,*args,**kwargs)\n'
                           + (' if command[0]==sys.executable: time.sleep(3)\n' if mode == 'late_launch' else '')
                           + ' return process\nsubprocess.Popen=launch\n') + harness
            if mode == 'late_directory':
                harness = ('import tempfile,time\nreal_mkdtemp=tempfile.mkdtemp\n'
                           'def acquire(*args,**kwargs):\n'
                           ' folder=real_mkdtemp(*args,**kwargs)\n'
                           ' if (kwargs.get("prefix") or (args[1] if len(args)>1 else None))=="comps-proof-": time.sleep(3)\n'
                           ' return folder\ntempfile.mkdtemp=acquire\n') + harness
            if mode == 'late_cleanup':
                harness = ('import tempfile,time\nreal_cleanup=tempfile.TemporaryDirectory.cleanup\n'
                           'def cleanup(self):\n'
                           ' if "comps-proof-" in self.name: time.sleep(3)\n'
                           ' return real_cleanup(self)\ntempfile.TemporaryDirectory.cleanup=cleanup\n') + harness
            if mode == 'deadline_disarm':
                harness = ('import signal,os\nreal_timer=signal.setitimer\n'
                           'def set_timer(which,seconds,*args):\n'
                           ' if seconds==0: os.kill(os.getpid(),signal.SIGALRM)\n'
                           ' return real_timer(which,seconds,*args)\nsignal.setitimer=set_timer\n') + harness
            runner_path = RUNNER
            if mode in ('dependencies', 'clean_runtime'):
                runtime = folder / 'runtime'
                for name in ('tools/prove-recorded-comps-readable-deal-labels.py', 'lib/release_reader.py', 'lib/release-reader-marker.json', 'mcp-server/package-lock.json'):
                    target = runtime / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, target)
                (runtime / 'mcp-server/package-lock.json').write_text(json.dumps(lock))
                runner_path = runtime / RUNNER.relative_to(ROOT)
            scratch = folder / 'proof-scratch'
            scratch.mkdir()
            environment = dict(os.environ, TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch))
            result = subprocess.run(['python3', *(['-O'] if mode == 'optimized_uuid' else []), '-c', harness, str(runner_path), origin, str(wrapper), str(timeout_seconds), str(extra_marked_read)], capture_output=True, text=True, timeout=35, env=environment)
            self.assertEqual(list(scratch.iterdir()), [], 'proof left temporary fixtures')
            if denied_reads:
                for path in ('/', '/favicon.ico'):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    with contextlib.closing(connection):
                        connection.request('GET', path)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 403)
                        response.read()
            if malformed_reader_headers:
                agent = 'doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)'
                for headers in (
                    [('User-Agent', agent), ('x-doctorcre-release-reader', 'wrong')],
                    [('User-Agent', 'browser'), ('x-doctorcre-release-reader', '1')],
                    [('User-Agent', agent), ('x-doctorcre-release-reader', '1'),
                     ('x-doctorcre-release-reader', '1')],
                    [('User-Agent', agent), ('User-Agent', 'browser'),
                     ('x-doctorcre-release-reader', '1')],
                ):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    with contextlib.closing(connection):
                        connection.putrequest('GET', f'/DoctorCRE/carr-system/{SHA}/mcp-server/package-lock.json')
                        for name, value in headers:
                            connection.putheader(name, value)
                        connection.endheaders()
                        response = connection.getresponse()
                        self.assertEqual(response.status, 403)
                        response.read()
            if mode in ('stalled_evaluation', 'late_launch'):
                pid = int((folder / 'pid').read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            if mode in ('timeout', 'wrapper_error', 'deadline_disarm'):
                self.assertTrue((folder / 'started').exists(), result.stdout + result.stderr)
                pid = int((folder / 'started').read_text())
                until = time.monotonic() + 30
                while True:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    self.assertLess(time.monotonic(), until, 'owned wrapper descendant survived cleanup')
                    threading.Event().wait(0.01)
            if extra_marked_read:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            allowed_paths = {'/release', '/@cfworker/json-schema/-/json-schema-4.1.1.tgz', *(f'/DoctorCRE/carr-system/{SHA}/mcp-server/{name}'
                                         for name in ('src/answer-blocks.js', 'src/answer-blocks.v1.schema.json', 'package-lock.json'))}
            self.assertTrue(set(requests) <= allowed_paths, requests)
            self.assertLessEqual(len(requests), 5, requests)
            if mode == 'pass':
                self.assertEqual(len(requests), 5, requests)
            return result

    def test_pass(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['natural_read'], 'passed')

    def test_denied_reads_do_not_consume_proof_budget(self):
        for mode in ('pass', 'timeout'):
            with self.subTest(mode=mode):
                result = self.invoke(mode, denied_reads=True)
                self.assertEqual(result.returncode, 0 if mode == 'pass' else 78,
                                 result.stdout + result.stderr)

    def test_malformed_reader_headers_are_rejected_before_accounting(self):
        result = self.invoke(malformed_reader_headers=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fifth_marked_read_exceeds_proof_budget(self):
        with self.assertRaisesRegex(AssertionError, '6 not less than or equal to 5'):
            self.invoke(extra_marked_read=True)

    def test_http_refusals(self):
        for mode in ('redirect', 'http_error', 'oversized'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['predicate'])

    def test_natural_unavailable(self):
        for mode in ('absent', 'old', 'identity'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['predicate'])

    def test_contradictions(self):
        for mode in ('uuid', 'name', 'producer'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                expected = {'uuid': 'natural.deal-readable', 'name': 'natural.canonical-deal-name', 'producer': 'synthetic.deal-name'}[mode]
                self.assertEqual(json.loads(result.stdout)['predicate'], expected)

    def test_optimized_python_keeps_predicates(self):
        result = self.invoke('optimized_uuid')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['predicate'], 'natural.deal-readable')

    def test_malformed_response_names_stage(self):
        result = self.invoke('shape')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['predicate'], 'natural.canonical-response')

    def test_clean_runtime_obtains_release_locked_dependency(self):
        result = self.invoke('clean_runtime')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['synthetic'], 'passed')

    def test_wrong_dependency_integrity_is_unavailable(self):
        result = self.invoke('integrity')
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['predicate'], 'synthetic.runtime-dependencies')

    def test_missing_runtime_dependencies_are_unavailable(self):
        result = self.invoke('dependencies')
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['predicate'], 'synthetic.runtime-dependencies')

    def test_dependency_archive_refusals(self):
        for mode in ('archive_path', 'archive_link', 'archive_size', 'archive_invalid', 'transitive', 'dependency_url'):
            with self.subTest(mode=mode):
                result = self.invoke(mode)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['predicate'], 'synthetic.runtime-dependencies')

    def test_producer_deadline_cleans_stalled_and_late_processes(self):
        for mode in ('stalled_evaluation', 'late_launch'):
            with self.subTest(mode=mode):
                result = self.invoke(mode, timeout_seconds=2)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_deadline_during_directory_acquisition_cleans_scratch(self):
        for mode in ('late_directory', 'late_cleanup'):
            with self.subTest(mode=mode):
                result = self.invoke(mode, timeout_seconds=2)
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_alarm_during_timer_cancellation_still_exits_unavailable(self):
        result = self.invoke('deadline_disarm')
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['reason'])

    def test_mcp_envelope(self):
        result = self.invoke('envelope')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
        result = self.invoke('timeout')
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_wrapper_error_terminates_descendants(self):
        result = self.invoke('wrapper_error')
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)

    def test_deadline_flag(self):
        result = subprocess.run(['python3', str(RUNNER), '--origin', 'http://127.0.0.1', '--call-wrapper', '/missing', '--timeout-seconds', '31'], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
