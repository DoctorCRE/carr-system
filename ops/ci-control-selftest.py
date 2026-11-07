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
                    if not job.get('container'):
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

    def test_hosted_database_lanes_require_image_without_acquisition(self):
        import re
        from ci_unit_shards import POSTGRES_PROFILE, COUNT
        self.assertEqual(POSTGRES_PROFILE, 'both')
        self.assertEqual(COUNT, 5)
        lanes = {'ci.yml': ('fast', 'gates', 'classes', 'migration-fixtures'),
                 'main-canary.yml': ('classes',), 'unit-shards.yml': ('unit',)}
        images = set()
        for filename, names in lanes.items():
            jobs = workflow(filename)['jobs']
            for name in names:
                job = jobs[name]
                image = job['container']['image']
                self.assertRegex(image, r'ghcr.io/jbookout/software-factory/ci-postgres@sha256:[0-9a-f]{64}')
                images.add(re.search(r'ghcr.io/[^\s\']+', image).group())
                self.assertEqual(job['env']['CARR_CI_POSTGRES_IMAGE'], POSTGRES_PROFILE)
                self.assertEqual(job['defaults']['run']['shell'], 'bash')
                if name == 'classes':
                    for selected in ('unit', 'gates', 'migration'):
                        self.assertIn("matrix.classes == '" + selected + "'", image)
                checks = [step for step in job['steps'] if 'ci-apt' in step.get('uses', '')
                          or 'ci-postgres.sh' in step.get('run', '')]
                self.assertTrue(checks)
                for step in checks:
                    if 'run' in step:
                        self.assertEqual(step['timeout-minutes'], 5)
            for job in jobs.values():
                for step in job.get('steps', []):
                    command = step.get('run', '')
                    self.assertNotRegex(command, r'apt(?:-get)?|curl.*postgres|wget.*postgres')
                    if 'ci-zsh.sh' in command:
                        self.assertEqual(step.get('if'), "matrix.classes != 'unit' && matrix.classes != 'gates' && matrix.classes != 'migration' && matrix.classes != 'dependency'")
        self.assertEqual(len(images), 1)
        action = workflow('../actions/ci-apt/action.yml')
        steps = action['runs']['steps']
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['env']['CARR_CI_POSTGRES_IMAGE'], 'both')
        self.assertNotRegex(json.dumps(steps), r'apt-get|actions/cache|--postgres-attempt')
        shards = workflow('unit-shards.yml')['jobs']['unit']
        run = next(step for step in shards['steps'] if step.get('name') == 'Run assigned MCP suites')
        self.assertIn('@postgres:5432/', run['env']['CARR_CI_DATABASE_URL'])
        canary = workflow('main-canary.yml')['jobs']['classes']['steps']
        run = next(step for step in canary if step.get('name') == "Run this group's check classes")
        self.assertIn('python ops/local-pg-ci.py --class migration --migration-part core', run['run'])
        self.assertIn('ops/ci.sh --strict --only migration --migration-part fixtures', run['run'])
        self.assertNotIn('local-pg-ci.py --class migration --migration-part fixtures', run['run'])

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
        self.assertEqual(wf['jobs']['select']['if'],
                         "${{ !cancelled() && !github.event.pull_request.draft }}")
        self.assertEqual(wf['jobs']['checks']['if'], "${{ always() }}")
        self.assertEqual(wf['jobs']['checks']['name'],
                         "ops/ci.sh --strict")
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
        self.assertEqual(wf['jobs']['checks']['name'], "ops/ci.sh --strict")
        self.assertIn('always()', wf['jobs']['checks']['if'])
        self.assertIn('!cancelled()', wf['jobs']['classes']['if'])
        self.assertEqual(set(wf['jobs']['checks']['needs']), {'select', 'lint', 'unit', 'fast', 'gates', 'secret', 'unit-coverage', 'scope', 'classes', 'migration-fixtures', 'launchd-python39'})
        steps = wf['jobs']['checks']['steps']
        verdict = next(step for step in steps if step.get('name') == 'Fail unless every class group succeeded')
        self.assertIn('python3 ops/ci-evidence.py verdict', verdict['run'])
        self.assertEqual(verdict['env']['CI_NEEDS'], '${{ toJSON(needs) }}')
        self.assertNotIn('GH_TOKEN', verdict['env'])

if __name__=='__main__': unittest.main()
