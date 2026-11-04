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

    def test_selected_account_publication_excludes_other_unknown_and_conflicting_rows(self):
        from lib.email_local_search import query_index
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('UPDATE message_objects SET account_id=99 WHERE local_key=1002')
            db.execute('INSERT INTO message_keys VALUES(1,1003)')
            db.execute('INSERT INTO message_objects VALUES(1003,NULL)')
        self.build(select_account=True)
        self.source.unlink()
        result = query_index(self.target, account_id=42, query='orchard', published_snapshot=True)
        self.assertEqual(result['match_count'], 0)
        self.assertEqual(result['coverage']['message_count'], 0)
        with self.assertRaises(PublicationError):
            query_index(self.target, account_id=99, query='orchard', published_snapshot=True)

    def test_size_cap_preserves_previous_generation(self):
        self.build()
        before = self.target.read_bytes()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE messages SET body=? WHERE id=1", ('oversized ' * 10000,))
        with patch('lib.email_local_search.MAX_INDEX_BYTES', 4096):
            with self.assertRaises(PublicationError):
                self.build(select_account=True)
        self.assertEqual(before, self.target.read_bytes())
        self.assertFalse(list(self.home.glob('.email-search-*')))

    def test_published_snapshot_cli_after_source_cleanup_is_readonly(self):
        self.build()
        self.source.unlink()
        before = self.target.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT / 'tools/email-local-search.py'),
            'query', '--published-snapshot', '--index', str(self.target), '--account-id', '42',
            '--query', 'orchard'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['match_count'], 2)
        self.assertEqual(before, self.target.read_bytes())

    def test_history_lookup_returns_exact_account_results_without_network_calls(self):
        from lib.email_local_search import query_index
        self.build()
        before = self.target.read_bytes()
        with patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = query_index(self.target, source=self.source, source_revision='synthetic-r1', account_id=42, query='early@example.invalid')
        self.assertEqual(result['match_count'], 1)
        self.assertEqual(result['results'][0]['sent_unix'], 946684800)
        self.assertNotIn('orchard', json.dumps(result))
        self.assertEqual(result['coverage']['completeness'], 'unknown')
        self.assertEqual(result['coverage']['built_at'], '2026-11-03T12:00:00Z')
        self.assertEqual(result['coverage']['freshness'], 'matches_current_decoded_source')
        self.assertEqual(before, self.target.read_bytes())
        with self.assertRaises(PublicationError):
            query_index(self.target, source=self.source, source_revision='synthetic-r1', account_id=99, query='orchard')

    def query(self, query='orchard', **options):
        from lib.email_local_search import query_index
        return query_index(self.target, source=self.source, source_revision='synthetic-r1', account_id=42, query=query, **options)

    def query_cli(self, *options):
        return subprocess.run([sys.executable, str(ROOT / 'tools/email-local-search.py'), 'query',
            '--index', str(self.target), '--source', str(self.source),
            '--source-revision', 'synthetic-r1', '--account-id', '42', *options], capture_output=True, text=True)

    def test_query_pagination_unicode_quotes_duplicates_and_replacement(self):
        with closing(sqlite3.connect(self.source)) as db:
            for i in range(3, 108):
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (i, 'zoe@example.invalid', 'Zoë O"Neil', 'recipient@example.invalid',
                            'Duplicate', '123 Érable Lane orchard', None, 'full',
                            None if i == 107 else 946684800, None))
                db.execute('INSERT INTO message_keys VALUES (?,?)', (i, i))
                db.execute('INSERT INTO message_objects VALUES (?,42)', (i,))
            db.commit()
        self.build()
        before = self.target.read_bytes()
        first = self.query('Zoë O"Neil', limit=40)
        self.assertEqual(first['match_count'], 105)
        second = self.query('Zoë O"Neil', limit=40, cursor=first['next_cursor'])
        third = self.query('Zoë O"Neil', limit=40, cursor=second['next_cursor'])
        refs = [r['source_ref'] for p in (first, second, third) for r in p['results']]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual([int(r.split(':')[1]) for r in refs], list(range(106, 2, -1)) + [107])
        self.assertIsNone(third['next_cursor'])
        self.assertEqual(self.query('123 Érable Lane')['match_count'], 105)
        self.assertEqual(self.query('zoe@example.invalid')['match_count'], 105)
        self.assertEqual(before, self.target.read_bytes())
        with self.assertRaises(PublicationError):
            self.query('other', cursor=first['next_cursor'])
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("UPDATE messages SET body='changed' WHERE id=3")
            db.commit()
        self.build()
        with self.assertRaises(PublicationError):
            self.query('Zoë O"Neil', cursor=first['next_cursor'])

    def test_query_rejects_input_before_open(self):
        from lib.email_local_search import query_index
        for query in ('', ' ', 'a' * 513, 'orchard OR history', 'subject:orchard',
                      'orchard*', '(orchard)', 'NEAR(orchard)', 'x\x00y', '"', '---'):
            with self.subTest(query=query), patch('sqlite3.connect') as connect:
                with self.assertRaises(PublicationError):
                    query_index(self.target, source=self.source, source_revision='synthetic-r1', account_id=42, query=query)
                connect.assert_not_called()
        self.build()
        for options in ({'limit': 0}, {'limit': 101}, {'limit': True}, {'cursor': 'secret'},
                        {'cursor': 'a' * 257}, {'excerpts': 'yes'}):
            with self.subTest(options=options), self.assertRaises(PublicationError):
                self.query(**options)
        self.assertEqual(self.query("orchard' UNION SELECT secret --")['match_count'], 0)

    def test_query_validates_completion_schema_account_permissions_and_owner(self):
        for sql in ("UPDATE messages SET account_id=99 WHERE id=1",
                    "UPDATE index_metadata SET document=replace(document,'complete','building')",
                    'PRAGMA user_version=2', 'DROP TABLE messages_fts',
                    'ALTER TABLE messages RENAME TO wrong',
                    "UPDATE index_metadata SET document='secret malformed json'",
                    "UPDATE messages SET source_ref='secret' WHERE id=1"):
            self.build()
            with closing(sqlite3.connect(self.target)) as db:
                db.execute(sql)
                db.commit()
            before = self.target.read_bytes()
            with self.subTest(sql=sql), self.assertRaises(PublicationError):
                self.query()
            self.assertEqual(before, self.target.read_bytes())
        self.build()
        self.target.chmod(0o644)
        with self.assertRaises(PublicationError):
            self.query()
        self.target.chmod(0o600)
        with patch('lib.email_local_search.os.getuid', return_value=os.getuid() + 1):
            with self.assertRaises(PublicationError):
                self.query()
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(self.target) + suffix)
            sidecar.touch(mode=0o600)
            with self.assertRaises(PublicationError):
                self.query()
            sidecar.unlink()
        saved = self.home / 'saved.db'
        self.target.rename(saved)
        self.target.symlink_to(saved)
        with self.assertRaises(PublicationError):
            self.query()

    def test_query_empty_coverage_and_cli_bounded_explicit_excerpts(self):
        self.build()
        before = self.target.read_bytes()
        result = self.query_cli('--query', 'orchard')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(len(result.stdout), 2000)
        self.assertNotIn('orchard', result.stdout + result.stderr)
        self.assertNotIn('Recent history', result.stdout)
        result = self.query_cli('--query', 'orchard', '--local-excerpts')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('orchard', result.stdout)
        self.assertEqual(result.stderr, '')
        empty = self.query('absent')
        self.assertEqual(empty['match_count'], 0)
        self.assertEqual(empty['coverage']['observed_min_unix'], 946684800)
        self.assertEqual(empty['coverage']['observed_max_unix'], 1767225600)
        self.assertIn('not proof', empty['meaning'])
        for options in (('--query', 'orchard', '--cursor', 'secret'),
                        ('--query', 'secret' * 100, '--local-excerpts')):
            result = self.query_cli(*options)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('secret', result.stdout + result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(before, self.target.read_bytes())
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('DELETE FROM messages')
            db.commit()
        self.build(allow_empty=True)
        self.assertEqual(self.query()['coverage']['message_count'], 0)
        self.assertIsNone(self.query()['coverage']['observed_min_unix'])

    def test_query_has_only_stdlib_dependencies_and_no_external_effects(self):
        import ast
        tree = ast.parse((ROOT / 'lib/email_local_search.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module or '').split('.')[0]]
            else:
                continue
            self.assertTrue(set(modules) <= sys.stdlib_module_names, modules)
        self.build()
        with patch('socket.socket', side_effect=AssertionError('network')) as network, \
                patch('subprocess.Popen', side_effect=AssertionError('external process')) as process:
            self.assertEqual(self.query()['match_count'], 2)
            self.query(excerpts=True)
        network.assert_not_called()
        process.assert_not_called()

    def test_query_limits_excerpts_and_uses_readonly_database(self):
        from lib import email_local_search
        with closing(sqlite3.connect(self.source)) as db:
            db.execute('UPDATE messages SET subject=?,body=?', ('s' * 10000, 'orchard ' + 'é' * 10000))
            db.commit()
        self.build()
        original = email_local_search._readonly

        def readonly(path):
            db = original(path)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('UPDATE messages SET body=NULL')
            db.rollback()
            return db

        before = self.target.read_bytes()
        with patch('lib.email_local_search._readonly', side_effect=readonly):
            result = self.query('orchard', excerpts=True, limit=1)
        self.assertEqual(len(result['results'][0]['excerpt']['body']), 500)
        self.assertEqual(len(result['results'][0]['excerpt']['subject']), 240)
        self.assertLess(len(json.dumps(result)), 5000)
        self.assertEqual(before, self.target.read_bytes())

    def test_query_rejects_replacement_during_open(self):
        from lib import email_local_search
        self.build()
        replacement = self.home / 'replacement.db'
        replacement.write_bytes(self.target.read_bytes())
        replacement.chmod(0o600)
        original = email_local_search._readonly

        def replace(path):
            db = original(path)
            os.replace(replacement, path)
            return db

        with patch('lib.email_local_search._readonly', side_effect=replace):
            with self.assertRaises(PublicationError):
                self.query()

    def test_query_refuses_stale_source_and_recovers_after_rebuild(self):
        self.build()
        page = self.query(limit=1)
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("UPDATE messages SET body='freshproperty' WHERE id=1")
            db.commit()
        for options in (('--query', 'freshproperty'),
                        ('--query', 'orchard', '--cursor', page['next_cursor'])):
            result = self.query_cli(*options)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(result.stdout, '')
            self.assertIn('stale', result.stderr.lower())
        self.build()
        result = self.query_cli('--query', 'freshproperty')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['match_count'], 1)

    def test_query_refuses_missing_freshness_provenance(self):
        for field in ('built_at', 'source_revision', 'source_sha256'):
            self.build()
            with closing(sqlite3.connect(self.target)) as db:
                meta = json.loads(db.execute('SELECT document FROM index_metadata').fetchone()[0])
                del meta[field]
                db.execute('UPDATE index_metadata SET document=? WHERE id=1', (json.dumps(meta),))
                db.commit()
            result = self.query_cli('--query', 'absent')
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(result.stdout, '')
            self.assertIn('freshness', result.stderr.lower())

    def test_query_refuses_invalid_freshness_provenance_and_revision(self):
        for field, value in (('built_at', 'not a timestamp'), ('built_at', '2026-11-03'),
                             ('built_at', None), ('source_revision', ''),
                             ('source_revision', 1), ('source_sha256', 'invalid'),
                             ('source_sha256', None), ('source_revision', 'another-revision')):
            self.build()
            with closing(sqlite3.connect(self.target)) as db:
                meta = json.loads(db.execute('SELECT document FROM index_metadata').fetchone()[0])
                meta[field] = value
                db.execute('UPDATE index_metadata SET document=? WHERE id=1', (json.dumps(meta),))
                db.commit()
            with self.subTest(field=field, value=value):
                result = self.query_cli('--query', 'absent')
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(result.stdout, '')
                self.assertIn('freshness', result.stderr.lower())

    def test_query_refuses_source_change_during_lookup(self):
        from lib import email_local_search
        self.build()
        original = email_local_search._readonly

        def change_source(path):
            if path == self.target:
                with closing(sqlite3.connect(self.source)) as db:
                    db.execute("UPDATE messages SET body='freshproperty' WHERE id=1")
                    db.commit()
            return original(path)

        with patch('lib.email_local_search._readonly', side_effect=change_source):
            with self.assertRaisesRegex(PublicationError, 'stale_index'):
                self.query('freshproperty')

    def test_query_refuses_missing_current_source_or_index(self):
        self.build()
        self.target.unlink()
        result = self.query_cli('--query', 'absent')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.build()
        self.source.unlink()
        result = self.query_cli('--query', 'absent')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')

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
        for _ in range(3):
            self.crash_build()
        self.assertEqual(old, self.digest())
        self.assertLessEqual(len(list(self.home.glob('.email-search-*'))), 1)
        for path in self.home.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.build()
        self.assertEqual(old, self.digest())
        self.assertFalse(list(self.home.glob('.email-search-*')))
        self.assertEqual(self.query()['match_count'], 2)

    def crash_build(self):
        child = subprocess.run([sys.executable, '-c',
            'import os,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); '
            'import lib.email_local_search as m; m.os.replace=lambda *a,**k: os._exit(77); '
            'm.build_index(Path(sys.argv[2]),Path(sys.argv[3]),mailbox="m",account_id=42,source_revision="r")',
            str(ROOT), str(self.source), str(self.target)], capture_output=True)
        self.assertEqual(child.returncode, 77)

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
