"""Open PR migration ownership through the GitHub read adapter."""
import unittest
from migration_claims import claims


class ClaimsTests(unittest.TestCase):
    def test_earlier_pr_owns_slot_and_all_peer_slots_block_allocation(self):
        data = {
            'repos/jbookout/carr-system/pulls?state=open&per_page=100': [
                [{'number': 7}, {'number': 9}, {'number': 11}]],
            'repos/jbookout/carr-system/pulls/7/files?per_page=100': [[
                {'filename': 'migrations/0891_earlier.sql', 'status': 'added'},
                {'filename': 'migrations/0800_history.sql', 'status': 'modified'}]],
            'repos/jbookout/carr-system/pulls/11/files?per_page=100': [[
                {'filename': 'migrations/0892_later.sql', 'status': 'renamed'}]],
        }
        result = claims(9, data.__getitem__)
        self.assertEqual(result.earlier, {891})
        self.assertEqual(result.occupied, {891, 892})
        self.assertEqual(result.owners, {891: 7, 892: 11})

    def test_missing_or_truncated_provider_data_is_not_an_empty_claim_set(self):
        for payload in ({}, [[{'number': '7'}]], [[{'number': 7}]]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                claims(9, lambda _: payload)

    def test_default_command_recognizes_its_own_open_branch(self):
        def read(path):
            self.assertTrue(path.endswith('pulls?state=open&per_page=100'))
            return [[{'number': 9, 'head': {'ref': 'feature'}}]]
        result = claims(None, read, branch='feature')
        self.assertEqual(result.earlier, set())
        self.assertEqual(result.occupied, set())


if __name__ == '__main__':
    unittest.main()
