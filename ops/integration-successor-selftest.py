#!/usr/bin/env python3
"""Integration-time contenders, immutable main union, and predecessor controls."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import migration_number_contract as contract

class IntegrationSuccessors(unittest.TestCase):
    def test_two_same_slot_contenders_follow_integrated_main(self):
        first = contract.allocate_integration_successors(['0748_base.sql'], ['0749_first.sql'])
        self.assertEqual(first, {'0749_first.sql': '0749_first.sql'})
        second = contract.allocate_integration_successors(['0748_base.sql', '0749_first.sql'], ['0749_second.sql'])
        self.assertEqual(second, {'0749_second.sql': '0750_second.sql'})
        contract.validate_migration_names(['0748_base.sql', '0749_first.sql', *second.values()])

    def test_ordered_batch_and_burned_slots(self):
        self.assertEqual(contract.allocate_integration_successors(['0532_room_dispatch_spine_scac_successor.sql'], ['0533_b.sql', '0533_a.sql']),
                         {'0533_a.sql': '0537_a.sql', '0533_b.sql': '0538_b.sql'})

    def test_main_union_requires_exact_bytes_and_forward_order(self):
        main={'0748_base.sql': 'base', '0749_first.sql': 'first'}
        contract.validate_integration_union(main, {**main, '0750_second.sql': 'second'})
        for bad in [{**main, '0749_first.sql': 'edited'}, {'0748_base.sql': 'base'},
                    {**main, '0749_second.sql': 'second'}, {**main, '0747_late.sql': 'late'}]:
            with self.assertRaises(contract.MigrationNumberError):
                contract.validate_integration_union(main, bad)

    def test_version_contenders_get_next_current_main_predecessor(self):
        self.assertEqual(contract.allocate_registry_successor([97]), (97, 98))
        self.assertEqual(contract.allocate_registry_successor([97, 98]), (98, 99))
        with self.assertRaises(contract.MigrationNumberError): contract.allocate_registry_successor([])

    def test_invalid_pending_identity_and_exhaustion_refused(self):
        for pending in [['not.sql'], ['0749a_letter.sql'], ['0749_one.sql', '0749_one.sql']]:
            with self.assertRaises(contract.MigrationNumberError):
                contract.allocate_integration_successors(['0748_base.sql'], pending)
        with self.assertRaises(contract.MigrationNumberError):
            contract.allocate_integration_successors(['9999_last.sql'], ['0749_one.sql'])

if __name__ == '__main__': unittest.main()
