# Test progress board publication size boundaries.
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
import progress_board as board


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(tempfile, "tempdir", root))
        self.enterContext(patch.object(board.repo_lib("machine_role"), "read_marker", return_value="primary"))

    def test_secondary_and_invalid_roles_refuse_every_orchestrator_door(self):
        from argparse import Namespace
        for marker in ('secondary', 'invalid'):
            for door in ('publisher', 'board', 'scheduled'):
                with self.subTest(marker=marker, door=door), \
                     patch.object(board.repo_lib('machine_role'), 'read_marker', return_value=marker), \
                     patch.object(board, 'read_state', return_value={'project': 'carr-v5', 'tasks': {}}), \
                     patch.object(board, 'call_verb', side_effect=RuntimeError('remote reached')) as remote, \
                     patch.object(board, 'render') as render, patch.object(board, 'build_all_repos'):
                    with self.assertRaisesRegex(RuntimeError, 'primary'):
                        if door == 'publisher':
                            board.publish_orchestrator({'tasks': {}})
                        elif door == 'board':
                            board.publish_board('carr-v5')
                        else:
                            board.command_render(Namespace(project='carr-v5', publish=True))
                    remote.assert_not_called()
                    render.assert_not_called()

    def test_repeated_updates_reuse_page_slots(self):
        state = {'tasks': {str(i): {'title': f'Work {i}', 'stage': 'review',
            'updated_at': '2026-01-01T00:00:00Z'} for i in range(1000)}}
        store = {}
        def call(verb, args):
            key = args['board_id']
            if verb == 'read-progress-board':
                return {'snapshot': copy.deepcopy(store.get(key))}
            self.assertEqual(args['base_version'], store.get(key, {}).get('version', 0))
            store[key] = {'version': args['base_version'] + 1, 'snapshot_json': copy.deepcopy(args['snapshot'])}
            return {'ok': True}
        with patch.object(board, 'call_verb', side_effect=call), patch.object(board, 'read_json_file', return_value={}):
            for i in range(11):
                state['tasks'][str(i)]['updated_at'] = f'2026-10-21T19:00:{i:02d}Z'
                board.publish_orchestrator(state)
            self.assertLessEqual(len(store), 19)
            view = store['orchestrator']['snapshot_json']['orchestrator']
            cards = view['cards'] + [c for p in view['pages'] for c in store[p['board_id']]['snapshot_json']['cards']]
            self.assertEqual(len({c['id'] for c in cards}), 1000)

    def test_partial_failure_and_concurrent_publishers_preserve_complete_observation(self):
        from concurrent.futures import ThreadPoolExecutor
        state = {'tasks': {str(i): {'title': f'Work {i}', 'stage': 'review'} for i in range(1000)}}
        store = {}
        fail = False
        def call(verb, args):
            key = args['board_id']
            if verb == 'read-progress-board':
                return {'snapshot': copy.deepcopy(store.get(key))}
            if fail and key.endswith('-2'):
                raise RuntimeError('partial page outage')
            self.assertEqual(args['base_version'], store.get(key, {}).get('version', 0))
            store[key] = {'version': args['base_version'] + 1, 'snapshot_json': copy.deepcopy(args['snapshot'])}
            return {'ok': True}
        with patch.object(board, 'call_verb', side_effect=call), patch.object(board, 'read_json_file', return_value={}):
            board.publish_orchestrator(state)
            before = copy.deepcopy(store['orchestrator'])
            fail = True
            with self.assertRaises(RuntimeError):
                board.publish_orchestrator(state)
            self.assertEqual(store['orchestrator'], before)
            view = before['snapshot_json']['orchestrator']
            for page in view['pages']:
                self.assertEqual(store[page['board_id']]['version'], page['version'])
            fail = False
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(board.publish_orchestrator, [state, state]))
            self.assertEqual(sorted(results), [2, 3])
            self.assertLessEqual(len(store), 19)
            view = store['orchestrator']['snapshot_json']['orchestrator']
            cards = view['cards'] + [c for p in view['pages'] for c in store[p['board_id']]['snapshot_json']['cards']]
            self.assertEqual(len({c['id'] for c in cards}), 1000)

    def test_large_inventory_publishes_every_card_in_bounded_readable_pages(self):
        state = {'project': 'carr-v5', 'tasks': {str(i): {
            'title': 'Engineering delivery ' + str(i) + 'x' * 180, 'stage': 'merged' if i % 2 else 'review',
            'updated_at': '2026-01-01T00:00:00Z', 'activity_status': 'stale', 'summary': 'Waiting for verified release ' + 'x' * 180,
            'repo': 'DoctorCRE/carr-system', 'pr': i + 1} for i in range(1200)}}
        state['tasks']['fresh'] = {'title': 'Fresh active work', 'stage': 'build', 'updated_at': '2026-10-21T19:00:00Z'}
        store = {}
        def call(verb, args):
            key = args['board_id']
            if verb == 'read-progress-board':
                return {'snapshot': copy.deepcopy(store.get(key))}
            self.assertLessEqual(board.snapshot_size(args['snapshot']), board.SNAPSHOT_BUDGET)
            store[key] = {'version': args['base_version'] + 1, 'snapshot_json': copy.deepcopy(args['snapshot'])}
            return {'ok': True}
        with patch.object(board, 'call_verb', side_effect=call), \
             patch.object(board, 'read_json_file', return_value={}):
            board.publish_orchestrator(state)
            view = store['orchestrator']['snapshot_json']['orchestrator']
            self.assertEqual(view['cards'][0]['title'], 'Fresh active work')
            self.assertEqual(view['total_cards'], 1201)
            cards = list(view['cards'])
            for page in view['pages']:
                cards.extend(store[page['board_id']]['snapshot_json']['cards'])
            self.assertEqual(len({card['id'] for card in cards}), 1201)
            self.assertEqual(sum(card['state'] == 'merged' for card in cards), 600)
            self.assertFalse(any(card['state'] == 'live' for card in cards))
            previous = copy.deepcopy(store['orchestrator'])
            with patch.object(board, 'call_verb', side_effect=RuntimeError('offline')):
                with self.assertRaises(RuntimeError):
                    board.publish_orchestrator(state)
            self.assertEqual(store['orchestrator'], previous)


if __name__ == '__main__':
    unittest.main()
