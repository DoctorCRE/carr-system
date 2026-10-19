#!/usr/bin/env python3
"""Machine cleanup behavior against temporary repositories and owned processes."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import uuid
import tracemalloc
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'tools'), str(ROOT / 'ops')]
import job_watchdog as watchdog


class WatchdogBudgetTests(unittest.TestCase):
    def test_budget_kills_term_ignoring_descendant_and_records_timeout(self):
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        config['thresholds']['kill_grace_seconds'] = 0.1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / 'child.py'
            child.write_text('import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n')
            parent = root / 'parent.py'
            parent.write_text('import subprocess,sys,time\np=subprocess.Popen([sys.executable,sys.argv[1]])\nprint(p.pid,flush=True)\ntime.sleep(30)\n')
            start = time.monotonic()
            with patch.object(watchdog, 'board_task'), redirect_stdout(io.StringIO()):
                code = watchdog.run_job(root, config, 'fixture', 'test', .005,
                                        [sys.executable, str(parent), str(child)])
            self.assertLess(time.monotonic() - start, 3)
            self.assertEqual(code, 124)
            row = next(iter(watchdog.read_latest(root / config['paths']['registry']).values()))
            self.assertTrue(row['timed_out'])
            pid = int(Path(row['log_path']).read_text().strip())
            state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith('Z'), state)

    def test_watchdog_rejects_nonfinite_budgets_before_starting_a_job(self):
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        for minutes in (float('nan'), float('inf')):
            with self.subTest(minutes=minutes), tempfile.TemporaryDirectory() as directory:
                with patch.object(watchdog, 'board_task'), redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError):
                        watchdog.run_job(Path(directory), config, 'fixture', 'test', minutes,
                                         [sys.executable, '-c', 'pass'])

    def test_watchdog_job_wrapper_is_independent_of_scan_agent_hold(self):
        from unittest.mock import Mock
        spec = importlib.util.spec_from_file_location('watchdog_cli', ROOT / 'tools/job-watchdog.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, 'read_holds', return_value={
                'com.carr.job-watchdog': Mock()}), patch.object(cli.watchdog, 'run_job', return_value=0) as run_job:
            with patch.object(sys, 'argv', ['job-watchdog.py', '--root', directory, 'run',
                    'storage-hygiene', 'cleanup', '5', '--', sys.executable, '-c', 'pass']), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
            run_job.assert_called_once()


spec = importlib.util.spec_from_file_location('janitor_fixtures', ROOT / 'ops/branch-janitor-selftest.py')
assert spec is not None and spec.loader is not None
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class RetirementKeepTests(unittest.TestCase):
    def test_local_reaper_waits_for_competing_maintenance_then_makes_progress(self):
        import fcntl
        import threading
        fixture = fixtures.Fixture()
        fixture.tree('finished')
        lock = fixture.repo / 'out/worktree-reap.lock'
        lock.parent.mkdir(exist_ok=True)
        with lock.open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            timer = threading.Timer(.15, lambda: fcntl.flock(stream, fcntl.LOCK_UN))
            timer.start()
            try:
                report = fixture.reaper().run({'fixture/repo': fixture.repo},
                    local_only=True, max_seconds=2)
            finally:
                timer.join()
        self.assertFalse(report['deferred'], report)
        self.assertTrue(report['rows'], report)

    def test_dirty_old_worktree_is_kept_and_reported(self):
        fixture = fixtures.Fixture()
        tree = fixture.tree('dirty')
        (tree / 'file').write_text('uncommitted work')
        fixture.age(tree)
        report = fixture.reaper().run({'fixture/repo': fixture.repo}, execute=True)
        row = next(r for r in report['rows'] if r.get('path') == str(tree))
        self.assertIsNone(row['action'])
        self.assertIn('uncommitted', row['reason'])
        self.assertTrue(tree.exists())

    def test_unpushed_old_worktree_is_kept(self):
        fixture = fixtures.Fixture()
        tree = fixture.tree('unpushed')
        fixture.commit('local only', cwd=tree)
        fixture.age(tree)
        report = fixture.reaper().run({'fixture/repo': fixture.repo}, execute=True)
        row = next(r for r in report['rows'] if r.get('path') == str(tree))
        self.assertIsNone(row['action'])
        self.assertIn('unpushed', row['reason'])
        self.assertTrue(tree.exists())

    def test_reaper_batches_and_resumes_after_each_completed_candidate(self):
        fixture = fixtures.Fixture()
        for name in ('first', 'second', 'third'):
            fixture.tree(name)
        first = fixture.reaper().run({'fixture/repo': fixture.repo}, max_items=2)
        second = fixture.reaper().run({'fixture/repo': fixture.repo}, max_items=2)
        self.assertEqual(len(first['rows']), 2)
        self.assertEqual(len(second['rows']), 2)
        keys = lambda report: {(r['kind'], r.get('ref'), r.get('path')) for r in report['rows']}
        self.assertFalse(keys(first) & keys(second))
        self.assertTrue(first['budget_exhausted'])

    def test_local_reaper_memory_is_bounded_and_cursor_reaches_later_trees(self):
        fixture = fixtures.Fixture()
        peaks = []
        for start, stop in ((0, 40), (40, 400)):
            for index in range(start, stop):
                fixture.git('worktree', 'add', '--detach', '--no-checkout', str(fixture.base / f'tree-{index:04}'))
            tracemalloc.start()
            report = fixture.reaper().run({'fixture/repo': fixture.repo}, local_only=True, max_items=10)
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            self.assertFalse(report['errors'], report)
            self.assertEqual(len(report['rows']), 10)
        self.assertLess(peaks[1], peaks[0] * 2 + 65536, peaks)
        self.assertTrue(any('tree-001' in r.get('path', '') for r in report['rows']), report)
        self.assertEqual(sum(1 for _ in __import__('branch_retirement').iter_worktrees(fixture.repo)), 401)


class JobCloneTests(unittest.TestCase):
    def test_finished_clone_keep_rules(self):
        import machine_cleanup as cleanup
        for condition in ('dirty', 'unpushed', 'live', 'open_pr', 'unfinished', 'unknown_process'):
            with self.subTest(condition=condition):
                fixture = fixtures.Fixture()
                workspace = fixture.base / 'room-codex-fixture'
                workspace.mkdir()
                clone = workspace / 'checkout'
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', 'main', cwd=clone)
                fixture.git('switch', '-c', 'job', cwd=clone)
                if condition == 'dirty':
                    (clone / 'file').write_text('keep these bytes')
                if condition == 'unpushed':
                    fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
                    fixture.git('config', 'user.name', 'Fixture', cwd=clone)
                    fixture.commit('not pushed', cwd=clone)
                pr = fixtures.pull(1, fixture.git('rev-parse', 'HEAD', cwd=clone), fixture.git('rev-parse', 'main'))
                pr['head']['ref'] = 'job'
                provider = fixtures.FakeProvider([pr] if condition == 'open_pr' else [])
                provider.branch_pulls = lambda repo, branch: provider.rows
                probe = lambda paths: None if condition == 'unknown_process' else ({str(workspace)} if condition == 'live' else set())
                row = {'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                       'status': 'timed_out' if condition == 'unfinished' else 'completed',
                       'dispatched_at': '2020-01-01T00:00:00+00:00'}
                receipt = cleanup.clean_clone(workspace, row, provider=provider, process_probe=probe,
                                               now=time.time(), older_than=7 * 86400, dry_run=False)
                self.assertEqual(receipt['status'], 'kept', receipt)
                self.assertTrue(clone.exists())
                self.assertNotIn('error', receipt, receipt)
                reasons = {'dirty': 'uncommitted', 'unpushed': 'unpushed', 'live': 'live process',
                           'open_pr': 'open PR', 'unfinished': 'finished dispatch', 'unknown_process': 'evidence unavailable'}
                self.assertIn(reasons[condition], receipt['reason'])

    def test_safe_clone_removal_rules_stage_and_preserve_bytes(self):
        import machine_cleanup as cleanup
        for rule in ('merged', 'closed', 'aged'):
            with self.subTest(rule=rule):
                fixture = fixtures.Fixture()
                workspace = fixture.base / 'room-codex-safe'
                workspace.mkdir()
                clone = workspace / 'checkout'
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', 'main', cwd=clone)
                fixture.git('switch', '-c', 'job', cwd=clone)
                if rule != 'merged':
                    fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
                    fixture.git('config', 'user.name', 'Fixture', cwd=clone)
                    fixture.commit('pushed abandoned bytes', cwd=clone)
                    fixture.git('push', 'origin', 'job', cwd=clone)
                head = fixture.git('rev-parse', 'HEAD', cwd=clone)
                pr = fixtures.pull(1, head, fixture.git('rev-parse', 'main'))
                pr['state'], pr['head']['ref'] = 'closed', 'job'
                provider = fixtures.FakeProvider([pr] if rule == 'closed' else [])
                provider.branch_pulls = lambda repo, branch: provider.rows
                row = {'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                       'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00+00:00'}
                now = time.time() + (8 * 86400 if rule == 'aged' else 0)
                receipt = cleanup.clean_clone(workspace, row, provider=provider, process_probe=lambda paths: set(),
                                               now=now, older_than=7 * 86400, dry_run=False)
                self.assertEqual(receipt['status'], 'staged', receipt)
                self.assertFalse(workspace.exists())
                staged = Path(receipt['staged_path']) / 'checkout'
                self.assertEqual(fixture.git('rev-parse', 'HEAD', cwd=staged), head)
                self.assertTrue((staged.parent.parent / 'manifest.json').exists())

    def test_clone_with_symlinked_git_directory_is_kept_before_git_runs(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-alias'
        clone = workspace / 'checkout'
        clone.mkdir(parents=True)
        (clone / '.git').symlink_to(fixture.repo / '.git', target_is_directory=True)
        provider = fixtures.FakeProvider()
        with patch.object(provider, 'verify_repository') as verify:
            receipt = cleanup.clean_clone(workspace,
                {'status': 'completed', 'checkout_path': str(clone), 'checkout_workspace': str(workspace)},
                provider=provider, process_probe=lambda paths: set(), now=time.time(), older_than=1, dry_run=False)
        verify.assert_not_called()
        self.assertEqual(receipt['status'], 'kept')

    def test_process_starting_during_final_pr_check_keeps_clone(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-race'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        provider = fixtures.FakeProvider()
        live = []
        def open_pulls(repo, branch):
            live.append(True)
            return []
        provider.open_pulls = open_pulls
        result = cleanup.clean_clone(workspace, {'status': 'completed', 'checkout_path': str(clone),
            'checkout_workspace': str(workspace), 'dispatched_at': '2020-01-01T00:00:00+00:00'},
            provider=provider, process_probe=lambda paths: set(paths) if live else set(),
            now=time.time(), older_than=1, dry_run=False)
        self.assertEqual(result['status'], 'kept', result)
        self.assertTrue(workspace.exists())


class ThreadArchiveTests(unittest.TestCase):
    def test_native_archive_preserves_active_recent_pinned_and_live_descendants(self):
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        import codex_wire
        for condition in ('active', 'recent', 'pinned', 'descendant', 'process', 'unknown', 'safe'):
            with self.subTest(condition=condition):
                calls = []
                thread = {'id': 'fixture-thread', 'updatedAt': 10, 'status': {'type': 'notLoaded'},
                          'path': '/fixture/session.jsonl'}
                if condition == 'active':
                    thread['status'] = {'type': 'active'}
                if condition == 'recent':
                    thread['updatedAt'] = 1000
                if condition == 'pinned':
                    thread['isPinned'] = True
                class Wire:
                    def __init__(self, timeout):
                        pass
                    def upgrade(self):
                        pass
                    def send_json(self, request):
                        calls.append(request)
                        self.request = request
                    def receive_json(self):
                        method = self.request['method']
                        if method == 'thread/archive':
                            thread['path'] = '/fixture/archived_sessions/session.jsonl'
                        result = {'thread': thread} if method == 'thread/read' else {}
                        if method == 'thread/list':
                            result = {'data': [dict(thread, id='child', updatedAt=1000)] if condition == 'descendant' else [], 'nextCursor': None}
                        return {'id': self.request['id'], 'result': result}
                    def close(self):
                        pass
                probe = lambda paths: None if condition == 'unknown' else (set(paths) if condition == 'process' else set())
                result = codex_wire.archive_finished_thread('fixture-thread', before=100,
                    process_probe=probe, wire_factory=Wire)
                self.assertEqual(result['status'], 'archived' if condition == 'safe' else 'kept', result)
                if condition == 'safe':
                    self.assertIn('thread/archive', [c['method'] for c in calls])
                    request = next(c for c in calls if c['method'] == 'thread/list')
                    self.assertIn('subAgent', request['params']['sourceKinds'])
                else:
                    self.assertNotIn('thread/archive', [c['method'] for c in calls])

    @unittest.skipUnless(shutil.which('codex'), 'Codex CLI is required for the native archive fixture')
    def test_native_cli_archives_fixture_session_without_deleting_bytes(self):
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        import codex_wire
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'sessions/2020/01/01'
            folder.mkdir(parents=True)
            tid, stamp = str(uuid.uuid4()), '2020-01-01T00:00:00Z'
            path = folder / f'rollout-2020-01-01T00-00-00-{tid}.jsonl'
            rows = [{'timestamp': stamp, 'type': 'session_meta', 'payload': {'id': tid, 'timestamp': stamp,
                'cwd': str(root), 'originator': 'codex_exec', 'cli_version': '0.0.0', 'source': 'exec', 'model_provider': 'openai'}},
                {'timestamp': stamp, 'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                    'content': [{'type': 'input_text', 'text': 'Fixture cleanup proof'}]}}]
            content = ''.join(json.dumps(row) + '\n' for row in rows)
            path.write_text(content)
            os.utime(path, (1577836800, 1577836800))
            with patch.dict(os.environ, {'CODEX_HOME': str(root)}):
                result = codex_wire.archive_finished_thread(tid, before=time.time() - 172800,
                    process_probe=lambda paths: set(), timeout=20)
                repeated = codex_wire.archive_finished_thread(tid, before=time.time() - 172800,
                    process_probe=lambda paths: set(), timeout=20)
            self.assertEqual(result['status'], 'archived', result)
            self.assertEqual(repeated['status'], 'archived', repeated)
            self.assertFalse(path.exists())
            self.assertEqual(next((root / 'archived_sessions').glob('*.jsonl')).read_text(), content)


class CleanupHealthTests(unittest.TestCase):
    def test_receipt_reports_partial_temp_batch_and_resumes_backlog(self):
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        config['machine_cleanup']['test_temp_max_items'] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch = root / 'scratch'
            scratch.mkdir()
            for name in ('carr-ci-test-a', 'carr-ci-test-b'):
                (scratch / name).mkdir()
                os.utime(scratch / name, (0, 0))
            options = dict(hook=None, tmp_root=root, user_tmp_root=scratch, roots={},
                dispatch_path=root / 'absent', provider=object(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
            first = cleanup.run(root, config, **options)
            self.assertTrue(first['budget_exhausted'], first)
            second = cleanup.run(root, config, **options)
            self.assertEqual(len(first['removed']), 1)
            self.assertEqual(len(second['removed']), 1)
            self.assertNotEqual(first['removed'][0]['path'], second['removed'][0]['path'])

    def test_oversized_fixture_does_not_starve_later_small_fixture(self):
        import storage_hygiene
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            huge = root / 'carr-ci-test-a-large'
            huge.mkdir()
            for index in range(10001):
                (huge / str(index)).touch()
            small = root / 'carr-ci-test-b-small'
            small.mkdir()
            os.utime(small, (0, 0))
            report = storage_hygiene.cleanup_test_temps(root, now=time.time(), min_age=21600,
                max_items=100, cursor='', process_probe=lambda paths: set(),
                deadline=lambda: False, dry_run=False)
            self.assertTrue(huge.exists())
            self.assertFalse(small.exists())
            self.assertFalse(report['errors'], report)
            self.assertIn('budget', report['kept'][0]['reason'])

    def test_health_retains_partial_size_evidence_instead_of_discarding_it(self):
        import itertools
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'room-codex-fixture').mkdir()
            with patch('time.monotonic', side_effect=itertools.count()):
                rows = cleanup.health_findings(root, config, tmp_root=root,
                    user_tmp_root=root, roots={}, now=90000)
            sizes = [r for r in rows if r['code'].endswith('_bytes_partial')]
            self.assertEqual(len(sizes), 2, rows)
            for row in sizes:
                self.assertFalse(row['measurement']['complete'])
                self.assertEqual(row['measurement']['allocated_bytes'], 0)
                self.assertIn('at least 0', row['detail'])

    def test_large_tree_measurement_returns_bounded_honest_lower_bound(self):
        import storage_hygiene
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'payload').write_bytes(b'x' * 8192)
            outside = root.parent / ('measurement-outside-' + uuid.uuid4().hex)
            try:
                outside.write_bytes(b'x' * 16384)
                (root / 'link').symlink_to(outside)
                for index in range(30):
                    (root / str(index)).mkdir()
                measured = storage_hygiene.allocated_bytes_evidence([root], max_entries=5)
                self.assertFalse(measured['complete'])
                self.assertEqual(measured['entries'], 5)
                self.assertGreaterEqual(measured['allocated_bytes'], 0)
                self.assertIn('entry budget', measured['reason'])
                full = storage_hygiene.allocated_bytes_evidence([root])
                self.assertTrue(full['complete'])
                self.assertGreaterEqual(full['allocated_bytes'], 8192)
                self.assertLess(full['allocated_bytes'], 24576)
            finally:
                outside.unlink(missing_ok=True)

    def test_scheduled_cleanup_records_temp_effects_bytes_and_durable_receipt(self):
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch, clones = root / 'scratch', root / 'clones'
            scratch.mkdir()
            clones.mkdir()
            old = scratch / 'jev-ring.fixture'
            old.mkdir()
            (old / 'payload').write_bytes(b'x' * 8192)
            os.utime(old / 'payload', (0, 0))
            os.utime(old, (0, 0))
            system_temp = clones / 'carr-ci-test-old'
            system_temp.mkdir()
            os.utime(system_temp, (0, 0))
            report = cleanup.run(root, config, hook=None, tmp_root=clones, user_tmp_root=scratch,
                roots={}, dispatch_path=root / 'absent.jsonl', provider=object(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
            self.assertFalse(old.exists())
            self.assertFalse(system_temp.exists())
            self.assertEqual(report['status'], 'completed')
            self.assertGreaterEqual(report['temp_staged_allocated_bytes'], 8192)
            saved = json.loads((root / 'out/machine-cleanup.json').read_text())
            self.assertEqual(saved['removed'][0]['status'], 'staged')
            self.assertEqual(saved['temp_staged_allocated_bytes'], report['temp_staged_allocated_bytes'])


    def test_temp_and_clone_byte_thresholds_feed_red_watchdog_findings(self):
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        config['machine_cleanup'].update(user_temp_bytes_threshold=1, job_clone_bytes_threshold=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'room-codex-fixture').mkdir()
            (root / 'room-codex-fixture/payload').write_bytes(b'x' * 8192)
            rows = cleanup.health_findings(root, config, tmp_root=root, user_tmp_root=root, roots={}, now=90000)
            codes = {row['code'] for row in rows}
            self.assertIn('job_clone_bytes', codes)
            self.assertIn('user_temp_bytes', codes)


    def test_cleanup_health_reports_thresholds_and_stale_success_as_watchdog_findings(self):
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for index in range(31):
                (root / ('room-codex-' + str(index))).mkdir()
            report = root / 'out/machine-cleanup.json'
            report.parent.mkdir()
            report.write_text(json.dumps({'last_success_at': 100, 'errors': []}))
            rows = cleanup.health_findings(root, config, tmp_root=root, roots={}, now=90000)
            self.assertEqual({r['code'] for r in rows}, {'job_clone_count', 'cleanup_stale'})
            found = watchdog.detect({'machine_cleanup': rows}, config, 90000)
            self.assertEqual({f['kind'] for f in found}, {'machine_cleanup'})
            self.assertTrue(all(f['owner'] == 'orchestrator' for f in found))


class ScheduledCleanupTests(unittest.TestCase):
    def test_persistent_lock_defers_only_worktrees_without_claiming_success(self):
        import fcntl
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        config['machine_cleanup']['max_seconds'] = .3
        lock = fixture.repo / 'out/worktree-reap.lock'
        lock.parent.mkdir(exist_ok=True)
        with lock.open('a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start = time.monotonic()
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook,
                tmp_root=fixture.base, dispatch_path=fixture.base / 'absent',
                roots={'fixture/repo': fixture.repo}, provider=fixtures.FakeProvider(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(report['errors'], report)
        self.assertEqual(report['status'], 'deferred')
        self.assertTrue(report['deferred'])
        self.assertIsNone(report['last_success_at'])

    def test_fixture_run_receipts_and_dry_run_do_not_remove_work(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-job'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        ledger = fixture.base / 'dispatch.jsonl'
        ledger.write_text(json.dumps({'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00+00:00', 'thread_id': 'finished'}) + '\n')
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        provider = fixtures.FakeProvider()
        provider.branch_pulls = lambda repo, branch: []
        calls = []
        def archive(tid, **kwargs):
            calls.append((tid, kwargs['dry_run']))
            return {'kind': 'thread', 'thread_id': tid, 'status': 'would_archive' if kwargs['dry_run'] else 'archived'}
        for dry_run in (True, False):
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook, tmp_root=fixture.base,
                dispatch_path=ledger, roots={}, provider=provider, process_probe=lambda paths: set(),
                archive_thread=archive, dry_run=dry_run)
            self.assertFalse(report['errors'], report)
            self.assertEqual(workspace.exists(), dry_run)
            self.assertEqual(len(report['removed']), 0 if dry_run else 2)
            receipt = json.loads((fixture.repo / ('out/machine-cleanup-dry.json' if dry_run else 'out/machine-cleanup.json')).read_text())
            self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(calls, [('finished', True), ('finished', False)])

    def test_interrupted_clone_move_is_read_back_before_another_effect(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-interrupted'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        ledger = fixture.base / 'dispatch.jsonl'
        ledger.write_text(json.dumps({'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00+00:00'}) + '\n')
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        provider = fixtures.FakeProvider()
        real_rename = os.rename
        def interrupt(*args, **kwargs):
            real_rename(*args, **kwargs)
            raise RuntimeError('fixture interrupted after rename')
        options = dict(hook=fixtures.hook, tmp_root=fixture.base, dispatch_path=ledger, roots={},
                       provider=provider, process_probe=lambda paths: set())
        with patch.object(cleanup.os, 'rename', side_effect=interrupt):
            first = cleanup.run(fixture.repo, config, **options)
        self.assertEqual(first['status'], 'failed')
        second = cleanup.run(fixture.repo, config, **options)
        self.assertFalse(second['errors'], second)
        self.assertEqual(len(second['removed']), 1, second)
        self.assertIn('read back', second['removed'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
