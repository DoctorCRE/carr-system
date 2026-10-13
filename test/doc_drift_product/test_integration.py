import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ProductIntegrationTest(unittest.TestCase):
    def load(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / 'ops' / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_artifact_omits_unchecked_values_everywhere(self):
        module = self.load('doc-drift-product')
        claim = dict(file='README.md', line=1, kind='path', target='/private/person@example.invalid/key',
                     sources=['https://example.invalid/?token=secret'], unchecked='machine-local reference')
        report = dict(claims=[claim], unchecked=[claim], findings=[])
        public = module.public_report(report)
        self.assertEqual(public['claims'], [dict(file='README.md', line=1, kind='path', unchecked='machine-local reference')])
        self.assertEqual(public['unchecked'], public['claims'])
        self.assertNotIn('person@', json.dumps(public))
        self.assertNotIn('secret', json.dumps(public))
        self.assertIn('person@', json.dumps(report))

    def test_all_main_events_share_one_writer_group(self):
        workflow = yaml.load((ROOT / '.github/workflows/doc-drift.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['concurrency']['group'], "doc-drift-${{ github.event.pull_request.number || 'main' }}")
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], 'false')

    def test_dependency_projection_refuses_changed_snapshot_version(self):
        module = self.load('doc-drift-product')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts/doc-drift').mkdir(parents=True)
            (root / 'requirements.txt').write_text('PyYAML==6.0.3\n')
            (root / 'scripts/doc-drift/requirements.txt').write_text('PyYAML==6.0.2\n')
            with self.assertRaisesRegex(ValueError, 'dependency projection'):
                module.verify_dependencies(root)
            (root / 'scripts/doc-drift/requirements.txt').write_text('PyYAML==6.0.3\n')
            module.verify_dependencies(root)


    def test_actual_registered_verbs_equal_checker_inventory(self):
        spec = importlib.util.spec_from_file_location('doc_drift_check', ROOT / 'scripts/doc-drift/check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry = subprocess.check_output([
            'node', '--input-type=module', '-e',
            'import {TOOLS} from "./mcp-server/src/tools.js"; console.log(JSON.stringify(Object.keys(TOOLS)));'
        ], cwd=ROOT, text=True)
        tree = module.Tree(ROOT)
        self.assertEqual(tree.verbs, set(json.loads(registry)))
        findings = [module.check(tree, claim) for claim in module.extract(
            tree, 'README.md', 'Use verb `call-verb` and verb `read-cre-lifecycle`. Use verb `admit-journey-one-minimum-receipt`.')]
        self.assertEqual([f['target'] for f in findings if f], ['admit-journey-one-minimum-receipt'])

    def test_registry_dependencies_installed_before_scan(self):
        workflow = yaml.load((ROOT / '.github/workflows/doc-drift.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['check']['steps']
        install = next((i for i, step in enumerate(steps) if step.get('run') == 'npm ci --prefix mcp-server --ignore-scripts'), None)
        scan = next(i for i, step in enumerate(steps) if step.get('name') == 'Check changed instructions and their references')
        self.assertIsNotNone(install)
        self.assertLess(install, scan)


if __name__ == '__main__':
    unittest.main()
