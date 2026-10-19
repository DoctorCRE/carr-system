"""The durable work queue's public interface, without live models or board writes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_queue import WorkQueue


class WorkQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'work.sqlite3'
        self.q = WorkQueue(self.path)
        self.addCleanup(self.q.close)

    def add(self, card, desk='sol', **fields):
        return self.q.enqueue(card, desk=desk, task='Build the card', actor='test', **fields)

    def test_one_order_survives_restart_and_prioritization_is_audited(self):
        self.add('A')
        self.add('B', desk='luna')
        self.add('C')
        self.q.prioritize('C', before='B', actor='orchestrator', reason='product dependency')
        with WorkQueue(self.path) as reopened:
            snapshot = reopened.export()
        self.assertEqual([i['card_id'] for i in snapshot['items']], ['A', 'C', 'B'])
        event = snapshot['items'][1]['last_event']
        self.assertEqual((event['actor'], event['reason']), ('orchestrator', 'product dependency'))
        self.assertEqual((event['from']['position'], event['to']['position']), (3, 2))
        self.assertEqual(len(snapshot['history']), 4)
        self.assertTrue(event['at'])

    def test_claim_filters_global_order_and_requeue_goes_to_tail(self):
        self.add('A')
        self.add('B')
        self.add('C', desk='luna')
        claimed = self.q.claim(eligible_desks={'sol'}, actor='runner', owner={'pid': 42})
        self.assertEqual(claimed['card_id'], 'A')
        self.assertIsNone(self.q.claim(eligible_desks={'sol'}, actor='runner', owner={'pid': 43}))
        self.assertEqual(self.q.claim(eligible_desks={'luna'}, actor='runner', owner={})['card_id'], 'C')
        self.q.finish('A', claimed['claim'], {'status': 'completed', 'model': 'actual-sol'}, actor='worker')
        self.assertEqual(self.q.get('A')['stage'], 'review')
        self.q.requeue('A', stage='fixing', actor='reviewer', reason='P1 fix required')
        self.assertEqual([i['card_id'] for i in self.q.export()['items']], ['B', 'C', 'A'])
        self.assertEqual(self.q.claim(eligible_desks={'sol'}, actor='runner', owner={})['card_id'], 'B')
        card = self.q.get('A')
        self.assertEqual(card['model'], 'actual-sol')
        self.assertIsNotNone(card['stages'][0]['finished_at'])
        self.assertEqual(card['stages'][-1]['stage'], 'fixing')
        self.assertTrue(card['ready'])

    def test_card_details_review_cap_and_merge_export_follow_one_order(self):
        self.add('A', wr='WR-pending', slice='S1', title='Queue', description='Durable cards')
        self.add('B')
        build = self.q.claim(eligible_desks={'sol'}, actor='runner', owner={})
        self.q.finish('A', build['claim'], {'status': 'completed'}, actor='worker')
        for round_number in range(1, 4):
            self.q.requeue('A', stage='review', actor='test', reason='review', task='Review code')
            review = self.q.claim(eligible_desks={'sol'}, card_id='A', actor='runner', owner={})
            self.q.finish('A', review['claim'], {'status': 'completed'}, actor='reviewer')
            self.assertEqual(self.q.get('A')['review_round'], round_number)
        with self.assertRaisesRegex(ValueError, 'three review'):
            self.q.requeue('A', stage='review', actor='test', reason='fourth')
        self.q.update('A', actor='reviewer', reason='findings', findings=[
            {'id': 'f1', 'level': 'P1', 'summary': 'Lost ordering', 'resolved': False}],
            prs=[{'repo': 'jbookout/carr-system', 'number': 1960, 'head': 'a' * 40}], ci_state='passed')
        self.q.update('A', actor='fixer', reason='fixed', findings=[
            {'id': 'f1', 'level': 'P1', 'summary': 'Lost ordering', 'resolved': True}])
        self.q.transition('A', 'merging', actor='orchestrator', reason='reviews passed')
        merging = self.q.merge_order()
        self.assertEqual(merging['schema'], 'room-work-queue-merges/v1')
        self.assertEqual([(e['card_id'], e['pr'], e['head']) for e in merging['entries']], [('A', 1960, 'a' * 40)])
        card = self.q.get('A')
        self.assertEqual((card['findings_opened'], card['findings_resolved'], card['merge_position']), (1, 1, 1))
        self.assertGreaterEqual(card['total_time_s'], 0)
        self.assertTrue(any(e['reason'] == 'findings' for e in self.q.export()['history']))

    def test_migration_is_idempotent_and_publish_retries_without_losing_events(self):
        results = Path(self.tmp.name) / 'dispatch-results.jsonl'
        results.write_text(json.dumps({'msg_id': 'old-1', 'desk': 'sol', 'task': 'old job',
            'dispatched_at': '2026-10-18T12:00:00Z', 'status': 'completed', 'model': 'actual'}) + '\n')
        self.assertEqual(self.q.import_results(results, actor='migration')['imported'], 1)
        self.assertEqual(self.q.import_results(results, actor='migration')['imported'], 0)
        old = self.q.export()['items'][0]
        self.assertEqual((old['stage'], old['model']), ('done', 'actual'))
        self.assertFalse(old['ready'])
        published = []
        def fail():
            raise RuntimeError('board offline')
        self.assertFalse(self.q.publish(fail, now=10))
        self.assertEqual(self.q.publication()['error'], 'board offline')
        self.add('new')
        self.assertFalse(self.q.publish(lambda: published.append(self.q.export()), now=11))
        self.assertTrue(self.q.publish(lambda: published.append(self.q.export()), now=12))
        self.assertEqual(len(published), 1)
        self.assertEqual(self.q.publication()['published'], self.q.export()['revision'])
        self.assertIsNone(self.q.publication()['error'])

    def test_dead_executor_is_blocked_and_never_automatically_replayed(self):
        self.add('A')
        claim = self.q.claim(eligible_desks={'sol'}, actor='runner', owner={'pid': 123})
        self.assertEqual(self.q.recover(lambda owner: True, actor='runner'), ['A'])
        self.assertEqual(self.q.get('A')['stage'], 'blocked')
        self.assertIsNone(self.q.claim(eligible_desks={'sol'}, actor='runner', owner={}))
        with self.assertRaisesRegex(ValueError, 'completion receipt'):
            self.q.requeue('A', stage='fixing', actor='test', reason='unsafe retry')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.q.finish('A', 'old-token', {'status': 'completed'}, actor='worker')
        self.q.finish('A', claim['claim'], {'status': 'completed'}, actor='reconciler')
        self.assertFalse(self.q.get('A')['active'])

    def test_uncertain_executor_retains_reservation_and_merge_requires_pinned_heads(self):
        self.add('A')
        claim = self.q.claim(eligible_desks={'sol'}, actor='runner', owner={})
        self.q.finish('A', claim['claim'], {'status': 'failed', 'termination_confirmed': False}, actor='worker')
        self.assertTrue(self.q.get('A')['active'])
        self.assertEqual(self.q.get('A')['stage'], 'blocked')
        self.q.finish('A', claim['claim'], {'status': 'completed'}, actor='reconciler')
        with self.assertRaisesRegex(ValueError, 'pinned'):
            self.q.transition('A', 'merging', actor='test', reason='missing pin')

    def test_findings_updates_preserve_previously_opened_findings(self):
        self.add('A')
        self.q.update('A', actor='reviewer', reason='round one', findings=[
            {'id': 'f1', 'level': 'P1', 'summary': 'first', 'resolved': False}])
        self.q.update('A', actor='reviewer', reason='round two', findings=[
            {'id': 'f2', 'level': 'P2', 'summary': 'second', 'resolved': False}])
        self.assertEqual(self.q.get('A')['findings_opened'], 2)

    def test_async_delivery_reserves_desk_until_explicit_completion(self):
        self.add('A')
        claim = self.q.claim(eligible_desks={'sol'}, actor='runner', owner={})
        self.q.finish('A', claim['claim'], {'status': 'delivered', 'session_id': 'remote'}, actor='worker')
        self.assertEqual(self.q.recover(lambda owner: True, actor='runner'), [])
        self.assertEqual(self.q.get('A')['stage'], 'building')
        self.assertTrue(self.q.get('A')['active'])

    def test_requeue_fixing_includes_findings_in_executor_instruction(self):
        self.add('A')
        self.q.update('A', actor='reviewer', reason='P1', findings=[
            {'id': 'f1', 'level': 'P1', 'summary': 'Preserve order', 'resolved': False}])
        self.q.requeue('A', stage='fixing', actor='orchestrator', reason='revision')
        task = self.q.candidates()[0]['task']
        self.assertIn('fixing', task)
        self.assertIn('Preserve order', task)

    def test_duplicate_intake_and_invalid_metadata_fail_without_mutation(self):
        self.add('A')
        revision = self.q.export()['revision']
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.add('A')
        with self.assertRaisesRegex(ValueError, 'list'):
            self.q.update('A', actor='test', reason='bad data', findings='P1')
        self.assertEqual(self.q.export()['revision'], revision)

    def test_board_projection_bounds_finished_history_and_keeps_all_active_cards(self):
        from work_queue import board_projection
        results = Path(self.tmp.name) / 'old.jsonl'
        results.write_text(''.join(json.dumps({'msg_id': str(i), 'desk': 'old', 'status': 'completed'}) + '\n' for i in range(40)))
        self.q.import_results(results, actor='migration')
        self.add('active')
        snapshot = board_projection(self.path)
        self.assertEqual(len(snapshot['items']), 21)
        self.assertEqual(snapshot['items'][-1]['card_id'], 'active')
        self.assertEqual(snapshot['omitted']['finished'], 20)
        self.assertEqual(len(self.q.export()['items']), 41)

    def test_concurrent_processes_cannot_claim_the_same_desk_twice(self):
        import subprocess
        self.add('A')
        self.add('B')
        script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from work_queue import WorkQueue
with WorkQueue(Path(sys.argv[2])) as q:
    row = q.claim(eligible_desks={'sol'}, actor='runner', owner={})
    print(json.dumps(row['card_id'] if row else None))
"""
        args = [sys.executable, '-c', script, str(Path(__file__).resolve().parent), str(self.path)]
        children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        results = []
        for child in children:
            out, err = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, err)
            results.append(json.loads(out))
        self.assertEqual(results.count('A'), 1)
        self.assertEqual(results.count(None), 3)

    def test_publish_does_not_acknowledge_a_change_made_during_publication(self):
        self.add('A')
        self.assertTrue(self.q.publish(lambda: self.add('B'), now=10))
        publication = self.q.publication()
        self.assertLess(publication['published'], publication['dirty'])
        self.assertTrue(self.q.publish(lambda: None, now=12))
        self.assertEqual(self.q.publication()['published'], self.q.export()['revision'])

    def test_read_projection_opens_an_existing_store_without_write_access(self):
        import sqlite3
        self.add('A')
        with WorkQueue(self.path, read_only=True) as reader:
            self.assertEqual(reader.export()['items'][0]['card_id'], 'A')
            with self.assertRaises(sqlite3.OperationalError):
                reader.enqueue('B', desk='sol', task='refused write', actor='test')
        self.assertEqual(len(self.q.export()['items']), 1)

    def test_merge_executor_start_is_idempotent_and_records_stage_timing(self):
        self.add('A')
        claim = self.q.claim(eligible_desks={'sol'}, actor='runner', owner={})
        self.q.finish('A', claim['claim'], {'status': 'completed'}, actor='worker')
        self.q.update('A', actor='reviewer', reason='approved', prs=[
            {'repo': 'jbookout/carr-system', 'number': 1960, 'head': 'a' * 40}])
        self.q.transition('A', 'merging', actor='orchestrator', reason='reviews passed')
        self.q.begin_merge('A', actor='merge-daemon', reason='pinned head claimed')
        revision = self.q.export()['revision']
        self.q.begin_merge('A', actor='merge-daemon', reason='restart readback')
        self.assertEqual(self.q.export()['revision'], revision)
        self.assertTrue(self.q.get('A')['active'])
        with self.assertRaisesRegex(ValueError, 'completion receipt'):
            self.q.requeue('A', stage='fixing', actor='test', reason='cannot preempt merge')
        self.assertIsNotNone(self.q.get('A')['stages'][-1]['started_at'])
        self.q.transition('A', 'done', actor='merge-daemon', reason='provider receipt')
        merged = self.q.get('A')['stages'][-2]
        self.assertIsNotNone(merged['finished_at'])
        self.assertGreaterEqual(merged['elapsed'], 0)


if __name__ == '__main__':
    unittest.main()
