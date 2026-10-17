#!/usr/bin/env python3
"""Verify both catalog readers against a full disposable post-0884 schema."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'ops'))
spec = importlib.util.spec_from_file_location('migration_recovery_tests', ROOT / 'tools/test-migration-0882.py')
assert spec is not None and spec.loader is not None
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
OLD_MAP = recovery.OLD_MAP
POSTGRES = "--postgres" in sys.argv
if POSTGRES:
    sys.argv.remove("--postgres")


class RetainedCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if POSTGRES:
            recovery.Recovery.setUpClass.__func__(cls)

    setUp = recovery.Recovery.setUp
    seed_pack_approval = recovery.Recovery.seed_pack_approval
    run_runner = recovery.Recovery.run_runner
    pack_snapshot = recovery.Recovery.pack_snapshot

    @unittest.skipUnless(POSTGRES, 'full snapshot refresh runs in the PostgreSQL 18 migration lane')
    def test_postapply_snapshot_refresh_and_other_drift_refusal(self):
        import sync_control_catalog
        from retained_pack_delivery import RETAINED_PACK_DELIVERY
        with psycopg.connect(self.base_dsn, autocommit=True) as owner:
            owner.execute("create database carr_ci")
            owner.execute("create role neondb_owner")
        full_dsn = f'postgres://carr_ci@127.0.0.1:{self.port}/carr_ci'
        restored = subprocess.run([str(self.bindir / 'psql'), full_dsn,
                                   '-v', 'ON_ERROR_STOP=1', '-q', '-f', str(ROOT / 'db/schema.sql')],
                                  capture_output=True, text=True)
        self.assertEqual(restored.returncode, 0, restored.stderr[-4000:])
        self.seed_pack_approval()
        applied = self.run_runner()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn('retained immutable pack_delivery', applied.stdout)
        records = self.pack_snapshot()[1:]
        self.conn.commit()
        with psycopg.connect(full_dsn) as full:
            # Seed only synthetic historical authority facts in this owned DB.
            # Runtime protections are restored before running either reader.
            full.execute("set local session_replication_role=replica")
            for table, rows in zip(('ops.rule_control_binding', 'ops.rule_approval_receipt', 'public.rule'), records):
                for (record,) in rows:
                    full.execute(sql.SQL("insert into {} select * from jsonb_populate_record(null::{},%s)").format(
                        sql.Identifier(*table.split('.')), sql.Identifier(*table.split('.'))),
                        (psycopg.types.json.Jsonb(record),))
            for row in sync_control_catalog.compile_catalog():
                values = tuple(row[k] for k in ('control_key','implementation_ref','test_ref','enforcement_class','installed'))
                if row['control_key']=='pack_delivery':
                    values = RETAINED_PACK_DELIVERY
                full.execute("insert into ops.enforcement_control_catalog "
                             "(control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at) "
                             "values(%s,%s,%s,%s,%s,now()) on conflict(control_key) do update set "
                             "implementation_ref=excluded.implementation_ref,test_ref=excluded.test_ref,"
                             "enforcement_class=excluded.enforcement_class,installed=excluded.installed,verified_at=excluded.verified_at", values)
            full.execute("insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest) "
                         "values('1fcaa63a-73d7-498d-9f76-e99bcd821a89','1fcaa63a','layer0','{}','shared',"
                         "'synthetic historical fixture','fixture',%s)", (OLD_MAP,))
        parity = [sys.executable, str(ROOT / 'ops/control-catalog-parity-gate.py')]
        snapshot = [str(ROOT / 'bin/schema-snapshot.sh'), '--from-disposable-local', full_dsn]
        candidate = self.pg.root / 'postapply.sql'
        refreshed = subprocess.run(snapshot + ['--output-candidate', str(candidate)], capture_output=True, text=True)
        self.assertEqual(refreshed.returncode, 0, refreshed.stdout[-2000:] + refreshed.stderr[-4000:])
        self.assertIn('accepted historical pack_delivery', refreshed.stderr)
        generated = candidate.read_text()
        self.assertIn(RETAINED_PACK_DELIVERY[1], generated)
        self.assertIn(RETAINED_PACK_DELIVERY[2], generated)
        passed = subprocess.run(parity, env={**os.environ, 'DATABASE_URL':full_dsn}, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        self.assertIn('accepted historical pack_delivery', passed.stdout)
        for change in (
            "update ops.enforcement_control_catalog set test_ref='different drift' where control_key='session_boot'",
            "update public.rule set version=3 where id='1fcaa63a-73d7-498d-9f76-e99bcd821a89'",
            "update ops.enforcement_control_catalog set test_ref='different drift' where control_key='pack_delivery'",
        ):
            with self.subTest(change=change), psycopg.connect(full_dsn) as full:
                full.execute("set local session_replication_role=replica")
                # Make the deliberately invalid source observable by the CLI readers.
                full.execute(change)
                full.commit()
                refused = subprocess.run(parity, env={**os.environ, 'DATABASE_URL':full_dsn}, capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0, refused.stdout)
                refused = subprocess.run(snapshot + ['--verify-only'], capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0, refused.stdout)
                self.assertIn('exact reviewed control catalog is missing or drifted', refused.stderr)
                full.execute("set local session_replication_role=replica")
                if 'public.rule' in change:
                    full.execute("update public.rule set version=2 where id='1fcaa63a-73d7-498d-9f76-e99bcd821a89'")
                else:
                    key = 'pack_delivery' if "='pack_delivery'" in change else 'session_boot'
                    row = next(r for r in sync_control_catalog.compile_catalog() if r['control_key']==key)
                    full.execute("update ops.enforcement_control_catalog set test_ref=%s where control_key=%s",
                                 (RETAINED_PACK_DELIVERY[2] if key=='pack_delivery' else row['test_ref'], key))


if __name__ == '__main__':
    unittest.main()
