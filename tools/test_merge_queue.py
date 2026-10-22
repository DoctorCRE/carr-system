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
 save();print(failure.get('stderr', f"gh: Forbidden (HTTP {failure.get('status',403)})"),file=sys.stderr);sys.exit(1)
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
 if d.get('merge_landed_error'): save();print('gh: response lost after merge (HTTP 502)',file=sys.stderr);sys.exit(1)
 save();sys.exit()
raise AssertionError(a)
'''

spec = importlib.util.spec_from_file_location("merge_queue", ENTRY)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class RequiredChecksPendingTests(unittest.TestCase):
    def run_checks(self, returncode, stdout, stderr):
        done = subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)
        with patch.object(module.subprocess, 'run', return_value=done):
            return module.command(['gh', 'pr', 'checks', '7', '-R', 'o/r', '--required', '--json', 'bucket'])

    def test_unreported_required_checks_read_as_none_yet(self):
        # The required aggregate check reports only when CI finishes; until then gh exits 1 with no output.
        out = self.run_checks(1, '', "no required checks reported on the 'feature' branch\n")
        self.assertEqual(json.loads(out), [])

    def test_other_checks_failures_still_raise(self):
        with self.assertRaises(module.CommandFailed):
            self.run_checks(1, '', 'some other failure\n')


class QueueTests(unittest.TestCase):
    def test_work_card_consumes_verified_merge_after_lost_response(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        command = module.command
        def lost_response(argv, **kwargs):
            result = command(argv, **kwargs)
            if argv[:3] == ['gh', 'pr', 'merge']:
                raise module.CommandFailed('lost response')
            return result
        with patch.object(module, 'command', side_effect=lost_response):
            self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT outcome,merged_commit FROM entries').fetchone()[:],
                         ('merged', self.merge_sha))
        self.restart()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            card = work.get(f'{module.REPOS[0]}#1@{self.updated}')
            self.assertEqual(card['stage'], 'done')
            self.assertEqual(card['merge_receipts'], [dict(repo=module.REPOS[0], number=1, head=self.updated)])
            self.assertEqual(work.merge_order()['entries'], [])
        self.assertEqual(len(self.calls('merge')), 1)

    def test_work_card_records_each_verified_pin_once_while_other_pin_waits(self):
        self.pr(1, head=self.updated)
        self.pr(2, head=self.updated, draft=True)
        self.q.enqueue = module.Queue.enqueue.__get__(self.q)
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.enqueue('multi', desk='multi', task='synthetic merge', actor='test')
            claim = work.claim(eligible_desks={'multi'}, actor='test', owner={})
            work.finish('multi', claim['claim'], {'status': 'completed'}, actor='test')
            work.update('multi', actor='test', reason='fixture pins', prs=[
                dict(repo=module.REPOS[0], number=n, head=self.updated) for n in (1, 2)])
            work.transition('multi', 'merging', actor='test', reason='fixture reviewed')
        self.q.tick()
        self.q.tick()
        self.restart()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            card = work.get('multi')
            self.assertEqual(card['stage'], 'merging')
            self.assertEqual(card['merge_receipts'], [dict(repo=module.REPOS[0], number=1, head=self.updated)])
            self.assertEqual(len([e for e in work.export()['history'] if e['kind'] == 'merge_completed']), 1)
        self.assertEqual(len(self.calls('merge')), 1)

    def test_later_regenerated_pin_advances_after_first_pr_merges(self):
        card_id = self.prepare_pinned_head()
        self.pr(2, head=self.updated)
        self.q.enqueue = module.Queue.enqueue.__get__(self.q)
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.update(card_id, actor='test', reason='first PR precedes regenerating PR', prs=[
                dict(repo=module.REPOS[0], number=2, head=self.updated),
                dict(repo=module.REPOS[0], number=1, head=self.approved)])
        self.load()['required'] = [{'bucket': 'pass'}]
        self.save()
        self.q.tick()
        self.q.tick()
        self.load()['required'] = [{'bucket': 'pending'}]
        self.save()
        self.restart()
        self.q.tick()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            card = work.get(card_id)
            self.assertEqual(card['prs'][1]['head'], self.updated)
            self.assertEqual(card['stage'], 'merging')
            self.assertEqual(card['merge_receipts'], [dict(repo=module.REPOS[0], number=2, head=self.updated)])
            self.assertEqual(len([e for e in work.export()['history'] if e['kind'] == 'pin_regenerated']), 1)
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE pr=1 AND approved=?',
                                         (self.updated,)).fetchone()[0], 'waiting_ci')

    def test_legacy_card_regeneration_requires_manual_repin_after_first_merge(self):
        card_id = self.prepare_pinned_head()
        self.pr(2, head=self.updated)
        self.q.enqueue = module.Queue.enqueue.__get__(self.q)
        first = dict(repo=module.REPOS[0], number=2, head=self.updated)
        later = dict(repo=module.REPOS[0], number=1, head=self.approved)
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.update(card_id, actor='test', reason='legacy multi-PR card', prs=[first, later])
            work.begin_merge(card_id, pin=first, actor='test', reason='first merge started')
            with work.db:
                work.db.execute("UPDATE items SET data=json_remove(data, '$.merge_inflight') WHERE card_id=?",
                                (card_id,))
        self.load()['required'] = [{'bucket': 'pass'}]
        self.save()
        self.q.tick()
        self.q.tick()
        self.load()['required'] = [{'bucket': 'pending'}]
        self.save()
        self.restart()
        self.q.tick()
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE pr=1 AND approved=?',
                                (self.approved,)).fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('blocked', 'needs_repin'))
        event = self.q.db.execute('SELECT * FROM events WHERE entry_id=? ORDER BY id DESC',
                                  (row['id'],)).fetchone()
        self.assertEqual(event['detail'], 'legacy_ownership')
        action = dict(self.q.db.execute("SELECT * FROM actions WHERE kind='regen' AND pr=1").fetchone())
        receipt = Path(action['receipt']).read_text()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            card = work.get(card_id)
            self.assertEqual(card['prs'], [first, later])
            self.assertEqual(card['merge_receipts'], [first])
            self.assertTrue(card['active'])
            self.assertFalse(any(e['kind'] == 'pin_regenerated' for e in work.export()['history']))
            work.finish_merge(card_id, repo=later['repo'], pr=1, head=self.approved, merged=False,
                              actor='human', reason='provider confirms no pending merge on remaining legacy pin')
            work.update(card_id, actor='human', reason='manual re-pin after reconciliation',
                        prs=[{**later, 'head': self.updated}])
            work.requeue(card_id, stage='review', actor='human', reason='review manual re-pin')
            work.transition(card_id, 'merging', actor='human', reason='reviewed prepared head')
        self.restart()
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE pr=1 AND approved=?',
                                         (self.updated,)).fetchone()[0], 'waiting_ci')
        self.assertEqual(Path(action['receipt']).read_text(), receipt)
        self.assertEqual(self.q.db.execute("SELECT attempts FROM actions WHERE kind='regen'").fetchone()[0], 1)
        self.assertEqual(len(self.calls('merge')), 1)

    def test_verified_merge_survives_later_provider_read_failure(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        self.data = self.load()
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls?state=open&per_page=100&page=1'], malformed=True)
        self.save()
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'merged')
        self.data = self.load()
        self.data['api_failure'] = dict(path=f'repos/{module.REPOS[0]}/pulls/1', status=404)
        self.save()
        for _ in range(module.MAX_ATTEMPTS + 1):
            self.restart()
            self.q.tick()
            self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'merged')
        self.assertEqual(len(self.calls('merge')), 1)

    def test_verified_merge_never_reissues_after_contradictory_readback(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        self.data = self.load()
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls?state=open&per_page=100&page=1'], malformed=True)
        self.save()
        self.q.tick()
        self.data = self.load()
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=False, state='open')
        self.save()
        self.restart()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'merged')

    def test_status_reconciles_while_daemon_holds_runner_lock(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='blocked',outcome='github_exhausted'")
        with self.q.runner_lock():
            result = subprocess.run([sys.executable, str(ENTRY), '--state', str(self.state),
                                     '--root', str(self.root), 'status'],
                                    text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)[0]['outcome'], 'merged')
        self.assertEqual(len(self.calls('merge')), 1)

    def test_merge_errors_reconcile_landed_commit_before_failure_handling(self):
        command = module.command
        errors = (module.CommandFailed('lost response'), module.ActionRejected('HTTP 403'),
                  module.MergeRejected('provider rejection'), module.WaitExpired('response timeout'),
                  module.GitHubExhausted('retry cap'))
        for n, error in enumerate(errors, 1):
            with self.subTest(error=type(error).__name__):
                self.pr(n, head=self.updated)
                self.q.enqueue(module.REPOS[0], n, self.updated)
                def lost_response(argv, **kwargs):
                    result = command(argv, **kwargs)
                    if argv[:3] == ['gh', 'pr', 'merge']:
                        raise error
                    return result
                with patch.object(module, 'command', side_effect=lost_response):
                    self.q.tick()
                self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE pr=?', (n,)).fetchone()[0], 'merged')
                self.restart()
                self.q.tick()
                self.assertEqual(len(self.calls('merge')), n)

    def test_post_merge_refresh_exhaustion_preserves_success_across_restart(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        self.data = self.load()
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls?state=open&per_page=100&page=1'], malformed=True)
        self.save()
        for _ in range(module.MAX_ATTEMPTS + 1):
            self.q.tick()
            self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'merged')
            self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'done')
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.q.db.execute("SELECT COUNT(*) FROM events WHERE outcome='refresh_exhausted'").fetchone()[0], 1)

    def test_expired_retry_deadline_does_not_hide_landed_merge(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        with self.q.db:
            self.q.db.execute('INSERT INTO waits(owner,kind,deadline) VALUES(?,?,?)', ('entry:1', 'github_retry', 1))
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:], ('done', 'merged'))
        self.assertEqual(len(self.calls('merge')), 1)

    def test_status_accepts_approved_patch_when_main_advanced(self):
        self.pr()
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=True, state='closed', merge_commit_sha=self.merge_sha)
        self.save()
        self.git('update-ref', 'refs/heads/main', self.merge_sha)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='blocked',outcome='github_exhausted'")
        self.assertNotEqual(self.git('rev-parse', self.approved+'^{tree}'), self.git('rev-parse', self.merge_sha+'^{tree}'))
        self.assertEqual(self.q.status()[0]['outcome'], 'merged')
        self.assertEqual(self.q.status()[0]['phase'], 'done')
        self.assertEqual(self.calls('merge'), [])

    def test_status_rejects_unmerged_or_unproven_commits(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.git('update-ref', 'refs/heads/main', self.merge_sha)
        cases = {
            'open': dict(merged=False, state='open'),
            'closed_unmerged': dict(merged=False),
            'inconsistent_state': dict(state='open'),
            'missing_commit': dict(merge_commit_sha=None),
            'invalid_commit': dict(merge_commit_sha='invalid'),
            'off_main': dict(merge_commit_sha=self.changed),
            'wrong_head': dict(head={'sha': self.changed, 'ref': 'branch-1'}),
            'wrong_base': dict(base={'sha': self.main, 'ref': 'other'}),
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                self.data = self.load()
                p = self.pr(head=self.updated)
                p.update(merged=True, state='closed', merge_commit_sha=self.merge_sha)
                p.update(changes)
                self.save()
                with self.q.db:
                    self.q.db.execute("UPDATE entries SET phase='blocked',outcome='github_exhausted'")
                self.assertEqual(self.q.status()[0]['outcome'], 'github_exhausted')
        self.assertEqual(self.calls('merge'), [])

    def test_tick_does_not_accept_inconsistent_merged_state(self):
        self.pr(head=self.updated)
        self.data['prs'][module.REPOS[0]+'#1'].update(merged=True, state='open', merge_commit_sha=self.merge_sha)
        self.save()
        self.git('update-ref', 'refs/heads/main', self.merge_sha)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        row = self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()
        self.assertNotEqual(row['phase'], 'done')
        self.assertNotIn(row['outcome'], ('merged', 'already_merged'))
        self.assertEqual(self.calls('merge'), [])

    def test_status_rejects_wrong_content_on_main_including_whitespace(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        for content in ('unreviewed\n', ' reviewed\n'):
            with self.subTest(content=content):
                (self.remote / 'file.txt').write_text(content)
                self.git('add', 'file.txt')
                tree = self.git('write-tree').strip()
                wrong = self.git('commit-tree', tree, '-p', self.main, '-m', 'wrong merge').strip()
                self.git('update-ref', 'refs/heads/main', wrong)
                self.data = self.load()
                self.data['prs'][module.REPOS[0]+'#1'].update(merged=True, state='closed', merge_commit_sha=wrong)
                self.save()
                with self.q.db:
                    self.q.db.execute("UPDATE entries SET phase='exhausted',outcome='github_exhausted'")
                self.assertEqual(self.q.status()[0]['outcome'], 'github_exhausted')
        self.assertEqual(self.calls('merge'), [])

    def test_status_recovers_after_read_timeout_without_rearming_merge(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='blocked',outcome='github_exhausted',github_attempts=4")
        command = module.command
        def timeout_read(argv, **kwargs):
            if argv[:3] == ['gh', 'api', f'repos/{module.REPOS[0]}/pulls/1']:
                raise module.WaitExpired('read timeout')
            return command(argv, **kwargs)
        with patch.object(module, 'command', side_effect=timeout_read):
            self.assertEqual(self.q.status()[0]['outcome'], 'github_exhausted')
        with patch.object(module.time, 'time', return_value=time.time() + module.BOUNDS['github_retry']['seconds'] + 1):
            row = self.q.status()[0]
        self.assertEqual((row['outcome'], row['merge_attempts'], row['github_attempts']), ('merged', 1, 4))
        self.assertEqual(len(self.calls('merge')), 1)

    def test_status_reconciles_stale_failures_without_github_mutations(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        for phase in ('blocked', 'exhausted'):
            with self.subTest(phase=phase):
                with self.q.db:
                    self.q.db.execute('UPDATE entries SET phase=?,outcome=?,github_attempts=?',
                                      (phase, 'github_exhausted', module.MAX_ATTEMPTS))
                before = len(self.load()['calls'])
                result = subprocess.run([sys.executable, str(ENTRY), '--state', str(self.state),
                                         '--root', str(self.root), 'status'],
                                        text=True, capture_output=True, check=True)
                row = json.loads(result.stdout)[0]
                self.assertEqual(row['outcome'], 'merged')
                self.assertIn(row['phase'], ('refreshing', 'done'))
                calls = self.load()['calls'][before:]
                self.assertTrue(calls)
                self.assertTrue(all(a[:2] == ['api', f'repos/{module.REPOS[0]}/pulls/1'] for a in calls))
                self.assertEqual(len(self.calls('merge')), 1)

    def test_exhausted_reads_after_landed_merge_preserve_merged(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        with self.q.db:
            self.q.db.execute('UPDATE entries SET github_attempts=?', (module.MAX_ATTEMPTS,))
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries').fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('done', 'merged'))
        self.assertEqual(len(self.calls('merge')), 1)

    def test_error_after_merge_lands_records_merged_without_remerging(self):
        self.pr(head=self.updated)
        self.data['merge_landed_error'] = True
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries').fetchone()
        self.assertEqual(row['outcome'], 'merged')
        self.assertIn(row['phase'], ('refreshing', 'done'))
        self.restart()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
    def test_submitter_preserves_work_card_priority_without_authorizing_unbound_pr(self):
        for number in (1, 2, 3):
            self.pr(number, head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.enqueue(module.REPOS[0], 2, self.updated)
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            work.prioritize(f'{module.REPOS[0]}#2@{self.updated}', actor='test', reason='reviewed priority')
        self.q.enqueue = module.Queue.enqueue.__get__(self.q)
        self.q.submit(module.REPOS[0], [1, 2, 3])
        self.q.tick()
        self.assertEqual([call[2] for call in self.calls('merge')], ['2'])
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual([pin['pr'] for pin in work.merge_order()['entries']], [2, 1])
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE pr=3').fetchone()[0], 'pending')

    def test_pending_checks_exit_eight_waits_without_spending_retries(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        gh = self.root / 'bin/gh'
        gh.write_text(gh.read_text().replace("if a[:2]==['pr','checks']: emit(d.get('required',[{'bucket':'pass'}])); sys.exit()",
            "if a[:2]==['pr','checks']: emit([{'bucket':'pending'}]); sys.exit(8)"))
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries').fetchone()
        self.assertEqual((row['outcome'], row['github_attempts'], row['transient_attempts']), ('waiting_ci', 0, 0))
        self.assertEqual(self.calls('merge'), [])

    def test_exhausted_listing_for_one_entry_does_not_poison_next_entry(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        self.data = self.load()
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls?state=open&per_page=100&page=1'], malformed=True)
        self.save()
        for _ in range(module.MAX_ATTEMPTS):
            with self.q.db:
                self.q.db.execute('UPDATE entries SET retry_at=0')
            self.q.tick()
        self.data = self.load()
        self.data.pop('gh_failure')
        self.save()
        self.pr(2, head=self.updated)
        self.q.enqueue(module.REPOS[0], 2, self.updated)
        self.q.tick()
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE pr=2').fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('done', 'merged'))

    def test_held_entry_can_resume_after_entry_window_without_rearming(self):
        self.pr(head=self.updated, held=True)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            clock.return_value += module.BOUNDS['entry']['seconds'] + 1
            self.pr(head=self.updated)
            self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)

    def test_ci_observation_restarts_after_75_minute_hold(self):
        self.assert_observation_restarts_after_park({'held': True}, 'ci')

    def test_ci_observation_restarts_after_75_minute_draft(self):
        self.assert_observation_restarts_after_park({'draft': True}, 'ci')

    def test_ci_observation_restarts_after_75_minute_stack(self):
        self.assert_observation_restarts_after_park({'base': 'parent'}, 'ci')

    def test_mergeability_observation_restarts_after_75_minute_hold(self):
        self.assert_observation_restarts_after_park({'held': True}, 'mergeability')

    def test_mergeability_observation_restarts_after_75_minute_draft(self):
        self.assert_observation_restarts_after_park({'draft': True}, 'mergeability')

    def test_mergeability_observation_restarts_after_75_minute_stack(self):
        self.assert_observation_restarts_after_park({'base': 'parent'}, 'mergeability')

    def assert_observation_restarts_after_park(self, parked, observation):
        state = 'unknown' if observation == 'mergeability' else 'clean'
        waiting = 'waiting_mergeability' if observation == 'mergeability' else 'waiting_ci'
        self.pr(head=self.updated, state=state)
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], waiting)
            clock.return_value += 1
            self.pr(head=self.updated, state=state, **parked)
            self.q.tick()
            clock.return_value += 75 * 60
            self.restart()
            self.pr(head=self.updated, state=state)
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries').fetchone()
            self.assertEqual((row['phase'], row['outcome']), ('pending', waiting))
            clock.return_value += module.BOUNDS[observation]['seconds']
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries').fetchone()
            expected = ('exhausted', 'ci_timeout') if observation == 'ci' else ('blocked', 'waiting_mergeability_timeout')
            self.assertEqual((row['phase'], row['outcome']), expected)
        self.assertEqual(self.calls('merge'), [])

    def test_stale_dirty_current_head_waits_for_ci_without_transport_retries(self):
        self.pr(head=self.updated, state='dirty')
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries').fetchone()
        self.assertEqual((row['outcome'], row['transient_attempts']), ('waiting_ci', 0))
        self.assertEqual(self.calls('merge'), [])

    def test_mergeability_observation_restarts_after_waiting_its_turn(self):
        self.assert_observation_restarts_after_waiting_its_turn('mergeability')

    def test_ci_observation_restarts_after_waiting_its_turn(self):
        self.assert_observation_restarts_after_waiting_its_turn('ci')

    def assert_observation_restarts_after_waiting_its_turn(self, observation):
        state = 'unknown' if observation == 'mergeability' else 'clean'
        waiting = 'waiting_mergeability' if observation == 'mergeability' else 'waiting_ci'
        self.pr(held=True, head=self.updated)
        self.pr(2, head=self.updated, state=state)
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.enqueue(module.REPOS[0], 2, self.updated)
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE pr=2').fetchone()[0], waiting)
            clock.return_value += 1
            self.pr(head=self.updated)
            self.q.tick()
            clock.return_value += 75 * 60
            self.restart()
            self.pr(head=self.updated, held=True)
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries WHERE pr=2').fetchone()
            self.assertEqual((row['phase'], row['outcome']), ('pending', waiting))
            clock.return_value += module.BOUNDS[observation]['seconds']
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries WHERE pr=2').fetchone()
            expected = ('exhausted', 'ci_timeout') if observation == 'ci' else ('blocked', 'waiting_mergeability_timeout')
            self.assertEqual((row['phase'], row['outcome']), expected)
        self.assertEqual(self.calls('merge'), [])

    def test_post_merge_provider_computation_waits_without_retry_exhaustion(self):
        self.pr(head=self.updated)
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        self.load()
        self.pr(2, head=self.updated, base='branch-1', state='unknown')
        self.data['retarget_state'] = 'unknown'
        self.save()
        for _ in range(module.MAX_ATTEMPTS + 1):
            self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE pr=1').fetchone()
        self.assertEqual((row['phase'], row['outcome'], row['transient_attempts']), ('refreshing', 'merged', 0))
        self.load()
        self.data['prs'][module.REPOS[0]+'#2']['mergeable_state'] = 'clean'
        self.save()
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE pr=1').fetchone()[0], 'done')
        self.assertEqual(len(self.calls('merge')), 1)

    def test_discovery_read_budget_recovers_after_cooldown_without_replaying_actions(self):
        self.pr(head=self.updated)
        self.data['gh_failure'] = dict(prefix=['api'], malformed=True)
        self.save()
        with patch.object(module.time, 'time', return_value=10000) as clock:
            for _ in range(module.MAX_ATTEMPTS):
                self.q.discover()
            failed_calls = len(self.load()['calls'])
            self.q.discover()
            self.assertEqual(len(self.load()['calls']), failed_calls)
            self.data.pop('gh_failure')
            self.save()
            clock.return_value += module.BOUNDS['github_retry']['seconds'] + 1
            self.restart()
            self.q.discover()
        self.assertEqual(self.q.db.execute('SELECT approved FROM entries WHERE pr=1').fetchone()[0], self.updated)
        self.assertEqual(self.calls('merge'), [])

    def test_timed_out_discovery_read_recovers_after_cooldown_across_restart(self):
        errors = (module.GitHubBudgetLockTimeout('budget lock deadline'),
                  subprocess.TimeoutExpired('gh', 120), module.WaitExpired('read deadline'))
        command = module.command
        listing = ['gh', 'api', f'repos/{module.REPOS[0]}/pulls?state=open&per_page=100&page=1']
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.pr(head=self.updated)
                with self.q.db:
                    self.q.db.execute('DELETE FROM entries')
                    self.q.db.execute('DELETE FROM github_failures')
                calls = []
                def request(argv, **kwargs):
                    if argv[:3] == listing:
                        calls.append(clock.return_value)
                        if len(calls) == 1:
                            raise error
                    return command(argv, **kwargs)
                with patch.object(module.time, 'time', return_value=10000) as clock, \
                        patch.object(module, 'command', side_effect=request):
                    self.q.discover()
                    self.restart()
                    clock.return_value += module.BOUNDS['github_retry']['seconds'] - 1
                    self.q.discover()
                    self.assertEqual(len(calls), 1, 'Discovery retried before its cooldown')
                    clock.return_value += 1
                    self.q.discover()
                row = self.q.db.execute('SELECT approved FROM entries WHERE pr=1').fetchone()
                self.assertIsNotNone(row, 'Discovery remained stopped after its cooldown')
                self.assertEqual(row['approved'], self.updated)
                self.assertEqual(len(calls), 2)
                self.assertEqual(self.calls('merge'), [])

    def test_timeout_stops_writes_and_entry_owned_reads_after_cooldown(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        cases = ((False, 'automatic', None), (True, 'automatic', entry),
                 (True, f'post-merge:{entry}', None))
        for read, scope, owner in cases:
            with self.subTest(read=read, scope=scope, entry=owner), \
                    self.q.github_scope(scope), \
                    patch.object(module.time, 'time', return_value=10000) as clock, \
                    patch.object(module, 'command', side_effect=module.WaitExpired('deadline')) as command:
                self.q._github_entry = owner
                args = ('api', f'repos/{module.REPOS[0]}/pulls/1') + (() if read else ('-X', 'PATCH'))
                with self.assertRaises(module.WaitExpired):
                    self.q.gh(*args)
                clock.return_value += module.BOUNDS['github_retry']['seconds'] + 1
                with self.assertRaisesRegex(module.WaitExpired, 'stopped; reconcile'):
                    self.q.gh(*args)
                self.assertEqual(command.call_count, 1, 'Stopped operation was replayed')

    def test_daemon_updated_head_is_discovered_and_waits_for_its_checks(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.tick()
        self.q.discover()
        self.load()
        self.data['required'] = [{'bucket':'pending'}]
        self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        row = self.q.db.execute('SELECT * FROM entries WHERE approved=?', (self.updated,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['outcome'], 'waiting_ci')
        self.load()
        self.data['required'] = [{'bucket':'pass'}]
        self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge')[0][-1], self.updated)

    def test_blocked_outdated_head_is_updated_before_red_checks(self):
        self.pr(state='blocked')
        self.data['required'] = [{'bucket':'fail'}]
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'updating')
        self.assertEqual(len([call for call in self.load()['calls'] if 'update-branch' in str(call)]), 1)
        self.assertEqual(self.calls('checks'), [])
        self.assertEqual(self.calls('merge'), [])

    def test_required_checks_must_pass_without_skips(self):
        queue = object.__new__(module.Queue)
        for buckets, expected in ((['pass'], True), (['pass', 'skipping'], False),
                                  (['skipping'], False), ([], False), (['pending'], False)):
            with self.subTest(buckets=buckets), patch.object(queue, 'gh', return_value=[
                    {'bucket': b} for b in buckets]):
                self.assertEqual(queue.green('jbookout/carr-system', 7, 'a' * 40), expected)

    def setUp(self):
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
        self.env['CARR_WORK_QUEUE_DB'] = str(self.root / 'work.sqlite3')
        self.env['PROGRESS_BOARD_LOCAL_ONLY'] = '1'
        self.env['FAKE_GH_STATE'] = str(self.fake_state)
        self.env['PATH'] = str(bin_dir) + os.pathsep + self.env['PATH']
        self.env['CARR_GITHUB_READ_BUDGET'] = str(self.root / 'github-budget.json')
        self.env['GH_LIMITER_DIR'] = str(self.root / 'legacy-budget')
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.q = module.Queue(self.state, self.root, gap=0)
        self.bind_work_queue()
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

        (self.root / 'tools/prepare_pr.py').write_text(
            'import json,sys\nprint(json.dumps({"head":sys.argv[sys.argv.index("--head")+1],"changed":False}))\n')

    def bind_work_queue(self):
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])):
            pass
        enqueue = self.q.enqueue
        def projected_enqueue(repo, n, head, note='', *, rearm=True):
            entry = enqueue(repo, n, head, note, rearm=rearm)
            if note == 'work queue projection':
                return entry
            card_id = f'{repo}#{n}@{head}'
            with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
                if not any(c['card_id'] == card_id for c in work.export()['items']):
                    work.enqueue(card_id, desk=card_id, task='synthetic merge', actor='test')
                    claim = work.claim(eligible_desks={card_id}, card_id=card_id, actor='test', owner={})
                    work.finish(card_id, claim['claim'], {'status': 'completed'}, actor='test')
                    work.update(card_id, actor='test', reason='fixture pin', prs=[
                        {'repo': repo, 'number': n, 'head': head}])
                    work.transition(card_id, 'merging', actor='test', reason='fixture reviewed')
            return entry
        self.q.enqueue = projected_enqueue

    def prepare_pinned_head(self):
        self.pr()
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        (self.root / 'tools/prepare_pr.py').write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            'args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n'
            'prepared = ' + repr(self.updated) + '\n'
            'if prepared == args["--head"]:\n print(json.dumps(dict(head=prepared, changed=False))); sys.exit()\n'
            'receipt = Path(args["--receipt"])\nreceipt.parent.mkdir(parents=True, exist_ok=True)\n'
            'receipt.write_text(json.dumps(dict(head=args["--head"], main=args["--main"], '
            'prepared=prepared, branch=args["--branch"])))\n'
            'state = Path(os.environ["FAKE_GH_STATE"])\ndata = json.loads(state.read_text())\n'
            'data["prs"]["jbookout/carr-system#1"]["head"]["sha"] = prepared\n'
            'state.write_text(json.dumps(data))\n'
            'print(json.dumps(dict(head=prepared, changed=True)))\n')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        return f'{module.REPOS[0]}#1@{self.approved}'

    def test_preparation_push_waits_for_checks_on_the_new_head(self):
        card_id = self.prepare_pinned_head()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.get(card_id)['prs'], [dict(repo=module.REPOS[0], number=1, head=self.updated)])
            event = work.get(card_id)['last_event']
            self.assertEqual(event['kind'], 'pin_regenerated')
            self.assertEqual(event['actor'], 'merge-daemon')
            self.assertEqual(event['from']['head'], self.approved)
            self.assertEqual(event['to']['head'], self.updated)
            self.assertEqual(event['to']['main'], self.main)
        self.restart()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE approved=?',
                                         (self.updated,)).fetchone()[0], 'waiting_ci')
        self.load()['required'] = [{'bucket': 'pass'}]
        self.save()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.calls('merge')[0][-1], self.updated)

    def test_completed_regeneration_recovers_pin_after_restart_once(self):
        card_id = self.prepare_pinned_head()
        self.q.prepare(module.REPOS[0], self.data['prs'][f'{module.REPOS[0]}#1'])
        self.restart()
        self.q.tick()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.get(card_id)['prs'][0]['head'], self.updated)
            self.assertEqual(len([e for e in work.export()['history'] if e['kind'] == 'pin_regenerated']), 1)
        self.assertEqual(self.calls('merge'), [])

    def test_lost_regeneration_response_stays_recoverable_until_reconciled(self):
        card_id = self.prepare_pinned_head()
        script = self.root / 'tools/prepare_pr.py'
        script.write_text(script.read_text() + 'raise SystemExit("response lost after push")\n')
        self.q.tick()
        self.restart()
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE approved=?', (self.approved,)).fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('pending', 'action_uncertain'))
        for _ in range(module.MAX_ATTEMPTS):
            self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE approved=?', (self.approved,)).fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('blocked', 'action_uncertain_exhausted'))
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.get(card_id)['prs'][0]['head'], self.approved)
        key = self.q.db.execute("SELECT key FROM actions WHERE kind='regen'").fetchone()[0]
        self.q.reconcile_action(key, retry=True)
        self.restart()
        self.q.tick()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.get(card_id)['prs'][0]['head'], self.updated)
            self.assertEqual(len([e for e in work.export()['history'] if e['kind'] == 'pin_regenerated']), 1)
        self.assertEqual(self.q.db.execute("SELECT attempts FROM actions WHERE kind='regen'").fetchone()[0], 1)
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE approved=?',
                                         (self.updated,)).fetchone()[0], 'waiting_ci')
        self.assertEqual(self.calls('merge'), [])

    def test_hold_after_lost_regeneration_response_requires_manual_repin(self):
        card_id = self.prepare_pinned_head()
        script = self.root / 'tools/prepare_pr.py'
        script.write_text(script.read_text() + 'raise SystemExit("response lost after push")\n')
        self.q.tick()
        action = dict(self.q.db.execute("SELECT * FROM actions WHERE kind='regen'").fetchone())
        receipt = Path(action['receipt']).read_text()
        self.load()
        self.pr(head=self.updated, held=True)
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE approved=?', (self.approved,)).fetchone()
        self.assertEqual((row['phase'], row['outcome']), ('blocked', 'needs_repin'))
        event = self.q.db.execute('SELECT * FROM events WHERE entry_id=? ORDER BY id DESC',
                                  (row['id'],)).fetchone()
        self.assertIn('held_during_regeneration', event['detail'])
        self.load()
        self.pr(head=self.updated)
        self.q.reconcile_action(action['key'], retry=True)
        self.restart()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as work:
            self.assertEqual(work.get(card_id)['prs'][0]['head'], self.approved)
            self.assertFalse(any(e['kind'] == 'pin_regenerated' for e in work.export()['history']))
            work.update(card_id, actor='human', reason='verified prepared head; manual re-pin', prs=[
                dict(repo=module.REPOS[0], number=1, head=self.updated)])
        self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries WHERE approved=?',
                                         (self.updated,)).fetchone()[0], 'waiting_ci')
        retained = dict(self.q.db.execute("SELECT * FROM actions WHERE kind='regen'").fetchone())
        self.assertEqual((retained['phase'], retained['attempts']), ('done', 1))
        self.assertEqual(Path(retained['receipt']).read_text(), receipt)
        self.assertEqual(self.calls('merge'), [])

    def test_foreign_push_after_regeneration_does_not_advance_pin(self):
        card_id = self.prepare_pinned_head()
        self.q.prepare(module.REPOS[0], self.data['prs'][f'{module.REPOS[0]}#1'])
        self.load()
        self.pr(head=self.changed)
        self.restart()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.get(card_id)['prs'][0]['head'], self.approved)
            self.assertFalse(any(e['kind'] == 'pin_regenerated' for e in work.export()['history']))
        self.assertEqual(self.calls('merge'), [])

    def test_regeneration_without_a_matching_receipt_does_not_advance_pin(self):
        card_id = self.prepare_pinned_head()
        self.q.prepare(module.REPOS[0], self.data['prs'][f'{module.REPOS[0]}#1'])
        path = self.state / 'regen' / f'1-{self.approved}-{self.main}.json'
        path.write_text(json.dumps(dict(head=self.changed, main=self.main, prepared=self.updated, branch='branch-1')))
        self.restart()
        self.q.tick()
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.get(card_id)['prs'][0]['head'], self.approved)
        self.assertEqual(self.calls('merge'), [])

    def test_interrupted_preparation_is_not_replayed(self):
        self.pr()
        marker = self.root / 'preparation-calls'
        (self.root / 'tools/prepare_pr.py').write_text(
            'from pathlib import Path\n'
            + 'with Path(' + repr(str(marker)) + ').open("a") as f: f.write("call\\n")\n'
            + 'raise SystemExit("seeded failure")\n')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.restart()
        self.q.tick()
        self.assertEqual(marker.read_text().splitlines(), ['call'])
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute("SELECT attempts FROM actions WHERE kind='regen'").fetchone()[0], 1)

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
        self.data['comments'][f'{repo}#{n}']=[]
        self.save()
        return p

    def calls(self, verb):
        return [a for a in self.load()['calls'] if a[:2]==['pr',verb]]

    def restart(self):
        self.q.db.close()
        self.q = module.Queue(self.state, self.root, gap=0)
        self.bind_work_queue()
        self.addCleanup(self.q.db.close)


    def test_head_move_after_enqueue_refuses_merge_without_reset(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.pr(head=self.changed)
        self.restart()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        row = self.q.db.execute('SELECT * FROM entries').fetchone()
        self.assertEqual((row['approved'], row['tested'], row['outcome']),
                         (self.approved, None, 'head_moved'))
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            card = work.get(f'{module.REPOS[0]}#1@{self.approved}')
            self.assertEqual(card['prs'], [dict(repo=module.REPOS[0], number=1, head=self.approved)])
            self.assertFalse(any(e['kind'] == 'pin_regenerated' for e in work.export()['history']))

    def test_legacy_review_entry_preserves_approved_head_on_restart(self):
        self.pr(head=self.changed)
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='review' WHERE id=?", (entry,))
            self.q.db.execute('INSERT INTO waits(owner,kind,deadline) VALUES(?,?,?)', (f'entry:{entry}', 'entry', 0))
        self.restart()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'head_moved')
        self.assertFalse(any('/comments' in str(a) for a in self.load()['calls']))

    def test_sigterm_during_checks_prevents_every_later_effect(self):
        self.pr()
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
        peer = module.GitHubReadBudget({'GH_LIMITER_DIR': str(self.root / 'legacy-budget')},
                                       path=q.budget.path, clock=lambda: wall[0])
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
        peer = module.GitHubReadBudget({'GH_LIMITER_DIR': str(self.root / 'legacy-budget')},
                                       path=q.budget.path, clock=lambda: wall[0])
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
        self.q.enqueue(module.REPOS[0], 1, self.approved)
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
        waiting_on_update = []
        def cancel(seconds):
            # Cancel only once the update-branch call is the one waiting for its slot.
            # An earlier read can also wait on a reserved call slot; cancelling there
            # ends the tick before the entry is planned and the actions row never exists.
            if waiting_on_update:
                self.q.stopped = True
            original_sleep(0)
        real_gh = self.q.gh
        def delay_update(*args, **kwargs):
            if 'update-branch' in str(args):
                waiting_on_update.append(True)
                self.q.last_gh = time.monotonic() + 1
            return real_gh(*args, **kwargs)
        with patch.object(self.q, 'gh', side_effect=delay_update), patch.object(module.time, 'sleep', side_effect=cancel):
            self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,attempts FROM actions').fetchone()[:], ('planned', 0))
        self.assertFalse(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_transient_github_backoff_survives_restart_and_recovers(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls/1'],
                                       stderr='HTTP 502: Bad Gateway')
        self.save()
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries').fetchone()
            self.assertGreaterEqual(row['retry_at'], 10096)
            self.assertLessEqual(row['retry_at'], 10120)
            self.restart()
            self.q.tick()
            self.assertEqual(len(self.load()['calls']), 1)
            clock.return_value = row['retry_at']
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries').fetchone()
            self.assertGreaterEqual(row['retry_at'] - clock.return_value, 288)
            clock.return_value = row['retry_at']
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries').fetchone()
            self.assertGreaterEqual(row['retry_at'] - clock.return_value, 864)
            self.assertLessEqual(row['retry_at'], 11800)
            self.load(); del self.data['gh_failure']; self.save()
            clock.return_value = row['retry_at']
            self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)

    def test_transient_github_window_expires_without_another_call(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls/1'],
                                       stderr='HTTP 503: unavailable')
        self.save()
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            self.restart()
            clock.return_value = 11800
            self.q.tick()
        self.assertEqual(len(self.load()['calls']), 1)
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'retry_exhausted'))

    def test_gh_failure_log_contains_bounded_redacted_stderr(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        token = 'ghp_' + 'a' * 40
        known = 'opaque-environment-secret'
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls/1'],
                                       stderr='x' * 3000 + ' Bad Gateway ' + token + ' ' + known)
        self.save()
        with patch.dict(os.environ, {'GH_TOKEN': known}):
            self.q.tick()
        self.q.flush_events()
        log = (self.state / 'queue.log').read_text()
        self.assertIn('Bad Gateway', log)
        self.assertIn('[REDACTED]', log)
        self.assertNotIn(token, log)
        self.assertNotIn(known, log)
        detail = json.loads(log.splitlines()[-1])['detail']
        self.assertLess(len(detail), 2300)

    def test_enqueue_rearms_transient_github_block_before_merge_intent(self):
        for n, outcome in enumerate(('github_exhausted', 'discovery_exhausted'), 1):
            with self.subTest(outcome=outcome):
                self.pr(n)
                entry = self.q.enqueue(module.REPOS[0], n, self.approved)
                with self.q.db:
                    self.q.db.execute("UPDATE entries SET phase='blocked',outcome=?,"
                                      "github_attempts=4,read_attempts=4,transient_attempts=4,retry_at=99999999999,"
                                      "merge_attempts=4,ci_since=1,ci_head=? WHERE id=?",
                                      (outcome, self.approved, entry))
                result = self.cli('enqueue', module.REPOS[0], str(n), self.approved, 'try again')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(int(result.stdout), entry)
                row = self.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                self.assertEqual((row['phase'], row['outcome'], row['github_attempts'],
                                  row['read_attempts'], row['transient_attempts'], row['retry_at']),
                                 ('pending', 'requeued', 0, 0, 0, 0))
                self.assertEqual((row['tested'], row['merge_attempts'], row['ci_since'], row['ci_head']),
                                 (None, 4, 1, self.approved))
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])

    def test_submit_retries_same_head_after_premerge_github_exhaustion(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute('UPDATE entries SET github_attempts=? WHERE id=?', (module.MAX_ATTEMPTS, entry))
        self.q.tick()
        row = self.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        self.assertEqual((row['phase'], row['outcome'], row['tested']), ('blocked', 'github_exhausted', None))
        result = self.cli('submit', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0], 'pending')
        self.restart()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.calls('merge')[0][-1], self.approved)

    def test_enqueue_never_rearms_held_or_head_moved_entries(self):
        n = 0
        for phase in ('blocked', 'exhausted'):
            for outcome, held in (('head_moved', False), ('held', False), ('github_exhausted', True)):
                n += 1
                with self.subTest(phase=phase, outcome=outcome, held=held):
                    self.pr(n, held=held)
                    entry = self.q.enqueue(module.REPOS[0], n, self.approved)
                    with self.q.db:
                        self.q.db.execute('UPDATE entries SET phase=?,outcome=?,github_attempts=4 WHERE id=?',
                                          (phase, outcome, entry))
                    self.q.enqueue(module.REPOS[0], n, self.approved)
                    row = self.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
                    self.assertEqual((row['phase'], row['github_attempts']), (phase, 4))
        self.assertEqual(self.calls('merge'), [])

    def test_enqueue_rearms_exhausted_entry_without_resetting_merge_or_ci_caps(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='exhausted',outcome='retry_exhausted',"
                              "transient_attempts=4,github_attempts=4,merge_attempts=4,ci_since=1,ci_head=? WHERE id=?",
                              (self.approved, entry))
        self.assertEqual(self.q.enqueue(module.REPOS[0], 1, self.approved, 'try again'), entry)
        row = self.q.db.execute('SELECT * FROM entries').fetchone()
        self.assertEqual((row['phase'], row['transient_attempts'], row['github_attempts']), ('pending', 0, 0))
        self.assertEqual((row['merge_attempts'], row['ci_since']), (4, 1))
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])

    def test_enqueue_does_not_rearm_exhausted_entry_when_live_head_moved(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='exhausted',outcome='retry_exhausted',"
                              "transient_attempts=4 WHERE id=?", (entry,))
        self.pr(head=self.changed)
        self.assertEqual(self.q.enqueue(module.REPOS[0], 1, self.approved), entry)
        row = self.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        self.assertEqual((row['phase'], row['outcome'], row['transient_attempts']),
                         ('exhausted', 'head_moved', 4))
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])

    def test_discovery_does_not_rearm_exhausted_entries(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='exhausted' WHERE id=?", (entry,))
        self.q.discover()
        self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'exhausted')

    def test_transient_merge_failure_reconciles_before_retry(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.data['gh_failure'] = dict(prefix=['pr', 'merge', '1'], stderr='HTTP 502: Bad Gateway')
        self.save()
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            row = self.q.db.execute('SELECT * FROM entries').fetchone()
            self.assertEqual((row['phase'], row['merge_attempts']), ('merge_rejected', 1))
            self.restart()
            self.load(); del self.data['gh_failure']; self.data['server_queued'] = True; self.save()
            clock.return_value = row['retry_at']
            self.q.tick()
            self.assertEqual(len(self.calls('merge')), 1)
            self.load(); self.data['server_queued'] = False; self.save()
            self.q.tick()
            self.q.tick()
        self.assertEqual(len(self.calls('merge')), 2)
        calls = self.load()['calls']
        merges = [i for i, a in enumerate(calls) if a[:2] == ['pr', 'merge']]
        self.assertTrue(any(a[:2] == ['api', 'graphql'] for a in calls[merges[0]+1:merges[1]]))

    def test_enqueue_recovers_exhausted_transient_read_budget(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls/1'],
                                       stderr='HTTP 502: Bad Gateway')
        self.save()
        with patch.object(module.time, 'time', return_value=10000) as clock:
            for _ in range(4):
                self.q.tick()
                clock.return_value = self.q.db.execute('SELECT retry_at FROM entries').fetchone()[0]
            self.assertEqual(self.q.db.execute('SELECT phase FROM entries').fetchone()[0], 'exhausted')
            self.assertEqual(len(self.load()['calls']), 4)
            self.load(); del self.data['gh_failure']; self.save()
            self.assertEqual(self.q.enqueue(module.REPOS[0], 1, self.approved), entry)
            self.restart()
            self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)

    def test_ci_wait_keeps_75_minutes_after_transient_recovery(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.data['gh_failure'] = dict(prefix=['api', f'repos/{module.REPOS[0]}/pulls/1'],
                                       stderr='HTTP 502: Bad Gateway')
        self.save()
        with patch.object(module.time, 'time', return_value=10000) as clock:
            self.q.tick()
            self.load(); del self.data['gh_failure']; self.data['required'] = [{'bucket':'pending'}]; self.save()
            clock.return_value = 10120
            self.q.tick()
            self.restart()
            clock.return_value = 11801
            self.q.tick()
            self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:], ('pending', 'waiting_ci'))
            clock.return_value = 14620
            self.q.tick()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:], ('exhausted', 'ci_timeout'))

    def test_transient_read_failures_have_three_retries(self):
        self.pr(); self.q.enqueue(module.REPOS[0], 1, self.approved)
        with patch.object(module.time, 'time', return_value=10000) as clock:
            for _ in range(8):
                with patch.object(self.q, 'pr', side_effect=RuntimeError('gh api failed (exit 1)')):
                    self.q.tick()
                clock.return_value = max(clock.return_value, self.q.db.execute('SELECT retry_at FROM entries').fetchone()[0])
                self.restart()
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries').fetchone()[:],
                         ('exhausted', 'retry_exhausted'))


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
                    fixture.q.enqueue(repo, 2, fixture.updated); fixture.q.tick()
                    self.assertEqual([a[2] for a in fixture.calls('merge')], ['2'])
                finally:
                    fixture.doCleanups()

    def test_rejected_merge_releases_the_queue_after_provider_readback(self):
        repo = module.REPOS[0]
        for verb in ('merge',):
            with self.subTest(verb=verb):
                fixture = QueueTests(); fixture.setUp()
                try:
                    fixture.pr(1); fixture.pr(2, head=fixture.updated)
                    fixture.data['gh_failure'] = dict(prefix=['pr', verb, '1']); fixture.save()
                    fixture.q.enqueue(repo, 1, fixture.approved)
                    fixture.q.enqueue(repo, 2, fixture.updated)
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


    def assert_rejected_read_is_bounded(self, path, status):
        self.pr(1)
        self.pr(2, head=self.updated)
        self.data['api_failure'] = {'path': path, 'status': status}
        self.save()
        first = self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.enqueue(module.REPOS[0], 2, self.updated)
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
        self.pr(2, state='behind', base='branch-1')
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.data['api_failure'] = {'path': 'repos/jbookout/carr-system/pulls/1', 'status': status}
        self.save()
        with patch.object(module.time, 'time', return_value=time.time()) as clock:
            for _ in range(4):
                self.q.tick()
                clock.return_value = max(clock.return_value, self.q.db.execute('SELECT retry_at FROM entries WHERE id=?', (entry,)).fetchone()[0])
                self.restart()
        self.assertIn(self.q.db.execute('SELECT phase FROM entries WHERE id=?', (entry,)).fetchone()[0],
                      ('blocked', 'exhausted'))
        with self.q.db:
            self.q.db.execute('UPDATE entries SET phase=? WHERE id=?', (phase, entry))
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
        self.assertEqual(self.q.db.execute('SELECT phase,outcome FROM entries WHERE id=?', (entry,)).fetchone()[:], ('refreshing', 'merged'))
        self.load()
        del self.data['api_failure']
        self.save()
        retry_at = self.q.db.execute('SELECT retry_at FROM entries WHERE id=?', (entry,)).fetchone()[0]
        with patch.object(module.time, 'time', return_value=max(time.time(), retry_at)):
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
                         ('refreshing', 'merged'))

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
        with patch.object(self.q, 'pr', side_effect=fail), patch.object(module.time, 'time', return_value=10000) as clock:
            for _ in range(8):
                self.q.tick()
                clock.return_value = max(clock.return_value, self.q.db.execute('SELECT retry_at FROM entries').fetchone()[0])
        self.assertEqual(reads, 2 * module.MAX_ATTEMPTS)
        self.assertEqual(self.q.db.execute('SELECT phase,tested,outcome FROM entries').fetchone()[:],
                         ('exhausted', self.approved, 'retry_exhausted'))
        self.q.reconcile(1)
        self.assertEqual(self.calls('merge'), [])


    def test_behind_update_requires_enqueue_of_new_head_before_merge(self):
        self.pr(state='behind')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.q.tick()
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'head_moved')
        self.q.enqueue(module.REPOS[0], 1, self.updated)
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertEqual(self.calls('merge')[0][-1], self.updated)
        self.assertFalse(any('/comments' in str(a) for a in self.load()['calls']))

    def conflicting_preparation(self, paths):
        self.git('switch', '-q', '-c', 'branch-1', self.approved)
        for path in paths:
            target = self.remote / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('PR bytes\n')
        self.git('add', '--', *paths)
        self.git('commit', '-qm', 'PR conflicts')
        head = self.git('rev-parse', 'HEAD').strip()
        self.git('switch', '-q', 'main')
        for path in paths:
            target = self.remote / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('main bytes\n')
        self.git('add', '--', *paths)
        self.git('commit', '-qm', 'main conflicts')
        self.main = self.git('rev-parse', 'HEAD').strip()
        self.pr(state='dirty', head=head)
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.q.enqueue(module.REPOS[0], 1, head)
        return head

    def install_preparation_generator(self, paths):
        (self.root / 'tools/prepare_pr.py').write_text(
            'import json,os,sys\nfrom pathlib import Path\n'
            'sys.path.insert(0, ' + repr(str(ROOT / 'tools')) + ')\n'
            'from prepare_pr import prepare\n'
            'args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n'
            'def regenerate(repo):\n'
            ' for path in ' + repr(paths) + ':\n'
            '  target = repo / path\n'
            '  assert target.read_text() == "main bytes\\n"\n'
            '  target.write_text("regenerated bytes\\n")\n'
            'sha = prepare(args["--remote"], args["--branch"], args["--head"], '
            'args["--main"], Path(args["--receipt"]), regenerate)\n'
            'state = Path(os.environ["FAKE_GH_STATE"])\ndata = json.loads(state.read_text())\n'
            'data["prs"]["jbookout/carr-system#1"]["head"]["sha"] = sha\n'
            'state.write_text(json.dumps(data))\n'
            'print(json.dumps(dict(head=sha, changed=sha != args["--head"])))\n')

    def test_conflicting_generated_outputs_prepare_and_regenerate_in_first_tick(self):
        paths = ['ops/config/gate-baseline.json', 'db/schema.sql',
                 'mcp-server/src/core-rule-ids.js',
                 'mcp-server/test/f03-design-contract-corpus-plan-postgres.generated.sql',
                 'mcp-server/test/f03-design-contract-corpus-slice-postgres.generated.sql']
        head = self.conflicting_preparation(paths)
        self.install_preparation_generator(paths)
        self.q.tick()
        prepared = self.git('rev-parse', 'branch-1').strip()
        self.assertNotEqual(prepared, head)
        for path in paths:
            self.assertEqual(self.git('show', f'{prepared}:{path}').strip(), 'regenerated bytes')
        self.git('merge-base', '--is-ancestor', head, prepared)
        self.git('merge-base', '--is-ancestor', self.main, prepared)
        with module.WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB']), read_only=True) as work:
            self.assertEqual(work.merge_order()['entries'][0]['head'], prepared)
        self.assertEqual(self.q.db.execute("SELECT COUNT(*) FROM waits WHERE kind='mergeability'").fetchone()[0], 0)
        self.assertFalse(list((self.root / 'out/orch/queue/codex').glob('*.txt')))
        self.assertEqual(self.calls('merge'), [])

    def test_stale_conflicting_result_prepares_without_mergeability_wait(self):
        self.pr(state='dirty')
        self.git('branch', 'branch-1', self.approved)
        self.data['required'] = [{'bucket': 'pending'}]
        self.save()
        self.install_preparation_generator([])
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick()
        self.assertNotEqual(self.git('rev-parse', 'branch-1').strip(), self.approved)
        self.assertEqual(self.q.db.execute("SELECT COUNT(*) FROM waits WHERE kind='mergeability'").fetchone()[0], 0)
        self.assertEqual(self.calls('merge'), [])

    def assert_review_only_conflict(self, path):
        head = self.conflicting_preparation([path])
        self.q.tick()
        entry = self.q.status()[0]
        self.assertEqual((entry['phase'], entry['outcome']), ('conflict', 'conflict'))
        detail = self.q.db.execute('SELECT detail FROM events WHERE entry_id=? ORDER BY id DESC LIMIT 1', (entry['id'],)).fetchone()[0]
        self.assertIn(path, detail)
        self.assertEqual(self.git('rev-parse', 'branch-1').strip(), head)
        self.assertEqual(self.q.db.execute("SELECT COUNT(*) FROM waits WHERE kind='mergeability'").fetchone()[0], 0)
        self.assertEqual(self.calls('merge'), [])

    def test_conflicting_registry_metadata_requires_review_without_waiting(self):
        self.assert_review_only_conflict('mcp-server/src/scac-mutation-registry.current.generated.js')

    def test_conflicting_dependency_lock_requires_review_without_waiting(self):
        self.assert_review_only_conflict('requirements.lock')

    def test_conflicting_source_paths_report_conflict_in_first_tick(self):
        paths = ['source-one.py', 'source-two.py', 'mcp-server/src/core-rule-ids.js']
        head = self.conflicting_preparation(paths)
        self.data['prs'][module.REPOS[0]+'#1']['base']['sha'] = self.base
        self.save()
        self.q.tick()
        entry = self.q.status()[0]
        self.assertEqual((entry['phase'], entry['outcome']), ('conflict', 'conflict'))
        detail = self.q.db.execute('SELECT detail FROM events WHERE entry_id=? ORDER BY id DESC LIMIT 1', (entry['id'],)).fetchone()[0]
        for path in paths[:2]:
            self.assertIn(path, detail)
        self.assertEqual(self.git('rev-parse', 'branch-1').strip(), head)
        self.assertEqual(self.q.db.execute("SELECT COUNT(*) FROM waits WHERE kind='mergeability'").fetchone()[0], 0)
        self.assertEqual(self.q.db.execute("SELECT COUNT(*) FROM actions WHERE kind='regen'").fetchone()[0], 0)
        self.assertEqual(self.calls('merge'), [])

    def test_conflicting_writes_brief_without_resolving(self):
        (self.remote/'file.txt').write_text('main conflict\n')
        self.git('commit','-qam','conflicting main')
        self.main=self.git('rev-parse','HEAD').strip()
        self.pr(state='dirty', head=self.changed)
        self.data['prs'][module.REPOS[0]+'#1']['base']['sha']=self.main
        self.save()
        self.q.enqueue(module.REPOS[0], 1, self.changed)
        self.q.tick()
        briefs=list((self.root/'out/orch/queue/codex').glob('*.txt'))
        self.assertEqual(len(briefs), 1)
        self.assertIn('file.txt', briefs[0].read_text())
        self.assertIn(self.changed, briefs[0].read_text())
        self.assertEqual(self.calls('merge'), [])

    def test_held_is_skipped_by_queue_discover_and_refresh(self):
        self.pr(state='behind', held=True, base='just-merged')
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

    def test_retargeted_draft_refresh_retries_after_unknown_across_restart(self):
        for restart in (False, True):
            with self.subTest(restart=restart):
                n = 3 if restart else 2
                self.pr(n=n, base='branch-1', draft=True)
                self.data['retarget_state'] = 'unknown'
                self.save()
                self.assertFalse(self.q.refresh(module.REPOS[0], 'branch-1'))
                self.load()
                child = self.data['prs'][f'{module.REPOS[0]}#{n}']
                self.assertEqual(child['base']['ref'], 'main')
                self.assertEqual(child['head']['sha'], self.approved)
                child['mergeable_state'] = 'behind'
                self.save()
                if restart:
                    self.restart()
                self.assertTrue(self.q.refresh(module.REPOS[0], 'branch-1'))
                updates = [a for a in self.load()['calls'] if f'/pulls/{n}/update-branch' in str(a)]
                self.assertEqual(len(updates), 1)
                self.assertEqual(self.data['prs'][f'{module.REPOS[0]}#{n}']['head']['sha'], self.updated)
                self.q.discover()
                self.assertEqual(self.q.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 0)

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



    def test_unrelated_unknown_pr_does_not_delay_next_queued_merge(self):
        self.pr()
        self.pr(2, head=self.updated)
        self.pr(99, state='unknown')
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.enqueue(module.REPOS[0], 2, self.updated)
        self.q.tick()
        self.q.tick()
        self.q.tick()
        self.assertEqual(len(self.calls('merge')), 2)
        self.assertFalse(any(a[1] == f'repos/{module.REPOS[0]}/pulls/99'
                             for a in self.load()['calls'] if a[0] == 'api'))




    def test_fifo_across_repositories(self):
        self.pr(n=2, repo=module.REPOS[1])
        self.pr(n=1)
        self.q.enqueue(module.REPOS[1],2,self.approved)
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge')[0][2:5], ['2','-R',module.REPOS[1]])

    def test_no_checks_and_required_red_never_merge(self):
        self.pr()
        self.data['required']=[];self.save()
        self.q.enqueue(module.REPOS[0],1,self.approved)
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])
        self.load()
        self.data['required']=[{'bucket':'fail'}];self.save()
        self.q.tick()
        self.assertEqual(self.calls('merge'),[])


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


    def test_no_discover_idle_exits_with_reason_for_empty_and_parked_queue(self):
        for phase in (None, 'conflict', 'blocked'):
            with self.subTest(phase=phase):
                if phase:
                    entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
                    with self.q.db:
                        self.q.db.execute('UPDATE entries SET phase=? WHERE id=?', (phase, entry))
                with patch.object(module.time, 'sleep', side_effect=AssertionError('idle run waited for another poll')):
                    self.q.run(poll=30, discover=False)
                result = subprocess.run([sys.executable, str(ENTRY), '--state', str(self.state),
                                         'run', '--no-discover', '--poll', '0.05'],
                                        capture_output=True, text=True, env=self.env, timeout=2)
                self.assertEqual(result.returncode, 0, result.stderr)
                log = [json.loads(line) for line in (self.state / 'queue.log').read_text().splitlines()]
                self.assertEqual(log[-1]['outcome'], 'idle')
                self.assertIn('discovery disabled', log[-1]['detail'])

    def test_uncertain_merge_exhausts_without_reissuing_or_rearming_on_enqueue(self):
        self.pr()
        entry = self.q.enqueue(module.REPOS[0], 1, self.approved)
        with self.q.db:
            self.q.db.execute("UPDATE entries SET phase='merging',tested=?,merge_attempts=1 WHERE id=?",
                              (self.approved, entry))
        for _ in range(module.MAX_ATTEMPTS):
            self.q.tick()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        row = self.q.db.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        self.assertEqual(row['phase'], 'blocked')
        self.assertEqual(row['outcome'], 'merge_uncertain_exhausted')
        self.assertEqual(row['merge_attempts'], 1)
        self.assertEqual(self.calls('merge'), [])


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
        self.assertEqual(self.calls('merge'), [])
        self.assertEqual(self.q.db.execute('SELECT outcome FROM entries').fetchone()[0], 'head_moved')
        self.q.enqueue(module.REPOS[0], 1, self.updated);self.q.tick()
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
        self.q.enqueue(module.REPOS[0], 2, self.approved)
        registry=self.root/'desks.json';registry.write_text(json.dumps({'desks':{}}));os.environ['CARR_HERMES_DESKS']=str(registry)
        for n,phase in ((1,'issued'),(2,'planned')):
            brief=self.root/f'conflict{n}.txt';brief.write_text('task')
            with self.q.db:
                self.q.db.execute('INSERT INTO actions(key,kind,repo,pr,head,payload,phase,desk) VALUES(?,?,?,?,?,?,?,?)',(f'a{n}','dispatch',module.REPOS[0],n,self.approved,str(brief),phase,'busy-sol' if n==1 else None))
        (self.root/'conflict1.dispatch.jsonl').write_text('{"status":')
        self.q.dispatch_conflicts()
        self.assertIsNotNone(self.q.db.execute("SELECT 1 FROM events WHERE pr=2 AND outcome='dispatch_waiting'").fetchone())


    def test_merging_does_not_invoke_the_board(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        self.q.tick(); self.q.tick()
        self.assertEqual(len(self.calls('merge')), 1)
        self.assertFalse((self.root / 'tools/progress_board.calls').exists())
        self.q.flush_events()
        self.assertIn('post-merge refresh complete', (self.state / 'queue.log').read_text())

    def test_dispatch_waits_for_free_sol_desk_then_records_result(self):
        self.pr()
        self.q.enqueue(module.REPOS[0], 1, self.approved)
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
            "import os\n"
            + "sys.path.insert(0, " + repr(str(ROOT/'tools/room-bridge')) + ")\n"
            "from work_queue import WorkQueue\n"
            "with WorkQueue(Path(os.environ['CARR_WORK_QUEUE_DB'])) as q:\n"
            " card=a[a.index('--card-id')+1]\n"
            " q.requeue(card, stage='fixing', desk='free-sol', actor='test', reason='authorized revision')\n"
            " claim=q.claim(eligible_desks={'free-sol'}, actor='test', owner={})\n"
            " q.finish(card, claim['claim'], {'status':'completed','msg_id':'verified'}, actor='test')\n"
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
        self.assertEqual(self.calls('merge'), [])
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
        return subprocess.run([sys.executable, str(ENTRY), '--state', str(self.state), '--root', str(self.root), *args],
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
        self.pr(state='blocked', draft=True, base='just-merged')
        self.data['retarget_state'] = 'blocked'; self.save()
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertTrue(any('update-branch' in str(a) for a in self.load()['calls']))

    def test_unknown_pr_does_not_prevent_other_postmerge_updates(self):
        self.pr(1,state='unknown',base='just-merged');self.pr(2,state='behind',base='just-merged')
        self.q.refresh(module.REPOS[0], 'just-merged')
        self.assertTrue(any('/pulls/2/update-branch' in str(a) for a in self.load()['calls']))

    def test_single_running_agent_owns_the_state(self):
        self.pr(held=True)
        self.q.enqueue(module.REPOS[0], 1, self.approved)
        p=subprocess.Popen([sys.executable,str(ENTRY),'--state',str(self.state),'--root',str(self.root),'run','--no-discover','--poll','0.05'])
        try:
            import time
            time.sleep(.2)
            result=self.cli('--root',str(self.root),'run','--no-discover','--poll','0.05')
            self.assertNotEqual(result.returncode,0)
            self.assertIsNone(p.poll())
        finally:
            p.kill();p.wait(timeout=5)

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

    def test_empty_discovery_service_idles_without_exiting(self):
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            self.q.stopped = True
        with patch.object(self.q, 'discover') as discover, patch.object(module.time, 'sleep', side_effect=sleep):
            self.q.run(poll=.05)
        discover.assert_called_once()
        self.assertTrue(sleeps)

    def test_serial_only_cli_rejects_removed_batch_flag(self):
        result = self.cli('--batch-size=2', 'status')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unrecognized arguments', result.stderr)

    def test_serial_only_journal_has_no_batch_records(self):
        tables = {r[0] for r in self.q.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('batches', tables)
        self.assertNotIn('batch_id', {r['name'] for r in self.q.db.execute('PRAGMA table_info(entries)')})

if __name__ == '__main__':
    unittest.main()
