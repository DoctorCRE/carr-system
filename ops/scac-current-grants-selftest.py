#!/usr/bin/env python3
"""Current registry grant validation stays exact and bound to the DB registry."""
import copy
import unittest

from scac_mutation_db_inventory import digest, validate_registered_grants

GRANT = {
    'ingress_key': 'db-function-acl:ops.scope_writer():carr_authority:execute',
    'ingress_kind': 'db_function_acl', 'signature': 'ops.scope_writer()',
    'grantee': 'carr_authority', 'privilege': 'execute', 'grantable': False,
}
BASE = {name: [] for name in ('secdef_execute', 'relation_dml', 'column_dml')}
BASELINE = {name: {'count': 0, 'digest': digest([])} for name in BASE}
CONTRACT = {'registry_version': 'fixture.v1', 'registry_digest': 'sha256:' + 'a' * 64,
            'secdef_execute': [GRANT]}


class CurrentGrants(unittest.TestCase):
    def projection(self):
        return {**copy.deepcopy(BASE), 'secdef_execute': [copy.deepcopy(GRANT)]}

    def test_registered_grant_passes(self):
        validate_registered_grants(self.projection(), BASELINE, CONTRACT)

    def test_unregistered_grant_refuses(self):
        projection = self.projection()
        projection['secdef_execute'].append({**GRANT, 'grantee': 'carr_writer',
                                             'ingress_key': 'unregistered'})
        with self.assertRaisesRegex(RuntimeError, 'catalog drift'):
            validate_registered_grants(projection, BASELINE, CONTRACT)

    def test_missing_or_altered_registered_grant_refuses(self):
        for rows in ([], [{**GRANT, 'grantable': True}]):
            with self.subTest(rows=rows), self.assertRaisesRegex(RuntimeError, 'registered grant'):
                validate_registered_grants({**BASE, 'secdef_execute': rows}, BASELINE, CONTRACT)

    def test_unrelated_relation_grant_refuses(self):
        projection = self.projection()
        projection['relation_dml'] = [{'ingress_key': 'rogue:insert'}]
        with self.assertRaisesRegex(RuntimeError, 'catalog drift'):
            validate_registered_grants(projection, BASELINE, CONTRACT)


if __name__ == '__main__':
    unittest.main()
