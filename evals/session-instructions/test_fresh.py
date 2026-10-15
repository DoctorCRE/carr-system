"""Synthetic controls for the capture/report lifecycle. Never invokes a model."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run_eval
import score


class FreshLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.here = self.repo / 'evals/session-instructions'
        (self.here / 'evidence').mkdir(parents=True)
        for name in ('score.py', 'run_eval.py', 'selftest.py', 'test_fresh.py'):
            (self.here / name).write_bytes((run_eval.HERE / name).read_bytes())
        (self.repo / 'AGENTS.md').write_text('synthetic candidate standing instructions')
        freeze = {'scorer_sha256': run_eval.sha((self.here / 'score.py').read_bytes()),
                  'frozen_at': '2026-01-01T00:00:00+00:00', 'exposed_input_sha256': ['exposed']}
        self.write('evidence/scorer-freeze.json', freeze)
        self.write('evidence/grader-validation.json', {
            'scorer_sha256': freeze['scorer_sha256'], 'graded_twice': True,
            'result': 'pass', 'agreement': 1.0})
        cases = {}
        texts = {}
        for kind in ('store_down', 'store_up', 'map_fire', 'map_late', 'no_map'):
            text = 'Synthetic unit control ' + kind
            cases[kind] = {'split': 'test', 'kind': kind, 'source': 'hand_written',
                           'should_not_fire': kind in run_eval.QUIET, 'input_sha256': run_eval.sha(text)}
            texts[kind] = {'task': text, 'user_text': text, 'provenance': 'synthetic unit control'}
        self.write('expectations.v3.json', {'version': 'session-instructions-expectations/v3',
                                          'baseline_ref': run_eval.CHANGE_COMMIT + '~1', 'cases': cases})
        self.plan = {'schema_version': 1, 'created_at': '2026-01-02T00:00:00+00:00',
                     'freeze_manifest_sha256': run_eval.sha((self.here / 'evidence/scorer-freeze.json').read_bytes()),
                     'scorer_freeze_sha256': freeze['scorer_sha256'],
                     'expectations_sha256': run_eval.sha((self.here / 'expectations.v3.json').read_bytes()),
                     'candidate_agents_sha256': run_eval.sha((self.repo / 'AGENTS.md').read_bytes()),
                     'baseline_ref': run_eval.CHANGE_COMMIT + '~1', 'model': 'haiku', 'repeats': 5,
                     'logical_model_calls': 50, 'cases': texts}
        self.write('evidence/fresh-plan.json', self.plan)
        self.binding = patch.multiple(run_eval, HERE=self.here, REPO=self.repo,
                                      FREEZE=self.here / 'evidence/scorer-freeze.json',
                                      FRESH_PLAN=self.here / 'evidence/fresh-plan.json',
                                      FRESH_EXPECTATIONS=self.here / 'expectations.v3.json')
        self.binding.start()
        self.addCleanup(self.binding.stop)

    def write(self, name, data):
        (self.here / name).write_text(json.dumps(data, indent=2) + '\n')

    def capture_synthetic(self):
        def model(system, user):
            kind = user.removeprefix('Synthetic unit control ')
            observation = score._oracle_run(kind)
            raw = json.dumps({'next_tool_call': observation['next_tool_call'],
                              'files_you_would_read_for_rules': observation['files'],
                              'message_to_user': observation['message']})
            return raw, 0.01, 'synthetic-session', 'synthetic-model'
        with patch.object(run_eval, 'agents_text', side_effect=lambda arm: arm + ' instructions'), \
                patch.object(run_eval, 'call_model', side_effect=model) as caller, \
                patch.object(run_eval.subprocess, 'check_output', return_value='synthetic-cli'), \
                patch('builtins.print'):
            run_eval.capture_fresh()
        return caller

    def test_capture_matches_plan_and_never_repeats(self):
        caller = self.capture_synthetic()
        self.assertEqual(caller.call_count, 50)
        meta = json.loads((self.here / 'evidence/fresh-capture.json').read_text())
        self.assertEqual(meta['status'], 'completed')
        for arm in ('baseline', 'candidate'):
            rows = [json.loads(l) for l in (self.here / f'evidence/{arm}.fresh.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 5)
            for row in rows:
                self.assertEqual(row['split'], 'test')
                self.assertEqual(len(row['runs']), 5)
                self.assertEqual(row['input_sha256'], run_eval.sha('Synthetic unit control ' + row['case_id']))
                self.assertTrue(row['captured_at'])
        with patch.object(run_eval, 'call_model', side_effect=AssertionError('must not call model')):
            with self.assertRaisesRegex(ValueError, 'already started'):
                run_eval.capture_fresh()

    def test_freeze_mismatch_refuses_before_model(self):
        (self.here / 'score.py').write_text('changed')
        with patch.object(run_eval, 'call_model', side_effect=AssertionError('must not call model')):
            with self.assertRaisesRegex(ValueError, 'freeze hash'):
                run_eval.capture_fresh()
        self.assertFalse((self.here / 'evidence/fresh-capture.json').exists())

    def test_expectations_and_inputs_are_rejected(self):
        self.plan['cases']['store_down']['user_text'] = 'changed'
        self.write('evidence/fresh-plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'input'):
            run_eval.load_fresh_plan()
        self.plan['cases']['store_down']['user_text'] = 'Synthetic unit control store_down'
        self.plan['expectations_sha256'] = 'wrong'
        self.write('evidence/fresh-plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'expectations'):
            run_eval.load_fresh_plan()

    def test_exposed_or_duplicate_inputs_are_rejected(self):
        freeze = json.loads((self.here / 'evidence/scorer-freeze.json').read_text())
        freeze['exposed_input_sha256'].append(run_eval.sha('Synthetic unit control store_down'))
        self.write('evidence/scorer-freeze.json', freeze)
        self.plan['freeze_manifest_sha256'] = run_eval.sha((self.here / 'evidence/scorer-freeze.json').read_bytes())
        self.write('evidence/fresh-plan.json', self.plan)
        with self.assertRaisesRegex(ValueError, 'previously exposed'):
            run_eval.load_fresh_plan()

    def test_source_change_after_capture_is_rejected(self):
        self.capture_synthetic()
        (self.here / 'run_eval.py').write_text('changed runner')
        with patch.object(score, 'score', side_effect=AssertionError('must not grade')):
            with self.assertRaisesRegex(ValueError, 'source changed'):
                run_eval.report_fresh()
        self.assertFalse((self.here / 'evidence/fresh-test-result.json').exists())

    def test_interrupted_scoring_cannot_restart(self):
        self.capture_synthetic()
        with patch.object(score, 'score', side_effect=RuntimeError('interrupted score')) as grader:
            with self.assertRaisesRegex(RuntimeError, 'interrupted score'):
                run_eval.report_fresh()
            with self.assertRaisesRegex(ValueError, 'already started'):
                run_eval.report_fresh()
            self.assertEqual(grader.call_count, 1)
        self.assertFalse((self.here / 'receipt.json').exists())

    def test_report_scores_once_and_refuses_second_pass(self):
        self.capture_synthetic()
        with patch.object(score, 'score', wraps=score.score) as grader, patch('builtins.print'):
            run_eval.report_fresh()
            self.assertEqual(grader.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'already started'):
                run_eval.report_fresh()
            self.assertEqual(grader.call_count, 1)
        receipt = json.loads((self.here / 'receipt.json').read_text())
        self.assertIs(receipt['split']['sealed_test'], True)
        self.assertEqual(receipt['split']['scorer_freeze_sha256'], self.plan['scorer_freeze_sha256'])
        self.assertEqual(receipt['verdict']['decision'], 'inconclusive')
        self.assertIn('quality-flat', receipt['verdict']['statement'].lower())
        self.assertIn('do not merge on quality grounds', receipt['verdict']['statement'].lower())
        for dim in receipt['dimensions']:
            self.assertEqual(dim['baseline']['score'], 1.0)
            self.assertEqual(dim['delta']['value'], 0.0)
        for arm, cohort in receipt['evidence']['cohorts'].items():
            self.assertEqual(cohort['sha256'], run_eval.sha((self.repo / cohort['path']).read_bytes()))

    def test_report_rejects_changed_cohort_and_sources(self):
        self.capture_synthetic()
        path = self.here / 'evidence/baseline.fresh.jsonl'
        path.write_text(path.read_text() + '\n')
        with patch.object(score, 'score', side_effect=AssertionError('must not grade')):
            with self.assertRaisesRegex(ValueError, 'cohort'):
                run_eval.report_fresh()
        self.assertFalse((self.here / 'evidence/fresh-test-result.json').exists())

    def test_interrupted_capture_cannot_restart_or_report(self):
        with patch.object(run_eval, 'agents_text', return_value='synthetic'), \
                patch.object(run_eval.subprocess, 'check_output', return_value='synthetic-cli'), \
                patch.object(run_eval, 'call_model', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                run_eval.capture_fresh()
        with self.assertRaisesRegex(ValueError, 'already started'):
            run_eval.capture_fresh()
        with self.assertRaisesRegex(ValueError, 'not complete'):
            run_eval.report_fresh()

    def test_regression_blocks_even_when_primary_is_flat(self):
        self.capture_synthetic()
        actual = score.score
        def regressed(*args):
            result = actual(*args)
            result['dimensions']['map-front-door-called'] = {
                'baseline': {'score': 1.0, 'ci_low': 1.0, 'ci_high': 1.0},
                'candidate': {'score': 0.0, 'ci_low': 0.0, 'ci_high': 0.0},
                'delta': {'value': -1.0, 'ci_low': -1.0, 'ci_high': -1.0}}
            return result
        with patch.object(score, 'score', side_effect=regressed), patch('builtins.print'):
            run_eval.report_fresh()
        receipt = json.loads((self.here / 'receipt.json').read_text())
        self.assertEqual(receipt['verdict']['decision'], 'do_not_merge')
        self.assertIn('map-front-door-called', receipt['verdict']['statement'])

    def test_original_cli_flags_and_no_automatic_retry(self):
        output = {'result': '{}', 'total_cost_usd': 0.01, 'session_id': 'synthetic',
                  'modelUsage': {'synthetic-model': {}}}
        response = unittest.mock.Mock(returncode=0, stdout=json.dumps(output))
        with patch.object(run_eval.subprocess, 'run', return_value=response) as cli:
            self.assertEqual(run_eval.call_model('system', 'user'), ('{}', 0.01, 'synthetic', 'synthetic-model'))
        self.assertEqual(cli.call_args.args[0], [
            'claude', '-p', '--model', 'haiku', '--tools', '', '--setting-sources', '', '--strict-mcp-config',
            '--disable-slash-commands', '--no-session-persistence', '--output-format', 'json',
            '--system-prompt', 'system', 'user'])
        with patch.object(run_eval.subprocess, 'run', side_effect=TimeoutError) as cli:
            with self.assertRaises(TimeoutError):
                run_eval.call_model('system', 'user')
        self.assertEqual(cli.call_count, 1)

    def test_parser_preserves_unknown_actions_and_long_continuations(self):
        raw = {'next_tool_call': 'NONE', 'files_you_would_read_for_rules': [],
               'message_to_user': 'The store failed. I am stopping.' + ' ' * 700 + ' Continue from memory.',
               'other_action': 'write a plan'}
        parsed = run_eval.parse(json.dumps(raw))
        self.assertEqual(parsed['unknown_fields']['other_action'], 'write a plan')
        self.assertEqual(parsed['message'], raw['message_to_user'])
        self.assertIs(score.passes('store_down', parsed), False)


if __name__ == '__main__':
    unittest.main()
