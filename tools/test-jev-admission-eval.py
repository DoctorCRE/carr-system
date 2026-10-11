"""The admission eval must follow the reviewed caller policy, with live controls."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('admission_eval', ROOT / 'evals/jev-judgments/run_eval.py')
assert spec is not None and spec.loader is not None
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class AdmissionEvalTests(unittest.TestCase):
    def test_successor_keeps_training_and_test_inputs_disjoint(self):
        cases = json.loads(evaluation.EXPECTATIONS.read_text())['cases'].values()
        train = {case['input_sha256'] for case in cases if case['split'] == 'train'}
        test = {case['input_sha256'] for case in cases if case['split'] == 'test'}
        self.assertFalse(train & test)

    def test_retired_mechanical_sites_are_not_owed_paid_calls(self):
        for site in ('rule_trigger_delivery', 'jev_session_watch', 'jev_defect_class',
                     'jev_executor_tier', 'jev_code_review', 'jev_requirements'):
            with self.subTest(site=site):
                self.assertTrue(evaluation.label({'site': site, 'session': True}))

    def test_live_sites_remain_paid_and_transport_is_recorded(self):
        client = evaluation._load(ROOT)
        for site in ('jev_deal_read', 'jev_model_route', 'jevlint_review', 'seat_health'):
            case = {'site': site, 'session': True}
            with self.subTest(site=site), tempfile.TemporaryDirectory() as scratch:
                self.assertFalse(evaluation.label(case))
                self.assertEqual(evaluation.observe(client, case, pathlib.Path(scratch)),
                                 {'paid': True, 'refusal': None})


if __name__ == '__main__':
    unittest.main()
