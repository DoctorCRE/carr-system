import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import client_portal_migration as migration


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.output = self.root / 'package'
        self.study = json.loads((Path(__file__).resolve().parents[1] /
            'client-portal/fixtures/example-health.market-study.json').read_text())
        for prop in self.study['properties']:
            prop.pop('ownership', None)
            prop['facts'] = [{'value': '1000 SF', 'label': 'Size'}]
        self.study.pop('ownership', None)
        self.mapping = {p['id']: '12345678-1234-4234-8234-%012d' % i
                        for i, p in enumerate(self.study['properties'])}
        self.descriptor = {'study': self.study, 'pages': ['home', 'tour'],
            'files': [{'path': 'asset.pdf', 'classification': 'content'},
                      {'path': 'financial.pdf', 'classification': 'financial'}],
            'selections': [{'version': 7, 'selected_ids': [self.study['properties'][0]['id']],
                            'retired_ids': ['retired'], 'notes_by_property': {}}]}
        (self.source / 'asset.pdf').write_bytes(b'synthetic asset')
        (self.source / 'financial.pdf').write_bytes(b'synthetic excluded content')
        def assets(node):
            if isinstance(node, dict):
                if set(node) == {'sha256', 'name', 'type'}:
                    node['sha256'] = migration.digest(b'synthetic asset')
                for value in node.values():
                    assets(value)
            elif isinstance(node, list):
                for value in node:
                    assets(value)
        assets(self.study)
        self.save()

    def save(self):
        (self.source / 'migration.json').write_text(json.dumps(self.descriptor))

    def test_conversion_parity_and_idempotent_rerun(self):
        migration.convert(self.source, self.mapping, self.output)
        before = {p.relative_to(self.output): p.read_bytes()
                  for p in self.output.rglob('*') if p.is_file()}
        migration.convert(self.source, self.mapping, self.output)
        self.assertEqual(before, {p.relative_to(self.output): p.read_bytes()
                                 for p in self.output.rglob('*') if p.is_file()})
        self.assertTrue(migration.verify(self.output))
        parity = subprocess.run(['node', 'mcp-server/portal-parity/cli.mjs',
            '--site', 'REF-V', '--stage', '1', '--rows', 'V-S14,V-S28',
            '--package', str(self.output / 'market-study.json'),
            '--probes', str(self.output / 'portal-parity-probes.json')],
            cwd=migration.ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, parity.returncode, parity.stdout + parity.stderr)
        self.assertEqual(self.descriptor['selections'],
                         json.loads((self.output / 'selections.json').read_text()))
        exclusions = json.loads((self.output / 'exclusions.json').read_text())
        self.assertEqual(['financial.pdf'], [row['path'] for row in exclusions])
        self.assertNotIn(b'synthetic excluded content', before.values())

    def test_unresolved_mapping_refuses_without_output(self):
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            migration.convert(self.source, {}, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_export_and_unclassified_files_refuse(self):
        del self.descriptor['selections']
        self.save()
        with self.assertRaisesRegex(ValueError, 'selection'):
            migration.convert(self.source, self.mapping, self.output)
        self.descriptor['selections'] = []
        self.save()
        (self.source / 'unknown.txt').write_text('unknown')
        with self.assertRaisesRegex(ValueError, 'classif'):
            migration.convert(self.source, self.mapping, self.output)

    def test_invalid_uuid_schema_and_financial_fields_refuse(self):
        mapping = dict(self.mapping)
        mapping[next(iter(mapping))] = 'invented'
        with self.assertRaises(ValueError):
            migration.convert(self.source, mapping, self.output)
        self.study['properties'][0]['ownership'] = {'asking_price': 123}
        self.save()
        with self.assertRaisesRegex(ValueError, 'financial'):
            migration.convert(self.source, self.mapping, self.output)
        del self.study['properties'][0]['ownership']
        del self.study['market']
        self.save()
        with self.assertRaisesRegex(ValueError, 'schema'):
            migration.convert(self.source, self.mapping, self.output)

    def test_drift_tampering_and_failed_stage(self):
        with patch.object(migration.os, 'rename', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                migration.convert(self.source, self.mapping, self.output)
        self.assertFalse(self.output.exists())
        migration.convert(self.source, self.mapping, self.output)
        (self.output / 'selections.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'parity'):
            migration.verify(self.output)
        with self.assertRaisesRegex(ValueError, 'drift'):
            migration.convert(self.source, self.mapping, self.output)

    def test_path_escape_symlink_and_limit_refuse(self):
        self.descriptor['files'][0]['path'] = '../outside'
        self.save()
        with self.assertRaises(ValueError):
            migration.convert(self.source, self.mapping, self.output)
        (self.source / 'link').symlink_to(self.root)
        with self.assertRaises(ValueError):
            migration.inventory(self.source)
        (self.source / 'link').unlink()
        with patch.object(migration, 'MAX_FILES', 1):
            with self.assertRaisesRegex(ValueError, 'limit'):
                migration.inventory(self.source)

    def test_inventory_reads_json_without_executing_script(self):
        (self.source / 'index.html').write_text('<div class="site-view" id="home"></div>')
        (self.source / 'data.js').write_text('window.SOURCE_DATA = ' + json.dumps({
            'properties': [{'id': 'example', 'address': 'Example address'}]}) + ';')
        result = migration.inventory(self.source)
        self.assertEqual(['home'], result['pages'])
        self.assertEqual('example', result['properties'][0]['id'])
        self.assertIsNone(result['selections'])
        (self.source / 'data.js').write_text('window.SOURCE_DATA = {}; process.exit(0);')
        with self.assertRaisesRegex(ValueError, 'script execution'):
            migration.inventory(self.source)

    def test_empty_export_excluded_asset_and_extra_output(self):
        self.descriptor['selections'] = []
        self.save()
        migration.convert(self.source, self.mapping, self.output)
        self.assertEqual([], json.loads((self.output / 'selections.json').read_text()))
        (self.output / 'unexpected').write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'parity'):
            migration.verify(self.output)
        self.descriptor['files'][0]['classification'] = 'financial'
        self.save()
        with self.assertRaisesRegex(ValueError, 'asset missing'):
            migration.convert(self.source, self.mapping, self.root / 'other')

    def test_omitted_source_page_refuses(self):
        (self.source / 'index.html').write_text('<div class="site-view" id="sources"></div>')
        self.descriptor['files'].append({'path': 'index.html', 'classification': 'content'})
        self.save()
        with self.assertRaisesRegex(ValueError, 'page omitted'):
            migration.convert(self.source, self.mapping, self.output)

    def test_api_credential_is_never_copied(self):
        self.descriptor['selections'][0]['csrf_token'] = 'synthetic-token'
        self.save()
        with self.assertRaisesRegex(ValueError, 'credentials'):
            migration.convert(self.source, self.mapping, self.output)
        self.assertFalse(self.output.exists())

    def test_omitted_source_property_refuses(self):
        (self.source / 'data.js').write_text('window.EXAMPLE = ' + json.dumps({
            'properties': [{'id': 'omitted', 'address': 'Synthetic address'}]}) + ';')
        self.descriptor['files'].append({'path': 'data.js', 'classification': 'content'})
        self.save()
        with self.assertRaisesRegex(ValueError, 'property omitted'):
            migration.convert(self.source, self.mapping, self.output)

    def test_financial_duplicate_refuses(self):
        (self.source / 'financial.pdf').write_bytes((self.source / 'asset.pdf').read_bytes())
        with self.assertRaisesRegex(ValueError, 'financial content'):
            migration.convert(self.source, self.mapping, self.output)


if __name__ == '__main__':
    unittest.main()
