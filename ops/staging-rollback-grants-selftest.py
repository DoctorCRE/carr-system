#!/usr/bin/env python3
"""Rollback authority follows verified installed SQL, independent of newer snapshots."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import schema_snapshot_grants as grants


class InstalledGrants(unittest.TestCase):
    def test_old_plan_excludes_unapplied_grants_and_keeps_old_privileges(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "migrations"
            directory.mkdir()
            old = "grant usage on schema public to carr_authority;"
            (directory / "0001_authority.sql").write_text(old)
            (directory / "0002_future.sql").write_text(
                "revoke usage on schema public from carr_authority;\n"
                "grant execute on function public.future() to carr_authority;")
            plan = grants.load_applied_grants_to_role(
                directory, {"0001_authority.sql": hashlib.sha256(old.encode()).hexdigest()}, "carr_authority")
            self.assertEqual(set(grants.acl_facts(plan)), {("schema", "public", "usage", False)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
