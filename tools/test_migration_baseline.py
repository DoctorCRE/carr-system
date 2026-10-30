"""Contracts for filename-keyed migration baseline coverage."""
import hashlib
import copy
import shutil
from pathlib import Path
import tempfile
import runpy
import subprocess
import sys
import unittest
from unittest import mock

from migration_baseline import MigrationBaseline, repository_ledger
from migrate import validate_ledger_identities


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def document():
    return {
        'schema': 'carr-migration-baseline/v1',
        'source_revision': '1' * 40,
        'snapshot': {'path': 'db/schema.sql', 'sha256': '2' * 64},
        'cutoff': '0002_b.sql',
        'migrations': [
            {'filename': '0001_a.sql', 'sha256': digest('select 1;\n')},
            {'filename': '0002_b.sql', 'sha256': digest('select 2;\n')},
        ],
    }


class BaselineTests(unittest.TestCase):
    def test_retained_and_archived_history_have_the_same_logical_ledger(self):
        baseline = MigrationBaseline.from_document(document())
        files = {'0001_a.sql': 'select 1;\n', '0002_b.sql': 'select 2;\n',
                 '0003_c.sql': 'select 3;\n'}
        expected = {'0001_a.sql': digest('select 1;\n'),
                    '0002_b.sql': digest('select 2;\n'),
                    '0003_c.sql': digest('select 3;\n')}
        self.assertEqual(baseline.repository_ledger(files), expected)
        self.assertEqual(baseline.repository_ledger({'0003_c.sql': 'select 3;\n'}), expected)

    def test_retained_history_cannot_drift_or_backfill(self):
        baseline = MigrationBaseline.from_document(document())
        for files in ({'0001_a.sql': 'select changed;'},
                      {'0001_new.sql': 'select 1;'},
                      {'0003_invalid.txt': 'select 3;'}):
            with self.subTest(files=files), self.assertRaises(ValueError):
                baseline.repository_ledger(files)

    def test_original_ledger_rows_are_required_without_mutation(self):
        baseline = MigrationBaseline.from_document(document())
        applied = dict(baseline.migrations)
        before = dict(applied)
        baseline.validate_coverage(applied)
        self.assertEqual(applied, before)
        for rows in ({}, {'0001_a.sql': applied['0001_a.sql']},
                     dict(applied, **{'0002_b.sql': '0' * 64}),
                     {'baseline.sql': '0' * 64}):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                baseline.validate_coverage(rows)

    def test_manifest_rejects_ambiguous_or_unbound_provenance(self):
        bad = [None, {}, dict(document(), schema='v2'),
               dict(document(), source_revision='main'),
               dict(document(), cutoff='0001_a.sql'),
               dict(document(), snapshot={'path': '../schema.sql', 'sha256': '2' * 64}),
               dict(document(), migrations=[])]
        for mutation in ('duplicate', 'reordered', 'path', 'checksum', 'extra'):
            doc = copy.deepcopy(document())
            if mutation == 'duplicate':
                doc['migrations'].append(doc['migrations'][-1])
            elif mutation == 'reordered':
                doc['migrations'].reverse()
            elif mutation == 'path':
                doc['migrations'][0]['filename'] = '../0001_a.sql'
            elif mutation == 'checksum':
                doc['migrations'][0]['sha256'] = 'a'
            else:
                doc['migrations'][0]['applied_at'] = 'now'
            bad.append(doc)
        for doc in bad:
            with self.subTest(document=doc), self.assertRaises(ValueError):
                MigrationBaseline.from_document(doc)

    def test_runner_newline_normalization_is_preserved(self):
        baseline = MigrationBaseline.from_document(document())
        self.assertEqual(baseline.repository_ledger({'0001_a.sql': 'select 1;\r\n'}),
                         dict(baseline.migrations))

    def test_missing_manifest_refuses_instead_of_inferring_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'cannot read'):
                MigrationBaseline.load(Path(directory))

    def test_committed_snapshot_is_a_checksummed_applied_set(self):
        root = Path(__file__).resolve().parents[1]
        baseline = MigrationBaseline.load(root)
        ledger = repository_ledger(root, baseline)
        parse = runpy.run_path(str(root / 'ops/migration-safety-gate.py'))['ledger_rows']
        rows = parse((root / 'db/schema.sql').read_text())
        self.assertTrue(rows)
        baseline.validate_coverage(rows)
        validate_ledger_identities(ledger, rows)

    def test_snapshot_check_accepts_lower_pending_and_refuses_identity_drift(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            shutil.copytree(root / 'migrations', repo / 'migrations')
            shutil.copytree(root / 'db', repo / 'db')
            (repo / 'ops').symlink_to(root / 'ops', target_is_directory=True)
            higher = repo / 'migrations/9999_review_higher.sql'
            higher.write_text('select 9999;\n')
            snapshot = (repo / 'db/schema.sql').read_text()
            start = snapshot.index('COPY public.schema_migrations ')
            end = snapshot.index('\\.\n', start)
            snapshot = (snapshot[:end] + higher.name + '\t' + digest(higher.read_text())
                        + '\t2026-01-01 00:00:00+00\n' + snapshot[end:])
            (repo / 'db/schema.sql').write_text(snapshot)
            (repo / 'migrations/9998_review_lower.sql').write_text('select 9998;\n')
            with mock.patch.dict(globals(), __file__=str(repo / 'tools/test_migration_baseline.py')):
                self.test_committed_snapshot_is_a_checksummed_applied_set()
                for invalid in ('checksum', 'missing', 'baseline'):
                    with self.subTest(invalid=invalid):
                        if invalid == 'checksum':
                            higher.write_text('select changed;\n')
                        elif invalid == 'missing':
                            higher.unlink()
                        else:
                            higher.write_text('select 9999;\n')
                            lines = snapshot.splitlines(keepends=True)
                            name = MigrationBaseline.load(repo).migrations[0][0]
                            (repo / 'db/schema.sql').write_text(''.join(
                                line for line in lines if not line.startswith(name + '\t')))
                        with self.assertRaises((AssertionError, ValueError)):
                            self.test_committed_snapshot_is_a_checksummed_applied_set()

    def test_generator_reads_exact_source_instead_of_working_tree(self):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'ops'))
        from git_env import fixture_env
        generate = runpy.run_path(str(root / 'ops/migration-baseline-manifest.py'))['build_manifest']
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'db').mkdir()
            (repo / 'migrations').mkdir()
            sql = 'select 1;\n'
            snapshot = ('COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n'
                        + '0001_a.sql\t' + digest(sql) + '\tfixture\n\\.\n')
            (repo / 'migrations/0001_a.sql').write_text(sql)
            (repo / 'db/schema.sql').write_text(snapshot)
            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, env=fixture_env(),
                                      check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            git('add', 'db/schema.sql', 'migrations/0001_a.sql')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'baseline fixture')
            source = git('rev-parse', 'HEAD')
            (repo / 'migrations/0001_a.sql').write_text('working tree drift')
            actual = generate(repo, source)
            self.assertEqual(actual['source_revision'], source)
            self.assertEqual(actual['snapshot']['sha256'], digest(snapshot))
            self.assertEqual(actual['migrations'], [{'filename': '0001_a.sql', 'sha256': digest(sql)}])
            git('add', 'migrations/0001_a.sql')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '-qm', 'mismatched fixture')
            with self.assertRaisesRegex(ValueError, 'differs'):
                generate(repo, git('rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
