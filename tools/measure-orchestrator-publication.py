"""Read canonical engineering metadata; print counts and sizes, never card content."""
import copy
import json
from pathlib import Path
import sys
import time
from typing import Any
from unittest.mock import patch
import progress_board as board
sys.path.insert(0, str(Path(__file__).parent / 'room-bridge'))
from work_queue import board_projection
from progress_orchestrator import project

start = time.monotonic()
root = Path('/Users/booko/carr-system/out/boards')
state = json.loads((root / 'carr-v5.json').read_text())
portfolio = json.loads((root / 'all-repos.json').read_text())
queue = board_projection('/Users/booko/.config/carr/work-queue.sqlite3')
raw = project(state, queue, portfolio=portfolio)
store: dict[str, Any] = {}
calls: list[str] = []
def call(verb, args):
    calls.append(verb)
    key = args['board_id']
    if verb == 'read-progress-board':
        return {'snapshot': copy.deepcopy(store.get(key))}
    assert board.snapshot_size(args['snapshot']) <= board.SNAPSHOT_BUDGET
    store[key] = {'version': args['base_version'] + 1, 'snapshot_json': copy.deepcopy(args['snapshot'])}
    return {'ok': True}
with patch.object(board, 'call_verb', side_effect=call), patch.object(board, 'read_json_file', return_value=portfolio), patch.object(board.work_queue_module(), 'board_projection', return_value=queue):
    board.publish_orchestrator(state)
    initial_calls, initial_writes = len(calls), calls.count('publish-board-snapshot')
    for i in range(10):
        state['tasks'][list(state['tasks'])[i]]['updated_at'] = f'2026-10-21T20:00:{i:02d}Z'
        board.publish_orchestrator(state)
view = store['orchestrator']['snapshot_json']['orchestrator']
cards = view['cards'] + [card for page in view['pages'] for card in store[page['board_id']]['snapshot_json']['cards']]
assert len({c['id'] for c in cards}) == len(raw['cards']) == view['total_cards']
legacy = None
try:
    board.board_snapshot(copy.deepcopy(state), costs={})
except board.SnapshotTooLarge as error:
    legacy = str(error)
print(json.dumps({'observed_at': raw['observed_at'], 'raw_active': len(raw['cards']), 'raw_history': len(raw['history']), 'raw_chars': board.snapshot_size(raw),
    'queue_items': len(queue['items']), 'queue_chars': board.snapshot_size(queue), 'active_preserved': len(cards), 'first_page': len(view['cards']),
    'history': len(view['history']), 'omitted_completed_history': view['omitted_history'], 'state_counts': view['state_counts'],
    'manifest_chars': board.snapshot_size(store['orchestrator']['snapshot_json']), 'extra_page_chars': [board.snapshot_size(store[p['board_id']]['snapshot_json']) for p in view['pages']],
    'stored_rows_after_10_updates': len(store), 'first_cycle_calls': initial_calls, 'first_cycle_writes': initial_writes, 'seconds': round(time.monotonic()-start, 3), 'legacy_failure': legacy}, indent=2))
