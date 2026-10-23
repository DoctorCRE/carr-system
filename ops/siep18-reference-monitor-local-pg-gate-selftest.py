#!/usr/bin/env python3
"""The monitor gate compares the latest reseal and retains historical bodies."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "siep18_gate", ROOT / "ops/siep18-reference-monitor-local-pg-gate.py"
)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class CatalogSourceTests(unittest.TestCase):
    def test_current_catalog_uses_the_deal_vendor_reseal(self):
        self.assertEqual(
            gate.LIVE_REGISTRY_MIGRATION,
            "migrations/0938_deal_vendor_assignments.sql",
        )

    def test_resealed_current_body_handles_uppercase_and_untagged_dollars(self):
        migration = gate.read_migration(ROOT, Path(gate.LIVE_REGISTRY_MIGRATION).name)
        definition = gate.current_function_sql(migration, gate.LIVE_REGISTRY_ORDINAL)
        expected = migration.split(
            "CREATE OR REPLACE FUNCTION ops.scac_mutation_catalog_v118_current()", 1
        )[1].split("AS $$", 1)[1].split("$$;", 1)[0].strip()
        self.assertEqual(gate.function_body(definition), expected)
        self.assertTrue(definition.endswith("end $$;"))
        self.assertNotIn("scac_reference_monitor_state", definition)

    def test_historical_tagged_catalog_bodies_remain_exact(self):
        for filename, version in [
            (Path(gate.SEALED_PREDECESSOR_MIGRATION).name, gate.SEALED_PREDECESSOR_ORDINAL),
            ("0890_retire_heavy_build_runtime.sql", 118),
        ]:
            with self.subTest(filename=filename):
                migration = gate.read_migration(ROOT, filename)
                marker = f"create or replace function ops.scac_mutation_catalog_v{version}_current()"
                expected = migration.split(marker, 1)[1].split("as $fn$", 1)[1].split("$fn$;", 1)[0].strip()
                definition = gate.current_function_sql(migration, version)
                self.assertEqual(gate.function_body(definition), expected)
                self.assertIn(gate.NARROWED_ROLE_AUTHORITY_SCOPE, definition)


if __name__ == "__main__":
    unittest.main()
