"""Exercise proof CLI success, refusal and bounded local actor fixtures."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from proof_release import read_release, release_revision, MAX_BYTES

RUNNER = Path(__file__).with_name('prove-calendar-snapshot-strict-instants.py')
SPEC = importlib.util.spec_from_file_location('calendar_proof', RUNNER)
assert SPEC is not None and SPEC.loader is not None
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


class ProofCLI(unittest.TestCase):
    def test_shared_reader_refuses_bad_identity_status_and_size(self):
        recorded = json.loads(RUNNER.with_name('fixtures').joinpath('proof-release-production.json').read_text())
        for payload in ({}, [], None, {'ok': False},
                        {**recorded, 'env': {'value': 'staging'}},
                        {**recorded, 'git_sha': {'value': 'x' * 40}},
                        {**recorded, 'git_sha': 'a' * 40}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                release_revision(payload)
        for response in (json.dumps(recorded) + '\n302', 'not JSON\n200',
                         'x' * (MAX_BYTES + 5)):
            with self.subTest(response=response[:30]), self.assertRaises(ValueError):
                read_release(lambda argv: response, lambda: 1)
        for budget in (0, -1, float('nan'), float('inf')):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                read_release(lambda argv: self.fail('expired read executed'), lambda: budget)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, text=True, timeout=5)

    def fixture(self, value, *args):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.json'
            path.write_text(json.dumps(value))
            return self.run_cli('--fixture', '--fixture-file', str(path), '--timeout-seconds', '2', *args)

    def test_help_and_exact_flags(self):
        self.assertEqual(self.run_cli('--help').returncode, 0)
        result = self.run_cli('--fixture', '--timeout-seconds', '30')
        self.assertEqual(result.returncode, 0, result.stderr)
        counts = json.loads(result.stdout)
        self.assertEqual([counts[k] for k in ('meetings', 'reviews', 'alternate_times', 'calendar_reads')], [1, 1, 1, 1])
        self.assertEqual(counts['mode'], 'fixture')

    def test_recorded_production_release_shape(self):
        value = copy.deepcopy(PROOF.FIXTURE)
        value['release'] = json.loads(RUNNER.with_name('fixtures').joinpath('proof-release-production.json').read_text())
        value['expected_revision'] = value['release']['git_sha']['value']
        result = self.fixture(value)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_contracts_fail(self):
        for target in ('observed_at', 'start', 'end', 'alternate', 'review_start', 'review_end'):
            value = copy.deepcopy(PROOF.FIXTURE)
            calendar = value['calendar']
            if target == 'observed_at':
                calendar[target] = '2026-02-31T10:00:00Z'
            elif target == 'alternate':
                calendar['items'][0]['times'] = ['2026-02-31T12:00:00Z']
            elif target.startswith('review_'):
                calendar['follow_up_reviews'][0][target.removeprefix('review_')] = '2026-02-31T09:00:00Z'
            else:
                calendar['items'][0][target] = '2026-03-02T24:00:00Z'
            with self.subTest(target=target):
                self.assertNotEqual(self.fixture(value).returncode, 0)

    def test_missing_data_identity_and_false_coverage_fail(self):
        for patch in ({'state': 'unavailable'}, {'observed_at': None}, {'items': None},
                      {'state': 'ready', 'notes': []}, {'notes': []},
                      {'observed_at': '2026-03-01T10:00:00Z'},
                      {'observed_at': '2026-03-02T12:00:00Z'}, {'malformed_items': -1}):
            value = copy.deepcopy(PROOF.FIXTURE)
            value['calendar'].update(patch)
            with self.subTest(patch=patch):
                self.assertNotEqual(self.fixture(value).returncode, 0)
        for release in ({}, {'git_sha': '2' * 40}, {'git_sha': 'invalid'}):
            value = copy.deepcopy(PROOF.FIXTURE)
            value['release'] = release
            self.assertNotEqual(self.fixture(value).returncode, 0)

    def test_current_empty_is_a_usable_read(self):
        value = copy.deepcopy(PROOF.FIXTURE)
        value['calendar'].update(state='empty', notes=[], items=[], follow_up_reviews=[], malformed_items=0,
                                 sources={'outlook_calendar': {'state': 'ready'}})
        self.assertEqual(self.fixture(value).returncode, 0)

    def test_stub_local_actor_exact_arguments_auth_timeout_and_json_failures(self):
        programs = [
            ('import json; print(' + repr(json.dumps(PROOF.FIXTURE['calendar'])) + ')', True),
            ('sys.exit(1)', False),
            ('print("not JSON")', False),
            ('import time; time.sleep(2)', False),
            ('print("x" * 1048577)', False),
        ]
        for program, passes in programs:
            with self.subTest(program=program[:30]), tempfile.TemporaryDirectory() as directory:
                wrapper = Path(directory) / 'run.sh'
                wrapper.write_text('#!' + sys.executable + '\nimport sys\n'
                                   'assert sys.argv[1:] == ["call", "calendar-meeting-prep", "{}"]\n' + program + '\n')
                wrapper.chmod(0o700)
                result = self.run_cli('--fixture', '--fixture-wrapper', str(wrapper), '--timeout-seconds', '0.3')
                self.assertEqual(result.returncode == 0, passes, result.stderr)

    def test_cli_refuses_invalid_budgets_and_live_fixture_options(self):
        for budget in ('0', '-1', '31', 'nan'):
            self.assertNotEqual(self.run_cli('--fixture', '--timeout-seconds', budget).returncode, 0)
        self.assertNotEqual(self.run_cli('--live', '--fixture-wrapper', '/unused').returncode, 0)

    def test_live_transport_release_ancestry_and_fail_closed_without_network(self):
        calendar = copy.deepcopy(PROOF.FIXTURE['calendar'])
        calendar.update(state='empty', notes=[], items=[], follow_up_reviews=[], malformed_items=0,
                        observed_at=PROOF.datetime.now(PROOF.timezone.utc).isoformat(),
                        sources={'outlook_calendar': {'state': 'ready'}})
        expected, deployed = '1' * 40, '2' * 40
        replies = [json.dumps({'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': deployed}}) + '\n200', '', json.dumps(calendar)]
        with patch.object(sys, 'argv', [str(RUNNER), '--live', '--timeout-seconds', '30', '--expected-revision', expected]), patch.object(PROOF, 'command', side_effect=replies) as execute:
            self.assertEqual(PROOF.main(), 0)
            commands = [call.args[0] for call in execute.call_args_list]
            self.assertEqual(commands[0][-1], 'https://api.doctorcre.com/release')
            self.assertEqual(commands[1], ['git', 'merge-base', '--is-ancestor', expected, deployed])
            self.assertEqual(commands[2], [str(PROOF.ROOT / 'run.sh'), 'call', 'calendar-meeting-prep', '{}'])
        with patch.object(sys, 'argv', [str(RUNNER), '--live', '--expected-revision', expected]), patch.object(PROOF, 'command', side_effect=[json.dumps({'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': deployed}}) + '\n200', ValueError('unrelated release')]) as execute:
            self.assertEqual(PROOF.main(), 1)
            self.assertEqual(execute.call_count, 2)


if __name__ == '__main__':
    unittest.main()
