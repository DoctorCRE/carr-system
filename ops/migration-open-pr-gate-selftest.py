"""Open PR collisions name the owner and the next usable slot."""
import importlib.util
import json
import os
import subprocess
import shutil
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from migration_claims import Claims
from lib.release_reader import Reply

spec = importlib.util.spec_from_file_location('open_pr_gate', ROOT / 'ops/migration-open-pr-gate.py')
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
from git_env import fixture_env


class CollisionTests(unittest.TestCase):
    def test_later_pr_gets_clear_renumber_target(self):
        errors = gate.findings({'0890_main.sql'}, {'0891_feature.sql'}, Claims({891}, {891, 892}, {891: 7}))
        self.assertEqual(errors, ['0891_feature.sql: slot 0891 belongs to PR #7; rename to 0893_feature.sql and update its references'])

    def test_earlier_pr_keeps_its_number(self):
        self.assertEqual(gate.findings({'0890_main.sql'}, {'0891_feature.sql'}, Claims(set(), {891}, {891: 11})), [])

    def test_lower_free_number_survives_reverse_merge_order(self):
        self.assertEqual(gate.findings({'0890_base.sql', '0892_later.sql'},
                                       {'0891_feature.sql'}, Claims(set(), set(), {})), [])

    def test_occupied_lower_main_slot_still_refuses(self):
        errors = gate.findings({'0890_main.sql', '0892_later.sql'},
                               {'0890_feature.sql'}, Claims(set(), set(), {}))
        self.assertIn('slot 0890 belongs to main', errors[0])

    def test_main_always_owns_its_number(self):
        errors = gate.findings({'0891_main.sql'}, {'0891_feature.sql'}, Claims(set(), set(), {}))
        self.assertIn('rename to 0892_feature.sql', errors[0])


class StackedBaseTests(unittest.TestCase):
    def test_main_owned_migration_is_not_reclaimed_by_a_stale_stack(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'number': 1945, 'pull_request': {
                'base': {'ref': 'stack-parent'}, 'head': {'sha': 'feature-head'}}}))
            trees = {
                'origin/stack-parent': {'0907_parent.sql'},
                'origin/main': {'0907_parent.sql', '0908_main.sql'},
                'fork': {'0907_parent.sql'},
            }
            for name, expected in [('0908_main.sql', 0), ('0908_feature.sql', 1)]:
                with self.subTest(migration=name):
                    trees['feature-head'] = {'0907_parent.sql', name}
                    with patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'pull_request',
                                                 'GITHUB_EVENT_PATH': str(event)}), \
                            patch.object(gate, 'migration_names', side_effect=lambda root, ref: trees[ref]), \
                            patch.object(gate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='fork')), \
                            patch.object(gate, 'claims', return_value=Claims({908}, {908}, {908: 1933})):
                        self.assertEqual(gate.main(), expected)


class HostedTests(unittest.TestCase):
    def test_gate_without_gh_keeps_collision_and_read_failure_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.run(['git', *args], cwd=root, check=True,
                                      capture_output=True, text=True, env=fixture_env()).stdout.strip()
            git('init', '-b', 'main')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@invalid.test')
            git('config', 'core.hooksPath', '/dev/null')
            (root / 'migrations').mkdir()
            (root / 'migrations/0890_main.sql').write_text('SELECT 1;')
            git('add', 'migrations/0890_main.sql')
            git('commit', '-m', 'base fixture')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            (root / 'migrations/0891_feature.sql').write_text('SELECT 2;')
            git('add', 'migrations/0891_feature.sql')
            git('commit', '-m', 'feature fixture')
            event = root / 'event.json'
            event.write_text(json.dumps({'number': 9, 'pull_request': {
                'base': {'ref': 'main'}, 'head': {'sha': git('rev-parse', 'HEAD')}}}))
            (root / 'git').symlink_to(shutil.which('git'))
            cases = [(200, [], 0), (200, [
                {'filename': 'migrations/0891_peer.sql', 'status': 'added'}], 1),
                (403, [], 2)]
            for status, files, expected in cases:
                def read(url, **kwargs):
                    data = [{'number': 7}, {'number': 9}] if '/files?' not in url else files
                    return Reply(status, {}, json.dumps(data).encode())
                with self.subTest(status=status, files=files), \
                        patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'pull_request',
                                                'GITHUB_EVENT_PATH': str(event),
                                                'GITHUB_TOKEN': 'synthetic', 'PATH': directory}), \
                        patch.object(gate, 'ROOT', root), \
                        patch('lib.release_reader.read', side_effect=read):
                    self.assertEqual(gate.main(), expected)


if __name__ == '__main__':
    unittest.main()
