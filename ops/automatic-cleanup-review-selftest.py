import ast
import importlib.util
import os
import shutil
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
    def test_thread_timeout_defers_only_that_item_and_reads_back_before_retry(self):
        import job_watchdog
        for returned_error in (False, True):
            with self.subTest(returned_error=returned_error), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                ledger = root / 'dispatch.jsonl'
                ledger.write_text(''.join(json.dumps({'thread_id': tid, 'status': 'completed',
                    'dispatched_at': '2020-01-01T00:00:00Z'}) + '\n' for tid in ('slow', 'healthy')))
                calls = []
                def archive(tid, **kwargs):
                    calls.append((tid, kwargs['dry_run']))
                    if tid == 'slow':
                        if returned_error:
                            return {'kind': 'thread', 'thread_id': tid, 'status': 'kept', 'error': 'TimeoutError'}
                        raise TimeoutError('archive unavailable')
                    return {'kind': 'thread', 'thread_id': tid, 'status': 'archived'}
                options = dict(hook=None, tmp_root=root, dispatch_path=ledger, roots={}, provider=object(),
                    process_probe=lambda paths: set(), archive_thread=archive)
                config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                first = machine_cleanup.run(root, config, **options)
                self.assertFalse(first['errors'], first)
                self.assertEqual(first['status'], 'deferred')
                self.assertEqual(first['removed'][0]['thread_id'], 'healthy')
                self.assertNotIn('pending', first)
                calls.clear()
                second = machine_cleanup.run(root, config, **options)
                self.assertIn(('slow', True), calls)
                self.assertNotIn(('slow', False), calls)
                self.assertFalse(second['errors'], second)
                self.assertEqual(len(second['deferred']), 1)

    def test_pending_thread_timeout_allows_other_cleanup_and_preserves_readback(self):
        import job_watchdog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / 'scratch'
            candidate = scratch / 'carr-ci-test-finished'
            candidate.mkdir(parents=True)
            os.utime(candidate, (0, 0))
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            pending = {'kind': 'thread', 'thread_id': 'slow', 'before': 100}
            receipt.write_text(json.dumps({'at': 1, 'pending': pending, 'errors': []}))
            calls = []
            def archive(tid, **kwargs):
                calls.append(kwargs['dry_run'])
                return {'kind': 'thread', 'thread_id': tid, 'status': 'kept', 'error': 'TimeoutError'}
            report = machine_cleanup.run(root, job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json'),
                hook=None, tmp_root=root, user_tmp_root=scratch, dispatch_path=root / 'absent', roots={},
                provider=object(), process_probe=lambda paths: set(), archive_thread=archive)
            self.assertFalse(report['errors'], report)
            self.assertFalse(candidate.exists())
            self.assertEqual(report['deferred'][0]['pending'], pending)
            self.assertEqual(calls, [True])

    def test_cached_evidence_never_overrides_final_publication_or_live_process(self):
        for changed in ('remote', 'process'):
            with self.subTest(changed=changed):
                fixture = fixtures.Fixture()
                workspace = fixture.base / 'room-codex-changed'
                clone = workspace / 'checkout'
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', '-c', 'job', 'origin/main', cwd=clone)
                fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
                fixture.git('config', 'user.name', 'Fixture', cwd=clone)
                fixture.commit('published job', cwd=clone)
                fixture.git('push', 'origin', 'job', cwd=clone)
                provider = fixtures.FakeProvider()
                def final_pulls(repo, branch):
                    if changed == 'remote' and branch is not None:
                        fixture.git('push', 'origin', '--delete', 'job', cwd=clone)
                    return []
                provider.open_pulls = final_pulls
                probes = []
                def processes(paths):
                    probes.append(paths)
                    return set(paths) if changed == 'process' and len(probes) > 1 else set()
                evidence = machine_cleanup.CloneEvidence(provider, processes, [workspace], roots={})
                result = machine_cleanup.clean_clone(workspace, {'status': 'completed',
                    'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                    'dispatched_at': '2020-01-01T00:00:00Z'}, provider=provider,
                    process_probe=processes, now=time.time(), older_than=0, dry_run=False,
                    evidence=evidence)
                self.assertEqual(result['status'], 'kept', result)
                self.assertNotIn('error', result)
                self.assertTrue(clone.exists())

    def test_published_ancestor_uses_canonical_objects_without_fetching_clone(self):
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-old-published'
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        fixture.commit('new published main')
        fixture.git('push', 'origin', 'main')
        provider = fixtures.FakeProvider()
        from unittest.mock import patch
        with patch.object(machine_cleanup, 'repository_roots', return_value={'jbookout/carr-system': fixture.repo}):
            evidence = machine_cleanup.CloneEvidence(provider, lambda paths: set(), [workspace])
            result = machine_cleanup.clean_clone(workspace, {'status': 'completed',
                'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                'dispatched_at': '2020-01-01T00:00:00Z'}, provider=provider,
                process_probe=lambda paths: set(), now=time.time(), older_than=0, dry_run=True,
                evidence=evidence)
        self.assertEqual(result['status'], 'would_stage', result)

    def test_dry_run_shares_remote_pr_evidence_across_finished_clones(self):
        import job_watchdog
        from unittest.mock import patch
        fixture = fixtures.Fixture()
        clones = fixture.base / 'clones'
        clones.mkdir()
        ledger = fixture.base / 'dispatch.jsonl'
        rows = []
        for index in range(6):
            workspace = clones / ('room-codex-' + str(index))
            clone = workspace / 'checkout'
            fixture.git('clone', str(fixture.remote), str(clone))
            fixture.git('switch', 'main', cwd=clone)
            rows.append({'status': 'completed', 'checkout_workspace': str(workspace),
                'checkout_path': str(clone), 'dispatched_at': index})
        ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        elapsed = [0]
        class SlowProvider(fixtures.FakeProvider):
            def branch_pulls(self, repo, branch):
                elapsed[0] += 50
                return []
            def open_pulls(self, repo, branch):
                elapsed[0] += 50
                return []
        config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        config['machine_cleanup']['job_clone_threshold'] = 2
        with patch.object(machine_cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]):
            report = machine_cleanup.run(fixture.repo, config, hook=None, tmp_root=clones,
                dispatch_path=ledger, roots={}, provider=SlowProvider(), dry_run=True,
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
        selected = [r for r in report['kept'] if r['status'] == 'would_stage']
        self.assertEqual(len(selected), 4, report)
        self.assertFalse(report['errors'], report)
        self.assertEqual(report['job_clones']['would_stage'], 4)
        self.assertEqual(report['job_clones']['projected_remaining'], 2)
        self.assertEqual(report['job_clones']['threshold'], 2)
        self.assertEqual(sum(report['job_clones']['reasons'].values()), 4)

    def test_finished_clone_from_each_authorized_repository_is_eligible(self):
        import job_watchdog
        fixture = fixtures.Fixture()
        for index, repo in enumerate(job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')['repositories']):
            with self.subTest(repo=repo):
                workspace = fixture.base / ('room-codex-repo-' + str(index))
                clone = workspace / 'checkout'
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', 'main', cwd=clone)
                provider = fixtures.FakeProvider()
                def verify_repository(actual, path):
                    if actual != repo:
                        raise RuntimeError('wrong repository')
                provider.verify_repository = verify_repository
                result = machine_cleanup.clean_clone(workspace, {'status': 'completed',
                    'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                    'dispatched_at': '2020-01-01T00:00:00Z'}, provider=provider,
                    process_probe=lambda paths: set(), now=time.time(), older_than=0, dry_run=True)
                self.assertEqual(result['status'], 'would_stage', result)

    def test_published_clone_dry_run_needs_no_remote_object_download(self):
        from unittest.mock import patch
        import subprocess
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-published'
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        original = subprocess.run

        def without_download(argv, *args, **kwargs):
            if argv[0] == 'git' and 'fetch' in argv:
                raise subprocess.TimeoutExpired('git fetch', 120)
            return original(argv, *args, **kwargs)

        with patch('subprocess.run', side_effect=without_download):
            result = machine_cleanup.clean_clone(workspace, {'status': 'completed',
                'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                'dispatched_at': '2020-01-01T00:00:00Z'}, provider=fixtures.FakeProvider(),
                process_probe=lambda paths: set(), now=time.time(), older_than=0, dry_run=True)
        self.assertEqual(result['status'], 'would_stage', result)
        self.assertTrue(clone.exists())

    def test_writer_lock_and_recorded_live_process_group_keep_clone(self):
        import fcntl
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        import write_ownership
        for held in (False, True):
            with self.subTest(held=held):
                fixture = fixtures.Fixture()
                workspace = fixture.base / 'room-codex-writer'
                workspace.mkdir()
                clone = workspace / 'checkout'
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', 'main', cwd=clone)
                lock = clone / '.git/room-writer.lock'
                with lock.open('w+') as stream:
                    if held:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        json.dump({**write_ownership.process_owner(), 'kind': 'process_group', 'pgid': os.getpgrp()}, stream)
                        stream.flush()
                    result = machine_cleanup.clean_clone(workspace, {'status': 'completed',
                        'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                        'dispatched_at': '2020-01-01T00:00:00Z'}, provider=fixtures.FakeProvider(),
                        process_probe=lambda paths: set(), now=time.time(), older_than=1, dry_run=False)
                self.assertEqual(result['status'], 'kept', result)
                self.assertIn('writer', result['reason'])
                self.assertTrue(workspace.exists())

    def test_finished_failed_job_with_stale_termination_flag_reclaims_when_writer_is_gone(self):
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-ended'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        result = machine_cleanup.clean_clone(workspace, {'status': 'failed', 'termination_confirmed': False,
            'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'dispatched_at': '2020-01-01T00:00:00Z'}, provider=fixtures.FakeProvider(),
            process_probe=lambda paths: set(), now=time.time(), older_than=0, dry_run=False)
        self.assertEqual(result['status'], 'staged', result)
        self.assertTrue((Path(result['staged_path']) / 'checkout/file').exists())

    def test_unpushed_commit_on_another_branch_keeps_clone(self):
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-unpushed'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', '-c', 'unpublished', 'origin/main', cwd=clone)
        fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
        fixture.git('config', 'user.name', 'Fixture', cwd=clone)
        fixture.commit('unpublished bytes', cwd=clone)
        fixture.git('switch', 'main', cwd=clone)
        result = machine_cleanup.clean_clone(workspace, {'status': 'completed',
            'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'dispatched_at': '2020-01-01T00:00:00Z'}, provider=fixtures.FakeProvider(),
            process_probe=lambda paths: set(), now=time.time(), older_than=0, dry_run=False)
        self.assertEqual(result['status'], 'kept', result)
        self.assertIn('unpushed', result['reason'])
        self.assertTrue(workspace.exists())

    def test_pressure_stages_finished_clones_oldest_first_down_to_threshold(self):
        import job_watchdog
        fixture = fixtures.Fixture()
        fixture.git('switch', '-c', 'job')
        fixture.commit('published job')
        fixture.git('push', 'origin', 'job')
        ledger = fixture.base / 'dispatch.jsonl'
        rows = []
        workspaces = []
        for index, name in enumerate(('a', 'b', 'c', 'd', 'e')):
            workspace = fixture.base / ('room-codex-' + name)
            workspace.mkdir()
            workspaces.append(workspace)
            clone = workspace / 'checkout'
            fixture.git('clone', str(fixture.remote), str(clone))
            fixture.git('switch', 'job', cwd=clone)
            rows.append({'status': 'completed', 'checkout_workspace': str(workspace),
                'checkout_path': str(clone), 'dispatched_at': time.time() - index * 60})
        # The oldest clone must survive, even though HEAD is published.
        oldest = workspaces[-1] / 'checkout'
        fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=oldest)
        fixture.git('config', 'user.name', 'Fixture', cwd=oldest)
        fixture.commit('private work', cwd=oldest)
        ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        config['machine_cleanup']['job_clone_threshold'] = 2
        options = dict(hook=None, tmp_root=fixture.base, dispatch_path=ledger, roots={},
            provider=fixtures.FakeProvider(), process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
        dry = machine_cleanup.run(fixture.repo, config, dry_run=True, **options)
        self.assertEqual([Path(r['path']).name for r in dry['kept'] if r['status'] == 'would_stage'],
                         ['room-codex-d', 'room-codex-c', 'room-codex-b'], dry)
        self.assertTrue(all(p.exists() for p in workspaces))
        config['machine_cleanup']['max_items'] = 1
        staged = []
        for _ in range(4):
            report = machine_cleanup.run(fixture.repo, config, **options)
            self.assertFalse(report['errors'], report)
            staged.extend(Path(r['path']).name for r in report['removed'])
        self.assertEqual(staged, ['room-codex-d', 'room-codex-c', 'room-codex-b'])
        self.assertEqual(sum(p.exists() for p in workspaces), 2)
        self.assertTrue(workspaces[0].exists())
        self.assertTrue(workspaces[-1].exists())

    def test_size_thresholds_resolve_for_410_clones_and_large_user_temp(self):
        import job_watchdog
        fixture = fixtures.Fixture()
        template = fixture.base / 'template'
        fixture.git('clone', str(fixture.remote), str(template))
        fixture.git('switch', 'main', cwd=template)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for index in range(410):
                clone = root / ('room-codex-' + str(index))
                shutil.copytree(template, clone / 'checkout')
                for child in range(25):
                    (clone / str(child)).touch()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            receipt.write_text(json.dumps({'last_success_at': time.time(), 'errors': []}))
            config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
            rows = machine_cleanup.health_findings(root, config, tmp_root=root,
                user_tmp_root=root, roots={}, now=time.time())
            self.assertEqual([r['code'] for r in rows], ['job_clone_count'], rows)

    def test_interruption_before_deferred_thread_readback_preserves_retry_intent(self):
        from unittest.mock import patch
        import job_watchdog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            deferred = {'kind': 'thread', 'thread_id': 'uncertain', 'status': 'deferred',
                'pending': {'kind': 'thread', 'thread_id': 'uncertain', 'before': 100}}
            receipt.write_text(json.dumps({'at': 1, 'deferred': [deferred]}))
            with patch('machine_cleanup.entries', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    machine_cleanup.run(root, job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json'),
                        hook=None, tmp_root=root, dispatch_path=root / 'absent', roots={}, provider=object(),
                        process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
            self.assertEqual(json.loads(receipt.read_text())['deferred'], [deferred])

    def test_large_population_size_uses_an_explicit_sample_estimate(self):
        from unittest.mock import patch
        import subprocess
        paths = [Path('/fixture') / str(index) for index in range(410)]
        def native(argv, **kwargs):
            if len(argv[2:]) > 32:
                raise subprocess.TimeoutExpired('du', 30)
            return subprocess.CompletedProcess(argv, 0, ''.join('8\t' + path + '\n' for path in argv[2:]), '')
        with patch('storage_hygiene.subprocess.run', side_effect=native):
            measured = storage_hygiene.allocated_bytes_evidence(paths)
        self.assertTrue(measured['complete'], measured)
        self.assertTrue(measured['estimated'])
        self.assertEqual(measured['population_size'], 410)
        self.assertEqual(measured['allocated_bytes'], 3358720)
        self.assertEqual(measured['measured_allocated_bytes'], 262144)

    def test_clone_retention_threshold_keeps_the_remaining_clones_on_later_runs(self):
        import job_watchdog
        fixture = fixtures.Fixture()
        ledger = fixture.base / 'dispatch.jsonl'
        workspace = fixture.base / 'room-codex-retained'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        ledger.write_text(json.dumps({'status': 'completed', 'checkout_workspace': str(workspace),
            'checkout_path': str(clone), 'dispatched_at': '2020-01-01T00:00:00Z'}) + '\n')
        report = machine_cleanup.run(fixture.repo, job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json'),
            hook=None, tmp_root=fixture.base, dispatch_path=ledger, roots={}, provider=fixtures.FakeProvider(),
            process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
        self.assertFalse(report['removed'], report)
        self.assertTrue(workspace.exists())

    def test_interrupted_temp_effect_is_reconciled_in_next_receipt(self):
        from unittest.mock import patch
        import job_watchdog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch, clones = root / 'scratch', root / 'clones'
            scratch.mkdir()
            clones.mkdir()
            candidate = scratch / 'carr-ci-test-owned'
            candidate.mkdir()
            os.utime(candidate, (0, 0))
            config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
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
        import job_watchdog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            previous = {'at': 1, 'status': 'running', 'last_success_at': 0,
                'removed': [{'kind': 'thread', 'thread_id': 'prior', 'status': 'archived'}],
                'kept': [{'kind': 'test_temp', 'path': 'protected', 'status': 'kept'}],
                'pending': {'kind': 'unknown', 'path': 'uncertain'}, 'errors': []}
            receipt.write_text(json.dumps(previous))
            config = job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
            for _ in range(2):
                report = machine_cleanup.run(root, config, hook=None, tmp_root=root,
                    dispatch_path=root / 'absent', roots={}, provider=object(),
                    process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
                self.assertEqual(report['status'], 'failed')
                for key in ('removed', 'kept', 'pending', 'last_success_at'):
                    self.assertEqual(report[key], previous[key])

    def test_thread_recovery_observes_previous_receipt_before_replacing_it(self):
        import job_watchdog
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
            report = machine_cleanup.run(root, job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json'),
                hook=None, tmp_root=root, dispatch_path=root / 'absent', roots={}, provider=object(),
                process_probe=lambda paths: set(), archive_thread=archive)
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(report['removed'][0]['thread_id'], 'finished')
            journal = [json.loads(line) for line in (root / 'out/machine-cleanup-recovery.jsonl').read_text().splitlines()]
            self.assertEqual(journal[0]['previous'], previous)

    def test_interrupted_thread_reconciliation_keeps_original_receipt(self):
        from unittest.mock import patch
        import job_watchdog
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
                    machine_cleanup.run(root, job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json'),
                        hook=None, tmp_root=root, dispatch_path=root / 'absent', roots={}, provider=object(),
                        process_probe=lambda paths: set(), archive_thread=lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
                save.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text()), previous)

    def test_interrupted_temp_missing_manifest_stays_failed_with_evidence(self):
        import job_watchdog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            previous = {'at': 1, 'status': 'running', 'last_success_at': 0,
                'removed': [{'kind': 'thread', 'thread_id': 'prior', 'status': 'archived'}],
                'kept': [], 'pending': {'kind': 'test_temp', 'path': str(root / 'carr-ci-test-missing'),
                    'status': 'removing'}, 'errors': []}
            receipt.write_text(json.dumps(previous))
            report = machine_cleanup.run(root, job_watchdog.load_config(ROOT / 'ops/config/job-watchdog.json'),
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
