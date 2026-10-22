#!/usr/bin/env python3
"""Exercise hosted event isolation, class conclusions and rerun admission."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import tempfile
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'ops' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow(filename="ci.yml"):
    return json.loads(subprocess.check_output(['node', '-e',
        "const y=require('js-yaml'),fs=require('fs');console.log(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
        str(ROOT / '.github/workflows' / filename)], cwd=ROOT / 'mcp-server', text=True))


def expression(value, github, cancelled=False, matrix=None):
    # Evaluate the workflow's actual expression with GitHub's missing-property
    # semantics; no reimplementation of the intended event predicate.
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
        for action in ('opened', 'synchronize', 'reopened', 'ready_for_review', 'converted_to_draft'):
            self.assertEqual(state(event(action))[2:], (True, True))
        changed = state(event('synchronize',base='release',run=4))
        self.assertEqual(changed[0],old[0]); self.assertEqual(changed[1:],(True,True,True))
        for name in ('schedule','workflow_dispatch'):
            a={'workflow':'CI','event_name':name,'event':{},'run_id':10}
            b={**a,'run_id':11}
            self.assertNotEqual(state(a)[0],state(b)[0]); self.assertEqual(state(a)[1:],(False,True,True))

    def test_draft_cost_split_and_lifecycle(self):
        events = {'opened', 'synchronize', 'reopened', 'ready_for_review', 'converted_to_draft'}
        for filename in ('ci.yml', 'doc-drift.yml'):
            wf = workflow(filename)
            self.assertEqual(set(wf['on']['pull_request']['types']), events)
            previous = None
            for action in events:
                e = {'workflow': wf['name'], 'event_name': 'pull_request', 'run_id': 1,
                     'event': {'action': action, 'pull_request': {'number': 7, 'draft': True}}}
                group = expression(wf['concurrency']['group'], e)
                if previous is not None:
                    self.assertEqual(group, previous)
                previous = group
                self.assertTrue(expression(wf['concurrency']['cancel-in-progress'], e))
            for draft in (True, False):
                e['event']['pull_request']['draft'] = draft
                if filename == 'ci.yml':
                    matrix = wf['jobs']['classes']['strategy']['matrix']
                    excluded = expression(matrix['exclude'], e)
                    actual = [(group, shard) for group in matrix['classes'] for shard in matrix['gate_shard']
                              if not any(all({'classes': group, 'gate_shard': shard}.get(k) == v
                                             for k, v in row.items()) for row in excluded)]
                    expected = [(group, shard) for group in matrix['classes']
                                if not draft or group not in ('gates', 'migration')
                                for shard in ([1, 2] if group == 'gates' else [1])]
                    self.assertEqual(actual, expected)
                    self.assertTrue(expression(wf['jobs']['checks']['if'], e))
                    self.assertEqual(load('ci-evidence').class_groups(), matrix['classes'])
                else:
                    self.assertNotIn('if', wf['jobs']['check'])
            for name in ('schedule', 'workflow_dispatch'):
                e = {'workflow': wf['name'], 'event_name': name, 'event': {}, 'run_id': 10}
                group = expression(wf['concurrency']['group'], e)
                self.assertNotEqual(group, expression(wf['concurrency']['group'], {**e, 'run_id': 11}))
                self.assertFalse(expression(wf['concurrency']['cancel-in-progress'], e))
                if filename == 'ci.yml':
                    exclusions = expression(wf['jobs']['classes']['strategy']['matrix']['exclude'], e)
                    self.assertTrue(all(row.get('gate_shard') == 2 and row['classes'] != 'gates' for row in exclusions))
                    self.assertTrue(expression(wf['jobs']['checks']['if'], e))

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
        self.assertEqual(wf['jobs']['classes']['if'], '${{ !cancelled() }}')
        self.assertEqual(set(wf['jobs']['checks']['needs']), {'scope', 'classes', 'migration-fixtures', 'fallow', 'test-first'})
        steps = wf['jobs']['checks']['steps']
        verdict = next(step for step in steps if step.get('name') == 'Fail unless every class group succeeded')
        self.assertIn('python3 ops/ci-evidence.py verdict', verdict['run'])
        self.assertEqual(verdict['env']['CI_NEEDS'], '${{ toJSON(needs) }}')
        self.assertNotIn('GH_TOKEN', verdict['env'])

    def test_verdict(self):
        mod = load('ci-evidence')
        groups=mod.class_groups()
        jobs=[{'name':'ops/ci.sh --strict --only '+g,'status':'completed','conclusion':'success'} for g in groups]
        self.assertEqual(mod.verdict(jobs,groups)[0],0)
        gates_group = next(g for g in groups if 'gates' in g.split())
        migration_group = next(g for g in groups if 'migration' in g.split())
        by_group = {g: next(j for j in jobs if j['name'] == 'ops/ci.sh --strict --only '+g) for g in groups}
        by_group[gates_group]['conclusion']='failure';by_group[migration_group]['conclusion']='cancelled'
        rc, lines=mod.verdict(jobs,groups)
        self.assertNotEqual(rc,0)
        self.assertIn('gates: FAILED',lines);self.assertIn('migration: CANCELLED',lines)
        self.assertIn('hxprobe: PASSED',lines)
        for c in next(g for g in groups if 'unit' in g.split()).split(): self.assertIn(c+': PASSED',lines)
        missing_migration = [j for j in jobs if j['name'] != 'ops/ci.sh --strict --only '+migration_group]
        missing_rc, missing_lines = mod.verdict(missing_migration,groups)
        self.assertNotEqual(missing_rc,0)
        self.assertIn('migration: MISSING',missing_lines)
        self.assertIn('hxprobe: PASSED',missing_lines)
        self.assertNotEqual(mod.verdict(jobs+[jobs[0]],groups)[0],0)
        for conclusion in ('skipped','timed_out','neutral',None):
            changed=[{**j,'conclusion':conclusion} for j in jobs]
            self.assertNotEqual(mod.verdict(changed,groups)[0],0)
        self.assertNotEqual(mod.verdict([{**j,'status':'in_progress'} for j in jobs],groups)[0],0)

    def test_rerun(self):
        mod=load('ci-rerun')
        run={'id':123,'name':'CI','path':'.github/workflows/ci.yml','event':'pull_request','status':'completed',
             'conclusion':'failure','head_sha':'a'*40,'run_attempt':1,'pull_requests':[{'number':1665}]}
        job={'id':456,'run_id':123,'name':'ops/ci.sh --strict --only gates','status':'completed','conclusion':'failure'}
        pr={'state':'open','head':{'sha':'a'*40}}
        self.assertEqual(mod.admit(run,job,pr,'a'*40), 'gates')
        for field,value in [('run_attempt',3),('status','in_progress'),('name','Other'),('head_sha','b'*40),('event','workflow_dispatch')]:
            with self.assertRaises(mod.Refusal): mod.admit({**run,field:value},job,pr,'a'*40)
        for field,value in [('run_id',999),('conclusion','success'),('name','unknown'),('status','queued')]:
            with self.assertRaises(mod.Refusal): mod.admit(run,{**job,field:value},pr,'a'*40)
        self.assertEqual(mod.admit({**run,'run_attempt':2},{**job,'conclusion':'cancelled'},pr,'a'*40),'gates')
        policy=json.loads((ROOT/'ops/config/platform-metering.v1.json').read_text())
        remote=Mock(repo=mod.REPO);remote.snapshot.return_value=(run,job,pr)
        checks=Mock();dispatch=Mock()
        mod.rerun(remote,123,456,'a'*40,policy,checks,dispatch)
        checks.assert_called_once_with('gates');dispatch.assert_called_once_with(123,456,1,'a'*40)
        checks.side_effect=mod.Refusal('local CI failed');dispatch.reset_mock()
        with self.assertRaises(mod.Refusal): mod.rerun(remote,123,456,'a'*40,policy,checks,dispatch)
        dispatch.assert_not_called()
        checks.side_effect=None
        remote.snapshot.side_effect=[(run,job,pr),({**run,'run_attempt':2},job,pr)]
        with self.assertRaises(mod.Refusal): mod.rerun(remote,123,456,'a'*40,policy,checks,dispatch)
        dispatch.assert_not_called()
        remote.snapshot.side_effect=None
        policy['temporary_controls']['github_actions_pause']['repository_actions_enabled']=False
        with self.assertRaises(Exception): mod.rerun(remote,123,456,'a'*40,policy,checks,dispatch)
        dispatch.assert_not_called()

    def test_rerun_accepts_every_workflow_class_job_but_not_the_aggregate(self):
        mod = load('ci-rerun')
        job_spec = workflow()['jobs']['classes']
        matrix = job_spec['strategy']['matrix']
        event = {'event_name': 'pull_request', 'event': {'pull_request': {'draft': False}}}
        exclusions = expression(matrix['exclude'], event)
        run = {'id': 123, 'name': 'CI', 'path': '.github/workflows/ci.yml',
               'event': 'pull_request', 'status': 'completed', 'conclusion': 'failure',
               'head_sha': 'a' * 40, 'run_attempt': 1}
        pr = {'state': 'open', 'head': {'sha': 'a' * 40}}
        names = set()
        for group in matrix['classes']:
            for shard in matrix['gate_shard']:
                row = {'classes': group, 'gate_shard': shard, 'job_names': matrix['job_names'][0]}
                if any(all(row.get(k) == v for k, v in excluded.items()) for excluded in exclusions):
                    continue
                name = expression(job_spec['name'], event, matrix=row)
                names.add(name)
                job = {'id': 456, 'run_id': 123, 'name': name,
                       'status': 'completed', 'conclusion': 'failure'}
                with self.subTest(name=name):
                    self.assertEqual(mod.admit(run, job, pr, 'a' * 40), group)
                    if group in ('gates', 'migration'):
                        with self.assertRaises(mod.Refusal):
                            mod.admit(run, job, {**pr, 'draft': True}, 'a' * 40)
        self.assertIn('Gates selftests shard 2 of 2', names)
        with self.assertRaises(mod.Refusal):
            mod.admit(run, {**job, 'name': 'ops/ci.sh --strict'}, pr, 'a' * 40)

    def test_rerun_tracks_a_workflow_job_rename(self):
        mod = load('ci-rerun')
        wf = workflow()
        metadata = wf['jobs']['classes']['strategy']['matrix']['job_names'][0]
        previous = metadata['gates_shard_2']['name']
        metadata['gates_shard_2']['name'] = 'Synthetic renamed gates shard'
        run = {'id': 123, 'name': 'CI', 'path': '.github/workflows/ci.yml',
               'event': 'pull_request', 'status': 'completed', 'conclusion': 'failure',
               'head_sha': 'a' * 40, 'run_attempt': 1}
        job = {'id': 456, 'run_id': 123, 'name': 'Synthetic renamed gates shard',
               'status': 'completed', 'conclusion': 'failure'}
        pr = {'state': 'open', 'head': {'sha': 'a' * 40}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github/workflows').mkdir(parents=True)
            (root / '.github/workflows/ci.yml').write_text(json.dumps(wf))
            (root / 'mcp-server').symlink_to(ROOT / 'mcp-server', target_is_directory=True)
            with patch.object(mod, 'ROOT', root):
                self.assertEqual(mod.admit(run, job, pr, 'a' * 40), 'gates')
                with self.assertRaises(mod.Refusal):
                    mod.admit(run, {**job, 'name': previous}, pr, 'a' * 40)

    def test_rerun_ignores_repository_and_mergeability_cache_changes(self):
        mod = load('ci-rerun')
        head = 'a' * 40
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml',
               'event':'pull_request', 'status':'completed', 'conclusion':'failure',
               'head_sha':head, 'run_attempt':1, 'repository':{'size':220185}}
        job = {'id':456, 'run_id':123, 'name':'ops/ci.sh --strict --only gates',
               'status':'completed', 'conclusion':'failure'}
        pr = {'number':1927, 'state':'open', 'head':{'sha':head},
              'base':{'sha':'b' * 40, 'repo':{'pushed_at':'before'}},
              'mergeable':None, 'mergeable_state':'unknown', 'rebaseable':None}
        current = ({**run, 'repository':{'size':232385}}, job,
                   {**pr, 'base':{'sha':'b' * 40, 'repo':{'pushed_at':'after'}},
                    'mergeable':True, 'mergeable_state':'blocked', 'rebaseable':False})
        remote = Mock(repo=mod.REPO)
        remote.snapshot.side_effect = [(run, job, pr), current]
        policy = json.loads((ROOT/'ops/config/platform-metering.v1.json').read_text())
        dispatch = Mock()
        mod.rerun(remote, 123, 456, head, policy, Mock(), dispatch)
        dispatch.assert_called_once_with(123, 456, 1, head)

    def test_rerun_refuses_changed_admission_binding(self):
        mod = load('ci-rerun')
        head = 'a' * 40
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml',
               'event':'pull_request', 'status':'completed', 'conclusion':'failure',
               'head_sha':head, 'run_attempt':1}
        job = {'id':456, 'run_id':123, 'name':'ops/ci.sh --strict --only gates',
               'status':'completed', 'conclusion':'failure'}
        pr = {'number':1927, 'state':'open', 'draft':False,
              'head':{'sha':head}, 'base':{'sha':'b' * 40}}
        policy = json.loads((ROOT/'ops/config/platform-metering.v1.json').read_text())
        for current in (
                ({**run, 'run_attempt':2}, job, pr),
                (run, {**job, 'id':457}, pr),
                (run, {**job, 'name':'ops/ci.sh --strict --only unit'}, pr),
                (run, {**job, 'conclusion':'cancelled'}, pr),
                (run, job, {**pr, 'number':1928}),
                (run, job, {**pr, 'base':{'sha':'c' * 40}}),
                (run, job, {**pr, 'head':{'sha':'c' * 40}}),
                (run, job, {**pr, 'state':'closed'}),
                (run, job, {**pr, 'draft':True})):
            with self.subTest(current=current):
                remote = Mock(repo=mod.REPO)
                remote.snapshot.side_effect = [(run, job, pr), current]
                dispatch = Mock()
                with self.assertRaises(mod.Refusal):
                    mod.rerun(remote, 123, 456, head, policy, Mock(), dispatch)
                dispatch.assert_not_called()

    def test_draft_reruns_keep_fast_feedback_and_refuse_expensive_jobs(self):
        mod = load('ci-rerun')
        head = 'a' * 40
        run = {'id': 123, 'name': 'CI', 'path': '.github/workflows/ci.yml',
               'event': 'pull_request', 'status': 'completed', 'conclusion': 'failure',
               'head_sha': head, 'run_attempt': 1}
        pr = {'state': 'open', 'draft': True, 'head': {'sha': head}}
        for group in mod.evidence.class_groups():
            job = {'id': 456, 'run_id': 123, 'name': 'ops/ci.sh --strict --only ' + group,
                   'status': 'completed', 'conclusion': 'failure'}
            with self.subTest(group=group):
                if group in ('gates', 'migration'):
                    with self.assertRaises(mod.Refusal):
                        mod.admit(run, job, pr, head)
                else:
                    self.assertEqual(mod.admit(run, job, pr, head), group)

    def test_draft_failure_ready_and_historical_retry(self):
        mod = load('ci-rerun')
        wf = workflow()
        head = 'a' * 40
        groups = mod.evidence.class_groups()
        fast_group = next(g for g in groups if 'unit' in g.split())
        draft = {'workflow': 'CI', 'event_name': 'pull_request', 'run_id': 123,
                 'event': {'action': 'synchronize', 'pull_request': {
                     'number': 7, 'draft': True, 'head': {'sha': head}}}}
        ready = {**draft, 'run_id': 124, 'event': {'action': 'ready_for_review',
                 'pull_request': {**draft['event']['pull_request'], 'draft': False}}}
        run = {'id': 123, 'name': 'CI', 'path': '.github/workflows/ci.yml',
               'event': 'pull_request', 'status': 'completed', 'conclusion': 'failure',
               'head_sha': head, 'run_attempt': 1}
        job = {'id': 456, 'run_id': 123, 'name': 'ops/ci.sh --strict --only ' + fast_group,
               'status': 'completed', 'conclusion': 'failure'}
        pr = {'state': 'open', 'draft': True, 'head': {'sha': head}}
        self.assertEqual(mod.admit(run, job, pr, head), fast_group)
        pr['draft'] = False
        self.assertEqual(mod.admit(run, job, pr, head), fast_group)
        retry = {**draft, 'run_attempt': 2}
        ready_group = expression(wf['concurrency']['group'], ready)
        for phase, event in (('draft failure', draft), ('ready', ready), ('historical retry', retry)):
            with self.subTest(phase=phase):
                self.assertTrue(expression(wf['jobs']['checks']['if'], event))
                excluded = expression(wf['jobs']['classes']['strategy']['matrix']['exclude'], event)
                jobs = [{'name': 'ops/ci.sh --strict --only ' + group, 'status': 'completed',
                         'conclusion': 'failure' if phase == 'draft failure' else 'success'}
                        for group in groups if {'classes': group} not in excluded]
                self.assertEqual(mod.evidence.verdict(jobs, groups)[0], 0 if phase == 'ready' else 1)
                if phase != 'ready':
                    self.assertNotEqual(expression(wf['concurrency']['group'], event), ready_group)
        self.assertEqual(expression(wf['concurrency']['group'], retry),
                         expression(wf['concurrency']['group'], draft))

    def test_rerun_repository_selection(self):
        mod = load('ci-rerun')
        job_groups = mod.evidence.class_jobs()
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml',
               'event':'pull_request', 'status':'completed', 'conclusion':'failure',
               'head_sha':'a'*40, 'run_attempt':1, 'pull_requests':[{'number':1665}]}
        job = {'id':456, 'run_id':123, 'name':'ops/ci.sh --strict --only gates',
               'status':'completed', 'conclusion':'failure'}
        pr = {'state':'open', 'head':{'sha':'a'*40}}
        app_run = {**run, 'name':'e2e', 'path':'.github/workflows/e2e.yml'}
        app_job = {**job, 'name':'journeys'}
        sibling = {'id':789, 'name':'test', 'status':'completed', 'conclusion':'success'}
        with tempfile.TemporaryDirectory() as temp:
            for repo, options in (
                    ('DoctorCRE/carr-system', []),
                    ('DoctorCRE/carr-system', ['--repo', 'DoctorCRE/carr-system']),
                    ('jbookout/doctorcre-app', ['--repo', 'jbookout/doctorcre-app'])):
                with self.subTest(repo=repo, options=options), tempfile.TemporaryDirectory(dir=temp) as attempt:
                    candidate = 'a'*40 if repo == mod.REPO else 'b'*40
                    shaped_run, shaped_job = (run, job) if repo == mod.REPO else (app_run, app_job)
                    shaped_run['head_sha'] = candidate
                    pr['head']['sha'] = candidate
                    responses = {'actions/runs/123':shaped_run, 'actions/jobs/456':shaped_job, 'pulls/1665':pr,
                                 'actions/runs/123/jobs?filter=latest&per_page=100':{'jobs':[shaped_job]},
                                 f'commits/{candidate}/check-runs?per_page=100':
                                     {'check_runs':[sibling, {**shaped_job, 'name':'journeys'}]}}
                    calls = []
                    def execute(command, **kwargs):
                        calls.append(command)
                        if command[:2] == ['gh', 'api']:
                            prefix = 'repos/' + repo + '/'
                            self.assertTrue(command[2].startswith(prefix))
                            return Mock(stdout=json.dumps(responses[command[2][len(prefix):]]))
                        self.assertEqual(command, ['gh','run','rerun','123','--job','456','--repo',repo])
                        return Mock(returncode=0)
                    def git_output(command, **kwargs):
                        return 'a'*40 if command[1] == 'rev-parse' else ''
                    with patch.object(sys, 'argv', ['ci-rerun.py','123','--job','456',*options]), \
                            patch.object(mod.tempfile, 'gettempdir', return_value=attempt), \
                            patch.object(mod.subprocess, 'check_output', side_effect=git_output), \
                            patch.object(mod.subprocess, 'run', side_effect=execute), \
                            patch.object(mod.evidence, 'class_jobs', return_value=job_groups), \
                            patch.object(mod, 'local_checks') as checks:
                        self.assertEqual(mod.main(), 0)
                    if repo == mod.REPO:
                        checks.assert_called_once_with('gates')
                        self.assertEqual(len(calls), 9)
                    else:
                        checks.assert_not_called()
                        self.assertEqual(len(calls), 10)
                    calls.clear()
                    with patch.object(sys, 'argv', ['ci-rerun.py','123','--job','456',*options]), \
                            patch.object(mod.tempfile, 'gettempdir', return_value=attempt), \
                            patch.object(mod.subprocess, 'check_output', side_effect=git_output), \
                            patch.object(mod.subprocess, 'run', side_effect=execute), \
                            patch.object(mod.evidence, 'class_jobs', return_value=job_groups), \
                            patch.object(mod, 'local_checks'):
                        self.assertEqual(mod.main(), 1)
                    self.assertTrue(all(command[:2] == ['gh', 'api'] for command in calls))
                    receipts = list(Path(attempt).rglob('123-1.json'))
                    self.assertEqual(len(receipts), 1)
                    self.assertEqual(json.loads(receipts[0].read_text())['repo'], repo)
                    self.assertEqual(json.loads(receipts[0].read_text())['head_sha'], candidate)
                    if repo == 'DoctorCRE/carr-system':
                        self.assertEqual(receipts[0].parent, Path(attempt) / 'carr-ci-rerun')
                    else:
                        self.assertEqual(receipts[0].parent.name, repo.replace('/', '--'))

    def test_rerun_head_mismatch_refused(self):
        mod = load('ci-rerun')
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml',
               'event':'pull_request', 'status':'completed', 'conclusion':'failure',
               'head_sha':'b'*40, 'run_attempt':1, 'pull_requests':[{'number':1665}]}
        job = {'id':456, 'run_id':123, 'name':'ops/ci.sh --strict --only gates',
               'status':'completed', 'conclusion':'failure'}
        for repo, pr_head in ((mod.REPO, 'b'*40),
                              ('jbookout/doctorcre-app', 'c'*40),
                              ('jbookout/doctorcre-app', None),
                              ('jbookout/software-factory', 'c'*40)):
            with self.subTest(repo=repo, pr_head=pr_head), tempfile.TemporaryDirectory() as temp:
                remote = Mock(repo=repo)
                shaped = ((run, job) if repo != 'jbookout/doctorcre-app' else
                          ({**run, 'name':'e2e', 'path':'.github/workflows/e2e.yml'}, {**job, 'name':'journeys'}))
                remote.snapshot.return_value = (*shaped, {'state':'open', 'head':{'sha':pr_head}})
                def git_output(command, **kwargs):
                    return 'a'*40 if command[1] == 'rev-parse' else ''
                with patch.object(sys, 'argv', ['ci-rerun.py','123','--job','456','--repo',repo]), \
                        patch.object(mod.tempfile, 'gettempdir', return_value=temp), \
                        patch.object(mod, 'GitHub', return_value=remote), \
                        patch.object(mod.subprocess, 'check_output', side_effect=git_output), \
                        patch.object(mod.subprocess, 'run') as dispatch, \
                        patch.object(mod, 'authorize_metered_execution') as meter, \
                        patch.object(mod, 'local_checks') as checks:
                    self.assertEqual(mod.main(), 1)
                checks.assert_not_called()
                meter.assert_not_called()
                dispatch.assert_not_called()
                self.assertEqual(list(Path(temp).rglob('123-1.json')), [])

    def test_rerun_remote_head_metering_and_recheck(self):
        mod = load('ci-rerun')
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml',
               'event':'pull_request', 'status':'completed', 'conclusion':'failure',
               'head_sha':'b'*40, 'run_attempt':1}
        job = {'id':456, 'run_id':123, 'name':'ops/ci.sh --strict --only gates',
               'status':'completed', 'conclusion':'failure'}
        pr = {'state':'open', 'head':{'sha':'b'*40}}
        remote = Mock(repo=mod.REPO)
        remote.snapshot.return_value = (run, job, pr)
        policy = json.loads((ROOT/'ops/config/platform-metering.v1.json').read_text())
        checks, dispatch = Mock(), Mock()
        with patch.object(mod, 'authorize_metered_execution',
                          wraps=mod.authorize_metered_execution) as meter:
            mod.rerun(remote, 123, 456, None, policy, checks, dispatch)
        checks.assert_called_once_with('gates')
        meter.assert_called_once_with(policy, 'github-actions-remote-ci',
                                      {'candidate_sha':'b'*40, 'local_checks_green':True})
        dispatch.assert_called_once_with(123, 456, 1, 'b'*40)
        for current in ((run, job, {**pr, 'head':{'sha':'c'*40}}),
                        ({**run, 'head_sha':'c'*40}, job, {**pr, 'head':{'sha':'c'*40}})):
            with self.subTest(current=current):
                dispatch.reset_mock()
                remote.snapshot.side_effect = [(run, job, pr), current]
                with self.assertRaises(mod.Refusal):
                    mod.rerun(remote, 123, 456, None, policy, checks, dispatch)
                dispatch.assert_not_called()

    def test_rerun_app_job_shapes(self):
        mod = load('ci-rerun')
        repo, head = 'jbookout/doctorcre-app', 'b'*40
        run = {'id':123, 'name':'e2e', 'path':'.github/workflows/e2e.yml', 'event':'pull_request',
               'status':'completed', 'conclusion':'failure', 'head_sha':head, 'run_attempt':1}
        job = {'id':456, 'run_id':123, 'name':'journeys', 'status':'completed', 'conclusion':'failure'}
        pr = {'state':'open', 'head':{'sha':head}}
        self.assertEqual(mod.admit(run, job, pr, head, repo), {'head':head, 'job':'journeys'})
        for shaped_run, shaped_job in (
                (run, {**job, 'name':'other'}),
                (run, {**job, 'name':'test'}),
                ({**run, 'name':'CI', 'path':'.github/workflows/ci.yml'}, {**job, 'name':'preview'}),
                ({**run, 'name':'CI', 'path':'.github/workflows/ci.yml'}, {**job, 'name':'ops/ci.sh --strict --only gates'}),
                ({**run, 'path':'.github/workflows/other.yml'}, job)):
            with self.subTest(run=shaped_run['path'], job=shaped_job['name']), self.assertRaises(mod.Refusal):
                mod.admit(shaped_run, shaped_job, pr, head, repo)
        with self.assertRaises(mod.Refusal):
            mod.admit(run, job, pr, head, 'jbookout/software-factory')
        with self.assertRaises(mod.Refusal):
            mod.admit(run, job, pr, head)
        group = {'head':head, 'job':'journeys'}
        def checks_at(*runs):
            remote = Mock(repo=repo)
            remote.api.return_value = {'check_runs':list(runs)}
            return remote
        green = {'id':10, 'name':'test', 'status':'completed', 'conclusion':'success'}
        failed_self = {'id':11, 'name':'journeys', 'status':'completed', 'conclusion':'failure'}
        mod.hosted_checks(checks_at(green, failed_self), group)
        mod.hosted_checks(checks_at({**green, 'conclusion':'failure'}, {**green, 'id':12}, failed_self), group)
        for runs in ((failed_self,), ({**green, 'conclusion':'failure'}, failed_self),
                     ({**green, 'status':'in_progress', 'conclusion':None}, failed_self)):
            with self.subTest(runs=runs), self.assertRaises(mod.Refusal):
                mod.hosted_checks(checks_at(*runs), group)
        remote = checks_at({**green, 'conclusion':'failure'}, failed_self)
        remote.snapshot.return_value = (run, job, pr)
        policy = json.loads((ROOT/'ops/config/platform-metering.v1.json').read_text())
        dispatch = Mock()
        with self.assertRaises(mod.Refusal):
            mod.rerun(remote, 123, 456, None, policy, lambda g: mod.hosted_checks(remote, g), dispatch)
        dispatch.assert_not_called()
        remote = checks_at(green, failed_self)
        remote.snapshot.return_value = (run, job, pr)
        mod.rerun(remote, 123, 456, None, policy, lambda g: mod.hosted_checks(remote, g), dispatch)
        dispatch.assert_called_once_with(123, 456, 1, head)

    def test_rerun_app_ci_test_job(self):
        mod = load('ci-rerun')
        repo, head = 'jbookout/doctorcre-app', 'b'*40
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml', 'event':'pull_request',
               'status':'completed', 'conclusion':'failure', 'head_sha':head, 'run_attempt':1}
        job = {'id':456, 'run_id':123, 'name':'test', 'status':'completed', 'conclusion':'failure'}
        pr = {'state':'open', 'head':{'sha':head}}
        self.assertEqual(mod.admit(run, job, pr, head, repo), {'head':head, 'job':'test'})
        for shaped_run, shaped_job, shaped_repo in (
                (run, {**job, 'name':'preview'}, repo),
                ({**run, 'path':'.github/workflows/other.yml'}, job, repo),
                (run, job, 'jbookout/software-factory')):
            with self.subTest(run=shaped_run['path'], job=shaped_job['name'], repo=shaped_repo), \
                    self.assertRaises(mod.Refusal):
                mod.admit(shaped_run, shaped_job, pr, head, shaped_repo)

    def test_rerun_unknown_repository_refused_before_validation(self):
        mod = load('ci-rerun')
        for repo in ('jbookout/other', 'someone/carr-system', 'DoctorCRE/carr-system/extra'):
            with self.subTest(repo=repo), \
                    patch.object(sys, 'argv', ['ci-rerun.py','123','--job','456','--repo',repo]), \
                    patch.object(mod.subprocess, 'check_output') as git, \
                    patch.object(mod.subprocess, 'run') as execute:
                with self.assertRaises(SystemExit) as raised:
                    mod.main()
                self.assertEqual(raised.exception.code, 2)
                git.assert_not_called()
                execute.assert_not_called()


if __name__=='__main__': unittest.main()
