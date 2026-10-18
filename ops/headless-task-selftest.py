#!/usr/bin/env python3
"""Behavioral headless runner tests; no model, login, launchd, or record writes."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import signal
import time
from unittest.mock import patch
import unittest
from datetime import datetime, timedelta, timezone

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class HeadlessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.repo = self.home / 'carr-system'
        (self.repo / 'ops/headless-tasks').mkdir(parents=True)
        (self.repo / 'ops/headless-tasks/tasks.json').write_text(json.dumps({
            'timezone': 'America/Chicago', 'tasks': {
                'test-task': {'cron': '0 * * * *', 'timeout_seconds': 5400,
                              'grace_seconds': 60}}}))
        prompt = self.home / '.claude/scheduled-tasks/test-task/SKILL.md'
        prompt.parent.mkdir(parents=True)
        prompt.write_text(json.dumps({'acceptance_contract':{'criteria':[{'id':'output','kind':'contains','path':'{receipt}.artifact','text':'synthetic completed task artifact'}]}}))
        self.fakebin = self.home / 'fakebin'
        self.fakebin.mkdir()
        fake = self.fakebin / 'claude'
        fake.write_text('''#!/usr/bin/env python3
import hashlib, json, os, pathlib, sys, time
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'loggedIn':os.environ.get('FAKE_MODE') != 'token-auth' or bool(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN')), 'authMethod':
          'api_key' if os.environ.get('FAKE_MODE') == 'api-auth' else
          ('oauth_token' if os.environ.get('FAKE_MODE') == 'token-auth' else 'claude.ai'),
          'apiProvider':'firstParty'}))
    sys.exit(0)
pathlib.Path(os.environ['FAKE_ARGS']).write_text(json.dumps({
    'argv': sys.argv[1:], 'prompt': sys.stdin.read(), 'cwd': os.getcwd(),
    'api_key_present': 'ANTHROPIC_API_KEY' in os.environ}))
mode = os.environ.get('FAKE_MODE', 'success')
if mode == 'timeout':
    pathlib.Path(os.environ['FAKE_ARGS']+'.pid').write_text(str(os.getpid()))
    time.sleep(60)
if mode == 'denied':
    print(json.dumps({'type':'system','subtype':'permission_denied'}), flush=True)
    time.sleep(60)
if mode == 'denied-result':
    print(json.dumps({'type':'result','subtype':'success','is_error':False,
                     'permission_denials':[{'tool_name':'Bash'}]}))
elif mode == 'invalid':
    print('not a result')
elif mode == 'abort':
    print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'Required store unavailable; stopped without work'}))
elif mode == 'null':
    print(json.dumps({'type':'result','subtype':'success','result':None}))
else:
    if os.environ.get('CARR_HEADLESS_RECEIPT') and mode != 'failure':
        artifact = pathlib.Path(os.environ['CARR_HEADLESS_RECEIPT']+'.artifact')
        artifact.write_text('Required store unavailable; stopped without work' if mode == 'failed-artifact' else 'synthetic completed task artifact')
        pathlib.Path(os.environ['CARR_HEADLESS_RECEIPT']).write_text(json.dumps({
            'schema':'carr-headless-completion/v1', 'task_id':'test-task',
            'run_id':os.environ['CARR_HEADLESS_RUN_ID'], 'outcome':'completed',
            'artifacts':[{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}))
    print(json.dumps({'type':'result','subtype':'success','is_error':False,
                     'permission_denials':[], 'result':'Required store unavailable; stopped without work' if mode == 'failed-artifact' else 'done'}))
sys.exit(7 if mode == 'failure' else 0)
''')
        fake.chmod(0o755)
        # Any accidental vendor access in child processes fails this fixture.
        (self.fakebin/'sitecustomize.py').write_text(
            f'import sys; sys.path.insert(0, {str(REPO)!r})\n'
            'from ops import jev_judge as j\n'
            'def forbidden(*args, **kwargs): raise AssertionError("paid judgment")\n'
            'j.judge = forbidden\n')
        recorder = self.repo / 'run.sh'
        recorder.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_RECORDS'])
with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if os.environ.get('FAKE_RECORD_FAIL'): sys.exit(1)
print(json.dumps({'ok':True}))
''')
        recorder.chmod(0o755)
        self.env = {**os.environ, 'HOME': str(self.home),
                    'PYTHONPATH': str(self.fakebin),
                    'PATH': str(self.fakebin) + os.pathsep + os.environ['PATH'],
                    'FAKE_ARGS': str(self.home / 'args.json'),
                    'FAKE_RECORDS': str(self.home / 'records.jsonl')}
        (self.repo/'tools').mkdir()
        ops_record = self.repo/'tools/ops-record.py'
        ops_record.write_text('''import json, os, pathlib, sys
p = pathlib.Path(os.environ['FAKE_OPS_RECORDS'])
with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if os.environ.get('FAKE_OPS_RECORD_FAIL'): sys.exit(1)
print(sys.argv[sys.argv.index('--correlation')+1]+' aa000000-0000-4000-8000-000000000002')
''')
        self.env['FAKE_OPS_RECORDS'] = str(self.home/'ops-records.jsonl')

    def run_task(self, mode='success', timeout='30'):
        return subprocess.run([str(REPO / 'bin/headless-task'), 'test-task',
                               '--repo', str(self.repo), '--timeout-seconds', timeout],
                              env={**self.env, 'FAKE_MODE': mode},
                              capture_output=True, text=True, timeout=90)

    def ledger(self):
        return [json.loads(s) for s in (self.repo / 'out/headless/test-task/ledger.jsonl').read_text().splitlines()]

    def test_success_cli_and_private_log(self):
        result = self.run_task()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = self.ledger()[-1]
        self.assertEqual(row['exit_code'], 0)
        self.assertTrue(row['start'] <= row['end'])
        log = Path(row['log_path'])
        self.assertTrue(log.is_file())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        args = json.loads((self.home / 'args.json').read_text())
        self.assertIn('-p', args['argv'])
        self.assertIn('--no-session-persistence', args['argv'])
        self.assertEqual(args['argv'][args['argv'].index('--permission-mode')+1], 'dontAsk')
        self.assertEqual(args['argv'][args['argv'].index('--permission-prompts')+1], 'none')
        self.assertIn('--allowedTools', args['argv'])
        self.assertIn('<scheduled-task name="test-task">', args['prompt'])
        self.assertIn('acceptance_contract', args['prompt'])
        self.assertIn('carr-headless-completion/v1', args['prompt'])
        self.assertEqual(Path(args['cwd']), self.repo.resolve())
        self.assertFalse((self.home / 'records.jsonl').exists())

    def test_denial_event_fails_promptly(self):
        result = self.run_task('denied')
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertEqual(self.ledger()[-1]['exit_code'], 77)
        self.assertIn('permission_denied', result.stdout)
        self.assertTrue((self.home / 'records.jsonl').exists())

    def test_denial_result_overrides_zero_exit(self):
        result = self.run_task('denied-result')
        diagnostic = result.stdout + result.stderr
        for log in (self.repo/'out/headless/test-task').glob('*.log'):
            diagnostic += log.read_text()
        self.assertEqual(result.returncode, 77, diagnostic)

    def test_timeout_is_logged_and_recorded(self):
        self.assertEqual(self.run_task('timeout', '0.15').returncode, 124)
        self.assertEqual(self.ledger()[-1]['exit_code'], 124)
        self.assertTrue((self.home / 'records.jsonl').exists())

    def test_nonzero_preserved_and_failure_deduped_by_task(self):
        self.assertEqual(self.run_task('failure').returncode, 7)
        self.assertEqual(self.run_task('failure').returncode, 7)
        calls = [json.loads(s) for s in (self.home / 'records.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 1, 'delivered episodes are not sent again')
        a = json.loads(calls[0][-1])
        self.assertIn('test-task', a['idempotency_key'])
        self.assertEqual(calls[0][:2], ['call', 'add-loop'])

    def test_double_fire_skips_but_failure_can_retry(self):
        self.assertEqual(self.run_task('failure').returncode, 7)
        self.assertEqual(self.run_task().returncode, 0)
        (self.home / 'args.json').unlink()
        result = self.run_task()
        self.assertEqual(result.returncode, 0)
        self.assertIn('SKIP', result.stdout)
        self.assertFalse((self.home / 'args.json').exists())
        self.assertEqual(len({r['run_id'] for r in self.ledger()}), 3)
        self.assertEqual(self.ledger()[-1]['status'], 'skipped')

    def test_invalid_zero_exit_is_failure(self):
        self.assertNotEqual(self.run_task('invalid').returncode, 0)

    def test_api_environment_not_forwarded(self):
        self.env['ANTHROPIC_API_KEY'] = 'synthetic-fixture-value'
        self.assertEqual(self.run_task().returncode, 0)
        self.assertFalse(json.loads((self.home / 'args.json').read_text())['api_key_present'])

    def test_log_redacts_credentials_before_storage(self):
        from lib.headless_tasks import log_text
        fixture_value = ''.join(['synthetic', '-private-value'])
        for text in [json.dumps({'access_token': fixture_value}),
                     json.dumps({'result': 'PASSWORD='+fixture_value}),
                     'api_key='+fixture_value, 'known='+fixture_value]:
            self.assertNotIn(fixture_value, log_text(text))
        self.assertNotIn(fixture_value, log_text(json.dumps({'access_token': fixture_value})))

    def test_api_login_is_refused_before_model_work(self):
        result = self.run_task('api-auth')
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertFalse((self.home/'args.json').exists())
        self.assertEqual(self.ledger()[-1]['exit_code'], 78)

    def test_missed_run_warn_names_action_and_install_grace(self):
        from lib.headless_tasks import health_rows
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        installed = agents / 'com.carr.headless.test-task.plist'
        installed.write_bytes(plistlib.dumps({'Label': 'com.carr.headless.test-task'}))
        now = datetime.now(timezone.utc)
        rows = health_rows(self.repo, self.home, now)
        self.assertEqual(rows[0]['status'], 'OK')
        os.utime(installed, (now.timestamp()-7200, now.timestamp()-7200))
        rows = health_rows(self.repo, self.home, now)
        self.assertEqual(rows[0]['status'], 'WARN')
        self.assertIn('bin/headless-task test-task', rows[0]['line'])
        self.assertIn('owner:', rows[0]['line'])
        self.assertIn('verify:', rows[0]['line'])
        self.assertIn('auto-clear:', rows[0]['line'])
        self.assertEqual(self.run_task().returncode, 0)
        self.assertEqual(health_rows(self.repo, self.home, now+timedelta(seconds=1))[0]['status'], 'OK')
        rows = health_rows(self.repo, self.home, now+timedelta(hours=2))
        self.assertEqual(rows[0]['status'], 'WARN')

    def test_uninstalled_task_is_not_monitored(self):
        from lib.headless_tasks import health_rows
        self.assertEqual(health_rows(self.repo, self.home), [])

    def test_run_sh_health_entrypoint_warns_without_database(self):
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        installed = agents / 'com.carr.headless.radar-weekly.plist'
        installed.write_bytes(plistlib.dumps({'Label': 'com.carr.headless.radar-weekly'}))
        old = datetime.now(timezone.utc).timestamp()-9*86400
        os.utime(installed, (old, old))
        result = subprocess.run([str(REPO/'run.sh'), 'health', '--section', 'headless'],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('WARN headless/radar-weekly', result.stdout)
        self.assertIn('bin/headless-task radar-weekly', result.stdout)

    def test_schedule_windows_weekend_and_month_gap(self):
        from lib.headless_tasks import schedule_window, schedule_interval
        friday = datetime(2026, 10, 2, 16, tzinfo=timezone.utc)
        monday = datetime(2026, 10, 5, 16, tzinfo=timezone.utc)
        self.assertEqual(schedule_window('0 11 * * 1-5', monday, 'America/Chicago'), monday)
        self.assertEqual(schedule_interval('0 11 * * 1-5', friday, 'America/Chicago'), 3*86400)
        self.assertGreater(schedule_interval('0 9 15-21 * *', datetime(2026, 10, 21, 14, tzinfo=timezone.utc), 'America/Chicago'), 20*86400)

    def test_committed_templates_match_manifest(self):
        from lib.headless_tasks import calendar_entries
        config = json.loads((REPO / 'ops/headless-tasks/tasks.json').read_text())
        for task_id, task in config['tasks'].items():
            plist = plistlib.loads((REPO / f'ops/headless-tasks/com.carr.headless.{task_id}.plist').read_bytes())
            self.assertEqual(plist['StartCalendarInterval'], calendar_entries(task['cron']))
            self.assertEqual(plist['ProgramArguments'][-1], task_id)
            self.assertNotIn('RunAtLoad', plist)
            self.assertNotIn('KeepAlive', plist)

    def test_01_termination_persists_identity_and_kills_child(self):
        child = subprocess.Popen([str(REPO/'bin/headless-task'), 'test-task', '--repo', str(self.repo),
                                  '--timeout-seconds', '60'], env={**self.env,'FAKE_MODE':'timeout'},
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        pidfile = self.home/'args.json.pid'
        try:
            deadline = time.monotonic()+30
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(pidfile.exists())
            pid = int(pidfile.read_text())
            child.send_signal(signal.SIGTERM)
            child.communicate(timeout=30)
            try:
                with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
                rows = self.ledger()
                self.assertEqual(rows[0]['status'], 'running')
                self.assertEqual(rows[-1]['reason'], 'interrupted')
                self.assertEqual(rows[0]['run_id'], rows[-1]['run_id'])
            finally:
                try: os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError: pass
        finally:
            if child.poll() is None: child.kill(); child.communicate()

    def test_02_cli_success_without_work_receipt_does_not_suppress_retry(self):
        self.assertEqual(self.run_task('abort').returncode, 65)
        self.assertEqual(self.run_task('null').returncode, 65)
        self.assertEqual(self.run_task().returncode, 0)

    def test_03_projection_excludes_business_and_multiline_credentials(self):
        from lib.headless_tasks import log_text
        for text in [json.dumps({'type':'result','result':'synthetic@example.invalid'}),
                     json.dumps({'type':'assistant','message':'-----BEGIN ' + 'PRIVATE KEY-----\nsynthetic-key\n-----END PRIVATE KEY-----'}),
                     'synthetic@example.invalid', 'synthetic-key\n']:
            projected = log_text(text)
            self.assertNotIn('synthetic@example.invalid', projected)
            self.assertNotIn('synthetic-key', projected)

    def _install(self, launch):
        from lib.headless_tasks import install_main
        source = self.repo/'ops/headless-tasks/com.carr.headless.test-task.plist'
        source.write_bytes(plistlib.dumps({'Label':'com.carr.headless.test-task',
            'StartCalendarInterval':[{'Minute':0}],
            'StandardOutPath':'{{REPO}}/out/headless/test-task/launchd.log',
            'StandardErrorPath':'{{REPO}}/out/headless/test-task/launchd.log'}))
        with patch('pathlib.Path.home', return_value=self.home), patch('lib.claude_scheduler_native.system_timezone', return_value='America/Chicago'), patch('lib.headless_tasks.subprocess.run', side_effect=launch):
            return install_main(['install', 'test-task'])

    def test_04_launchd_files_private_before_bootstrap(self):
        def launch(cmd, **kwargs):
            if cmd[1] == 'bootstrap':
                plist = plistlib.loads(Path(cmd[-1]).read_bytes())
                path = Path(plist['StandardOutPath'])
                path.touch(exist_ok=True)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(plist['Umask'], 0o077)
            return subprocess.CompletedProcess(cmd, 113 if cmd[1] == 'print' else 0)
        old = os.umask(0o022)
        try: self._install(launch)
        finally: os.umask(old)

    def test_05_failed_bootstrap_can_retry_and_unloaded_uninstall_recovers(self):
        state = {'fail':True}
        def launch(cmd, **kwargs):
            code = 113 if cmd[1] in ('print', 'bootout') else (5 if state['fail'] else 0)
            if kwargs.get('check') and code: raise subprocess.CalledProcessError(code, cmd)
            return subprocess.CompletedProcess(cmd, code)
        with self.assertRaises(subprocess.CalledProcessError): self._install(launch)
        state['fail'] = False
        self.assertEqual(self._install(launch), 0)
        from lib.headless_tasks import install_main
        with patch('pathlib.Path.home', return_value=self.home), patch('lib.headless_tasks.subprocess.run', side_effect=launch):
            self.assertEqual(install_main(['uninstall','test-task']), 0)
        self.assertFalse((self.home/'Library/LaunchAgents/com.carr.headless.test-task.plist').exists())

    def test_06_pending_alert_replays_after_next_success(self):
        self.env['FAKE_RECORD_FAIL'] = '1'
        self.assertEqual(self.run_task('failure').returncode, 7)
        self.env.pop('FAKE_RECORD_FAIL')
        self.assertEqual(self.run_task().returncode, 0)
        calls = [json.loads(s) for s in (self.home/'records.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[0][-1])['idempotency_key'], json.loads(calls[1][-1])['idempotency_key'])

    def test_07_torn_ledger_recovers_and_corruption_is_reported(self):
        folder = self.repo/'out/headless/test-task'
        folder.mkdir(parents=True)
        (folder/'ledger.jsonl').write_text('{"status":"failed"}\n{"status":')
        self.assertEqual(self.run_task().returncode, 0)
        self.assertTrue(list(folder.glob('ledger.corrupt.*')))
        self.assertTrue((self.home/'records.jsonl').exists())
        self.assertEqual(self.run_task().returncode, 0)
        self.assertEqual(self.ledger()[-1]['status'], 'skipped')

    def test_07_interior_corruption_delivers_alerts_without_replaying_history(self):
        folder = self.repo/'out/headless/test-task'
        folder.mkdir(parents=True)
        ledger = folder/'ledger.jsonl'
        original = b'{"status":"failed"}\n{"status":\n{"status":"failed"}\n'
        ledger.write_bytes(original)
        # Canonical intent must remain untouched until its ledger can be read.
        outbox = folder/'pending-runs.json'
        outbox.write_text('[]')
        for attempt in range(2):
            result = self.run_task()
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse((self.home/'args.json').exists(), 'corruption must block model work')
            self.assertFalse((self.home/'ops-records.jsonl').exists(), 'unreadable history must not be published')
            self.assertEqual(outbox.read_text(), '[]')
            self.assertTrue(ledger.read_bytes().startswith(original))
            self.assertTrue(any(p.read_bytes() == original for p in folder.glob('ledger.corrupt.*')))
            alerts = json.loads((folder/'alerts.json').read_text())
            self.assertEqual({a['reason'] for a in alerts}, {'ledger_corrupt', 'runner_error'})
            self.assertTrue(all(a['delivered'] for a in alerts))
            calls = [json.loads(s) for s in (self.home/'records.jsonl').read_text().splitlines()]
            self.assertEqual(len(calls), 2, f'attempt {attempt}: acknowledge each episode once')
            self.assertEqual(len({json.loads(c[-1])['idempotency_key'] for c in calls}), 2)

    def test_intact_final_ledger_row_without_newline_survives_append(self):
        folder = self.repo/'out/headless/test-task'
        folder.mkdir(parents=True)
        (folder/'ledger.jsonl').write_text('{"status":"failed"}')
        self.assertEqual(self.run_task().returncode, 0)
        self.assertEqual(self.ledger()[0], {'status': 'failed'})
        self.assertEqual(self.run_task().returncode, 0)
        self.assertEqual(self.ledger()[-1]['status'], 'skipped')

    def test_08_first_due_slot_not_full_install_interval(self):
        from lib.headless_tasks import health_rows
        settings = json.loads((self.repo/'ops/headless-tasks/tasks.json').read_text())
        settings['tasks']['test-task']['cron'] = '0 9 * * 4'
        (self.repo/'ops/headless-tasks/tasks.json').write_text(json.dumps(settings))
        agents = self.home/'Library/LaunchAgents'; agents.mkdir(parents=True)
        installed = agents/'com.carr.headless.test-task.plist'; installed.touch()
        at = datetime(2026,10,1,13,59,tzinfo=timezone.utc).timestamp()
        os.utime(installed, (at, at))
        rows = health_rows(self.repo,self.home,datetime(2026,10,3,15,tzinfo=timezone.utc))
        self.assertEqual(rows[0]['status'], 'WARN')

    def test_09_unreadable_manifest_and_ledger_are_hard_nonrolling_findings(self):
        from lib.headless_tasks import health_rows
        path = self.repo/'ops/headless-tasks/tasks.json'
        original = path.read_text(); path.write_text('{')
        rows = health_rows(self.repo, self.home)
        self.assertTrue(rows[0]['hard_error']); self.assertFalse(rows[0]['time_rolling'])
        path.write_text(original)
        agents = self.home/'Library/LaunchAgents'; agents.mkdir(parents=True)
        (agents/'com.carr.headless.test-task.plist').touch()
        folder = self.repo/'out/headless/test-task'; folder.mkdir(parents=True)
        (folder/'ledger.jsonl').write_text('{')
        rows = health_rows(self.repo,self.home)
        self.assertTrue(rows[0]['hard_error']); self.assertFalse(rows[0]['time_rolling'])

    def test_10_upstream_receipt_recorded_and_consumed_without_desktop_snapshot(self):
        self.assertEqual(self.run_task().returncode, 0)
        calls = [json.loads(s) for s in (self.home/'ops-records.jsonl').read_text().splitlines()]
        self.assertTrue(any('scheduled-session' in c and 'succeeded' in c for c in calls))
        result = subprocess.run([str(REPO/'bin/headless-task'),'test-task','--repo',str(self.repo),
            '--check-fresh-since', stamp_before()],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_02_failed_artifact_with_correct_receipt_digest_is_not_completion(self):
        result = self.run_task('failed-artifact')
        self.assertEqual(result.returncode, 65, result.stderr)
        self.assertFalse(any(r['status'] == 'success' for r in self.ledger()))
        self.assertFalse((self.home/'ops-records.jsonl').exists() and any(
            'succeeded' in json.loads(line) for line in
            (self.home/'ops-records.jsonl').read_text().splitlines()))
        self.assertEqual(self.run_task().returncode, 0, 'failure must not suppress real work')

    def test_completion_predicate_uses_the_task_deadline(self):
        from lib.headless_tasks import verify_completion
        self.assertEqual(self.run_task().returncode,0)
        row=self.ledger()[-1]
        receipt=Path(row['completion_receipt']) if 'completion_receipt' in row else self.repo/'out/headless/test-task'/(row['run_id']+'.completion.json')
        with self.assertRaisesRegex(ValueError,'deadline'):
            verify_completion(receipt,'test-task',row['run_id'],(self.home/'.claude/scheduled-tasks/test-task/SKILL.md').read_text(),deadline=time.monotonic()-1)

    def test_canonical_outage_replays_original_terminal_identity_without_work(self):
        self.env['FAKE_OPS_RECORD_FAIL']='1'
        self.assertEqual(self.run_task().returncode,0)
        folder=self.repo/'out/headless/test-task'
        pending=json.loads((folder/'pending-runs.json').read_text())
        self.assertEqual(len(pending),1)
        run_id=pending[0]['run_id']
        # The ledger is authoritative even if its projection is missing/stale.
        (folder/'pending-runs.json').write_text('[]')
        self.env.pop('FAKE_OPS_RECORD_FAIL')
        (self.home/'args.json').unlink()
        self.assertEqual(self.run_task().returncode,0)
        self.assertFalse((self.home/'args.json').exists())
        calls=[json.loads(s) for s in (self.home/'ops-records.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls),2)
        self.assertTrue(all(c[c.index('--correlation')+1]==run_id for c in calls))
        self.assertEqual(json.loads((folder/'pending-runs.json').read_text()),[])

    def test_10_terminal_append_crash_recovers_canonical_receipt_before_skip(self):
        driver = (f'import sys,os;sys.path.insert(0,{str(REPO)!r});'
                  'from lib import headless_tasks as h; real=h.append_ledger;\n'
                  'def crash(path,row):\n'
                  ' real(path,row)\n'
                  ' if row.get("status")=="success" and not row.get("canonical_recorded"): os._exit(143)\n'
                  'h.append_ledger=crash\n'
                  f'sys.exit(h.main(["test-task","--repo",{str(self.repo)!r}]))')
        result = subprocess.run([sys.executable,'-c',driver], env=self.env,
                                capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,143,result.stderr)
        self.assertFalse((self.home/'ops-records.jsonl').exists())
        (self.home/'args.json').unlink()
        result = self.run_task()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('window_succeeded',result.stdout)
        self.assertFalse((self.home/'args.json').exists(), 'completed effects must not repeat')
        self.assertTrue((self.home/'ops-records.jsonl').exists(), 'canonical publication was lost')
        calls = [json.loads(s) for s in (self.home/'ops-records.jsonl').read_text().splitlines()]
        self.assertEqual(len(calls),1)
        self.assertIn('succeeded',calls[0])
        self.assertEqual(calls[0][calls[0].index('--correlation')+1],
                         next(r['run_id'] for r in self.ledger() if r['status']=='success'))
        result = subprocess.run([str(REPO/'bin/headless-task'),'test-task','--repo',str(self.repo),
            '--check-fresh-since',stamp_before()],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.run_task().returncode,0)
        self.assertEqual(len((self.home/'ops-records.jsonl').read_text().splitlines()),1)

    def test_canonical_recovery_notice_keeps_verified_work_fresh(self):
        recorder = self.repo/'tools/ops-record.py'
        with recorder.open('a') as handle:
            # cmd_run emits the acknowledgement, then incident recovery notices.
            handle.write("print('  cleared synthetic-incident recovered after verified work')\n")
        self.assertEqual(self.run_task().returncode, 0)
        folder = self.repo/'out/headless/test-task'
        self.assertEqual(json.loads((folder/'pending-runs.json').read_text()), [])
        self.assertTrue(self.ledger()[-1]['canonical_recorded'])
        result = subprocess.run([str(REPO/'bin/headless-task'), 'test-task', '--repo', str(self.repo),
            '--check-fresh-since', stamp_before()], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_11_monthly_predicate_stops_before_model_and_does_not_refresh_success(self):
        settings = json.loads((self.repo/'ops/headless-tasks/tasks.json').read_text())
        settings['tasks']['test-task']['monthly'] = True
        (self.repo/'ops/headless-tasks/tasks.json').write_text(json.dumps(settings))
        (self.repo/'bin').mkdir()
        (self.repo/'bin/monthly-gate.py').write_text('raise SystemExit(1)\n')
        self.assertEqual(self.run_task().returncode, 0)
        self.assertFalse((self.home/'args.json').exists())
        self.assertEqual(self.ledger()[-1]['reason'], 'monthly_completed')
        self.assertFalse(any(r['status']=='success' for r in self.ledger()))

    def test_12_subscription_setup_token_is_forwarded_and_validated(self):
        self.env['CLAUDE_CODE_OAUTH_TOKEN'] = 'synthetic-subscription-token'
        self.assertEqual(self.run_task('token-auth').returncode, 0)

    def test_termination_only_ignores_darwin_eperm_for_absent_group(self):
        from lib.headless_tasks import _terminate
        from unittest.mock import Mock
        child = Mock(pid=987654)
        with patch('os.killpg', side_effect=PermissionError), patch('lib.headless_tasks.subprocess.run',
                return_value=subprocess.CompletedProcess([],0,stdout='1\n2\n')):
            _terminate(child)
        child.wait.assert_called_once()
        with patch('os.killpg', side_effect=PermissionError), patch('lib.headless_tasks.subprocess.run',
                return_value=subprocess.CompletedProcess([],0,stdout='987654\n')):
            with self.assertRaises(PermissionError): _terminate(child)

    def test_termination_reaps_exited_child_before_darwin_group_probe(self):
        from lib.headless_tasks import _terminate
        from unittest.mock import Mock
        reaped = []
        child = Mock(pid=987654)
        child.poll.side_effect = lambda: reaped.append(True) or 0
        def probe(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, stdout='' if reaped else '987654\n')
        with patch('os.killpg', side_effect=PermissionError), patch(
                'lib.headless_tasks.subprocess.run', side_effect=probe):
            _terminate(child)
        child.poll.assert_called_once()
        child.wait.assert_called_once()

    def test_canonical_zero_exit_without_ack_is_not_recorded(self):
        from lib.headless_tasks import record_run
        row={'status':'success','start':stamp_before(),'end':stamp_before(),'run_id':'aa000000-0000-4000-8000-000000000001'}
        with patch('lib.headless_tasks.subprocess.run', return_value=subprocess.CompletedProcess([],0,stdout='no receipt')):
            self.assertFalse(record_run(self.repo,'test-task',row))

    def test_monthly_success_remains_fresh_through_completed_window(self):
        from lib.headless_tasks import health_rows
        settings = json.loads((self.repo/'ops/headless-tasks/tasks.json').read_text())
        settings['tasks']['test-task'].update(cron='0 9 15-21 * *', monthly=True)
        (self.repo/'ops/headless-tasks/tasks.json').write_text(json.dumps(settings))
        agents=self.home/'Library/LaunchAgents'; agents.mkdir(parents=True)
        (agents/'com.carr.headless.test-task.plist').touch()
        folder=self.repo/'out/headless/test-task'; folder.mkdir(parents=True)
        (folder/'ledger.jsonl').write_text(json.dumps({'status':'success','exit_code':0,'end':'2026-10-15T15:00:00Z'})+'\n')
        self.assertEqual(health_rows(self.repo,self.home,datetime(2026,10,20,16,tzinfo=timezone.utc))[0]['status'],'OK')
        self.assertEqual(health_rows(self.repo,self.home,datetime(2026,11,16,16,tzinfo=timezone.utc))[0]['status'],'WARN')

    def test_completion_reads_artifact_digest_and_rejects_wrong_run(self):
        from lib.headless_tasks import verify_completion
        import hashlib
        artifact=self.home/'artifact';artifact.write_text('expected')
        receipt=self.home/'receipt'
        data={'schema':'carr-headless-completion/v1','task_id':'test-task','run_id':'r','outcome':'completed','artifacts':[{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
        receipt.write_text(json.dumps(data))
        prompt=json.dumps({'acceptance_contract':{'criteria':[{'id':'output','kind':'contains','path':str(artifact),'text':'expected'}]}})
        with patch('ops.jev_judge.judge',side_effect=AssertionError('paid')):
            self.assertEqual(verify_completion(receipt,'test-task','r',prompt),'completed')
        with self.assertRaises(ValueError):verify_completion(receipt,'test-task','wrong',prompt)
        artifact.write_text('tampered')
        with self.assertRaises(ValueError):verify_completion(receipt,'test-task','r',prompt)

    def test_unknown_criteria_and_noop_require_separate_typed_acceptance(self):
        from lib.headless_tasks import verify_completion
        import hashlib
        artifact=self.home/'output';artifact.write_text('expected')
        receipt=self.home/'receipt'
        data={'schema':'carr-headless-completion/v1','task_id':'test-task','run_id':'r','outcome':'noop','artifacts':[{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
        receipt.write_text(json.dumps(data))
        criterion={'id':'output','kind':'contains','path':str(artifact),'text':'expected'}
        for prompt in ['Make it good',json.dumps({'acceptance_contract':{'criteria':[criterion]}})]:
            with self.assertRaisesRegex(ValueError,'needs review'):verify_completion(receipt,'test-task','r',prompt)
        prompt=json.dumps({'acceptance_contract':{'noop_criteria':[criterion]}})
        self.assertEqual(verify_completion(receipt,'test-task','r',prompt),'noop')

    def test_hard_killed_wrapper_reconciles_orphan_before_retry(self):
        child=subprocess.Popen([str(REPO/'bin/headless-task'),'test-task','--repo',str(self.repo)],
            env={**self.env,'FAKE_MODE':'timeout'}, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        pidfile=self.home/'args.json.pid'
        deadline=time.monotonic()+30
        while not pidfile.exists() and time.monotonic()<deadline: time.sleep(.02)
        self.assertTrue(pidfile.exists())
        pid=int(pidfile.read_text())
        try:
            child.kill(); child.wait(timeout=5)
            self.assertEqual(self.run_task().returncode,0)
            self.assertTrue(any(r.get('reason')=='interrupted' for r in self.ledger()))
        finally:
            if child.poll() is None: child.kill(); child.wait()
            try: os.killpg(pid,signal.SIGKILL)
            except (ProcessLookupError,PermissionError): pass

    def test_missing_child_birth_prevents_model_work(self):
        driver = (f'import sys,os;sys.path.insert(0,{str(REPO)!r});'
                  'from lib import headless_tasks as h;'
                  'birth=h.process_birth;'
                  'h.process_birth=lambda pid:birth(pid) if pid==os.getpid() else "";'
                  f'sys.exit(h.main(["test-task","--repo",{str(self.repo)!r}]))')
        result = subprocess.run([sys.executable, '-c', driver], env=self.env,
                                cwd=self.repo, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 70, result.stderr)
        self.assertFalse((self.home/'args.json').exists())
        self.assertEqual(self.ledger()[-1]['status'], 'failed')

    def test_child_cannot_do_work_before_its_identity_is_durable(self):
        """Kill during identity capture, before the child journal append."""
        driver = self.home/'launch-gap.py'
        marker = self.home/'unjournaled-child.pid'
        driver.write_text(
            'import os,sys,time\n'
            f'sys.path.insert(0,{str(REPO)!r})\n'
            'from pathlib import Path\n'
            'import lib.headless_tasks as h\n'
            'real=h.process_birth\n'
            'def stalled(pid):\n'
            ' if pid != os.getpid():\n'
            f'  Path({str(marker)!r}).write_text(str(pid))\n'
            '  time.sleep(60)\n'
            ' return real(pid)\n'
            'h.process_birth=stalled\n'
            f'raise SystemExit(h.main(["test-task","--repo",{str(self.repo)!r}]))\n')
        wrapper = subprocess.Popen([sys.executable,str(driver)], cwd=self.repo,
            env={**self.env,'FAKE_MODE':'timeout'},
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        pid = None
        try:
            deadline = time.monotonic()+10
            while not marker.exists() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists(), 'identity capture never began')
            pid = int(marker.read_text())
            time.sleep(.2)
            wrapper.kill(); wrapper.wait(timeout=5)
            self.assertFalse((self.home/'args.json').exists(),
                'effect-capable CLI started before its identity was journaled')
            deadline = time.monotonic()+5
            while True:
                probe = subprocess.run(['ps','-p',str(pid),'-o','pid='],
                    capture_output=True,text=True,timeout=5)
                self.assertIn(probe.returncode, (0,1), probe.stderr)
                if not probe.stdout.strip() or time.monotonic()>=deadline:
                    break
                time.sleep(.02)
            self.assertFalse(probe.stdout.strip(), 'unjournaled launcher survived wrapper death')
            self.assertEqual(self.run_task().returncode, 0)
            self.assertTrue(any(r.get('reason')=='interrupted' for r in self.ledger()))
        finally:
            if wrapper.poll() is None:
                wrapper.kill(); wrapper.wait(timeout=5)
            if pid:
                try: os.killpg(pid,signal.SIGKILL)
                except (ProcessLookupError,PermissionError): pass


def stamp_before():
    return (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()


if __name__ == '__main__':
    unittest.main(verbosity=2)
