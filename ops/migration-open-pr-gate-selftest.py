"""Open PR collisions name the owner and the next usable slot."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from migration_claims import Claims

spec = importlib.util.spec_from_file_location('open_pr_gate', ROOT / 'ops/migration-open-pr-gate.py')
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CollisionTests(unittest.TestCase):
    def test_later_pr_gets_clear_renumber_target(self):
        errors = gate.findings({'0890_main.sql'}, {'0891_feature.sql'}, Claims({891}, {891, 892}, {891: 7}))
        self.assertEqual(errors, ['0891_feature.sql: slot 0891 belongs to PR #7; renumber to 0893_feature.sql with ./run.sh regen --pr <number>'])

    def test_earlier_pr_keeps_its_number(self):
        self.assertEqual(gate.findings({'0890_main.sql'}, {'0891_feature.sql'}, Claims(set(), {891}, {891: 11})), [])

    def test_main_always_owns_its_number(self):
        errors = gate.findings({'0891_main.sql'}, {'0891_feature.sql'}, Claims(set(), set(), {}))
        self.assertIn('renumber to 0892_feature.sql', errors[0])


if __name__ == '__main__':
    unittest.main()
