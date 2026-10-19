import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch


class DispatchCardTests(unittest.TestCase):
    flags = ('--wr', 'WR-14', '--slice', 'S1', '--title', 'Job dispatch',
             '--description', 'Keep jobs attached to their requests.')
    def send(self, root, *flags, card=None, task='Repair dispatch.'):
        reply = card or {'human_ref': 'WR-000014', 'title': 'Job dispatch',
                         'desired_outcome': 'Keep every job attached to its request.', 'state': 'captured'}
        with patch.dict('os.environ', {'PROGRESS_BOARD_ROOT': root}), \
             patch.object(dispatch.verb_io, '_run_verb', return_value=reply), \
             patch.object(dispatch, 'dispatch', return_value={'status': 'completed'}) as launch, \
             contextlib.redirect_stderr(io.StringIO()) as error, \
             contextlib.redirect_stdout(io.StringIO()):
            status = dispatch.main(['--registry', str(Path(root) / 'desks.json'),
                                    'send', 'builder', task, *flags])
        return status, launch, error.getvalue()

    def test_captured_request_allocates_card_before_launch(self):
        with tempfile.TemporaryDirectory() as root:
            status, launch, error = self.send(root, '--wr', 'WR-14', '--slice', 'S1',
                '--title', 'Job dispatch', '--description', 'Keep jobs attached to their requests.')
            self.assertEqual(status, 0, error)
            state = json.loads((Path(root) / 'boards' / 'all-repos.json').read_text())
            task = next(iter(state['tasks'].values()))
            self.assertEqual(task['card_id'], 'WR14.S1.J1')
            self.assertEqual(task['work_request'], 'WR-000014')
            self.assertIn('Card ID: WR14.S1.J1', launch.call_args.args[1])
            self.assertIn('Work Request: WR-000014', launch.call_args.args[1])

    def test_missing_fields_unknown_wr_and_invalid_copy_never_launch(self):
        cases = [('--wr', 'unknown'), ('--slice', 'S0'), ('--slice', 'S2'),
                 ('--title', 'Four words are excessive'), ('--title', 'Fix the'),
                 ('--description', 'WR14 needs work.'),
                 ('--description', 'First. Second. Third.')]
        for field, value in cases + [(f, '') for f in ('--slice', '--title', '--description')]:
            flags = list(self.flags)
            flags[flags.index(field) + 1] = value
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as root:
                status, launch, error = self.send(root, *flags)
                self.assertEqual(status, 2, error)
                launch.assert_not_called()
                self.assertFalse((Path(root) / 'boards' / 'all-repos.json').exists())
        with tempfile.TemporaryDirectory() as root:
            status, launch, error = self.send(root, *self.flags, card={'ok': True, 'error': 'not_found'})
            self.assertEqual(status, 2)
            launch.assert_not_called()
            self.assertIn('report-problem', error)
            status, launch, error = self.send(root, *self.flags, card={
                'human_ref': 'WR-000015', 'title': 'Different request', 'desired_outcome': 'Other work.'})
            self.assertEqual(status, 2)
            launch.assert_not_called()

    def test_open_pr_and_review_fix_rounds_keep_one_card_and_job(self):
        from lib import progress_hierarchy as hierarchy
        import progress_board as board
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.send(root, *self.flags)[0], 0)
            path = Path(root) / 'boards' / 'all-repos.json'
            state = json.loads(path.read_text())
            body = ('Work Request: WR-000014\nCard ID: WR14.S1.J1.carr#42\n'
                    'Card title: Job dispatch\nCard description: Keep jobs attached to their requests.')
            pr = {'number': 42, 'title': 'Repair dispatch', 'body': body,
                  'url': 'https://github.com/jbookout/carr-system/pull/42', 'state': 'OPEN'}
            with patch.object(board, 'latest_release', return_value=None):
                state = board.assemble_all_repos(state, {'jbookout/carr-system': ([pr], [])})
            projected = hierarchy.project(state)
            self.assertEqual(list(projected['cards']), ['WR14.S1.J1.carr#42'])
            path.write_text(json.dumps(state))
            for round_name in ('review', 'fix'):
                status, launch, error = self.send(root, *self.flags, '--pr', 'carr#42')
                self.assertEqual(status, 0, error)
                self.assertIn('Card ID: WR14.S1.J1.carr#42', launch.call_args.args[1])
            status, launch, error = self.send(root, *self.flags, task='Own PR: #42\nRepair review findings.')
            self.assertEqual(status, 0, error)
            self.assertIn('Card ID: WR14.S1.J1.carr#42', launch.call_args.args[1])
            state = json.loads(path.read_text())
            self.assertEqual(len(state['tasks']), 1)
            self.assertEqual(max(state['card_jobs']['WR14.S1'].values()), 1)

    def test_sparse_slice_plan_keeps_ordinal_and_concurrent_sends_get_distinct_jobs(self):
        from concurrent.futures import ThreadPoolExecutor
        def call(verb, args):
            if verb == 'work-request-card':
                return {'human_ref': 'WR-000014', 'title': 'Job dispatch',
                        'desired_outcome': 'Keep related work together.', 'plan': {'id': 'plan'}}
            return {'slice_plan': {'slices': [
                {'ordinal': 4, 'slice_ref': 'dispatch', 'objective': 'Keep dispatch safe.'}]}}
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ', {'PROGRESS_BOARD_ROOT': root}):
            with ThreadPoolExecutor(max_workers=4) as pool:
                cards = list(pool.map(lambda i: dispatch.dispatch_card.prepare(
                    'WR-14', 'S4', 'Job dispatch', 'Keep related work together.', 'builder', call), range(8)))
            self.assertEqual({c['card_id'] for c in cards}, {f'WR14.S4.J{i}' for i in range(1, 9)})

    def test_corrupt_board_refuses_without_losing_existing_card_numbers(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'boards' / 'all-repos.json'
            path.parent.mkdir()
            path.write_text('{broken board')
            status, launch, error = self.send(root, *self.flags)
            self.assertEqual(status, 2)
            launch.assert_not_called()
            self.assertEqual(path.read_text(), '{broken board')

    def test_missing_work_request_refuses_before_launch_and_names_capture_call(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.object(dispatch, 'dispatch', return_value={'status': 'completed'}) as launch, \
             contextlib.redirect_stderr(io.StringIO()) as error, \
             contextlib.redirect_stdout(io.StringIO()):
            status = dispatch.main(['--registry', str(Path(root) / 'desks.json'),
                                    'send', 'builder', 'Repair dispatch.'])
        self.assertEqual(status, 2)
        launch.assert_not_called()
        self.assertIn('report-problem', error.getvalue())
        self.assertIn('--wr', error.getvalue())

    def test_emergency_records_os_actor_before_launch_and_weekly_report_lists_every_use(self):
        with tempfile.TemporaryDirectory() as root:
            results = Path(root) / 'results.jsonl'
            observed = []
            def launch(*args, **kwargs):
                observed.extend(json.loads(line) for line in results.read_text().splitlines())
                return {'status': 'completed'}
            with patch.object(dispatch, 'dispatch', side_effect=launch), \
                 patch.object(dispatch.verb_io, '_run_verb') as call, \
                 contextlib.redirect_stdout(io.StringIO()):
                for reason in ('Restore dispatch during outage.', 'Repair urgent refusal.'):
                    status = dispatch.main(['--registry', str(Path(root) / 'desks.json'),
                        '--results', str(results), 'send', 'builder', 'Repair dispatch.', '--emergency', reason])
                    self.assertEqual(status, 0)
                call.assert_not_called()
            self.assertEqual(len(observed), 3)
            self.assertTrue(observed[0]['actor']['user'])
            self.assertIsInstance(observed[0]['actor']['uid'], int)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(dispatch.main(['--results', str(results), 'emergency-report']), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(len(report['uses']), 2)
            self.assertEqual(report['uses'][0]['reason'], 'Restore dispatch during outage.')

    def test_weekly_report_is_scheduled_on_each_machine_and_fails_on_corrupt_log(self):
        import plistlib
        repo = Path(__file__).resolve().parents[2]
        schedule = plistlib.loads((repo / 'ops/launchd/com.carr.dispatch-emergencies-weekly.plist').read_bytes())
        self.assertEqual(schedule['StartCalendarInterval']['Weekday'], 1)
        self.assertIn('emergency-report', schedule['ProgramArguments'])
        services = json.loads((repo / 'ops/config/services.json').read_text())['services']
        service = next(s for s in services if s['key'] == 'dispatch-emergencies-weekly')
        self.assertEqual(service['environments'][0]['runs_on'], 'every')
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'results.jsonl'
            log.write_text('{broken audit')
            with self.assertRaises(ValueError):
                dispatch.dispatch_card.emergency_report(log)


if __name__ == '__main__':
    unittest.main()
