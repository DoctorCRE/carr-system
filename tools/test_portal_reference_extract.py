"""Synthetic CLI acceptance tests. No client sources or network access."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'tools/portal-reference-extract.py'
FIXTURES = ROOT / 'client-portal/fixtures/reference-extractor'


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        shutil.copytree(FIXTURES / 'ref-a', self.source)
        self.output = self.base / 'output'

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(CLI), '--source', str(self.source),
                                 '--output', str(self.output), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if ok else 2, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def change_mapping(self, change):
        path = self.source / 'extract.json'
        mapping = json.loads(path.read_text())
        change(mapping)
        path.write_text(json.dumps(mapping))

    def test_full_assets_inventory_and_deterministic_repeat(self):
        first = self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertIn('ownership', package)
        report = json.loads((self.output / 'manifest.json').read_text())
        self.assertGreater(report['counts']['mapped_rows'], 100)
        self.assertTrue(report['assets'])
        self.assertTrue(next(r for r in report['rows'] if r['id'] == 'A-S03')['sources'])
        for asset in report['assets']:
            content = (self.output / asset['output']).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), asset['sha256'])
        before = {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        self.output = self.base / 'again'
        self.assertEqual(first, self.run_cli())
        self.assertEqual(before, {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)

    def test_numeric_leaf_mappings_build_center_array(self):
        def change(mapping):
            center = mapping['fields'].pop('/map/center')
            for index in range(2):
                mapping['fields'][f'/map/center/{index}'] = {
                    **center, 'pointer': center['pointer'] + f'/{index}'}
        self.change_mapping(change)
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertEqual(package['map']['center'], [-100.5, 40.25])

    def test_nested_property_leaves_build_arrays(self):
        data = json.loads((self.source / 'data.js').read_text().removeprefix(
            'window.referenceData = ').removesuffix(';\n'))
        def change(mapping):
            original = mapping['fields'].pop('/properties')
            for index in reversed(range(len(data['properties']))):
                for key in data['properties'][index]:
                    mapping['fields'][f'/properties/{index}/{key}'] = {
                        **original, 'pointer': f'/properties/{index}/{key}'}
        self.change_mapping(change)
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertEqual(len(package['properties']), len(data['properties']))
        self.assertEqual(package['properties'][0]['address'], data['properties'][0]['address'])

    def test_sparse_and_ambiguous_array_indices_are_refused(self):
        for index, message in [('1', 'sparse'), ('00', 'ambiguous')]:
            def change(mapping):
                center = mapping['fields'].pop('/map/center', None)
                if center is None:
                    center = mapping['fields'].pop('/map/center/1')
                mapping['fields'][f'/map/center/{index}'] = {**center, 'pointer': '/0'}
            self.change_mapping(change)
            result = self.run_cli(ok=False)
            self.assertIn(message, str(result))
            self.assertIn(f'/map/center/{index}', str(result))
            self.assertFalse(self.output.exists())

    def test_optional_ownership_absent(self):
        shutil.rmtree(self.source)
        shutil.copytree(FIXTURES / 'ref-v', self.source)
        self.run_cli()
        self.assertNotIn('ownership', json.loads((self.output / 'market-study.json').read_text()))

    def test_partial_waives_only_record_layer_groups(self):
        self.change_mapping(lambda m: m['fields'].update({k: None for k in ('/client', '/prepared', '/properties')}))
        self.run_cli('--mode', 'partial')
        self.output = self.base / 'full'
        failure = self.run_cli(ok=False)
        self.assertIn('/client', str(failure))
        self.assertFalse(self.output.exists())
        self.change_mapping(lambda m: m['fields'].update({'/market': None}))
        self.assertIn('/market', str(self.run_cli('--mode', 'partial', ok=False)))

    def test_missing_source_field_has_path_and_count(self):
        self.change_mapping(lambda m: m['fields']['/market'].update({'pointer': '/absent'}))
        result = self.run_cli(ok=False)
        self.assertGreater(result['error_count'], 0)
        self.assertIn('/absent', str(result))
        self.assertFalse(self.output.exists())

    def test_unsafe_rich_text_uses_package_validator(self):
        path = self.source / 'data.js'
        path.write_text(path.read_text().replace('Synthetic methodology.', '<script>bad()</script>'))
        self.assertIn('/demographics/methodology', str(self.run_cli(ok=False)))

    def test_traversal_and_symlink_escape(self):
        self.change_mapping(lambda m: m['assets'][0].update({'file': '../outside'}))
        self.assertIn('path', str(self.run_cli(ok=False)))
        self.change_mapping(lambda m: m['assets'][0].update({'file': 'escape'}))
        (self.base / 'outside').write_text('private')
        (self.source / 'escape').symlink_to(self.base / 'outside')
        self.assertIn('path', str(self.run_cli(ok=False)))

    def test_ambiguous_and_unmapped_constants(self):
        path = self.source / 'app.js'
        original = path.read_text()
        path.write_text(original + '\nfunction second() { const MAP_ZOOM = 4; }')
        self.assertIn('ambiguous', str(self.run_cli(ok=False)))
        path.write_text(original + '\nconst UNKNOWN_DEAL_VALUE = 99;')
        self.assertIn('UNKNOWN_DEAL_VALUE', str(self.run_cli(ok=False)))

    def test_no_execution_of_javascript(self):
        path = self.source / 'app.js'
        path.write_text(path.read_text().replace('const MAP_ZOOM = 11;', 'const MAP_ZOOM = (() => { throw Error("executed"); })();'))
        result = self.run_cli(ok=False)
        self.assertIn('nonliteral', str(result))
        self.assertNotIn('executed', json.dumps(result))

    def test_missing_inventory_field_fails(self):
        self.change_mapping(lambda m: m['fields'].update({'/ownership': None}))
        self.assertIn('/ownership', str(self.run_cli(ok=False)))


    def test_json_source_and_missing_asset_mapping(self):
        data = (self.source / 'data.js').read_text()
        (self.source / 'data.json').write_text(data.removeprefix('window.referenceData = ').removesuffix(';\n'))
        self.change_mapping(lambda m: m['sources'].update({'data': {'file': 'data.json'}}))
        self.run_cli()
        self.output = self.base / 'without-asset'
        self.change_mapping(lambda m: m['assets'].pop())
        self.assertIn('asset has no source mapping', str(self.run_cli(ok=False)))

    def test_arbitrary_source_code_is_never_executed(self):
        path = self.source / 'app.js'
        path.write_text(path.read_text() + '\nthrow Error("must not execute");')
        self.run_cli()
        self.assertTrue((self.output / 'market-study.json').exists())

    def test_explicit_inline_literal_ast_mapping(self):
        path = self.source / 'app.js'
        path.write_text(path.read_text().replace('const MAP_CENTER = [-100.5, 40.25];',
                                               'initializeMap({center: [-100.5, 40.25]});'))
        self.change_mapping(lambda m: m['sources'].update({'center': {
            'file': 'app.js', 'ast_pointer': '/body/0/expression/arguments/0/properties/0/value'}}))
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertEqual(package['map']['center'], [-100.5, 40.25])

    def test_source_symlink_and_relative_source_refused(self):
        original = self.source / 'data.js'
        destination = self.base / 'outside.js'
        original.rename(destination)
        original.symlink_to(destination)
        self.assertIn('symlink', str(self.run_cli(ok=False)))
        self.source = Path('relative-source')
        self.assertIn('absolute', str(self.run_cli(ok=False)))

    def test_ignore_template_binding_requires_a_reason(self):
        path = self.source / 'app.js'
        path.write_text(path.read_text() + '\nconst TEMPLATE_LABEL = "Template only";')
        self.assertIn('unmapped constant', str(self.run_cli(ok=False)))
        self.change_mapping(lambda m: m['ignore'].update({'app.js:TEMPLATE_LABEL': 'Shared template label, no deal value'}))
        self.run_cli()

    def test_duplicate_keys_and_computed_properties_fail(self):
        path = self.source / 'app.js'
        original = path.read_text()
        for expression in ('{a: 1, a: 2}', '{["a"]: 1}', '[...other]'):
            path.write_text(original.replace('const MAP_ZOOM = 11;', 'const MAP_ZOOM = ' + expression + ';'))
            self.assertFalse(self.run_cli(ok=False)['ok'])

    def test_output_must_be_external_new_and_absolute(self):
        self.output = self.source / 'output'
        self.assertIn('output', str(self.run_cli(ok=False)))
        self.output = ROOT / 'client-portal/forbidden-extract'
        self.assertIn('output', str(self.run_cli(ok=False)))
        self.output = self.base / 'exists'
        self.output.mkdir()
        self.assertIn('output', str(self.run_cli(ok=False)))
        self.output = Path('relative-output')
        self.assertIn('absolute', str(self.run_cli(ok=False)))


if __name__ == '__main__':
    unittest.main()
