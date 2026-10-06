#!/usr/bin/env python3
"""Current SQL history survives a generated successor and a catalog probe."""
import json
from pathlib import Path
import re
import unittest

from successor_generation import render_sql, probe_sql

ROOT = Path(__file__).resolve().parents[1]


class Rendering(unittest.TestCase):
    def test_current_template_extends_history_and_binds_measured_catalog(self):
        template = (ROOT / 'migrations/0840_relationship_scac_successor.sql').read_text()
        old_catalog = json.loads(re.search(r"when 'scac-mutation-registry.v109' then '([^']+)'::jsonb end;", template)[1])
        version_row = re.search(r"values \('scac-mutation-registry.v109',.*?'(sha256:[0-9a-f]{64})',(\d+),(\d+),", template)
        predecessor = dict(number=109, digest=version_row[1], entry_count=int(version_row[2]), source_count=int(version_row[3]), catalog=dict(sorted(old_catalog.items())), entry_set=json.loads((ROOT / 'ops/config/scac-registry-full-entry-set-seals.json').read_text())['scac-mutation-registry.v109'])
        measured = {**old_catalog, 'projection_version': 'scac-db-catalog-projection.v110'}
        measured['secdef_execute'] = {'count': 1243, 'digest': 'sha256:' + 'f'*64}
        sql = render_sql(template, predecessor, [], measured, 'sha256:' + 'a'*64, [])
        self.assertIn("when 'scac-mutation-registry.v109' then '" + predecessor['digest'], sql)
        self.assertIn("when 'scac-mutation-registry.v110' then '" + json.dumps(measured, separators=(',', ':')) + "'::jsonb end;", sql)
        self.assertIn('observed_count<>1243', sql)
        probe = probe_sql(sql)
        self.assertNotRegex(probe, r'(?m)^do \$')
        self.assertIn('create or replace function ops.scac_mutation_catalog_v110_current()', probe)
        self.assertIn('insert into ops.scac_mutation_registry_entry', probe)

    def test_predecessor_catalog_order_matches_its_database_representation(self):
        template = (ROOT / 'migrations/0840_relationship_scac_successor.sql').read_text()
        catalog = json.loads(re.search(r"when 'scac-mutation-registry.v109' then '([^']+)'::jsonb end;", template)[1])
        row = re.search(r"values \('scac-mutation-registry.v109',.*?'(sha256:[0-9a-f]{64})',(\d+),(\d+),", template)
        predecessor = dict(number=109,digest=row[1],entry_count=int(row[2]),source_count=int(row[3]),catalog=catalog,entry_set='sha256:'+'a'*64)
        measured = {**catalog,'projection_version':'scac-db-catalog-projection.v110'}
        first = render_sql(template, predecessor, [], measured, 'sha256:'+'b'*64, [])
        reordered = {**predecessor,'catalog':dict(reversed(list(catalog.items())))}
        self.assertEqual(first, render_sql(template, reordered, [], measured, 'sha256:'+'b'*64, []))

    def test_category_counts_do_not_cascade_or_share_identity(self):
        template = (ROOT / 'migrations/0840_relationship_scac_successor.sql').read_text()
        catalog = json.loads(re.search(r"when 'scac-mutation-registry.v109' then '([^']+)'::jsonb end;", template)[1])
        row = re.search(r"values \('scac-mutation-registry.v109',.*?'(sha256:[0-9a-f]{64})',(\d+),(\d+),", template)
        predecessor = dict(number=109, digest=row[1], entry_count=int(row[2]), source_count=int(row[3]), catalog=catalog, entry_set='sha256:'+'a'*64)
        for shared in (False, True):
            with self.subTest(shared=shared):
                old = json.loads(json.dumps(catalog))
                source = template
                if shared:
                    source = source.replace('observed_count=13 and', 'observed_count=12 and').replace(json.dumps(catalog, separators=(',', ':')), json.dumps({**catalog, 'role_authority': {**catalog['role_authority'], 'count': 12}}, separators=(',', ':')))
                    old['role_authority']['count'] = 12
                predecessor['catalog'] = old
                measured = json.loads(json.dumps(old))
                measured['column_dml']['count'] = 13
                measured['role_authority']['count'] = 14
                measured['projection_version'] = 'scac-db-catalog-projection.v110'
                sql = render_sql(source, predecessor, [], measured, 'sha256:'+'b'*64, [])
                self.assertTrue("observed_count<>13 or observed_digest<>'" + old['column_dml']['digest'] + "'" in sql)
                self.assertIn('observed_count=14 and', sql)

    def test_predecessor_rows_use_validated_frozen_inventory(self):
        import successor_generation
        rows = successor_generation.predecessor_rows(ROOT, 'scac-mutation-registry.v110')
        self.assertTrue(rows)
        self.assertEqual(rows[0]['ingress_key'], sorted(row['ingress_key'] for row in rows)[0])


if __name__ == '__main__':
    unittest.main()
