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
BASE: dict[str, list[dict[str, object]]] = {
    name: [] for name in ('secdef_execute', 'relation_dml', 'column_dml')
}
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

    def test_exact_retired_grant_is_removed_from_the_live_catalog(self):
        retired = {**GRANT, 'ingress_key': 'retired:execute', 'signature': 'ops.retired_writer()'}
        baseline = {**BASELINE, 'secdef_execute': {'count': 1, 'digest': digest([retired])}}
        contract = {**CONTRACT, 'retired_secdef_execute': [retired]}
        validate_registered_grants(self.projection(), baseline, contract)
        with self.assertRaisesRegex(RuntimeError, 'retired grant'):
            validate_registered_grants({**BASE, 'secdef_execute': [GRANT, retired]}, baseline, contract)
        with self.assertRaisesRegex(RuntimeError, 'catalog drift'):
            validate_registered_grants(self.projection(), baseline,
                                       {**contract, 'retired_secdef_execute': [{**retired, 'grantable': True}]})

    def test_registered_replacement_retires_the_old_definition(self):
        old = {**GRANT, 'config': ['search_path=ops, public, pg_temp']}
        new = {**GRANT, 'config': ['search_path=pg_catalog, ops, pg_temp']}
        baseline = {**BASELINE, 'secdef_execute': {'count': 1, 'digest': digest([old])}}
        contract = {**CONTRACT, 'secdef_execute': [new], 'retired_secdef_execute': [old]}
        validate_registered_grants({**BASE, 'secdef_execute': [new]}, baseline, contract)
        with self.assertRaisesRegex(RuntimeError, 'registered grant'):
            validate_registered_grants({**BASE, 'secdef_execute': [old]}, baseline, contract)
        with self.assertRaisesRegex(RuntimeError, 'retired grant'):
            validate_registered_grants({**BASE, 'secdef_execute': [new, old]}, baseline, contract)

    def test_retired_grants_preserve_catalog_order(self):
        first = {**GRANT, 'ingress_key': 'a'}
        live = {**GRANT, 'ingress_key': 'b'}
        last = {**GRANT, 'ingress_key': 'z'}
        baseline = {**BASELINE, 'secdef_execute': {'count': 3, 'digest': digest([first, live, last])}}
        contract = {**CONTRACT, 'retired_secdef_execute': [first, last]}
        validate_registered_grants({**BASE, 'secdef_execute': [GRANT, live]}, baseline, contract)

    def test_unrelated_relation_grant_refuses(self):
        projection = self.projection()
        projection['relation_dml'] = [{'ingress_key': 'rogue:insert'}]
        with self.assertRaisesRegex(RuntimeError, 'catalog drift'):
            validate_registered_grants(projection, BASELINE, CONTRACT)


if __name__ == '__main__':
    unittest.main()
