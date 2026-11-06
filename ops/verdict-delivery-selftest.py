"""Exercise verdict transitions through the delivery interface with synthetic GitHub data."""
import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.loadpy import load_module_from_path
m = load_module_from_path('verdict_delivery', str(Path(__file__).with_name('verdict-delivery.py')))
SHA = 'a' * 40
BODY = f'VERDICT: APPROVE\nReviewed-SHA: {SHA}\nReviewer: Codex independent reviewer\nNo findings.'


class Delivery(unittest.TestCase):
    def setUp(self):
        self.pr = {'number': 12, 'state': 'open', 'draft': False, 'title': 'Feature',
                   'head': {'sha': SHA, 'repo': {'full_name': m.REPO}},
                   'base': {'ref': 'main'}, 'labels': [], 'user': {'login': 'builder'}}
        self.review = {'id': 5, 'state': 'APPROVED', 'commit_id': SHA, 'body': BODY,
                       'submitted_at': '2026-11-05T12:00:00Z',
                       'user': {'login': m.APP, 'type': 'Bot'}}
        self.data = {'queue/12': {'headRefOid': SHA, 'isInMergeQueue': False}, 'pulls/12': self.pr, 'pulls/12/reviews?per_page=100': [self.review]}
        self.writes = []

    def read(self, path):
        return copy.deepcopy(self.data[path])

    def run_delivery(self, **kw):
        return m.deliver(12, self.read, self.writes.append, app_login=m.APP, **kw)

    def test_approval_event_submits_then_evaluates_native_arm(self):
        import contextlib
        import io
        import json
        import tempfile
        from unittest.mock import patch
        for decision in ('APPROVE', 'CHANGES'):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as directory:
                self.data['pulls/12/reviews?per_page=100'] = []
                self.writes.clear()
                event = {'action': 'created', 'issue': {'number': 12, 'pull_request': {}},
                         'comment': {'id': 1, 'body': BODY.replace('APPROVE', decision),
                                     'user': {'login': 'transport'}},
                         'sender': {'login': 'transport'}}
                path = Path(directory) / 'event.json'
                path.write_text(json.dumps(event))
                def write(action):
                    self.writes.append(action)
                    if action['kind'] == 'submit':
                        self.data['pulls/12/reviews?per_page=100'].append(dict(
                            self.review, body=action['body'],
                            state='APPROVED' if action['decision'] == 'approve' else 'CHANGES_REQUESTED'))
                with patch.dict(m.os.environ, {'GITHUB_EVENT_PATH': str(path),
                        'GITHUB_REPOSITORY': m.REPO, 'CARR_VERDICT_TRANSPORT_LOGIN': 'transport',
                        'CARR_VERDICT_ARM_ENABLED': 'true'}, clear=True), patch.object(
                        m.sys, 'argv', ['verdict-delivery.py', 'event']), patch.object(
                        m, 'github_read', self.read), patch.object(m, 'github_write', write), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(m.main(), 0)
                self.assertEqual([a['kind'] for a in self.writes],
                                 ['submit', 'arm'] if decision == 'APPROVE' else ['submit'])

    def test_received_submitted(self):
        self.data['pulls/12/reviews?per_page=100'] = []
        result = self.run_delivery(verdict=BODY, intake_id=1)
        self.assertEqual(result['state'], 'submitted')
        self.assertEqual(self.writes[0]['sha'], SHA)
        self.assertEqual(self.writes[0]['decision'], 'approve')

    def test_changes_submitted_never_armed(self):
        self.data['pulls/12/reviews?per_page=100'] = []
        result = self.run_delivery(verdict=BODY.replace('APPROVE', 'CHANGES'), intake_id=1, arm_enabled=True)
        self.assertEqual(result['state'], 'submitted')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0]['decision'], 'request-changes')

    def test_approved_current_checks_green_armed(self):
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'armed')
        self.assertEqual(self.writes, [{'kind': 'arm', 'number': 12, 'sha': SHA}])

    def test_native_merge_owns_pending_checks(self):
        # No check or ruleset response is available to this adapter.
        # GitHub owns pending, failing and successful required-check decisions.
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'armed')
        self.assertEqual(self.writes, [{'kind': 'arm', 'number': 12, 'sha': SHA}])

    def test_checks_green_permission_held(self):
        self.assertEqual(self.run_delivery()['state'], 'approved-current-head')
        self.assertFalse(self.writes)

    def test_head_moved(self):
        self.pr['head']['sha'] = 'b' * 40
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=1)['state'], 'head-moved')
        self.assertFalse(self.writes)

    def test_held_label_and_2988(self):
        self.pr['labels'] = [{'name': 'merge-hold'}]
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'held')
        self.pr['labels'] = []
        self.data['pulls/2988'] = self.pr
        self.assertEqual(m.deliver(2988, self.read, self.writes.append, app_login=m.APP)['state'], 'held')
        self.assertFalse(self.writes)

    def test_unapproved_refused(self):
        self.review['commit_id'] = 'b' * 40
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'refused')
        self.assertFalse(self.writes)

    def test_race_before_arm(self):
        calls = 0
        def read(path):
            nonlocal calls
            value = self.read(path)
            if path == 'pulls/12':
                calls += 1
                if calls > 1:
                    value['head']['sha'] = 'b' * 40
            return value
        self.assertEqual(m.deliver(12, read, self.writes.append, app_login=m.APP,
                                  arm_enabled=True)['state'], 'head-moved')
        self.assertFalse(self.writes)

    def test_hold_added_before_arm(self):
        calls = 0
        def read(path):
            nonlocal calls
            value = self.read(path)
            if path == 'pulls/12':
                calls += 1
                if calls > 1:
                    value['labels'] = [{'name': 'merge-hold'}]
            return value
        self.assertEqual(m.deliver(12, read, self.writes.append, app_login=m.APP,
                                  arm_enabled=True)['state'], 'held')
        self.assertFalse(self.writes)

    def test_hold_added_during_queue_lookup_refuses_arm(self):
        def read(path):
            value = self.read(path)
            if path == 'queue/12':
                self.pr['labels'] = [{'name': 'merge-hold'}]
            return value
        result = m.deliver(12, read, self.writes.append, app_login=m.APP, arm_enabled=True)
        self.assertEqual(result['state'], 'held')
        self.assertFalse(self.writes)

    def test_approval_dismissed_during_queue_lookup_refuses_arm(self):
        def read(path):
            value = self.read(path)
            if path == 'queue/12':
                self.review['state'] = 'DISMISSED'
            return value
        result = m.deliver(12, read, self.writes.append, app_login=m.APP, arm_enabled=True)
        self.assertEqual(result['state'], 'refused')
        self.assertFalse(self.writes)

    def test_approval_replaced_during_queue_lookup_refuses_arm(self):
        def read(path):
            value = self.read(path)
            if path == 'queue/12':
                self.data['pulls/12/reviews?per_page=100'].append(dict(
                    self.review, id=6, state='CHANGES_REQUESTED',
                    submitted_at='2026-11-05T13:00:00Z'))
            return value
        result = m.deliver(12, read, self.writes.append, app_login=m.APP, arm_enabled=True)
        self.assertEqual(result['state'], 'refused')
        self.assertFalse(self.writes)

    def test_duplicate_intake_no_review_write(self):
        self.review['body'] += '\nVerdict-Intake: 1'
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=1)['state'], 'refused')
        self.assertFalse(self.writes)

    def test_consumed_approval_cannot_replace_newer_changes(self):
        self.review['body'] += '\nVerdict-Intake: 1'
        change = dict(self.review, id=6, state='CHANGES_REQUESTED',
                      body=BODY.replace('APPROVE', 'CHANGES') + '\nVerdict-Intake: 2',
                      submitted_at='2026-11-05T13:00:00Z')
        self.data['pulls/12/reviews?per_page=100'].append(change)
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'refused')
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=1,
                                          arm_enabled=True)['state'], 'refused')
        self.assertFalse(self.writes)
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'refused')

    def test_consumed_intake_refused_across_all_prior_app_reviews(self):
        for state in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED', 'COMMENTED', 'PENDING'):
            with self.subTest(state=state):
                prior = dict(self.review, state=state, body=BODY + '\nVerdict-Intake: 1')
                latest = dict(self.review, id=6, body=BODY + '\nVerdict-Intake: 2',
                              submitted_at='2026-11-05T13:00:00Z')
                self.data['pulls/12/reviews?per_page=100'] = [prior, latest]
                for body in (BODY, BODY.replace('APPROVE', 'CHANGES'), BODY + '\nRevised text.'):
                    result = self.run_delivery(verdict=body, intake_id=1, arm_enabled=True)
                    self.assertEqual(result['state'], 'refused')
                    self.assertFalse(self.writes)

    def test_delayed_older_intake_refused_after_newer_changes(self):
        self.review.update(state='CHANGES_REQUESTED',
                           body=BODY.replace('APPROVE', 'CHANGES') + '\nVerdict-Intake: 2')
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=1)['state'], 'refused')
        self.assertFalse(self.writes)

    def test_intake_consumption_is_scoped_to_app_and_head(self):
        for changes in ({'commit_id': 'b' * 40}, {'user': {'login': 'other[bot]'}}):
            with self.subTest(changes=changes):
                self.data['pulls/12/reviews?per_page=100'] = [
                    dict(self.review, body=BODY + '\nVerdict-Intake: 2', **changes)]
                self.writes.clear()
                self.assertEqual(self.run_delivery(verdict=BODY, intake_id=1)['state'], 'submitted')
                self.assertEqual(len(self.writes), 1)

    def test_new_changes_invalidates_approval(self):
        change = dict(self.review, id=6, state='CHANGES_REQUESTED', submitted_at='2026-11-05T13:00:00Z')
        self.data['pulls/12/reviews?per_page=100'].append(change)
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'refused')


    def test_parser_refuses_malformed_and_duplicate_headers(self):
        for body in ['', BODY.replace(SHA, 'abc'), BODY + '\nReviewed-SHA: ' + SHA,
                     BODY.replace('Codex independent reviewer', ''), BODY.replace('APPROVE', 'COMMENT')]:
            with self.subTest(body=body), self.assertRaises(ValueError): m.parse_verdict(body)

    def test_only_trusted_created_pr_comment_is_input(self):
        event = {'action': 'created', 'issue': {'number': 12, 'pull_request': {}},
                 'comment': {'id': 1, 'body': BODY, 'user': {'login': 'transport'}},
                 'sender': {'login': 'transport'}}
        self.assertEqual(m.intake(event, 'transport')[0], 12)
        for key, value in [('action', 'edited'), ('sender', {'login': 'stranger'})]:
            bad = dict(event, **{key: value})
            with self.assertRaises(ValueError): m.intake(bad, 'transport')
        with self.assertRaises(ValueError): m.intake(event, '')

    def test_dry_run_never_writes(self):
        result = self.run_delivery(arm_enabled=True, dry_run=True)
        self.assertEqual(result['state'], 'would-arm')
        self.assertFalse(self.writes)

    def test_write_error_stops_without_retry(self):
        def fail(action):
            self.writes.append(action)
            raise RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            m.deliver(12, self.read, fail, app_login=m.APP, arm_enabled=True)
        self.assertEqual(len(self.writes), 1)

    def test_approval_revoked_before_arm(self):
        calls = 0
        def read(path):
            nonlocal calls
            value = self.read(path)
            if path == 'pulls/12/reviews?per_page=100':
                calls += 1
                if calls > 1:
                    value[0]['state'] = 'DISMISSED'
            return value
        self.assertEqual(m.deliver(12, read, self.writes.append, app_login=m.APP,
                                  arm_enabled=True)['state'], 'refused')
        self.assertFalse(self.writes)

    def test_existing_arm_is_idempotent(self):
        self.pr['auto_merge'] = {'enabled_by': {'login': m.APP}}
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'armed')
        self.assertFalse(self.writes)


    def test_new_approval_after_changes_posts_again(self):
        self.review['body'] += '\nVerdict-Intake: 1'
        change = dict(self.review, id=6, state='CHANGES_REQUESTED',
                      body=BODY.replace('APPROVE', 'CHANGES') + '\nVerdict-Intake: 2',
                      submitted_at='2026-11-05T13:00:00Z')
        self.data['pulls/12/reviews?per_page=100'].append(change)
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=3)['state'], 'submitted')
        self.assertEqual(len(self.writes), 1)


    def test_github_request_budget_refuses_before_network(self):
        from unittest.mock import patch
        with patch.object(m, 'REQUESTS', m.MAX_REQUESTS), patch.object(m.subprocess, 'run') as command:
            with self.assertRaises(ValueError): m.gh(['api', 'repos/example'])
            command.assert_not_called()

    def test_existing_native_queue_entry_is_idempotent(self):
        self.data['queue/12']['isInMergeQueue'] = True
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'armed')
        self.assertFalse(self.writes)


if __name__ == '__main__':
    unittest.main()
