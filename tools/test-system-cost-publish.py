"""Cost collector publication through its command/module seam, without providers."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import system_costs as costs
import progress_board as board


class CostPublication(unittest.TestCase):
    def test_existing_snapshot_establishes_highwater_on_upgrade(self):
        now = datetime.now(timezone.utc)
        config_data = {'budget_usd': 100, 'providers': {}}
        newer = costs.summarize(config_data, {}, now.date() - timedelta(days=1), observed_at=now.isoformat())
        older = costs.summarize(config_data, {}, now.date() - timedelta(days=2), observed_at=(now - timedelta(hours=1)).isoformat())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            output = root / 'costs.json'
            output.write_text(json.dumps(newer))
            with patch.object(costs, 'ROOT', root), patch.object(costs, 'collect', return_value=older), \
                 patch.object(costs, 'read_tokens', return_value={}), redirect_stdout(io.StringIO()):
                costs.main(['--config', str(config), '--output', str(output)])
            self.assertEqual(json.loads(output.read_text()), newer)

    def test_stale_collection_cannot_replace_or_publish_newer_snapshot(self):
        config_data = {'budget_usd': 100, 'providers': {'github': {
            'label': 'GitHub', 'plan': 'Pro', 'monthly_usd': 4}}}
        newer = costs.summarize(config_data, {'github': {'state': 'ready', 'rows': []}},
                                datetime(2026, 10, 6).date(), observed_at='2026-10-07T00:00:00Z')
        older = costs.summarize(config_data, {'github': {'state': 'ready', 'rows': []}},
                                datetime(2026, 10, 5).date(), observed_at='2026-10-06T00:00:00Z')
        correction = {**newer, 'observed_at': '2026-10-06T23:00:00Z'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            output = root / 'costs.json'
            with patch.object(costs, 'ROOT', root), patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root)}), \
                 patch.object(costs, 'read_tokens', return_value={}), patch.object(costs, 'reconcile') as reconcile, \
                 patch.object(board, 'publish_board') as publish, patch.object(board, 'command_init'), \
                 patch.object(board, 'state_path', return_value=config), redirect_stdout(io.StringIO()):
                for report in (newer, older, correction):
                    with patch.object(costs, 'collect', return_value=report):
                        costs.main(['--config', str(config), '--output', str(output), '--alerts', '--publish'])
                self.assertEqual(json.loads(output.read_text()), newer)
                self.assertEqual(publish.call_count, 1)
                self.assertEqual(reconcile.call_count, 1)

    def test_concurrent_collection_publishes_newest_observation(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        newer_done = threading.Event()
        config_data = {'budget_usd': 100, 'providers': {}}
        newer = costs.summarize(config_data, {}, datetime(2026, 10, 6).date(), observed_at='2026-10-07T00:00:00Z')
        older = costs.summarize(config_data, {}, datetime(2026, 10, 5).date(), observed_at='2026-10-06T00:00:00Z')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            output = root / 'costs.json'
            def collect(*args, **kwargs):
                if threading.current_thread().name.endswith('_0'):
                    self.assertTrue(newer_done.wait(timeout=10))
                    return older
                return newer
            def run(newest):
                result = costs.main(['--config', str(config), '--output', str(output), '--publish'])
                if newest:
                    newer_done.set()
                return result
            with patch.object(costs, 'ROOT', root), patch.object(costs, 'collect', side_effect=collect), \
                 patch.object(costs, 'read_tokens', return_value={}), patch.object(board, 'publish_board') as publish, \
                 patch.object(board, 'state_path', return_value=config), redirect_stdout(io.StringIO()):
                with ThreadPoolExecutor(2) as pool:
                    pending = [pool.submit(run, newest) for newest in (False, True)]
                    for result in pending:
                        self.assertEqual(result.result(timeout=15), 0)
                self.assertEqual(json.loads(output.read_text()), newer)
                self.assertEqual(publish.call_count, 1)

    def test_first_publish_creates_cost_board_and_reads_back_collected_evidence(self):
        now = datetime.now(timezone.utc)
        report = costs.summarize({'budget_usd': 100, 'providers': {
            'jev': {'label': 'Jev', 'plan': 'Usage', 'monthly_usd': 0, 'budget_usd': 50}}},
            {'jev': {'state': 'ready', 'rows': []}}, now.date() - timedelta(days=1), observed_at=now.isoformat())
        remote = {}
        calls = []
        def verb(name, args):
            calls.append((name, args))
            if name == 'read-progress-board':
                return {'ok': True, 'snapshot': remote.get(args['board_id']), 'questions': []}
            if name == 'publish-board-snapshot':
                remote[args['board_id']] = {'version': args['base_version'] + 1, 'snapshot_json': args['snapshot']}
                return {'ok': True}
            self.fail('unexpected verb: ' + name)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'fixture-config.json'
            config.write_text('{}')
            with patch.object(costs, 'ROOT', root), patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}), \
                 patch.object(board, 'REPO_ROOT', root), patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root / 'out')}), \
                 patch.object(board, 'call_verb', side_effect=verb), \
                 patch.object(board, 'refresh_and_publish', side_effect=lambda project: board.publish_board(project)), \
                 redirect_stdout(io.StringIO()):
                result = costs.main(['--config', str(config), '--output', str(root / 'custom/costs.json'), '--publish'])
            self.assertEqual(result, 0)
            self.assertTrue((root / 'out/boards/system-costs.json').is_file())
            self.assertEqual(remote['system-costs']['snapshot_json']['costs']['state'], 'ready')
            self.assertEqual(remote['system-costs']['snapshot_json']['costs']['through'], report['through'])
            self.assertEqual([name for name, _ in calls], ['read-progress-board', 'publish-board-snapshot', 'read-progress-board'])


    def test_existing_board_is_published_without_reinitializing_it(self):
        report = {'state': 'ready', 'providers': [], 'alerts': [], 'through': '2026-10-05', 'observed_at': '2026-10-06T00:00:00Z'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            output = root / 'system-costs.json'
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root)}), \
                 patch.object(board, 'refresh_and_publish'), \
                 patch.object(costs, 'ROOT', root), patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}):
                import argparse
                board.command_init(argparse.Namespace(project='system-costs', title='Existing board title'))
                before = board.state_path('system-costs').read_text()
                with patch.object(board, 'command_init', side_effect=AssertionError('board reset')), \
                     patch.object(board, 'publish_board') as publish, redirect_stdout(io.StringIO()):
                    self.assertEqual(costs.main(['--config', str(config), '--output', str(output), '--publish']), 0)
                self.assertEqual(publish.call_args.args, ('system-costs',))
                self.assertIn('costs', publish.call_args.kwargs)
                self.assertEqual(board.state_path('system-costs').read_text(), before)

    def test_concurrent_board_create_is_tolerated_but_other_init_failures_propagate(self):
        report = {'state': 'ready', 'providers': [], 'alerts': [], 'through': '2026-10-05', 'observed_at': '2026-10-06T00:00:00Z'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            original_init = board.command_init
            def concurrent_init(args):
                original_init(args)  # Another collector wins its exclusive create.
                original_init(args)  # The current create gets the existing-board refusal.
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root)}), \
                 patch.object(board, 'refresh_and_publish'), \
                 patch.object(costs, 'ROOT', root), patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}), \
                 patch.object(board, 'command_init', side_effect=concurrent_init), \
                 patch.object(board, 'publish_board') as publish, redirect_stdout(io.StringIO()):
                self.assertEqual(costs.main(['--config', str(config), '--output', str(root / 'costs.json'), '--publish']), 0)
                self.assertEqual(publish.call_args.args, ('system-costs',))
                self.assertIn('costs', publish.call_args.kwargs)
            with patch.dict(os.environ, {'PROGRESS_BOARD_ROOT': str(root / 'fresh')}), \
                 patch.object(costs, 'ROOT', root), patch.object(costs, 'collect', return_value=report), patch.object(costs, 'read_tokens', return_value={}), \
                 patch.object(board, 'command_init', side_effect=SystemExit('publication failed')), \
                 patch.object(board, 'publish_board') as publish, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, 'publication failed'):
                    costs.main(['--config', str(config), '--output', str(root / 'costs.json'), '--publish'])
                publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
