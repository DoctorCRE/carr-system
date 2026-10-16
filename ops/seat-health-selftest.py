#!/usr/bin/env python3
import json
import os
import io
import importlib.util
from datetime import datetime, timedelta, timezone
from unittest import mock
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import seat_health as health
import host_liveness


spec = importlib.util.spec_from_file_location('seat_cli', Path(__file__).with_name('seat-health.py'))
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class SeatHealthTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        # Hermetic: no real tailnet read (unknown liveness keeps the old placement).
        patch = mock.patch.dict(os.environ, {'HOME': self.home.name,
                                             'TAILSCALE_BIN': os.path.join(self.home.name, 'no-tailscale')})
        patch.start()
        self.addCleanup(patch.stop)

    def invoke(self, root, *args, probe=None, reconcile=None, publish=None, live=None, out=None):
        command = ['seat-health', '--runtime-repo', str(root), '--output-root', str(root / 'results'), *args]
        config = host_liveness.load_config()
        live = live or host_liveness.liveness(None, config)
        with mock.patch.object(sys, 'argv', command), mock.patch.object(sys, 'stdout', out or io.StringIO()), \
             mock.patch.object(cli, 'probe', side_effect=probe or self.passing), \
             mock.patch.object(cli, 'hosts', return_value=(config, live)), \
             mock.patch.object(cli, 'publish', side_effect=publish or (lambda *a, **k: {'model_route': 'ok'})), \
             mock.patch.object(cli, 'reconcile', side_effect=reconcile or (lambda *a: 'none')):
            return cli.main()

    def passing(self, seat, *args):
        return health.assess(seat, '323', '', 0, .01)

    def test_01_jev_conformance(self):
        spec = importlib.util.spec_from_file_location('conformance', Path(__file__).with_name('check-jev-conformance.py'))
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.assertEqual(checker.python_errors(Path(health.__file__).read_text()), [])

    def test_01_jev_rejects_cached_observations(self):
        import jev_semantic
        fresh = {'model': 'jev-1.13.0', 'answers': {'answer': {'choice': '323'}}}
        seen = []
        def request(state, *a, **kw):
            seen.append(state['run_id'])
            return fresh
        with mock.patch.object(jev_semantic, 'ask', side_effect=request), mock.patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(health.probe_jev('run-one'), 0)
            self.assertEqual(health.probe_jev('run-two'), 0)
        self.assertEqual(seen, ['run-one', 'run-two'])
        with mock.patch.object(jev_semantic, 'ask', return_value=dict(fresh, cache_hit=True)), mock.patch.object(sys, 'stdout', io.StringIO()):
            self.assertEqual(health.probe_jev('run-three'), 1)

    def test_02_observations_do_not_complete_daily_recording(self):
        for options in [('--seat', 'grok', '--no-record'), ('--no-record',)]:
            with self.subTest(options=options), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                observed = self.passing if len(options) > 1 else lambda seat, *a: health.assess(seat, '', '', 1, .01)
                self.invoke(root, *options, probe=observed)
                seen = []
                def record(row, *args):
                    seen.append(row['seat'])
                    return 'none'
                self.assertEqual(self.invoke(root, reconcile=record), 0)
                self.assertEqual(seen, list(health.SEATS))
                seen.clear()
                self.assertEqual(self.invoke(root, reconcile=record), 0)
                self.assertEqual(seen, [])

    def test_03_refresh_preserves_unselected_and_interrupted_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            output = root / 'results'
            report = output / 'seat-health.json'
            seed = {seat: dict(self.passing(seat), observed_at=datetime.now(timezone.utc).isoformat()) for seat in health.SEATS}
            health.atomic_json(report, {'schema': 'carr-seat-health/v1', 'seats': seed})
            self.invoke(root, '--seat', 'grok', '--no-record')
            self.assertEqual(set(json.loads(report.read_text())['seats']), set(health.SEATS))
            count = 0
            def interrupted(seat, *args):
                nonlocal count
                count += 1
                if count == 2:
                    saved = json.loads(report.read_text())
                    self.assertTrue(all(health.dispatchable(saved, s) for s in health.SEATS))
                    raise KeyboardInterrupt()
                return self.passing(seat)
            with self.assertRaises(KeyboardInterrupt):
                self.invoke(root, '--force', '--no-record', probe=interrupted)
            self.assertEqual(set(json.loads(report.read_text())['seats']), set(health.SEATS))

    def test_04_telemetry_cannot_escape_deadline(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'sol-studio.log').write_text('model: gpt-6.1-sol\ncodex\n323\nCODEX_EXIT 0\n')
            def delayed(*a):
                time.sleep(.6)
                return None
            started = time.monotonic()
            with mock.patch.object(health, 'execute', return_value=('', '', 0, .01)), mock.patch.object(health, 'codex_usage', side_effect=delayed):
                row = health.probe('sol-studio', root, root, .05, '')
            self.assertLess(time.monotonic() - started, .4)
            self.assertFalse(row['passed'])
            self.assertGreaterEqual(row['latency_seconds'], .05)

    def test_05_builders_require_completion_and_model(self):
        good = {'type': 'result', 'subtype': 'success', 'result': '323', 'is_error': False, 'modelUsage': {'opus-5.5': {}}}
        cases = [('opus-studio', '323', False),
                 ('opus-studio', json.dumps(good) + '\nOPUS_EXIT 0', True),
                 ('opus-studio', json.dumps(dict(good, subtype='error_max_turns')) + '\nOPUS_EXIT 0', False),
                 ('opus-studio', json.dumps(dict(good, modelUsage={'other': {}})) + '\nOPUS_EXIT 0', False),
                 ('sol-studio', 'model: other\ncodex\n323\nCODEX_EXIT 0', False),
                 ('sol-studio', 'model: gpt-6.1-sol\ncodex\n323\nCODEX_EXIT 0', True),
                 ('sol-studio', 'model: gpt-6.1-sol\ncodex\n323', False),
                 ('sol-studio', 'CODEX_EXIT 0\nmodel: gpt-6.1-sol\ncodex\n323', False)]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for seat, raw, passed in cases:
                with self.subTest(seat=seat, raw=raw):
                    (root / (seat + '.log')).write_text(raw)
                    with mock.patch.object(health, 'execute', return_value=('', '', 0, .01)):
                        row = health.probe(seat, root, root, 1, '')
                    self.assertEqual(row['passed'], passed)

    def test_06_malformed_values_fail_closed_and_continue(self):
        report = {'schema': 'carr-seat-health/v1', 'seats': {'grok': {'observed_at': None}}}
        self.assertFalse(health.dispatchable(report, 'grok'))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'session.jsonl').write_text(json.dumps({'timestamp': None, 'payload': {'rate_limits': {'used_percent': 4}}}) + '\n')
            self.assertIsNone(health.codex_usage(root))
            with mock.patch.object(health, 'execute', return_value=(json.dumps({'error': {'message': 'bad'}}), '', 1, .01)):
                self.assertFalse(health.probe('jev', root, root, 1, '')['passed'])
            health.atomic_json(root / 'results/seat-health-grok-loop.json', [])
            seen = []
            def observed(seat, *args):
                seen.append(seat)
                return health.assess(seat, '', '', 1, .01)
            self.invoke(root, '--seat', 'grok', '--seat', 'jev', probe=observed, reconcile=lambda row, path, verb: health.reconcile(row, path, lambda *a: {'ok': False}))
            self.assertEqual(seen, ['grok', 'jev'])
            self.assertEqual(set(json.loads((root / 'results/seat-health.json').read_text())['seats']), {'grok', 'jev'})

    def test_07_torn_and_malformed_ledger_recovers(self):
        for tail in ['{"day":', '[]', '{"day": "today"}', json.dumps({'completed_recording': True, 'day': datetime.now(timezone.utc).date().isoformat(), 'passed': True, 'seats': [{}] * 6})]:
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                ledger = root / 'results/seat-health-runs.jsonl'
                ledger.parent.mkdir()
                ledger.write_text(tail)
                self.assertEqual(self.invoke(root), 0)
                lines = ledger.read_text().splitlines()
                self.assertIsInstance(json.loads(lines[-1]), dict)
                self.assertEqual(self.invoke(root), 0)

    def test_07_interrupted_ledger_publication_does_not_disable_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ledger = root / 'results/seat-health-runs.jsonl'
            ledger.parent.mkdir()
            old = json.dumps({'day': '2020-01-01', 'passed': True, 'seats': list(health.SEATS), 'completed_recording': True}) + '\n'
            ledger.write_text(old)
            replace = Path.replace
            def interrupted(path, target):
                if target == ledger:
                    raise OSError('interrupted publication')
                return replace(path, target)
            with mock.patch.object(Path, 'replace', interrupted), self.assertRaises(OSError):
                self.invoke(root)
            self.assertEqual(ledger.read_text(), old)
            self.assertEqual(self.invoke(root), 0)
            seen = []
            self.invoke(root, probe=lambda seat, *a: seen.append(seat) or self.passing(seat))
            self.assertEqual(seen, [])

    def test_08_closed_failed_loop_replaced_in_same_observation(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'loop.json'
            health.atomic_json(state, {'loop_id': 'old', 'open_key': 'old-key'})
            calls = []
            def verb(name, payload):
                calls.append(name)
                if name == 'add-loop': return {'ok': True, 'loop_id': 'new'}
                if name == 'read-loop': return {'loop': {'status': 'done' if payload['loop_id'] == 'old' else 'open', 'version': 2}}
                return {'ok': True}
            self.assertEqual(health.reconcile(health.assess('grok', '', '', 1, .01), state, verb), 'open')
            self.assertEqual(calls, ['read-loop', 'add-loop', 'read-loop', 'update-loop'])
            self.assertEqual(json.loads(state.read_text())['loop_id'], 'new')

    def test_09_relative_runtime_executes_record_verb(self):
        with tempfile.TemporaryDirectory(dir='.') as d:
            root = Path(d).relative_to(Path.cwd())
            runner = root / 'run.sh'
            runner.write_text("#!/bin/sh\nprintf '{\"ok\":true}\\n'\n")
            runner.chmod(0o700)
            self.assertEqual(health.verb_call(root, 'read-loop', {}), {'ok': True})

    def test_10_boot_prose_does_not_classify_terminal_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'sol-studio.log').write_text('model: gpt-6.1-sol\nRule: sign-in needs credentials\ncodex\n323\nCODEX_EXIT 7')
            with mock.patch.object(health, 'execute', return_value=('', 'runner failure', 0, .01)):
                row = health.probe('sol-studio', root, root, 1, '')
            self.assertEqual(row['failing_layer'], 'runner_wrapper')
    maxDiff = 800
    def test_only_exact_answer_passes(self):
        for answer, code, expected in [('323\n', 0, True), ('324', 0, False),
                                       ('', 0, False), ('323', 1, False),
                                       ('The answer is 323', 0, False)]:
            with self.subTest(answer=answer, code=code):
                row = health.assess('grok', answer, '', code, 0.2)
                self.assertEqual(row['passed'], expected)
                self.assertEqual(row['dispatchable'], expected)
                self.assertIn('owner orchestrator', row['action'])
                self.assertIn('auto-clear', row['action'])

    def test_builder_wrapper_log_is_the_answer_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / 'out/orch/sol-run.sh'
            runner.parent.mkdir(parents=True)
            runner.write_text('#!/bin/zsh\nprint -r -- "model: gpt-6.1-sol\ncodex\n323\nrunner receipt noise\ntokens used\n99\n323\nCODEX_EXIT 0" > "$2"\n')
            output = root / 'results'
            command = [sys.executable, str(Path(__file__).with_name('seat-health.py')),
                       '--runtime-repo', str(root), '--output-root', str(output),
                       '--seat', 'sol-studio', '--no-record']
            proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            row = json.loads((output / 'seat-health.json').read_text())['seats']['sol-studio']
            self.assertTrue(row['passed'])
            runner.write_text('#!/bin/zsh\nprint -r -- "codex\n323\nCODEX_EXIT 7" > "$2"\n')
            proc = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 1)
            self.assertFalse(json.loads((output / 'seat-health.json').read_text())['seats']['sol-studio']['passed'])

    def test_failures_name_the_layer_without_echoing_diagnostics(self):
        for diagnostic, code, layer in [('token expired SECRET', 3, 'auth'),
                ('fetch failed SECRET', 1, 'network'), ('wrapper missing SECRET', 127, 'runner_wrapper'),
                ('wrong model SECRET', 5, 'model')]:
            row = health.assess('grok', '', diagnostic, code, .1)
            self.assertEqual(row['failing_layer'], layer)
            self.assertNotIn('SECRET', json.dumps(row))

    def test_normal_transcript_auth_words_cannot_reclassify_a_wrong_answer(self):
        row = health.assess('sol-studio', '324', 'Rule says sign-in needs credentials.', 0, 1)
        self.assertEqual(row['failing_layer'], 'model')

    def test_usage_drops_credentials_and_unknown_fields(self):
        self.assertEqual(health.numeric_usage({'input_tokens': 4, 'token': 'SECRET',
            'primary': {'used_percent': 21, 'password': 'SECRET'}, 'cost_usd': float('nan')}),
            {'input_tokens': 4, 'primary': {'used_percent': 21}})

    def test_unknown_stale_future_and_failed_seats_are_not_dispatchable(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        report = {'schema': 'carr-seat-health/v1', 'observed_at': now.isoformat(), 'seats': {
            'grok': {'passed': True, 'dispatchable': True, 'observed_at': now.isoformat()}}}
        self.assertTrue(health.dispatchable(report, 'grok', now=now))
        self.assertFalse(health.dispatchable(report, 'missing', now=now))
        for at in ['2026-10-03T00:00:00+00:00', '2026-10-06T00:00:00+00:00', 'bad']:
            report['seats']['grok']['observed_at'] = at
            self.assertFalse(health.dispatchable(report, 'grok', now=now))

    def test_one_loop_is_updated_then_cleared_on_exact_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'loop.json'
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                if name == 'add-loop':
                    return {'ok': True, 'loop_id': 'fixture-loop'}
                if name == 'read-loop':
                    return {'ok': True, 'loop': {'version': len(calls), 'status': 'open'}}
                self.assertIn('base_version', payload)
                return {'ok': True}
            fail = health.assess('grok', '', '', 0, 1)
            self.assertEqual(health.reconcile(fail, state, verb), 'open')
            self.assertEqual(health.reconcile(fail, state, verb), 'open')
            self.assertEqual(sum(n == 'add-loop' for n, _ in calls), 1)
            self.assertEqual(health.reconcile(health.assess('grok', '323', '', 0, 1), state, verb), 'cleared')
            self.assertEqual(json.loads(state.read_text()), {})
            self.assertEqual(calls[-1][0], 'close-loop')

    def test_uncertain_open_reuses_key_even_after_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'loop.json'
            keys = []
            def lost_response(name, payload):
                keys.append(payload['idempotency_key'])
                return {'ok': False}
            fail = health.assess('grok', '', '', 0, 1)
            self.assertEqual(health.reconcile(fail, state, lost_response), 'error')
            self.assertEqual(health.reconcile(health.assess('grok', '323', '', 0, 1), state, lost_response), 'error')
            self.assertEqual(keys[0], keys[1])

    def test_timeout_terminates_the_runner(self):
        import sys, time
        started = time.monotonic()
        output, diagnostic, code, _ = health.execute([sys.executable, '-c', 'import time; time.sleep(30)'], '.', .05)
        self.assertEqual(code, 124)
        self.assertLess(time.monotonic() - started, 4)

    def test_missing_runner_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = health.probe('opus-studio', root, root, 1, root / 'e2e')
            self.assertFalse(row['passed'])
            self.assertEqual(row['failing_layer'], 'runner_wrapper')

    def test_daily_schedule_uses_existing_chain(self):
        root = Path(__file__).resolve().parents[1]
        nightly = (root / 'bin/nightly.sh').read_text()
        self.assertIn('step "daily AI seat health" ./.venv/bin/python ops/seat-health.py', nightly)
        required = nightly.split('required=(', 1)[1].split(')', 1)[0]
        for path in ('ops/seat-health.py', 'ops/host_liveness.py', 'tools/room-bridge/dispatch.py'):
            self.assertIn(path, required)

    def test_health_rows_show_bound_actions_without_new_model_calls(self):
        rows = health.health_rows({})
        self.assertEqual(len(rows), 6)
        self.assertTrue(all('on breach:' in line and 'owner orchestrator' in line
                            and 'Verify:' in line and 'auto-clear' in line for line in rows))
        # No observation at all cannot prove the gap is young: past the bound, red.
        self.assertEqual(rows, [line for line in rows if line.startswith('STALE-RED ') and ' last=never ' in line])

    def report_with(self, **seats):
        return {'schema': 'carr-seat-health/v1', 'seats': seats}

    def test_stale_versus_fail_missing_expired_failed_passed(self):
        now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        fresh = '2026-10-12T06:00:00+00:00'
        failed = dict(health.assess('grok', '', 'token expired', 3, .2), observed_at=fresh)
        passed = dict(health.assess('jev', '323', '', 0, .2), observed_at=fresh)
        expired = dict(health.assess('sol-studio', '323', '', 0, .2), observed_at='2026-10-05T06:00:00+00:00')
        future = dict(health.assess('e2e-auth', '323', '', 0, .2), observed_at='2026-10-13T06:00:00+00:00')
        rows = dict(zip(health.SEATS, health.health_rows(self.report_with(
            grok=failed, jev=passed, **{'sol-studio': expired, 'e2e-auth': future,
                                         'sol-macbook': {'observed_at': 'garbage'}}), now=now)))
        self.assertTrue(rows['opus-studio'].startswith('STALE-RED opus-studio last=never '), rows['opus-studio'])
        self.assertTrue(rows['sol-studio'].startswith('STALE-RED sol-studio last=2026-10-05 '), rows['sol-studio'])
        self.assertTrue(rows['e2e-auth'].startswith('STALE e2e-auth last=2026-10-13 '), rows['e2e-auth'])
        self.assertTrue(rows['sol-macbook'].startswith('STALE-RED sol-macbook last=never '), rows['sol-macbook'])
        self.assertTrue(rows['grok'].startswith('FAIL grok latency=0.2s layer=auth '), rows['grok'])
        self.assertTrue(rows['jev'].startswith('PASS jev latency=0.2s layer=none '), rows['jev'])
        # FAIL is reserved for a probe that ran and failed: no other row may say it.
        self.assertEqual([seat for seat, line in rows.items() if line.startswith('FAIL')], ['grok'])
        # A fresh row whose pass bit is not a real boolean is a failed run, never a pass.
        bent = dict(passed, passed='yes')
        self.assertTrue(health.health_rows(self.report_with(jev=bent), now=now)[4].startswith('FAIL jev'))

    def test_stale_escalates_to_red_after_72h_when_host_online_or_unknown(self):
        now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        def row_at(hours):
            return dict(health.assess('grok', '323', '', 0, .2),
                        observed_at=(now - timedelta(hours=hours)).isoformat())
        # Inside the bound: a warning, never red.
        [young] = [l for l in health.health_rows(self.report_with(grok=row_at(48)), now=now) if ' grok ' in l]
        self.assertTrue(young.startswith('STALE grok last='), young)
        self.assertFalse(health.is_red(young))
        [edge] = [l for l in health.health_rows(self.report_with(grok=row_at(72)), now=now) if ' grok ' in l]
        self.assertTrue(edge.startswith('STALE grok '), edge)
        # Past the bound, host online (absent from offline) or unknown: red, with a bound action.
        for offline in (None, {}, {'opus-studio': 'mac-studio'}):
            [old] = [l for l in health.health_rows(self.report_with(grok=row_at(73)), now=now, offline=offline)
                     if ' grok ' in l]
            self.assertTrue(old.startswith('STALE-RED grok last=2026-10-09 '), old)
            self.assertTrue(health.is_red(old))
            for part in ('deduplicated loop seat-health:grok', 'owner orchestrator',
                         'stopped producing rows', 'Verify:', 'auto-clear'):
                self.assertIn(part, old)
        # Past the bound while the host is offline: SKIP, a warning only.
        rows = health.health_rows(self.report_with(grok=row_at(500)), now=now, offline={'grok': 'mac-studio'})
        [skip] = [l for l in rows if 'grok' in l.split(' · ')[0]]
        self.assertTrue(skip.startswith('SKIP grok: host mac-studio offline'), skip)
        self.assertFalse(health.is_red(skip))

    def test_show_exits_nonzero_on_stale_red(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
            seats = {seat: dict(health.assess(seat, '323', '', 0, .1),
                                observed_at=datetime.now(timezone.utc).isoformat()) for seat in health.SEATS}
            seats['grok'] = dict(seats['grok'], observed_at=old)
            health.atomic_json(root / 'results/seat-health.json', self.report_with(**seats))
            out = io.StringIO()
            self.assertEqual(self.invoke(root, '--show', out=out), 1)
            self.assertIn('STALE-RED grok', out.getvalue())

    def test_offline_host_reads_skip_not_fail(self):
        now = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
        failed = dict(health.assess('grok', '', 'runner gone', 127, .2), observed_at='2026-10-12T06:00:00+00:00')
        rows = health.health_rows(self.report_with(grok=failed), now=now,
                                  offline={'grok': 'mac-studio', 'opus-studio': 'mac-studio'})
        self.assertTrue(rows[0].startswith('SKIP opus-studio: host mac-studio offline · on breach:'), rows[0])
        self.assertTrue(rows[3].startswith('SKIP grok: host mac-studio offline · '), rows[3])
        self.assertFalse(any(line.startswith('FAIL') for line in rows))

    def test_show_exits_zero_on_stale_and_skip_and_nonzero_on_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            report = root / 'results/seat-health.json'
            now = datetime.now(timezone.utc)
            seats = {seat: dict(health.assess(seat, '323', '', 0, .1), observed_at=now.isoformat())
                     for seat in health.SEATS}
            seats['grok'] = dict(seats['grok'], observed_at=(now - timedelta(hours=30)).isoformat())
            health.atomic_json(report, self.report_with(**seats))
            out = io.StringIO()
            live = host_liveness.liveness(None, host_liveness.load_config())
            for name in live['hosts']:
                live['hosts'][name]['state'] = 'online'
            live['hosts']['joes-macbook-pro']['state'] = 'offline'
            self.assertEqual(self.invoke(root, '--show', out=out, live=live), 0)
            self.assertIn('STALE grok last=', out.getvalue())
            self.assertIn('SKIP sol-macbook: host joes-macbook-pro offline', out.getvalue())
            health.atomic_json(report, self.report_with(grok=dict(health.assess('grok', '', '', 1, .1),
                observed_at=datetime.now(timezone.utc).isoformat())))
            self.assertEqual(self.invoke(root, '--show'), 1)

    def test_placement_runs_seats_where_their_host_is(self):
        config = host_liveness.load_config()
        def live(me, studio='online', macbook='online'):
            view = host_liveness.liveness(None, config)
            view['self'] = me
            view['hosts']['mac-studio']['state'] = studio
            view['hosts']['joes-macbook-pro']['state'] = macbook
            return view
        macbook = live('joes-macbook-pro', studio='offline')
        self.assertEqual(cli.placement('jev', config, macbook), ('run', False))
        self.assertEqual(cli.placement('sol-macbook', config, macbook), ('run', True))
        self.assertEqual(cli.placement('opus-studio', config, macbook), ('skip', 'host mac-studio offline'))
        self.assertEqual(cli.placement('grok', config, live('joes-macbook-pro')),
                         ('skip', 'runs on mac-studio, not this Mac'))
        studio = live('mac-studio')
        self.assertEqual(cli.placement('grok', config, studio), ('run', False))
        self.assertEqual(cli.placement('sol-macbook', config, studio), ('run', False))
        self.assertEqual(cli.placement('sol-macbook', config, live('mac-studio', macbook='offline')),
                         ('skip', 'host joes-macbook-pro offline'))
        self.assertEqual(cli.placement('grok', config, host_liveness.liveness(None, config)), ('run', False))

    def test_macbook_run_probes_only_its_seats_and_skips_the_offline_studio(self):
        config = host_liveness.load_config()
        status = {'BackendState': 'Running', 'Self': {'DNSName': 'joes-macbook-pro.tailc8cc93.ts.net.'},
                  'Peer': {'k': {'DNSName': 'mac-studio.tailc8cc93.ts.net.', 'Online': False,
                                 'LastSeen': '2026-10-09T23:46:23.1Z'}}}
        live = host_liveness.liveness(status, config)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seen, out = [], io.StringIO()
            def observed(seat, *args):
                seen.append((seat, args[-1]))
                return self.passing(seat)
            self.assertEqual(self.invoke(root, '--no-record', probe=observed, live=live, out=out), 0)
            self.assertEqual(seen, [('sol-macbook', True), ('jev', False)])
            self.assertIn('SKIP opus-studio: host mac-studio offline', out.getvalue())
            self.assertEqual(set(json.loads((root / 'results/seat-health.json').read_text())['seats']),
                             {'sol-macbook', 'jev'})
            ledger = [json.loads(line) for line in (root / 'results/seat-health-runs.jsonl').read_text().splitlines()]
            self.assertFalse(ledger[-1]['completed_recording'])

    def test_local_sol_macbook_probe_runs_this_macs_wrapper(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runner = root / 'out/orch/sol-run.sh'
            runner.parent.mkdir(parents=True)
            runner.write_text('#!/bin/zsh\nprint -r -- "model: gpt-6.1-sol\ncodex\n323\nCODEX_EXIT 0" > "$2"\n')
            calls = []
            real = health.execute
            def spy(argv, *a, **k):
                calls.append(argv[0])
                return real(argv, *a, **k)
            with mock.patch.object(health, 'execute', side_effect=spy):
                row = health.probe('sol-macbook', root, root, 10, '', here=True)
            self.assertTrue(row['passed'], row)
            self.assertNotIn('ssh', calls)
            (root / 'out/orch/mac-sol-run.sh').write_text('#!/bin/zsh\nprint -r -- "codex\n324\nCODEX_EXIT 0" > "$2"\n')
            self.assertEqual(health.local_sol_wrapper(root).name, 'mac-sol-run.sh')
            self.assertFalse(health.probe('sol-macbook', root, root, 10, '', here=True)['passed'])

    def dashboard(self, seats=None, route_extra=None, compute=None):
        route = dict(route_extra or {})
        if seats is not None:
            route['seats'] = seats
        return {'ok': True, 'providers': [
            {'provider': 'model_route', 'model_route': route or None, 'state': 'stale',
             'observed_at': '2026-10-01T00:00:00+00:00'},
            dict({'provider': 'local_compute', 'state': 'collector_absent'}, **(compute or {}))]}

    def test_record_layer_first_local_file_second(self):
        old, new = '2026-10-11T06:00:00+00:00', '2026-10-12T06:00:00+00:00'
        dash = self.dashboard({'grok': {'passed': True, 'observed_at': new, 'source': 'dash'},
                               'jev': {'passed': True, 'observed_at': old, 'source': 'dash'},
                               'opus-studio': {'passed': True, 'observed_at': new, 'source': 'dash'},
                               'bogus': {'observed_at': new}})
        local = self.report_with(grok={'observed_at': old, 'source': 'file'},
                                 jev={'observed_at': new, 'source': 'file'},
                                 **{'opus-studio': {'observed_at': new, 'source': 'file'},
                                    'sol-macbook': {'observed_at': old, 'source': 'file'}})
        seats = health.combined_report(dash, local)['seats']
        self.assertEqual({seat: row['source'] for seat, row in seats.items()},
                         {'grok': 'dash', 'jev': 'file', 'opus-studio': 'dash', 'sol-macbook': 'file'})
        self.assertEqual(health.combined_report(None, local)['seats'], local['seats'])
        self.assertEqual(health.combined_report({'ok': True, 'providers': 'x'}, {})['seats'], {})

    def test_current_report_reads_the_dashboard_through_the_repo_door_then_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            fresh = datetime.now(timezone.utc).isoformat()
            health.atomic_json(repo / 'out/orch/budget/seat-health.json',
                               self.report_with(grok={'passed': True, 'observed_at': fresh, 'source': 'file'}))
            dash = self.dashboard({'jev': {'passed': False, 'observed_at': fresh, 'source': 'dash'}})
            with mock.patch.object(health, 'verb_call', return_value=dash) as door:
                seats = health.current_report(repo)['seats']
            self.assertEqual(door.call_args.args, (repo, 'read-resource-dashboard', {}))
            self.assertEqual({seat: row['source'] for seat, row in seats.items()}, {'grok': 'file', 'jev': 'dash'})
            with mock.patch.object(health, 'verb_call', return_value={'ok': False}):
                self.assertEqual(set(health.current_report(repo, repo / 'missing.json')['seats']), set())

    def test_publish_writes_merged_seats_and_host_liveness_through_the_door(self):
        config = host_liveness.load_config()
        status = {'BackendState': 'Running', 'Self': {'DNSName': 'joes-macbook-pro.tailc8cc93.ts.net.'},
                  'Peer': {'k': {'DNSName': 'mac-studio.tailc8cc93.ts.net.', 'Online': False,
                                 'LastSeen': '2026-10-09T23:46:23.1Z'}}}
        live = host_liveness.liveness(status, config)
        studio_row = {'passed': False, 'observed_at': '2026-10-05T06:00:00+00:00', 'failing_layer': 'auth'}
        dash = self.dashboard({'grok': studio_row}, route_extra={'endpoint': 'http://127.0.0.1:8000'},
                              compute={'measured_capacity': {'cpu_cores': 24},
                                       'observed_at': '2026-10-02T00:00:00+00:00',
                                       'configured_capacity': {'host': 'mac-studio'}})
        calls = []
        def door(name, payload):
            calls.append((name, payload))
            return dash if name == 'read-resource-dashboard' else {'ok': True}
        jev = dict(health.assess('jev', '323', '', 0, .3), observed_at='2026-10-12T06:00:00+00:00',
                   record_action='none', usage={'input_tokens': 4})
        result = health.publish({'jev': jev}, door, live=live, source_host='joes-macbook-pro')
        self.assertEqual(result, {'model_route': 'ok', 'local_compute': 'ok'})
        writes = {payload['provider']: payload for name, payload in calls if name == 'record-resource-observation'}
        route = writes['model_route']
        self.assertEqual(set(route['model_route']['seats']), {'jev', 'grok'})
        self.assertEqual(route['model_route']['seats']['jev'],
                         {key: jev[key] for key in health.SEAT_ROW_FIELDS})
        self.assertNotIn('endpoint', route['model_route'])
        self.assertEqual(route['model_route']['carried_forward'],
                         {'endpoint': {'value': 'http://127.0.0.1:8000',
                                       'observed_at': '2026-10-01T00:00:00+00:00'}})
        self.assertEqual(route['model_route']['seat_source_host'], 'joes-macbook-pro')
        self.assertEqual((route['state'], route['reason']), ('partial', 'seats not passing: grok'))
        compute = writes['local_compute']
        self.assertNotIn('cpu_cores', compute['measured_capacity'])
        self.assertEqual(compute['measured_capacity']['carried_forward'],
                         {'cpu_cores': {'value': 24, 'observed_at': '2026-10-02T00:00:00+00:00'}})
        self.assertEqual(compute['measured_capacity']['hosts']['mac-studio'],
                         {'state': 'offline', 'last_seen': '2026-10-09T23:46Z'})
        self.assertEqual(compute['configured_capacity'], {'host': 'mac-studio'})
        self.assertEqual((compute['state'], compute['reason']),
                         ('host_offline', 'host offline: mac-studio since 2026-10-09T23:46Z'))
        for payload in writes.values():
            self.assertEqual(payload['source'], 'ops/seat-health.py')
            self.assertTrue(payload['idempotency_key'] and payload['observed_at'])
            self.assertNotIn('usage', json.dumps(payload))
        self.assertNotEqual(writes['model_route']['idempotency_key'], writes['local_compute']['idempotency_key'])
        # A refused write and an unreadable dashboard are reported, never raised.
        self.assertEqual(health.publish({'jev': jev}, lambda name, payload: {'ok': False}),
                         {'model_route': 'error'})
        def broken(name, payload):
            raise OSError('door down')
        self.assertEqual(health.publish({'jev': jev}, broken, live=live),
                         {'model_route': 'error', 'local_compute': 'error'})

    def test_publish_never_restamps_a_foreign_measurement(self):
        # A carried key keeps the stamp it was first measured at across publishes.
        writes = {}
        dash = self.dashboard({}, route_extra={'endpoint': 'x'},
                              compute={'measured_capacity': {'cpu_cores': 24},
                                       'observed_at': '2026-10-02T00:00:00+00:00'})
        jev = dict(health.assess('jev', '323', '', 0, .3), observed_at='2026-10-12T06:00:00+00:00')
        live = host_liveness.liveness(None, host_liveness.load_config())
        for _ in range(2):
            def door(name, payload):
                if name == 'read-resource-dashboard':
                    return dash
                writes[payload['provider']] = payload
                return {'ok': True}
            health.publish({'jev': jev}, door, live=live, now=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
            dash = {'ok': True, 'providers': [dict(payload, observed_at=payload['observed_at'])
                                              for payload in writes.values()]}
        self.assertEqual(writes['model_route']['observed_at'], '2026-10-12T12:00:00+00:00')
        self.assertEqual(writes['model_route']['model_route']['carried_forward'],
                         {'endpoint': {'value': 'x', 'observed_at': '2026-10-01T00:00:00+00:00'}})
        self.assertEqual(writes['local_compute']['measured_capacity']['carried_forward'],
                         {'cpu_cores': {'value': 24, 'observed_at': '2026-10-02T00:00:00+00:00'}})
        for payload in writes.values():
            self.assertNotIn('endpoint', payload.get('model_route') or {})
            self.assertNotIn('cpu_cores', payload.get('measured_capacity') or {})

    def test_unreadable_tailnet_publishes_unknown_never_ok(self):
        config = host_liveness.load_config()
        def payloads(status):
            writes = {}
            def door(name, payload):
                if name != 'read-resource-dashboard':
                    writes[payload['provider']] = payload
                return {'ok': True}
            health.publish({}, door, live=host_liveness.liveness(status, config))
            return writes['local_compute']
        for status in (None, {'BackendState': 'Stopped'}):
            compute = payloads(status)
            self.assertNotEqual(compute['state'], 'ok')
            self.assertEqual(compute['state'], 'partial')
            self.assertTrue(compute['reason'].startswith('host liveness unknown: tailnet not readable'),
                            compute['reason'])
            self.assertTrue(all(row['state'] == 'unknown'
                                for row in compute['measured_capacity']['hosts'].values()))
        peers = {f'p{i}': {'DNSName': spec['dns_name'] + '.', 'Online': True}
                 for i, spec in enumerate(config['hosts'].values())}
        first = next(iter(config['hosts'].values()))['dns_name'] + '.'
        compute = payloads({'BackendState': 'Running', 'Self': {'DNSName': first}, 'Peer': peers})
        self.assertEqual((compute['state'], compute['reason']), ('ok', None))

    def test_recorded_run_publishes_and_a_refused_publish_fails_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seen = []
            def published(rows, door, **kw):
                seen.append(sorted(rows))
                return {'model_route': 'ok'}
            self.assertEqual(self.invoke(root, '--seat', 'jev', publish=published), 0)
            self.assertEqual(seen, [['jev']])
            self.assertEqual(self.invoke(root, '--seat', 'jev', '--no-record', publish=published), 0)
            self.assertEqual(len(seen), 1)
            out = io.StringIO()
            self.assertEqual(self.invoke(root, '--seat', 'jev', out=out,
                                         publish=lambda *a, **k: {'model_route': 'error'}), 1)
            self.assertIn('PUBLISH model_route=error', out.getvalue())

    def test_actual_read_loop_shape_supplies_cas_and_done_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'loop.json'
            state.write_text(json.dumps({'loop_id': 'fixture', 'open_key': 'fixture-key'}))
            calls = []
            def verb(name, payload):
                calls.append((name, payload))
                if name == 'read-loop':
                    return {'loop': {'version': 7, 'status': 'open'}}
                return {'ok': True}
            passed = health.assess('jev', '323', '', 0, .1)
            self.assertEqual(health.reconcile(passed, state, verb), 'cleared')
            self.assertEqual(calls[-1][1]['base_version'], 7)

    def test_e2e_auth_requires_the_builder_model_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'e2e'
            for text, passed in [('gpt-6-luna Available', True), ('Other models are available', False)]:
                cli.write_text('#!/bin/sh\nprintf "%s\n" "' + text + '"\n')
                cli.chmod(0o700)
                row = health.probe('e2e-auth', root, root, 2, cli)
                self.assertEqual(row['passed'], passed)

    def test_conflict_cannot_clear_a_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state.json'
            state.write_text(json.dumps({'loop_id': 'fixture', 'open_key': 'key'}))
            def verb(name, payload):
                return {'loop': {'version': 5, 'status': 'open'}} if name == 'read-loop' else {'ok': False, 'error': 'version_conflict'}
            self.assertEqual(health.reconcile(health.assess('grok', '323', '', 0, 1), state, verb), 'error')
            self.assertEqual(json.loads(state.read_text())['loop_id'], 'fixture')

    def test_corrupt_health_is_never_a_pass(self):
        # Unreadable evidence says the probe cannot be shown to have run: STALE, never PASS.
        for report in [[], {'seats': []}, {'seats': {'grok': []}},
                       {'schema': 'carr-seat-health/v1', 'seats': {'grok': []}},
                       {'schema': 'other', 'seats': {'grok': {'passed': True, 'dispatchable': True,
                                                              'observed_at': datetime.now(timezone.utc).isoformat()}}}]:
            self.assertTrue(all(line.startswith('STALE') for line in health.health_rows(report)), report)


if __name__ == '__main__':
    unittest.main()
