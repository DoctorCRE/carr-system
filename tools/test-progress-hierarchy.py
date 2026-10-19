import copy
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import progress_hierarchy as h


class HierarchyTests(unittest.TestCase):
    def board(self):
        return {'project': 'test', 'work_requests': {'WR-000014': {
            'title': 'Merge queue', 'description': 'Keep reviewed changes moving safely.',
            'slices': [{'slice_id': 'queue', 'title': 'Queue ordering'},
                       {'slice_id': 'review', 'title': 'Review checks'}]}}, 'tasks': {
            'one': {'title': 'Queue ordering', 'work_request': 'WR-000014', 'slice_id': 'queue',
                    'job_id': 'dispatch-a', 'status': 'running', 'repo': 'jbookout/carr-system', 'pr': 1960},
            'two': {'title': 'Review checks', 'work_request': 'WR-000014', 'slice_id': 'review',
                    'status': 'review', 'repo': 'jbookout/doctorcre-app', 'pr': 316}}}

    def test_registered_slice_order_and_parent_address(self):
        b = self.board()
        view = h.project(b)
        self.assertEqual(list(view['work_requests']), ['WR14'])
        self.assertEqual(list(view['slices']), ['WR14.S1', 'WR14.S2'])
        self.assertEqual(list(view['cards']), ['WR14.S1.J1.carr#1960', 'WR14.S2.J1.app#316'])
        self.assertEqual(view['work_requests']['WR14']['counts'], {'done': 0, 'active': 2, 'blocked': 0, 'failed': 0})
        self.assertEqual(b['tasks']['one']['card_id'], 'WR14.S1.J1.carr#1960')

    def test_merge_evidence_folds_slice_but_done_without_pr_does_not(self):
        b = self.board()
        b['tasks']['one'].update(merged_at='2026-10-19T12:00:00Z', merge_sha='a' * 40)
        b['tasks']['two']['status'] = 'done'
        view = h.project(b)
        self.assertEqual(view['slices']['WR14.S1']['status'], 'done')
        self.assertEqual(view['work_requests']['WR14']['status'], 'active')
        self.assertEqual(view['work_requests']['WR14']['counts']['done'], 1)
        b['tasks']['two'].update(merged_at='2026-10-19T13:00:00Z', merge_sha='b' * 40)
        self.assertEqual(h.project(b)['work_requests']['WR14']['status'], 'done')

    def test_job_keeps_number_when_pr_opens_and_title_changes_and_reparent_alias_resolves(self):
        b = self.board()
        b['tasks']['one'].pop('pr')
        h.project(b)
        b['tasks']['one']['pr'] = 1960
        view = h.project(b)
        self.assertEqual(view['aliases']['WR14.S1.J1'], 'WR14.S1.J1.carr#1960')
        b['tasks']['one']['title'] = 'Queue safety'
        self.assertIn('WR14.S1.J1.carr#1960', h.project(b)['cards'])
        b['tasks']['one']['slice_id'] = 'review'
        view = h.project(b)
        self.assertEqual(view['aliases']['WR14.S1.J1.carr#1960'], 'WR14.S2.J2.carr#1960')

    def test_same_job_two_prs_and_review_retries_share_one_number(self):
        b = self.board()
        b['tasks']['one']['pr_links'] = [{'repo': 'jbookout/carr-system', 'pr': 1960},
                                        {'repo': 'jbookout/doctorcre-app', 'pr': 316}]
        b['tasks']['retry'] = {**b['tasks']['one'], 'title': 'Queue fix'}
        view = h.project(b)
        self.assertIn('WR14.S1.J1.app#316', view['cards'])
        self.assertEqual(len(view['slices']['WR14.S1']['cards']), 2)

    def test_new_earlier_job_never_renumbers_existing_jobs(self):
        b = self.board()
        h.project(b)
        b['tasks']['zero'] = {**b['tasks']['one'], 'created_at': '2000', 'job_id': 'late-import', 'pr': 1}
        self.assertIn('WR14.S1.J2.carr#1', h.project(b)['cards'])

    def test_blocked_and_failed_slices_count_without_disappearing(self):
        b = self.board()
        b['tasks']['one']['status'] = 'blocked'
        b['tasks']['two']['status'] = 'failed'
        self.assertEqual(h.project(b)['work_requests']['WR14']['counts'],
                         {'done': 0, 'active': 0, 'blocked': 1, 'failed': 1})

    def test_plain_titles_and_proposals_preserve_human_edits(self):
        for title in ['Four words are bad', 'Fix abcdef1234', 'WR14 cleanup', 'PR 1960', 'src/board.py', 'fix-board-title', 'Fix 123456']:
            with self.subTest(title=title), self.assertRaises(ValueError):
                h.validate_copy(title, 'Make the board easier to understand.')
        with self.assertRaises(ValueError):
            h.validate_copy('Board cleanup', 'WR14 fixes the board.')
        task = {'title': 'feat(board): group cards under their Work Request',
                'summary': 'Group related changes so people can see the work clearly.',
                'card_title': 'My wording', 'card_description': 'A person wrote this.'}
        h.propose_copy(task)
        self.assertEqual(task['card_title'], 'My wording')
        self.assertEqual(task['card_description'], 'A person wrote this.')
        h.validate_copy(**task['proposed_copy']['values'])

    def test_backfill_is_a_pure_dry_run_and_uses_one_legacy_intake(self):
        b = {'project': 'test', 'tasks': {'a': {'title': 'V2 A: Lead map', 'slice': 'A', 'milestone': 'V2'},
                                       'b': {'title': 'Board cleanup'}, 'c': {'title': 'Queue ordering'}}}
        before = copy.deepcopy(b)
        plan = h.backfill(b)
        self.assertEqual(b, before)
        self.assertEqual(len(plan['create_requests']), 2)
        self.assertEqual(plan['assignments']['b']['request_key'], 'legacy')
        self.assertEqual(plan['assignments']['c']['request_key'], 'legacy')
        self.assertTrue(all(row['verb'] == 'report-problem' for row in plan['create_requests']))
        self.assertNotIn('WR1', json.dumps(plan))

    def test_backfill_apply_only_captures_and_replay_uses_same_idempotency_key(self):
        b = {'project': 'test', 'tasks': {'a': {'title': 'Board cleanup'}}}
        plan = h.backfill(b)
        calls = []
        def record(verb, args):
            calls.append((verb, args))
            return {'ok': True, 'human_ref': 'WR-000014', 'title': 'Legacy backlog'}
        applied = h.apply_backfill(b, plan, record)
        h.apply_backfill(b, plan, record)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0][0], 'report-problem')
        self.assertEqual(applied['tasks']['a']['work_request'], 'WR-000014')
        self.assertEqual(applied['work_requests']['WR-000014']['slices'], [])
        with self.assertRaises(ValueError):
            h.apply_backfill({'tasks': {}}, plan, record)

    def test_snapshot_retains_failed_and_merged_evidence_and_removes_old_plan_fields(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('progress_board_hierarchy_test', Path(__file__).with_name('progress_board.py'))
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        b = self.board()
        b['tasks']['one'].update(plan_id='old-name', merged_at='2026-10-19T12:00:00Z', merge_sha='a' * 40)
        b['tasks']['two']['status'] = 'failed'
        snap = board.board_snapshot(b, costs={})
        self.assertEqual(snap['schema'], 'carr-progress-board.v4')
        self.assertEqual(len(snap['tasks']), 2)
        self.assertNotIn('plan_id', snap['tasks']['WR14.S1.J1.carr#1960'])
        self.assertTrue(snap['tasks']['WR14.S1.J1.carr#1960']['done'])
        self.assertEqual(snap['hierarchy']['work_requests']['WR14']['counts']['failed'], 1)
        board.retain_recent_cards(b, {('jbookout/carr-system', 1960): {'state': 'MERGED', 'mergedAt': '2000-01-01T00:00:00Z'}})
        self.assertEqual(len(b['tasks']), 2)

    def test_registered_ordinals_and_captured_small_request_need_no_triage(self):
        row = h.request_record({'human_ref': 'WR-000014', 'state': 'captured',
            'title': 'Merge queue', 'desired_outcome': 'Keep reviewed changes moving safely.'})
        self.assertEqual(row['slices'], [])
        ordered = h.request_record({'human_ref': 'WR-000014', 'title': 'Merge queue',
            'desired_outcome': 'Keep reviewed changes moving safely.'},
            {'slice_plan': {'slices': [{'slice_ref': 'later', 'ordinal': 2, 'objective': 'Review checks'},
                                       {'slice_ref': 'first', 'ordinal': 1, 'objective': 'Queue ordering'}]}})
        self.assertEqual([s['slice_id'] for s in ordered['slices']], ['first', 'later'])

    def test_duplicate_review_row_cannot_erase_merged_evidence(self):
        b = self.board()
        b['tasks']['one'].update(merged_at='2026-10-19T12:00:00Z', merge_sha='a' * 40)
        b['tasks']['retry'] = {**b['tasks']['one'], 'job_id': 'repair-job', 'status': 'review'}
        b['tasks']['retry'].pop('merged_at')
        b['tasks']['retry'].pop('merge_sha')
        self.assertTrue(h.project(b)['cards']['WR14.S1.J1.carr#1960']['done'])

    def test_new_discovered_pr_uses_captured_legacy_request_until_parent_is_known(self):
        b = self.board()
        b['legacy_work_request'] = 'WR-000014'
        b['work_requests']['WR-000014']['slices'] = []
        for task in b['tasks'].values():
            task.pop('slice_id')
        b['tasks']['discovered'] = {'title': 'Board cleanup', 'status': 'review',
                                    'repo': 'jbookout/carr-system', 'pr': 9}
        view = h.project(b)
        self.assertIn('WR14.S1.J1.carr#9', view['cards'])
        self.assertIn('discovered', b['later_triage'])

    def test_legacy_disposition_receipts_remain_parented_and_expandable(self):
        b = {'project': 'test', 'tasks': {'new': {'title': 'Board cleanup'}},
             'legacy_history': {'old': {'title': 'Board repair', 'status': 'archived',
                 'legacy_disposition': {'schema': 'carr-legacy-disposition.v1'}, 'reason': 'legacy, unverified'}}}
        proposal = h.backfill(b)
        self.assertIn('old', proposal['assignments'])
        applied = h.apply_backfill(b, proposal, lambda v,a: {'ok':True, 'human_ref':'WR-000014'})
        self.assertEqual(applied['legacy_history']['old']['work_request'], 'WR-000014')
        view = h.project(applied)
        self.assertTrue(view['cards'][view['aliases']['old']]['archived'])
        self.assertEqual(view['slices']['WR14.S1']['status'], 'active')

    def test_implicit_job_keeps_number_when_it_opens_a_pr(self):
        b = self.board()
        b['tasks']['one'].pop('job_id')
        b['tasks']['one'].pop('pr')
        self.assertIn('WR14.S1.J1', h.project(b)['cards'])
        b['tasks']['one']['pr'] = 1960
        self.assertIn('WR14.S1.J1.carr#1960', h.project(b)['cards'])

    def test_accepted_request_without_registered_slices_still_uses_implicit_slice(self):
        def record(verb, args):
            if verb == 'work-request-card':
                return {'human_ref':'WR-000014', 'title':'Merge queue', 'desired_outcome':'Keep changes moving.', 'plan':{'id':'existing'}}
            raise RuntimeError('engineering-passport refused: engineering_slice_plan_not_registered')
        self.assertEqual(h.load_request('WR-000014', record)['slices'], [])
        def outage(verb, args):
            if verb == 'work-request-card':
                return record(verb, args)
            raise RuntimeError('service unavailable')
        with self.assertRaisesRegex(RuntimeError, 'service unavailable'):
            h.load_request('WR-000014', outage)

    def test_registered_ordinals_are_addresses_even_when_not_dense(self):
        b = self.board()
        b['work_requests']['WR-000014']['slices'][0]['ordinal'] = 2
        b['work_requests']['WR-000014']['slices'][1]['ordinal'] = 5
        self.assertEqual(list(h.project(b)['slices']), ['WR14.S2', 'WR14.S5'])

    def test_clear_standalone_prs_get_work_requests_instead_of_a_maintenance_bucket(self):
        b = {'project':'test', 'tasks':{
            'one':{'title':'Board cleanup', 'repo':'jbookout/carr-system', 'pr':12},
            'review':{'title':'Board cleanup', 'repo':'jbookout/carr-system', 'pr':12},
            'two':{'title':'Merge queue', 'repo':'jbookout/carr-system', 'pr':13}}}
        facts = {'jbookout/carr-system#12':{'title':'Group progress cards', 'body':'Group related changes so the board is easier to follow.'},
                 'jbookout/carr-system#13':{'title':'Order the merge queue', 'body':'Keep reviewed changes moving in a predictable order.'}}
        proposal = h.backfill(b, facts=facts)
        self.assertEqual(len(proposal['create_requests']), 2)
        self.assertEqual(proposal['assignments']['one']['request_key'], proposal['assignments']['review']['request_key'])
        self.assertNotEqual(proposal['assignments']['one']['request_key'], proposal['assignments']['two']['request_key'])
        self.assertEqual(proposal['later_triage'], [])

    def test_slice_counts_separate_jobs_from_prs_and_use_pipeline_stages(self):
        b = self.board()
        b['tasks']['job'] = {'title':'Queue checks', 'work_request':'WR-000014', 'slice_id':'queue', 'status':'queued'}
        row = h.project(b)['slices']['WR14.S1']
        self.assertEqual(row['pr_counts'], {'build':1})
        self.assertEqual(row['job_counts'], {'queued':1})

    def test_summary_keeps_meaningful_method_words_and_rejects_sentence_fragments(self):
        row = {'title':'A `whats-new` answer that remembers what each partner saw',
               'body':'Show changes since the last visit so returning partners can catch up.'}
        h.propose_copy(row)
        self.assertEqual(row['card_title'], 'Recent changes')
        with self.assertRaises(ValueError):
            h.validate_copy('A answer that', 'Show changes since the last visit.')


if __name__ == '__main__':
    unittest.main()
