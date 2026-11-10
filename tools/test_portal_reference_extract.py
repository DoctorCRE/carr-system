"""Synthetic CLI acceptance tests. No client sources or network access."""
import contextlib
import importlib.util
import io
from unittest.mock import patch
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

    def read_data(self):
        spec = importlib.util.spec_from_file_location('extractor', CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        program = module.node_call({'action': 'parse', 'text': (self.source / 'data.ts').read_text()})
        return module.literal(module.collect_bindings(program)['window.referenceData'][0], 'data.ts')

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

    def test_original_javascript_packages_remain_byte_identical(self):
        expected = {
            'ref-a': '58b20573d0a3d0c42833a69718826882828cad390bc8ec1e9a9a1f42531378d4',
            'ref-v': '1974ab25837a3793f66bd9222d413c283adc8a0ebdd3144fa61745e719872064',
        }
        for site, digest in expected.items():
            with self.subTest(site=site):
                self.source = FIXTURES / site
                self.output = self.base / site
                self.run_cli()
                self.assertEqual(hashlib.sha256((self.output / 'market-study.json').read_bytes()).hexdigest(), digest)

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
        self.assert_provenance()

    def test_nested_property_leaves_build_arrays(self):
        data = self.read_data()
        def change(mapping):
            original = mapping['fields'].pop('/properties')
            for index in reversed(range(len(data['properties']))):
                for key in data['properties'][index]:
                    mapping['fields'][f'/properties/{index}/{key}'] = {
                        **original, 'pointer': f'/properties/{index}/{key}'}
        self.change_mapping(change)
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assert_provenance()
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

    def test_numeric_object_override(self):
        pointer = '/demographics/income_bands/areas/0/counts/100000'
        self.change_mapping(lambda m: m['fields'].update({pointer: {'source': 'data', 'pointer': pointer}}))
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertIsInstance(package['demographics']['income_bands']['areas'][0]['counts'], dict)
        self.assert_provenance()

    def test_reconstruct_numeric_objects_from_leaves(self):
        data = self.read_data()
        def leaves(value, path):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    yield from leaves(child, path + '/' + key)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    yield from leaves(child, path + '/' + str(index))
            else:
                yield path
        def change(mapping):
            mapping['fields'].pop('/demographics')
            for pointer in reversed(list(leaves(data['demographics'], '/demographics'))):
                mapping['fields'][pointer] = {'source': 'data', 'pointer': pointer}
        self.change_mapping(change)
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertEqual(package['demographics']['income_bands'], data['demographics']['income_bands'])
        for actual, expected in zip(package['demographics']['micro_zones']['zones'], data['demographics']['micro_zones']['zones']):
            self.assertEqual(actual['population'], expected['population'])

    def assert_provenance(self):
        report = json.loads((self.output / 'manifest.json').read_text())
        ids = next(row for row in report['rows'] if row['id'] == 'A-G11')
        self.assertTrue(ids['sources'], 'property IDs need effective source provenance')
        center = next(row for row in report['rows'] if row['id'] == 'A-A03')['sources']['/map/center']
        origins = center if isinstance(center, list) else [center]
        self.assertTrue(all(entry.get('binding') == 'MAP_CENTER' for entry in origins), origins)
        package = json.loads((self.output / 'market-study.json').read_text())
        data = self.read_data()
        def select(value, pointer):
            for key in pointer[1:].split('/') if pointer else []:
                key = key.replace('~1', '/').replace('~0', '~')
                value = value[int(key)] if isinstance(value, list) else value[key]
            return value
        bindings = {'window.referenceData': data, 'MAP_CENTER': [-100.5, 40.25], 'MAP_ZOOM': 11}
        for row in report['rows']:
            if row['status'] != 'mapped':
                continue
            for pointer in row['fields']:
                if pointer in row['waived']:
                    continue
                entries = row['sources'][pointer]
                self.assertIsInstance(entries, list)
                self.assertTrue(entries, row['id'])
                for entry in entries:
                    actual = select(package, entry['target'])
                    if 'asset_field' in entry:
                        asset = select(package, entry['asset_field'])
                        self.assertEqual(asset['sha256'], hashlib.sha256((self.source / entry['file']).read_bytes()).hexdigest())
                    else:
                        expected = select(bindings[entry['binding']], entry['pointer'])
                        self.assertEqual(actual, expected, entry)
        center = next(row for row in report['rows'] if row['id'] == 'A-A03')['sources']['/map/center']
        self.assertEqual([entry['pointer'] for entry in center], ['/0', '/1'])
        self.assertTrue(all(entry['binding'] == 'MAP_CENTER' for entry in center))

    def call_main(self, module):
        stdout = io.StringIO()
        with patch.object(sys, 'argv', ['extract', '--source', str(self.source), '--output', str(self.output)]), contextlib.redirect_stdout(stdout):
            status = module.main()
        return status, json.loads(stdout.getvalue())

    def test_workflow_failure_transitions(self):
        spec = importlib.util.spec_from_file_location('extractor', CLI)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for fault in ('parse_timeout', 'validate_timeout', 'staging_create', 'files_create', 'write', 'chmod', 'rename'):
            with self.subTest(fault=fault):
                actual_run = module.subprocess.run
                def timeout(*args, **kwargs):
                    self.assertEqual(kwargs.get('timeout'), 60)
                    action = json.loads(kwargs['input'])['action']
                    if action == fault.removesuffix('_timeout'):
                        raise subprocess.TimeoutExpired('node PRIVATE_VALUE', 60)
                    return actual_run(*args, **kwargs)
                if fault.endswith('_timeout'):
                    seam = patch.object(module.subprocess, 'run', side_effect=timeout)
                elif fault == 'staging_create':
                    seam = patch.object(module.tempfile, 'mkdtemp', side_effect=OSError('PRIVATE_VALUE'))
                else:
                    method = {'files_create': 'mkdir', 'write': 'write_bytes', 'chmod': 'chmod', 'rename': 'rename'}[fault]
                    seam = patch.object(Path, method, side_effect=OSError('PRIVATE_VALUE'))
                with seam:
                    status, result = self.call_main(module)
                self.assertEqual(status, 2)
                self.assertFalse(result['ok'])
                self.assertNotIn('PRIVATE_VALUE', json.dumps(result))
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.base.glob('.reference-extract-*')), [])

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
        path = self.source / 'data.ts'
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
        path = self.source / 'app.ts'
        original = path.read_text()
        path.write_text(original + '\nfunction second() { const MAP_ZOOM = 4; }')
        self.assertIn('ambiguous', str(self.run_cli(ok=False)))
        path.write_text(original + '\nconst UNKNOWN_DEAL_VALUE = 99;')
        self.assertIn('UNKNOWN_DEAL_VALUE', str(self.run_cli(ok=False)))

    def test_no_execution_of_javascript(self):
        path = self.source / 'app.ts'
        path.write_text(path.read_text().replace('const MAP_ZOOM = 11;', 'const MAP_ZOOM = (() => { throw Error("executed"); })();'))
        result = self.run_cli(ok=False)
        self.assertIn('nonliteral', str(result))
        self.assertNotIn('executed', json.dumps(result))

    def test_missing_inventory_field_fails(self):
        self.change_mapping(lambda m: m['fields'].update({'/ownership': None}))
        self.assertIn('/ownership', str(self.run_cli(ok=False)))


    def test_json_source_and_missing_asset_mapping(self):
        (self.source / 'data.json').write_text(json.dumps(self.read_data()))
        self.change_mapping(lambda m: m['sources'].update({'data': {'file': 'data.json'}}))
        self.run_cli()
        self.output = self.base / 'without-asset'
        self.change_mapping(lambda m: m['assets'].pop())
        self.assertIn('asset has no source mapping', str(self.run_cli(ok=False)))

    def test_arbitrary_source_code_is_never_executed(self):
        path = self.source / 'app.ts'
        path.write_text(path.read_text() + '\nthrow Error("must not execute");')
        self.run_cli()
        self.assertTrue((self.output / 'market-study.json').exists())

    def test_explicit_inline_literal_ast_mapping(self):
        path = self.source / 'app.ts'
        path.write_text(path.read_text().replace('const MAP_CENTER = [-100.5, 40.25];',
                                               'initializeMap({center: [-100.5, 40.25]});').replace(
            'export { MAP_CENTER, MAP_ZOOM };', 'export { MAP_ZOOM };'))
        self.change_mapping(lambda m: m['sources'].update({'center': {
            'file': 'app.ts', 'ast_pointer': '/body/0/expression/arguments/0/properties/0/value'}}))
        self.run_cli()
        package = json.loads((self.output / 'market-study.json').read_text())
        self.assertEqual(package['map']['center'], [-100.5, 40.25])

    def test_source_symlink_and_relative_source_refused(self):
        original = self.source / 'data.ts'
        destination = self.base / 'outside.js'
        original.rename(destination)
        original.symlink_to(destination)
        self.assertIn('symlink', str(self.run_cli(ok=False)))
        self.source = Path('relative-source')
        self.assertIn('absolute', str(self.run_cli(ok=False)))

    def test_ignore_template_binding_requires_a_reason(self):
        path = self.source / 'app.ts'
        path.write_text(path.read_text() + '\nconst TEMPLATE_LABEL = "Template only";')
        self.assertIn('unmapped constant', str(self.run_cli(ok=False)))
        self.change_mapping(lambda m: m['ignore'].update({'app.ts:TEMPLATE_LABEL': 'Shared template label, no deal value'}))
        self.run_cli()

    def test_duplicate_keys_and_computed_properties_fail(self):
        path = self.source / 'app.ts'
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
