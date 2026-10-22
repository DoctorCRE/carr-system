"""Completion-driven handoffs through the durable queue and runner seam."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from work_queue import WorkQueue
from work_runner import Runner, Resources
from work_handoffs import Handoffs
import work_handoffs
import work_runner
import dispatch

REPO = 'jbookout/carr-system'
HEAD = 'a' * 40
PR = {'repo': REPO, 'number': 7, 'head': HEAD}


class Provider:
    def __init__(self):
        self.snapshot = {'head': HEAD, 'ci_state': 'passed', 'comments': [],
                         'state': 'open', 'branch': 'feature', 'labels': ['do_not_merge']}
        self.released = []

    def pr(self, pin):
        return self.snapshot.copy()

    def release_hold(self, pin):
        self.released.append(pin.copy())
        self.snapshot['labels'] = []


class HandoffTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'queue.sqlite3'
        self.queue = WorkQueue(self.path)
        self.addCleanup(self.queue.close)
        self.env = patch.dict(os.environ, {'CARR_WORK_AUTO_HANDOFFS': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.desks = {'builder': {'kind': 'codex-session', 'family': 'sol', 'effort': 'high'},
                      'claude-reviewer': {'kind': 'claude-api', 'effort': 'high'}}
        self.provider = Provider()
        self.flow = Handoffs(self.queue, self.desks, self.provider)
        self.queue.enqueue('job', desk='builder', task='Build queue transitions', actor='test',
                           options={'codex': True, 'checkout': 'new:feature'}, family='sol')

    def finish(self, desk, result):
        claim = self.queue.claim(eligible_desks={desk}, actor='test', owner={})
        self.assertIsNotNone(claim)
        self.queue.finish('job', claim['claim'], result, actor='test')

    def build(self):
        self.finish('builder', {'status': 'completed', 'pr': PR, 'family': 'sol'})

    def review(self, verdict='APPROVE', head=HEAD):
        self.provider.snapshot['comments'] = [{'id': 99, 'body': f'{verdict}\nReviewed-SHA: {head}\nP1: preserve ordering',
            'author_association': 'OWNER', 'user': {'login': 'jbookout'},
            'created_at': '2099-01-01T00:00:00Z'}]
        self.finish('claude-reviewer', {'status': 'completed', 'comment_url':
                                      'https://github.com/jbookout/carr-system/pull/7#issuecomment-99'})

    def git_heads(self):
        root = self.path.parent / 'git'
        root.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-c', 'user.name=Queue test',
                '-c', 'user.email=jbookout@users.noreply.github.com', '-c', 'commit.gpgsign=false',
                *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        git('init', '-b', 'main')
        (root / 'base').write_text('base')
        git('add', 'base')
        git('commit', '-m', 'base')
        git('switch', '-c', 'feature')
        (root / 'feature').write_text('feature')
        git('add', 'feature')
        git('commit', '-m', 'feature')
        old = git('rev-parse', 'HEAD')
        git('switch', 'main')
        (root / 'main').write_text('main')
        git('add', 'main')
        git('commit', '-m', 'main advances')
        git('switch', 'feature')
        git('merge', '--no-ff', 'main', '-m', 'merge main')
        return root, git, old, git('rev-parse', 'HEAD')

    def test_review_cannot_reuse_a_comment_from_before_its_claim(self):
        self.build()
        self.flow.tick()
        self.review()
        self.provider.snapshot['comments'][0]['created_at'] = '2000-01-01T00:00:00Z'
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.assertFalse(self.provider.released)

    def test_hold_effect_survives_queue_rollback_without_duplicate_release(self):
        self.build()
        self.flow.tick()
        self.review()
        release = self.provider.release_hold
        def interrupted(pin):
            release(pin)
            raise RuntimeError('interrupted after provider effect')
        self.provider.release_hold = interrupted
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.provider.release_hold = lambda pin: release(pin) if self.provider.snapshot['labels'] else None
        with WorkQueue(self.path) as restarted:
            Handoffs(restarted, self.desks, self.provider).tick()
            self.assertEqual(restarted.get('job')['stage'], 'merging')
        self.assertEqual(self.provider.released, [PR])

    def test_inflight_merge_retains_its_pin_when_main_advances(self):
        self.build()
        self.flow.tick()
        self.review()
        self.flow.tick()
        self.queue.begin_merge('job', actor='test', reason='provider owns merge')
        self.provider.snapshot['head'] = 'b' * 40
        self.provider.main_merges_only = lambda pin, head: True
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['prs'], [PR])
        self.assertTrue(self.queue.get('job')['active'])

    def test_provider_failure_preserves_receipt_and_does_not_stop_other_handoffs(self):
        self.build()
        self.flow.tick()
        self.review()
        self.queue.enqueue('second', desk='builder', task='Build second', actor='test', family='sol')
        claim = self.queue.claim(eligible_desks={'builder'}, actor='test', owner={})
        self.queue.finish('second', claim['claim'], {'status': 'completed', 'pr': {**PR, 'number': 8}}, actor='test')
        self.provider.pr = lambda pin: (_ for _ in ()).throw(RuntimeError('provider unavailable'))
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.assertTrue(self.queue.get('second')['ready'])
        self.assertFalse(self.provider.released)

    def test_off_flag_and_manual_park_leave_completion_unadvanced(self):
        self.build()
        with patch.dict(os.environ, {'CARR_WORK_AUTO_HANDOFFS': '0'}):
            self.flow.tick()
        self.assertEqual(self.queue.get('job')['desk'], 'builder')
        self.assertFalse(self.queue.get('job')['ready'])
        self.queue.transition('job', 'parked', actor='orchestrator', reason='park overrides automation')
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'parked')
        self.assertFalse(self.provider.released)

    def test_missing_pr_or_same_family_reviewer_never_queues(self):
        self.finish('builder', {'status': 'completed', 'result': 'No PR'})
        self.flow.tick()
        self.assertEqual(self.queue.candidates(), [])
        self.queue.requeue('job', stage='building', actor='test', reason='new authorized build')
        self.build()
        self.desks['claude-reviewer'] = {'kind': 'codex-session', 'family': 'astra'}
        self.flow.tick()
        self.assertEqual(self.queue.candidates(), [])

    def test_untrusted_or_unbound_approval_never_releases_hold(self):
        self.build()
        self.flow.tick()
        self.review()
        self.provider.snapshot['comments'][0]['author_association'] = 'NONE'
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.assertFalse(self.provider.released)

    def test_nonmerge_commit_or_other_branch_merge_cannot_repin(self):
        root, git, old, new = self.git_heads()
        (root / 'fix').write_text('new feature code')
        git('add', 'fix')
        git('commit', '-m', 'source change')
        changed = git('rev-parse', 'HEAD')
        self.assertFalse(work_handoffs.main_merges_only(root, old, changed, 'main'))
        git('switch', '-c', 'other', old)
        (root / 'other').write_text('other branch')
        git('add', 'other')
        git('commit', '-m', 'other change')
        git('switch', 'feature')
        git('reset', '--hard', old)
        git('merge', '--no-ff', 'other', '-m', 'merge other')
        self.assertFalse(work_handoffs.main_merges_only(root, old, git('rev-parse', 'HEAD'), 'main'))

    def test_priority_change_during_provider_readback_wins(self):
        self.build()
        self.flow.tick()
        self.review()
        self.queue.enqueue('other', desk='other', task='other work', actor='test')
        original = self.provider.pr
        def read(pin):
            self.queue.prioritize('other', actor='orchestrator', reason='new priority')
            return original(pin)
        self.provider.pr = read
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.assertFalse(self.provider.released)

    def test_newer_trusted_block_prevents_an_older_approval_from_advancing(self):
        self.build()
        self.flow.tick()
        self.review()
        self.provider.snapshot['comments'].append({'id': 100, 'body': 'BLOCK\nNew issue',
            'author_association': 'OWNER', 'user': {'login': 'jbookout'}, 'created_at': '2099-01-02T00:00:00Z'})
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.assertFalse(self.provider.released)

    def test_changed_reviewer_family_is_refused_before_executor_runs(self):
        self.build()
        self.flow.tick()
        claim = self.queue.claim(eligible_desks={'claude-reviewer'}, actor='test', owner={})
        executed = []
        def dispatch_call(*args, on_started, **kwargs):
            on_started({'name': 'claude-reviewer', 'kind': 'codex-session', 'family': 'astra'})
            executed.append(True)
            return {'status': 'completed'}
        with patch.object(dispatch, 'dispatch', side_effect=dispatch_call):
            work_runner.worker(self.queue, 'job', claim['claim'], self.path.parent / 'registry.json', self.path.parent / 'results.jsonl')
        self.assertEqual(executed, [])
        self.assertEqual(self.queue.get('job')['stage'], 'failed')

    def test_existing_builder_prose_receipt_carries_pr_number_and_head(self):
        self.finish('builder', {'status': 'completed', 'result':
            f'Opened https://github.com/{REPO}/pull/7.\nHead SHA: `{HEAD}`. Tests: 4 passed.'})
        self.flow.tick()
        self.assertEqual((self.queue.get('job')['desk'], self.queue.get('job')['ready']),
                         ('claude-reviewer', True))

    def test_github_hold_release_checks_head_and_observes_existing_effect_before_retry(self):
        responses = [
            {'state': 'open', 'head': {'sha': HEAD}, 'labels': [{'name': 'do_not_merge'}]},
            [],
            {'state': 'open', 'head': {'sha': HEAD}, 'labels': []},
            {'state': 'open', 'head': {'sha': HEAD}, 'labels': []},
        ]
        effects = []
        def run(argv, **kwargs):
            effects.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(responses.pop(0)), '')
        with patch.object(work_handoffs.subprocess, 'run', side_effect=run), patch.object(work_handoffs.time, 'sleep'):
            provider = work_handoffs.GitHub()
            provider.release_hold(PR)
            provider.release_hold(PR)
        self.assertEqual(sum('DELETE' in call for call in effects), 1)
        self.assertEqual(responses, [])

    def test_main_merge_between_review_and_completion_keeps_the_approval(self):
        root, git, old, new = self.git_heads()
        self.provider.main_merges_only = lambda pin, head: work_handoffs.main_merges_only(root, pin['head'], head, 'main')
        self.finish('builder', {'status': 'completed', 'pr': {**PR, 'head': old}})
        self.flow.tick()
        self.review(head=old)
        self.provider.snapshot['head'] = new
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['prs'][0]['head'], new)
        self.assertEqual(self.queue.get('job')['stage'], 'merging')

    def test_runner_advances_receipt_and_launches_reviewer(self):
        self.build()
        launched = []
        runner = Runner(self.queue, Resources(self.path.parent / 'locks'),
                        launch=lambda card, handles: launched.append((card['stage'], card['desk'])),
                        terminated=lambda owner: False, handoffs=self.flow)
        runner.tick()
        self.assertEqual(launched, [('review', 'claude-reviewer')])

    def test_main_merge_only_head_is_repinned_without_another_review(self):
        root, git, old, new = self.git_heads()
        pin = {**PR, 'head': old}
        self.provider.main_merges_only = lambda pin, head: work_handoffs.main_merges_only(root, pin['head'], head, 'main')
        self.provider.snapshot['head'] = old
        self.finish('builder', {'status': 'completed', 'pr': pin})
        self.flow.tick()
        self.review(head=old)
        self.flow.tick()
        self.provider.snapshot['head'] = new
        self.flow.tick()
        card = self.queue.get('job')
        self.assertEqual((card['stage'], card['review_round'], card['prs'][0]['head']), ('merging', 1, new))
        self.assertEqual(self.queue.candidates(), [])

    def test_third_rejection_blocks_with_round_cap_and_never_queues_fourth(self):
        self.build()
        for round_number in range(1, 4):
            self.flow.tick()
            self.review('REQUEST_CHANGES')
            self.flow.tick()
            if round_number < 3:
                self.finish('builder', {'status': 'completed', 'pr': PR})
        self.flow.tick()
        card = self.queue.get('job')
        self.assertEqual((card['stage'], card['ready'], card['review_round']), ('blocked', False, 3))
        self.assertEqual(card['last_event']['reason'], 'round cap')
        self.assertEqual(self.queue.candidates(), [])

    def test_request_changes_returns_findings_to_original_builder(self):
        self.build()
        self.flow.tick()
        self.review('REQUEST_CHANGES')
        self.flow.tick()
        card = self.queue.get('job')
        self.assertEqual((card['stage'], card['desk'], card['ready'], card['review_round']),
                         ('fixing', 'builder', True, 1))
        self.assertIn('preserve ordering', self.queue.candidates()[0]['task'])
        self.assertFalse(self.provider.released)
        self.finish('builder', {'status': 'completed', 'pr': {**PR, 'head': 'b' * 40}})
        self.flow.tick()
        self.assertEqual(self.queue.get('job')['stage'], 'review')
        self.assertTrue(self.queue.get('job')['ready'])

    def test_approve_pins_exact_head_and_releases_hold_without_manual_ack(self):
        self.build()
        self.flow.tick()
        self.review()
        self.flow.tick()
        card = self.queue.get('job')
        self.assertEqual((card['stage'], card['ready'], card['ci_state']), ('merging', False, 'passed'))
        self.assertEqual(self.queue.merge_order()['entries'][0]['head'], HEAD)
        self.assertEqual(self.provider.released, [PR])
        self.flow.tick()
        self.assertEqual(self.provider.released, [PR])

    def test_builder_completion_queues_independent_review_once_across_restart(self):
        self.build()
        self.flow.tick()
        with WorkQueue(self.path) as restarted:
            Handoffs(restarted, self.desks, self.provider).tick()
            card = restarted.get('job')
            self.assertEqual((card['stage'], card['desk'], card['ready']),
                             ('review', 'claude-reviewer', True))
            self.assertEqual(card['prs'], [PR])
            self.assertEqual(card['review_round'], 0)
            candidate = restarted.candidates()[0]
            self.assertIn('independent exact-head reviewer', candidate['task'])
            self.assertIn(HEAD, candidate['task'])
            self.assertEqual(len([e for e in restarted.export()['history']
                                  if e['kind'] == 'handoff']), 1)


if __name__ == '__main__':
    unittest.main()
