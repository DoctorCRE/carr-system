"""Exercise merged Card discovery and close calls through the command line."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CloseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'bin').mkdir()
        contract = self.root / 'mcp-server/src/progress-address.v1.json'
        contract.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'mcp-server/src/progress-address.v1.json', contract)
        self.gh_error = False
        self.extra_repos = {}
        self.close_error = False
        self.evidence_error = False
        self.calls = self.root / 'calls.jsonl'
        self.card = dict(ok=True, human_ref='WR-000557', version=3, state='captured',
                         slices=[dict(slice_id='WR557.S5', version=2, outcome='open')],
                         acceptance_criteria=[dict(id='S5-CLOSE', text='Close finished requests')])
        self.prs = [dict(body='Card: WR557.S5.J1\n\nDelivery', state='MERGED',
                         url='https://github.com/DoctorCRE/carr-system/pull/123',
                         mergeCommit=dict(oid='a' * 40))]

    def run_close(self, *args):
        shutil.copyfile(ROOT / 'bin/close-work-request', self.root / 'bin/close-work-request')
        adapter = f'''#!{sys.executable}
import json, pathlib, sys
with pathlib.Path({str(self.calls)!r}).open('a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'pr':
    if {self.gh_error!r}:
        print('synthetic GitHub failure', file=sys.stderr)
        sys.exit(1)
    repo = sys.argv[sys.argv.index('--repo')+1]
    print(json.dumps({self.prs!r} if repo == 'DoctorCRE/carr-system' else {self.extra_repos!r}.get(repo, [])))
else:
    assert sys.argv[1] == 'call'
    if {self.close_error!r} and sys.argv[2] == 'close-work-request-slice':
        print('synthetic version conflict', file=sys.stderr)
        sys.exit(1)
    if {self.evidence_error!r} and sys.argv[2] == 'complete-work-request':
        print('missing_user_journey_ref', file=sys.stderr)
        sys.exit(1)
    print(json.dumps({self.card!r} if sys.argv[2] == 'work-request-card' else {{'ok': True}}))
'''
        for path in (self.root / 'bin/gh', self.root / 'run.sh'):
            path.write_text(adapter)
            path.chmod(0o755)
        result = subprocess.run([sys.executable, str(self.root / 'bin/close-work-request'), *args],
                                env={**os.environ, 'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH']},
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []
        return result, calls

    def test_github_failure_reports_cause_without_writes(self):
        self.gh_error = True
        result, calls = self.run_close('WR557.S5', '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('synthetic GitHub failure', result.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])

    def test_dry_run_prints_versioned_close_without_writes(self):
        result, calls = self.run_close('WR557.S5')
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        close = plan['calls'][0]
        self.assertEqual(close['verb'], 'close-work-request-slice')
        self.assertEqual(close['args']['base_version'], 2)
        self.assertEqual(close['args']['change_refs'], [dict(pr_url=self.prs[0]['url'], merge_commit_sha='a' * 40)])
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])

    def test_apply_closes_only_the_selected_slice(self):
        self.card['slices'].append(dict(slice_id='WR557.S6', version=1, outcome='open'))
        result, calls = self.run_close('WR557.S5', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [c for c in calls if c[0] == 'call' and c[1] != 'work-request-card']
        self.assertEqual([c[1] for c in writes], ['close-work-request-slice'])
        self.assertEqual(json.loads(writes[0][2])['slice'], 5)

    def test_open_pr_prevents_slice_close_even_with_a_merge(self):
        self.prs.append(dict(body='Card: WR557.S5.J2', state='OPEN',
                             url='https://github.com/DoctorCRE/carr-system/pull/124', mergeCommit=None))
        result, calls = self.run_close('WR-000557', '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['calls'], [])
        self.assertIn('open pull request', result.stdout)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])

    def test_no_merge_or_nonfirst_or_prefix_card_never_closes(self):
        for body, state in [('Card: WR557.S5.J1', 'CLOSED'),
                            ('Summary\nCard: WR557.S5.J1', 'MERGED'),
                            ('Card: WR5570.S5.J1', 'MERGED'),
                            ('Card: WR557.S50.J1', 'MERGED'),
                            ('Card: WR557.S5.J1 trailing', 'MERGED')]:
            with self.subTest(body=body, state=state):
                self.prs[0].update(body=body, state=state)
                result, _ = self.run_close('WR557.S5')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['calls'], [])

    def test_bad_merge_evidence_refuses_before_writes(self):
        self.prs[0]['mergeCommit']['oid'] = 'abcdef'
        result, calls = self.run_close('WR557.S5', '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('full merge SHA', result.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])

    def test_completion_preserves_existing_refs_and_explicit_attestation_inputs(self):
        prior = dict(pr_url='https://github.com/jbookout/doctorcre-app/pull/50', merge_commit_sha='b' * 40)
        self.card['slices'].append(dict(slice_id='WR557.S2', version=2, outcome='done', change_refs=[prior]))
        evidence = self.root / 'evidence.json'
        evidence.write_text(json.dumps(dict(user_facing=True, user_journey_ref='EXERCISED synthetic journey',
            criterion_evidence=[dict(id='S5-CLOSE', evidence='Verified synthetic closure')])))
        result, calls = self.run_close('WR-000557', '--completion-evidence', str(evidence), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [c for c in calls if c[0] == 'call' and c[1] != 'work-request-card']
        self.assertEqual([c[1] for c in writes], ['complete-work-request', 'close-work-request-slice', 'complete-work-request'])
        completion = json.loads(writes[-1][2])
        self.assertEqual(completion['base_version'], 3)
        self.assertIn(prior, completion['change_refs'])
        self.assertTrue(completion['user_facing'])
        self.assertEqual(completion['criterion_evidence'][0]['evidence'], 'Verified synthetic closure')

    def test_missing_completion_evidence_is_visible_and_never_fabricated(self):
        result, _ = self.run_close('WR557')
        plan = json.loads(result.stdout)
        self.assertEqual([c['verb'] for c in plan['calls']], ['close-work-request-slice'])
        self.assertIn('--completion-evidence', plan['blocked'][-1]['reason'])

    def test_apply_missing_completion_evidence_does_not_close_slices(self):
        result, calls = self.run_close('WR557', '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])

    def test_cross_repository_merges_keep_their_urls(self):
        app = dict(body='Card: WR557.S5.J2', state='MERGED',
                   url='https://github.com/jbookout/doctorcre-app/pull/321', mergeCommit=dict(oid='c' * 40))
        self.extra_repos['jbookout/doctorcre-app'] = [app]
        result, _ = self.run_close('WR557.S5')
        refs = json.loads(result.stdout)['calls'][0]['args']['change_refs']
        self.assertEqual(len(refs), 2)
        self.assertIn(dict(pr_url=app['url'], merge_commit_sha='c' * 40), refs)

    def test_truncated_search_refuses_before_writes(self):
        self.prs *= 1000
        result, calls = self.run_close('WR557.S5', '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('truncated', result.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])

    def test_slice_write_failure_stops_before_completion(self):
        self.close_error = True
        evidence = self.root / 'evidence.json'
        evidence.write_text(json.dumps(dict(user_facing=False,
            criterion_evidence=[dict(id='S5-CLOSE', evidence='Synthetic check')])))
        result, calls = self.run_close('WR557', '--completion-evidence', str(evidence), '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('version conflict', result.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'],
                         ['work-request-card', 'complete-work-request', 'close-work-request-slice'])

    def test_invalid_completion_evidence_refuses_before_slice_writes(self):
        self.evidence_error = True
        evidence = self.root / 'evidence.json'
        evidence.write_text(json.dumps(dict(user_facing=True,
            criterion_evidence=[dict(id='S5-CLOSE', evidence='Synthetic check')])))
        result, calls = self.run_close('WR557', '--completion-evidence', str(evidence), '--apply')
        self.assertEqual(result.returncode, 1)
        self.assertIn('user_journey_ref', result.stderr)
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card', 'complete-work-request'])
        self.assertTrue(json.loads(calls[-1][2])['dry_run'])

    def test_confirmed_closed_is_an_idempotent_noop(self):
        self.card['state'] = 'confirmed_closed'
        self.gh_error = True
        result, calls = self.run_close('WR557', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['calls'], [])
        self.assertEqual([c[1] for c in calls if c[0] == 'call'], ['work-request-card'])


if __name__ == '__main__':
    unittest.main()
