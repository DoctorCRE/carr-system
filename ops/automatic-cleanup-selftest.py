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
    def test_slow_recovery_ledger_stops_without_effects(self):
        import branch_retirement as retirement
        fixture = fixtures.Fixture()
        ledger = fixture.repo / 'out/orch/branch-janitor-actions.jsonl'
        ledger.parent.mkdir(parents=True)
        event = {'candidate': {'repo': 'fixture/repo', 'kind': 'worktree',
            'path': 'untouched', 'head': 'abc'}, 'status': 'verified_no_effect'}
        ledger.write_text((json.dumps(event) + '\n') * 5)
        before = ledger.read_bytes()
        elapsed = [0.0]
        loads = json.loads
        def slow_loads(value, *args, **kwargs):
            row = loads(value, *args, **kwargs)
            if isinstance(row, dict) and 'candidate' in row:
                elapsed[0] += 60
            return row
        with patch.object(retirement.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(retirement.json, 'loads', side_effect=slow_loads):
            report = fixture.reaper().run({'fixture/repo': fixture.repo}, execute=True,
                local_only=True, max_seconds=240)
        self.assertLessEqual(elapsed[0], 240, report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertFalse(report['actions'], report)
        self.assertEqual(ledger.read_bytes(), before)

    def test_slow_untracked_content_stops_before_next_read(self):
        import branch_retirement as retirement
        from lib.cleanup_budget import BudgetExhausted
        fixture = fixtures.Fixture()
        tree = fixture.tree('slow-content')
        payload = tree / 'orphan'
        payload.write_bytes(b'x' * 65536 * 5)
        before = payload.read_bytes()
        elapsed = [0.0]
        real_open = Path.open
        class SlowRead:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def read(self, size):
                elapsed[0] += 60
                return self.stream.read(size)
        def open_file(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            return SlowRead(stream) if path == payload and args == ('rb',) else stream
        reaper = fixture.reaper()
        reaper.deadline = lambda: elapsed[0] >= 240
        with patch.object(Path, 'open', open_file):
            with self.assertRaises(BudgetExhausted):
                reaper.work_content(tree, deadline=reaper.deadline)
        self.assertLessEqual(elapsed[0], 240)
        self.assertEqual(payload.read_bytes(), before)

    def test_expiry_at_final_worktree_probe_performs_no_move(self):
        import branch_retirement as retirement
        fixture = fixtures.Fixture()
        tree = fixture.tree('before-move')
        elapsed, reads = [0.0], [0]
        real_age = fixtures.hook.index_age_s
        def final_read(path):
            age = real_age(path)
            if path == str(tree):
                reads[0] += 1
                if reads[0] == 3:
                    elapsed[0] = 240
            return age
        with patch.object(retirement.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(fixtures.hook, 'index_age_s', side_effect=final_read):
            report = fixture.reaper().run({'fixture/repo': fixture.repo},
                execute=True, local_only=True, max_seconds=240)
        self.assertTrue(tree.exists(), report)
        self.assertEqual((tree / 'file').read_text(), 'initial\n')
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertFalse(report['actions'], report)
        ledger = fixture.repo / 'out/orch/branch-janitor-actions.jsonl'
        self.assertEqual(json.loads(ledger.read_text().splitlines()[-1])['status'], 'verified_no_effect')

    def test_slow_nested_tree_reads_stop_at_deadline(self):
        import branch_retirement as retirement
        fixture = fixtures.Fixture()
        tree = fixture.tree('nested')
        for index in range(5):
            (tree / f'directory-{index}').mkdir()
        elapsed = [0.0]
        real_scandir = os.scandir
        def scan(path):
            if not isinstance(path, int) and Path(path).is_relative_to(tree):
                elapsed[0] += 60
            return real_scandir(path)
        with patch.object(retirement.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(os, 'scandir', side_effect=scan):
            report = fixture.reaper().run({'fixture/repo': fixture.repo}, execute=True,
                local_only=True, max_seconds=240)
        self.assertLessEqual(elapsed[0], 240, report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertFalse(report['actions'], report)
        self.assertEqual((tree / 'file').read_text(), 'initial\n')

    def test_slow_worktree_discovery_stops_before_another_row(self):
        import branch_retirement as retirement
        fixture = fixtures.Fixture()
        trees = [fixture.tree(f'slow-{index}') for index in range(4)]
        elapsed = [0.0]
        real_entries = retirement.iter_worktrees
        def slow_entries(root):
            for entry in real_entries(root):
                elapsed[0] += 60
                yield entry
        with patch.object(retirement.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(retirement, 'iter_worktrees', side_effect=slow_entries):
            report = fixture.reaper().run({'fixture/repo': fixture.repo}, execute=True,
                local_only=True, max_seconds=240)
        self.assertLessEqual(elapsed[0], 240, report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertFalse(report['actions'], report)
        for tree in trees:
            self.assertEqual((tree / 'file').read_text(), 'initial\n')

    def test_identity_readback_does_not_resolve_other_worktree_paths(self):
        fixture = fixtures.Fixture()
        tree = fixture.tree('target')
        reaper = fixture.reaper()
        entries = [{'path': str(fixture.base / f'unrelated-{index}')} for index in range(400)]
        entries.append({'path': str(tree), 'head': 'a' * 40, 'branch': 'target'})
        with patch.object(fixtures.hook, 'worktree_entries', return_value=entries), \
             patch.object(Path, 'resolve', autospec=True, side_effect=Path.resolve) as resolve:
            identity = reaper.tree_identity(fixture.repo, tree)
        self.assertEqual(identity, {'head': 'a' * 40, 'branch': 'target', 'detached': False})
        self.assertLessEqual(resolve.call_count, 1, 'unrelated paths must not spend the staging budget')

    def test_local_keep_guards_do_not_spend_the_remote_pr_budget(self):
        for condition in ('dirty', 'unpushed'):
            with self.subTest(condition=condition):
                fixture = fixtures.Fixture()
                tree = fixture.tree('guarded')
                if condition == 'dirty':
                    (tree / 'file').write_text('keep these bytes')
                else:
                    fixture.commit('local only', cwd=tree)
                fixture.age(tree)
                provider = fixtures.FakeProvider()
                with patch.object(provider, 'branch_pulls', wraps=provider.branch_pulls) as queries:
                    report = fixture.reaper(provider=provider).run({'fixture/repo': fixture.repo},
                        execute=True, local_only=True)
                self.assertFalse(report['errors'], report)
                self.assertTrue(tree.exists())
                self.assertFalse(report['actions'], report)
                queries.assert_not_called()
    def test_prunable_worktree_with_surviving_directory_keeps_contents_and_scan_continues(self):
        fixture = fixtures.Fixture()
        broken = fixture.tree('aaa-broken')
        healthy = fixture.tree('zzz-healthy')
        (broken / '.git').unlink()
        (broken / 'recovery-notes').write_text('keep this work')
        report = fixture.reaper().run({'fixture/repo': fixture.repo}, local_only=True)
        self.assertFalse(report['errors'], report)
        row = next(r for r in report['rows'] if r.get('path') == str(broken))
        self.assertIsNone(row['action'])
        self.assertIn('prunable', row['reason'])
        self.assertEqual((broken / 'recovery-notes').read_text(), 'keep this work')
        good = next(r for r in report['rows'] if r.get('path') == str(healthy))
        self.assertEqual(good['action'], 'stage_worktree')
        self.assertTrue(healthy.exists())
        self.assertEqual(len(list(__import__('branch_retirement').iter_worktrees(fixture.repo))), 3)
        self.assertFalse((fixture.base / '_to_delete').exists())

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
    def test_remote_archive_preservation_keeps_other_clone_guards(self):
        import machine_cleanup as cleanup
        for guard in ('none', 'dirty', 'open-pr', 'unfinished', 'deleted-tag'):
            with self.subTest(guard=guard):
                fixture = fixtures.Fixture()
                workspace = fixture.base / 'room-codex-archived'
                workspace.mkdir()
                clone = workspace / 'checkout'
                fixture.git('clone', str(fixture.remote), str(clone))
                fixture.git('switch', '-c', 'job', 'origin/main', cwd=clone)
                fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
                fixture.git('config', 'user.name', 'Fixture', cwd=clone)
                head = fixture.commit('archived clone payload', cwd=clone)
                tag = 'archive/worktree/job-2026-10-19'
                fixture.git('tag', '-a', tag, head, '-m', 'fixture archive', cwd=clone)
                fixture.git('push', 'origin', 'refs/tags/' + tag, cwd=clone)
                fixture.git('update-ref', '-d', 'refs/tags/' + tag, cwd=clone)
                provider = fixtures.FakeProvider()
                if guard == 'dirty':
                    (clone / 'file').write_text('keep uncommitted clone payload')
                elif guard == 'open-pr':
                    opened = fixtures.pull(1, head, fixture.git('rev-parse', 'main'))
                    opened['head']['ref'] = 'job'
                    provider.rows.append(opened)
                elif guard == 'deleted-tag':
                    def remove_tag(*args):
                        fixture.git('--git-dir=' + str(fixture.remote), 'update-ref', '-d', 'refs/tags/' + tag)
                        return []
                    provider.open_pulls = remove_tag
                dispatch = {'status': 'running' if guard == 'unfinished' else 'completed',
                    'checkout_path': str(clone), 'checkout_workspace': str(workspace),
                    'dispatched_at': '2020-01-01T00:00:00Z'}
                row = cleanup.clean_clone(workspace, dispatch, provider=provider,
                    process_probe=lambda paths: set(), now=time.time() + 10 * 86400,
                    older_than=7 * 86400, dry_run=False)
                self.assertNotIn('error', row, row)
                self.assertEqual(row['status'], 'staged' if guard == 'none' else 'kept', row)
                if guard == 'none':
                    self.assertFalse(workspace.exists())
                    self.assertIn('_to_delete', Path(row['staged_path']).parts)
                    self.assertEqual((Path(row['staged_path']) / 'checkout/file').read_text(), 'archived clone payload\n')
                else:
                    self.assertTrue(clone.exists())
                    expected = {'dirty': 'uncommitted', 'open-pr': 'open PR',
                        'unfinished': 'finished dispatch', 'deleted-tag': 'changed before staging'}[guard]
                    self.assertIn(expected, row['reason'])

    def test_clone_outside_authorized_repository_identity_is_kept(self):
        import machine_cleanup as cleanup
        from branch_retirement import GitHub
        fixture = fixtures.Fixture()
        workspace = fixture.base / 'room-codex-other-repository'
        workspace.mkdir()
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        result = cleanup.clean_clone(workspace, {'status': 'completed',
            'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'dispatched_at': '2020-01-01T00:00:00+00:00'}, provider=GitHub(),
            process_probe=lambda paths: set(), now=time.time(), older_than=1, dry_run=False)
        self.assertEqual(result['status'], 'kept', result)
        self.assertNotIn('error', result)
        self.assertIn('identity', result['reason'])
        self.assertTrue(clone.exists())
        self.assertFalse((workspace.parent / '_to_delete').exists())

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
    def test_unloaded_historical_thread_is_kept_without_an_archive_attempt(self):
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        import codex_wire
        for code, message, failed in ((-32600, 'thread not loaded: historical', False),
                (-32600, 'permission denied', True), (-32000, 'thread not loaded: historical', True)):
            with self.subTest(code=code, message=message):
                calls = []
                class Wire:
                    def __init__(self, timeout):
                        pass
                    def upgrade(self):
                        pass
                    def send_json(self, request):
                        calls.append(request)
                        self.request = request
                    def receive_json(self):
                        result = {'id': self.request['id'], 'result': {}}
                        if self.request['method'] == 'thread/read':
                            result = {'id': self.request['id'], 'error': {'code': code, 'message': message}}
                        return result
                    def close(self):
                        pass
                result = codex_wire.archive_finished_thread('historical', before=100,
                    process_probe=lambda paths: set(), wire_factory=Wire)
                self.assertEqual(result['status'], 'kept')
                self.assertEqual('error' in result, failed, result)
                self.assertNotIn('thread/archive', [c['method'] for c in calls])
                if not failed:
                    self.assertIn('not loaded', result['reason'])

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
                expected_kib = int(subprocess.check_output(
                    ['/usr/bin/du', '-sk', str(root)], text=True, timeout=3).split()[0])
                self.assertEqual((full['allocated_bytes'] + 1023) // 1024, expected_kib)
                self.assertEqual(full['entries'], 33)
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
    def test_clean_run_after_thread_recovery_exhaustion_advances_last_success(self):
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            pending = {'kind': 'thread', 'thread_id': 'stuck', 'before': 100,
                       'readback_attempts': 2}
            receipt.write_text(json.dumps({'at': 1, 'last_success_at': 123, 'pending': pending}))
            def archive(tid, **kwargs):
                self.assertTrue(kwargs['dry_run'])
                return {'kind': 'thread', 'thread_id': tid, 'status': 'deferred',
                        'budget_exhausted': True}
            options = dict(hook=None, tmp_root=root, roots={}, dispatch_path=root / 'absent',
                provider=object(), process_probe=lambda paths: set(), archive_thread=archive)
            exhausted = cleanup.run(root, config, **options)
            self.assertEqual(exhausted['status'], 'failed')
            self.assertEqual(exhausted['errors'][0]['error'], 'thread_recovery_exhausted')
            report = cleanup.run(root, config, **options)
            self.assertEqual(report['status'], 'completed', report)
            self.assertGreater(report['last_success_at'], 123)
            self.assertEqual(report['errors'], [])
            self.assertEqual(report['stuck_thread_count'], 1)
            self.assertIn('stuck', report['stuck_threads'])
            self.assertEqual(json.loads(receipt.read_text()), report)
            findings = cleanup.health_findings(root, config, tmp_root=root, roots={}, now=time.time())
            self.assertEqual([row['code'] for row in findings], ['cleanup_stuck_threads'])
            self.assertIn('1', findings[0]['detail'])

    def test_stuck_thread_later_resolved_is_removed_from_map(self):
        import machine_cleanup as cleanup
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        for status in ('archived', 'verified_no_effect', 'would_archive'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                receipt = root / 'out/machine-cleanup.json'
                receipt.parent.mkdir()
                stuck = {'kind': 'thread', 'thread_id': 'stuck', 'before': 100,
                         'readback_attempts': 3, 'error': 'thread_recovery_exhausted'}
                receipt.write_text(json.dumps({'at': 1, 'last_success_at': 123,
                    'stuck_threads': {'stuck': stuck}, 'errors': [stuck]}))
                dispatch = root / 'dispatch.jsonl'
                dispatch.write_text((json.dumps({'thread_id': 'stuck'}) + '\n') * 2)
                calls = []
                resolution = [False]
                def archive(tid, **kwargs):
                    calls.append((tid, kwargs['dry_run']))
                    self.assertTrue(kwargs['dry_run'], 'stuck effects must only be read back')
                    self.assertEqual(kwargs['before'], 100)
                    if not resolution[0]:
                        raise TimeoutError('still unresolved')
                    return {'kind': 'thread', 'thread_id': tid, 'status': status}
                options = dict(hook=None, tmp_root=root, roots={}, dispatch_path=dispatch,
                    provider=object(), process_probe=lambda paths: set(), archive_thread=archive)
                retained = cleanup.run(root, config, **options)
                self.assertEqual(retained['status'], 'completed', retained)
                self.assertIn('stuck', retained['stuck_threads'])
                resolution[0] = True
                report = cleanup.run(root, config, **options)
                self.assertNotIn('stuck', report['stuck_threads'], report)
                self.assertEqual(report['stuck_thread_count'], 0)
                self.assertEqual(report['status'], 'completed')
                self.assertEqual(report['errors'], [])
                self.assertEqual(calls, [('stuck', True)] * 2)
                self.assertEqual(json.loads(receipt.read_text()), report)
                findings = cleanup.health_findings(root, config, tmp_root=root, roots={}, now=time.time())
                self.assertEqual(findings, [])

    def test_stuck_thread_recovery_does_not_starve_other_cleanup_kinds(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        self.addCleanup(shutil.rmtree, fixture.base)
        tree = fixture.tree('old')
        clones = fixture.base / 'clones'
        workspace = clones / 'room-codex-finished'
        workspace.mkdir(parents=True)
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        dispatch = fixture.base / 'dispatch.jsonl'
        dispatch.write_text(''.join(json.dumps(row) + '\n' for row in (
            {'thread_id': 'stuck', 'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00Z'},
            {'checkout_workspace': str(workspace), 'checkout_path': str(clone),
             'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00Z'},
            {'thread_id': 'healthy', 'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00Z'})))
        receipt = fixture.repo / 'out/machine-cleanup.json'
        receipt.parent.mkdir(exist_ok=True)
        pending = {'kind': 'thread', 'thread_id': 'stuck', 'before': 100}
        receipt.write_text(json.dumps({'at': 1, 'status': 'deferred', 'last_success_at': 123,
                                      'pending': pending, 'errors': []}))
        calls = []
        def archive(tid, **kwargs):
            calls.append((tid, kwargs['dry_run']))
            if tid == 'stuck':
                self.assertTrue(kwargs['dry_run'], 'unresolved effects must only be read back')
                self.assertEqual(kwargs['before'], 100)
                return {'kind': 'thread', 'thread_id': tid, 'status': 'deferred',
                        'budget_exhausted': True, 'reason': 'fixture archive readback timed out'}
            return {'kind': 'thread', 'thread_id': tid, 'status': 'archived'}
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        options = dict(hook=fixtures.hook, tmp_root=clones, dispatch_path=dispatch,
            roots={'fixture/repo': fixture.repo}, provider=fixtures.FakeProvider(),
            process_probe=lambda paths: set(), archive_thread=archive)
        for _ in range(3):
            report = cleanup.run(fixture.repo, config, **options)
        self.assertFalse(workspace.exists(), 'a stuck thread must not starve clone cleanup')
        self.assertFalse(tree.exists(), 'a stuck thread must not starve worktree cleanup')
        self.assertIn(('healthy', False), calls)
        self.assertEqual(report['status'], 'failed', report)
        self.assertFalse(report['budget_exhausted'], report)
        self.assertEqual(report['last_success_at'], 123)
        self.assertNotIn('pending', report)
        error = next(row for row in report['errors'] if row.get('thread_id') == 'stuck')
        self.assertEqual(error['error'], 'thread_recovery_exhausted')
        self.assertEqual(error['before'], 100)
        again = cleanup.run(fixture.repo, config, **options)
        self.assertEqual(again['errors'], [])
        self.assertEqual(again['status'], 'completed')
        self.assertEqual(again['stuck_threads']['stuck'], error)
        self.assertEqual(calls.count(('stuck', True)), 4)
        self.assertNotIn(('stuck', False), calls)
        self.assertEqual(json.loads(receipt.read_text()), again)
        findings = cleanup.health_findings(fixture.repo, config, tmp_root=clones, roots={}, now=time.time())
        self.assertIn('cleanup_stuck_threads', [row['code'] for row in findings])
        self.assertNotIn('cleanup_failed', [row['code'] for row in findings])

    def test_stuck_thread_readback_exceptions_remain_bounded_when_run_budget_expires(self):
        import machine_cleanup as cleanup
        for failure in (TimeoutError('fixture timeout'), subprocess.TimeoutExpired('fixture readback', 30)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                workspace = root / 'clones/room-codex-kept'
                workspace.mkdir(parents=True)
                (workspace / 'payload').write_text('unrecognized checkout stays intact')
                dispatch = root / 'dispatch.jsonl'
                dispatch.write_text(json.dumps({'thread_id': 'stuck'}) + '\n')
                pending = {'kind': 'thread', 'thread_id': 'stuck', 'before': 100,
                           'thread_offset': dispatch.stat().st_size}
                receipt = root / 'out/machine-cleanup.json'
                receipt.parent.mkdir()
                receipt.write_text(json.dumps({'at': 1, 'last_success_at': 123, 'pending': pending}))
                elapsed, calls = [0.0], []
                def archive(tid, **kwargs):
                    calls.append((tid, kwargs['dry_run']))
                    elapsed[0] += 240
                    raise failure
                config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                options = dict(hook=None, tmp_root=workspace.parent, roots={}, dispatch_path=dispatch,
                    provider=object(), process_probe=lambda paths: set(), archive_thread=archive)
                with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]):
                    for _ in range(3):
                        report = cleanup.run(root, config, **options)
                    self.assertTrue(report['budget_exhausted'])
                    self.assertNotIn('pending', report)
                    resumed = cleanup.run(root, config, **options)
                self.assertEqual(calls, [('stuck', True)] * 4)
                self.assertEqual(resumed['status'], 'deferred', resumed)
                self.assertTrue(resumed['budget_exhausted'])
                self.assertEqual([row['path'] for row in resumed['kept']], [str(workspace)])
                self.assertEqual((workspace / 'payload').read_text(), 'unrecognized checkout stays intact')
                self.assertEqual(resumed['errors'], [])
                error = resumed['stuck_threads']['stuck']
                self.assertEqual(error['readback_attempts'], 3)
                self.assertEqual(error['thread_offset'], pending['thread_offset'])
                self.assertEqual(error['before'], 100)
                self.assertEqual(resumed['last_success_at'], 123)

    def test_worktree_slice_timeout_defers_scheduler_before_overall_deadline(self):
        import branch_retirement as retirement
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        self.addCleanup(shutil.rmtree, fixture.base)
        trees = [fixture.tree(f'slow-{index}') for index in range(4)]
        elapsed = [0.0]
        real_entries = retirement.iter_worktrees
        def slow_entries(root):
            for entry in real_entries(root):
                elapsed[0] += 40
                yield entry
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(retirement, 'iter_worktrees', side_effect=slow_entries):
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook, tmp_root=fixture.base,
                dispatch_path=fixture.base / 'absent', roots={'fixture/repo': fixture.repo},
                provider=fixtures.FakeProvider(), process_probe=lambda paths: set(),
                archive_thread=lambda *a, **k: self.fail('no thread fixture'))
        self.assertLess(elapsed[0], config['machine_cleanup']['max_seconds'])
        self.assertEqual(report['status'], 'deferred', report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertFalse(report['removed'], report)
        self.assertIsNone(report['last_success_at'])
        self.assertTrue(any(row['kind'] == 'sweep' for row in report['deferred']))
        for tree in trees:
            self.assertEqual((tree / 'file').read_text(), 'initial\n')

    def test_thread_item_batches_complete_and_resume_without_duplicate_archives(self):
        import machine_cleanup as cleanup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            clones = root / 'clones'
            clones.mkdir()
            dispatch = root / 'dispatch.jsonl'
            dispatch.write_text(''.join(json.dumps({'thread_id': thread, 'status': 'completed',
                'dispatched_at': '2020-01-01T00:00:00Z'}) + '\n' for thread in ('first', 'second')))
            config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
            config['machine_cleanup']['max_items'] = 1
            archived, offsets = [], []
            def archive(thread, **kwargs):
                archived.append(thread)
                return {'kind': 'thread', 'thread_id': thread, 'status': 'archived'}
            with patch.object(cleanup.time, 'monotonic', return_value=0):
                for thread in ('first', 'second'):
                    report = cleanup.run(root, config, hook=None, tmp_root=clones, roots={},
                        dispatch_path=dispatch, provider=object(), process_probe=lambda paths: set(),
                        archive_thread=archive)
                    self.assertEqual(report['status'], 'completed', report)
                    self.assertFalse(report['deferred'], report)
                    self.assertFalse(report['budget_exhausted'], report)
                    self.assertEqual([row['thread_id'] for row in report['removed']], [thread])
                    offsets.append(json.loads((root / 'out/machine-cleanup-cursor.json').read_text())['thread_offset'])
            self.assertGreater(offsets[0], 0)
            self.assertGreater(offsets[1], offsets[0])
            self.assertEqual(archived, ['first', 'second'])

    def test_temp_budget_resumes_unfinished_fixture_before_or_after_move(self):
        import machine_cleanup as cleanup
        for moved in (False, True):
            with self.subTest(moved=moved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                scratch, clones = root / 'scratch', root / 'clones'
                scratch.mkdir()
                clones.mkdir()
                candidate = scratch / 'carr-ci-test-budget'
                candidate.mkdir()
                (candidate / 'payload').write_text('preserve complete fixture')
                for path in (candidate / 'payload', candidate):
                    os.utime(path, (0, 0))
                elapsed, calls = [0.0], []
                def probe(paths):
                    calls.append(paths)
                    if not moved and len(calls) == 2:
                        elapsed[0] = 240
                    return set()
                lstat = Path.lstat
                def slow_stat(path, *args, **kwargs):
                    result = lstat(path, *args, **kwargs)
                    if moved and path.name == 'fixture' and '_to_delete' in path.parts:
                        elapsed[0] = 240
                    return result
                config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                options = dict(hook=None, tmp_root=clones, user_tmp_root=scratch, roots={},
                    dispatch_path=root / 'absent', provider=object(), archive_thread=lambda *a, **k: None)
                with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                     patch.object(Path, 'lstat', slow_stat):
                    first = cleanup.run(root, config, **options, process_probe=probe)
                self.assertEqual(first['status'], 'deferred', first)
                self.assertFalse(first['errors'], first)
                self.assertIsNone(first['last_success_at'])
                self.assertEqual(candidate.exists(), not moved)
                second = cleanup.run(root, config, **options, process_probe=lambda paths: set())
                self.assertEqual(second['status'], 'completed', second)
                self.assertFalse(second['errors'], second)
                self.assertNotIn('pending', second)
                self.assertFalse(candidate.exists())
                self.assertEqual(len(second['removed']), 1, second)
                self.assertEqual((Path(second['removed'][0]['staged_path']) / 'payload').read_text(),
                    'preserve complete fixture')

    def test_deferred_temp_vanishes_before_resume(self):
        import machine_cleanup as cleanup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            scratch, clones = root / 'scratch', root / 'clones'
            scratch.mkdir()
            clones.mkdir()
            candidate = scratch / 'carr-ci-test-budget'
            candidate.mkdir()
            (candidate / 'payload').write_text('owned fixture bytes')
            for path in (candidate / 'payload', candidate):
                os.utime(path, (0, 0))
            receipt = root / 'out/machine-cleanup.json'
            receipt.parent.mkdir()
            receipt.write_text(json.dumps({'last_success_at': 123}))
            elapsed, calls = [0.0], []
            def probe(paths):
                calls.append(paths)
                if len(calls) == 2:
                    elapsed[0] = 240
                return set()
            config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
            options = dict(hook=None, tmp_root=clones, user_tmp_root=scratch, roots={},
                dispatch_path=root / 'absent', provider=object(), archive_thread=lambda *a, **k: None)
            with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]):
                first = cleanup.run(root, config, **options, process_probe=probe)
            self.assertEqual(first['status'], 'deferred', first)
            self.assertEqual(first['last_success_at'], 123)
            self.assertEqual(first['errors'], [])
            self.assertIn('pending', first)
            staged = Path(first['pending']['staged_path'])
            (candidate / 'payload').unlink()
            candidate.rmdir()
            self.assertFalse(staged.exists())
            resumed = cleanup.run(root, config, **options, process_probe=lambda paths: set())
            self.assertEqual(resumed['status'], 'completed', resumed)
            self.assertEqual(resumed['errors'], [])
            self.assertEqual([(r['path'], r['status']) for r in resumed['removed']],
                [(str(candidate), 'removed')])
            self.assertNotIn('pending', resumed)
            self.assertEqual(resumed['temp_freed_bytes'], 0)
            self.assertEqual(resumed['temp_staged_allocated_bytes'], 0)
            self.assertEqual(resumed['removed'][0]['allocated_bytes'], 0)
            self.assertEqual(json.loads(receipt.read_text()), resumed)
            again = cleanup.run(root, config, **options, process_probe=lambda paths: set())
            self.assertEqual(again['status'], 'completed', again)
            self.assertEqual(again['errors'], [])
            self.assertEqual(again['removed'], [])
            self.assertNotIn('pending', again)

    def test_thread_timeout_defers_and_reads_back_before_retry(self):
        import machine_cleanup as cleanup
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        import codex_wire
        for timeout_at in ('initialize', 'thread/archive'):
            with self.subTest(timeout_at=timeout_at), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                clones = root / 'clones'
                clones.mkdir()
                ledger = root / 'dispatch.jsonl'
                ledger.write_text(json.dumps({'thread_id': 'finished', 'status': 'completed',
                    'dispatched_at': '2020-01-01T00:00:00Z'}) + '\n')
                elapsed, calls, timed_out = [0.0], [], [False]
                thread = {'id': 'finished', 'updatedAt': 10, 'status': {'type': 'idle'},
                          'path': '/fixture/session.jsonl'}
                class Wire:
                    def __init__(self, timeout):
                        pass
                    def upgrade(self):
                        pass
                    def close(self):
                        pass
                    def send_json(self, request):
                        self.request = request
                        calls.append(request['method'])
                        if request['method'] == 'thread/archive':
                            thread['path'] = '/fixture/archived_sessions/session.jsonl'
                    def receive_json(self):
                        method = self.request['method']
                        if method == timeout_at and not timed_out[0]:
                            timed_out[0] = True
                            elapsed[0] = 240
                            raise TimeoutError('fixture wire deadline')
                        result = {'thread': thread} if method == 'thread/read' else {}
                        if method == 'thread/list':
                            result = {'data': [], 'nextCursor': None}
                        return {'id': self.request['id'], 'result': result}
                def archive(*args, **kwargs):
                    return codex_wire.archive_finished_thread(*args, **kwargs, wire_factory=Wire)
                config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                options = dict(hook=None, tmp_root=clones, roots={}, dispatch_path=ledger,
                    provider=object(), process_probe=lambda paths: set(), archive_thread=archive)
                with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]):
                    first = cleanup.run(root, config, **options)
                self.assertEqual(first['status'], 'deferred', first)
                self.assertFalse(first['errors'])
                self.assertEqual(first['pending']['thread_id'], 'finished')
                self.assertIsNone(first['last_success_at'])
                second = cleanup.run(root, config, **options)
                self.assertEqual(second['status'], 'completed', second)
                self.assertFalse(second['errors'])
                self.assertNotIn('pending', second)
                self.assertEqual(len(second['removed']), 1, second)
                self.assertEqual(calls.count('thread/archive'), 1, calls)
                self.assertEqual(json.loads((root / 'out/machine-cleanup-runs.jsonl').read_text().splitlines()[0])['status'],
                    'deferred')

    def test_clone_budget_defers_remainder_and_resumes_from_saved_cursor(self):
        import machine_cleanup as cleanup
        from lib.cleanup_budget import BudgetExhausted
        for failure in (BudgetExhausted('fixture deadline'), subprocess.TimeoutExpired('git fetch', 1)):
            with self.subTest(failure=type(failure).__name__):
                fixture = fixtures.Fixture()
                clones = fixture.base / 'clones'
                clones.mkdir()
                workspaces = [clones / ('room-codex-' + name) for name in ('a', 'b', 'c')]
                dispatches = []
                for workspace in workspaces:
                    workspace.mkdir()
                    clone = workspace / 'checkout'
                    fixture.git('clone', str(fixture.remote), str(clone))
                    fixture.git('switch', 'main', cwd=clone)
                    dispatches.append({'checkout_workspace': str(workspace), 'checkout_path': str(clone),
                        'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00Z'})
                ledger = fixture.base / 'dispatch.jsonl'
                ledger.write_text(''.join(json.dumps(row) + '\n' for row in dispatches))
                config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                options = dict(hook=fixtures.hook, tmp_root=clones, dispatch_path=ledger,
                    roots={}, provider=fixtures.FakeProvider(), process_probe=lambda paths: set(),
                    archive_thread=lambda *a, **k: self.fail('no thread'))
                elapsed = [0.0]
                fetch = cleanup.fetch_remote_refs
                def slow_fetch(path):
                    if Path(path).parent == workspaces[1]:
                        elapsed[0] = 240
                        raise failure
                    return fetch(path)
                with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                     patch.object(cleanup, 'fetch_remote_refs', side_effect=slow_fetch):
                    first = cleanup.run(fixture.repo, config, **options)
                self.assertEqual(first['status'], 'deferred', first)
                self.assertTrue(first['deferred'], first)
                self.assertTrue(first['budget_exhausted'])
                self.assertEqual(first['errors'], [])
                self.assertIsNone(first['last_success_at'])
                cursor = json.loads((fixture.repo / 'out/machine-cleanup-cursor.json').read_text())
                self.assertEqual(cursor['clone'], workspaces[0].name)
                self.assertEqual([path.exists() for path in workspaces], [False, True, True])
                second = cleanup.run(fixture.repo, config, **options)
                self.assertEqual(second['status'], 'completed', second)
                self.assertEqual(second['errors'], [])
                self.assertNotIn('pending', second)
                self.assertEqual([row['path'] for row in second['removed']],
                    [str(path) for path in workspaces[1:]])
                self.assertFalse(any(path.exists() for path in workspaces))

    def test_vanished_temp_is_removed_and_run_completes(self):
        import machine_cleanup as cleanup
        for stage in ('listed', 'before_move'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                scratch, clones = root / 'scratch', root / 'clones'
                scratch.mkdir()
                clones.mkdir()
                candidate = scratch / 'carr-ci-test-vanished'
                candidate.mkdir()
                os.utime(candidate, (0, 0))
                calls = []
                def probe(paths):
                    calls.append(paths)
                    if len(calls) == (1 if stage == 'listed' else 2):
                        candidate.rmdir()
                    return set()
                config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                report = cleanup.run(root, config, hook=None, tmp_root=clones,
                    user_tmp_root=scratch, roots={}, dispatch_path=root / 'absent',
                    provider=object(), process_probe=probe, archive_thread=lambda *a, **k: None)
                self.assertEqual(report['status'], 'completed', report)
                self.assertEqual(report['errors'], [])
                self.assertEqual([(row['path'], row['status']) for row in report['removed']],
                    [(str(candidate), 'removed')])
                self.assertNotIn('pending', report)
                self.assertEqual(report['temp_freed_bytes'], 0)
                self.assertEqual(json.loads((root / 'out/machine-cleanup.json').read_text()), report)

    def test_slow_test_temp_discovery_uses_shared_run_deadline(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        scratch, clones = fixture.base / 'scratch', fixture.base / 'clones'
        scratch.mkdir()
        clones.mkdir()
        for index in range(5):
            path = scratch / f'carr-ci-test-{index}'
            path.mkdir()
            (path / 'payload').write_text('whole fixture')
        elapsed = [0.0]
        real_scandir = os.scandir
        identity = scratch.stat().st_ino
        class SlowEntries:
            def __init__(self, path):
                self.stream = real_scandir(path)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
            def close(self):
                self.stream.close()
            def __iter__(self):
                return self
            def __next__(self):
                entry = next(self.stream)
                elapsed[0] += 60
                return entry
        def scan(path):
            slow = os.fstat(path).st_ino == identity if isinstance(path, int) else Path(path) == scratch
            return SlowEntries(path) if slow else real_scandir(path)
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(os, 'scandir', side_effect=scan):
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook, tmp_root=clones,
                user_tmp_root=scratch, dispatch_path=fixture.base / 'absent.jsonl', roots={},
                provider=fixtures.FakeProvider(), process_probe=lambda paths: set(),
                archive_thread=lambda *a, **k: self.fail('no thread'))
        self.assertLessEqual(elapsed[0], 240, report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertNotIn('pending', report)
        for path in scratch.glob('carr-ci-test-*'):
            self.assertEqual((path / 'payload').read_text(), 'whole fixture')
        self.assertEqual(len(list(scratch.glob('carr-ci-test-*'))), 5)

    def test_clone_expiry_before_move_keeps_complete_workspace(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        clones = fixture.base / 'clones'
        workspace = clones / 'room-codex-expiry'
        workspace.mkdir(parents=True)
        clone = workspace / 'checkout'
        fixture.git('clone', str(fixture.remote), str(clone))
        fixture.git('switch', 'main', cwd=clone)
        ledger = fixture.base / 'dispatch.jsonl'
        ledger.write_text(json.dumps({'checkout_path': str(clone), 'checkout_workspace': str(workspace),
            'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00Z'}) + '\n')
        elapsed = [0.0]
        provider = fixtures.FakeProvider()
        def expire(*args):
            elapsed[0] = 240
            return []
        provider.open_pulls = expire
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]):
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook, tmp_root=clones,
                dispatch_path=ledger, roots={}, provider=provider, process_probe=lambda paths: set(),
                archive_thread=lambda *a, **k: self.fail('no thread'))
        self.assertTrue(workspace.exists(), report)
        self.assertEqual((clone / 'file').read_text(), 'initial\n')
        self.assertLessEqual(elapsed[0], 240)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertFalse(report['removed'], report)
        self.assertNotIn('pending', report)

    def test_temp_recovery_expiry_preserves_pending_without_failure(self):
        import machine_cleanup as cleanup
        import storage_hygiene
        for moved in (True, False):
            with self.subTest(moved=moved), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                scratch, clones = root / 'scratch', root / 'clones'
                scratch.mkdir()
                clones.mkdir()
                candidate = scratch / 'carr-ci-test-recovery'
                candidate.mkdir()
                (candidate / 'payload').write_text('keep complete staged bytes')
                for path in (candidate / 'payload', candidate):
                    os.utime(path, (0, 0))
                staged = storage_hygiene.cleanup_test_temps(scratch, now=time.time(), min_age=21600,
                    max_items=10, cursor='', process_probe=lambda paths: set(), deadline=lambda: False,
                    dry_run=False)['removed'][0]
                actual = Path(staged['staged_path']) if moved else candidate
                if not moved:
                    os.rename(staged['staged_path'], candidate)
                pending = {**staged, 'status': 'intent'}
                receipt = root / 'out/machine-cleanup.json'
                receipt.parent.mkdir()
                receipt.write_text(json.dumps({'pending': pending, 'at': 0}))
                elapsed = [0.0]
                real_lstat = Path.lstat
                def expire(path, *args, **kwargs):
                    result = real_lstat(path, *args, **kwargs)
                    if path == actual:
                        elapsed[0] = 240
                    return result
                config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
                with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                     patch.object(Path, 'lstat', expire):
                    report = cleanup.run(root, config, hook=None, tmp_root=clones, roots={},
                        dispatch_path=root / 'absent.jsonl', provider=object(),
                        process_probe=lambda paths: set(), archive_thread=lambda *a, **k: None)
                self.assertTrue(report.get('budget_exhausted'), report)
                self.assertFalse(report['errors'], report)
                self.assertEqual(report.get('pending'), pending)
                self.assertEqual(candidate.exists(), not moved)
                self.assertEqual((actual / 'payload').read_text(), 'keep complete staged bytes')

    def test_slow_clone_recovery_preserves_pending_for_retry(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        clones = fixture.base / 'clones'
        clones.mkdir()
        workspace = clones / 'room-codex-recovery'
        workspace.mkdir()
        (workspace / 'payload').write_text('preserve complete item')
        staging = clones / '_to_delete'
        staging.mkdir()
        for index in range(5):
            target = staging / (workspace.name + '-' + str(index))
            target.mkdir()
            (target / 'manifest.json').write_text(json.dumps({
                'path': str(workspace), 'status': 'verified_no_effect'}))
        receipt = fixture.repo / 'out/machine-cleanup.json'
        receipt.parent.mkdir(exist_ok=True)
        pending = {'kind': 'job_clone', 'path': str(workspace)}
        receipt.write_text(json.dumps({'pending': pending, 'at': 0}))
        elapsed = [0.0]
        real_scandir = os.scandir
        class SlowEntries:
            def __init__(self):
                self.stream = real_scandir(staging)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def __iter__(self):
                return self
            def __next__(self):
                entry = next(self.stream)
                elapsed[0] += 60
                return entry
        def scan(path):
            return SlowEntries() if Path(path) == staging else real_scandir(path)
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(os, 'scandir', side_effect=scan):
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook, tmp_root=clones,
                dispatch_path=fixture.base / 'absent.jsonl', roots={}, provider=fixtures.FakeProvider(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: self.fail('no thread'))
        self.assertLessEqual(elapsed[0], 240, report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertEqual(report['pending'], pending)
        self.assertEqual(json.loads(receipt.read_text())['pending'], pending)
        self.assertEqual((workspace / 'payload').read_text(), 'preserve complete item')

    def test_slow_clone_discovery_stops_within_cleanup_budget(self):
        import machine_cleanup as cleanup
        fixture = fixtures.Fixture()
        clones = fixture.base / 'clones'
        clones.mkdir()
        for index in range(5):
            workspace = clones / f'room-codex-{index}'
            workspace.mkdir()
            (workspace / 'payload').write_text('preserve complete item')
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        elapsed = [0.0]
        real_scandir = os.scandir
        class SlowEntries:
            def __init__(self, path):
                self.entries = real_scandir(path)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.entries.close()
            def __iter__(self):
                return self
            def __next__(self):
                entry = next(self.entries)
                elapsed[0] += 60
                return entry
        def scan(path):
            return SlowEntries(path) if Path(path) == clones else real_scandir(path)
        with patch.object(cleanup.time, 'monotonic', side_effect=lambda: elapsed[0]), \
             patch.object(os, 'scandir', side_effect=scan):
            report = cleanup.run(fixture.repo, config, hook=fixtures.hook, tmp_root=clones,
                dispatch_path=fixture.base / 'absent.jsonl', roots={}, provider=fixtures.FakeProvider(),
                process_probe=lambda paths: set(), archive_thread=lambda *a, **k: self.fail('no thread'))
        self.assertLessEqual(elapsed[0], 240, report)
        self.assertTrue(report['budget_exhausted'], report)
        self.assertFalse(report['errors'], report)
        self.assertNotIn('pending', report)
        for workspace in clones.glob('room-codex-*'):
            self.assertEqual((workspace / 'payload').read_text(), 'preserve complete item')
        self.assertEqual(len(list(clones.glob('room-codex-*'))), 5)
        self.assertFalse((clones / '_to_delete').exists())

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
