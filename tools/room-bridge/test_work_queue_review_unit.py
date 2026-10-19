"""Offline regression probes for PR 1969. Expected to fail on 881279f3."""
import contextlib
import importlib.util
import io
import json
import os
import signal
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

    def test_blocked_merge_retains_ownership_across_restart_until_receipt(self):
        self.merge_card('M', 1)
        self.q.begin_merge('M', actor='test', reason='provider request')
        self.q.transition('M', 'blocked', actor='test', reason='observation timeout')
        with WorkQueue(self.q.path) as restarted:
            self.assertTrue(restarted.get('M')['active'])
            with self.assertRaisesRegex(ValueError, 'completion receipt'):
                restarted.update('M', actor='test', reason='cannot replace active pin', prs=[
                    {'repo': mq.REPOS[0], 'number': 1, 'head': 'b'*40}])
            restarted.finish_merge('M', repo=mq.REPOS[0], pr=1, head='a'*40,
                                   merged=False, actor='test', reason='provider confirms closed without merge')
            self.assertFalse(restarted.get('M')['active'])
            restarted.requeue('M', stage='fixing', actor='test', reason='confirmed stopped')
            self.assertEqual(restarted.claim(eligible_desks={'sol'}, actor='test', owner={})['card_id'], 'M')

    def test_card_with_two_prs_finishes_only_after_both_verified(self):
        self.merge_card('M', 1)
        self.q.update('M', actor='test', reason='second pinned PR', prs=[
            {'repo': mq.REPOS[0], 'number': n, 'head': 'a'*40} for n in (1, 2)])
        self.q.begin_merge('M', actor='test', reason='provider request')
        for number in (1, 2):
            self.q.finish_merge('M', repo=mq.REPOS[0], pr=number, head='a'*40,
                                merged=True, actor='test', reason=f'merge {number} confirmed on main')
            self.assertEqual(self.q.get('M')['stage'], 'merging' if number == 1 else 'done')
        self.assertFalse(self.q.get('M')['active'])

    def test_partial_termination_retains_other_merge_ownership(self):
        self.merge_card('M', 1)
        self.q.update('M', actor='test', reason='two pinned PRs', prs=[
            {'repo': mq.REPOS[0], 'number': n, 'head': 'a'*40} for n in (1, 2)])
        self.q.begin_merge('M', actor='test', reason='provider requests')
        for number in (1, 2):
            self.q.finish_merge('M', repo=mq.REPOS[0], pr=number, head='a'*40,
                                merged=False, actor='test', reason=f'provider closed PR {number}')
            self.assertEqual(self.q.get('M')['active'], number == 1)
        self.assertEqual(self.q.get('M')['stage'], 'blocked')

    def test_historical_import_does_not_start_execution_or_publication(self):
        old = self.root/'old.jsonl'
        old.write_text(json.dumps({'msg_id':'old','desk':'synthetic','status':'completed'})+'\n')
        with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB':str(self.q.path)}), patch.object(work_runner, 'kick') as kick, contextlib.redirect_stdout(io.StringIO()):
            dispatch.main(['--registry',str(self.root/'registry.json'),'import-results',str(old)])
        kick.assert_not_called()
        self.assertEqual(self.q.publication()['dirty'], 0)

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
            daemon.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,dispatcher_pid,work_card) VALUES(?,?,?,?,?,?,?,?,?,?)', ('synthetic','dispatch',mq.REPOS[0],1,'a'*40,str(self.root/'brief'),'issued','sol',42424242,'conflict-card'))
        with patch.dict(os.environ, {'CARR_HERMES_DESKS':str(registry),'CARR_WORK_QUEUE_DB':str(self.q.path)}), patch.object(daemon,'expire_dispatch',return_value=False), patch.object(daemon,'dispatcher_identity',return_value=None), patch.object(mq,'command',return_value=''):
            daemon._dispatch_conflicts()
        self.assertEqual(tuple(daemon.db.execute('SELECT phase,desk FROM actions').fetchone()), ('issued','sol'))

    def test_stale_merge_completion_cannot_overwrite_requeued_executor(self):
        self.merge_card('M', 1)
        self.q.begin_merge('M', actor='test', reason='provider request')
        self.q.finish_merge('M', repo=mq.REPOS[0], pr=1, head='a'*40,
                            merged=False, actor='test', reason='provider stopped')
        self.q.requeue('M', stage='fixing', actor='test', reason='repair')
        self.q.claim(eligible_desks={'sol'}, actor='test', owner={})
        with self.assertRaisesRegex(ValueError, 'merge ownership'):
            self.q.finish_merge('M', repo=mq.REPOS[0], pr=1, head='a'*40,
                                merged=True, actor='test', reason='stale receipt')
        self.assertEqual(self.q.get('M')['stage'], 'fixing')

    def test_daemon_verifies_pinned_merge_before_completing_card(self):
        self.merge_card('M', 1)
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        state = {'number': 1, 'head': {'sha': 'a'*40, 'ref': 'synthetic'},
                 'base': {'ref': 'main'}, 'state': 'open', 'mergeable': True,
                 'mergeable_state': 'clean', 'labels': []}
        effects = []
        def merge(*args):
            self.assertTrue(self.q.get('M')['active'])
            effects.append(args)
            daemon.stopped = True
        with patch.object(daemon, 'pr', side_effect=lambda *args: dict(state)), \
             patch.object(daemon, 'behind', return_value=False), \
             patch.object(daemon, 'prepare', return_value=False), \
             patch.object(daemon, 'green', return_value=True), \
             patch.object(daemon, 'gh', side_effect=merge), \
             patch.object(daemon, 'fetch'), patch.object(daemon, 'git') as ancestry, \
             patch.object(daemon, 'refresh', return_value=True):
            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                daemon.run(poll=0, discover=False)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            self.assertEqual(len(effects), 1)
            self.assertEqual(effects[0][-2:], ('--match-head-commit', 'a'*40))
            self.assertEqual(self.q.get('M')['stage'], 'merging')
            daemon.stopped = False
            state.update(merged=True, merge_commit_sha='b'*40, state='closed')
            daemon.tick()
            ancestry.assert_called_once_with(mq.REPOS[0], 'merge-base', '--is-ancestor', 'b'*40, 'origin/main')
        self.assertEqual(self.q.get('M')['stage'], 'done')
        self.assertFalse(self.q.get('M')['active'])
        self.assertEqual(len([e for e in self.q.export()['history'] if e['kind'] == 'merge_started']), 1)

    def test_unprojected_journal_and_unreadable_order_cannot_select_merges(self):
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        daemon.enqueue(mq.REPOS[0], 1, 'a'*40)
        with patch.object(daemon, 'pr') as read:
            daemon.tick()
            read.assert_not_called()
            with patch.dict(os.environ, {'CARR_WORK_QUEUE_DB': str(self.root/'absent.sqlite3')}):
                with self.assertRaises(mq.sqlite3.OperationalError):
                    daemon.tick()
            read.assert_not_called()

    def test_durable_dispatch_retains_blocked_worker_then_consumes_completion(self):
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        self.q.enqueue('fix', desk='sol', task='synthetic', actor='test')
        with daemon.db:
            daemon.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,work_card) VALUES(?,?,?,?,?,?,?,?,?)',
                              ('fix', 'dispatch', mq.REPOS[0], 1, 'a'*40, 'synthetic', 'issued', 'sol', 'fix'))
        claim = self.q.claim(eligible_desks={'sol'}, actor='test', owner={})
        self.q.finish('fix', claim['claim'], {'status': 'failed', 'termination_confirmed': False}, actor='test')
        with patch.object(daemon, 'dispatcher_identity', side_effect=AssertionError('sender is not the worker')):
            daemon._dispatch_conflicts()
            self.assertEqual(tuple(daemon.db.execute('SELECT phase,desk FROM actions').fetchone()), ('issued', 'sol'))
            self.q.finish('fix', claim['claim'], {'status': 'completed', 'msg_id': 'verified'}, actor='test')
            daemon._dispatch_conflicts()
        self.assertEqual(tuple(daemon.db.execute('SELECT phase,desk FROM actions').fetchone()), ('done', None))

    def test_previous_card_result_is_not_a_receipt_for_a_new_dispatch(self):
        self.merge_card('M', 1)
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        with daemon.db:
            daemon.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,work_card,work_after) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              ('fix', 'dispatch', mq.REPOS[0], 1, 'a'*40, 'synthetic', 'issued', 'sol', 'M', self.q.export()['revision']))
        daemon._dispatch_conflicts()
        self.assertEqual(tuple(daemon.db.execute('SELECT phase,desk FROM actions').fetchone()), ('issued', 'sol'))
        self.q.requeue('M', stage='fixing', actor='test', reason='accepted revision')
        claim = self.q.claim(eligible_desks={'sol'}, actor='test', owner={})
        self.q.finish('M', claim['claim'], {'status': 'completed', 'msg_id': 'new-receipt'}, actor='test')
        daemon._dispatch_conflicts()
        self.assertEqual(tuple(daemon.db.execute('SELECT phase,desk FROM actions').fetchone()), ('done', None))

    def test_conflict_dispatch_uses_original_card_for_revision(self):
        self.merge_card('M', 1)
        daemon = mq.Queue(self.root/'daemon', root=ROOT)
        self.addCleanup(daemon.db.close)
        brief = self.root/'brief.txt'
        brief.write_text('synthetic')
        registry = self.root/'registry.json'
        registry.write_text(json.dumps({'desks': {'sol': {
            'kind': 'codex-session', 'model': 'gpt-6-sol', 'effort': 'high',
            'sandbox': 'workspace-write', 'last_auth': True, 'thread_id': None}}}))
        with daemon.db:
            daemon.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',
                              ('fix', 'dispatch', mq.REPOS[0], 1, 'a'*40, str(brief)))
        with patch.dict(os.environ, {'CARR_HERMES_DESKS': str(registry)}), \
             patch.object(daemon, 'pr', return_value={'labels': [], 'head': {'sha': 'a'*40}, 'state': 'open'}), \
             patch.object(daemon, 'dispatcher_identity', return_value=None), \
             patch.object(mq, 'command', return_value=''), patch.object(mq.subprocess, 'Popen') as sender:
            sender.return_value.pid = 12345
            daemon._dispatch_conflicts()
        argv = sender.call_args.args[0]
        self.assertEqual(argv[argv.index('--card-id')+1], 'M')
        self.assertEqual(argv[argv.index('--stage')+1], 'fixing')

if __name__ == '__main__':
    unittest.main(verbosity=2)
