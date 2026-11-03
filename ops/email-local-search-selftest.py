#!/usr/bin/env python3
"""Exercise private decoded-mail publication through its library and CLI."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.email_local_search import build_index, IndexError as PublicationError


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.source = self.home / 'decoded.db'
        self.target = self.home / 'search.db'
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript((ROOT / 'ops/fixtures/email-local-search-schema.sql').read_text())
        self.source.chmod(0o600)

    def build(self, **options):
        return build_index(self.source, self.target, mailbox='synthetic-mailbox', account_id=42,
                           source_revision='synthetic-r1', clock=lambda: '2026-11-03T12:00:00Z', **options)

    def digest(self):
        return hashlib.sha256(self.target.read_bytes()).hexdigest()

    def test_atomic_index_rebuild_preserves_private_old_index_on_failure(self):
        self.build()
        old = self.digest()
        with patch('lib.email_local_search.os.replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual(old, self.digest())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.home.glob('.email-search-*')))

    def test_indexes_all_dates_fts_and_versioned_unknown_coverage(self):
        before = self.source.read_bytes()
        result = self.build()
        with closing(sqlite3.connect(self.target)) as db:
            metadata = json.loads(db.execute('SELECT document FROM index_metadata').fetchone()[0])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'orchard'").fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT DISTINCT account_id FROM messages').fetchall(), [(42,)])
        self.assertEqual(metadata['observed_min_unix'], 946684800)
        self.assertEqual(metadata['observed_max_unix'], 1767225600)
        self.assertEqual(metadata['coverage'], 'unknown')
        self.assertEqual(metadata['build_state'], 'complete')
        self.assertEqual(metadata['mailbox'], 'synthetic-mailbox')
        self.assertEqual(metadata['source_revision'], 'synthetic-r1')
        self.assertEqual(metadata['built_at'], '2026-11-03T12:00:00Z')
        self.assertEqual(metadata['source_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(result, {'schema_version': 1, 'build_state': 'complete', 'message_count': 2})
        self.assertEqual(before, self.source.read_bytes())

    def test_replay_and_replacement(self):
        self.build()
        old = self.digest()
        self.build()
        self.assertEqual(old, self.digest())
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("UPDATE messages SET body='replacement' WHERE id=1")
            db.commit()
        self.build()
        self.assertNotEqual(old, self.digest())

    def test_permissions_symlinks_unsafe_parent_and_owner(self):
        for mode in (0o644, 0o660):
            self.source.chmod(mode)
            with self.assertRaises(PublicationError):
                self.build()
        self.source.chmod(0o600)
        self.home.chmod(0o755)
        with self.assertRaises(PublicationError):
            self.build()
        self.home.chmod(0o700)
        link = self.home / 'link.db'
        link.symlink_to(self.source)
        with self.assertRaises(PublicationError):
            build_index(link, self.target, mailbox='x', account_id=42, source_revision='r')
        self.target.symlink_to(self.source)
        with self.assertRaises(PublicationError):
            self.build()
        self.target.unlink()
        with patch('lib.email_local_search.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaises(PublicationError):
                self.build()
        unsafe = self.home / 'unsafe'
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o777)
        child = unsafe / 'private'
        child.mkdir(mode=0o700)
        with self.assertRaises(PublicationError):
            build_index(self.source, child / 'index.db', mailbox='x', account_id=42, source_revision='r')

    def test_malformed_and_cross_account_preserve_old(self):
        self.build()
        old = self.digest()
        for sql in ('UPDATE message_objects SET account_id=99',
                    'DELETE FROM message_keys', 'DROP TABLE messages'):
            with closing(sqlite3.connect(self.source)) as db:
                db.execute(sql)
                db.commit()
            with self.assertRaises(PublicationError):
                self.build()
            self.assertEqual(old, self.digest())

    def test_sidecars_rejected_without_touching_old(self):
        self.build()
        for base in (self.source, self.target):
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = Path(str(base) + suffix)
                sidecar.write_bytes(b'synthetic')
                sidecar.chmod(0o600)
                with self.assertRaises(PublicationError):
                    self.build()
                sidecar.unlink()

    def test_empty_and_undated_coverage_remains_unknown(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('UPDATE messages SET sent_unix=NULL, sent_utc=NULL')
            db.commit()
        self.build()
        with closing(sqlite3.connect(self.target)) as db:
            metadata = json.loads(db.execute('SELECT document FROM index_metadata').fetchone()[0])
        self.assertIsNone(metadata['observed_min_unix'])
        self.assertEqual(metadata['undated_count'], 2)
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('DELETE FROM messages')
            db.commit()
        self.assertEqual(self.build(allow_empty=True)['message_count'], 0)
        with closing(sqlite3.connect(self.target)) as db:
            metadata = json.loads(db.execute('SELECT document FROM index_metadata').fetchone()[0])
        self.assertEqual(metadata['coverage'], 'unknown')

    def test_private_generations_under_permissive_umask(self):
        from lib import email_local_search
        original = email_local_search._populate

        def inspect(*args):
            for path in self.home.rglob('*'):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
            return original(*args)

        previous = os.umask(0)
        try:
            with patch('lib.email_local_search._populate', side_effect=inspect):
                self.build()
        finally:
            os.umask(previous)

    def test_lock_symlink_and_insecure_existing_index_refused(self):
        self.build()
        old = self.digest()
        self.target.chmod(0o644)
        with self.assertRaises(PublicationError):
            self.build()
        self.target.chmod(0o600)
        lock = self.home / '.search.db.lock'
        lock.unlink()
        lock.symlink_to(self.source)
        with self.assertRaises((PublicationError, OSError)):
            self.build()
        self.assertEqual(old, self.digest())

    def test_cli_excludes_concurrent_writer_and_recovers_after_crash(self):
        self.build()
        old = self.digest()
        lock = self.home / '.search.db.lock'
        with lock.open('r+b') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.cli()
            self.assertEqual(result.returncode, 2)
            self.assertEqual(old, self.digest())
        child = subprocess.run([sys.executable, '-c',
            'import os,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            'import lib.email_local_search as m; m.os.replace=lambda *a,**k: os._exit(77); '
            'm.build_index(Path(sys.argv[2]),Path(sys.argv[3]),mailbox="m",account_id=42,source_revision="r")',
            str(ROOT), str(self.source), str(self.target)], capture_output=True)
        self.assertEqual(child.returncode, 77)
        self.assertEqual(old, self.digest())
        for path in self.home.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.build()
        self.assertEqual(old, self.digest())

    def cli(self, destination=None, *options):
        return subprocess.run([sys.executable, str(ROOT / 'tools/email-local-search.py'), 'build',
            '--source', str(self.source), '--index', str(destination or self.target), '--mailbox', 'synthetic',
            '--account-id', '42', '--source-revision', 'synthetic-r1', *options],
            capture_output=True, text=True, cwd=self.home)

    def test_cli_alias_destination_preserves_source(self):
        link = self.home / 'link.db'
        link.symlink_to(self.source)
        parent = self.home / 'linked-parent'
        parent.symlink_to(self.home, target_is_directory=True)
        aliases = (self.source, link,
                   Path(self.source.name), parent / self.source.name,
                   self.home / 'absent' / '..' / self.source.name,
                   Path('/' + str(self.source)))
        before = self.source.read_bytes()
        hard = self.home / 'hard.db'
        os.link(self.source, hard)
        result = self.cli(hard)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.source.read_bytes(), before)
        hard.unlink()
        for alias in aliases:
            with self.subTest(alias=alias):
                result = self.cli(alias)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertFalse(list(self.home.glob('.*.lock')))
                self.assertFalse(list(self.home.glob('.email-search-*')))

    def test_cli_case_alias_preserves_source(self):
        alias = self.source.with_name(self.source.name.upper())
        if not alias.exists() or not alias.samefile(self.source):
            self.skipTest('case-sensitive filesystem has no case alias')
        before = self.source.read_bytes()
        result = self.cli(alias)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(self.source.read_bytes(), before)

    def test_cli_empty_source_preserves_previous_index(self):
        self.assertEqual(self.cli().returncode, 0)
        before = self.target.read_bytes()
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('DELETE FROM messages')
            db.commit()
        source_before = self.source.read_bytes()
        result = self.cli()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn('--allow-empty', result.stderr)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertFalse(list(self.home.glob('.email-search-*')))
        result = self.cli(None, '--allow-empty')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['message_count'], 0)
        with closing(sqlite3.connect(self.target)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)

    def test_cli_empty_source_can_create_initial_index(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('DELETE FROM messages')
            db.commit()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['message_count'], 0)

    def test_source_cannot_be_destination_lock(self):
        before = self.source.read_bytes()
        source = self.home / '.search.db.lock'
        self.source.rename(source)
        with patch('lib.email_local_search.os.open', wraps=os.open) as opened:
            with self.assertRaisesRegex(PublicationError, 'source_is_destination'):
                build_index(source, self.target, mailbox='synthetic', account_id=42,
                            source_revision='r')
        opened.assert_not_called()
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(self.target.exists())

    def test_cli_bounded_output_and_sanitized_failure(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['message_count'], 2)
        self.assertNotIn('orchard', result.stdout + result.stderr)
        self.source.write_bytes(b'secret synthetic broken schema')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('secret', result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_out_of_range_account_is_a_bounded_refusal(self):
        with self.assertRaises(PublicationError):
            build_index(self.source, self.target, mailbox='synthetic',
                        account_id=2**64, source_revision='r')


if __name__ == '__main__':
    unittest.main()
