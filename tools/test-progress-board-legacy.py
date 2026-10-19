#!/usr/bin/env python3
"""Legacy disposition through the CLI and published History contract."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import progress_board as board

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/progress-board-legacy.json').read_text())
AT = datetime(2026, 10, 19, 11, 29, 41, tzinfo=timezone.utc)


class LegacyDispositionTests(unittest.TestCase):
    def test_merged_legacy_note_moves_to_history_without_weakening_new_proof(self):
        task = copy.deepcopy(FIXTURE['tasks']['cloud-jev-codereview'])
        state = {'project': 'test', 'tasks': {'old': task, 'new': {
            'title': 'New completion', 'created_at': '2026-10-19T10:00:00Z',
            'updated_at': '2026-10-19T10:00:00Z', 'status': 'done',
            'note': 'PR 1408'}}}
        facts = {('jbookout/carr-system', 1408): (FIXTURE['github']['jbookout/carr-system#1408'], None)}
        with patch.object(board, 'now_utc', return_value=AT):
            result, report = board.dispose_legacy(state, facts, AT)
            snapshot = board.board_snapshot(result, costs={})
        self.assertEqual(report['counts'], {'done': 1, 'dropped': 0, 'legacy, unverified': 0, 'kept': 1})
        self.assertIn('old', state['tasks'])
        self.assertNotIn('old', snapshot['tasks'])
        self.assertEqual(snapshot['history']['old']['status'], 'done')
        receipt = snapshot['history']['old']['reason']
        self.assertIn('legacy pre-proof card verified by GitHub state', receipt)
        self.assertIn('jbookout/carr-system#1408', receipt)
        self.assertIn(FIXTURE['github']['jbookout/carr-system#1408']['mergeCommit']['oid'], receipt)
        self.assertEqual(snapshot['tasks']['new']['status'], 'review')
        again, _ = board.dispose_legacy(result, facts, AT)
        self.assertEqual(again, result)


if __name__ == '__main__':
    unittest.main()
