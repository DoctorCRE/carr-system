#!/usr/bin/env python3
"""Offline public-command tests; models and HTTP are fake executables."""
import json
import fcntl
import importlib.util
import signal
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]


class StudySourcesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="study-sources-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name in ("bin", "ops/prompts", "tools/room-bridge", "fake-bin"):
            (self.root / name).mkdir(parents=True)
        for name in ("bin/study-sources.sh", "bin/study_sources.py", "bin/with-timeout.py",
                     "ops/prompts/source-study-brief.md", "tools/progress_board.py", "tools/room-bridge/grok_wire.py"):
            source = ROOT / name
            if source.exists():
                shutil.copyfile(source, self.root / name)
        self.env = dict(os.environ, PATH=str(self.root / "fake-bin") + os.pathsep + os.environ["PATH"],
                        FAKE_EVENTS=str(self.root / "events.jsonl"), PROGRESS_BOARD_ROOT=str(self.root / "out"),
                        PROGRESS_BOARD_LOCAL_ONLY="1")
        (self.root / 'fake-bin/fixture_report.py').write_text(textwrap.dedent('''
            import hashlib
            import json
            from pathlib import Path
            def report():
                request = json.loads(Path('request.json').read_text())
                evidence = request['prompt'].split('include: READ ')[-1].splitlines()[0]
                return ('## Why Joe picked it\\nThe source addresses study verification.\\n'
                        '## Concepts and methods\\n'
                        '| concept | source standard | CARR application | first step | measure | owner |\\n'
                        '| --- | --- | --- | --- | --- | --- |\\n'
                        '| evidence | compare artifacts | source-study command | add validation | reject TODO | code job |\\n'
                        '## Lateral combinations\\nApply the evidence contract to imports.\\n'
                        '## Installables\\nNo installable code.\\n'
                        '## Declines\\nNone; all concepts apply.\\n'
                        '## Work items\\n'
                        '1. Validate reports; done-test: TODO is rejected.\\n'
                        '2. Check read evidence; done-test: wrong digest is rejected.\\n'
                        '3. Own process lifetime; done-test: cancellation stops children.\\n'
                        '## Sources read / NOT READ\\nREAD ' + evidence + '\\n')
        '''))
        self.executable('tools/room-bridge/source_study.py', """
            import json, os, subprocess, sys
            from pathlib import Path
            phase = sys.argv[sys.argv.index('--phase') + 1]
            route = {'name': 'grok-build' if phase == 'retrieval' else 'source-study',
                     'kind': 'grok-cli' if phase == 'retrieval' else 'codex-session',
                     'model': 'grok-4.7' if phase == 'retrieval' else 'fixture-model',
                     'effort': 'high', 'sandbox': 'read-only', 'digest': 'fixture-route'}
            if '--preflight' in sys.argv:
                print(json.dumps(route)); sys.exit(0)
            request = json.loads(Path('request.json').read_text())
            if phase == 'retrieval':
                command = [str(Path(__file__).resolve().parents[2] / 'bin/grok-run.sh'),
                           '--prompt', request['prompt'], '--timeout-seconds', str(request['timeout'])]
            else:
                command = ['codex', '-m', route['model'], '-c', 'model_reasoning_effort="high"',
                           '-s', 'read-only', request['prompt']]
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'room', 'phase': phase, 'route': route}) + '\\n')
            proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True)
            if proc.returncode:
                sys.exit(proc.returncode)
            if phase == 'retrieval':
                result = proc.stdout.decode()
            elif Path('report.md').exists():
                try: result = Path('report.md').read_text(encoding='utf-8')
                except UnicodeDecodeError:
                    sys.stdout.buffer.write(bytes([255])); sys.exit(0)
            else:
                result = ''
            print(json.dumps({'route': route, 'status': 'completed', 'result': result,
                             'observed': {'model': 'grok-4.7-build' if phase == 'retrieval' else route['model'],
                                          'effort': 'high', 'thread_id': 'fixture-thread'}}))
        """)
        self.executable("bin/grok-run.sh", """
            import json, os, sys
            from pathlib import Path
            if '--help' in sys.argv:
                print('--prompt --effort')
                sys.exit(0)
            cards = json.loads((Path(os.environ['PROGRESS_BOARD_ROOT']) / 'boards/source-studies.json').read_text())['tasks']
            assert any(c['status'] == 'running' for c in cards.values())
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'grok', 'args': sys.argv[1:]}) + '\\n')
            print('RAW X POST: methods and linked source https://example.com/method')
        """)
        self.executable("fake-bin/codex", """
            import json, os, sys
            from pathlib import Path
            assert os.fstat(0).st_rdev == os.stat('/dev/null').st_rdev
            prompt = sys.argv[-1]
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'codex', 'args': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
            from fixture_report import report
            Path('report.md').write_text(report())
        """)
        self.executable("fake-bin/curl", """
            import json, os, sys
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'curl', 'args': sys.argv[1:]}) + '\\n')
            print('<html><body>RAW ARTICLE</body></html>')
        """)

    def executable(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(body))
        path.chmod(0o755)

    def run_tool(self, *urls, cwd=None):
        return subprocess.run(['bash', str(self.root / 'bin/study-sources.sh'), *urls],
                              cwd=cwd or self.root, env=self.env, input='must not reach codex',
                              capture_output=True, text=True, timeout=30)

    def events(self):
        return [json.loads(line) for line in (self.root / 'events.jsonl').read_text().splitlines()]

    def cards(self):
        return json.loads((self.root / 'out/boards/source-studies.json').read_text())['tasks']

    def test_each_url_gets_one_retrieval_one_high_study_and_done_card(self):
        urls = ('https://x.com/author/status/123', 'https://example.com/article')
        result = self.run_tool(*urls)
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual([e['kind'] for e in events].count('grok'), 1)
        self.assertEqual([e['kind'] for e in events].count('curl'), 1)
        studies = [e for e in events if e['kind'] == 'codex']
        self.assertEqual(len(studies), 2)
        self.assertEqual(len(self.cards()), 2)
        for study in studies:
            args = study['args']
            self.assertEqual(args[args.index('-m') + 1], 'fixture-model')
            self.assertIn('model_reasoning_effort="high"', args)
            self.assertIn('WHY JOE PICKED IT', args[-1])
            folder = Path(study['cwd'])
            self.assertEqual(folder.parent.parent, self.root / 'out/source-studies')
            self.assertEqual(folder.parent.name, datetime.now().date().isoformat())
            self.assertTrue((folder / 'retrieval.txt').is_file())
            self.assertTrue((folder / 'report.md').is_file())
        for card in self.cards().values():
            self.assertEqual(card['status'], 'done')
            self.assertIn('report.md', card['note'])

    def test_missing_report_blocks_card_with_reason(self):
        self.executable('fake-bin/codex', 'pass')
        result = self.run_tool('https://x.com/author/status/123')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertTrue(card['blocked_reason'])
        self.assertTrue(card['next_action'])
        self.assertIn('completion/model evidence invalid', card['note'])
        self.assertIn('report.md', card['note'])

    def test_retrieval_failure_blocks_without_study(self):
        self.executable('bin/grok-run.sh', 'import sys; sys.exit(4)')
        result = self.run_tool('https://x.com/author/status/123')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('retrieval Model Room failed (exit 4)', card['note'])
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/study-room.json')))

    def test_room_retrieval_deadline_is_forwarded(self):
        self.executable('bin/grok-run.sh', """
            import json, os, sys
            if '--help' in sys.argv:
                print('--timeout-seconds')
                sys.exit(0)
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'grok', 'args': sys.argv[1:]}) + '\\n')
            print('RAW POST')
        """)
        result = self.run_tool('https://twitter.com/author/status/123')
        self.assertEqual(result.returncode, 0, result.stderr)
        grok, = [e for e in self.events() if e['kind'] == 'grok']
        self.assertEqual(float(grok['args'][grok['args'].index('--timeout-seconds') + 1]), 600)

    def test_empty_report_is_blocked(self):
        self.executable('fake-bin/codex', "from pathlib import Path; Path('report.md').write_text('   ')")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('completion/model evidence invalid', card['note'])

    def test_failed_study_cannot_promote_a_report(self):
        self.executable('fake-bin/codex', "from pathlib import Path; import sys; Path('report.md').write_text('partial'); sys.exit(9)")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('study Model Room failed (exit 9)', card['note'])

    def test_more_than_four_sources_run_in_parallel_waves(self):
        self.executable('fake-bin/codex', """
            import json, os, time
            from pathlib import Path
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'start', 'pid': os.getpid()}) + '\\n')
            time.sleep(0.5)
            from fixture_report import report
            Path('report.md').write_text(report())
            with open(os.environ['FAKE_EVENTS'], 'a') as f:
                f.write(json.dumps({'kind': 'end', 'pid': os.getpid()}) + '\\n')
        """)
        result = self.run_tool(*(f'https://example.com/article/{i}' for i in range(9)))
        self.assertEqual(result.returncode, 0, result.stderr)
        active, peak, starts = set(), 0, 0
        for event in self.events():
            if event['kind'] == 'start':
                active.add(event['pid'])
                starts += 1
                peak = max(peak, len(active))
            elif event['kind'] == 'end':
                active.remove(event['pid'])
        self.assertEqual(starts, 9)
        self.assertLessEqual(peak, 4)
        self.assertGreater(peak, 1)
        self.assertFalse(active)
        self.assertEqual(len(self.cards()), 9)
        self.assertTrue(all(card['status'] == 'done' for card in self.cards().values()))

    def test_repeated_source_preserves_prior_report_and_card(self):
        url = 'https://example.com/article'
        for _ in range(2):
            result = self.run_tool(url)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list(self.root.glob('out/source-studies/*/*/report.md'))), 2)
        self.assertEqual(len(self.cards()), 2)

    def test_retrieval_timeout_blocks_without_study(self):
        self.env['STUDY_RETRIEVAL_TIMEOUT_SECONDS'] = '0.2'
        self.executable('bin/grok-run.sh', """
            import sys, time
            if '--help' in sys.argv:
                print('--prompt --effort')
                sys.exit(0)
            time.sleep(10)
        """)
        result = self.run_tool('https://x.com/author/status/123')
        self.assertEqual(result.returncode, 1, result.stderr)
        card, = self.cards().values()
        self.assertEqual(card['status'], 'blocked')
        self.assertIn('retrieval timed out', card['note'])
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/study-room.json')))


    def test_model_work_uses_room_and_verified_result(self):
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('room', [e['kind'] for e in self.events()])

    def test_no_unrestricted_worker_authority(self):
        self.run_tool('https://example.com/article')
        for event in self.events():
            self.assertNotIn('danger-full-access', event.get('args', []))

    def test_credential_urls_never_enter_any_sink(self):
        for url in ('https://synthetic-user:changeme@example.com/a',
                    'https://example.com/a?access_token=synthetic-query-secret',
                    'https://example.com/a?key=synthetic-query-secret',
                    'https://example.com/a?sig=synthetic-query-secret'):
            with self.subTest(url=url):
                result = self.run_tool(url)
                self.assertNotEqual(result.returncode, 0)
                sinks = result.stdout + result.stderr
                for path in self.root.rglob('*'):
                    if path.is_file() and ('out' in path.parts or path.name == 'events.jsonl'):
                        sinks += str(path) + path.read_text(errors='replace')
                for secret in ('synthetic-user', 'changeme', 'synthetic-query-secret'):
                    self.assertNotIn(secret, sinks)

    def test_transport_fetches_literal_braces(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'raw article')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            (self.root / 'fake-bin/curl').unlink()
            result = self.run_tool(f'http://127.0.0.1:{server.server_port}/article{{a,b}}')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(requests, ['/article{a,b}'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_todo_report_is_blocked(self):
        self.executable('fake-bin/codex', "from pathlib import Path; Path('report.md').write_text('TODO')")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(all(c['status'] == 'blocked' for c in self.cards().values()))

    def test_not_read_primary_cannot_complete(self):
        self.executable('fake-bin/codex', """
            from pathlib import Path
            from fixture_report import report
            Path('report.md').write_text(report().replace('\\nREAD ', '\\nNOT READ '))
        """)
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(all(c['status'] == 'blocked' for c in self.cards().values()))

    def test_invalid_result_shape_is_terminal_failure(self):
        self.executable('tools/room-bridge/source_study.py', "print('[]')")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        self.assertTrue(all(c['status'] == 'blocked' for c in self.cards().values()))

    def test_whitespace_retrieval_never_launches_study(self):
        self.executable('fake-bin/curl', "print('   ')")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/study-room.json')))

    def test_invalid_encoding_is_terminal_per_source_failure(self):
        self.executable('fake-bin/codex', "from pathlib import Path; Path('report.md').write_bytes(bytes([255]))")
        result = self.run_tool('https://example.com/article')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Traceback', result.stderr)
        self.assertTrue(all(c['status'] == 'blocked' for c in self.cards().values()))

    def sleeping_worker(self):
        self.executable('fake-bin/codex', """
            import subprocess, sys, time
            from pathlib import Path
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
            Path('.runtime-fixture').mkdir()
            Path('.runtime-fixture/auth.json').write_text('synthetic-provider-auth')
            Path('escaped.pid').write_text(str(child.pid))
            time.sleep(60)
        """)

    def wait_pid(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            files = list(self.root.glob('out/source-studies/*/*/escaped.pid'))
            if files:
                pid = int(files[0].read_text())
                self.addCleanup(self.kill_pid, pid)
                return pid
            time.sleep(.03)
        self.fail('worker did not start')

    @staticmethod
    def kill_pid(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def assert_dead(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], capture_output=True, text=True)
            if result.returncode or result.stdout.strip().startswith('Z'):
                return
            time.sleep(.05)
        self.fail(f'escaped worker {pid} survived')

    def test_timeout_reaps_escaped_descendant(self):
        self.sleeping_worker()
        self.env['STUDY_CODEX_TIMEOUT_SECONDS'] = '1'
        proc = subprocess.Popen(['bash', str(self.root / 'bin/study-sources.sh'), 'https://example.com/a'],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pid = self.wait_pid()
        proc.communicate(timeout=20)
        self.assert_dead(pid)
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/.runtime-*')))

    def test_cancellation_reaps_work_and_records_terminal_card(self):
        self.sleeping_worker()
        proc = subprocess.Popen(['bash', str(self.root / 'bin/study-sources.sh'), 'https://example.com/a'],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.kill_pid, proc.pid)
        pid = self.wait_pid()
        proc.terminate()
        proc.communicate(timeout=20)
        self.assert_dead(pid)
        self.assertFalse(list(self.root.glob('out/source-studies/*/*/.runtime-*')))
        self.assertTrue(all(c['status'] == 'blocked' for c in self.cards().values()))

    def test_board_root_is_resolved_from_caller(self):
        for setting in ('relative-output', '~/study-fixture-' + self.root.name):
            with self.subTest(setting=setting):
                self.env['PROGRESS_BOARD_ROOT'] = setting
                directory = Path(setting).expanduser()
                if not directory.is_absolute():
                    directory = self.root / 'fake-bin' / directory
                if setting.startswith('~'):
                    self.addCleanup(shutil.rmtree, directory, True)
                result = self.run_tool('https://example.com/a', cwd=self.root / 'fake-bin')
                self.assertEqual(result.returncode, 0, result.stderr)
                cards = json.loads((directory / 'boards/source-studies.json').read_text())['tasks']
                self.assertTrue(all(c['status'] == 'done' for c in cards.values()))

    def test_board_lock_wait_is_bounded(self):
        directory = self.root / 'out/boards'
        directory.mkdir(parents=True)
        self.env['STUDY_BOARD_TIMEOUT_SECONDS'] = '.2'
        with (directory / '.source-studies.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            proc = subprocess.Popen(['bash', str(self.root / 'bin/study-sources.sh'), 'https://example.com/a'],
                                    env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                out, err = proc.communicate(timeout=2)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(b'board', err)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                self.fail('unbounded board lock wait')

    def test_board_child_has_deadline(self):
        self.env['STUDY_BOARD_TIMEOUT_SECONDS'] = '.2'
        (self.root / 'tools/progress_board.py').write_text('import time; time.sleep(60)')
        proc = subprocess.Popen(['bash', str(self.root / 'bin/study-sources.sh'), 'https://example.com/a'],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            proc.communicate(timeout=2)
            self.assertNotEqual(proc.returncode, 0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail('unbounded board child wait')

    def test_x_routing_has_one_table_driven_classification(self):
        spec = importlib.util.spec_from_file_location('study_fixture', self.root / 'bin/study_sources.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for host in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com'):
            self.assertTrue(module.is_x_source('https://' + host + '/post'))
        for host in ('example.com', 'x.com.example.com', 'notx.com'):
            self.assertFalse(module.is_x_source('https://' + host + '/post'))

    def test_prompt_role_does_not_identify_an_operator(self):
        role = (self.root / 'ops/prompts/source-study-brief.md').read_text().splitlines()[0]
        self.assertNotRegex(role, r"[A-Z][a-z]+ [A-Z][a-z]+'s")

    def test_malformed_host_is_argument_error(self):
        result = self.run_tool('https://[bad')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
