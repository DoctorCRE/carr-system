import importlib.util
import pathlib
import unittest
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location('home_fixture', pathlib.Path(__file__).with_name('staging-home-fixture.py'))
home = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home)


class HomeFixtureTests(unittest.TestCase):
    def test_seeds_only_the_verified_run_owned_deal_and_note(self):
        journey, conn = MagicMock(), MagicMock()
        journey.identities.return_value = {'deal': 'owned-deal', 'note_b': 'owned-note'}
        journey.prepare.return_value = {'deal': {'id': 'owned-deal'}}
        result = home.prepare(journey, conn, 'owned-run')
        journey.prepare.assert_called_once_with(conn, 'owned-run')
        journey.verify.assert_called_once_with(conn, 'owned-run')
        calls = conn.cursor.return_value.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[1], ('owned-deal',))
        self.assertEqual(calls[1].args[1], ('owned-note', 'owned-deal'))
        self.assertIn("next_date=date '2000-01-01'", calls[0].args[0])
        self.assertIn("kind='next_step'", calls[1].args[0])
        conn.commit.assert_called_once()
        self.assertEqual(result, journey.prepare.return_value)

    def test_refuses_unverified_provenance_before_mutating(self):
        journey, conn = MagicMock(), MagicMock()
        journey.verify.side_effect = ValueError('fixture provenance changed')
        with self.assertRaisesRegex(ValueError, 'provenance changed'):
            home.prepare(journey, conn, 'owned-run')
        conn.cursor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
