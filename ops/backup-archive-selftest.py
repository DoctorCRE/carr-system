#!/usr/bin/env python3
"""Exercise backup archive receipts and retention with opaque synthetic bytes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-proof-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for name in ('bin', 'lib', 'backups', 'out', 'remote', 'home', 'mcp-server/node_modules/.bin'):
            (self.repo / name).mkdir(parents=True, exist_ok=True)
        for name in ('bin/backup-archive-r2.py', 'lib/r2_archive.py'):
            shutil.copyfile(ROOT / name, self.repo / name)
        self.wrapper = self.repo / 'mcp-server/node_modules/.bin/wrangler'
        self.wrapper.write_text('''#!/usr/bin/python3
import os, pathlib, shutil, sys, time
args = sys.argv[1:]
target = pathlib.Path(os.environ['FIXTURE_REMOTE']) / args[3].replace('/', '_')
mode = os.environ.get('FIXTURE_MODE', '')
if args[2] == 'put':
    if mode == 'upload_error':
        print('fixture-provider-private-error', file=sys.stderr)
        raise SystemExit(7)
    shutil.copyfile(args[args.index('--file') + 1], target)
elif args[2] == 'get':
    if mode == 'barrier':
        gate = pathlib.Path(os.environ['FIXTURE_REMOTE']).parent
        (gate / 'reading').touch()
        deadline = time.monotonic() + 10
        while not (gate / 'release').exists():
            if time.monotonic() > deadline: raise SystemExit(9)
            time.sleep(0.01)
    if mode == 'wrong':
        sys.stdout.buffer.write(b'wrong opaque ciphertext')
    elif mode == 'get_error':
        raise SystemExit(8)
    elif '--file' in args:
        shutil.copyfile(target, args[args.index('--file') + 1])
    else:
        sys.stdout.buffer.write(target.read_bytes())
''')
        self.wrapper.chmod(0o700)
        self.path = self.repo / 'backups/carr-20260101.sql.age'
        self.path.write_bytes(b'opaque synthetic transport bytes\n')
        self.child_env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.repo / 'home'),
                          'LC_ALL': 'C', 'FIXTURE_REMOTE': str(self.repo / 'remote')}

    def run_archive(self, *args, mode=''):
        return subprocess.run([sys.executable, str(self.repo / 'bin/backup-archive-r2.py'),
                               *(str(a) for a in args or (self.path,))],
                              env={**self.child_env, 'FIXTURE_MODE': mode},
                              capture_output=True, text=True, timeout=15)

    def receipt(self, path=None):
        return json.loads(Path(str(path or self.path) + '.archive.json').read_text())

    def test_wrong_remote_bytes_remain_owed_and_retry_can_verify(self):
        failed = self.run_archive(mode='wrong')
        self.assertEqual(failed.returncode, 1, failed.stdout)
        receipt = json.loads(failed.stdout)
        self.assertIs(receipt['archived'], False)
        self.assertEqual(receipt['reason'], 'readback_failed')
        self.assertEqual(self.receipt(), receipt)
        self.assertTrue(self.path.exists())
        retried = self.run_archive()
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIs(json.loads(retried.stdout)['archived'], True)
        self.assertEqual(self.receipt()['sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_quota_refusal_has_nonzero_status_and_durable_owed_copy(self):
        (self.repo / 'out/r2-usage.json').write_text(json.dumps({
            'objects': {'other': {'bytes': 8 * 1024 ** 3}}}))
        result = self.run_archive()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['reason'], 'quota_exceeded')
        self.assertIs(self.receipt()['archived'], False)
        self.assertTrue(self.path.exists())

    def test_provider_error_is_redacted_and_retry_preserves_ciphertext(self):
        result = self.run_archive(mode='upload_error')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['reason'], 'upload_failed')
        self.assertNotIn('fixture-provider-private-error', result.stdout + result.stderr)
        self.assertEqual(self.run_archive().returncode, 0)

    def test_ledger_noop_does_not_certify_a_missing_remote_object(self):
        self.assertEqual(self.run_archive().returncode, 0)
        for remote in (self.repo / 'remote').iterdir():
            remote.unlink()
        result = self.run_archive()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.receipt()['reason'], 'readback_failed')
        self.assertEqual(self.run_archive().returncode, 0)

    def test_pruning_keeps_owed_unknown_and_changed_copies(self):
        self.assertEqual(self.run_archive().returncode, 0)
        verified = self.path
        os.utime(verified, (1, 1))
        unknown = self.path.with_name('carr-20260102.sql.age')
        unknown.write_bytes(b'unknown')
        os.utime(unknown, (2, 2))
        changed = self.path.with_name('carr-20260103.sql.age')
        changed.write_bytes(b'original')
        self.assertEqual(self.run_archive(changed).returncode, 0)
        changed.write_bytes(b'altered')
        os.utime(changed, (3, 3))
        for day in range(4, 19):
            (self.path.parent / f'carr-202601{day:02}.sql.age').write_bytes(b'newer opaque bytes')
        result = self.run_archive('--prune-local', self.path.parent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(verified.exists())
        self.assertFalse(Path(str(verified) + '.archive.json').exists())
        self.assertTrue(unknown.exists())
        self.assertTrue(changed.exists())

    def test_dry_run_does_not_replace_an_owed_receipt(self):
        self.run_archive(mode='upload_error')
        receipt = self.receipt()
        result = self.run_archive(self.path, '--dry-run')
        self.assertEqual(result.returncode, 0)
        self.assertIs(json.loads(result.stdout)['archived'], False)
        self.assertEqual(self.receipt(), receipt)

    def test_output_selection_preserves_an_unverified_same_day_copy(self):
        result = self.run_archive('--select-output', self.path)
        selected = Path(result.stdout.strip())
        self.assertEqual(result.returncode, 0)
        self.assertNotEqual(selected, self.path)
        self.assertEqual(selected.parent, self.path.parent)
        self.assertFalse(selected.exists())
        self.assertEqual(self.run_archive().returncode, 0)
        self.assertNotEqual(self.run_archive('--select-output', self.path).stdout.strip(), str(self.path))

    def test_output_selection_is_unique_before_either_generation_starts(self):
        self.path.unlink()
        first = self.run_archive('--select-output', self.path)
        second = self.run_archive('--select-output', self.path)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertNotEqual(first.stdout, second.stdout)
        self.assertNotEqual(first.stdout.strip(), str(self.path))

    def test_archive_keeps_quota_metadata_for_health_readers(self):
        self.assertEqual(self.run_archive().returncode, 0)
        ledger = json.loads((self.repo / 'out/r2-usage.json').read_text())
        self.assertEqual(ledger['quota_gb_last_seen'], 8)
        self.assertIn('quota_provenance_last_seen', ledger)

    def test_prune_skips_a_copy_held_by_an_active_reader(self):
        self.assertEqual(self.run_archive().returncode, 0)
        os.utime(self.path, (1, 1))
        for day in range(2, 17):
            (self.path.parent / f'carr-202601{day:02}.sql.age').write_bytes(b'newer')
        with self.path.open('rb') as source:
            fcntl.flock(source, fcntl.LOCK_EX)
            result = self.run_archive('--prune-local', self.path.parent)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(self.path.exists())
        self.assertEqual(self.run_archive('--prune-local', self.path.parent).returncode, 0)
        self.assertFalse(self.path.exists())

    def test_archive_holds_its_copy_until_remote_verification_finishes(self):
        process = subprocess.Popen(
            [sys.executable, str(self.repo / 'bin/backup-archive-r2.py'), str(self.path)],
            env={**self.child_env, 'FIXTURE_MODE': 'barrier'},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.repo / 'reading').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.repo / 'reading').exists())
            with self.path.open('rb') as source:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(source, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            (self.repo / 'release').touch()
            output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, output + error)

    def test_full_quota_retry_reuses_only_its_existing_reservation(self):
        self.run_archive(mode='wrong')
        ledger_path = self.repo / 'out/r2-usage.json'
        ledger = json.loads(ledger_path.read_text())
        ledger['objects']['other'] = {'bytes': 8 * 1024 ** 3 - self.path.stat().st_size}
        ledger_path.write_text(json.dumps(ledger))
        failed = self.run_archive(mode='upload_error')
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads(ledger_path.read_text())['objects'], ledger['objects'])
        self.assertEqual(self.run_archive().returncode, 0)

    def shell(self, mode='', archive_result=None, archive_status=0, skip_r2=False):
        shutil.copyfile(ROOT / 'bin/backup-dump.sh', self.repo / 'bin/backup-dump.sh')
        python = self.repo / '.venv/bin/python'
        python.parent.mkdir(parents=True, exist_ok=True)
        python.symlink_to(sys.executable)
        commands = self.repo / 'commands'
        commands.mkdir()
        cat = commands / 'cat'
        cat.write_text('#!/bin/sh\nprintf "fictional-public-recipient"\n')
        cat.chmod(0o700)
        (self.repo / 'lib/psycopg.py').write_text('def connect(*a, **kw): raise RuntimeError("fixture database unavailable")\n')
        (self.repo / 'bin/backup-guard.py').write_text("""
import json, pathlib, sys
out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])
out.write_bytes(b'new opaque synthetic bytes')
print(json.dumps({'bytes': out.stat().st_size, 'floor': 1}))
""")
        if archive_result is not None:
            (self.repo / 'bin/backup-archive-r2.py').write_text(
                'import sys\n'
                'if "--select-output" in sys.argv: print(sys.argv[-1]); raise SystemExit(0)\n'
                'if "--prune-local" in sys.argv: raise SystemExit(0)\n'
                + f'print({archive_result!r})\nraise SystemExit({archive_status})\n')
        return subprocess.run(['/bin/zsh' , str(self.repo / 'bin/backup-dump.sh')],
                              env={**self.child_env, 'PATH': str(commands) + ':/usr/bin:/bin',
                                   'CARR_DB_BACKUP_URL': 'fixture-not-a-dsn', 'FIXTURE_MODE': mode,
                                   **({'BACKUP_SKIP_R2': '1'} if skip_r2 else {})},
                              capture_output=True, text=True, timeout=20)

    def test_shell_reports_owed_without_archive_success_or_pruning_unknown_copy(self):
        old = self.path
        for day in range(2, 18):
            (old.parent / f'carr-202601{day:02}.sql.age').write_bytes(b'unknown old copy')
        result = self.shell(mode='wrong')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('R2 archive: OWED', result.stdout + result.stderr)
        self.assertNotIn('R2 archive: backups/', result.stdout)
        self.assertTrue(old.exists())

    def test_shell_success_requires_verified_archiver_result(self):
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('R2 archive: backups/', result.stdout)
        receipts = list(self.path.parent.glob('*.archive.json'))
        self.assertEqual(len(receipts), 1)
        self.assertIs(json.loads(receipts[0].read_text())['archived'], True)

    def test_shell_rejects_malformed_json_and_truthy_archive_values(self):
        for payload, status in [('noise', 0),
                                ('{"archived":"true","state":"archived","key":"backups/fake"}', 0),
                                ('{"archived":true,"state":"archived","key":"backups/fake"}', 7)]:
            with self.subTest(payload=payload, status=status):
                result = self.shell(archive_result=payload, archive_status=status)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('R2 archive: OWED', result.stderr)
                shutil.rmtree(self.repo / 'commands')
                (self.repo / '.venv/bin/python').unlink()

    def test_dangling_output_link_never_selects_an_existing_path(self):
        self.path.unlink()
        self.path.symlink_to(self.path.parent / 'missing')
        result = self.run_archive('--select-output', self.path)
        self.assertEqual(result.returncode, 0)
        self.assertNotEqual(result.stdout.strip(), str(self.path))

    def test_cloud_skip_keeps_canonical_filename_and_reports_skip(self):
        result = self.shell(skip_r2=True, archive_result='invalid', archive_status=9)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('R2 archive: skipped (BACKUP_SKIP_R2)', result.stdout)
        generated = list(self.path.parent.glob('carr-*.sql.age'))
        self.assertEqual(len(generated), 2)
        self.assertTrue(all(len(path.name) == len('carr-20260101.sql.age') for path in generated))
        self.assertFalse(list(self.path.parent.glob('*.archive.json')))


if __name__ == '__main__':
    unittest.main()
