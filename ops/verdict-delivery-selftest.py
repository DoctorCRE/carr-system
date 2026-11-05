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
        self.data = {'queue/12': {'headRefOid': SHA, 'isInMergeQueue': False}, 'pulls/12': self.pr, 'pulls/12/reviews?per_page=100': [self.review],
                     'rules/branches/main': [{'type': 'required_status_checks', 'parameters': {
                         'required_status_checks': [{'context': 'strict', 'integration_id': 15368}]}}],
                     f'commits/{SHA}/check-runs?per_page=100': {'total_count': 1, 'check_runs': [
                         {'id': 1, 'name': 'strict', 'head_sha': SHA, 'status': 'completed',
                          'conclusion': 'success', 'app': {'id': 15368}}]},
                     f'commits/{SHA}/status?per_page=100': {'total_count': 0, 'statuses': []}}
        self.writes = []

    def read(self, path):
        return copy.deepcopy(self.data[path])

    def run_delivery(self, **kw):
        return m.deliver(12, self.read, self.writes.append, app_login=m.APP, **kw)

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

    def test_approved_current_checks_pending(self):
        self.data[f'commits/{SHA}/check-runs?per_page=100']['check_runs'][0]['conclusion'] = None
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'approved-current-head')
        self.assertFalse(self.writes)

    def test_checks_green_permission_held(self):
        self.assertEqual(self.run_delivery()['state'], 'checks-green')
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

    def test_duplicate_intake_no_review_write(self):
        self.review['body'] += '\nVerdict-Intake: 1'
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=1)['state'], 'checks-green')
        self.assertFalse(self.writes)

    def test_new_changes_invalidates_approval(self):
        change = dict(self.review, id=6, state='CHANGES_REQUESTED', submitted_at='2026-11-05T13:00:00Z')
        self.data['pulls/12/reviews?per_page=100'].append(change)
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'refused')

    def test_required_check_provider_and_completeness(self):
        checks = self.data[f'commits/{SHA}/check-runs?per_page=100']
        for field, value in [('total_count', 2), ('total_count', 100)]:
            checks[field] = value
            with self.assertRaises(ValueError): self.run_delivery(arm_enabled=True)
        checks['total_count'] = 1
        checks['check_runs'][0]['app']['id'] = 999
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'approved-current-head')
        self.assertFalse(self.writes)

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

    def test_status_provider_binding_and_red_collision(self):
        checks = self.data[f'commits/{SHA}/check-runs?per_page=100']
        statuses = self.data[f'commits/{SHA}/status?per_page=100']
        statuses.update(total_count=1, statuses=[{'id': 7, 'context': 'strict', 'state': 'success',
                       'creator': {'type': 'Bot', 'login': 'github-actions[bot]'}}])
        self.data['apps/github-actions'] = {'id': 15368}
        checks.update(total_count=0, check_runs=[])
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'armed')
        self.data['apps/github-actions']['id'] = 999
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'approved-current-head')
        self.data['apps/github-actions']['id'] = 15368
        statuses['statuses'][0]['state'] = 'failure'
        checks.update(total_count=1, check_runs=[{'id': 1, 'name': 'strict', 'head_sha': SHA,
                      'status': 'completed', 'conclusion': 'success', 'app': {'id': 15368}}])
        self.assertEqual(self.run_delivery(arm_enabled=True)['state'], 'approved-current-head')

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

    def test_unprotected_branch_refuses(self):
        self.data['rules/branches/main'] = []
        with self.assertRaises(ValueError): self.run_delivery(arm_enabled=True)
        self.assertFalse(self.writes)

    def test_new_approval_after_changes_posts_again(self):
        self.review['body'] += '\nVerdict-Intake: 1'
        change = dict(self.review, id=6, state='CHANGES_REQUESTED', submitted_at='2026-11-05T13:00:00Z')
        self.data['pulls/12/reviews?per_page=100'].append(change)
        self.assertEqual(self.run_delivery(verdict=BODY, intake_id=2)['state'], 'submitted')
        self.assertEqual(len(self.writes), 1)

    def test_checks_turn_red_before_arm(self):
        calls = 0
        def read(path):
            nonlocal calls
            value = self.read(path)
            if '/check-runs?' in path:
                calls += 1
                if calls > 1:
                    value['check_runs'][0]['conclusion'] = 'failure'
            return value
        self.assertEqual(m.deliver(12, read, self.writes.append, app_login=m.APP,
                                  arm_enabled=True)['state'], 'approved-current-head')
        self.assertFalse(self.writes)

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
