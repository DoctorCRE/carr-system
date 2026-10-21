"""Same-card merge recovery through the journal and durable queue interfaces."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_merge_queue as fixture

module = fixture.module


class MergeRecoveryTests(unittest.TestCase):
    setUp = fixture.QueueTests.setUp
    bind_work_queue = fixture.QueueTests.bind_work_queue
    git = fixture.QueueTests.git
    save = fixture.QueueTests.save
    load = fixture.QueueTests.load
    pr = fixture.QueueTests.pr
    calls = fixture.QueueTests.calls

    def blocked_card(self, *, inflight=False):
        self.card_id = 'original-product-card'
        self.pins = [dict(repo=module.REPOS[1], number=n, head=self.updated) for n in (298, 300, 305)]
        self.pr(298, repo=module.REPOS[1], head=self.updated)
        self.pr(300, repo=module.REPOS[1], head=self.updated)['state'] = 'closed'
        self.save()
        self.pr(305, repo=module.REPOS[1], head=self.updated, base='branch-300')
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.enqueue(self.card_id, desk='source', task='Original product work', actor='test', prs=self.pins)
            claim = work.claim(eligible_desks={'source'}, actor='test', owner={})
            work.finish(self.card_id, claim['claim'], {'status': 'completed'}, actor='test')
            work.requeue(self.card_id, stage='review', actor='test', reason='source review')
            claim = work.claim(eligible_desks={'source'}, actor='test', owner={})
            work.finish(self.card_id, claim['claim'], {'status': 'completed'}, actor='reviewer')
            work.transition(self.card_id, 'merging', actor='reviewer', reason='exact heads approved')
        self.q.tick()
        self.q.tick()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            if inflight:
                work.begin_merge(self.card_id, pin=self.pins[2], actor='test', reason='genuine ownership')
            work.transition(self.card_id, 'blocked', actor='test', reason='closed PR needs source repair')
        self.load()['prs'][module.REPOS[1] + '#300'].update(state='open', mergeable=False, mergeable_state='dirty')
        self.save()

    def snapshot(self):
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            snapshot = work.export()
        for card in snapshot['items']:
            card.pop('total_time_s')  # Running elapsed time is an observation, not a mutation.
        return snapshot

    def recover(self, revision=None):
        return self.q.recover_work_card(self.card_id,
                                       expected_revision=self.snapshot()['revision'] if revision is None else revision,
                                       actor='orchestrator', reason='same-card repair')

    def assert_refused(self, message):
        before = self.snapshot()
        with self.assertRaisesRegex((module.QueueChanged, RuntimeError, ValueError), message):
            self.recover(before['revision'])
        self.assertEqual(self.snapshot(), before)

    def test_incomplete_provider_truth_retains_ownership(self):
        self.blocked_card()
        del self.load()['prs'][module.REPOS[1] + '#305']['merged']
        self.save()
        self.assert_refused('incomplete provider')

    def test_inflight_pin_retains_ownership(self):
        self.blocked_card(inflight=True)
        self.assert_refused('in-flight')

    def test_legacy_unknown_ownership_refuses(self):
        self.blocked_card()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            with work.db:
                work.db.execute("UPDATE items SET data=json_remove(data, '$.merge_inflight') WHERE card_id=?", (self.card_id,))
        self.assert_refused('legacy_ownership')

    def test_source_claim_refuses(self):
        self.blocked_card()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            # Synthetic persisted legacy state: never mutate an operational queue.
            with work.db:
                work.db.execute('UPDATE items SET claim=? WHERE card_id=?', ('source-owner', self.card_id))
        self.assert_refused('source claim')

    def test_provider_queued_or_automatic_merge_refuses(self):
        self.blocked_card()
        self.load()['server_queued'] = True
        self.save()
        self.assert_refused('provider still owns')

    def test_provider_read_failure_refuses(self):
        self.blocked_card()
        self.load()['api_failure'] = dict(path=f'repos/{module.REPOS[1]}/pulls/305', status=403)
        self.save()
        self.assert_refused('403')

    def test_issued_merge_intent_refuses_even_with_empty_card_ownership(self):
        self.blocked_card()
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=? WHERE pr=305", (self.updated,))
        self.assert_refused('unresolved merge journal')

    def test_unknown_journal_effect_state_refuses(self):
        self.blocked_card()
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='uncertain' WHERE pr=305")
        self.assert_refused('unresolved merge journal')

    def test_issued_or_uncertain_regeneration_refuses(self):
        self.blocked_card()
        for phase in ('issued', 'uncertain', 'planned', 'exhausted'):
            with self.subTest(phase=phase):
                with self.q.db:
                    self.q.db.execute('INSERT OR REPLACE INTO actions(key,kind,repo,pr,head,payload,phase) VALUES(?,?,?,?,?,?,?)',
                                      ('regen-fixture', 'regen', module.REPOS[1], 305, self.updated, self.main, phase))
                self.assert_refused('unresolved provider action')

    def test_concurrent_work_order_change_refuses(self):
        self.blocked_card()
        revision = self.snapshot()['revision']
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.prioritize(self.card_id, actor='other-session', reason='concurrent order change')
        before = self.snapshot()
        with self.assertRaisesRegex(module.QueueChanged, 'work queue changed'):
            self.recover(revision)
        self.assertEqual(self.snapshot(), before)

    def test_running_daemon_lock_refuses_recovery(self):
        self.blocked_card()
        other = module.Queue(self.state, self.root, gap=0)
        self.addCleanup(other.db.close)
        before = self.snapshot()
        with other.runner_lock(), self.assertRaises(BlockingIOError):
            self.recover()
        self.assertEqual(self.snapshot(), before)

    def test_stale_daemon_order_cannot_own_a_later_run_of_the_same_pins(self):
        self.blocked_card()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            old_order = work.merge_order()['entries']
        self.recover()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.requeue(self.card_id, stage='review', actor='test', reason='new review')
            work.transition(self.card_id, 'merging', actor='test', reason='new merge run')
            before = work.export()['revision']
            with self.assertRaises(module.QueueChanged):
                work.begin_merge(self.card_id, pin=self.pins[2], expected_order=old_order,
                                 actor='stale-daemon', reason='old run must not acquire ownership')
            with self.assertRaises(module.QueueChanged):
                work.advance_merge_pin(self.card_id, repo=module.REPOS[1], pr=305,
                                       head=self.updated, prepared=self.changed, main=self.main,
                                       actor='stale-daemon', reason='old regeneration must not repin',
                                       expected_order=old_order)
            self.assertEqual(work.export()['revision'], before)

    def test_cli_uses_guarded_operation_and_stale_retry_refuses(self):
        self.blocked_card()
        revision = self.snapshot()['revision']
        argv = [sys.executable, str(fixture.ENTRY), '--state', str(self.state), '--root', str(self.root),
                'recover-work-card', self.card_id, '--expected-revision', str(revision),
                '--actor', 'test-operator', '--reason', 'synthetic CLI verification']
        result = subprocess.run(argv, env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['active'])
        before = self.snapshot()
        retry = subprocess.run(argv, env=self.env, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(retry.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_recover_stale_run_preserves_unstarted_pin_and_all_evidence(self):
        self.blocked_card()
        before = self.snapshot()
        journal_before = self.q.status()
        card = before['items'][0]
        self.assertTrue(card['active'])
        self.assertIsNone(card['claim'])
        self.assertEqual(card['merge_inflight'], [])
        self.assertEqual(card['merge_receipts'], self.pins[:1])
        self.assertEqual(card['merge_stopped'], self.pins[1:2])
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            stale_order = work.merge_order()['entries']
            with self.assertRaisesRegex(ValueError, 'active executor'):
                work.requeue(self.card_id, stage='fixing', actor='test', reason='blocked repro')
        recovered = self.q.recover_work_card(self.card_id, expected_revision=before['revision'],
                                             actor='orchestrator', reason='repair original card')
        self.assertFalse(recovered['active'])
        self.assertEqual(recovered['stage'], 'blocked')
        for key in ('prs', 'merge_receipts', 'merge_stopped', 'findings', 'review_round'):
            self.assertEqual(recovered[key], card[key])
        after = self.snapshot()
        self.assertEqual(after['history'][:-1], before['history'])
        self.assertEqual(after['history'][-1]['kind'], 'merge_run_recovered')
        self.assertEqual(after['history'][-1]['actor'], 'orchestrator')
        self.assertEqual(self.q.status(), journal_before)
        self.assertTrue(all(s['finished_at'] for s in recovered['stages'] if s['stage'] == 'merging'))
        self.assertEqual(self.load()['prs'][module.REPOS[1] + '#305']['state'], 'open')
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            self.assertEqual(work.merge_order()['entries'], [])
            work.requeue(self.card_id, stage='fixing', actor='test', reason='explicit same-card repair')
            with self.assertRaises(module.QueueChanged):
                work.begin_merge(self.card_id, pin=self.pins[2], expected_order=stale_order,
                                 actor='stale-daemon', reason='must refuse')
        self.assertEqual(len(self.calls('merge')), 1)


if __name__ == '__main__':
    unittest.main()
