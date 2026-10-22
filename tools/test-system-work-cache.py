import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path
spec=importlib.util.spec_from_file_location('cache',Path(__file__).with_name('system_work_cache.py'))
assert spec is not None and spec.loader is not None
cache=importlib.util.module_from_spec(spec);spec.loader.exec_module(cache)
NOW=datetime(2026,10,1,tzinfo=timezone.utc)
class CacheTests(unittest.TestCase):
 def setUp(self):
  home=self.enterContext(tempfile.TemporaryDirectory())
  self.enterContext(patch.object(Path,'home',return_value=Path(home)))
 def read(self,args):
  pr={'number':1,'title':'Synthetic build','createdAt':'2026-09-01T00:00:00Z','updatedAt':'2026-09-02T00:00:00Z','url':'https://github.com/example/repo/pull/1','headRefName':'synthetic','body':'WR-000001','mergedAt':None,'state':'OPEN'}
  return self.response([pr])
 def response(self, rows, cursor='cursor-1', more=False):
  connection={'nodes':rows,'pageInfo':{'endCursor':cursor,'hasNextPage':more}}
  return {'data':{'repository':{'history':connection,'updates':connection}}}
 def branches(self,repo,root,now):
  return [{'name':'synthetic','sha':'1'*40,'date':'2026-09-01T00:00:00Z'}]
 def test_refresh_call_budget_does_not_grow_with_stale_branches(self):
  calls=[]
  def read(args):
   calls.append(args)
   return self.read(args)
  def branches(repo,root,now):
   return [{'name':f'stale-{n:02}','sha':f'{n:040x}','date':'2026-09-01T00:00:00Z'} for n in range(1,60)]
  result=cache.collect_github(NOW,read,branches=branches)
  self.assertTrue(result['complete'])
  self.assertEqual(sum(row['kind']=='remote_branch' for row in result['items']),118)
  self.assertLessEqual(len(calls),2, f'{len(calls)} GitHub calls for two repositories')
 def test_both_repos_open_prs_and_stale_unmerged_branches(self):
  r=cache.collect_github(NOW,self.read,{'synthetic':{'action':'progress','label':"The Dot's suggestion"}},branches=self.branches)
  self.assertTrue(r['complete']);self.assertEqual(len(r['items']),4)
  self.assertEqual(sum(x['kind']=='remote_branch' for x in r['items']),2)
  self.assertEqual(r['items'][1]['suggested_triage']['label'],"The Dot's suggestion")
 def test_each_repository_uses_its_current_github_owner(self):
  owners=[]
  def read(args):
   fields=dict(arg.split('=',1) for arg in args if '=' in arg)
   owners.append((fields['owner'],fields['name']))
   return self.read(args)
  result=cache.collect_github(NOW,read,branches=self.branches)
  self.assertTrue(result['complete'])
  self.assertEqual(owners,[('DoctorCRE','carr-system'),('jbookout','doctorcre-app')])
 def test_inventory_bytes_match_original_rest_projection(self):
  result=cache.collect_github(NOW,self.read,{'synthetic':{'action':'progress','label':"The Dot's suggestion"}},branches=self.branches)
  expected=(Path(__file__).parent/'fixtures/system-work-cache/inventory.json').read_text().rstrip('\n')
  self.assertEqual(json.dumps(result,sort_keys=True),expected)
 def test_expired_refresh_resumes_history_and_reconciles_old_pr_changes(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'cache.json'; calls=[]
   first=self.read([])['data']['repository']['history']['nodes'][0]
   merged={**first,'state':'MERGED','mergedAt':'2026-10-01T00:10:00Z','updatedAt':'2026-10-01T00:10:00Z','body':'WR-000002'}
   last={**first,'number':2,'createdAt':'2026-09-02T00:00:00Z'}
   def read(args):
    fields=dict(arg.split('=',1) for arg in args if '=' in arg)
    calls.append(fields)
    response=self.response([first],cursor='page-1',more=True)
    if len(calls)>2:
     self.assertEqual(fields['history'],'page-1')
     self.assertEqual(fields['updates'],'null')
     response=self.response([last],cursor='page-2')
     response['data']['repository']['updates']={'nodes':[merged,first],'pageInfo':{'endCursor':'updates-2','hasNextPage':False}}
    return response
   old=cache.cached_github(path,now=NOW,read=read,branches=self.branches)
   self.assertFalse(old['complete'])
   result=cache.cached_github(path,now=NOW+timedelta(minutes=16),read=read,branches=self.branches)
   self.assertTrue(result['complete']);self.assertEqual(len(calls),4)
   prs=[row for row in result['items'] if row['kind']=='pull_request']
   self.assertEqual(len(prs),4)
   self.assertEqual(sum(row['completed'] for row in prs),2)
   self.assertEqual(result['pr_work_refs'],['WR-000001','WR-000002'])
   self.assertEqual(set(result),set(old))
   state=path.with_suffix('.json.prs').read_text()
   self.assertNotIn('body',state)
 def test_cache_prevents_repeat_network_and_failures_stay_incomplete(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'cache.json';first=cache.cached_github(p,now=NOW,read=self.read,branches=self.branches)
   second=cache.cached_github(p,now=NOW,read=lambda _:self.fail('cache read used GitHub'),branches=lambda *_:self.fail('cache hit fetched git'))
   self.assertEqual(first,second)
  def fail(_):raise OSError('offline')
  self.assertFalse(cache.collect_github(NOW,fail)['complete'])
 def test_completed_pr_history_retains_old_merges_and_excludes_closed_unmerged(self):
  def read(args):
   rows=self.read(args)
   pr=rows['data']['repository']['history']['nodes'][0]
   merged={**pr,'number':2,'state':'MERGED','mergedAt':'2026-09-02T00:00:00Z'}
   closed={**pr,'number':3,'state':'CLOSED','mergedAt':None}
   return self.response([closed,merged,pr])
  result=cache.collect_github(NOW,read,branches=self.branches)
  self.assertTrue(result['completed_pr_history'])
  live=[item for item in result['items'] if item['completed']]
  self.assertEqual(len(live),2)
  self.assertTrue(all(item['state']=='merged' and item['last_activity_at']=='2026-09-02T00:00:00Z' for item in live))
  self.assertFalse(any(item['id'].endswith(':pr:3') for item in result['items']))
 def test_large_history_and_update_backlog_use_one_page_per_stream_per_refresh(self):
  prs=[{**self.read([])['data']['repository']['history']['nodes'][0], 'number':n,'updatedAt':'2026-10-01T00:00:01Z'} for n in range(1,251)]
  with tempfile.TemporaryDirectory() as d:
   calls=[];path=Path(d)/'cache.json'
   def read(args):
    fields=dict(arg.split('=',1) for arg in args if '=' in arg);calls.append(fields)
    data={}
    for stream,rows in [('history',prs),('updates',list(reversed(prs)))]:
     start=0 if fields[stream]=='null' else int(fields[stream])
     page=rows[start:start+100]
     data[stream]={'nodes':page,'pageInfo':{'endCursor':str(start+len(page)) if page else None,'hasNextPage':start+len(page)<250}}
    return {'data':{'repository':data}}
   for refresh in range(3):
    result=cache.cached_github(path,now=NOW+timedelta(minutes=16*refresh),read=read,branches=self.branches)
    self.assertEqual(len(calls),2*(refresh+1))
    self.assertEqual(result['complete'],refresh==2)
   self.assertEqual(sum(row['kind']=='pull_request' for row in result['items']),500)
   self.assertEqual([c['history'] for c in calls],['null','null','100','100','200','200'])
   self.assertEqual([c['updates'] for c in calls],['null','null','100','100','200','200'])
 def test_real_git_fetch_filters_merged_recent_and_deleted_branches(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); source=root/'source'
   def git(*args, date='2026-09-01T00:00:00Z'):
    env={**os.environ,'GIT_AUTHOR_DATE':date,'GIT_COMMITTER_DATE':date}
    return subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*args],env=env,text=True,capture_output=True,check=True).stdout.strip()
   source.mkdir();git('init','-b','main');git('commit','--allow-empty','-m','base');git('branch','landed')
   git('checkout','-b','stale/feature');git('commit','--allow-empty','-m','feature',date='2026-09-02T00:00:00Z')
   sha=git('rev-parse','HEAD')
   git('checkout','-b','recent');git('commit','--allow-empty','-m','recent',date='2026-09-30T00:00:00Z')
   git('checkout','-b','exactly-seven-days');git('commit','--allow-empty','-m','boundary',date='2026-09-24T00:00:00Z')
   env={'CARR_REPO_ROOT':str(source),'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':f'url.{source}.insteadOf','GIT_CONFIG_VALUE_0':'https://github.com/DoctorCRE/carr-system.git'}
   with patch.dict(os.environ,env):
    rows=cache.local_branches('jbookout/carr-system',root/'mirrors',NOW)
    self.assertEqual(rows,[{'name':'stale/feature','sha':sha,'date':'2026-09-02T00:00:00Z'}])
    git('branch','-D','stale/feature')
    self.assertEqual(cache.local_branches('jbookout/carr-system',root/'mirrors',NOW),[])
 def test_cold_mirror_reuses_fetched_checkout_objects(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'checkout';source.mkdir()
   subprocess.run(['git','init','-b','main',str(source)],capture_output=True,check=True)
   subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--allow-empty','-m','base'],capture_output=True,check=True)
   real_run=subprocess.run
   def transport(args,**kwargs):
    if any(arg.startswith('https://') for arg in args):
     probe=real_run([*args[:2],'cat-file','-e','refs/heads/main^{commit}'],capture_output=True)
     self.assertEqual(probe.returncode,0,'cold fetch must reuse the already-fetched checkout')
     return subprocess.CompletedProcess(args,0,stdout='',stderr='')
    return real_run(args,**kwargs)
   with patch.dict(os.environ,{'CARR_REPO_ROOT':str(source)}), patch.object(subprocess,'run',side_effect=transport):
    self.assertEqual(cache.local_branches('jbookout/carr-system',root/'mirrors',NOW),[])
 def test_builder_brief_with_pr_is_excluded_and_no_body_cached(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'brief-synthetic.txt';p.write_text('ROLE: builder\nREPOS: carr-system\nBRANCH: synthetic\n')
   self.assertEqual(cache.unfinished_briefs(d,[{'repository':'jbookout/carr-system','branch':'synthetic'}]),[])
   rows=cache.unfinished_briefs(d,[]);self.assertEqual(len(rows),1);self.assertNotIn('body',rows[0])
 def test_concurrent_refresh_never_overwrites_newer_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'cache.json'
   started=threading.Event(); release=threading.Event()
   def older_read(args):
    if args[1]=='graphql':
     started.set(); self.assertTrue(release.wait(5))
    return self.read(args)
   with ThreadPoolExecutor(max_workers=2) as pool:
    old=pool.submit(cache.cached_github,path,now=NOW,read=older_read,branches=self.branches)
    self.assertTrue(started.wait(5))
    newer=cache.cached_github(path,now=NOW+timedelta(minutes=1),read=self.read,branches=self.branches)
    release.set(); result=old.result(timeout=5)
   self.assertEqual(json.loads(path.read_text()),newer)
   self.assertEqual(result,newer)
 def test_malformed_dependencies_never_claim_complete_or_crash(self):
  for malformed in ({}, None, [None], [[None]], [[{}]]):
   with self.subTest(malformed=malformed):
    result=cache.collect_github(NOW,read=lambda _:malformed)
    self.assertFalse(result['complete'])
    self.assertTrue(all(not c['complete'] for c in result['coverage']))
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'cache.json';p.write_text('null')
   self.assertTrue(cache.cached_github(p,now=NOW,read=self.read,branches=self.branches)['complete'])
  self.assertEqual(cache.dot_suggestions('Last commit: 2026-09-01'),{})
 def test_dot_suggestions_are_labels_not_automatic_dispositions(self):
  report='ALREADY LANDED ANOTHER WAY • 1\nsynthetic-old\nLast commit: 2026-09-01\nJOB G • 3/4: Abandoned work worth finishing • 12\nsynthetic-finish\nLast commit: 2026-09-01\n'
  r=cache.dot_suggestions(report);self.assertEqual(r['synthetic-old']['action'],'cancel');self.assertEqual(r['synthetic-finish']['action'],'progress')
if __name__=='__main__':unittest.main()
