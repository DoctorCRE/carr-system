#!/usr/bin/env python3
import json
import os
import io
import importlib.util
from datetime import datetime, timezone
from unittest import mock
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import seat_health as health


spec = importlib.util.spec_from_file_location('seat_cli', Path(__file__).with_name('seat-health.py'))
assert spec is not None and spec.loader is not None
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class SeatHealthTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patch = mock.patch.dict(os.environ, {'HOME': self.home.name})
        patch.start()
        self.addCleanup(patch.stop)

    def invoke(self, root, *args, probe=None, reconcile=None):
        command = ['seat-health', '--runtime-repo', str(root), '--output-root', str(root / 'results'), *args]
        with mock.patch.object(sys, 'argv', command), mock.patch.object(sys, 'stdout', io.StringIO()), \
             mock.patch.object(cli, 'probe', side_effect=probe or self.passing), \
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
            self.assertEqual(proc.returncode, 0, proc.stderr)
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
        self.assertIn('ops/seat-health.py', nightly.split('required=(', 1)[1].split(')', 1)[0])

    def test_health_rows_show_bound_actions_without_new_model_calls(self):
        rows = health.health_rows({})
        self.assertEqual(len(rows), 6)
        self.assertTrue(all('on breach:' in line and 'owner orchestrator' in line
                            and 'Verify:' in line and 'auto-clear' in line for line in rows))
        self.assertTrue(all(line.startswith('FAIL') for line in rows))

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

    def test_corrupt_health_is_visible_as_failure(self):
        for report in [[], {'seats': []}, {'seats': {'grok': []}}]:
            self.assertTrue(all(line.startswith('FAIL') for line in health.health_rows(report)))


if __name__ == '__main__':
    unittest.main()
