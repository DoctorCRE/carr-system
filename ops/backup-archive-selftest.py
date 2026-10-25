#!/usr/bin/env python3
"""Exercise archive proof and ciphertext retention with synthetic bytes."""
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
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
import hashlib, json, os, pathlib, shutil, sys
args = sys.argv[1:]
key = args[args.index('--key') + 1] if '--key' in args else args[3].split('/', 1)[1]
target = pathlib.Path(os.environ['FIXTURE_REMOTE']) / key.replace('/', '_')
mode = os.environ.get('FIXTURE_MODE', '')
if args[2] == 'put':
    if mode == 'upload_error':
        print('fixture-provider-private-error', file=sys.stderr)
        raise SystemExit(7)
    shutil.copyfile(args[args.index('--file') + 1], target)
elif args[:2] == ['s3api', 'head-object']:
    if mode == 'head_error':
        raise SystemExit(8)
    data = target.read_bytes()
    print(json.dumps({'ContentLength': len(data) + (mode == 'size'),
                      'ETag': 'wrong' if mode == 'wrong' else hashlib.md5(data).hexdigest()}))
else:
    raise SystemExit('unexpected download')
''')
        self.wrapper.chmod(0o700)
        (self.wrapper.parent / 'aws').symlink_to(self.wrapper)
        self.path = self.repo / 'backups/carr-20260101.sql.age'
        self.path.write_bytes(b'opaque synthetic transport bytes\n')
        self.child_env = {'PATH': str(self.wrapper.parent) + ':/usr/bin:/bin', 'HOME': str(self.repo / 'home'),
                          'LC_ALL': 'C', 'CLOUDFLARE_ACCOUNT_ID': 'fixture-account', 'FIXTURE_REMOTE': str(self.repo / 'remote')}

    def run_archive(self, *args, mode=''):
        return subprocess.run([sys.executable, str(self.repo / 'bin/backup-archive-r2.py'),
                               *(str(a) for a in args or (self.path,))],
                              env={**self.child_env, 'FIXTURE_MODE': mode},
                              capture_output=True, text=True, timeout=15)

    def test_bucket_probe_with_missing_interpreter_is_unavailable(self):
        module = runpy.run_path(str(self.repo / 'lib/r2_archive.py'))
        self.wrapper.write_text('#!/missing/fixture-interpreter\n')
        size, count, note = module['bucket_size']()
        self.assertIsNone(size)
        self.assertIsNone(count)
        self.assertIn('not consulted', note)

    def test_archive_keeps_quota_metadata_for_health_readers(self):
        self.assertEqual(self.run_archive().returncode, 0)
        ledger = json.loads((self.repo / 'out/r2-usage.json').read_text())
        self.assertEqual(ledger['quota_gb_last_seen'], 8)
        self.assertIn('quota_provenance_last_seen', ledger)

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
                f'print({archive_result!r})\nraise SystemExit({archive_status})\n')
        return subprocess.run(['/bin/zsh', str(self.repo / 'bin/backup-dump.sh')],
                              env={**self.child_env, 'PATH': str(commands) + ':' + self.child_env['PATH'],
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
        self.assertTrue(self.path.exists())

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

    def test_cloud_skip_keeps_canonical_filename_and_reports_skip(self):
        result = self.shell(skip_r2=True, archive_result='invalid', archive_status=9)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('R2 archive: skipped (BACKUP_SKIP_R2)', result.stdout)
        generated = list(self.path.parent.glob('carr-*.sql.age'))
        self.assertEqual(len(generated), 2)
        self.assertTrue(all(len(path.name) == len('carr-20260101.sql.age') for path in generated))

    def test_failed_archive_retains_ciphertext_and_reports_reason(self):
        for mode, reason in [('upload_error', 'verification_failed'), ('wrong', 'verification_failed'),
                             ('head_error', 'verification_failed'), ('size', 'verification_failed')]:
            with self.subTest(mode=mode):
                result = self.run_archive(mode=mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIs(json.loads(result.stdout)['archived'], False)
                self.assertEqual(json.loads(result.stdout)['reason'], reason)
                self.assertTrue(self.path.exists())
                ledger = json.loads((self.repo / 'out/r2-usage.json').read_text())
                self.assertFalse(ledger['objects'])
                self.assertNotIn('fixture-provider-private-error', result.stdout + result.stderr)

    def test_quota_refusal_retains_ciphertext(self):
        (self.repo / 'out/r2-usage.json').write_text(json.dumps({
            'objects': {'other': {'bytes': 8 * 1024 ** 3}}}))
        result = self.run_archive()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'quota_exceeded')
        self.assertTrue(self.path.exists())

    def test_ledger_cache_cannot_certify_missing_remote_bytes(self):
        self.assertEqual(self.run_archive().returncode, 0)
        for remote in (self.repo / 'remote').iterdir():
            remote.unlink()
        result = self.run_archive()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'verification_failed')
        self.assertTrue(self.path.exists())

    def test_dry_run_never_certifies_or_uploads(self):
        result = self.run_archive(self.path, '--dry-run')
        self.assertEqual(result.returncode, 0)
        self.assertIs(json.loads(result.stdout)['archived'], False)
        self.assertFalse(list((self.repo / 'remote').iterdir()))

    def test_same_day_generations_preserve_previous_ciphertexts(self):
        self.assertEqual(self.shell().returncode, 0)
        shutil.rmtree(self.repo / 'commands')
        (self.repo / '.venv/bin/python').unlink()
        self.assertEqual(self.shell().returncode, 0)
        copies = list(self.path.parent.glob('carr-*.sql.age'))
        self.assertEqual(len(copies), 3)
        self.assertEqual(self.path.read_bytes(), b'opaque synthetic transport bytes\n')


if __name__ == '__main__':
    unittest.main()
