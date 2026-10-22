"""Offline regression probes for PR 1969. Expected to fail on 881279f3."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
from work_queue import WorkQueue
from work_runner import Runner, Resources
import dispatch
import work_runner
class Probes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(self.root / 'work.sqlite3'),
                                     'PROGRESS_BOARD_LOCAL_ONLY': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.q = WorkQueue(self.root / 'work.sqlite3')
        self.addCleanup(self.q.close)


    def test_priority_committed_before_claim_controls_next_launch(self):
        for card in ('A','B'):
            self.q.enqueue(card, desk='sol', task='synthetic', actor='review')
        resources = Resources(self.root/'locks', self.root/'slots', limit=1)
        original = resources.acquire
        def acquire(*args, **kwargs):
            handles = original(*args, **kwargs)
            self.q.prioritize('B', actor='review', reason='committed before claim')
            return handles
        resources.acquire = acquire
        launched = []
        Runner(self.q, resources, launch=lambda card, handles: launched.append(card['card_id']), terminated=lambda owner:False).tick()
        self.assertEqual(launched, ['B'])


    def test_historical_import_does_not_start_execution_or_publication(self):
        old = self.root/'old.jsonl'
        old.write_text(json.dumps({'msg_id':'old','desk':'synthetic','status':'completed'})+'\n')
        with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB':str(self.q.path)}), patch.object(work_runner, 'kick') as kick, contextlib.redirect_stdout(io.StringIO()):
            dispatch.main(['--registry',str(self.root/'registry.json'),'import-results',str(old)])
        kick.assert_not_called()
        self.assertEqual(self.q.publication()['dirty'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
