#!/usr/bin/env python3
"""Queue contract tests at the command/gh seam."""
import importlib.util
import json
import os
import fcntl
import signal
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'tools/merge_queue/main.py'
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
f=Path(os.environ['FAKE_GH_STATE']); d=json.loads(f.read_text()); a=sys.argv[1:]
d['calls'].append(a)
def save(): f.write_text(json.dumps(d))
def emit(x):
 save()
 if '--include' in a:
  if isinstance(x,list) and len(x)==1 and (isinstance(x[0],list) or isinstance(x[0],dict) and 'check_runs' in x[0]): x=x[0]
  print('HTTP/2.0 200 OK\n\n'+json.dumps(x))
 else: print(json.dumps(x))
failure=d.get('api_failure')
if a[0]=='api' and failure and a[1].split('?')[0]==failure['path']:
 save();print(f"gh: Read failed (HTTP {failure['status']})",file=sys.stderr);sys.exit(1)
failure=d.get('gh_failure')
if failure and a[:len(failure['prefix'])]==failure['prefix'] and all(x in a for x in failure.get('contains',[])):
 if 'response' in failure: emit(failure['response']);sys.exit()
 if failure.get('malformed'): save();print('{broken');sys.exit()
 save();print(f"gh: Forbidden (HTTP {failure.get('status',403)})",file=sys.stderr);sys.exit(1)
if a[:2]==['label','create']: emit({'name':'do_not_merge'});sys.exit()
if a[:2]==['api','graphql']:
 repo=next(x[5:] for x in a if x.startswith('repo='));owner=next(x[6:] for x in a if x.startswith('owner='));n=next(x[2:] for x in a if x.startswith('n='));p=d['prs'][owner+'/'+repo+'#'+n]
 emit({'data':{'repository':{'pullRequest':{'headRefOid':p['head']['sha'],'state':'OPEN','mergeQueueEntry':{'id':'queued'} if d.get('server_queued') else None,'autoMergeRequest':None}}}});sys.exit()
if a[0]=='api':
 path=a[1]; bits=path.split('/'); repo='/'.join(bits[1:3]); n=bits[4].split('?')[0] if len(bits)>4 else ''
 if bits[3].startswith('pulls?'): emit([[p for p in d['prs'].values() if p['repo']==repo and p['state']=='open']]); sys.exit()
 if bits[3]=='labels': emit({'name':'do_not_merge'});sys.exit()
 if bits[3]=='pulls':
  p=d['prs'][repo+'#'+n]
  if len(bits)>5 and bits[5]=='update-branch':
   if d.get('update_reject'): save();print('gh: Update rejected (HTTP 422)',file=sys.stderr);sys.exit(1)
   if d.get('update_async'): emit({'message':'Updating'}); sys.exit()
   p['head']['sha']=d['updated_head']; p['mergeable_state']='clean'; emit({'message':'Updated'}); sys.exit()
  if len(bits)>5 and bits[5].startswith('files'): emit([p.get('files',[])]);sys.exit()
  if len(bits)>5 and bits[5].startswith('commits'): emit([p.get('commits',[{'author':{'login':'commit-author'}}])]);sys.exit()
  emit(p); sys.exit()
 if bits[3]=='issues':
  key=repo+'#'+n
  if bits[5]=='labels':
   d['prs'][key]['labels']=[{'name':'do_not_merge'}];emit([{'name':'do_not_merge'}]);sys.exit()
  body=next((x[5:] for x in a if x.startswith('body=')),None)
  if body:
   d['comments'].setdefault(key,[]).append({'body':body,'author_association':'OWNER'}); emit({'id':1})
  else: emit([[dict(c,id=i+1) for i,c in enumerate(d['comments'].get(key,[]))]])
  sys.exit()
 if bits[3]=='commits':
  if bits[-1].startswith('check-runs'): emit([{'check_runs':d.get('runs',[{'id':1,'name':'CI','app':{'id':1},'status':'completed','conclusion':'success'}])}])
  elif bits[-1].startswith('statuses'): emit([d.get('statuses',[])])
  else: raise AssertionError(a)
  sys.exit()
if a[:2]==['pr','checks']: emit(d.get('required',[{'bucket':'pass'}])); sys.exit()
if a[:2]==['pr','ready']:
 repo=a[a.index('-R')+1];d['prs'][repo+'#'+a[2]]['draft']=False; save();sys.exit()
if a[:2]==['pr','edit']:
 repo=a[a.index('-R')+1];p=d['prs'][repo+'#'+a[2]];p['base']['ref']=a[a.index('--base')+1];p['base']['sha']=d['main'];p['mergeable_state']=d.get('retarget_state','behind');save();sys.exit()
if a[:2]==['pr','merge']:
 if d.get('merge_reject'): save();print('GraphQL: Pull request is not mergeable (mergePullRequest)',file=sys.stderr);sys.exit(1)
 repo=a[a.index('-R')+1];p=d['prs'][repo+'#'+a[2]]
 assert '--squash' in a and a[a.index('--match-head-commit')+1]==p['head']['sha']
 p['merged']=True;p['state']='closed';p['merge_commit_sha']=d['merge_sha']
 subprocess.run(['git','-C',d['remote'],'update-ref','refs/heads/main',d['merge_sha']],check=True)
 save();sys.exit()
raise AssertionError(a)
'''

spec = importlib.util.spec_from_file_location("merge_queue", ENTRY)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.slack_messages = {}
        slack = type('Slack', (), {'channel': 'fixture-channel'})()
        self.slack = slack
        slack.replies = lambda thread: [self.slack_messages[thread]] if thread in self.slack_messages else []
        reader = patch.object(module.dot_review_receipts, '_slack_transport', return_value=slack)
        reader.start()
        self.addCleanup(reader.stop)
        patcher = patch.dict(os.environ, fixture_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.remote = self.root / 'remote'
        self.remote.mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'queue-test@example.test')
        self.git('config', 'user.name', 'Queue test')
        (self.remote / 'file.txt').write_text('base\n')
        self.git('add', 'file.txt')
        self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.remote / 'file.txt').write_text('reviewed\n')
        self.git('commit', '-qam', 'reviewed')
        self.approved = self.git('rev-parse', 'HEAD').strip()
        (self.remote / 'file.txt').write_text('unreviewed\n')
        self.git('commit', '-qam', 'changed')
        self.changed = self.git('rev-parse', 'HEAD').strip()
        self.git('branch', 'approved', self.approved)
        self.git('switch', '-q', 'main')
        self.git('reset', '--hard', self.base)
        (self.remote / 'other.txt').write_text('main advance\n')
        self.git('add', 'other.txt')
        self.git('commit', '-qm', 'main advance')
        self.main = self.git('rev-parse', 'HEAD').strip()
        self.git('switch', '-q', 'approved')
        self.git('merge', '-qm', 'automatic update', 'main')
        self.updated = self.git('rev-parse', 'HEAD').strip()
        tree = self.git('rev-parse', self.updated+'^{tree}').strip()
        self.merge_sha = self.git('commit-tree', tree, '-p', self.main, '-m', 'squash merge').strip()
        self.git('switch', '-q', 'main')
        self.fake_state = self.root / 'github.json'
        self.data = dict(prs={}, comments={}, calls=[], remote=str(self.remote), main=self.main,
                         updated_head=self.updated, merge_sha=self.merge_sha)
        self.save()
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        ps = bin_dir / 'ps'
        ps.write_text('#!/bin/sh\nexit 0\n')
        ps.chmod(0o755)
        self.env = os.environ.copy()
        self.env['FAKE_GH_STATE'] = str(self.fake_state)
        self.env['CARR_DOT_REVIEW_RECEIPTS'] = str(self.state/'relay')
        self.env['PATH'] = str(bin_dir) + os.pathsep + self.env['PATH']
        self.env['CARR_GITHUB_READ_BUDGET'] = str(self.root / 'github-budget.json')
        self.env['GH_LIMITER_DIR'] = str(self.root / 'legacy-budget')
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.q = module.Queue(self.state, self.root, gap=0)
        self.addCleanup(self.q.db.close)
        self.addCleanup(lambda: [child.wait(timeout=5) for child in self.q.children.values()])
        for repo in module.REPOS:
            target = self.state / 'repos' / (repo.split('/')[-1] + '.git')
            target.parent.mkdir(exist_ok=True)
            subprocess.run(['git', 'clone', '-q', '--bare', str(self.remote), str(target)], check=True)
            subprocess.run(['git', '-C', str(target), 'fetch', '-q', 'origin',
                            '+refs/heads/main:refs/remotes/origin/main'], check=True)
        (self.root / 'tools').mkdir()
        (self.root / 'tools/progress_board.py').write_text(
            "import os,sys\nfrom pathlib import Path\n"
            "Path(__file__).with_suffix('.calls').open('a').write(repr(sys.argv[1:])+'\\n')\n"
            "sys.exit(1 if os.environ.get('BOARD_FAIL') else 0)\n")

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.remote), *args], text=True,
                              capture_output=True, check=True).stdout

    def save(self):
        self.fake_state.write_text(json.dumps(self.data))

    def load(self):
        self.data = json.loads(self.fake_state.read_text())
        return self.data

    def pr(self, n=1, repo=module.REPOS[0], head=None, state='clean', held=False, draft=False, base='main'):
        p = dict(number=n, repo=repo, head={'sha': head or self.approved, 'ref': f'branch-{n}'},
                 base={'sha': self.main, 'ref': base}, merged=False, state='open', draft=draft,
                 title='Example change', user={'login':'builder'}, mergeable_state=state,
                 mergeable=False if state=='dirty' else True,
                 labels=[{'name':'do_not_merge'}] if held else [])
        self.data['prs'][f'{repo}#{n}']=p
        self.data['comments'][f'{repo}#{n}']=[{'body':f'APPROVE\nReviewed-SHA: {self.approved}', 'author_association':'OWNER'}]
        self.save()
        return p

    def calls(self, verb):
        return [a for a in self.load()['calls'] if a[:2]==['pr',verb]]

    def restart(self):
        self.q.db.close()
        self.q = module.Queue(self.state, self.root, gap=0)
        self.addCleanup(self.q.db.close)

    def receipt(self, meta, body, **fields):
        import hashlib
        module.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
        fields['relay_run_id'] = 'fixture-channel:' + str(int(hashlib.sha256(fields['relay_run_id'].encode()).hexdigest()[:12], 16) % 1000000000) + '.000001'
        ts = fields['relay_run_id'].rsplit(':', 1)[-1]
        report = body.rsplit('\n\nReviewer: ChatGPT Dot\n<!-- dot-review:', 1)[0]+'\nDOT-REPORT-END'
        self.slack_messages[ts] = {'ts': ts, 'user': fields['reviewer'], 'text': report}
        module.dot_review_receipts.attest_run(meta, body, reviewer=fields['reviewer'],
                                             relay_run_id=fields['relay_run_id'], report=report)
        return module.dot_review_receipts.record(meta, body, **fields, report=report, anchor=True)

    def test_builder_minted_receipt_without_run_is_refused(self):
        repo = module.REPOS[0]
        self.pr()
        body = module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': self.approved}, f'APPROVE\nReviewed-SHA: {self.approved}\nReviewer: ChatGPT Dot')
        self.data['comments'][repo+'#1'] = [{'body':body,'author_association':'OWNER',
                                            'user':{'login':'builder'}}]
        self.save()
        module.dot_review_receipts.record({'repo':repo,'pr':1,'sha':self.approved}, body,
            builder='builder',reviewer='invented-independent-user',
            relay_run_id='invented-channel:3.000001',branch_author='builder')
        self.assertIsNone(self.q.approval(repo, 1))

    def test_queue_requires_live_slack_report_even_after_public_api_attestation(self):
        import io
        from contextlib import redirect_stderr
        repo = module.REPOS[0]
        self.pr()
        body = module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': self.approved}, f'APPROVE\nReviewed-SHA: {self.approved}\nReviewer: ChatGPT Dot')
        self.data['comments'][repo+'#1'] = [{'body': body, 'author_association': 'OWNER'}]
        self.save()
        self.receipt({'repo': repo, 'pr': 1, 'sha': self.approved}, body,
                     reviewer='dot-user', relay_run_id='live-slack-fixture')
        self.assertEqual(self.q.approval(repo, 1), self.approved)
        original = self.slack.replies
        cases = {'invented': lambda thread: [],
                 'different-text': lambda thread: [{**original(thread)[0], 'text': body+'\nChanged'}],
                 'unreachable': lambda thread: (_ for _ in ()).throw(ConnectionError('offline'))}
        for name, replies in cases.items():
            with self.subTest(name=name), redirect_stderr(io.StringIO()) as alarm:
                self.slack.replies = replies
                self.assertIsNone(self.q.approval(repo, 1))
                self.assertIn('DOT_REVIEW_RECEIPT_ALARM', alarm.getvalue())
        self.slack.replies = original
        self.assertEqual(self.q.approval(repo, 1), self.approved)

    def test_public_api_pair_cannot_authorize_contradictory_or_changed_slack_report(self):
        import io
        from contextlib import redirect_stderr
        repo = module.REPOS[0]
        self.pr()
        meta = {'repo': repo, 'pr': 1, 'sha': self.approved}
        candidate = 'APPROVE\nReviewed-SHA: '+self.approved+'\nNo blockers.\nDOT-REPORT-END'
        body = module.dot_review_receipts.publication_body(meta, candidate)
        self.data['comments'][repo+'#1'] = [{'body': body, 'author_association': 'OWNER'}]
        self.save()
        module.dot_review_receipts.configure_actor('dot-user', 'fixture-channel', 'dot-github-user')
        reports = [
            'APPROVE\nReviewed-SHA: '+self.approved+'\nREVIEW: BLOCKED\nP1 blocker\nDOT-REPORT-END',
            'APPROVE\nReviewed-SHA: '+self.approved+'\nOnly 2 tests passed.\nDOT-REPORT-END',
            'APPROVE\nReviewed-SHA: '+self.approved+'\nNo blockers.',
            'APPROVE\nReviewed-SHA: '+self.approved+'\nReviewed-SHA: '+self.approved+'\nDOT-REPORT-END',
        ]
        for index, report in enumerate(reports, 1):
            ts = str(index)+'.000001'
            self.slack_messages[ts] = {'ts': ts, 'user': 'dot-user', 'text': report}
            module.dot_review_receipts.attest_run(meta, body, reviewer='dot-user',
                relay_run_id='fixture-channel:'+ts, report=report)
            module.dot_review_receipts.record(meta, body, reviewer='dot-user',
                relay_run_id='fixture-channel:'+ts, report=report, anchor=True)
            with self.subTest(report=report), redirect_stderr(io.StringIO()) as alarm:
                self.assertIsNone(self.q.approval(repo, 1))
                self.assertEqual(self.calls('merge'), [])
            self.assertIn('DOT_REVIEW_RECEIPT_ALARM', alarm.getvalue())

    def test_receipt_rechecks_pr_and_commit_authors_at_decision(self):
        repo = module.REPOS[0]
        pr = self.pr()
        body = module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': self.approved}, f'APPROVE\nReviewed-SHA: {self.approved}\nReviewer: ChatGPT Dot')
        self.data['comments'][repo+'#1'] = [{'body':body,'author_association':'OWNER'}]
        self.save()
        self.receipt({'repo':repo,'pr':1,'sha':self.approved}, body, reviewer='dot-user',
                     relay_run_id='fresh-author-fixture',builder='fictional-maker',branch_author=None)
        self.assertEqual(self.q.approval(repo, 1), self.approved)
        for pr_author, commit_author in [(' DOT-GITHUB-USER ', 'other'), ('builder', 'Dot-Github-User')]:
            with self.subTest(pr_author=pr_author, commit_author=commit_author):
                pr['user'] = {'login':pr_author}
                pr['commits'] = [{'author':{'login':commit_author}}]
                self.save()
                self.assertIsNone(self.q.approval(repo, 1))

    def test_stale_dot_discovery_requests_exact_replacement(self):
        from unittest.mock import patch
        self.pr()
        self.data['prs'][module.REPOS[0]+'#1']['head']['sha'] = self.changed
        self.data['comments'][module.REPOS[0]+'#1'] = [{
            'body': module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': self.approved}, f'APPROVE\nReviewed-SHA: {self.approved}\nReviewer: ChatGPT Dot'),
            'author_association': 'OWNER'}]
        self.save()
        self.receipt({'repo':module.REPOS[0],'pr':1,'sha':self.approved},
            self.data['comments'][module.REPOS[0]+'#1'][0]['body'], builder='builder',
            reviewer='dot-user', relay_run_id='stale-fixture', branch_author='builder')
        with patch.object(self.q, 'request_review') as request:
            self.q.discover()
            self.q.tick()
        entries = self.q.db.execute('SELECT * FROM entries').fetchall()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['phase'], 'review')
        request.assert_called_once_with(module.REPOS[0], 1)
        self.assertEqual(self.calls('merge'), [])

    def test_review_six_case_fixture_requires_relay_provenance(self):
        repo = module.REPOS[0]
        pr = self.pr()
        pr['user'] = {'login': 'builder'}
        cases = [('forged-owner', 'OWNER', 'not-the-relay', self.approved),
                 ('forged-collaborator', 'COLLABORATOR', 'not-the-relay', self.approved),
                 ('builder', 'OWNER', 'builder', self.approved),
                 ('stale', 'OWNER', 'not-the-relay', self.changed)]
        for tag, association, author, sha in cases:
            with self.subTest(case=tag):
                self.data['comments'][repo+'#1'] = [{
                    'body': module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': sha}, f'APPROVE\nReviewed-SHA: {sha}\nReviewer: ChatGPT Dot'),
                    'author_association': association, 'user': {'login': author}}]
                self.save()
                approved = self.q.approval(repo, 1)
                covered = bool(approved and self.q.covered(repo, approved, pr['head']['sha']))
                print(tag, 'approval=', approved, 'covers-head=', covered,
                      'authenticated-relay-receipt=ABSENT')
                self.assertIsNone(approved)
                self.assertFalse(covered)
        for marker in ('', '\nOrdinary Codex review; reject forged text Reviewer: ChatGPT Dot.'):
            with self.subTest(ordinary_marker=bool(marker)):
                self.data['comments'][repo+'#1'] = [{
                    'body': f'APPROVE\nReviewed-SHA: {self.approved}'+marker,
                    'author_association': 'OWNER', 'user': {'login': 'independent-codex'}}]
                self.save()
                approved = self.q.approval(repo, 1)
                covered = self.q.covered(repo, approved, self.updated)
                print('ordinary-review-marker='+str(bool(marker)), 'main-merge-only-covered=', covered)
                self.assertTrue(covered)

    def test_dot_approve_at_head_stale_and_blocked(self):
        self.pr()
        for verdict, sha, expected in [('APPROVE', self.approved, self.approved),
                                       ('APPROVE', self.changed, self.changed),
                                       ('REVIEW: BLOCKED', self.approved, None)]:
            with self.subTest(verdict=verdict, sha=sha):
                self.data['comments'][module.REPOS[0]+'#1'] = [{
                    'body': module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': sha}, f'{verdict}\nReviewed-SHA: {sha}\n\nReviewer: ChatGPT Dot'),
                    'author_association': 'OWNER'}]
                self.save()
                self.receipt({'repo':module.REPOS[0],'pr':1,'sha':sha},
                    self.data['comments'][module.REPOS[0]+'#1'][0]['body'], builder='builder',
                    reviewer='dot-user', relay_run_id=verdict+sha, branch_author='builder')
                self.assertEqual(self.q.approval(module.REPOS[0], 1), expected)

    def test_dot_receipt_binds_all_fields_and_requires_independence(self):
        repo = module.REPOS[0]
        self.pr()
        body = module.dot_review_receipts.publication_body({'repo': module.REPOS[0], 'pr': 1, 'sha': self.approved}, f'APPROVE\nReviewed-SHA: {self.approved}\nReviewer: ChatGPT Dot')
        self.data['comments'][repo+'#1'] = [{'body':body, 'author_association':'NONE',
                                            'user':{'login':'shared-owner'}}]
        self.save()
        meta = {'repo':repo,'pr':1,'sha':self.approved}
        receipt = self.receipt(meta, body, builder='builder', reviewer='dot-user',
                                                   relay_run_id='receipt-fixture', branch_author='builder')
        path = next((self.state/'relay'/'receipts').glob('*.json'))
        self.assertEqual(self.q.approval(repo, 1), self.approved)
        self.assertFalse(self.q.covered(repo, self.approved, self.updated))
        for field, value in [('repo', module.REPOS[1]), ('pr', 2), ('reviewed_sha', self.changed),
                             ('body_sha256', '0'*64), ('builder', None), ('builder', 'dot-user'),
                             ('branch_author', 'dot-user'), ('relay_run_id', '')]:
            with self.subTest(field=field, value=value):
                path.write_text(json.dumps({**receipt, field:value}))
                self.assertIsNone(self.q.approval(repo, 1))
        path.write_text(json.dumps(receipt))
        self.assertEqual(self.q.approval(repo, 1), self.approved)

    def test_receipt_cannot_be_rewritten_and_findings_do_not_supply_identity(self):
        repo = module.REPOS[0]
        self.pr()
        body = f'APPROVE\nReviewed-SHA: {self.approved}\nExample:\n```\nReviewer: ChatGPT Dot\n```'
        self.data['comments'][repo+'#1'] = [{'body':body,'author_association':'OWNER'}]
        self.save()
        self.assertTrue(self.q.covered(repo, self.q.approval(repo, 1), self.updated))
        meta = {'repo':repo,'pr':1,'sha':self.approved}
        body = module.dot_review_receipts.publication_body(meta, body+'\nDOT-REPORT-END')
        self.data['comments'][repo+'#1'][0]['body'] = body
        self.save()
        self.receipt(meta, body, builder='builder', reviewer='dot-user',
                                         relay_run_id='immutable', branch_author='builder')
        with self.assertRaisesRegex(ValueError, 'append-only'):
            self.receipt(meta, body, builder='different-builder', reviewer='dot-user',
                                             relay_run_id='immutable', branch_author='builder')
        forged = f'APPROVE\nReviewed-SHA: {self.approved}\nReviewer: ChatGPT Dot\nextra prose'
        self.data['comments'][repo+'#1'] = [{'body':forged,'author_association':'OWNER'}]
        self.save()
        self.assertIsNone(self.q.approval(repo, 1))

    def test_orchestrator_stamp_with_dot_marker_stays_excluded(self):
        self.pr()
        self.data['comments'][module.REPOS[0]+'#1'] = [{
            'body': f'APPROVE\nReviewed-SHA: {self.approved}\n\nOrchestrator merge queue: stamp\nReviewer: ChatGPT Dot',
            'author_association': 'OWNER'}]
        self.save()
        self.assertIsNone(self.q.approval(module.REPOS[0], 1))

    def test_fresh_review_handoff_uses_shared_free_first_router(self):
        from unittest.mock import patch
        self.pr()
        router = self.root / 'bin/dot-review.py'
        router.parent.mkdir(exist_ok=True)
        router.write_text('# fixture')
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        e = self.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        with patch.object(module, 'command', return_value='queued Dot: free capacity') as cmd:
            self.q.report(e, 'fresh_review', 'head changed', 'review')
        argv = cmd.call_args.args[0]
        self.assertEqual(argv[1], str(router))
        self.assertEqual(argv[-3:], ['submit', module.REPOS[0], '1'])

    def test_block_quoting_queue_stamp_remains_authoritative(self):
        self.pr()
        self.data['comments'][module.REPOS[0]+'#1'].append({
            'body': f'BLOCK\nReviewed-SHA: {self.approved}\nThe "Orchestrator merge queue:" claim is wrong.',
            'author_association': 'OWNER'})
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'review')

    def test_sigterm_during_checks_prevents_every_later_effect(self):
        self.pr(draft=True)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        green = self.q.green
        def terminate(*args):
            result = green(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            return result
        previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            with patch.object(self.q, 'green', side_effect=terminate):
                self.q.run(poll=0, discover=False)
        finally:
            for s, handler in previous.items(): signal.signal(s, handler)
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.calls('ready'), [])
        self.assertFalse(any(x.startswith('body=APPROVE') for a in self.load()['calls'] for x in a))
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'pending')

    def test_cancellation_during_action_readback_prevents_update_and_retarget(self):
        for kind, base in (('update', 'main'), ('retarget', 'branch-1')):
            with self.subTest(kind=kind):
                self.pr(n=2, state='behind', base=base)
                self.q.stopped = False
                read = self.q.pr
                def cancel(*args):
                    p = read(*args)
                    self.q.stopped = True
                    return p
                with patch.object(self.q, 'pr', side_effect=cancel):
                    self.q.action(kind, module.REPOS[0], 2, self.approved, 'main')
                self.assertEqual(self.calls('edit'), [])
                self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_reconciliation_uses_runner_lock_for_merge_and_action(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?", (self.approved,))
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase) VALUES(?,?,?,?,?,?,?)',
                              ('locked', 'update', module.REPOS[0], 1, self.approved, 'main', 'issued'))
        with (self.state / 'agent.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for reconcile in (lambda: self.q.reconcile(1, retry=True),
                              lambda: self.q.reconcile_action('locked', retry=True)):
                with self.assertRaises(BlockingIOError): reconcile()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'merging')
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0], 'issued')
        self.assertEqual(self.load()['calls'], [])

    def test_rejected_update_exhausts_after_three_retries_across_restart(self):
        self.pr(state='behind')
        self.data['update_reject'] = True; self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        for _ in range(8):
            self.q.tick(); self.restart()
        self.assertEqual(sum('update-branch' in str(a) for a in self.load()['calls']), 4)
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'action_exhausted'))
        self.assertEqual(self.q.db.execute('SELECT read_attempts FROM entries').fetchone()[0], 0)

    def test_rejected_merge_exhausts_after_three_retries_across_restart(self):
        self.pr(); self.data['merge_reject'] = True; self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        for _ in range(12):
            self.q.tick(); self.restart()
        self.assertEqual(len(self.calls('merge')), 4)
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'merge_exhausted'))

    def test_waiting_repo_does_not_block_other_repo_but_preserves_local_fifo(self):
        self.pr(1, state='unknown'); self.pr(2); self.pr(3, repo=module.REPOS[1])
        for repo, n in ((module.REPOS[0], 1), (module.REPOS[0], 2), (module.REPOS[1], 3)):
            self.q.enqueue(repo, n, self.approved)
        self.q.tick()
        self.assertEqual([a[2:5] for a in self.calls('merge')], [['3', '-R', module.REPOS[1]]])

    def test_default_gh_spacing_is_two_seconds_and_shared_between_queues(self):
        self.pr()
        q = module.Queue(self.state, self.root)
        other = module.Queue(self.root / 'other-state', self.root)
        self.addCleanup(q.db.close); self.addCleanup(other.db.close)
        starts = []
        real_command = module.command
        def observe(argv, **kwargs):
            if argv[0] == 'gh': starts.append(time.monotonic())
            return real_command(argv, **kwargs)
        with patch.object(module, 'command', side_effect=observe):
            q.pr(module.REPOS[0], 1)
            other.pr(module.REPOS[0], 1)
        self.assertGreaterEqual(starts[1] - starts[0], 1.99)

    def test_shared_gh_spacing_rechecks_short_sleeps_after_delayed_start(self):
        q = module.Queue(self.state, self.root)
        other = module.Queue(self.root / 'other-state', self.root)
        self.addCleanup(q.db.close); self.addCleanup(other.db.close)
        now, starts, sleeps = [100.0], [], []
        q.budget.clock = other.budget.clock = lambda: now[0]
        real_fsync = os.fsync
        def fsync(fd):
            real_fsync(fd)
            if not starts:
                now[0] += .4
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += min(seconds, .05)
        def command(argv, **kwargs):
            starts.append(now[0])
            now[0] += .1
            return '{}'
        with patch.object(module.time, 'monotonic', side_effect=lambda: now[0]), \
                patch.object(module.time, 'sleep', side_effect=sleep), \
                patch('lib.github_rate_limit.os.fsync', side_effect=fsync), \
                patch.object(module, 'command', side_effect=command):
            q.api('repos/example/repo')
            other.api('repos/example/repo')
        self.assertGreater(sleeps[0], .05)
        self.assertGreaterEqual(len(sleeps), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 2.0)

    def test_shared_gh_spacing_waits_for_reserved_slot_when_wall_clock_lags(self):
        q = module.Queue(self.state, self.root)
        other = module.Queue(self.root / 'other-state', self.root)
        self.addCleanup(q.db.close); self.addCleanup(other.db.close)
        wall, monotonic, starts, sleeps = [100.0], [100.0], [], []
        q.budget.clock = other.budget.clock = lambda: wall[0]
        def sleep(seconds):
            sleeps.append(seconds)
            monotonic[0] += seconds
            wall[0] += seconds / 2
        def command(argv, **kwargs):
            starts.append((wall[0], monotonic[0]))
            return '{}'
        with patch.object(module.time, 'monotonic', side_effect=lambda: monotonic[0]), \
                patch.object(module.time, 'sleep', side_effect=sleep), \
                patch.object(module, 'command', side_effect=command):
            q.api('repos/example/repo')
            reserved_slot = json.loads(q.budget.path.read_text())[q.budget.shared]['next_start']
            other.api('repos/example/repo')
        self.assertGreaterEqual(starts[1][0], reserved_slot)
        self.assertGreaterEqual(starts[1][1] - starts[0][1], 2.0)
        self.assertGreaterEqual(len(sleeps), 2)

    def test_shared_gh_spacing_survives_forward_wall_clock_step_from_delay_peer(self):
        q = module.Queue(self.state, self.root)
        self.addCleanup(q.db.close)
        wall, monotonic, starts = [100.0], [100.0], []
        q.budget.clock = lambda: wall[0]
        peer = module.GitHubReadBudget({}, path=q.budget.path, clock=lambda: wall[0])
        self.assertEqual(peer.reserve('core'), 0.0)
        peer_start = monotonic[0]
        def sleep(seconds):
            monotonic[0] += seconds
            wall[0] += seconds + (1.9 if monotonic[0] < 100.3 else 0)
        def command(argv, **kwargs):
            starts.append(monotonic[0])
            return '{}'
        with patch.object(module.time, 'monotonic', side_effect=lambda: monotonic[0]), \
                patch.object(module.time, 'sleep', side_effect=sleep), \
                patch.object(module, 'command', side_effect=command):
            q.api('repos/example/repo')
        self.assertGreaterEqual(starts[0] - peer_start, 2.0)

    def test_reservation_suite_runs_as_script_without_pythonpath(self):
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, str(ROOT / 'tools/test_github_rate_limit.py')],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_forward_clock_step_before_reserve_returns_preserves_peer_gap(self):
        q = module.Queue(self.state, self.root)
        self.addCleanup(q.db.close)
        wall, monotonic, starts = [100.0], [100.0], []
        q.budget.clock = lambda: wall[0]
        peer = module.GitHubReadBudget({}, path=q.budget.path, clock=lambda: wall[0])
        self.assertEqual(peer.reserve('core'), 0.0)
        peer_start = monotonic[0]
        original_reserve = q.budget.reserve
        def reserve(*args, **kwargs):
            reservation = original_reserve(*args, **kwargs)
            wall[0] += 1.9
            return reservation
        def sleep(seconds):
            monotonic[0] += seconds
            wall[0] += seconds
        def command(argv, **kwargs):
            starts.append(monotonic[0])
            return '{}'
        with patch.object(module.time, 'monotonic', side_effect=lambda: monotonic[0]), \
                patch.object(module.time, 'sleep', side_effect=sleep), \
                patch.object(q.budget, 'reserve', side_effect=reserve), \
                patch.object(module, 'command', side_effect=command):
            q.api('repos/example/repo')
        self.assertGreaterEqual(starts[0] - peer_start, 2.0)

    def test_successful_budget_check_crossing_spacing_deadline_prevents_dispatch(self):
        for bound, rollback in ((3, .8), (30, 27.8), (3, 0)):
            with self.subTest(bound=bound, rollback=rollback):
                q = module.Queue(self.root / f'late-check-{bound}-{rollback}', self.root)
                self.addCleanup(q.db.close)
                wall, monotonic, checks = [100.0], [100.0], []
                q.budget.clock = lambda: wall[0]
                with q.budget.state() as data:
                    data[q.budget.shared] = {'next_start': 102.0}
                original_reserve, original_check = q.budget.reserve, q.budget.check
                def reserve(*args, **kwargs):
                    reservation = original_reserve(*args, **kwargs)
                    wall[0] -= rollback
                    return reservation
                def sleep(seconds):
                    monotonic[0] += seconds
                    wall[0] += seconds
                def check(resource):
                    original_check(resource)
                    checks.append(monotonic[0])
                    sleep(1)
                with patch.object(module.time, 'monotonic', side_effect=lambda: monotonic[0]), \
                        patch.object(module.time, 'sleep', side_effect=sleep), \
                        patch.dict(module.BOUNDS['spacing'], seconds=bound), \
                        patch.object(q.budget, 'reserve', side_effect=reserve), \
                        patch.object(q.budget, 'check', side_effect=check), \
                        patch.object(module, 'command', return_value='{}') as command:
                    with self.assertRaises(module.WaitExpired):
                        q.api('repos/example/repo')
                command.assert_not_called()
                self.assertEqual(len(checks), 1)
                self.assertGreaterEqual(monotonic[0], 100 + bound)

    def test_reserved_slot_is_rechecked_after_provider_budget_check(self):
        q = module.Queue(self.state, self.root)
        self.addCleanup(q.db.close)
        wall, monotonic, starts = [100.0], [100.0], []
        q.budget.clock = lambda: wall[0]
        with q.budget.state() as data:
            data[q.budget.shared] = {'next_start': 102.0}
        original_check = q.budget.check
        checks = []
        def check(resource):
            original_check(resource)
            checks.append(wall[0])
            if len(checks) == 1:
                wall[0] -= 1.0
        def sleep(seconds):
            wall[0] += seconds
            monotonic[0] += seconds
        def command(argv, **kwargs):
            starts.append(wall[0])
            return '{}'
        with patch.object(module.time, 'monotonic', side_effect=lambda: monotonic[0]), \
                patch.object(module.time, 'sleep', side_effect=sleep), \
                patch.object(q.budget, 'check', side_effect=check), \
                patch.object(module, 'command', side_effect=command):
            q.api('repos/example/repo')
        self.assertGreaterEqual(starts[0], 102.0)
        self.assertGreaterEqual(len(checks), 2)

    def test_cancelled_mutation_retains_uncertainty_and_shared_cooldown(self):
        q = module.Queue(self.state, self.root)
        self.addCleanup(q.db.close)
        q.budget.clock = lambda: 100.0
        def command(*args, **kwargs):
            q.stopped = True
            raise module.Cancelled()
        with patch.object(module, 'command', side_effect=command):
            with self.assertRaises(module.ActionUncertain):
                q._gh_request(('pr', 'merge', '1'), False, None)
        data = json.loads(q.budget.path.read_text())
        self.assertEqual(data[q.budget.shared]['next_start'], 102.0)
        q.stopped = False
        with q.budget.call_slot(timeout=.02):
            pass

    def test_completion_lock_failure_never_replays_an_issued_update(self):
        p = self.pr(state='behind')
        real_open = os.open
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                q = module.Queue(self.root / f'cleanup-{cancelled}', self.root, gap=0)
                self.addCleanup(q.db.close)
                calls = []
                def command(*args, **kwargs):
                    calls.append(args)
                    if cancelled:
                        raise module.Cancelled()
                    return '{}'
                def open_lock(path, *args, **kwargs):
                    if calls and str(path) == str(q.budget.path) + '.lock':
                        raise OSError('completion lock unavailable')
                    return real_open(path, *args, **kwargs)
                with patch.object(q, 'pr', return_value=p), \
                        patch.object(q, 'behind', return_value=True), \
                        patch.object(module, 'command', side_effect=command):
                    with patch('lib.github_rate_limit.os.open', side_effect=open_lock):
                        if cancelled:
                            with self.assertRaises(module.ActionUncertain):
                                q.action('update', module.REPOS[0], 1, self.approved, '')
                        else:
                            q.action('update', module.REPOS[0], 1, self.approved, '')
                    self.assertEqual(q.db.execute('SELECT phase,attempts FROM actions').fetchone()[:],
                                     ('issued', 1))
                    with self.assertRaises(module.ActionUncertain):
                        q.action('update', module.REPOS[0], 1, self.approved, '')
                self.assertEqual(len(calls), 1)

    def test_failed_completion_write_keeps_peer_spacing(self):
        for fault in ('fsync', 'replace'):
            with self.subTest(fault=fault):
                q = module.Queue(self.root / f'first-{fault}', self.root)
                other = module.Queue(self.root / f'peer-{fault}', self.root)
                self.addCleanup(q.db.close); self.addCleanup(other.db.close)
                now, starts, failed = [100.0], [], []
                q.budget.clock = other.budget.clock = lambda: now[0]
                real_fsync, real_replace = os.fsync, os.replace
                def inject_fault(operation):
                    if operation == fault and len(starts) == 1 and not failed:
                        failed.append(operation)
                        raise OSError('completion persistence failed')
                def fsync(fd):
                    inject_fault('fsync')
                    real_fsync(fd)
                    if not starts:
                        now[0] += .4
                def replace(*args):
                    inject_fault('replace')
                    return real_replace(*args)
                def command(*args, **kwargs):
                    starts.append(now[0])
                    now[0] += .1
                    return '{}'
                with patch.object(module.time, 'monotonic', side_effect=lambda: now[0]), \
                        patch.object(module.time, 'sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + min(seconds, .05))), \
                        patch('lib.github_rate_limit.os.fsync', side_effect=fsync), \
                        patch('lib.github_rate_limit.os.replace', side_effect=replace), \
                        patch.object(module, 'command', side_effect=command):
                    try:
                        q.api('repos/example/repo')
                    except RuntimeError:
                        pass
                    other.api('repos/example/repo')
                self.assertEqual(failed, [fault])
                self.assertGreaterEqual(starts[1] - starts[0], 2.0)

    def test_orphan_dispatch_on_restart_releases_desk_without_replay(self):
        registry = self.root / 'desks.json'; registry.write_text('{"desks":{}}')
        os.environ['CARR_HERMES_DESKS'] = str(registry)
        brief = self.root / 'orphan.txt'; brief.write_text('task')
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',
                              ('orphan', 'dispatch', module.REPOS[0], 1, self.approved, str(brief), 'issued', 'sol'))
        self.restart(); self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('uncertain', None))
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE outcome='dispatch_uncertain'").fetchone())

    def test_changed_stack_base_is_not_overwritten_during_refresh(self):
        self.pr(n=2, base='branch-1', draft=True)
        read = self.q.pr
        reads = 0
        def move_base(*args):
            nonlocal reads
            reads += 1
            if reads == 2:
                self.load(); self.data['prs'][module.REPOS[0]+'#2']['base']['ref'] = 'other-unmerged'; self.save()
            return read(*args)
        with patch.object(self.q, 'pr', side_effect=move_base):
            self.q.refresh(module.REPOS[0], 'branch-1')
        self.assertEqual(self.calls('edit'), [])
        self.assertEqual(self.load()['prs'][module.REPOS[0]+'#2']['base']['ref'], 'other-unmerged')

    def test_upgrade_does_not_replace_an_issued_retarget_intent(self):
        self.pr(n=2, base='branch-1')
        key = f'retarget:{module.REPOS[0]}:2:{self.approved}:main'
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase) VALUES(?,?,?,?,?,?,?)',
                              (key, 'retarget', module.REPOS[0], 2, self.approved, 'main', 'issued'))
        self.restart()
        self.q.refresh(module.REPOS[0], 'branch-1')
        self.assertEqual(self.calls('edit'), [])
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 1)

    def test_ci_wait_deadline_survives_restart(self):
        self.pr(); self.data['required'] = [{'bucket':'pending'}]; self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        now = time.time()
        with patch.object(module.time, 'time', return_value=now): self.q.tick()
        self.restart()
        with patch.object(module.time, 'time', return_value=now + 75*60): self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'ci_timeout'))
        self.assertEqual(self.calls('merge'), [])

    def test_dispatch_cancelled_after_bridge_preflight_never_spawns(self):
        self.pr()
        brief = self.root / 'cancelled.txt'; brief.write_text('task')
        registry = self.root / 'desks.json'
        registry.write_text(json.dumps({'desks': {'sol': {
            'kind':'codex-session', 'model':'gpt-6.1-sol', 'effort':'high',
            'sandbox':'workspace-write', 'last_auth':True, 'thread_id':None}}}))
        os.environ['CARR_HERMES_DESKS'] = str(registry)
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',
                              ('cancel', 'dispatch', module.REPOS[0], 1, self.approved, str(brief)))
        real_command = module.command
        def cancel(argv, **kwargs):
            if '--help' in argv:
                self.q.stopped = True
                return 'send --fresh'
            return real_command(argv, **kwargs)
        with patch.object(module, 'command', side_effect=cancel): self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('planned', None))
        self.assertEqual(self.q.children, {})

    def test_restart_adopts_live_dispatcher_identity_but_rejects_pid_reuse(self):
        registry = self.root / 'desks.json'; registry.write_text('{"desks":{}}')
        os.environ['CARR_HERMES_DESKS'] = str(registry)
        brief = self.root / 'live.txt'; brief.write_text('task')
        receipt = brief.with_suffix('.dispatch.jsonl')
        identity = f'4242 Thu Oct 8 12:00:00 2026 python {self.root}/tools/room-bridge/dispatch.py --results {receipt} send sol'
        ps = self.root / 'bin/ps'; ps.write_text('#!/bin/sh\necho "'+identity+'"\n'); ps.chmod(0o755)
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk,dispatcher_pid,dispatcher_identity,receipt) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('live', 'dispatch', module.REPOS[0], 1, self.approved, str(brief), 'issued', 'sol',
                 4242, module.hashlib.sha256(identity.encode()).hexdigest(), str(receipt)))
        self.restart(); self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('issued', 'sol'))
        ps.write_text('#!/bin/sh\necho "4242 unrelated-process"\n')
        self.restart(); self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase,desk FROM actions').fetchone()[:], ('uncertain', None))

    def test_cancellation_while_waiting_for_call_slot_leaves_update_unissued(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        original_sleep = module.time.sleep
        def cancel(seconds):
            self.q.stopped = True
            original_sleep(0)
        real_gh = self.q.gh
        def delay_update(*args, **kwargs):
            if 'update-branch' in str(args): self.q.last_gh = time.monotonic() + 1
            return real_gh(*args, **kwargs)
        with patch.object(self.q, 'gh', side_effect=delay_update), patch.object(module.time, 'sleep', side_effect=cancel):
            self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,attempts FROM actions').fetchone()[:], ('planned', 0))
        self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_transient_read_failures_have_three_retries(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        for _ in range(8):
            with patch.object(self.q, 'pr', side_effect=RuntimeError('gh api failed (exit 1)')):
                self.q.tick()
            self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'retry_exhausted'))

    def test_every_github_call_site_has_a_persisted_failure_budget(self):
        repo = module.REPOS[0]
        path = f'repos/{repo}'
        cases = (
            ('PR read (queue, action, refresh, dispatch, reconcile, migration, archive)',
             ('api', path+'/pulls/1'), (), lambda q: q.pr(repo, 1)),
            ('approval comments', ('api', path+'/issues/1/comments?per_page=100&page=1'), (),
             lambda q: q.approval(repo, 1)),
            ('check runs', ('api', path+f'/commits/{self.approved}/check-runs?per_page=100&page=1'), (),
             lambda q: q.green(repo, 1, self.approved)),
            ('commit statuses', ('api', path+f'/commits/{self.approved}/statuses?per_page=100&page=1'), (),
             lambda q: q.green(repo, 1, self.approved)),
            ('required checks', ('pr', 'checks', '1'), (), lambda q: q.green(repo, 1, self.approved)),
            ('update branch', ('api', path+'/pulls/1/update-branch'), (),
             lambda q: q.api(path+'/pulls/1/update-branch', '-X', 'PUT', '-f', 'expected_head_sha='+self.approved)),
            ('retarget', ('pr', 'edit', '1'), (), lambda q: q.gh('pr', 'edit', '1', '-R', repo, '--base', 'main')),
            ('approval stamp', ('api', path+'/issues/1/comments'), ('body=stamp',),
             lambda q: q.api(path+'/issues/1/comments', '-f', 'body=stamp')),
            ('draft ready', ('pr', 'ready', '1'), (), lambda q: q.gh('pr', 'ready', '1', '-R', repo)),
            ('guarded merge', ('pr', 'merge', '1'), (),
             lambda q: q.gh('pr', 'merge', '1', '-R', repo, '--squash', '--match-head-commit', self.approved)),
            ('merge intent GraphQL (queue and reconcile)', ('api', 'graphql'), ('n=1',),
             lambda q: q.merge_pending(repo, 1, self.approved)),
            ('open PRs (discovery, refresh, hold migration)', ('api', path+'/pulls?state=open&per_page=100&page=1'), (),
             lambda q: q.pages(path+'/pulls?state=open&per_page=100')),
            ('changed files for hold migration', ('api', path+'/pulls/1/files?per_page=100&page=1'), (),
             lambda q: q.pages(path+'/pulls/1/files?per_page=100')),
            ('create hold label', ('label', 'create', module.HOLD), (),
             lambda q: q.gh('label', 'create', module.HOLD, '-R', repo, '--force')),
            ('apply hold label', ('api', path+'/issues/1/labels'), (),
             lambda q: q.api(path+'/issues/1/labels', '-X', 'POST', '-f', 'labels[]='+module.HOLD)),
        )
        for name, prefix, contains, invoke in cases:
            with self.subTest(site=name):
                fixture = QueueTests()
                fixture.setUp()
                try:
                    fixture.pr(1)
                    fixture.pr(2, head=fixture.updated)
                    fixture.data['gh_failure'] = dict(prefix=prefix, contains=contains)
                    fixture.save()
                    for _ in range(8):
                        with self.assertRaises((RuntimeError, OSError, subprocess.TimeoutExpired)):
                            invoke(fixture.q)
                        fixture.restart()
                    calls = fixture.load()['calls']
                    rejected = lambda a: a[:len(prefix)]==list(prefix) and all(x in a for x in contains)
                    self.assertEqual(sum(rejected(a) for a in calls), 4)
                    fixture.q.enqueue(repo, 2, fixture.approved)
                    fixture.q.tick()
                    self.assertEqual([a[2] for a in fixture.calls('merge') if a[2]=='2'], ['2'])
                    self.assertTrue(fixture.load()['prs'][repo+'#2']['merged'])
                    self.assertEqual(sum(rejected(a) for a in fixture.load()['calls']), 4)
                finally:
                    fixture.doCleanups()

    def test_rejected_stamp_blocks_entry_and_releases_next_pr(self):
        repo = module.REPOS[0]
        self.pr(1); self.pr(2, head=self.updated)
        prefix = ['api', f'repos/{repo}/issues/1/comments']
        marker = f'body=APPROVE\nReviewed-SHA: {self.approved}\n\nOrchestrator merge queue: independent approval of {self.approved}; patch unchanged; hosted checks green.'
        self.data['gh_failure'] = dict(prefix=prefix, contains=[marker])
        self.save()
        first = self.q.enqueue(repo, 1, self.approved)
        self.q.enqueue(repo, 2, self.approved)
        for _ in range(8):
            self.q.tick(); self.restart()
        entry = self.q.db.execute('SELECT phase,outcome,merge_attempts FROM entries WHERE id=?', (first,)).fetchone()
        self.assertEqual(entry[0], 'blocked')
        self.assertEqual(entry[2], 0)
        self.assertEqual(sum(a[:2]==prefix and marker in a for a in self.load()['calls']), 4)
        self.assertEqual([a[2] for a in self.calls('merge')], ['2'])
        reason = self.q.db.execute('SELECT detail FROM events WHERE entry_id=? ORDER BY id DESC LIMIT 1', (first,)).fetchone()[0]
        self.assertIn('403', reason)
        self.assertIn('4', reason)

    def test_response_errors_and_transient_failures_share_the_github_budget(self):
        repo = module.REPOS[0]
        path = f'repos/{repo}'
        cases = (
            ('HTTP500', dict(prefix=['api', path+'/pulls/1'], status=500), lambda q: q.pr(repo, 1)),
            ('malformed JSON', dict(prefix=['api', path+'/pulls/1'], malformed=True), lambda q: q.pr(repo, 1)),
            ('invalid page list', dict(prefix=['api', path+'/pulls?state=open&per_page=100&page=1'], response={}),
             lambda q: q.pages(path+'/pulls?state=open&per_page=100')),
            ('GraphQL error envelope', dict(prefix=['api', 'graphql'], response={'errors':[{'message':'unavailable'}]}),
             lambda q: q.merge_pending(repo, 1, self.approved)),
            ('missing PR fields', dict(prefix=['api', path+'/pulls/1'], response={}), lambda q: q.pr(repo, 1)),
            ('invalid required checks', dict(prefix=['pr', 'checks', '1'], response=[{}]),
             lambda q: q.green(repo, 1, self.approved)),
        )
        for name, failure, invoke in cases:
            with self.subTest(error=name):
                fixture = QueueTests(); fixture.setUp()
                try:
                    fixture.pr(1); fixture.pr(2, head=fixture.updated)
                    fixture.data['gh_failure'] = failure; fixture.save()
                    for _ in range(8):
                        with self.assertRaises((RuntimeError, ValueError, KeyError, TypeError)):
                            invoke(fixture.q)
                        fixture.restart()
                    prefix = failure['prefix']
                    self.assertEqual(sum(a[:len(prefix)]==prefix for a in fixture.load()['calls']), 4)
                    fixture.q.enqueue(repo, 2, fixture.approved); fixture.q.tick()
                    self.assertEqual([a[2] for a in fixture.calls('merge')], ['2'])
                finally:
                    fixture.doCleanups()

    def test_rejected_ready_and_merge_release_the_queue_after_provider_readback(self):
        repo = module.REPOS[0]
        for verb in ('ready', 'merge'):
            with self.subTest(verb=verb):
                fixture = QueueTests(); fixture.setUp()
                try:
                    fixture.pr(1, draft=True); fixture.pr(2, head=fixture.updated)
                    fixture.data['gh_failure'] = dict(prefix=['pr', verb, '1']); fixture.save()
                    fixture.q.enqueue(repo, 1, fixture.approved)
                    fixture.q.enqueue(repo, 2, fixture.approved)
                    for _ in range(12):
                        fixture.q.tick(); fixture.restart()
                    self.assertEqual(len([a for a in fixture.calls(verb) if a[2]=='1']), 4)
                    self.assertIn(fixture.q.db.execute('SELECT phase FROM entries WHERE pr=1').fetchone()[0], ('blocked', 'exhausted'))
                    self.assertEqual([a[2] for a in fixture.calls('merge') if a[2]=='2'], ['2'])
                finally:
                    fixture.doCleanups()

    def test_reconcile_unmerged_intent_resets_only_its_exhausted_read_budget(self):
        repo = module.REPOS[0]
        self.pr(1)
        self.pr(2, head=self.updated)
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{repo}/pulls/2']); self.save()
        for _ in range(4):
            with self.assertRaises(module.ReadRejected):
                self.q.pr(repo, 2)
            self.restart()
        self.load()
        entry = self.q.enqueue(repo, 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?,merge_attempts=1 WHERE id=?", (self.approved, entry))
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{repo}/pulls/1'])
        self.save()
        for _ in range(4):
            self.q.tick(); self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], 'blocked')
        self.load(); del self.data['gh_failure']; self.save()
        self.q.reconcile(entry, retry=True)
        self.restart(); self.q.tick()
        self.assertEqual([a[2] for a in self.calls('merge')], ['1'])
        self.assertEqual(self.q.db.execute('SELECT merge_attempts FROM entries WHERE id=?', (entry,)).fetchone()[0], 2)
        with self.assertRaises(module.GitHubExhausted):
            self.q.pr(repo, 2)
        self.assertEqual(sum(a[:2]==['api', f'repos/{repo}/pulls/2'] for a in self.load()['calls']), 4)

    def test_mixed_failures_and_changing_heads_do_not_replenish_entry_budget(self):
        repo = module.REPOS[0]
        self.pr(1); self.pr(2, head=self.updated)
        first = self.q.enqueue(repo, 1, self.approved)
        self.q.enqueue(repo, 2, self.approved)
        for attempt in range(4):
            self.load()
            path = f'repos/{repo}/pulls/1' if attempt % 2 == 0 else f'repos/{repo}/issues/1/comments'
            self.data['gh_failure'] = dict(prefix=['api', path])
            if attempt == 3:
                self.data['prs'][repo+'#1']['head']['sha'] = self.updated
            self.save(); self.q.tick(); self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase,github_attempts,read_attempts,transient_attempts FROM entries WHERE id=?', (first,)).fetchone()[:],
                         ('blocked', 4, 2, 2))
        self.assertEqual([a[2] for a in self.calls('merge')], ['2'])

    def assert_rejected_read_is_bounded(self, path, status):
        self.pr(1)
        self.pr(2, head=self.updated)
        self.data['api_failure'] = {'path': path, 'status': status}
        self.save()
        first = self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.enqueue(module.REPOS[0], 2, self.approved)
        for _ in range(8):
            self.q.tick()
            self.restart()
        entry = self.q.db.execute('SELECT * FROM entries WHERE id=?', (first,)).fetchone()
        self.assertEqual((entry['phase'], entry['outcome']), ('blocked', 'read_exhausted'))
        self.assertEqual(entry['read_attempts'], 4)
        self.assertEqual(entry['transient_attempts'], 0)
        self.assertEqual(entry['merge_attempts'], 0)
        calls = self.load()['calls']
        next_merge = next(i for i, a in enumerate(calls) if a[:3]==['pr', 'merge', '2'])
        self.assertEqual(sum(a[0]=='api' and a[1].split('?')[0]==path for a in calls[:next_merge]), 4)
        self.assertEqual([a[2] for a in self.calls('merge')], ['2'])
        reason = self.q.db.execute('SELECT detail FROM events WHERE entry_id=? AND outcome=?',
                                  (first, 'read_exhausted')).fetchone()[0]
        self.assertIn(str(status), reason)
        self.assertIn('4', reason)

    def test_rejected_pr_read_is_bounded_and_next_pr_runs(self):
        self.assert_rejected_read_is_bounded('repos/jbookout/carr-system/pulls/1', 404)

    def test_rejected_comment_read_is_bounded_and_next_pr_runs(self):
        self.assert_rejected_read_is_bounded('repos/jbookout/carr-system/issues/1/comments', 403)

    def test_rejected_check_read_is_bounded_and_next_pr_runs(self):
        self.assert_rejected_read_is_bounded(f'repos/jbookout/carr-system/commits/{self.approved}/check-runs', 404)

    def test_rejected_api_reads_follow_effective_method_and_graphql_operation(self):
        self.pr()
        cases = (
            ('repos/jbookout/carr-system/pulls/1', ('-X', 'GET', '-f', 'page=1'), module.ReadRejected),
            ('repos/jbookout/carr-system/pulls/1', ('--method=HEAD',), module.ReadRejected),
            ('repos/jbookout/carr-system/pulls/1', ('-f', 'body=comment'), module.ActionRejected),
            ('repos/jbookout/carr-system/pulls/1', ('-X', 'PUT', '-f', 'expected_head_sha='+self.approved), module.ActionRejected),
            ('graphql', ('-f', 'query=query { viewer { login } }'), module.ReadRejected),
            ('graphql', ('-f', 'query=mutation { effect }'), module.ActionRejected),
        )
        for path, args, error in cases:
            with self.subTest(path=path, args=args):
                self.data['api_failure'] = {'path': path, 'status': 403}
                self.save()
                with self.assertRaises(error):
                    self.q.api(path, *args)
                self.load()

    def assert_merged_entry_reconciles_and_refreshes(self, status, phase):
        self.pr()
        self.pr(2, state='behind')
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.data['api_failure'] = {'path': 'repos/jbookout/carr-system/pulls/1', 'status': status}
        self.save()
        for _ in range(4):
            self.q.tick()
            self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0],
                         phase)
        self.load()
        del self.data['api_failure']
        self.save()
        self.q.reconcile(entry)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], phase)
        self.q.reconcile(entry, retry=True)
        self.restart()
        self.data['api_failure'] = {'path': 'repos/jbookout/carr-system/pulls/1', 'status': status}
        self.save()
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], 'merging')
        self.load()
        del self.data['api_failure']
        self.save()
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome,tested,merge_attempts FROM entries WHERE id=?',
                                          (entry,)).fetchone()[:], ('done', 'merged', self.approved, 1))
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(sum('update-branch' in str(a) for a in self.load()['calls']), 1)
        self.assertEqual(self.load()['prs'][module.REPOS[0]+'#2']['head']['sha'], self.updated)
        reason = self.q.db.execute('SELECT detail FROM events WHERE entry_id=? ORDER BY id DESC LIMIT 1',
                                  (entry,)).fetchone()[0]
        self.assertIn(self.merge_sha, reason)
        self.assertIn('post-merge refresh complete', reason)

    def test_exhausted_merged_entry_reconciles_and_refreshes_without_remerging(self):
        self.assert_merged_entry_reconciles_and_refreshes(500, 'exhausted')

    def test_blocked_merged_entry_reconciles_and_refreshes_without_remerging(self):
        self.assert_merged_entry_reconciles_and_refreshes(404, 'blocked')

    def test_merged_reconciliation_never_replays_merge_after_inconsistent_readback(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.load()
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='exhausted',transient_attempts=4 WHERE id=?", (entry,))
        self.q.reconcile(entry, retry=True)
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=False, state='open')
        self.save()
        self.restart()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries WHERE id=?', (entry,)).fetchone()[:],
                         ('merging', 'merge_uncertain'))

    def test_issued_merge_observation_exhausts_without_losing_intent(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?,merge_attempts=1", (self.approved,))
        reads = 0
        read = self.q.pr
        def fail(*args):
            nonlocal reads
            reads += 1
            read(*args)
            raise RuntimeError('provider observation failed')
        with patch.object(self.q, 'pr', side_effect=fail):
            for _ in range(8): self.q.tick()
        self.assertEqual(reads, 4)
        self.assertEqual(self.q.db.execute('SELECT phase,tested,outcome FROM entries').fetchone()[:],
                         ('exhausted', self.approved, 'retry_exhausted'))
        self.q.reconcile(1)
        self.assertEqual(self.calls('merge'), [])

    def test_auto_enqueue_reactivation_is_capped_at_three(self):
        self.pr()
        for _ in range(8):
            self.q.discover()
            with self.q.db: self.q.db.execute("UPDATE entries SET phase='review'")
            self.restart()
        self.q.discover()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'review')
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE outcome='auto_enqueue_exhausted'").fetchone())

    def test_approved_head_mismatch_requires_fresh_review(self):
        self.pr(head=self.changed)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(self.cli('status').returncode, 0)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'review')
        self.assertEqual(self.calls('merge'), [])

    def test_behind_updates_once_then_retests_equivalent_patch(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.calls('merge')[0][-1], self.updated)
        posts=[a for a in self.load()['calls'] if any(x.startswith('body=APPROVE') for x in a)]
        self.assertIn('Reviewed-SHA: '+self.updated, next(x for x in posts[0] if x.startswith('body=')))

    def test_conflicting_writes_brief_without_resolving(self):
        (self.remote/'file.txt').write_text('main conflict\n')
        self.git('commit','-qam','conflicting main')
        self.main=self.git('rev-parse','HEAD').strip()
        self.pr(state='dirty', head=self.changed)
        self.data['prs'][module.REPOS[0]+'#1']['base']['sha']=self.main
        self.save()
        self.q.refresh(module.REPOS[0], 'just-merged')
        briefs=list((self.root/'out/orch/queue/codex').glob('*.txt'))
        self.assertEqual(len(briefs), 1)
        self.assertIn('file.txt', briefs[0].read_text())
        self.assertIn(self.changed, briefs[0].read_text())
        self.assertEqual(self.calls('merge'), [])

    def test_held_is_skipped_by_queue_discover_and_refresh(self):
        self.pr(state='behind', held=True)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.discover()
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertEqual(self.calls('merge'), [])
        self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'held')

    def test_stacked_draft_retargets_and_updates_after_parent_merge(self):
        self.pr(n=2, base='branch-1', draft=True)
        self.q.refresh(module.REPOS[0], 'branch-1')
        self.assertEqual(len(self.calls('edit')), 1)
        self.assertEqual(self.load()['prs'][module.REPOS[0]+'#2']['base']['ref'], 'main')
        self.assertTrue(any('update-branch' in str(a) for a in self.load()['calls']))
        self.assertTrue(self.data['prs'][module.REPOS[0]+'#2']['draft'])

    def test_crash_after_merge_reconciles_main_and_refreshes(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.db.close()
        self.q=module.Queue(self.state,self.root,gap=0)
        self.addCleanup(self.q.db.close)
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'done')
        self.assertIn('confirmed', (self.q.db.execute("SELECT detail FROM events WHERE outcome='merged' ORDER BY id DESC").fetchone()[0]))

    def test_board_failure_does_not_stop_merges_and_is_logged(self):
        os.environ['BOARD_FAIL']='1'
        self.pr()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.flush_events()
        self.q.tick()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')),1)
        self.assertIn('progress_board_write_failed',(self.state/'queue.log').read_text())
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'done')

    def test_real_board_cli_creates_and_updates_queue_cards(self):
        board_root = self.root / 'board-output'
        env = {'PROGRESS_BOARD_ROOT': str(board_root), 'PROGRESS_BOARD_LOCAL_ONLY': '1',
               'PROGRESS_BOARD_SKIP_GH': '1', 'PROGRESS_BOARD_SKIP_PROBE': '1'}
        with patch.dict(os.environ, env):
            module.command([sys.executable, str(ROOT / 'tools/progress_board.py'),
                            'init', 'carr-v5', '--title', 'Queue test'])
            self.q.root = ROOT
            for repo in module.REPOS:
                with self.q.db:
                    self.q.event(repo, 123, 'queued', 'Approved test head')
            self.q.flush_events()
            path = board_root / 'boards/carr-v5.json'
            tasks = json.loads(path.read_text())['tasks']
            for card, repo in zip(('pr-123', 'app-pr-123', 'factory-pr-123'), module.REPOS):
                self.assertEqual(tasks[card]['repo'], repo)
                self.assertEqual(tasks[card]['pr'], 123)
                self.assertEqual(tasks[card]['status'], 'review')
                self.assertEqual(tasks[card]['executor'], 'Merge queue')
                self.assertEqual(tasks[card]['title'], f'{repo.split("/")[-1]} PR #123')
            with self.q.db:
                self.q.event(module.REPOS[0], 123, 'waiting_ci', 'Hosted checks pending')
            self.q.flush_events()
            card = json.loads(path.read_text())['tasks']['pr-123']
            self.assertEqual(card['stage'], 'ci')
            self.assertEqual(card['note'], 'Merge queue: waiting_ci. Hosted checks pending')
            self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM events WHERE published=1').fetchone()[0], 4)

    def test_real_board_queue_events_preserve_existing_card_metadata(self):
        board_root = self.root / 'board-output'
        env = {'PROGRESS_BOARD_ROOT': str(board_root), 'PROGRESS_BOARD_LOCAL_ONLY': '1',
               'PROGRESS_BOARD_SKIP_GH': '1', 'PROGRESS_BOARD_SKIP_PROBE': '1'}
        board_cli = [sys.executable, str(ROOT / 'tools/progress_board.py')]
        metadata = {'title': 'Assigned task', 'executor': 'Codex', 'provider': 'OpenAI',
                    'model': 'fixture', 'effort': 'high'}
        with patch.dict(os.environ, env):
            module.command(board_cli + ['init', 'carr-v5', '--title', 'Queue test'])
            for card, repo in zip(('pr-123', 'app-pr-123', 'factory-pr-123'), module.REPOS):
                fields = [value for key, value in metadata.items() for value in ('--' + key, value)]
                module.command(board_cli + ['task', 'carr-v5', card, *fields,
                                            '--status', 'review', '--repo', repo, '--pr', '123'])
            self.q.root = ROOT
            for outcome, stage in (('waiting_ci', 'ci'), ('blocked_review', 'review')):
                for repo in module.REPOS:
                    with self.q.db:
                        self.q.event(repo, 123, outcome, 'Queue update')
                self.q.flush_events()
                tasks = json.loads((board_root / 'boards/carr-v5.json').read_text())['tasks']
                for card in ('pr-123', 'app-pr-123', 'factory-pr-123'):
                    self.assertEqual({key: tasks[card][key] for key in metadata}, metadata)
                    self.assertEqual(tasks[card]['stage'], stage)
                    self.assertEqual(tasks[card]['note'], f'Merge queue: {outcome}. Queue update')
            self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM events WHERE published=1').fetchone()[0], 6)

    def test_board_stderr_survives_retry_exhaustion_and_restart(self):
        script = self.root / 'tools/progress_board.py'
        script.write_text("import sys\nprint('board rejected: missing executor', file=sys.stderr)\nsys.exit(1)\n")
        self.q.enqueue(module.REPOS[0], 123, self.approved)
        for _ in range(module.MAX_ATTEMPTS):
            self.q.flush_events()
        self.q.db.close()
        self.q = module.Queue(self.state, self.root, gap=0)
        self.addCleanup(self.q.db.close)
        self.q.flush_events()
        rows = [json.loads(line) for line in (self.state / 'queue.log').read_text().splitlines()]
        failures = [row for row in rows if row['outcome'].startswith('progress_board_write_')]
        self.assertEqual(failures[-1]['outcome'], 'progress_board_write_exhausted')
        self.assertIn('board rejected: missing executor', failures[-1]['detail'])
        self.assertIn('exhausted 4 attempts', failures[-1]['detail'])
        self.assertEqual(self.q.db.execute('SELECT published FROM events').fetchone()[0], 0)

    def test_board_stderr_is_redacted_before_bounding(self):
        script = self.root / 'tools/progress_board.py'
        script.write_text("import sys\nprint('prefix-' + 'x' * 3000 + ' postgres://user:secret@host/database token=ghp_' + 'a' * 3000 + ' final failure', file=sys.stderr)\nsys.exit(1)\n")  # ci-secret-scan: allow
        self.q.enqueue(module.REPOS[0], 123, self.approved)
        self.q.flush_events()
        row = json.loads((self.state / 'queue.log').read_text().splitlines()[-1])
        self.assertIn('final failure', row['detail'])
        self.assertIn('[REDACTED]', row['detail'])
        self.assertNotIn('postgres://', row['detail'])
        self.assertNotIn('a' * 100, row['detail'])
        self.assertNotIn('prefix-', row['detail'])
        self.assertLess(len(row['detail']), 2200)

    def test_board_timeout_keeps_partial_stderr_and_stops_retries(self):
        self.q.enqueue(module.REPOS[0], 123, self.approved)
        timeout = subprocess.TimeoutExpired('board', 60, stderr=b'publication stalled: token=ghp_abcdefghijklmnopqrstuv')
        with patch.object(module.subprocess, 'run', side_effect=timeout) as run:
            self.q.flush_events()
            self.q.flush_events()
        row = json.loads((self.state / 'queue.log').read_text().splitlines()[-1])
        self.assertEqual(row['outcome'], 'progress_board_write_exhausted')
        self.assertIn('publication stalled', row['detail'])
        self.assertIn('[REDACTED]', row['detail'])
        self.assertNotIn('ghp_', row['detail'])
        self.assertEqual(run.call_count, 1)

    def test_fifo_across_repositories(self):
        self.pr(n=2, repo=module.REPOS[1])
        self.pr(n=1)
        self.q.enqueue(module.REPOS[1],2,self.approved)
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge')[0][2:5], ['2','-R',module.REPOS[1]])

    def test_no_checks_and_required_red_never_merge(self):
        self.pr()
        self.data['runs']=[];self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])
        self.load(); self.data['runs']=[{'id':1,'name':'CI','app':{'id':1},'status':'completed','conclusion':'success'}]
        self.data['required']=[{'bucket':'fail'}];self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_queued_approval_cannot_authorize_itself(self):
        self.pr(head=self.updated)
        self.data['comments'][module.REPOS[0]+'#1']=[{'body':f'APPROVE\nReviewed-SHA: {self.approved}\n\nOrchestrator merge queue: independent approval of somebody.', 'author_association':'OWNER'}]
        self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_later_release_block_marker_and_malformed_header_require_review(self):
        for body in ('REVIEW: BLOCKED\nReviewed-SHA: '+self.approved,
                     'APPROVE\nExample\nReviewed-SHA: '+self.approved,
                     f'APPROVE\nReviewed-SHA: {self.approved}\nReviewed-SHA: {self.changed}'):
            with self.subTest(body=body):
                self.pr()
                self.data['comments'][module.REPOS[0]+'#1'].append({'body':body,'author_association':'OWNER'})
                self.save()
                self.assertIsNone(self.q.approval(module.REPOS[0],1))

    def test_git_locations_from_a_calling_hook_cannot_redirect_private_repo(self):
        with patch.dict(os.environ, {'GIT_DIR': str(self.root/'wrong.git'), 'GIT_WORK_TREE': str(self.root/'wrong')}):
            self.assertEqual(self.q.git(module.REPOS[0], 'rev-parse', 'origin/main').strip(), self.main)

    def test_exited_dispatch_without_receipt_is_logged_and_not_repeated(self):
        registry=self.root/'desks.json';registry.write_text('{"desks":{}}')
        os.environ['CARR_HERMES_DESKS']=str(registry)
        brief=self.root/'brief.txt';brief.write_text('brief')
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',('exited','dispatch',module.REPOS[0],1,self.approved,str(brief),'issued','sol'))
        child=subprocess.Popen([sys.executable,'-c','pass'])
        child.wait();self.q.children['exited']=child
        self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute("SELECT phase FROM actions WHERE key='exited'").fetchone()[0], 'uncertain')
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE outcome='dispatch_uncertain'").fetchone())

    def test_fresh_review_can_reactivate_the_same_exact_head(self):
        self.pr();self.data['comments'][module.REPOS[0]+'#1'].append({'body':'REVIEW: BLOCKED','author_association':'OWNER'});self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        self.load();self.data['comments'][module.REPOS[0]+'#1'].append({'body':f'APPROVE\nReviewed-SHA: {self.approved}','author_association':'OWNER'});self.save()
        self.q.discover();self.q.tick()
        self.assertEqual(len(self.calls('merge')),1)

    def test_legacy_queue_stamp_is_not_an_independent_review(self):
        self.pr()
        self.data['comments'][module.REPOS[0]+'#1']=[{'body':f'APPROVE\nReviewed-SHA: {self.approved}\nOrchestrator: verified exact head and green.', 'author_association':'OWNER'}]
        self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_interrupted_update_does_not_blindly_repeat(self):
        self.pr(state='behind')
        self.data['update_async']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.q.db.close()
        self.q=module.Queue(self.state,self.root,gap=0);self.addCleanup(self.q.db.close)
        self.q.tick()
        updates=[a for a in self.load()['calls'] if 'update-branch' in str(a)]
        self.assertEqual(len(updates),1)
        self.assertEqual(self.calls('merge'),[])

    def test_rejected_update_can_retry_after_fresh_readback(self):
        self.pr(state='behind');self.data['update_reject']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        self.load();self.data['update_reject']=False;self.save();self.q.tick();self.q.tick()
        self.assertEqual(len(self.calls('merge')),1)

    def test_operator_can_reconcile_interrupted_update(self):
        self.pr(state='behind');self.data['update_async']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        key=self.q.db.execute("SELECT key FROM actions WHERE kind='update'").fetchone()[0]
        self.q.reconcile_action(key,retry=False)
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'issued')
        self.q.reconcile_action(key,retry=True)
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'planned')

    def test_partial_dispatch_receipt_does_not_block_other_briefs(self):
        self.pr(1);self.pr(2)
        registry=self.root/'desks.json';registry.write_text(json.dumps({'desks':{}}));os.environ['CARR_HERMES_DESKS']=str(registry)
        for n,phase in ((1,'issued'),(2,'planned')):
            brief=self.root/f'conflict{n}.txt';brief.write_text('task')
            with self.q.db:
                self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',(f'a{n}','dispatch',module.REPOS[0],n,self.approved,str(brief),phase,'busy-sol' if n==1 else None))
        (self.root/'conflict1.dispatch.jsonl').write_text('{"status":')
        self.q.dispatch_conflicts()
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE pr=2 AND outcome='dispatch_waiting'").fetchone())

    def test_board_failure_retries_and_all_outcomes_are_logged(self):
        self.pr()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick();self.q.tick()
        os.environ['BOARD_FAIL']='1';self.q.flush_events()
        log=(self.state/'queue.log').read_text()
        self.assertIn('merge_requested',log)
        self.assertIn('post-merge refresh complete',log)
        self.assertGreater(self.q.db.execute('SELECT COUNT(*) FROM events WHERE published=0').fetchone()[0],0)
        os.environ.pop('BOARD_FAIL');self.q.flush_events()
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM events WHERE published=0').fetchone()[0],0)

    def test_dispatch_waits_for_free_sol_desk_then_records_result(self):
        self.pr()
        brief=self.root/'conflict.txt';brief.write_text('bounded task')
        with self.q.db:
            self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload) VALUES(?,?,?,?,?,?)',('test','dispatch',module.REPOS[0],1,self.approved,str(brief)))
        registry=self.root/'desks.json'
        desk={'kind':'codex-session','model':'gpt-6.1-sol','effort':'high','sandbox':'workspace-write','last_auth':True,'thread_id':None,'busy':True}
        registry.write_text(json.dumps({'desks':{'free-sol':desk}}));os.environ['CARR_HERMES_DESKS']=str(registry)
        bridge=self.root/'tools/room-bridge';bridge.mkdir()
        (bridge/'dispatch.py').write_text("import json,sys\nfrom pathlib import Path\n"
            "a=sys.argv[1:]\nif '--help' in a: print('send --fresh');sys.exit()\n"
            "assert '--family' not in a\n"
            "Path(a[a.index('--results')+1]).write_text(json.dumps({'desk':'free-sol','kind':'codex-session','status':'completed','thread_id':'new-thread','result':'fixed'})+'\\n')\n")
        self.q.dispatch_conflicts()
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'planned')
        desk['busy']=False;registry.write_text(json.dumps({'desks':{'free-sol':desk}}))
        self.q.dispatch_conflicts()
        import time
        for _ in range(50):
            time.sleep(.01)
            self.q.dispatch_conflicts()
            if self.q.db.execute('SELECT phase FROM actions').fetchone()[0]=='done': break
        self.assertEqual(self.q.db.execute('SELECT phase FROM actions').fetchone()[0],'done')
        self.assertTrue(brief.with_suffix('.dispatch.jsonl').exists())

    def test_partial_legacy_append_does_not_stall_live_queue(self):
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-queue.txt').write_text(f'{module.REPOS[0]} 1 {self.approved} okay\n' + module.REPOS[1]+' 2 ')
        self.q.import_legacy(legacy)
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0],1)

    def test_launchd_is_only_installed_on_primary_machine(self):
        sys.path.insert(0,str(ROOT))
        from lib.launchd_scope import allowed_on_machine
        self.assertFalse(allowed_on_machine('com.carr.merge-queue.plist',primary=False))

    def test_merge_commit_must_be_on_main(self):
        self.pr()
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=True,state='closed',merge_commit_sha=self.changed);self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'pending')

    def test_definite_merge_rejection_rearms_only_after_provider_readback(self):
        self.pr();self.data['merge_reject']=True;self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick()
        self.load();self.data['merge_reject']=False;self.save()
        self.q.tick();self.q.tick()
        self.assertTrue(self.load()['prs'][module.REPOS[0]+'#1']['merged'])

    def test_queued_server_merge_is_never_reissued(self):
        self.pr();self.q.enqueue(module.REPOS[0],1,self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merge_rejected',tested=?",(self.approved,))
        self.data['server_queued']=True;self.save();self.q.tick();self.q.tick()
        self.assertEqual(self.calls('merge'),[])

    def test_operator_reconcile_requires_unchanged_head_and_no_server_intent(self):
        self.pr();self.q.enqueue(module.REPOS[0],1,self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?",(self.approved,))
        self.q.reconcile(1,retry=False)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'merging')
        self.data['server_queued']=True;self.save();self.q.reconcile(1,retry=True)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'merging')
        self.load();self.data['server_queued']=False;self.save();self.q.reconcile(1,retry=True)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0],'pending')

    def test_imported_completed_merge_reconciles_without_replaying_refresh(self):
        self.pr();self.pr(2)
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=True,state='closed',merge_commit_sha=self.merge_sha);self.save()
        self.git('update-ref','refs/heads/main',self.merge_sha)
        self.q.enqueue(module.REPOS[0],1,self.approved);self.q.enqueue(module.REPOS[0],2,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge')[0][2],'2')
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=1').fetchone()[0],'done')

    def test_successful_archive_keeps_inputs_and_redirects_enqueue(self):
        self.pr();self.q.enqueue(module.REPOS[0],1,self.approved);self.q.tick();self.q.tick()
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-queue.txt').write_text(f'{module.REPOS[1]} 2 {self.approved} late append\n')
        (legacy/'merge-one.sh').write_text('old')
        self.q.archive_legacy(legacy)
        self.assertTrue((legacy/'_to_delete/merge-one.sh').exists())
        self.assertTrue((legacy/'merge-queue.txt').exists())
        self.assertIn('tools/merge_queue/main.py',(legacy/'merge-enqueue.sh').read_text())
        self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0],2)


    def cli(self, *args):
        return subprocess.run([sys.executable, str(ENTRY), '--state', str(self.state), *args],
                              text=True, capture_output=True)

    def test_legacy_import_recovers_attempted_entries_ignoring_pointers(self):
        legacy=self.root/'legacy';legacy.mkdir()
        line=f'{module.REPOS[0]} 1 {self.approved} old note\n'
        (legacy/'merge-queue.txt').write_text(line+line+f'{module.REPOS[1]} 2 {self.approved} second\n')
        (legacy/'merge-queue.done').write_text(f'{module.REPOS[1]} 2 {self.approved} attempted\n'+line)
        (legacy/'merge-queue.carr-system.ptr').write_text('99')
        self.q.import_legacy(legacy)
        self.q.import_legacy(legacy)
        rows=self.q.db.execute('SELECT repo,pr,phase FROM entries ORDER BY id').fetchall()
        self.assertEqual([tuple(r) for r in rows],[(module.REPOS[0],1,'pending'),(module.REPOS[1],2,'pending')])
        self.assertTrue((self.state/'legacy-import.json').exists())

    def test_outdated_draft_with_blocked_status_is_refreshed(self):
        self.pr(state='blocked', draft=True)
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertTrue(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_unknown_pr_does_not_prevent_other_postmerge_updates(self):
        self.pr(1,state='unknown');self.pr(2,state='behind')
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertTrue(any('/pulls/2/update-branch' in str(a) for a in self.load()['calls']))

    def test_single_running_agent_owns_the_state(self):
        p=subprocess.Popen([sys.executable,str(ENTRY),'--state',str(self.state),'run','--no-discover','--poll','0.05'])
        try:
            import time
            time.sleep(.2)
            result=self.cli('run','--no-discover','--poll','0.05')
            self.assertNotEqual(result.returncode,0)
            self.assertIsNone(p.poll())
        finally:
            p.terminate();p.wait(timeout=5)

    def test_handover_refuses_to_start_without_hold_migration_receipt(self):
        legacy=self.root/'legacy';legacy.mkdir()
        with self.assertRaises(RuntimeError):
            self.q.run(poll=.01,discover=False,legacy=legacy)

    def test_install_plan_renders_template_without_activating_agent(self):
        self.q.root=ROOT
        target=self.root/'LaunchAgents/com.carr.merge-queue.plist'
        self.q.install_agent(target,apply=False)
        import plistlib
        p=plistlib.loads(target.read_bytes())
        self.assertEqual(p['WorkingDirectory'],str(ROOT))
        self.assertNotIn('{{REPO}}',target.read_text())

    def test_hold_migration_preserves_registry_allowlist_and_titles(self):
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-holds.txt').write_text(f'{module.REPOS[1]} Leads waits for cleanup\n')
        (legacy/'registry-merge-hold.txt').write_text('1499\n1550\n1481\n')
        self.pr(1);self.pr(1499);self.pr(3,repo=module.REPOS[1]);self.pr(4,repo=module.REPOS[1])
        for n in (1,1499): self.data['prs'][module.REPOS[0]+f'#{n}']['files']=[{'filename':'migrations/100.sql'}]
        self.data['prs'][module.REPOS[1]+'#3']['title']='Leads workspace'
        self.data['prs'][module.REPOS[1]+'#4']['title']='please do_not_merge this'
        self.save()
        plan=self.q.migrate_holds(legacy,apply=False)
        self.assertEqual({(r['repo'],r['pr']) for r in plan},{(module.REPOS[0],1),(module.REPOS[1],3),(module.REPOS[1],4)})
        self.q.migrate_holds(legacy,apply=True)
        self.assertTrue(self.load()['prs'][module.REPOS[0]+'#1']['labels'])
        self.assertFalse(self.data['prs'][module.REPOS[0]+'#1499']['labels'])

    def test_archive_refuses_without_confirmed_new_agent_merge(self):
        legacy=self.root/'legacy';legacy.mkdir()
        (legacy/'merge-one.sh').write_text('old')
        with self.assertRaises(RuntimeError): self.q.archive_legacy(legacy)
        self.assertTrue((legacy/'merge-one.sh').exists())

    def test_launchd_keepalive_has_throttle_and_no_interval_restart(self):
        import plistlib
        p=plistlib.loads((ROOT/'ops/launchd/com.carr.merge-queue.plist').read_bytes())
        self.assertTrue(p['KeepAlive'])
        self.assertGreaterEqual(p['ThrottleInterval'],30)
        self.assertNotIn('StartInterval',p)
        self.assertIn('run',p['ProgramArguments'])

    def test_empty_queue_idles_without_exiting(self):
        p = subprocess.Popen([sys.executable, str(ENTRY), '--state', str(self.state),
                              'run', '--poll', '0.05', '--no-discover'])
        try:
            import time
            time.sleep(.2)
            self.assertIsNone(p.poll(), 'an empty queue must remain alive')
        finally:
            p.terminate()
            p.wait(timeout=5)

if __name__ == '__main__':
    unittest.main()
