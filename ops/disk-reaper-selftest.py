#!/usr/bin/env python3
"""Disk deletion contracts against isolated real Git repositories, never live trees."""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import platform
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "ops"))
from git_env import fixture_env

spec = importlib.util.spec_from_file_location("tested_disk_reaper", SOURCE / "ops/disk-reaper.py")
reaper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reaper
spec.loader.exec_module(reaper)
hook = reaper.module("tested_scratch_hook", SOURCE / "hooks/session-scratch-reap.py")


class Pulls:
    def __init__(self):
        self.rows = []
        self.failed = False

    def repository(self, root):
        return "fixture/repo"

    def get(self, repo, branch=None, fresh=False):
        if self.failed:
            raise RuntimeError("PR evidence unavailable")
        return [r for r in self.rows if branch is None or r.get("branch") == branch]


class Processes:
    def __init__(self):
        self.busy = False
        self.failed = False

    def collect(self):
        if self.failed:
            raise RuntimeError("incomplete cwd/open-file census")
        return None

    def users(self, target, census):
        return [type("Process", (), {"pid": 424242})()] if self.busy else []

    def stop_orphans(self, *args):
        return []


class TestReaper(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="disk-reaper-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "canonical"
        self.remote = self.base / "remote.git"
        self.git(self.base, "init", "--bare", "-q", "-b", "main", str(self.remote))
        self.git(self.base, "init", "-q", "-b", "main", str(self.root))
        self.git(self.root, "config", "user.name", "Fixture")
        self.git(self.root, "config", "user.email", "fixture@example.invalid")
        (self.root / "file").write_text("initial\n")
        self.git(self.root, "add", "file")
        self.git(self.root, "commit", "-qm", "initial")
        self.git(self.root, "remote", "add", "origin", str(self.remote))
        self.git(self.root, "push", "-qu", "origin", "main")
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()
        self.config = json.loads((SOURCE / "ops/config/disk-reaper.v1.json").read_text())
        self.config.update(repositories=[str(self.root)], protected=[],
                           dispatch_results=str(self.base / "dispatch.jsonl"),
                           session_directory=str(self.base / "sessions.json"),
                           locations=[{"pattern": str(self.scratch / '*'), "kind": "scratch"}])
        self.pulls, self.processes = Pulls(), Processes()

    def git(self, cwd, *args):
        p = subprocess.run(["git", *args], cwd=cwd, env=fixture_env(), text=True,
                           capture_output=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout.strip()

    def clone(self, name="old"):
        p = self.scratch / name
        self.git(self.base, "clone", "-q", str(self.remote), str(p))
        self.git(p, "config", "user.name", "Fixture")
        self.git(p, "config", "user.email", "fixture@example.invalid")
        self.age(p)
        return p

    def age(self, p, seconds=800000):
        stamp = time.time() - seconds
        for directory, dirs, files in os.walk(p, followlinks=False):
            for name in files + dirs:
                item = Path(directory) / name
                os.utime(item, (stamp, stamp), follow_symlinks=False)
        os.utime(p, (stamp, stamp))
        if (p / '.git').is_file():
            index = Path(reaper.worktrees.index_gitdir(str(p))) / "index"
            os.utime(index, (stamp, stamp))

    def engine(self, **kwargs):
        return reaper.Reaper(self.root, self.config, pulls=self.pulls, processes=self.processes,
                            usage=kwargs.pop('usage', lambda _: (1000000000000, 0, 500000000000)), **kwargs)

    def run_reaper(self, **kwargs):
        report = self.engine().run(**kwargs)
        self.assertEqual(report['errors'], [])
        return report

    def row(self, report, p):
        return next(r for r in report['rows'] if r['path'] == str(p))

    def kept(self, p, reason):
        report = self.run_reaper(execute=True)
        self.assertTrue(p.exists(), report)
        row = self.row(report, p)
        self.assertEqual(row['action'], 'keep', report)
        self.assertIn(reason, row['reason'])

    def dispatch(self, p, **kwargs):
        row = dict(msg_id='one', checkout_workspace=str(p), checkout_path=str(p),
                   ownership_state='released', status='completed', thread_id='session-one')
        row.update(kwargs)
        with Path(self.config['dispatch_results']).open('a') as f:
            f.write(json.dumps(row) + '\n')

    def test_dry_run_and_execute(self):
        p = self.clone()
        report = self.run_reaper()
        self.assertEqual(self.row(report, p)['action'], 'would_remove')
        self.assertTrue(p.exists())
        report = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), report)
        self.assertEqual(self.row(report, p)['action'], 'removed')
        self.assertGreater(report['freed_bytes'], 0)
        rows = reaper.jsonl(self.root / 'out/disk-reaper.jsonl')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]['rows'], report['rows'])

    def test_cli_default_inspection_and_explicit_execute(self):
        p = self.clone()
        self.git(p, 'config', f'url.{self.remote}.insteadOf', 'https://github.com/fixture/repo')
        self.git(p, 'remote', 'set-url', 'origin', 'https://github.com/fixture/repo')
        self.age(p)
        fakebin = self.base / 'fakebin'
        fakebin.mkdir()
        started = time.strftime('%a %b %d %H:%M:%S %Y', time.localtime(time.time() - 1000))
        outputs = {'ps': f'1 0 {os.getuid()} 0 {started} fixture fixture\n',
                   'launchctl': 'PID\tStatus\tLabel\n', 'lsof': 'p1\nfcwd\nn/\n',
                   'gh': '[[]]'}
        for name, output in outputs.items():
            file = fakebin / name
            file.write_text(f'#!{sys.executable}\nimport sys\nsys.stdout.write({output!r})\n')
            file.chmod(0o755)
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps(self.config))
        argv = [sys.executable, str(SOURCE / 'ops/disk-reaper.py'),
                '--root', str(self.root), '--config', str(cfg)]
        env = {**fixture_env(), 'PATH': str(fakebin) + os.pathsep + os.environ['PATH']}
        for execute in (False, True):
            run = subprocess.run(argv + (['--execute'] if execute else []), env=env,
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(self.row(result, p)['action'], 'removed' if execute else 'would_remove', result)
            self.assertEqual(p.exists(), not execute)

    def test_tracked_modified_staged_deleted_and_untracked(self):
        p = self.clone()
        for staged, deleted in [(False, False), (True, False), (False, True)]:
            with self.subTest(staged=staged, deleted=deleted):
                self.git(p, 'reset', '--hard', 'HEAD')
                if deleted:
                    (p / 'file').unlink()
                else:
                    (p / 'file').write_text('do not lose\n')
                if staged:
                    self.git(p, 'add', 'file')
                self.age(p)
                self.kept(p, 'uncommitted')
        self.git(p, 'reset', '--hard', 'HEAD')
        (p / 'notes').write_text('untracked work')
        self.age(p)
        self.kept(p, 'uncommitted')

    def test_unpushed_upstream(self):
        p = self.clone()
        (p / 'file').write_text('local work')
        self.git(p, 'commit', '-qam', 'unpushed')
        self.age(p)
        self.kept(p, 'local-only')

    def test_no_upstream_local_commits(self):
        p = self.clone()
        self.git(p, 'switch', '-qc', 'local-only')
        (p / 'file').write_text('local work')
        self.git(p, 'commit', '-qam', 'local-only')
        self.age(p)
        self.kept(p, 'local-only')

    def test_no_remote_and_bare_repositories(self):
        p = self.clone()
        self.git(p, 'remote', 'remove', 'origin')
        self.age(p)
        self.kept(p, 'no upstream')
        bare = self.scratch / 'bare'
        self.git(self.base, 'clone', '--bare', '-q', str(self.remote), str(bare))
        self.config['locations'][0]['kind'] = 'staged'
        self.age(bare)
        self.kept(bare, 'bare repository')

    def test_closed_pr_deleted_upstream_preserves_only_unpublished_tip(self):
        p = self.scratch / 'wt'
        self.git(self.root, 'worktree', 'add', '-qb', 'published', str(p))
        (p / 'file').write_text('published feature')
        self.git(p, 'commit', '-qam', 'published')
        published = self.git(p, 'rev-parse', 'HEAD')
        self.git(p, 'push', '-q', 'origin', 'HEAD:refs/pull/1/head')
        self.config['locations'][0]['kind'] = 'worktree'
        self.pulls.rows = [dict(branch='published', state='closed', number=1,
                               head={'sha': published}, updated_at='2026-01-01T00:00:00Z')]
        (p / 'file').write_text('more unpublished work')
        self.git(p, 'commit', '-qam', 'local-only')
        self.age(p)
        self.kept(p, 'local-only')
        self.git(p, 'reset', '--hard', published)
        result = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), result)

    def test_detached_local_commits(self):
        p = self.clone()
        self.git(p, 'switch', '--detach', '-q')
        (p / 'file').write_text('detached work')
        self.git(p, 'commit', '-qam', 'detached')
        self.age(p)
        self.kept(p, 'local-only')

    def test_other_local_branch_and_stash(self):
        p = self.clone()
        self.git(p, 'switch', '-qc', 'local-work')
        (p / 'file').write_text('local work')
        self.git(p, 'commit', '-qam', 'local-work')
        self.git(p, 'switch', '-q', 'main')
        self.age(p)
        self.kept(p, 'local-only')
        self.git(p, 'branch', '-D', 'local-work')
        (p / 'file').write_text('stash work')
        self.git(p, 'stash', 'push', '-q')
        self.age(p)
        self.kept(p, 'stash')

    def test_stash_protects_only_its_branch_worktree(self):
        a, b = self.scratch / 'a', self.scratch / 'b'
        self.git(self.root, 'worktree', 'add', '-qb', 'topic/a', str(a))
        self.git(self.root, 'worktree', 'add', '-qb', 'topic/ab', str(b))
        self.config['locations'][0]['kind'] = 'worktree'
        for message in ([], ['-m', 'message mentions On topic/ab:']):
            with self.subTest(message=message):
                (a / 'file').write_text('stashed on A')
                self.git(a, 'stash', 'push', '-q', *message)
                self.age(a)
                self.age(b)
                report = self.run_reaper()
                self.assertEqual(self.row(report, a)['reason'], 'local stash')
                self.assertEqual(self.row(report, b)['action'], 'would_remove')
                self.git(a, 'stash', 'drop', '-q')
        (a / 'file').write_text('retained stash')
        self.git(a, 'stash', 'push', '-q')
        self.age(a)
        self.age(b)
        report = self.run_reaper(execute=True)
        self.assertEqual(self.row(report, a)['reason'], 'local stash')
        self.assertTrue(a.exists())
        self.assertEqual(self.row(report, b)['action'], 'removed')
        self.assertFalse(b.exists())
        self.assertIn('WIP on topic/a:', self.git(a, 'stash', 'list'))

    def test_detached_stash_matches_base_commit(self):
        a, b = self.scratch / 'a', self.scratch / 'b'
        self.git(self.root, 'worktree', 'add', '--detach', str(a))
        (a / 'file').write_text('detached stash')
        self.git(a, 'stash', 'push', '-qm', 'detached changes')
        (self.root / 'file').write_text('new published base')
        self.git(self.root, 'commit', '-qam', 'new base')
        self.git(self.root, 'push', '-q')
        self.git(self.root, 'worktree', 'add', '--detach', str(b))
        self.config['locations'][0]['kind'] = 'worktree'
        self.age(a)
        self.age(b)
        report = self.run_reaper(execute=True)
        self.assertEqual(self.row(report, a)['reason'], 'local stash')
        self.assertTrue(a.exists())
        self.assertEqual(self.row(report, b)['action'], 'removed')
        self.assertFalse(b.exists())

    def test_summary_groups_kept_bytes_by_reason(self):
        a, b, c = self.clone('a'), self.clone('b'), self.clone('c')
        removable = self.clone('removable')
        for p in (a, b):
            (p / 'file').write_text('uncommitted')
        (c / 'file').write_text('stash')
        self.git(c, 'stash', 'push', '-q')
        report = self.run_reaper()
        self.assertEqual(report['would_free_bytes'], self.row(report, removable)['bytes'])
        self.assertEqual(report['kept_by_reason'], {
            'uncommitted tracked or untracked work': {
                'bytes': self.row(report, a)['bytes'] + self.row(report, b)['bytes'],
                'count': 2, 'unmeasured_count': 0},
            'local stash': {'bytes': self.row(report, c)['bytes'],
                            'count': 1, 'unmeasured_count': 0}})
        saved = reaper.jsonl(self.root / 'out/disk-reaper.jsonl')[-1]
        self.assertEqual(saved['kept_by_reason'], report['kept_by_reason'])

    def test_independent_clone_keeps_stash_from_another_branch(self):
        p = self.clone()
        self.git(p, 'switch', '-qc', 'stash-owner')
        (p / 'file').write_text('stash belongs to this clone')
        self.git(p, 'stash', 'push', '-q')
        self.git(p, 'switch', '-q', 'main')
        self.age(p)
        self.kept(p, 'local stash')

    def test_summary_marks_unmeasured_candidates(self):
        p = self.clone()
        with patch.object(reaper, 'inventory', side_effect=ValueError('scan failed')):
            report = self.run_reaper()
        self.assertTrue(p.exists())
        self.assertEqual(report['would_free_bytes'], 0)
        self.assertEqual(report['kept_by_reason'], {
            'scan failed': {'bytes': 0, 'count': 1, 'unmeasured_count': 1}})

    def test_remote_deleted_and_failed_fetch(self):
        p = self.clone()
        self.git(p, 'switch', '-qc', 'feature')
        (p / 'file').write_text('feature')
        self.git(p, 'commit', '-qam', 'feature')
        self.git(p, 'push', '-qu', 'origin', 'feature')
        self.git(self.root, 'push', '-q', 'origin', ':feature')
        self.age(p)
        self.kept(p, 'local-only')
        self.git(p, 'remote', 'set-url', 'origin', str(self.base / 'missing.git'))
        self.kept(p, 'git failed')

    def test_dispatch_released_immediate_held_running_unknown(self):
        p = self.clone()
        self.config['locations'][0]['kind'] = 'dispatch'
        self.dispatch(p)
        (p / '.git' / 'index').touch()
        report = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), report)
        for state, status in [('held', 'completed'), (None, 'running'), (None, 'completed')]:
            p = self.clone('job-' + str(state) + status)
            self.dispatch(p, msg_id=p.name, ownership_state=state, status=status)
            self.kept(p, 'dispatch')

    def test_held_job_always_wins_over_another_release(self):
        p = self.clone()
        self.dispatch(p, msg_id='old')
        self.dispatch(p, msg_id='live', ownership_state='held', status='running')
        self.kept(p, 'dispatch held')

    def test_nested_git_in_dispatch_never_loses_work(self):
        p = self.scratch / 'room-codex-test'
        p.mkdir()
        nested = p / 'checkout'
        self.git(self.base, 'clone', '-q', str(self.remote), str(nested))
        (nested / 'file').write_text('unfinished')
        self.age(p)
        self.config['locations'][0]['kind'] = 'dispatch'
        self.dispatch(p)
        self.kept(p, 'uncommitted')

    def test_registered_sibling_outside_configured_locations_stays_keep(self):
        p = self.base / 'outside-configured-locations'
        self.git(self.root, 'worktree', 'add', '-qb', 'outside', str(p))
        self.age(p)
        report = self.run_reaper()
        self.assertEqual(self.row(report, p)['action'], 'keep')
        self.assertIn('outside configured locations', self.row(report, p)['reason'])
        self.assertTrue(p.exists())
        info = reaper.inventory(p)
        with self.assertRaisesRegex(RuntimeError, 'outside configured locations'):
            self.engine().remove(reaper.Candidate(p, 'worktree'), info)
        self.assertTrue(p.exists())

    def test_location_symlink_escape_and_final_recheck(self):
        p = self.clone()
        engine = self.engine()
        info = reaper.inventory(p)
        self.config['locations'] = []
        with self.assertRaisesRegex(RuntimeError, 'outside configured locations'):
            engine.remove(reaper.Candidate(p, 'scratch'), info)
        self.assertTrue(p.exists())
        outside = self.base / 'escaped-worktree'
        self.git(self.root, 'worktree', 'add', '-qb', 'escaped', str(outside))
        self.age(outside)
        alias = self.scratch / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        self.config['locations'] = [{'pattern': str(self.scratch / '*/*'), 'kind': 'scratch'}]
        with self.assertRaisesRegex(ValueError, 'scratch directory'):
            engine.register([alias / outside.name], 'one')
        report = engine.run()
        self.assertEqual(self.row(report, outside)['action'], 'keep')
        self.assertIn('outside configured locations', self.row(report, outside)['reason'])
        self.assertTrue(outside.exists())

    def test_session_hook_registration_obeys_narrow_config(self):
        outside = self.root / 'out/previously-hardcoded'
        outside.mkdir(parents=True)
        allowed = self.scratch / 'allowed'
        allowed.mkdir()
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps(self.config))
        args = ['--root', str(self.root), '--config', str(cfg), '--session', 'one']
        for p in (outside, allowed):
            hook.main(args + ['--register', str(p)], event={})
        registry = self.engine().registry()
        self.assertNotIn(str(outside), registry)
        self.assertEqual(registry[str(allowed)]['session'], 'one')

    def test_old_open_pr_always_kept_closed_merged_and_absent_follow_safety(self):
        p = self.scratch / 'wt'
        self.git(self.root, 'worktree', 'add', '-qb', 'feature', str(p))
        self.config['locations'][0]['kind'] = 'worktree'
        self.age(p)
        for state, merged, expected in [('open', None, 'keep'),
                ('closed', None, 'would_remove'), ('closed', '2020-01-01T00:00:00Z', 'would_remove')]:
            with self.subTest(state=state, merged=merged):
                self.pulls.rows = [dict(branch='feature', state=state, merged_at=merged,
                                       updated_at='2020-01-01T00:00:00Z')]
                self.assertEqual(self.row(self.run_reaper(), p)['action'], expected)
        self.pulls.rows = []
        self.assertEqual(self.row(self.run_reaper(), p)['action'], 'would_remove')
        (p / 'file').write_text('uncommitted')
        self.kept(p, 'uncommitted')

    def test_denied_native_ps_keeps_every_candidate(self):
        paths = [self.clone('one'), self.clone('two')]
        engine = self.engine()
        engine.processes = reaper.Processes()
        original = reaper.command
        def denied(argv, **kwargs):
            if argv[0] == 'ps':
                raise PermissionError('Operation not permitted: ps')
            return original(argv, **kwargs)
        with patch.object(reaper, 'command', side_effect=denied):
            report = engine.run(execute=True)
        self.assertEqual(report['would_free_bytes'], 0)
        self.assertEqual(report['freed_bytes'], 0)
        for p in paths:
            self.assertTrue(p.exists())
            self.assertEqual(self.row(report, p)['action'], 'keep')
            self.assertIn('ps', self.row(report, p)['reason'])

    def test_recent_open_pr_and_unknown_pr(self):
        p = self.clone()
        self.pulls.rows = [dict(branch='main', state='open', updated_at='2099-01-01T00:00:00Z')]
        self.kept(p, 'open PR')
        self.pulls.rows = []
        self.pulls.failed = True
        self.kept(p, 'PR evidence unavailable')

    def test_protected_parent_and_child(self):
        p = self.clone()
        for protected in [p, p / 'node_modules', p.parent]:
            with self.subTest(protected=protected):
                self.config['protected'] = [str(protected)]
                self.kept(p, 'protected path')

    def test_live_process_and_failed_census(self):
        p = self.clone()
        self.processes.busy = True
        self.kept(p, 'live process')
        self.processes.busy, self.processes.failed = False, True
        self.kept(p, 'census')

    def test_process_cwd_open_file_unknown_pid_and_lsof_failure(self):
        p = self.clone()
        processes = reaper.Processes()
        process = reaper.orphan.Process(42, 1, os.getuid(), 'identity', 1000, 0,
                                       'node', 'node serve.mjs', str(p))
        for name in [p, p / 'file']:
            with self.subTest(name=name):
                census = ([process], set(), {42: {name}}, {42: p})
                self.assertEqual([q.pid for q in processes.users(p, census)], [42])
        self.assertEqual([q.pid for q in processes.users(p,
            ([], set(), {43: {p / 'file'}}, {}))], [43])
        def native(argv, **kwargs):
            if argv[0] == 'ps':
                text = '42 1 ' + str(os.getuid()) + ' 0 ' + time.strftime(
                    '%a %b %d %H:%M:%S %Y', time.localtime(time.time() - 1000)) + ' node node serve.mjs\n'
                return subprocess.CompletedProcess(argv, 0, text, '')
            if argv[0] == 'launchctl':
                return subprocess.CompletedProcess(argv, 0, 'PID\tStatus\tLabel\n', '')
            return subprocess.CompletedProcess(argv, 0, f'p42\nf3\nn{p}/file\nfcwd\nn{p}\n', '')
        with patch.object(reaper, 'command', side_effect=native):
            census = processes.collect()
        self.assertEqual(census[3], {42: p})
        self.assertEqual(census[2], {42: {p, p / 'file'}})
        original = native
        def denied(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[0] == 'lsof':
                result.stderr = 'permission denied'
            return result
        with patch.object(reaper, 'command', side_effect=denied):
            with self.assertRaisesRegex(RuntimeError, 'census'):
                processes.collect()

    def test_native_process_open_file_and_cwd(self):
        if platform.system() != 'Darwin':
            self.skipTest('native launchctl census requires macOS')
        p = self.clone()
        child = subprocess.Popen([sys.executable, '-c',
            'import time; f=open("file"); print("ready",flush=True); time.sleep(30)'],
            cwd=p, stdout=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close()
        self.addCleanup(cleanup)
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        # Exercise native collection, not a supplied busy boolean.
        processes = reaper.Processes()
        try:
            census = processes.collect()
        except PermissionError as exc:
            self.skipTest(f'native census unavailable in host sandbox: {exc}')
        self.assertIn(child.pid, [q.pid for q in processes.users(p, census)])
        report = reaper.Reaper(
            self.root, self.config, pulls=self.pulls, processes=processes)
        result = report.run(execute=True)
        self.assertTrue(p.exists(), result)
        self.assertIn('live process', self.row(result, p)['reason'])
        child.terminate()
        child.wait(timeout=5)
        child.stdout.close()

    def test_recent_write_inside_ignored_directory(self):
        p = self.clone()
        (p / '.git/info/exclude').write_text('node_modules/\n')
        (p / 'node_modules').mkdir()
        (p / 'node_modules/new').write_text('active build')
        self.kept(p, 'recent write')

    def test_symlink_outside_candidate_is_not_followed(self):
        p = self.clone()
        target = self.base / 'valuable'
        target.mkdir()
        (target / 'data').write_text('precious')
        (p / '.git/info/exclude').write_text('external\n')
        (p / 'external').symlink_to(target, target_is_directory=True)
        self.age(p)
        result = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), result)
        self.assertEqual((target / 'data').read_text(), 'precious')

    def test_worktree_closed_pr_immediate_and_abandoned_three_days(self):
        p = self.scratch / 'wt'
        self.git(self.root, 'worktree', 'add', '-qb', 'feature', str(p))
        self.config['locations'][0]['kind'] = 'worktree'
        self.kept(p, 'recent write')
        self.pulls.rows = [dict(branch='feature', state='closed', updated_at='2026-01-01T00:00:00Z')]
        result = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), result)
        self.assertNotIn(str(p), self.git(self.root, 'worktree', 'list', '--porcelain'))
        self.pulls.rows = []
        self.git(self.root, 'worktree', 'add', '-qb', 'abandoned', str(p))
        self.age(p, 3 * 86400 + 10)
        result = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), result)

    def test_maintenance_lock_does_not_block_other_cleanup(self):
        wt = self.scratch / 'wt'
        self.git(self.root, 'worktree', 'add', '-qb', 'old-wt', str(wt))
        self.age(wt)
        clean = self.clone('independent')
        self.config['locations'].insert(0, {'pattern': str(wt), 'kind': 'worktree'})
        with reaper.maintenance(self.root):
            result = self.run_reaper(execute=True)
        self.assertTrue(wt.exists(), result)
        self.assertIn('maintenance lock held', self.row(result, wt)['reason'])
        self.assertFalse(clean.exists(), result)

    def test_locked_worktree_preserved(self):
        p = self.scratch / 'wt'
        self.git(self.root, 'worktree', 'add', '-qb', 'locked-wt', str(p))
        self.git(self.root, 'worktree', 'lock', str(p))
        self.age(p)
        self.kept(p, 'locked')

    def test_artifact_newest_two_and_open_pr_reference(self):
        self.config['locations'] = [{'pattern': str(self.root / 'out/e2e/*'), 'kind': 'artifact', 'series': '{parent}'}]
        runs = []
        for i in range(4):
            p = self.root / f'out/e2e/run-{i}'
            p.mkdir(parents=True)
            (p / 'result').write_text(str(i))
            self.age(p, seconds=10000 - i * 100)
            runs.append(p)
        self.pulls.rows = [dict(branch='unrelated', state='open', body='Evidence out/e2e/run-0/result')]
        result = self.run_reaper(execute=True)
        self.assertEqual([p.exists() for p in runs], [True, False, True, True], result)
        self.assertIn('referenced', self.row(result, runs[0])['reason'])

    def test_staged_seven_days_and_nested_dirty_repo(self):
        p = self.clone()
        self.config['locations'][0]['kind'] = 'staged'
        self.age(p, seconds=6 * 86400)
        self.kept(p, 'recent write')
        (p / 'file').write_text('salvage')
        self.age(p, seconds=8 * 86400)
        self.kept(p, 'uncommitted')
        self.git(p, 'reset', '--hard', 'HEAD')
        self.age(p, seconds=8 * 86400)
        result = self.run_reaper(execute=True)
        self.assertFalse(p.exists(), result)

    def test_registry_ownership_inode_and_session_scope(self):
        p = self.clone()
        other = self.clone('other-session')
        engine = self.engine()
        engine.register([p], 'one')
        engine.register([other], 'two')
        result = engine.run(execute=True, session='one')
        self.assertFalse(p.exists(), result)
        self.assertTrue(other.exists(), result)
        self.assertIn('not this session', self.row(result, other)['reason'])
        registry = engine.registry()
        registry[str(other)]['identity'] = [-1, -1]
        reaper.save(engine.registry_path, registry)
        result = engine.run(execute=True, session='two')
        self.assertTrue(other.exists(), result)

    def test_session_hook_register_and_stop_same_engine(self):
        p = self.scratch / 'scratch-one'
        p.mkdir(parents=True)
        cfg = self.base / 'config.json'
        cfg.write_text(json.dumps(self.config))
        args = ['--root', str(self.root), '--config', str(cfg)]
        with redirect_stdout(io.StringIO()):
            hook.main(args + ['--register', str(p)], event={'session_id': 'one'})
        registry = self.engine().registry()
        self.assertEqual(registry[str(p)]['session'], 'one')
        with patch.object(hook.reaper, 'Processes', return_value=self.processes), \
                patch.object(hook.reaper, 'Pulls', return_value=self.pulls), redirect_stdout(io.StringIO()):
            hook.main(args, event={'session_id': 'one', 'hook_event_name': 'Stop'})
        self.assertFalse(p.exists())
        self.assertEqual(hook.created_paths({'hook_event_name': 'PostToolUse',
            'tool_input': {'command': 'mktemp -d /private/tmp/agent-XXXX'},
            'tool_response': {'exit_code': 0, 'stdout': '/private/tmp/agent-1234\n'}}), ['/private/tmp/agent-1234'])

    def test_pressure_flag_health_bound_action_and_clear(self):
        engine = self.engine(usage=lambda _: (1000000000000, 0, 10000000000))
        p = self.clone()
        result = engine.run(execute=True)
        self.assertFalse(p.exists(), result)
        flag = self.root / 'out/disk-pressure.flag'
        self.assertTrue(flag.exists())
        health = json.loads((self.root / 'out/disk-pressure-health.json').read_text())
        self.assertEqual(health['status'], 'WARN')
        for phrase in ['owner orchestrator', 'on breach', 'verify', 'auto-clear', 'dispatch admission hold']:
            self.assertIn(phrase, health['row'])
        self.engine().run(execute=True)
        self.assertFalse(flag.exists())
        engine.run()  # Inspection must not arm a dispatch hold.
        self.assertFalse(flag.exists())

    def test_change_at_mutation_seam_is_preserved(self):
        p = self.clone()
        engine = self.engine()
        original = engine.safety
        def safety(c, info, dispatch, fresh=False):
            value = original(c, info, dispatch, fresh=fresh)
            if fresh:
                (p / 'file').write_text('race')
            return value
        engine.safety = safety
        result = engine.run(execute=True)
        self.assertTrue(p.exists(), result)
        self.assertIn('changed at removal', self.row(result, p)['reason'])

    def test_orphan_only_signals_released_owned_reparented_process(self):
        p = self.clone()
        now = time.time()
        started = time.strftime('%a %b %d %H:%M:%S %Y', time.localtime(now - 1000))
        original = reaper.orphan.Process(999999, 1, os.getuid(), started, 1000, 0,
                                         'node', 'node serve.mjs', str(p))
        processes = reaper.Processes()
        opened = {original.pid: {p}}
        for changes, managed, ended, expected in [({}, set(), True, 1),
                ({'ppid': 555}, set(), True, 0), ({}, {999999}, True, 0),
                ({}, set(), False, 0), ({'started': 'changed'}, set(), True, 0),
                ({'args': 'codex active-executor'}, set(), True, 0)]:
            observed = replace(original, **changes)
            census = ([original], managed, opened, {original.pid: p})
            fresh = ([observed], managed, opened, {original.pid: p})
            with patch.object(processes, 'collect', return_value=fresh), \
                    patch.object(reaper.os, 'kill') as kill, patch.object(reaper.time, 'sleep'):
                processes.stop_orphans(p, self.config, ended, census)
                self.assertEqual(kill.call_count, expected)

    def test_launchd_calendar_and_config_contract(self):
        plist = plistlib.loads((SOURCE / 'ops/launchd/com.carr.disk-reaper.plist').read_bytes())
        self.assertNotIn('StartInterval', plist)
        self.assertEqual([r['Hour'] for r in plist['StartCalendarInterval']], list(range(0, 24, 2)))
        self.assertEqual(plist['ProgramArguments'][-1], '--execute')
        config = reaper.load_config(SOURCE / 'ops/config/disk-reaper.v1.json')
        self.assertEqual(len(config['protected']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
