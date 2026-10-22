"""Drain real temporary Git checkouts without relaxing retention guards."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'ops')]
import branch_retirement
import local_jobs
import machine_cleanup

spec = importlib.util.spec_from_file_location('cleanup_fixtures', ROOT / 'ops/branch-janitor-selftest.py')
assert spec is not None and spec.loader is not None
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class CleanupThroughputTests(unittest.TestCase):
    clone_count = 300
    worktree_count = 400
    budget_scale = 1

    def test_three_runs_drain_backlog_and_preserve_guards(self):
        fixture = fixtures.Fixture()
        self.addCleanup(shutil.rmtree, fixture.base)
        repo = 'jbookout/carr-system'
        head = fixture.git('rev-parse', 'HEAD')
        clones, scratch = fixture.base / 'clones', fixture.base / 'scratch'
        clones.mkdir()
        scratch.mkdir()
        template = fixture.base / 'template'
        fixture.git('clone', str(fixture.remote), str(template))
        fixture.git('switch', 'main', cwd=template)
        for index in range(self.worktree_count):
            fixture.tree(f'tree-{index:04}')
        for index in range(self.clone_count):
            shutil.copytree(template, clones / f'room-codex-{index:04}' / 'checkout')
        for index in range(self.clone_count * 2 // 5):
            path = scratch / f'carr-ci-test-{index:04}'
            path.mkdir()
            os.utime(path, (0, 0))
        guarded = []
        for condition in ('dirty', 'unpushed', 'open-pr'):
            tree = fixture.tree(f'guard-{condition}')
            if condition == 'dirty':
                (tree / 'file').write_text('uncommitted fixture bytes')
            if condition == 'unpushed':
                fixture.commit('unpushed fixture bytes', cwd=tree)
                fixture.age(tree)
            guarded.append(tree)
        provider = fixtures.FakeProvider()
        opened = fixtures.pull(1, head, head)
        opened['head']['ref'] = 'guard-open-pr'
        opened['head']['repo']['full_name'] = repo
        provider.rows.append(opened)
        dispatches = []
        for workspace in sorted(clones.glob('room-codex-*')):
            dispatches.append({'checkout_workspace': str(workspace), 'checkout_path': str(workspace / 'checkout'),
                               'status': 'completed', 'dispatched_at': '2020-01-01T00:00:00Z'})
        for condition in ('dirty', 'unpushed', 'open-pr', 'unfinished'):
            workspace = clones / f'room-codex-000-guard-{condition}'
            clone = workspace / 'checkout'
            shutil.copytree(template, clone)
            fixture.git('switch', '-c', f'guard-{condition}', cwd=clone)
            if condition == 'dirty':
                (clone / 'file').write_text('uncommitted fixture bytes')
            if condition == 'unpushed':
                fixture.git('config', 'user.name', 'Fixture', cwd=clone)
                fixture.git('config', 'user.email', 'fixture@example.invalid', cwd=clone)
                fixture.commit('unpushed fixture bytes', cwd=clone)
            guarded.append(clone)
            dispatches.append({'checkout_workspace': str(workspace), 'checkout_path': str(clone),
                               'status': 'delivered' if condition == 'unfinished' else 'completed',
                               'dispatched_at': '2020-01-01T00:00:00Z'})
        dispatch_path = fixture.base / 'dispatch.jsonl'
        dispatch_path.write_text(''.join(json.dumps(row) + '\n' for row in dispatches))
        config = local_jobs.load_config(ROOT / 'ops/config/local-jobs.json')
        config['machine_cleanup']['max_items'] //= self.budget_scale
        totals = {'job_clone': 0, 'worktree': 0}
        for run in range(1, 4):
            with patch.object(branch_retirement, 'ownership', return_value='orphaned'):
                report = machine_cleanup.run(fixture.repo, config, hook=fixtures.hook,
                    tmp_root=clones, user_tmp_root=scratch, dispatch_path=dispatch_path,
                    roots={repo: fixture.repo}, provider=provider, process_probe=lambda paths: set(),
                    archive_thread=lambda *args, **kwargs: self.fail('no thread fixture'))
            self.assertFalse(report['errors'], report['errors'])
            self.assertFalse(report['deferred'], report['deferred'])
            for row in report['removed']:
                staged = Path(row['staged_path'])
                self.assertIn('_to_delete', staged.parts)
                self.assertTrue(staged.is_dir())
                if row['kind'] in totals:
                    self.assertEqual((staged / ('checkout/file' if row['kind'] == 'job_clone' else 'file')).read_text(),
                        'initial\n')
                    totals[row['kind']] += 1
            for path in guarded:
                self.assertTrue(path.exists(), str(path))
            print(f'drain run {run}: {totals}', flush=True)
        self.assertEqual(totals, {'job_clone': self.clone_count, 'worktree': self.worktree_count})
        self.assertEqual(len(list(clones.glob('room-codex-*'))), 4)
        self.assertEqual(sum('_to_delete' not in Path(row['path']).parts
            for row in branch_retirement.iter_worktrees(fixture.repo)), 4)


if __name__ == '__main__':
    unittest.main()
