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

    def run_product(self, report, *args):
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory) / 'report.json', Path(directory) / 'out'
            source.write_text(json.dumps(report))
            out.write_text('earlier step\n')
            resolved = [str(out) if arg == 'OUT' else arg for arg in args]
            result = subprocess.run([sys.executable, str(ROOT / 'ops/doc-drift-product.py'), '--report', str(source),
                                     *resolved], capture_output=True, text=True)
            return result.returncode, out.read_text()

    def summarize(self, report, known):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'baseline.json'
            baseline.write_text(json.dumps(known))
            return self.run_product(report, '--baseline', str(baseline), '--summary', 'OUT')

    gone = dict(file='README.md', line=3, kind='path', target='bin/gone.sh', suggestion='bin/here.sh')
    verb = dict(file='README.md', line=9, kind='verb', target='gone', suggestion=None)

    def test_baseline_records_each_stale_claim_without_its_line(self):
        code, text = self.run_product(self.report([self.verb, self.gone, dict(self.gone, line=12)]),
                                      '--write-baseline', 'OUT')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text), dict(schema='doc-drift-baseline/v1', source_sha='a' * 40, findings=[
            dict(file='README.md', kind='path', target='bin/gone.sh'),
            dict(file='README.md', kind='path', target='bin/gone.sh'),
            dict(file='README.md', kind='verb', target='gone')]))

    def test_drift_beyond_the_baseline_is_summarized_and_fails_the_run(self):
        known = dict(findings=[dict(file='README.md', kind='verb', target='gone')])
        code, text = self.summarize(self.report([self.gone, dict(self.verb, line=40)]), known)
        self.assertEqual(code, 1)
        self.assertEqual(text, 'earlier step\n'
                         '## Instruction drift at `aaaaaaaaaaaa`\n\n'
                         '1 new stale claims beyond the baseline across 2 instruction files. '
                         '1 known stale claims are in `scripts/doc-drift/baseline.json`. Owner: orchestrator. '
                         'Repair the instruction, then rerun `ops/ci.sh --doc-drift`.\n\n'
                         '- `README.md:3` missing path `bin/gone.sh`; candidate `bin/here.sh`\n')

    def test_known_drift_passes_and_reports_the_baseline_count(self):
        known = dict(findings=[dict(file='README.md', kind='path', target='bin/gone.sh'),
                               dict(file='README.md', kind='verb', target='gone')])
        code, text = self.summarize(self.report([dict(self.gone, line=7), self.verb]), known)
        self.assertEqual(code, 0)
        self.assertIn('0 new stale claims beyond the baseline across 2 instruction files. '
                      '2 known stale claims are in', text)

    def test_a_repeated_claim_beyond_its_baseline_count_fails(self):
        known = dict(findings=[dict(file='README.md', kind='path', target='bin/gone.sh')])
        code, text = self.summarize(self.report([self.gone, dict(self.gone, line=30)]), known)
        self.assertEqual(code, 1)
        self.assertIn('- `README.md:30` missing path `bin/gone.sh`', text)

    def test_summary_refuses_to_run_without_a_baseline(self):
        code, _ = self.run_product(self.report([]), '--summary', 'OUT')
        self.assertEqual(code, 2)

    def test_committed_baseline_is_a_versioned_claim_list(self):
        known = json.loads((ROOT / 'scripts/doc-drift/baseline.json').read_text())
        self.assertEqual(known['schema'], 'doc-drift-baseline/v1')
        self.assertTrue(known['findings'])
        self.assertTrue(all(set(f) == {'file', 'kind', 'target'} for f in known['findings']))

    def test_weekly_run_summarizes_after_upload_and_pull_requests_only_warn(self):
        workflow = yaml.load((ROOT / '.github/workflows/doc-drift.yml').read_text(), Loader=yaml.BaseLoader)
        steps = workflow['jobs']['check']['steps']
        upload = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/upload-artifact@'))
        summary = next(i for i, step in enumerate(steps) if '--summary' in step.get('run', ''))
        self.assertLess(upload, summary)
        self.assertEqual(steps[upload]['if'], 'always()')
        self.assertEqual(steps[summary]['if'], "github.event_name != 'pull_request'")
        self.assertIn('"$GITHUB_STEP_SUMMARY"', steps[summary]['run'])
        self.assertIn('--baseline scripts/doc-drift/baseline.json', steps[summary]['run'])
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
