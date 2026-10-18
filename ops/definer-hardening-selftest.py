#!/usr/bin/env python3
"""Source-only regressions for the SECURITY DEFINER hardening review round.

Paired with ops/definer-hardening-local-pg-gate.py. Needs no database:
  * the qualification audit flags bare application references and ignores
    comments, literals, qualified names, CTEs and pg_catalog built-ins, and
    the path resolver binds a name the way the routine's own path would;
  * the temporary-object substitution fixture's legacy routine is the exact
    0617 body and path, so its positive control attacks the real pre-0764
    shape and not an invented one.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration


import importlib.util
import re
import unittest
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "ops"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    assert spec is not None and spec.loader is not None, relative
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = load("definer_hardening_local_pg_gate", "ops/definer-hardening-local-pg-gate.py")


class FakeCatalog(GATE.Catalog):  # type: ignore[name-defined,misc]
    def __init__(self):  # noqa: D401 - no database
        self.relations = {"actor": {"public"}, "memory_item": {"public"},
                          "rule": {"public", "ops"}, "pg_class": {"pg_catalog"}}
        self.routines = {"digest": {"public"}, "normalize_retrieval_phrase": {"public"},
                         "now": {"pg_catalog"}, "coalesce": {"pg_catalog"}}


class QualificationAudit(unittest.TestCase):
    def setUp(self):
        self.catalog = FakeCatalog()

    def found(self, body, resolve=None):
        resolve = resolve or self.catalog.any_application
        return [value for _s, _e, value in GATE.dependency_edits(body, resolve)]

    def test_flags_bare_relations_rowtypes_inserts_and_helpers(self):
        body = ("declare prior memory_item%rowtype; begin "
                "insert into actor(id) values (digest('x','sha256')); "
                "update actor set slug='y'; select 1 from actor a join memory_item m on true; end")
        self.assertEqual(self.found(body), [
            "public.memory_item", "public.actor", "public.digest",
            "public.actor", "public.actor", "public.memory_item",
        ])

    def test_ignores_comments_literals_qualified_names_and_variables(self):
        body = ("-- from actor\n/* join actor */ select id into actor from public.actor actor "
                "where note = 'from actor' and \"actor\" is not null; perform public.digest('x')")
        self.assertEqual(self.found(body), [])

    def test_ignores_cte_names_and_catalog_builtins(self):
        body = ("with recursive actor(id) as (select 1), rule as materialized (select 2) "
                "select coalesce(now(), now()) from actor join rule on true join pg_class on true")
        self.assertEqual(self.found(body), [])

    def test_path_resolver_follows_the_routine_path(self):
        ops_first = self.catalog.path_resolver(["ops", "public", "pg_temp"])
        public_first = self.catalog.path_resolver(["public", "ops", "pg_temp"])
        self.assertEqual(self.found("select 1 from rule", ops_first), ["ops.rule"])
        self.assertEqual(self.found("select 1 from rule", public_first), ["public.rule"])
        self.assertEqual(self.found("select now() from pg_class", ops_first), [])

    def test_apply_edits_is_idempotent(self):
        body = "select id from actor where digest(slug) is not null"
        once = GATE.apply_edits(body, GATE.dependency_edits(body, self.catalog.any_application))
        self.assertEqual(once, "select id from public.actor where public.digest(slug) is not null")
        self.assertEqual(GATE.dependency_edits(once, self.catalog.any_application), [])


class SubstitutionFixtureParity(unittest.TestCase):
    def test_legacy_clone_is_the_exact_0617_routine(self):
        migration = read_migration(REPO, "0617_delivery_cadence_a05.sql")
        fixture = (REPO / "mcp-server/test/definer-temp-substitution-postgres.sql").read_text(encoding="utf-8")
        original = re.search(
            r"create or replace function ops\.v5_a05_assurance_cadence_batch\(p_recipient_slug text\)\n"
            r"(.*?)\nas \$\$\n(.*?)\n\$\$;", migration, re.S)
        legacy = re.search(
            r"create function pg_temp\.legacy_v5_a05_assurance_cadence_batch\(p_recipient_slug text\)\n"
            r"(.*?)\nas \$legacy\$\n(.*?)\n\$legacy\$;", fixture, re.S)
        self.assertIsNotNone(original)
        self.assertIsNotNone(legacy)
        self.assertEqual(original.group(2), legacy.group(2))
        self.assertIn("set search_path = pg_catalog, ops, public", original.group(1))
        self.assertIn("set search_path = pg_catalog, ops, public", legacy.group(1))
        self.assertNotIn("pg_temp", legacy.group(1))


class LockRehearsalFrontier(unittest.TestCase):
    def test_accepts_the_declared_predecessor_before_rehearsing(self):
        predecessor = {'migration': 'migrations/0853_party_identity_scac_successor.sql'}
        rehearsal = load('hardening_rehearsal', 'ops/definer-hardening-lock-rehearsal-gate.py')
        connection = MagicMock()
        connection.__enter__.return_value = connection

        def execute(query):
            if query == 'select filename from public.schema_migrations':
                return [(Path(predecessor['migration']).name,)]
            raise RuntimeError('accepted frontier; start scale rehearsal')

        connection.execute.side_effect = execute
        with patch.dict('os.environ', {'CARR_LOCAL_PG_DSN': 'host=127.0.0.1 dbname=fixture'}), \
             patch.object(rehearsal.psycopg, 'connect', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'accepted frontier'):
                rehearsal.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
