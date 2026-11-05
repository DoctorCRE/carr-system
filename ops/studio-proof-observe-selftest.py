#!/usr/bin/env python3
"""Exercise the explicit receipt reader without accessing installed jobs."""
from contextlib import closing
import json
import os
import shutil
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from git_env import fixture_env

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import outlook_intake, outlook_runtime

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = Path(__file__).with_name('studio-proof-observe.py')


class StudioObservationTest(unittest.TestCase):
    def test_real_producers_through_observer_and_scheduled_wrapper(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, fixture_env(), clear=True):
            root = Path(directory)
            repo = root / 'runtime'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '-q', '-b', 'main')
            git('-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.test',
                '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'runtime', '--allow-empty')
            full_sha = git('rev-parse', 'HEAD')
            path = root / 'receipt.txt'
            def observe(line):
                path.write_text(line)
                return subprocess.run([sys.executable, str(PROGRAM), '--receipt-file', str(path),
                    '--service', 'outlook-intake', '--runtime-sha', full_sha,
                    '--count', 'run.mail_ids', '--minimum', '1'],
                    capture_output=True, text=True, timeout=10, env=fixture_env())
            shared = outlook_runtime.receipt_line('outlook-intake', 'succeeded', 'processed',
                                                  {'run': {'mail_ids': 2}}, repo)
            intake = outlook_intake.receipt_line(
                outlook_intake.Outcome('succeeded', 'processed', {'mail_ids': 2}), repo)
            for line in (shared, intake):
                with self.subTest(producer=line.split(' ', 1)[0]):
                    result = observe(line)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('OBSERVED', result.stdout)
            receipt = json.loads(intake.split(' ', 1)[1])
            self.assertEqual(receipt['runtime_sha'], full_sha)
            for name in ('bin/run-scheduled.sh', 'tools/ops-spool.py'):
                target = root / name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(ROOT / name, target)
            (root / '.venv/bin').mkdir(parents=True)
            (root / '.venv/bin/python').symlink_to(sys.executable)
            child = root / 'receipt.py'
            child.write_text(f'print({intake!r})\n')
            spool = root / 'out/spool.sqlite3'
            result = subprocess.run(['/bin/zsh', str(root / 'bin/run-scheduled.sh'),
                'outlook-intake', 'synthetic.identity', sys.executable, str(child)],
                cwd=root, env={**fixture_env(), 'HOME': str(root), 'CARR_RUN_SPOOL_DB': str(spool)},
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with closing(sqlite3.connect(spool)) as connection:
                rows = connection.execute('select state, argv from spool').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], 'succeeded')
            args = json.loads(rows[0][1])
            recorded = args[args.index('--detail') + 1]
            self.assertEqual(json.loads(recorded), receipt)
            import contextlib
            from datetime import datetime, timedelta, timezone
            import importlib.util
            import io
            from unittest.mock import MagicMock
            from lib.studio_proof import Requirement, Source, Installation, Evidence, observe as join
            spec = importlib.util.spec_from_file_location('boundary_recorder', ROOT / 'tools/ops-record.py')
            recorder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(recorder)
            conn, cur = MagicMock(), MagicMock()
            conn.__enter__.return_value = conn
            conn.cursor.return_value.__enter__.return_value = cur
            run_id = '00000000-0000-4000-8000-000000000001'
            cur.fetchone.return_value = (run_id,)
            with patch.object(recorder, 'connect', return_value=conn), \
                 patch.object(recorder, 'service_id', return_value='synthetic-service'), \
                 patch.object(recorder, '_record_success_recovery', return_value=[]), \
                 patch.object(recorder, '_reap_expired_runs'), \
                 patch.object(sys, 'argv', ['ops-record.py', 'run', *args]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(recorder.main(), 0)
            params = next(call.args[1] for call in cur.execute.call_args_list
                          if 'insert into ops.run' in call.args[0])
            now = datetime.now(timezone.utc)
            cur.fetchall.return_value = [(run_id, params[1], 'outlook-intake', params[5], params[4],
                params[15], params[16], params[6], params[8], params[10], params[11], now,
                now+timedelta(hours=1), params[20])]
            with patch.object(recorder, 'connect', return_value=conn):
                batch = recorder.read_run_batch('outlook-intake', 'synthetic.identity', 'production',
                                               now-timedelta(hours=1), now)
            requirement = Requirement('synthetic-boundary', 'outlook-intake', 'synthetic.identity',
                                      'production', 'studio', 'run.mail_ids', 1, 3600)
            source = Source('DoctorCRE/carr-system', full_sha, full_sha, 'studio')
            install = Installation('studio', full_sha, now-timedelta(minutes=20),
                                   now-timedelta(minutes=10), True, True, True, True)
            evidence = Evidence(batch, install, full_sha, {run_id: 'studio'}, source_ancestry_verified=True)
            observed = join(requirement, source, evidence, now)
            self.assertEqual(observed.state, 'live-proven', observed)
            self.assertEqual(observed.run_id, run_id)
            self.assertEqual(batch.runs[0].counts, receipt['counts'])
            result = observe('outlook-intake-receipt ' + recorded)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for changes in ({'runtime_sha': full_sha[:9]}, {'runtime_sha': '0' * 40},
                            {'state': 'failed'}, {'state': 'skipped'},
                            {'counts': {'run': {'mail_ids': True}}}):
                with self.subTest(changes=changes):
                    line = 'outlook-intake-receipt ' + json.dumps({**receipt, **changes})
                    result = observe(line)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn('BLOCKED', result.stdout)
            missing = outlook_runtime.receipt_line('outlook-intake', 'succeeded', 'processed',
                                                    {'run': {'mail_ids': 2}}, root / 'missing')
            self.assertIsNone(json.loads(missing.split(' ', 1)[1])['runtime_sha'])
            self.assertEqual(observe(missing).returncode, 1)

    def test_canonical_cli_acquires_in_process_and_cannot_certify_supplied_json(self):
        import contextlib
        from datetime import datetime, timedelta, timezone
        import importlib.util
        import io
        from lib.studio_proof import Run, RunBatch, acquire_evidence
        spec = importlib.util.spec_from_file_location('studio_observer', PROGRAM)
        observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observer)
        now = datetime.now(timezone.utc)
        batch = RunBatch('ok', True, False, now-timedelta(hours=1), now, ())
        arguments = ['--service', 'outlook-intake', '--key', 'outlook.intake',
                     '--environment', 'production', '--runtime-sha', 'a'*40,
                     '--merge-sha', 'a'*40, '--requirement-id', 'identity',
                     '--since', (now-timedelta(hours=1)).isoformat(),
                     '--count', 'run.mail_ids', '--minimum', '1', '--max-age-seconds', '3600']
        with patch('lib.studio_proof.read_runs', return_value=batch) as read, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(observer.canonical_main(arguments), 1)
        read.assert_called_once()
        result = json.loads(output.getvalue())
        self.assertEqual(result['state'], 'held')
        self.assertIn('target binding unavailable', result['reasons'][0])
        self.assertIn('actual installation interval', result['reasons'][0])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            observer.canonical_main(arguments + ['--receipt-file', '/tmp/untrusted.json'])

    def test_completed_receipt_and_blocked_prerequisites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.txt'
            receipt = {'state': 'succeeded', 'runtime_sha': 'a' * 40,
                       'counts': {'run': {'processed': 2}}}
            def observe(value):
                path.write_text('outlook-intake-receipt ' + json.dumps(value))
                return subprocess.run([sys.executable, str(PROGRAM), '--receipt-file', str(path),
                    '--service', 'outlook-intake', '--runtime-sha', 'a' * 40,
                    '--count', 'run.processed', '--minimum', '1'],
                    capture_output=True, text=True, timeout=10, env=fixture_env())
            result = observe(receipt)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('OBSERVED', result.stdout)
            for changes in [{'state': 'failed'}, {'state': 'skipped'},
                            {'runtime_sha': 'aaaaaaa'}, {'runtime_sha': 'b' * 40},
                            {'counts': {}}, {'counts': {'run': {'processed': 0}}},
                            {'counts': {'run': {'processed': True}}}]:
                with self.subTest(changes=changes):
                    result = observe({**receipt, **changes})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('BLOCKED', result.stdout)
            path.write_text('refresh succeeded')
            command = [sys.executable, str(PROGRAM), '--receipt-file', str(path),
                       '--service', 'outlook-intake', '--runtime-sha', 'a' * 40,
                       '--count', 'run.processed', '--minimum', '1']
            for text in ['refresh succeeded', 'x' * 1048577]:
                path.write_text(text)
                self.assertNotEqual(subprocess.run(command, capture_output=True, env=fixture_env()).returncode, 0)
            path.unlink()
            self.assertNotEqual(subprocess.run(command, capture_output=True, env=fixture_env()).returncode, 0)


if __name__ == '__main__':
    unittest.main()
