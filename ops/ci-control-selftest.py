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


def expression(value, github, cancelled=False, matrix=None):
    # Evaluate the workflow's actual expression with GitHub's missing-property
    # semantics; no reimplementation of the intended event predicate.
    if not isinstance(value, str):
        return value
    source = '''const get=(o,p)=>p.split('.').reduce((v,k)=>v&&v[k],o)||'';
    const github=JSON.parse(process.argv[1]);const always=()=>true;const cancelled=()=>JSON.parse(process.argv[3]);const fromJSON=JSON.parse;
    const needs={scope:{outputs:{full:'true'}}};
    const matrix=JSON.parse(process.argv[4]);
    const format=(template,...args)=>template.replace(/\\{(\\d+)\\}/g,(_,i)=>args[Number(i)]);
    const e=process.argv[2].replace(/github(?:\\.[a-zA-Z_]+)+/g,p=>JSON.stringify(get({github},p)));
    console.log(JSON.stringify(eval(e)));'''
    import re
    def evaluate(match):
        return json.loads(subprocess.check_output(['node', '-e', source,
            json.dumps(github), match.group(1), json.dumps(cancelled), json.dumps(matrix or {})], text=True))
    if value.startswith('${{') and value.endswith('}}') and value.count('${{') == 1:
        return evaluate(re.fullmatch(r'\$\{\{(.*?)\}\}', value))
    return re.sub(r'\$\{\{(.*?)\}\}', lambda m: str(evaluate(m)), value)


class Controls(unittest.TestCase):
    def test_node_install_reuses_downloads_without_duplicating_dependency_audit(self):
        wf = workflow()
        setup = next(step for step in wf['jobs']['classes']['steps']
                     if 'actions/setup-node@' in step.get('uses', ''))
        self.assertEqual(setup['with'].get('cache'), 'npm')
        self.assertEqual(setup['with'].get('cache-dependency-path', '').split(),
                         ['mcp-server/package-lock.json', 'practice-plugin/package-lock.json'])
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
        installs = [step for job in workflow()['jobs'].values() for step in job.get('steps', [])
                    if step.get('name') == 'Install PostgreSQL 17']
        for step in installs:
            self.assertEqual(step.get('timeout-minutes'), 5)
            self.assertEqual(step['run'], 'bash ops/ci-postgres.sh')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeout = root / 'timeout'
            timeout.write_text('#!' + sys.executable + "\n"
                "import os,pathlib,sys\n"
                "p=pathlib.Path(os.environ['ATTEMPTS'])\n"
                "n=int(p.read_text())+1 if p.exists() else 1\n"
                "p.write_text(str(n))\n"
                "assert sys.argv[1:] == ['--kill-after=5s','110s','bash','-euo','pipefail','-c','install_postgres']\n"
                "sys.exit(124 if os.environ['INSTALL_RESULT']=='timeout' or n==1 else 0)\n")
            timeout.chmod(0o755)
            for mode, expected in [('retry-success', 0), ('timeout', 1)]:
                attempts = root / (mode + '-attempts')
                result = subprocess.run(['bash', 'ops/ci-postgres.sh'], cwd=ROOT,
                    env={**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH'],
                         'ATTEMPTS': str(attempts), 'INSTALL_RESULT': mode},
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual(attempts.read_text(), '2')

    def test_merge_group_keeps_the_full_required_workflow(self):
        wf = workflow()
        self.assertEqual(wf['on']['merge_group']['types'], ['checks_requested'])
        event = {'workflow': 'CI', 'event_name': 'merge_group', 'event': {}, 'run_id': 42}
        self.assertTrue(expression(wf['jobs']['classes']['if'], event))
        self.assertTrue(expression(wf['jobs']['migration-fixtures']['if'], event))
        self.assertTrue(expression(wf['jobs']['checks']['if'], event))
        exclusions = expression(wf['jobs']['classes']['strategy']['matrix']['exclude'], event)
        self.assertFalse(any(row == {'classes': 'migration'} for row in exclusions))
        self.assertFalse(any(row == {'classes': 'gates'} for row in exclusions))
        self.assertEqual(wf['jobs']['checks']['name'], 'ops/ci.sh --strict')

    def test_ready_metadata_does_not_replace_required_check(self):
        wf = workflow()
        triggers = wf['on']['pull_request']['types']
        self.assertNotIn('ready_for_review', triggers)
        self.assertNotIn('edited', triggers)
        self.assertNotIn('labeled', triggers)
        self.assertTrue({'opened', 'synchronize', 'reopened'} <= set(triggers))
        self.assertEqual(wf['jobs']['checks']['name'], 'ops/ci.sh --strict')

    def test_events(self):
        wf = workflow()
        def event(action, sha='a', base='main', run=1):
            return {'workflow':'CI','event_name':'pull_request','run_id':run,
                    'event':{'action':action,'pull_request':{'number':1665,'head':{'sha':sha},'base':{'ref':base}}}}
        def state(e):
            return (expression(wf['concurrency']['group'], e),
                    expression(wf['concurrency']['cancel-in-progress'], e),
                    bool(expression(wf['jobs']['classes'].get('if', '${{ true }}'),e)),
                    bool(expression(wf['jobs']['checks']['if'], e)))
        old = state(event('opened'))
        new = state(event('synchronize',sha='b',run=2))
        self.assertEqual(old[0],new[0]); self.assertTrue(new[1])
        for action in wf['on']['pull_request']['types']:
            self.assertEqual(state(event(action))[2:], (True, True))
        changed = state(event('synchronize',base='release',run=4))
        self.assertEqual(changed[0],old[0]); self.assertEqual(changed[1:],(True,True,True))
        for name in ('schedule','workflow_dispatch'):
            a={'workflow':'CI','event_name':name,'event':{},'run_id':10}
            b={**a,'run_id':11}
            self.assertNotEqual(state(a)[0],state(b)[0]); self.assertEqual(state(a)[1:],(False,True,True))

    def test_superseded_expensive_jobs_can_cancel(self):
        wf = workflow()
        event = {'event_name': 'pull_request', 'event': {'pull_request': {'draft': False}}}
        for job in ('classes', 'migration-fixtures'):
            with self.subTest(job=job):
                condition = wf['jobs'][job]['if']
                self.assertTrue(expression(condition, event, cancelled=False))
                self.assertFalse(expression(condition, event, cancelled=True))
        self.assertTrue(expression(wf['jobs']['checks']['if'], event, cancelled=True))

    def test_required_context_reports_even_after_a_failed_class(self):
        wf = workflow()
        self.assertEqual(wf['jobs']['checks']['name'], 'ops/ci.sh --strict')
        self.assertIn('always()', wf['jobs']['checks']['if'])
        self.assertIn('!cancelled()', wf['jobs']['classes']['if'])
        self.assertEqual(set(wf['jobs']['checks']['needs']), {'scope', 'classes', 'migration-fixtures', 'fallow'})
        steps = wf['jobs']['checks']['steps']
        verdict = next(step for step in steps if step.get('name') == 'Fail unless every class group succeeded')
        self.assertIn('python3 ops/ci-evidence.py verdict', verdict['run'])
        self.assertEqual(verdict['env']['CI_NEEDS'], '${{ toJSON(needs) }}')
        self.assertNotIn('GH_TOKEN', verdict['env'])

if __name__=='__main__': unittest.main()
