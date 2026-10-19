"""Offline regression probes for PR 1969. Expected to fail on 881279f3."""
import contextlib
import importlib.util
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
spec = importlib.util.spec_from_file_location('review_merge_queue', ROOT / 'tools/merge_queue/main.py')
mq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mq)

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

    def merge_card(self, card, number):
        self.q.enqueue(card, desk='sol', task='synthetic', actor='review')
        claim = self.q.claim(eligible_desks={'sol'}, card_id=card, actor='review', owner={})
        self.q.finish(card, claim['claim'], {'status':'completed'}, actor='review')
        self.q.update(card, actor='review', reason='pin', prs=[{'repo':mq.REPOS[0], 'number':number, 'head':'a'*40}])
        self.q.transition(card, 'merging', actor='review', reason='approved')

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

    def test_started_merge_cannot_be_requeued_without_stopping(self):
        self.merge_card('M', 1)
        self.q.begin_merge('M', actor='review', reason='provider still running')
        self.q.transition('M', 'blocked', actor='review', reason='no stop or completion observed')
        with self.assertRaisesRegex(ValueError, 'completion receipt'):
            self.q.requeue('M', stage='fixing', actor='review', reason='must retain active reservation')

    def test_historical_import_does_not_start_execution_or_publication(self):
        old = self.root/'old.jsonl'
        old.write_text(json.dumps({'msg_id':'old','desk':'synthetic','status':'completed'})+'\n')
        with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB':str(self.q.path)}), patch.object(work_runner, 'kick') as kick, contextlib.redirect_stdout(io.StringIO()):
            dispatch.main(['--registry',str(self.root/'registry.json'),'import-results',str(old)])
        kick.assert_not_called()

    def test_daemon_selects_current_global_merge_order(self):
        self.merge_card('A', 1)
        self.merge_card('B', 2)
        self.q.prioritize('B', actor='review', reason='next merge')
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        with daemon.db:
            for n in (1,2):
                daemon.db.execute('INSERT INTO entries(repo,pr,approved,note) VALUES(?,?,?,?)', (mq.REPOS[0],n,'a'*40,'synthetic'))
        selected = []
        class Selected(BaseException): pass
        def pr(repo, number):
            selected.append(number)
            raise Selected()
        with patch.object(daemon, 'pr', side_effect=pr), self.assertRaises(Selected):
            daemon.tick()
        self.assertEqual(selected, [self.q.merge_order()['entries'][0]['pr']])

    def test_queued_conflict_work_is_not_misclassified_as_orphaned(self):
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        registry = self.root/'registry.json'
        registry.write_text(json.dumps({'desks':{}}))
        self.q.enqueue('conflict-card', desk='sol', task='synthetic', actor='review')
        with daemon.db:
            daemon.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,dispatcher_pid) VALUES(?,?,?,?,?,?,?,?,?)', ('synthetic','dispatch',mq.REPOS[0],1,'a'*40,str(self.root/'brief'),'issued','sol',42424242))
        with patch.dict(os.environ, {'CARR_HERMES_DESKS':str(registry),'CARR_WORK_QUEUE_DB':str(self.q.path)}), patch.object(daemon,'expire_dispatch',return_value=False), patch.object(daemon,'dispatcher_identity',return_value=None), patch.object(mq,'command',return_value=''):
            daemon._dispatch_conflicts()
        self.assertEqual(tuple(daemon.db.execute('SELECT phase,desk FROM actions').fetchone()), ('issued','sol'))

if __name__ == '__main__':
    unittest.main(verbosity=2)
