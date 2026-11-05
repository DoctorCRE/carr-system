"""Synthetic migration acceptance through the validator and local parity adapter."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rehearsal', ROOT / 'tools/portal-migration-rehearsal.py')
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / 'client-portal/fixtures/migration-rehearsal.json').read_text())

    def rejected(self, change, code):
        change(self.manifest)
        with self.assertRaisesRegex(ValueError, code):
            module.prepare(self.manifest)

    def test_four_portals_and_preserved_delta(self):
        prepared = module.prepare(self.manifest)
        self.assertEqual(len(prepared), 4)
        self.assertEqual([p['kind'] for p in prepared], ['package', 'package', 'legacy', 'legacy'])
        self.assertEqual(prepared[1]['imported'], {'version': 8, 'selections': ['active-1', 'retired-1'],
                         'notes_by_property': {'retired-1': 'Synthetic retired note'}, 'notes': 'Synthetic legacy note'})
        self.assertEqual(self.manifest['portals'][1]['copy']['version'], 7)

    def test_unchanged_snapshot_needs_no_delta(self):
        portal = self.manifest['portals'][1]
        portal['freeze'] = copy.deepcopy(portal['copy'])
        del portal['delta']
        self.assertEqual(module.prepare(self.manifest)[1]['imported']['version'], 7)

    def test_duplicate_slug(self):
        self.rejected(lambda m: m['portals'][1].update(slug=m['portals'][0]['slug']), 'duplicate-slug')

    def test_duplicate_deal(self):
        self.rejected(lambda m: m['portals'][1].update(deal=m['portals'][0]['deal']), 'duplicate-deal')

    def test_wrong_bundle_hash(self):
        self.rejected(lambda m: m['portals'][2].update(bundle_sha256='0' * 64), 'bundle-hash')

    def test_wrong_file_hash(self):
        self.rejected(lambda m: m['portals'][0]['files'][0].update(content='changed'), 'file-hash')

    def test_missing_alias_target(self):
        self.rejected(lambda m: m['portals'][3]['aliases'][0].update(target='absent.pdf'), 'alias-target')

    def test_cross_portal_alias(self):
        self.rejected(lambda m: m['portals'][3]['aliases'][0].update(portal='synthetic-ref-a'), 'cross-portal-alias')

    def test_mixed_publication(self):
        self.rejected(lambda m: m['portals'][0]['files'][0].update(revision=2), 'mixed-publication')

    def test_publication_package_and_alias_revisions(self):
        for portal, key in [(0, 'package'), (3, 'aliases')]:
            with self.subTest(key=key):
                manifest = copy.deepcopy(self.manifest)
                target = manifest['portals'][portal][key]
                if key == 'aliases':
                    target = target[0]
                target['revision'] = 2
                with self.assertRaisesRegex(ValueError, 'mixed-publication'):
                    module.prepare(manifest)

    def test_retired_entries_cannot_disappear_from_delta(self):
        self.rejected(lambda m: m['portals'][1]['delta']['selection'].update(selections=['active-1']), 'delta-mismatch')

    def test_package_hash(self):
        self.rejected(lambda m: m['portals'][0]['package'].update(sha256='0' * 64), 'package-hash')

    def test_missing_delta(self):
        self.rejected(lambda m: m['portals'][1].pop('delta'), 'delta-required')

    def test_wrong_delta_version(self):
        self.rejected(lambda m: m['portals'][1]['delta'].update(from_version=6), 'delta-mismatch')

    def test_lost_retired_note(self):
        self.rejected(lambda m: m['portals'][1]['delta']['selection'].update(notes_by_property={}), 'delta-mismatch')

    def test_same_version_changed_data(self):
        self.rejected(lambda m: m['portals'][0]['freeze'].update(notes='changed'), 'version-not-increased')

    def test_backward_version(self):
        self.rejected(lambda m: m['portals'][1]['freeze'].update(version=6), 'version-not-increased')

    def test_bounds(self):
        self.rejected(lambda m: m['portals'].append(copy.deepcopy(m['portals'][0])), 'four-portals')
        self.manifest['portals'].pop()
        self.rejected(lambda m: m['portals'][0].update(files=m['portals'][0]['files'] * 33), 'file-limit')

    def test_paths_and_synthetic_boundary(self):
        self.rejected(lambda m: m['portals'][0].update(slug='real-client'), 'synthetic-slug')
        self.manifest['portals'][0]['slug'] = 'synthetic-ref-a'
        self.rejected(lambda m: m['portals'][0]['files'][0].update(path='../escape'), 'unsafe-path')

    def test_real_local_parity_and_pending_integration(self):
        receipt = module.rehearse(self.manifest)
        self.assertEqual(receipt['status'], 'pending-integration')
        self.assertFalse(receipt['cutover_ready'])
        self.assertEqual(receipt['local']['status'], 'PASS')
        self.assertEqual(len(receipt['local']['portals']), 4)
        self.assertTrue(all(p['stale_save_status'] == 409 for p in receipt['local']['portals']))
        self.assertEqual(receipt['production_proof'], 'pending')
        self.assertEqual(receipt['integration'], {'S9': 'pending', 'S10-S17': 'pending'})

    def test_adapter_detects_lost_imported_notes(self):
        portals = module.prepare(self.manifest)
        portals[1]['imported']['notes_by_property'] = {}
        run = module.subprocess.run(['node', str(ROOT / 'mcp-server/portal-parity/migration-fixtures.mjs')],
                                    input=json.dumps(portals), text=True, capture_output=True, timeout=60)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('retain versions, retired IDs, and all notes', run.stderr)

    def test_invalid_package_fails_rehearsal(self):
        with mock.patch.object(module, 'prepare', wraps=module.prepare) as prepare:
            portals = module.prepare(self.manifest)
            portals[0]['package']['data'] = {}
            prepare.side_effect = None
            prepare.return_value = portals
            self.assertEqual(module.rehearse(self.manifest)['status'], 'failed')

    def test_manifest_size(self):
        self.rejected(lambda m: m.update(oversized='x' * module.MAX_BYTES), 'manifest-size')

    def test_adapter_failure_is_not_a_pass(self):
        for error in [module.subprocess.TimeoutExpired('node', 60), FileNotFoundError('node')]:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(module.subprocess, 'run', side_effect=error):
                    receipt = module.rehearse(self.manifest)
                self.assertEqual(receipt['status'], 'failed')
                self.assertFalse(receipt['cutover_ready'])


if __name__ == '__main__':
    unittest.main()
