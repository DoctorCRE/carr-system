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

    def test_slice_addresses_survive_reordering_and_insertion(self):
        b = self.board()
        h.project(b)
        rows = b['work_requests']['WR-000014']['slices']
        rows.reverse()
        rows.insert(0, {'slice_id': 'new', 'title': 'New slice'})
        view = h.project(b)
        self.assertEqual(view['aliases']['one'], 'WR14.S1.J1.carr#1960')
        self.assertEqual(view['aliases']['two'], 'WR14.S2.J1.app#316')
        self.assertEqual([row['ordinal'] for row in rows], [3, 2, 1])

    def test_completed_jobs_without_pr_roll_up_as_done(self):
        for field, value in [('status', 'done'), ('stage', 'live')]:
            with self.subTest(field=field):
                b = self.board()
                b['tasks'] = {'one': b['tasks']['one']}
                b['tasks']['one'].pop('pr')
                b['tasks']['one'][field] = value
                view = h.project(b)
                self.assertTrue(view['cards']['WR14.S1.J1']['done'])
                self.assertEqual(view['slices']['WR14.S1']['status'], 'done')

    def test_new_jobs_require_a_captured_request_even_on_an_empty_board(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('board_dispatch', Path(__file__).with_name('progress_board.py'))
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        args = board.parser().parse_args(['task', 'test', 'new', '--title', 'Board cleanup',
            '--status', 'running', '--executor', 'Codex'])
        for state in [{'tasks': {}}, {'tasks': {}, 'work_requests': {}}]:
            with self.subTest(state=state), self.assertRaisesRegex(SystemExit, 'Capture and bind'):
                board.update_task(state, args)
            self.assertEqual(state['tasks'], {})

    def test_backfill_groups_related_actions_queue_repairs_and_linked_requests(self):
        tasks = {str(n): {'title': 'Imported change', 'repo': repo, 'pr': n} for repo, n in [
            ('jbookout/doctorcre-app',155), ('jbookout/doctorcre-app',156),
            ('jbookout/carr-system',1678), ('jbookout/carr-system',1682),
            ('jbookout/carr-system',20), ('jbookout/carr-system',21)]}
        facts = {
            'jbookout/doctorcre-app#155': {'title':'Bump actions/download-artifact from 4 to 8', 'headRefName':'dependabot/github_actions/actions/download-artifact-8'},
            'jbookout/doctorcre-app#156': {'title':'Bump actions/setup-node from 4 to 5', 'headRefName':'dependabot/github_actions/actions/setup-node-5'},
            'jbookout/carr-system#1678': {'title':'Fix queue spacing', 'headRefName':'queue-spacing-first'},
            'jbookout/carr-system#1682': {'title':'Repair queue spacing after review', 'headRefName':'queue-spacing-repair'},
            'jbookout/carr-system#20': {'title':'First delivery', 'body':'Work Request: WR-000014'},
            'jbookout/carr-system#21': {'title':'Second delivery', 'body':'Work Request: WR-000014'},
        }
        plan = h.backfill({'project':'test', 'tasks':tasks}, facts=facts)
        for a,b in [('155','156'), ('1678','1682'), ('20','21')]:
            self.assertEqual(plan['assignments'][a]['request_key'], plan['assignments'][b]['request_key'])
        self.assertEqual(len(plan['requests']), 3)
        self.assertTrue(all('ordinal' in row for request in plan['requests'].values() for row in request['slices']))

    def test_copy_uses_specific_subjects_and_never_clips_a_sentence_or_method(self):
        database = {'title': "Fix database design defects found by the Dot schema review",
                    'body': 'This repair round resolves all three numbered findings in the latest REVIEW: BLOCKED comment.'}
        h.propose_copy(database)
        self.assertEqual(database['card_title'], 'Database repair')
        self.assertNotIn('numbered findings', database['card_description'])
        cadence = {'title':'Cadence activation checks',
                   'body':'The cadence tests assert that a subject reads `no_receipt_on_record` before activation.'}
        h.propose_copy(cadence)
        self.assertIn('no_receipt_on_record', cadence['card_description'])
        long = {'title':'Board cleanup', 'body':'Keep ' + 'related work ' * 50 + 'together.'}
        h.propose_copy(long)
        self.assertNotIn('rea.', long['card_description'])
        clipped = {'title':'Dot research: Job queue, work through these in order on your own cloud computer, rea'}
        h.propose_copy(clipped)
        self.assertNotIn('rea.', clipped.get('card_description', ''))
        with self.assertRaises(ValueError):
            h.validate_copy('Review', 'Review the change.')

    def test_regeneration_replaces_old_generated_copy_and_preserves_human_edits(self):
        old = {'title':'Review checks', 'description':'The tests assert that a subject reads.'}
        row = {'title':'Cadence activation checks', 'body':'The cadence tests follow the activation date recorded by the database.',
            'card_title':old['title'], 'card_description':old['description'],
            'proposed_copy':{'generator':'plain-summary.v1', 'values':old}}
        h.propose_copy(row)
        self.assertEqual(row['card_title'], 'Cadence checks')
        self.assertNotEqual(row['card_description'], old['description'])
        row['card_title'] = 'My wording'
        row['card_description'] = 'A person wrote this.'
        h.propose_copy(row)
        self.assertEqual(row['card_title'], 'My wording')
        self.assertEqual(row['card_description'], 'A person wrote this.')

    def test_registered_request_accepts_a_new_job_and_backfilled_slices_keep_ordinals(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('board_captured', Path(__file__).with_name('progress_board.py'))
        board = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(board)
        state = {'tasks': {}, 'work_requests': {'WR-000014': {'title':'Board cleanup'}}}
        args = board.parser().parse_args(['task', 'test', 'new', '--title', 'Board cleanup',
            '--status', 'running', '--executor', 'Codex', '--work-request', 'WR-000014'])
        board.update_task(state, args)
        self.assertEqual(state['tasks']['new']['work_request'], 'WR-000014')
        b = {'project':'test', 'tasks':{
            'later':{'title':'V2 B: Board navigation', 'slice':'B', 'milestone':'V2'},
            'first':{'title':'V2 A: Board grouping', 'slice':'A', 'milestone':'V2'}}}
        plan = h.backfill(b)
        self.assertEqual([(s['slice_id'],s['ordinal']) for s in plan['requests']['series:V2']['slices']], [('A',1),('B',2)])

    def test_summaries_name_the_subject_and_ignore_delivery_instructions(self):
        for source, title, body in [
            ('Expose authenticated client lease-expiry data', 'Lease expiry radar', 'W13 producer for the DoctorCRE lease expiry radar.'),
            ('Remove client-identifying data from public source', 'Data privacy', 'Do not merge, approve, or enable auto-merge.'),
            ('Interactive progress board', 'Board cleanup', 'It pairs with the producer for the published JSON contract. Show each request and its slices so people can follow the work.')]:
            with self.subTest(source=source):
                row = {'title':source, 'body':body}
                h.propose_copy(row)
                self.assertEqual(row['card_title'], title)
                self.assertNotRegex(row['card_description'], r'^(?:W13|Do not merge|It pairs)')

    def test_job_number_wins_over_a_discovered_pr_number(self):
        for link in [{'pr':1960}, {'pr_links':[{'repo':'jbookout/carr-system','pr':1960}]}]:
            with self.subTest(link=link):
                b = self.board()
                b['tasks']['one'].pop('pr')
                h.project(b)
                b['tasks']['discovered'] = {**b['tasks']['one'], 'job_id':'discovered', 'pr':1960}
                h.project(b)
                b['tasks']['one'].update(link)
                view = h.project(b)
                self.assertEqual(view['aliases']['one'], 'WR14.S1.J1.carr#1960')
                self.assertEqual(view['aliases']['discovered'], 'WR14.S1.J1.carr#1960')

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

    def test_model_copy_is_applied_before_parent_proposals_and_preserves_human_wording(self):
        b = {'project':'test','tasks':{'one':{'title':'Refresh obsolete deployment knowledge and remove dead instructions',
             'repo':'jbookout/carr-system','pr':12, 'card_title':'My wording', 'card_description':'A person wrote this.'}}}
        facts = {'jbookout/carr-system#12':{'title':b['tasks']['one']['title'], 'body':'Refresh the instructions used to deliver changes.'}}
        self.assertEqual(h.backfill(b, facts=facts)['summary_required'], [])
        proposal = h.backfill(b, facts=facts, summaries={'one':{'title':'Delivery instructions', 'description':'Refresh delivery instructions so operators use the current process.'}})
        self.assertEqual(proposal['summary_required'], [])
        self.assertEqual(proposal['assignments']['one']['request_key'], 'pr:jbookout/carr-system#12')
        applied = h.apply_backfill(b, proposal, lambda v,a:{'ok':True,'human_ref':'WR-000014'})
        self.assertEqual(applied['tasks']['one']['card_title'], 'My wording')
        self.assertEqual(applied['tasks']['one']['card_description'], 'A person wrote this.')

    def test_description_sentence_limit_handles_titles_of_address(self):
        h.validate_copy('Suggestion actions', 'Dr. CRE offers suggestions. Partners choose what to accept.')
        with self.assertRaises(ValueError):
            h.validate_copy('Suggestion actions', 'One sentence. Another sentence. A third sentence.')

    def test_implicit_slice_reuses_the_work_request_human_copy(self):
        b = self.board()
        b['work_requests']['WR-000014'].update(title='Reduce instruction handoffs while preserving source ownership',
            card_title='Delivery flow', card_description='Keep delivery instructions together.', slices=[])
        for task in b['tasks'].values():task['slice_id']='implicit'
        row = h.project(b)['slices']['WR14.S1']
        self.assertEqual(row['card_title'], 'Delivery flow')
        self.assertEqual(row['card_description'], 'Keep delivery instructions together.')


if __name__ == '__main__':
    unittest.main()
