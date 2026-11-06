#!/usr/bin/env python3
"""Exercise hosted event isolation and class conclusions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def workflow(filename="ci.yml"):
    return json.loads(subprocess.check_output(['node', '-e',
        "const y=require('js-yaml'),fs=require('fs');console.log(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
        str(ROOT / '.github/workflows' / filename)], cwd=ROOT / 'mcp-server', text=True))


class Controls(unittest.TestCase):
    def test_pr_gates_install_the_shared_pinned_scanner_before_checks(self):
        jobs = workflow()['jobs']
        install = next(step for step in jobs['secret']['steps']
                       if step.get('run') == 'python3 ops/gitleaks.py --install')
        steps = jobs['gates']['steps']
        checks = next(step for step in steps if step.get('name') == 'Gates checks')
        self.assertIn(install, steps[:steps.index(checks)])

    def test_node_install_reuses_downloads_without_duplicating_dependency_audit(self):
        wf = workflow()
        setup = next(step for step in wf['jobs']['classes']['steps']
                     if 'actions/setup-node@' in step.get('uses', ''))
        self.assertFalse(setup['with']['package-manager-cache'])
        cache = workflow('../actions/ci-downloads/action.yml')
        self.assertIn('$ImageOS-$ImageVersion', cache['runs']['steps'][0]['run'])
        npm, pip = cache['runs']['steps'][1:]
        for step, locks in ((npm, ('.nvmrc', 'mcp-server/package-lock.json', 'practice-plugin/package-lock.json')),
                            (pip, ('.python-version', 'requirements.lock'))):
            key = step['with']['key']
            self.assertIn('${{ runner.os }}-${{ runner.arch }}-${{ steps.image.outputs.identity }}', key)
            for lock in locks:
                self.assertIn(lock, key)
            self.assertNotIn('restore-keys', step['with'])
        for filename in ('ci.yml', 'main-canary.yml', 'unit-shards.yml'):
            for job in workflow(filename)['jobs'].values():
                steps = job.get('steps', [])
                setups = [step for step in steps if 'actions/setup-node@' in step.get('uses', '')
                          or 'actions/setup-python@' in step.get('uses', '')]
                if not setups:
                    continue
                with self.subTest(workflow=filename, job=job.get('name')):
                    self.assertTrue(all('cache' not in step['with'] for step in setups))
                    restore = next(step for step in steps if step.get('uses') == './.github/actions/ci-downloads')
                    self.assertGreater(steps.index(restore), steps.index(setups[-1]))
                    self.assertEqual(restore.get('if'), setups[-1].get('if'))
        install = next(step for step in wf['jobs']['classes']['steps']
                       if step.get('name') == 'Install Node dependencies')
        commands = install['run'].splitlines()
        self.assertEqual(commands, ['npm --prefix mcp-server ci --no-audit',
                                    'npm --prefix practice-plugin ci --no-audit'])
        self.assertIn('dependency', wf['jobs']['classes']['strategy']['matrix']['classes'])
        # The strict class, rather than install's informational audit, retains
        # the existing vulnerability policy and fails on unavailable reports.
        self.assertIn('["npm", "audit", "--omit=dev", "--audit-level=high", "--json"]',
                      (ROOT / 'ops/ci-dep-check.py').read_text())

    def test_class_jobs_do_not_wait_for_a_scope_runner(self):
        job = workflow()['jobs']['classes']
        self.assertNotIn('scope', job.get('needs', []))
        migration = next(step for step in job['steps'] if step.get('id') == 'scope')
        self.assertIn('ops/db-acceptance-scope.py', migration['run'])
        self.assertTrue(migration['continue-on-error'])
        self.assertEqual(job['strategy']['matrix']['classes'][0], 'migration')
        for step in job['steps']:
            self.assertNotIn('needs.scope', json.dumps(step))

    def test_postgres_install_is_bounded_and_retries_once(self):
        installs = [step for filename in ('ci.yml', 'main-canary.yml')
                    for job in workflow(filename)['jobs'].values() for step in job.get('steps', [])
                    if step.get('name') == 'Install PostgreSQL 17']
        for step in installs:
            self.assertEqual(step.get('timeout-minutes'), 5)
            self.assertEqual(step['run'], 'bash ops/ci-postgres.sh')
        canary = workflow('main-canary.yml')['jobs']['classes']['steps']
        install = next(step for step in canary if step.get('name') == 'Install PostgreSQL 17')
        configure = next(step for step in canary
                         if 'create_main_cluster = false' in step.get('run', ''))
        self.assertLess(canary.index(configure), canary.index(install))
        self.assertEqual(configure.get('if'), "contains(matrix.classes, 'migration')")
        self.assertFalse(any('apt-get install' in step.get('run', '') for step in canary))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeout = root / 'timeout'
            timeout.write_text('#!' + sys.executable + "\n"
                "import os,pathlib,sys\n"
                "p=pathlib.Path(os.environ['ATTEMPTS'])\n"
                "n=int(p.read_text())+1 if p.exists() else 1\n"
                "p.write_text(str(n))\n"
                "assert sys.argv[1:3] == ['--kill-after=5s','110s'] and sys.argv[-1] == '--postgres-attempt' and sys.argv[-2].endswith('/ci-apt.py')\n"
                "sys.exit({'timeout':124,'failure':1,'missing':127}.get(os.environ['INSTALL_RESULT'], 1 if n==1 else 0))\n")
            timeout.chmod(0o755)
            for mode, expected in [('retry-success', 0), ('timeout', 1), ('failure', 1), ('missing', 1)]:
                attempts = root / (mode + '-attempts')
                result = subprocess.run(['bash', 'ops/ci-postgres.sh'], cwd=ROOT,
                    env={**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH'],
                         'ATTEMPTS': str(attempts), 'INSTALL_RESULT': mode},
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual(attempts.read_text(), '2')

    def test_native_candidate_event_contract(self):
        wf = workflow()
        self.assertEqual(wf['on']['merge_group']['types'], ['checks_requested'])
        self.assertEqual(wf['on']['pull_request']['types'],
                         ['opened', 'edited', 'synchronize', 'reopened',
                          'converted_to_draft', 'ready_for_review'])
        metadata = "github.event_name == 'pull_request' && github.event.action == 'edited' && github.event.changes.base == null"
        self.assertEqual(wf['concurrency'], {
            'group': "${{ github.workflow }}-${{ github.event_name }}-${{ (" + metadata + ") && format('metadata-{0}', github.run_id) || github.event.pull_request.number || github.ref }}",
            'cancel-in-progress': "${{ github.event_name == 'pull_request' && !(" + metadata + ") }}"})
        candidate = "!(" + metadata + ")"
        self.assertEqual(wf['jobs']['select']['if'],
                         "${{ !cancelled() && !github.event.pull_request.draft && " + candidate + " }}")
        self.assertEqual(wf['jobs']['checks']['if'], "${{ always() && " + candidate + " }}")
        self.assertEqual(wf['jobs']['checks']['name'],
                         "${{ (" + metadata + ") && 'CI metadata (no validation)' || 'ops/ci.sh --strict' }}")
        for name, job in wf['jobs'].items():
            if name in ('select', 'checks'):
                continue
            with self.subTest(job=name):
                self.assertIn('select', job['needs'])
                self.assertIn('needs.select.outputs', job['if'])
                self.assertIn('!cancelled()', job['if'])
        for name in ('classes', 'migration-fixtures'):
            self.assertFalse(wf['jobs'][name]['strategy']['fail-fast'])

    def test_required_context_reports_even_after_a_failed_class(self):
        wf = workflow()
        self.assertIn("|| 'ops/ci.sh --strict'", wf['jobs']['checks']['name'])
        self.assertIn('always()', wf['jobs']['checks']['if'])
        self.assertIn('!cancelled()', wf['jobs']['classes']['if'])
        self.assertEqual(set(wf['jobs']['checks']['needs']), {'select', 'lint', 'unit', 'fast', 'gates', 'secret', 'unit-coverage', 'scope', 'classes', 'migration-fixtures', 'launchd-python39'})
        steps = wf['jobs']['checks']['steps']
        verdict = next(step for step in steps if step.get('name') == 'Fail unless every class group succeeded')
        self.assertIn('python3 ops/ci-evidence.py verdict', verdict['run'])
        self.assertEqual(verdict['env']['CI_NEEDS'], '${{ toJSON(needs) }}')
        self.assertNotIn('GH_TOKEN', verdict['env'])

if __name__=='__main__': unittest.main()
