import importlib.util
import json
import subprocess
import sys
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


    def report(self, findings):
        return dict(schema='doc-drift/v1', owner='orchestrator', scope='full', source_sha='a' * 40,
                    files_checked=['README.md', 'RECOVERY.md'], claims=[], unchecked=[], findings=findings)

    def summarize(self, report):
        with tempfile.TemporaryDirectory() as directory:
            source, summary = Path(directory) / 'report.json', Path(directory) / 'summary.md'
            source.write_text(json.dumps(report))
            summary.write_text('earlier step\n')
            result = subprocess.run([sys.executable, str(ROOT / 'ops/doc-drift-product.py'), '--report', str(source),
                                     '--summary', str(summary)], capture_output=True, text=True)
            return result.returncode, summary.read_text()

    def test_drift_is_summarized_and_fails_the_run(self):
        code, text = self.summarize(self.report([
            dict(file='README.md', line=3, kind='path', target='bin/gone.sh', suggestion='bin/here.sh'),
            dict(file='README.md', line=9, kind='verb', target='gone', suggestion=None)]))
        self.assertEqual(code, 1)
        self.assertEqual(text, 'earlier step\n'
                         '## Instruction drift at `aaaaaaaaaaaa`\n\n'
                         '2 stale claims in 1 of 2 instruction files. Owner: orchestrator. '
                         'Repair the instruction, then rerun `ops/ci.sh --doc-drift`.\n\n'
                         '- `README.md:3` missing path `bin/gone.sh`; candidate `bin/here.sh`\n'
                         '- `README.md:9` missing verb `gone`\n')

    def test_clean_scan_passes_with_a_summary(self):
        code, text = self.summarize(self.report([]))
        self.assertEqual(code, 0)
        self.assertIn('0 stale claims in 0 of 2 instruction files.', text)

    def test_weekly_run_summarizes_after_upload_and_pull_requests_only_warn(self):
        workflow = yaml.load((ROOT / '.github/workflows/doc-drift.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['check']['steps']
        upload = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/upload-artifact@'))
        summary = next(i for i, step in enumerate(steps) if '--summary' in step.get('run', ''))
        self.assertLess(upload, summary)
        self.assertEqual(steps[upload]['if'], 'always()')
        self.assertEqual(steps[summary]['if'], "github.event_name != 'pull_request'")
        self.assertIn('"$GITHUB_STEP_SUMMARY"', steps[summary]['run'])
        self.assertIn('schedule', workflow['on'])

    def test_no_job_can_write_github_issues(self):
        workflow = yaml.load((ROOT / '.github/workflows/doc-drift.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['permissions'], dict(contents='read'))
        self.assertEqual(list(workflow['jobs']), ['check'])
        self.assertNotIn('permissions', workflow['jobs']['check'])
        self.assertFalse((ROOT / 'scripts/doc-drift/loops.py').exists())
        sources = [ROOT / '.github/workflows/doc-drift.yml', ROOT / 'ops/doc-drift-product.py',
                   *sorted((ROOT / 'scripts/doc-drift').glob('*.py'))]
        for source in sources:
            self.assertNotRegex(source.read_text(), r'issues', source.name)

if __name__ == '__main__':
    unittest.main()
