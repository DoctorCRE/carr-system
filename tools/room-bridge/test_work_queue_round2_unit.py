"""Offline review probes for carr-system PR 1969 head e5731019."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/room-bridge'))
import test_work_queue_review_unit as review
mq = review.mq

class Round2(unittest.TestCase):
    setUp = review.Probes.setUp
    merge_card = review.Probes.merge_card

    def test_active_merge_done_transition_cannot_release_without_receipt(self):
        self.merge_card('M', 1)
        self.q.begin_merge('M', actor='test', reason='provider still owns effect')
        try:
            self.q.transition('M', 'done', actor='test', reason='display stage only')
        except ValueError:
            pass
        self.assertTrue(self.q.get('M')['active'], 'stage command released merge without a receipt')

    def test_legacy_unbound_planned_action_cannot_authorize_new_work(self):
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        brief = self.root/'brief.txt'
        brief.write_text('synthetic')
        registry = self.root/'registry.json'
        registry.write_text(json.dumps({'desks': {'sol': {
            'kind':'codex-session', 'model':'gpt-6-sol', 'effort':'high',
            'sandbox':'workspace-write', 'last_auth':True, 'thread_id':None}}}))
        with daemon.db:
            daemon.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',
                              ('legacy-fix','dispatch',mq.REPOS[0],1,'a'*40,str(brief)))
        self.assertEqual(self.q.export()['items'], [])
        with patch.dict(os.environ, {'CARR_HERMES_DESKS':str(registry)}), \
             patch.object(daemon,'pr',return_value={'labels':[],'head':{'sha':'a'*40},'state':'open'}), \
             patch.object(daemon,'dispatcher_identity',return_value=None), \
             patch.object(mq,'command',return_value=''), patch.object(mq.subprocess,'Popen') as sender:
            sender.return_value.pid=12345
            daemon._dispatch_conflicts()
        sender.assert_not_called()
        self.assertEqual(self.q.export()['items'], [])
        self.assertIsNotNone(daemon.db.execute("SELECT 1 FROM events WHERE outcome='dispatch_unbound'").fetchone())

    def test_merge_revalidates_priority_before_provider_effect(self):
        self.merge_card('A',1)
        self.merge_card('B',2)
        daemon=mq.Queue(self.root/'daemon',root=ROOT)
        self.addCleanup(daemon.db.close)
        def pr(repo,number):
            return {'number':number,'head':{'sha':'a'*40,'ref':'synthetic'},
                    'base':{'ref':'main'},'state':'open','mergeable':True,
                    'mergeable_state':'clean','labels':[]}
        effects=[]
        def green(*args):
            self.q.prioritize('B',actor='test',reason='committed before merge starts')
            return True
        def merge(*args):
            effects.append(int(args[2]))
            daemon.stopped=True
        with patch.object(daemon,'pr',side_effect=pr), patch.object(daemon,'behind',return_value=False), \
             patch.object(daemon,'prepare',return_value=False), patch.object(daemon,'green',side_effect=green), \
             patch.object(daemon,'gh',side_effect=merge):
            daemon.tick()
            self.assertEqual(effects, [])
            daemon.tick()
        self.assertEqual(effects, [2])
        self.assertNotIn(1,effects, 'stale selection merged after priority changed before begin_merge')

if __name__=='__main__':
    suite=unittest.TestSuite(Round2(name) for name in [
      'test_active_merge_done_transition_cannot_release_without_receipt',
      'test_legacy_unbound_planned_action_cannot_authorize_new_work',
      'test_merge_revalidates_priority_before_provider_effect'])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
