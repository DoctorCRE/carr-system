#!/usr/bin/env python3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from migration_number_contract import MigrationNumberError, validate_migration_names


class MigrationSlots(unittest.TestCase):
    def test_two_files_cannot_claim_one_new_slot(self):
        with self.assertRaisesRegex(MigrationNumberError, 'unregistered collision 0890'):
            validate_migration_names(['0890_first.sql', '0890_second.sql'])

    def test_withdrawn_slots_cannot_be_reused(self):
        with self.assertRaisesRegex(MigrationNumberError, 'permanently burned migration slot 0533'):
            validate_migration_names(['0533_reused.sql'])


if __name__ == '__main__':
    unittest.main()
