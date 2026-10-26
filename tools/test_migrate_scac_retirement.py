"""Applied approval-era repairs no longer alter forward migration ordering."""
import unittest
import migrate


class MigrationOrderingTests(unittest.TestCase):
    def test_retired_recovery_files_use_lexical_individual_batches(self):
        names = (
            '0940_codex_job_delegation_control_refs.sql',
            '0944_retire_orphan_control_inventory.sql',
            '0947_allow_approved_control_evidence_refresh.sql',
            '0950_remove_retired_workflow_manifest_binding.sql',
            '0951_deal_vendor_assignments.sql',
            '0952_deal_vendor_grant_retirements.sql',
            '0953_morning_brief_chicago_calendar.sql',
            '0954_lead_workspace_set_reads.sql',
            '0955_retrieval_concept_question_matching.sql',
            '0957_retire_scac_catalog_commit_check.sql',
        )
        files = [(name, 'select 1;', 'synthetic') for name in names]
        for offset in range(len(files)):
            with self.subTest(offset=offset):
                pending = files[offset:]
                self.assertEqual(migrate.migration_batches(pending), [[item] for item in pending])
                selected, held = migrate.migrations_through(files, pending, pending[0][0])
                self.assertEqual(selected, pending[:1])
                self.assertEqual(held, pending[1:])


if __name__ == '__main__':
    unittest.main()
