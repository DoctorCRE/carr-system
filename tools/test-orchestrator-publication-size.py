import copy
import json
import unittest
from unittest.mock import patch
import progress_board as board


class PublicationBoundaryTests(unittest.TestCase):
    def test_large_inventory_publishes_every_card_in_bounded_readable_pages(self):
        state = {'project': 'carr-v5', 'tasks': {str(i): {
            'title': 'Engineering delivery ' + str(i) + 'x' * 180, 'stage': 'merged' if i % 2 else 'review',
            'updated_at': '2026-01-01T00:00:00Z', 'summary': 'Waiting for verified release ' + 'x' * 180,
            'repo': 'jbookout/carr-system', 'pr': i + 1} for i in range(1200)}}
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
             patch.object(board, 'read_json_file', return_value={}), \
             patch.object(board.work_queue_module(), 'board_projection', return_value={'items': []}):
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
