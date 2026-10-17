#!/usr/bin/env python3
"""Only discharge the obsolete pack refresh when no active control remains."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from migrate import DATA_DEPENDENT_MIGRATIONS

NAME = '0884_refresh_pack_delivery_catalog_after_shadow_retirement.sql'


class PackRefresh(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute("attach database ':memory:' as ops")
        self.db.execute("attach database ':memory:' as public")
        self.db.execute('create table ops.enforcement_control_catalog(control_key text, installed boolean)')
        self.db.execute('create table ops.rule_control_binding(control_key text, rule_id text)')
        self.db.execute('create table public.rule(id text, status text)')

    def required(self):
        return self.db.execute(DATA_DEPENDENT_MIGRATIONS[NAME][0]).fetchone() is not None

    def test_missing_control_needs_no_refresh(self):
        self.assertFalse(self.required())

    def test_installed_control_requires_original_migration(self):
        self.db.execute("insert into ops.enforcement_control_catalog values('pack_delivery',true)")
        self.assertTrue(self.required())

    def test_retired_binding_does_not_reactivate_control(self):
        self.db.execute("insert into ops.rule_control_binding values('pack_delivery','old-rule')")
        self.db.execute("insert into public.rule values('old-rule','retired')")
        self.assertFalse(self.required())

    def test_active_binding_keeps_original_guard(self):
        self.db.execute("insert into ops.rule_control_binding values('pack_delivery','active-rule')")
        self.db.execute("insert into public.rule values('active-rule','active')")
        self.assertTrue(self.required())

    def test_unrelated_installed_control_is_unchanged(self):
        self.db.execute("insert into ops.enforcement_control_catalog values('other',true)")
        self.assertFalse(self.required())


if __name__ == '__main__':
    unittest.main()
