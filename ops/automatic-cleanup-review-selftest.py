import ast
import importlib.util
import os
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'tools'), str(ROOT / 'ops')]
import machine_cleanup
import storage_hygiene

spec = importlib.util.spec_from_file_location('fixtures', ROOT / 'ops/branch-janitor-selftest.py')
assert spec is not None and spec.loader is not None
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ReviewProbes(unittest.TestCase):
    def test_interrupted_temp_effect_is_reconciled_in_next_receipt(self):
        from unittest.mock import patch
        import local_jobs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch, clones = root / 'scratch', root / 'clones'
            scratch.mkdir()
            clones.mkdir()
            candidate = scratch / 'carr-ci-test-owned'
            candidate.mkdir()
            os.utime(candidate, (0, 0))
            config = local_jobs.load_config(ROOT / 'ops/config/local-jobs.json')
            options = dict(hook=None, tmp_root=clones, user_tmp_root=scratch, roots={},
                dispatch_path=root / 'absent.jsonl', provider=object(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
            remove = storage_hygiene.os.rename
            def interrupt_after_effect(*args, **kwargs):
                remove(*args, **kwargs)
                raise KeyboardInterrupt('owned fixture interrupt after effect')
            with patch.object(storage_hygiene.os, 'rename', side_effect=interrupt_after_effect):
                with self.assertRaises(KeyboardInterrupt):
                    machine_cleanup.run(root, config, **options)
            import json
            interrupted = json.loads((root / 'out/machine-cleanup.json').read_text())
            self.assertEqual(interrupted['pending']['kind'], 'test_temp')
            self.assertFalse(candidate.exists())
            recovered = machine_cleanup.run(root, config, **options)
            self.assertTrue(any(row.get('path') == str(candidate) for row in recovered['removed'])
                or recovered['status'] == 'failed', recovered)

    def test_temp_staging_preserves_payload_and_manifest_without_permanent_delete(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            candidate.mkdir()
            (candidate / 'payload').write_text('keep these bytes')
            for path in (candidate / 'payload', candidate):
                os.utime(path, (0, 0))
            with patch.object(storage_hygiene.shutil, 'rmtree', side_effect=AssertionError('permanent delete')):
                report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                    max_items=100, cursor='', process_probe=lambda paths: set(), deadline=lambda: False,
                    dry_run=False)
            row = report['removed'][0]
            self.assertEqual(row['status'], 'staged')
            staged = Path(row['staged_path'])
            self.assertEqual((staged / 'payload').read_text(), 'keep these bytes')
            self.assertEqual(json.loads((staged.parent / 'manifest.json').read_text())['status'], 'staged')
            self.assertEqual(report['freed_bytes'], 0)
            self.assertGreater(report['staged_allocated_bytes'], 0)

    def test_unknown_pending_kind_preserves_previous_evidence_on_repeated_runs(self):
        import local_jobs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            previous = {'at': 1, 'status': 'running', 'last_success_at': 0,
                'removed': [{'kind': 'thread', 'thread_id': 'prior', 'status': 'archived'}],
                'kept': [{'kind': 'test_temp', 'path': 'protected', 'status': 'kept'}],
                'pending': {'kind': 'unknown', 'path': 'uncertain'}, 'errors': []}
            receipt.write_text(json.dumps(previous))
            config = local_jobs.load_config(ROOT / 'ops/config/local-jobs.json')
            for _ in range(2):
                report = machine_cleanup.run(root, config, hook=None, tmp_root=root,
                    dispatch_path=root / 'absent', roots={}, provider=object(),
                    process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
                self.assertEqual(report['status'], 'failed')
                for key in ('removed', 'kept', 'pending', 'last_success_at'):
                    self.assertEqual(report[key], previous[key])

    def test_thread_recovery_observes_previous_receipt_before_replacing_it(self):
        import local_jobs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            previous = {'at': time.time(), 'status': 'running', 'last_success_at': 0,
                'removed': [{'kind': 'thread', 'thread_id': 'prior', 'status': 'archived'}],
                'kept': [], 'pending': {'kind': 'thread', 'thread_id': 'finished', 'before': 100}, 'errors': []}
            receipt.write_text(json.dumps(previous))
            def archive(tid, **kwargs):
                self.assertEqual(json.loads(receipt.read_text()), previous)
                self.assertTrue(kwargs['dry_run'])
                self.assertEqual(kwargs['before'], 100)
                return {'kind': 'thread', 'thread_id': tid, 'status': 'archived'}
            report = machine_cleanup.run(root, local_jobs.load_config(ROOT / 'ops/config/local-jobs.json'),
                hook=None, tmp_root=root, dispatch_path=root / 'absent', roots={}, provider=object(),
                process_probe=lambda paths: set(), archive_thread=archive)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['removed'][0]['thread_id'], 'finished')
            journal = [json.loads(line) for line in (root / 'out/machine-cleanup-recovery.jsonl').read_text().splitlines()]
            self.assertEqual(journal[0]['previous'], previous)

    def test_interrupted_thread_reconciliation_keeps_original_receipt(self):
        from unittest.mock import patch
        import local_jobs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            previous = {'at': 1, 'status': 'running', 'last_success_at': 0,
                'removed': [{'kind': 'thread', 'thread_id': 'prior', 'status': 'archived'}],
                'kept': [], 'pending': {'kind': 'thread', 'thread_id': 'finished', 'before': 100}, 'errors': []}
            receipt.write_text(json.dumps(previous))
            with patch.object(machine_cleanup, 'save') as save:
                with self.assertRaises(KeyboardInterrupt):
                    machine_cleanup.run(root, local_jobs.load_config(ROOT / 'ops/config/local-jobs.json'),
                        hook=None, tmp_root=root, dispatch_path=root / 'absent', roots={}, provider=object(),
                        process_probe=lambda paths: set(), archive_thread=lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
                save.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text()), previous)

    def test_interrupted_temp_missing_manifest_stays_failed_with_evidence(self):
        import local_jobs
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            previous = {'at': 1, 'status': 'running', 'last_success_at': 0,
                'removed': [{'kind': 'thread', 'thread_id': 'prior', 'status': 'archived'}],
                'kept': [], 'pending': {'kind': 'test_temp', 'path': str(root / 'carr-ci-test-missing'),
                    'status': 'removing'}, 'errors': []}
            receipt.write_text(json.dumps(previous))
            report = machine_cleanup.run(root, local_jobs.load_config(ROOT / 'ops/config/local-jobs.json'),
                hook=None, tmp_root=root, dispatch_path=root / 'absent', roots={}, provider=object(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
            self.assertEqual(report['status'], 'failed')
            for key in ('pending', 'removed', 'last_success_at'):
                self.assertEqual(report[key], previous[key])

    def test_fixture_inspection_supplies_bytes_without_a_second_unbounded_du(self):
        from unittest.mock import patch
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            candidate.mkdir()
            (candidate / 'payload').write_bytes(b'x' * 8192)
            for path in (candidate / 'payload', candidate):
                os.utime(path, (0, 0))
            with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('du', 3)):
                report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                    max_items=100, cursor='', process_probe=lambda paths: set(),
                    deadline=lambda: False, dry_run=False)
            self.assertFalse(report['errors'], report)
            self.assertEqual(len(report['removed']), 1)
            self.assertGreaterEqual(report['staged_allocated_bytes'], 8192)

    def test_lane_expiring_after_rename_still_verifies_the_completed_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            candidate.mkdir()
            os.utime(candidate, (0, 0))
            expired = False
            calls = 0
            def probe(paths):
                nonlocal calls, expired
                calls += 1
                expired = calls >= 3
                return set()
            report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                max_items=100, cursor='', process_probe=probe, deadline=lambda: expired,
                dry_run=False)
            self.assertFalse(report['errors'], report)
            self.assertEqual(len(report['removed']), 1)
            self.assertEqual(report['removed'][0]['status'], 'staged')
            self.assertTrue(report['budget_exhausted'])

    def test_inspection_budget_after_intent_keeps_fixture_without_failed_pending_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            candidate.mkdir()
            os.utime(candidate, (0, 0))
            expired = False
            calls = 0
            def probe(paths):
                nonlocal calls, expired
                calls += 1
                expired = calls >= 2
                return set()
            rows = []
            report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                max_items=100, cursor='', process_probe=probe, deadline=lambda: expired,
                dry_run=False, record=rows.append)
            self.assertTrue(candidate.exists())
            self.assertFalse(report['errors'], report)
            self.assertNotIn('error', rows[-1])
            self.assertIn('budget', rows[-1]['reason'])
            manifest = json.loads(Path(rows[0]['manifest_path']).read_text())
            self.assertEqual(manifest['status'], 'verified_no_effect')
            self.assertTrue(report['budget_exhausted'])

    def test_process_opening_during_staging_restores_the_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            candidate.mkdir()
            os.utime(candidate, (0, 0))
            calls = []
            def probe(paths):
                calls.append(paths)
                return set(paths) if len(calls) == 3 else set()
            report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                max_items=100, cursor='', process_probe=probe, deadline=lambda: False, dry_run=False)
            self.assertTrue(candidate.exists())
            self.assertFalse(report['removed'])
            self.assertIn('restored', report['kept'][0]['reason'])

    def test_nested_write_during_final_probe_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / 'carr-ci-test-owned'
            nested = candidate / 'nested'
            nested.mkdir(parents=True)
            payload = nested / 'payload'
            payload.write_text('old')
            for path in (payload, nested, candidate):
                os.utime(path, (0, 0))
            calls = []
            def probe(paths):
                calls.append(paths)
                if len(calls) == 2:
                    payload.write_text('just written by a test that has now exited')
                return set()
            report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                max_items=1000, cursor='', process_probe=probe, deadline=lambda: False,
                dry_run=False)
            self.assertTrue(payload.exists(), report)

    def test_detached_clone_with_open_pr_is_kept(self):
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-owned'
        clone = workspace / 'checkout'
        workspace.mkdir()
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        fixture.git('switch', '-c', 'job', cwd=clone)
        fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
        fixture.git('config', 'user.name', 'Fixture', cwd=clone)
        head = fixture.commit('pushed open PR work', cwd=clone)
        fixture.git('push', 'origin', 'job', cwd=clone)
        fixture.git('checkout', '--detach', head, cwd=clone)
        pr = fixtures.pull(1, head, fixture.git('rev-parse', 'main'))
        pr['head']['ref'] = 'job'
        provider = fixtures.FakeProvider([pr])
        receipt = machine_cleanup.clean_clone(workspace, {'status': 'completed',
            'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'dispatched_at': '2020-01-01T00:00:00+00:00'}, provider=provider,
            process_probe=lambda paths: set(), now=time.time() + 8 * 86400,
            older_than=7 * 86400, dry_run=False)
        self.assertEqual(receipt['status'], 'kept', receipt)

    def test_detached_dispatch_branch_protects_advanced_open_pr_and_allows_closed_pr(self):
        for condition in ('advanced_open', 'closed', 'unbound_closed'):
            with self.subTest(condition=condition):
                fixture = fixtures.Fixture()
                workspace = fixture.base / 'room-codex-detached'
                clone = workspace / 'checkout'
                workspace.mkdir()
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', '-c', 'job', 'origin/main', cwd=clone)
                fixture.git('push', 'origin', 'job', cwd=clone)
                head = fixture.git('rev-parse', 'HEAD', cwd=clone)
                fixture.git('checkout', '--detach', head, cwd=clone)
                pr = fixtures.pull(1, 'f' * 40 if condition == 'advanced_open' else head, head)
                pr['head']['ref'] = 'job'
                pr['state'] = 'open' if condition == 'advanced_open' else 'closed'
                dispatch = {'status': 'completed', 'checkout_path': str(clone),
                    'checkout_workspace': str(workspace), 'dispatched_at': '2020-01-01T00:00:00+00:00'}
                if condition != 'unbound_closed':
                    dispatch['checkout_branch'] = 'job'
                result = machine_cleanup.clean_clone(workspace, dispatch, provider=fixtures.FakeProvider([pr]),
                    process_probe=lambda paths: set(), now=time.time(), older_than=1, dry_run=False)
                self.assertEqual(result['status'], 'staged' if condition == 'closed' else 'kept', result)
                self.assertNotIn('error', result)

    def test_scheduled_machine_entry_never_falls_through_to_permanent_deletion(self):
        from unittest.mock import patch
        from contextlib import redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(storage_hygiene, 'storage_roots', return_value=(root, root, root)), \
                 patch.object(storage_hygiene, 'storage_snapshot', return_value=(0, 0)), \
                 patch.object(machine_cleanup, 'run', return_value={'errors': [], 'removed': [], 'kept': []}), \
                 patch.object(storage_hygiene, 'plan_cleanup', side_effect=AssertionError('permanent cleanup path')), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(storage_hygiene.main(['--machine']), 0)

    def test_commit_pr_lookup_is_bounded_and_refuses_incomplete_evidence(self):
        from branch_retirement import GitHub
        from unittest.mock import patch
        provider = GitHub()
        connection = {'nodes': [], 'pageInfo': {'hasNextPage': True, 'endCursor': 'more'}}
        with patch.object(provider, 'query', return_value={'object': {'associatedPullRequests': connection}}) as query:
            with self.assertRaises(RuntimeError):
                provider.commit_pulls('fixture/repo', 'a' * 40)
            self.assertEqual(query.call_count, 1)
        connection['pageInfo']['hasNextPage'] = False
        with patch.object(provider, 'query', return_value={'object': {'associatedPullRequests': connection}}):
            self.assertEqual(provider.commit_pulls('fixture/repo', 'a' * 40), [])

    def test_health_storage_reader_still_has_callable_root_discovery(self):
        tree = ast.parse((ROOT / 'tools/health-check.py').read_text())
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == '_storage_hygiene_row')
        scope = {'sys': sys, 'Path': Path}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'health-check.py', 'exec'), scope)
        from unittest.mock import patch
        with patch.object(storage_hygiene, 'storage_roots', return_value=(Path('/fixture/T'), Path('/fixture/X'), Path('/fixture/R'))), \
             patch.object(storage_hygiene, 'storage_snapshot', return_value=(0, 0)):
            scope['_storage_hygiene_row']()


if __name__ == '__main__':
    unittest.main(verbosity=2)
