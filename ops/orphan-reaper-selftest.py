#!/usr/bin/env python3
"""Offline orphan-process-reaper contract tests. No live processes are signaled."""
import importlib.util
import io
import json
import os
import plistlib
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
import subprocess
import unittest
from dataclasses import replace
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env
ENV = fixture_env()
from lib import launchd_calendar
spec = importlib.util.spec_from_file_location('orphan_reaper', ROOT / 'ops/orphan-reaper.py')
assert spec is not None and spec.loader is not None
r = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)


class ReaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.config = r.load_config(ROOT / 'ops/config/orphan-reaper.v1.json')
        self.p = r.Process(42, 1, 501, 'Mon Oct  5 01:00:00 2026', 4000, 99,
                           '/bin/zsh', '/bin/zsh -c source /Users/test/.claude/shell-snapshots/a.sh; while :; do :; done', '')

    def scan(self, table, state, now, managed=frozenset()):
        return r.scan(table, managed, state, now, self.config, 501, Path('/Users/test'))

    def mature(self, p=None):
        p = p or self.p
        state = {}
        for now in range(0, 1801, 300):
            state, candidates = self.scan([p], state, now)
            self.assertEqual(candidates, [])
        return state

    def test_all_predicates_and_strict_sustained_boundary(self):
        state = self.mature()
        _, candidates = self.scan([self.p], state, 1801)
        self.assertEqual(candidates, [self.p])
        for field, value in [('ppid', 9), ('uid', 502), ('comm', 'node'),
                             ('cpu', 50), ('age', 1800), ('args', '/bin/zsh -c while true; do :; done')]:
            with self.subTest(field=field):
                _, candidates = self.scan([replace(self.p, **{field: value})], state, 1801)
                self.assertEqual(candidates, [])

    def test_scratch_directory_and_codex_snapshot_provenance(self):
        for cwd, args in [('/private/tmp/claude-501/job/scratchpad', '/bin/bash -c while :; do :; done'),
                          ('', '/bin/bash -c source /Users/test/.codex/shell_snapshots/a.sh; while :; do :; done')]:
            p = replace(self.p, comm='/bin/bash', cwd=cwd, args=args)
            _, candidates = self.scan([p], self.mature(p), 1801)
            self.assertEqual(candidates, [p])
        for cwd in ['/private/tmp/claude-501-evil/job', '/tmp/unrelated', '/private/tmp/claude-502/job']:
            p = replace(self.p, cwd=cwd, args='/bin/zsh -c while :; do :; done')
            self.assertEqual(self.scan([p], {}, 0)[0]['observations'], {})

    def test_low_cpu_missing_sample_and_pid_reuse_reset_history(self):
        state = self.mature()
        for table, now in [([replace(self.p, cpu=49)], 1801), ([], 1801),
                           ([self.p], 2300), ([replace(self.p, started='new process')], 1801),
                           ([self.p], -1)]:
            with self.subTest(now=now, table=table):
                new, candidates = self.scan(table, state, now)
                self.assertEqual(candidates, [])
                self.assertEqual(self.scan([self.p], new, now + 1)[1], [])

    def test_config_change_resets_sustained_window(self):
        state = self.mature()
        self.config = dict(self.config, cpu_threshold=98)
        self.assertEqual(self.scan([self.p], state, 1801)[1], [])

    def test_never_reap_managed_jobs_and_trees_even_after_reparenting(self):
        parent = replace(self.p, pid=10, ppid=1)
        child = replace(self.p, pid=42, ppid=10)
        state, candidates = self.scan([parent, child], {}, 0, {10})
        self.assertEqual(candidates, [])
        self.assertIn(r.identity(child), state['protected'])
        for now in range(300, 2401, 300):
            state, candidates = self.scan([self.p], state, now)
            self.assertEqual(candidates, [])

    def test_named_protections_and_live_node_parent(self):
        for args in ['com.carr.any-job', 'tools/merge_queue/main.py', 'dot-relay.py',
                     'dot-feeder.py', 'dot-supervisor.py', 'dispatch.py', 'codex', 'node test.js']:
            p = replace(self.p, args=self.p.args + '; ' + args)
            with self.subTest(args=args):
                self.assertEqual(self.scan([p], self.mature(), 1801)[1], [])
        self.assertEqual(self.scan([replace(self.p, ppid=100, comm='node')], self.mature(), 1801)[1], [])

    def run_fixture(self, *, dry=False, changed=None, survives=True, reporting_error=False):
        clock = [1801.0]
        signals, reports, collections = [], [], [0]
        out = self.repo / 'out'
        out.mkdir(exist_ok=True)
        (out / 'orphan-reaper-state.json').write_text(json.dumps(self.mature()))
        def collect():
            collections[0] += 1
            if signals and not survives:
                return [], set()
            return [changed if changed and collections[0] > 1 else self.p], set()
        def report(payload):
            reports.append(payload)
            if reporting_error:
                raise RuntimeError('record layer unavailable')
        count = r.run_once(self.repo, self.config, collect=collect, clock=lambda: clock[0],
                           send_signal=lambda pid, sig: signals.append((pid, sig)),
                           sleep=lambda n: clock.__setitem__(0, clock[0] + n), reporter=report,
                           dry_run=dry, uid=501, home=Path('/Users/test'))
        return count, signals, reports, clock[0]

    def test_sensitive_arguments_never_leave_census(self):
        secret = 'synthetic-token-client@example.invalid'
        self.p = replace(self.p, args=self.p.args + ' ' + secret)
        with self.assertRaises(RuntimeError):
            self.run_fixture(reporting_error=True)
        for path in (self.repo / 'out').iterdir():
            self.assertNotIn(secret, path.read_text())
        reports = []
        r.run_once(self.repo, self.config, collect=lambda: ([], set()), clock=lambda: 2000,
                   reporter=reports.append, uid=501, home=Path('/Users/test'))
        self.assertNotIn(secret, json.dumps(reports))

    def test_transport_timeout_does_not_print_payload(self):
        def transport(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 30)
        with patch.object(r, 'run_command', side_effect=transport):
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaises(RuntimeError) as error:
                r.record_defect(self.repo, {'idempotency_key': 'test'})
            self.assertNotIn('idempotency_key', str(error.exception))
            self.assertEqual(stderr.getvalue(), '')

    def test_offline_runs_keep_collecting_reaping_and_recover_all_records(self):
        with self.assertRaises(RuntimeError):
            self.run_fixture(reporting_error=True)
        reports, signals, calls = [], [], []
        other = replace(self.p, pid=43)
        def collect():
            calls.append(True)
            return [other], set()
        def offline(payload):
            raise RuntimeError('record layer unavailable')
        now = [1900.0]
        for poll in range(8):
            now[0] = 1900.0 + poll * 300
            with self.assertRaises(RuntimeError):
                r.run_once(self.repo, self.config, collect=collect, clock=lambda: now[0],
                           sleep=lambda n: now.__setitem__(0, now[0] + n), reporter=offline,
                           send_signal=lambda *args: signals.append(args), uid=501, home=Path('/Users/test'))
        self.assertGreaterEqual(len(calls), 7)
        self.assertIn((43, signal.SIGTERM), signals)
        state = r.read_state(self.repo / 'out/orphan-reaper-state.json')
        self.assertEqual(len(state['pending']), 2)
        keys = {p['idempotency_key'] for p in state['pending']}
        r.run_once(self.repo, self.config, collect=lambda: ([], set()), clock=lambda: 4000,
                   reporter=reports.append, uid=501, home=Path('/Users/test'))
        self.assertEqual({p['idempotency_key'] for p in reports}, keys)
        self.assertEqual(r.read_state(self.repo / 'out/orphan-reaper-state.json')['pending'], [])

    def test_failed_intent_save_never_signals(self):
        original_save, calls, signals = r.save, [0], []
        def save(path, data):
            calls[0] += 1
            if calls[0] >= 3:
                raise OSError('disk full')
            original_save(path, data)
        out = self.repo / 'out'
        out.mkdir()
        (out / 'orphan-reaper-state.json').write_text(json.dumps(self.mature()))
        with patch.object(r, 'save', side_effect=save), self.assertRaises(OSError):
            r.run_once(self.repo, self.config, collect=lambda: ([self.p], set()), clock=lambda: 1801,
                       send_signal=lambda *args: signals.append(args), reporter=lambda _: None,
                       uid=501, home=Path('/Users/test'))
        self.assertEqual(signals, [])
        self.assertEqual(r.read_state(self.repo / 'out/orphan-reaper-state.json')['pending'], [])

    def test_crash_after_signal_keeps_intent_and_never_retries_signal(self):
        class Crash(BaseException):
            pass
        out = self.repo / 'out'
        out.mkdir()
        path = out / 'orphan-reaper-state.json'
        path.write_text(json.dumps(self.mature()))
        def crash(pid, sig):
            state = r.read_state(path)
            self.assertEqual(len(state['pending']), 1)
            actual = state['pending'][0]['actual']
            row = json.loads(actual[actual.index('{'):])
            self.assertEqual(row[sig.name], 'unconfirmed')
            raise Crash()
        with self.assertRaises(Crash):
            r.run_once(self.repo, self.config, collect=lambda: ([self.p], set()), clock=lambda: 1801,
                       send_signal=crash, reporter=lambda _: (_ for _ in ()).throw(RuntimeError('offline')),
                       uid=501, home=Path('/Users/test'))
        reports, signals = [], []
        r.run_once(self.repo, self.config, collect=lambda: ([self.p], set()), clock=lambda: 1802,
                   reporter=reports.append, send_signal=lambda *args: signals.append(args),
                   uid=501, home=Path('/Users/test'))
        self.assertEqual(signals, [])
        self.assertEqual(len(reports), 1)
        actual = reports[0]['actual']
        self.assertEqual(json.loads(actual[actual.index('{'):])['SIGTERM'], 'unconfirmed')

    def test_signal_outcomes_are_durable_and_truthful(self):
        for error, outcome in [(ProcessLookupError(), 'process_missing'), (PermissionError(), 'failed')]:
            with self.subTest(outcome=outcome):
                reports = []
                with patch.object(r.os, 'kill', side_effect=error):
                    out = self.repo / 'out'
                    out.mkdir(exist_ok=True)
                    (out / 'orphan-reaper-state.json').write_text(json.dumps(self.mature()))
                    try:
                        r.run_once(self.repo, self.config, collect=lambda: ([self.p], set()), clock=lambda: 1801,
                                   send_signal=r.os.kill, reporter=reports.append, uid=501, home=Path('/Users/test'))
                    except PermissionError:
                        pass
                self.assertEqual(len(reports), 1)
                actual = reports[0]['actual']
                self.assertEqual(json.loads(actual[actual.index('{'):])['SIGTERM'], outcome)

    def test_incident_time_survives_retry_across_midnight(self):
        before = datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc)
        with patch.object(r.time, 'time', return_value=before.timestamp()), self.assertRaises(RuntimeError):
            self.run_fixture(reporting_error=True)
        state = r.read_state(self.repo / 'out/orphan-reaper-state.json')
        self.assertEqual(state['pending'][0]['occurred_on'], '2026-10-08')
        rows = [json.loads(line) for line in (self.repo / 'out/orphan-reaper.jsonl').read_text().splitlines()]
        self.assertTrue(all(row['occurred_at'] == before.isoformat() for row in rows))
        reports = []
        after = datetime(2026, 10, 9, tzinfo=timezone.utc)
        with patch.object(r.time, 'time', return_value=after.timestamp()):
            r.run_once(self.repo, self.config, collect=lambda: ([], set()), clock=lambda: 2000,
                       reporter=reports.append, uid=501, home=Path('/Users/test'))
        self.assertEqual(reports[0], state['pending'][0])

    def test_term_then_kill_ten_seconds_durable_outcomes_and_finding(self):
        count, signals, reports, now = self.run_fixture()
        self.assertEqual(count, 1)
        self.assertEqual(signals, [(42, signal.SIGTERM), (42, signal.SIGKILL)])
        self.assertGreaterEqual(now, 1811)
        lines = (self.repo / 'out/orphan-reaper.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), 4)
        row = json.loads(lines[0])
        self.assertEqual((row['pid'], row['cpu'], row['age']), (42, 99, 4000))
        self.assertEqual([json.loads(line)['SIGTERM'] for line in lines],
                         ['unconfirmed', 'sent', 'sent', 'sent'])
        self.assertEqual([json.loads(line).get('SIGKILL') for line in lines],
                         [None, None, 'unconfirmed', 'sent'])
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['detected_by'], 'check')
        self.assertEqual(reports[0]['rule_violated'], '36856823')

    def test_term_exit_no_kill_and_dry_run_has_no_effects(self):
        self.assertEqual(self.run_fixture(survives=False)[1], [(42, signal.SIGTERM)])
        (self.repo / 'out/orphan-reaper.jsonl').unlink()
        count, signals, reports, _ = self.run_fixture(dry=True)
        self.assertEqual((count, signals, reports), (1, [], []))
        self.assertFalse((self.repo / 'out/orphan-reaper.jsonl').exists())

    def test_revalidate_before_term_and_kill(self):
        for p in [replace(self.p, ppid=9), replace(self.p, started='reused'),
                  replace(self.p, args='merge_queue/main.py'), replace(self.p, cpu=2)]:
            with self.subTest(process=p):
                self.assertEqual(self.run_fixture(changed=p)[1], [])

    def test_pending_record_retries_same_key_without_resignaling(self):
        with self.assertRaisesRegex(RuntimeError, 'record reporting unavailable'):
            self.run_fixture(reporting_error=True)
        state = json.loads((self.repo / 'out/orphan-reaper-state.json').read_text())
        key = state['pending'][0]['idempotency_key']
        reports, signals = [], []
        r.run_once(self.repo, self.config, collect=lambda: ([], set()), clock=lambda: 1900,
                   send_signal=lambda *args: signals.append(args), reporter=reports.append,
                   uid=501, home=Path('/Users/test'))
        self.assertEqual(signals, [])
        self.assertEqual(reports[0]['idempotency_key'], key)

    def test_new_managed_membership_and_identity_change_before_kill(self):
        for kind in ('managed', 'reused', 'live_parent', 'low_cpu'):
            with self.subTest(kind=kind):
                now, calls, signals = [1801.0], [0], []
                out = self.repo / 'out'
                out.mkdir(exist_ok=True)
                (out / 'orphan-reaper-state.json').write_text(json.dumps(self.mature()))
                def collect():
                    calls[0] += 1
                    p, managed = self.p, set()
                    if calls[0] == 3:
                        if kind == 'managed': managed = {42}
                        elif kind == 'reused': p = replace(p, started='new')
                        elif kind == 'live_parent': p = replace(p, ppid=9)
                        else: p = replace(p, cpu=2)
                    return [p], managed
                r.run_once(self.repo, self.config, collect=collect, clock=lambda: now[0],
                           sleep=lambda n: now.__setitem__(0, now[0] + n),
                           send_signal=lambda *args: signals.append(args), reporter=lambda _: None,
                           uid=501, home=Path('/Users/test'))
                self.assertEqual(signals, [(42, signal.SIGTERM)])

    def test_native_census_failure_and_record_receipt_refusal(self):
        with patch.object(r, 'run_command', return_value=subprocess.CompletedProcess([], 1, '', 'denied')):
            with self.assertRaises(RuntimeError): r.collect_native(self.config)
        for code, stdout in [(1, ''), (0, '{"ok":false}'), (0, 'not json')]:
            with self.subTest(code=code, stdout=stdout), patch.object(r, 'run_command', return_value=subprocess.CompletedProcess([], code, stdout, '')):
                with self.assertRaises(RuntimeError): r.record_defect(self.repo, {'idempotency_key': 'test'})
        with patch.object(r, 'run_command', return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', '')) as command:
            r.record_defect(self.repo, {'idempotency_key': 'test'})
            self.assertEqual(command.call_args.args[0][:3], [str(self.repo/'run.sh'), 'call', 'record-defect'])

    def test_census_failure_after_term_still_records_finding(self):
        out = self.repo / 'out'
        out.mkdir()
        (out / 'orphan-reaper-state.json').write_text(json.dumps(self.mature()))
        now, calls, reports, signals = [1801.0], [0], [], []
        def collect():
            calls[0] += 1
            if calls[0] == 3: raise RuntimeError('census refused')
            return [self.p], set()
        with self.assertRaisesRegex(RuntimeError, 'census refused'):
            r.run_once(self.repo, self.config, collect=collect, clock=lambda: now[0],
                       sleep=lambda n: now.__setitem__(0, now[0] + n),
                       send_signal=lambda *args: signals.append(args), reporter=reports.append,
                       uid=501, home=Path('/Users/test'))
        self.assertEqual(signals, [(42, signal.SIGTERM)])
        self.assertEqual(len(reports), 1)

    def test_process_and_launchd_parsers_fail_closed(self):
        now = time.mktime(time.strptime('Mon Oct 5 01:00:00 2026', '%a %b %d %H:%M:%S %Y')) + 4000
        rows = r.parse_ps('42 1 501 99.0 Mon Oct 5 01:00:00 2026 /bin/zsh /bin/zsh -c :', now)
        self.assertEqual((rows[0].pid, rows[0].ppid, rows[0].comm), (42, 1, '/bin/zsh'))
        self.assertEqual(r.parse_launchctl('PID\tStatus\tLabel\n42\t0\tcom.carr.test\n-\t0\tcom.apple.test'), {42})
        for text in ['broken', '42 1 501 NaN Mon Oct 5 01:00:00 2026 /bin/zsh x']:
            with self.assertRaises(ValueError): r.parse_ps(text, 1791165600)
        with self.assertRaises(ValueError): r.parse_launchctl('broken')

    def test_invalid_config_and_corrupt_state_refused(self):
        for key, value in [('cpu_threshold', True), ('min_age_seconds', -1), ('max_sample_gap_seconds', '300')]:
            path = self.repo / 'bad.json'
            path.write_text(json.dumps(dict(self.config, **{key: value})))
            with self.assertRaises(ValueError): r.load_config(path)
        (self.repo / 'out').mkdir()
        (self.repo / 'out/orphan-reaper-state.json').write_text('{broken')
        with self.assertRaises(ValueError):
            r.run_once(self.repo, self.config, collect=lambda: ([], set()))

    def test_plist_uses_existing_wrapper_and_requested_interval(self):
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.orphan-reaper.plist').read_bytes())
        self.assertEqual(launchd_calendar.cadence_seconds(plist), 300)
        self.assertFalse(plist['RunAtLoad'])
        self.assertIn('{{REPO}}/bin/run-scheduled.sh', plist['ProgramArguments'])
        self.assertIn('{{REPO}}/ops/orphan-reaper.py', plist['ProgramArguments'])


    def test_plist_is_accepted_by_supported_installer(self):
        spec = importlib.util.spec_from_file_location('reaper_installer', ROOT / 'ops/config-as-code.py')
        assert spec and spec.loader
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        source = (ROOT / 'ops/launchd/com.carr.orphan-reaper.plist').read_text()
        # Bind the template to the fixture BEFORE validating it. The unbound
        # template resolves {{REPO}} to whichever checkout runs this suite, and
        # the installer rightly refuses a detached or non-main one (hosted CI).
        source = source.replace('{{REPO}}', str(self.repo))
        self.assertIsNone(installer.launchd_template_refusal(source))
        for target in ('bin/run-scheduled.sh', '.venv/bin/python', 'ops/orphan-reaper.py'):
            path = self.repo / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture only')
        templates, agents = self.repo / 'templates', self.repo / 'agents'
        templates.mkdir()
        agents.mkdir()
        name = 'com.carr.orphan-reaper.plist'
        (templates / name).write_text(source)
        prior = plistlib.loads(installer.concrete(source).encode())
        prior['StandardOutPath'] = str(self.repo / 'old.log')
        (agents / name).write_bytes(plistlib.dumps(prior))
        log, launchctl = self.repo / 'calls', self.repo / 'launchctl'
        launchctl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n')
        launchctl.chmod(0o700)
        result = subprocess.run([sys.executable, str(ROOT / 'ops/config-as-code.py'),
            'reinstall-launchd-calendar', '--templates', str(templates), '--launch-agents', str(agents),
            '--launchctl', str(launchctl), '--apply'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((agents / name).read_text(), installer.concrete(source))
        self.assertIn('bootstrap', log.read_text())

    def test_installer_still_refuses_template_bound_to_detached_checkout(self):
        spec = importlib.util.spec_from_file_location('reaper_installer_refusal', ROOT / 'ops/config-as-code.py')
        assert spec and spec.loader
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        clone = self.repo / 'detached-clone'
        clone.mkdir()
        env = dict(ENV, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
                   GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
        for args in (['init', '-q', '-b', 'main'], ['commit', '-q', '--allow-empty', '-m', 'x'],
                     ['checkout', '-q', '--detach']):
            subprocess.run(['git', '-C', str(clone), *args], check=True, capture_output=True, env=env)
        source = (ROOT / 'ops/launchd/com.carr.orphan-reaper.plist').read_text()
        refusal = installer.launchd_template_refusal(source.replace('{{REPO}}', str(clone)))
        self.assertIsNotNone(refusal)
        self.assertIn('detached HEAD', refusal)
        # The same standalone clone on main is accepted, so the refusal is about the branch.
        subprocess.run(['git', '-C', str(clone), 'checkout', '-q', 'main'], check=True, capture_output=True, env=env)
        self.assertIsNone(installer.launchd_template_refusal(source.replace('{{REPO}}', str(clone))))

    def test_service_catalog_and_workflow_inventory_close(self):
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        service = next((row for row in services if row['key'] == 'orphan-process-reaper'), None)
        self.assertIsNotNone(service)
        assert service is not None
        environment = service['environments'][0]
        self.assertEqual(service['repo_path'], 'ops/orphan-reaper.py')
        self.assertEqual(environment['environment'], 'production')
        self.assertEqual(environment['deploy_mechanism'], 'ops/launchd/com.carr.orphan-reaper.plist')
        self.assertEqual(environment['expected_cadence_seconds'], 300)
        result = subprocess.run(['node', '--input-type=module', '-e',
            "import {workflowDefinitionInventory} from './ops/scac-mutation-inventory.mjs'; workflowDefinitionInventory()"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrapper_records_declared_service_in_isolated_spool(self):
        (self.repo / 'bin').mkdir()
        for directory in ('lib', 'tools'):
            (self.repo / directory).symlink_to(ROOT / directory, target_is_directory=True)
        wrapper = self.repo / 'bin/run-scheduled.sh'
        shutil.copyfile(ROOT / 'bin/run-scheduled.sh', wrapper)
        spool = self.repo / 'spool.sqlite3'
        result = subprocess.run(['/bin/zsh', str(wrapper), 'orphan-process-reaper', 'launchd.run', '/usr/bin/true'],
            env=dict(os.environ, CARR_RUN_SPOOL_DB=str(spool)), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with sqlite3.connect(spool) as db:
            rows = db.execute('select service, run_key, state from spool').fetchall()
        self.assertEqual(rows, [('orphan-process-reaper', 'launchd.run', 'succeeded')])


if __name__ == '__main__':
    unittest.main()
