import json
import unittest
from progress_orchestrator import project


class OrchestratorTests(unittest.TestCase):
    def test_review_return_merge_and_release_are_distinct_facts(self):
        state = {'tasks': {'p': {'title': 'Deal room search', 'repo': 'jbookout/doctorcre-app', 'pr': 12,
            'stage': 'fixing', 'executor': 'Codex', 'pr_phase': 'Changes requested'}}}
        result = project(state, observed_at='2026-10-21T19:01:00Z')
        self.assertEqual(result['cards'][0]['state'], 'fixing')
        self.assertEqual(len(result['cards']), 1)
        self.assertEqual(result['cards'][0]['owner'], 'Codex')
        state['tasks']['p'].update(stage='merged', pr_phase='Merged', merge_sha='a'*40)
        self.assertEqual(project(state)['cards'][0]['state'], 'merged')
        state['tasks']['p']['stage'] = 'live'
        state['tasks']['p']['evidence'] = 'Matching app release contains the merge; consumer exercise passed'
        self.assertEqual(project(state)['history'][0]['delivery_evidence'], state['tasks']['p']['evidence'])
        self.assertEqual(project(state)['history'][0]['state'], 'live')

    def test_projection_excludes_private_payloads_and_keeps_unknown_explicit(self):
        state = {'tasks': {'one': {'title': 'Calendar availability', 'stage': 'done',
            'task': 'RAW BRIEF', 'description': 'PRIVATE DESCRIPTION', 'result_detail': 'RAW RESULT',
            'claim': 'SECRET CLAIM', 'executor': '/Users/private', 'summary': 'contact person@example.com'}}}
        result = project(state)
        encoded = json.dumps(result)
        for private in ('RAW BRIEF', 'PRIVATE DESCRIPTION', 'RAW RESULT', 'SECRET CLAIM', '/Users/private', 'person@example.com'):
            self.assertNotIn(private, encoded)
        self.assertEqual(result['cards'][0]['state'], 'review')
        self.assertEqual(result['cards'][0]['owner'], 'Not recorded')
        self.assertEqual(result['sources']['github']['state'], 'unknown')

    def test_portfolio_contains_backend_and_app_and_bounds_completed_history(self):
        tasks = {str(i): {'title': f'Feature {i}', 'stage': 'live', 'repo': 'DoctorCRE/carr-system'} for i in range(100)}
        tasks['active'] = {'title': 'Shared search contract', 'stage': 'build', 'repo': 'DoctorCRE/carr-system'}
        result = project({'tasks': tasks}, portfolio={'tasks': {'app': {'title': 'Deal room search', 'stage': 'review', 'repo': 'jbookout/doctorcre-app'}}})
        self.assertEqual(len(result['history']), 20)
        self.assertEqual(len(result['cards']), 2)
        self.assertEqual(set(result['sources']), {'github', 'portfolio'})

    def test_duplicate_pull_requests_share_one_card(self):
        task = {'title': 'Deal room search', 'stage': 'review', 'repo': 'jbookout/doctorcre-app', 'pr': 12}
        result = project({'tasks': {'local': task}}, portfolio={'tasks': {'github': task}})
        self.assertEqual(len(result['cards']), 1)
        self.assertEqual(result['cards'][0]['prs'], ['https://github.com/jbookout/doctorcre-app/pull/12'])

    def test_scheduled_refresh_runs_every_five_minutes_without_a_model(self):
        import plistlib
        from pathlib import Path
        config = plistlib.loads((Path(__file__).parents[1]/'ops/launchd/local.carr-progress-board.plist').read_bytes())
        self.assertEqual(len(config['StartCalendarInterval']), 12)
        self.assertEqual(config['ProgramArguments'][1], '{{REPO}}/ops/progress-board-render.sh')


if __name__ == '__main__':
    unittest.main()
