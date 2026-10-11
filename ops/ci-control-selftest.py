#!/usr/bin/env python3
"""Exercise hosted event isolation, class conclusions and rerun admission."""
import importlib.util
import json
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


def workflow():
    return json.loads(subprocess.check_output(['node', '-e',
        "const y=require('js-yaml'),fs=require('fs');console.log(JSON.stringify(y.load(fs.readFileSync(process.argv[1],'utf8'))))",
        str(ROOT / '.github/workflows/ci.yml')], cwd=ROOT / 'mcp-server', text=True))


def expression(value, github):
    # Evaluate the workflow's actual expression with GitHub's missing-property
    # semantics; no reimplementation of the intended event predicate.
    source = '''const get=(o,p)=>p.split('.').reduce((v,k)=>v&&v[k],o)||'';
    const github=JSON.parse(process.argv[1]);const always=()=>true;
    const e=process.argv[2].replace(/github(?:\\.[a-zA-Z_]+)+/g,p=>JSON.stringify(get({github},p)));
    console.log(JSON.stringify(eval(e)));'''
    import re
    def evaluate(match):
        return json.loads(subprocess.check_output(['node', '-e', source,
            json.dumps(github), match.group(1)], text=True))
    if value.startswith('${{') and value.endswith('}}') and value.count('${{') == 1:
        return evaluate(re.fullmatch(r'\$\{\{(.*?)\}\}', value))
    return re.sub(r'\$\{\{(.*?)\}\}', lambda m: str(evaluate(m)), value)


class Controls(unittest.TestCase):
    def test_events(self):
        wf = workflow()
        def event(action, sha='a', base='main', changes=None, run=1):
            return {'workflow':'CI','event_name':'pull_request','run_id':run,
                    'event':{'action':action,'pull_request':{'number':1665,'head':{'sha':sha},'base':{'ref':base}},'changes':changes or {}}}
        def state(e):
            return (expression(wf['concurrency']['group'], e),
                    expression(wf['concurrency']['cancel-in-progress'], e),
                    bool(expression(wf['jobs']['classes'].get('if', '${{ true }}'),e)),
                    bool(expression(wf['jobs']['checks']['if'], e)))
        old = state(event('opened'))
        new = state(event('synchronize',sha='b',run=2))
        self.assertEqual(old[0],new[0]); self.assertTrue(new[1])
        for changes in ({'title':{'from':'old'}},{'body':{'from':'old'}},{}):
            edit = state(event('edited',changes=changes,run=3))
            self.assertNotEqual(edit[0],old[0])
            self.assertEqual(edit[1:],(False,False,False))
            self.assertNotEqual(expression(wf['jobs']['checks']['name'],event('edited',changes=changes,run=3)), 'ops/ci.sh --strict')
        changed = state(event('edited',base='release',changes={'base':{'ref':{'from':'main'}}},run=4))
        self.assertNotEqual(changed[0],old[0]); self.assertEqual(changed[1:],(False,True,True))
        for name in ('schedule','workflow_dispatch'):
            a={'workflow':'CI','event_name':name,'event':{},'run_id':10}
            b={**a,'run_id':11}
            self.assertNotEqual(state(a)[0],state(b)[0]); self.assertEqual(state(a)[1:],(False,True,True))

    def test_verdict(self):
        mod = load('ci-evidence')
        groups=mod.class_groups()
        jobs=[{'name':'ops/ci.sh --strict --only '+g,'status':'completed','conclusion':'success'} for g in groups]
        self.assertEqual(mod.verdict(jobs,groups)[0],0)
        jobs[1]['conclusion']='failure';jobs[2]['conclusion']='cancelled'
        rc, lines=mod.verdict(jobs,groups)
        self.assertNotEqual(rc,0)
        self.assertIn('gates: FAILED',lines);self.assertIn('replay: CANCELLED',lines)
        for c in groups[0].split(): self.assertIn(c+': PASSED',lines)
        self.assertNotEqual(mod.verdict(jobs[:-1],groups)[0],0)
        self.assertIn('migration: MISSING',mod.verdict(jobs[:-1],groups)[1])
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
        remote=Mock();remote.snapshot.return_value=(run,job,pr)
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

    def test_rerun_repository_selection(self):
        mod = load('ci-rerun')
        run = {'id':123, 'name':'CI', 'path':'.github/workflows/ci.yml',
               'event':'pull_request', 'status':'completed', 'conclusion':'failure',
               'head_sha':'a'*40, 'run_attempt':1, 'pull_requests':[{'number':1665}]}
        job = {'id':456, 'run_id':123, 'name':'ops/ci.sh --strict --only gates',
               'status':'completed', 'conclusion':'failure'}
        pr = {'state':'open', 'head':{'sha':'a'*40}}
        responses = {'actions/runs/123':run, 'actions/jobs/456':job, 'pulls/1665':pr,
                     'actions/runs/123/jobs?filter=latest&per_page=100':{'jobs':[job]}}
        with tempfile.TemporaryDirectory() as temp:
            for repo, options in (
                    ('jbookout/carr-system', []),
                    ('jbookout/carr-system', ['--repo', 'jbookout/carr-system']),
                    ('jbookout/doctorcre-app', ['--repo', 'jbookout/doctorcre-app']),
                    ('jbookout/software-factory', ['--repo', 'jbookout/software-factory'])):
                with self.subTest(repo=repo, options=options), tempfile.TemporaryDirectory(dir=temp) as attempt:
                    candidate = 'a'*40 if repo == mod.REPO else 'b'*40
                    run['head_sha'] = candidate
                    pr['head']['sha'] = candidate
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
                            patch.object(mod, 'local_checks') as checks:
                        self.assertEqual(mod.main(), 0)
                    checks.assert_called_once_with('gates')
                    self.assertEqual(len(calls), 9)
                    calls.clear()
                    with patch.object(sys, 'argv', ['ci-rerun.py','123','--job','456',*options]), \
                            patch.object(mod.tempfile, 'gettempdir', return_value=attempt), \
                            patch.object(mod.subprocess, 'check_output', side_effect=git_output), \
                            patch.object(mod.subprocess, 'run', side_effect=execute), \
                            patch.object(mod, 'local_checks'):
                        self.assertEqual(mod.main(), 1)
                    self.assertTrue(all(command[:2] == ['gh', 'api'] for command in calls))
                    receipts = list(Path(attempt).rglob('123-1.json'))
                    self.assertEqual(len(receipts), 1)
                    self.assertEqual(json.loads(receipts[0].read_text())['repo'], repo)
                    self.assertEqual(json.loads(receipts[0].read_text())['head_sha'], candidate)
                    if repo == 'jbookout/carr-system':
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
                remote = Mock()
                remote.snapshot.return_value = (run, job, {'state':'open', 'head':{'sha':pr_head}})
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
        remote = Mock()
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

    def test_rerun_unknown_repository_refused_before_validation(self):
        mod = load('ci-rerun')
        for repo in ('jbookout/other', 'someone/carr-system', 'jbookout/carr-system/extra'):
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
