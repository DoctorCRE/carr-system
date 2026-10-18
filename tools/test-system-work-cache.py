import importlib.util
import json
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
 def read(self,args):
  if args[0]=='api' and '/pulls?' in args[1]:return [[{'number':1,'title':'Synthetic build','created_at':'2026-09-01T00:00:00Z','updated_at':'2026-09-02T00:00:00Z','html_url':'https://github.com/example/repo/pull/1','head':{'ref':'synthetic'},'body':'WR-000001'}]]
  if '/branches?' in args[1]:return [[{'name':'main','commit':{'sha':'0'*40}},{'name':'synthetic','commit':{'sha':'1'*40}},{'name':'merged','commit':{'sha':'2'*40}}]]
  if '/commits/' in args[1]:return {'commit':{'committer':{'date':'2026-09-01T00:00:00Z'}}}
  return {'ahead_by':0 if args[1].endswith('merged') else 1}
 def test_both_repos_open_prs_and_stale_unmerged_branches(self):
  r=cache.collect_github(NOW,self.read,{'synthetic':{'action':'progress','label':"The Dot's suggestion"}})
  self.assertTrue(r['complete']);self.assertEqual(len(r['items']),4)
  self.assertEqual(sum(x['kind']=='remote_branch' for x in r['items']),2)
  self.assertEqual(r['items'][1]['suggested_triage']['label'],"The Dot's suggestion")
 def test_cache_prevents_repeat_network_and_failures_stay_incomplete(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'cache.json';first=cache.cached_github(p,now=NOW,read=self.read)
   second=cache.cached_github(p,now=NOW,read=lambda _:self.fail('cache read used GitHub'))
   self.assertEqual(first,second)
  def fail(_):raise OSError('offline')
  self.assertFalse(cache.collect_github(NOW,fail)['complete'])
 def test_completed_pr_history_retains_old_merges_and_excludes_closed_unmerged(self):
  def read(args):
   rows=self.read(args)
   if '/pulls?' in args[1]:
    merged={**rows[0][0],'number':2,'state':'closed','merged_at':'2026-09-02T00:00:00Z'}
    closed={**rows[0][0],'number':3,'state':'closed','merged_at':None}
    return [[*rows[0],merged,closed]]
   return rows
  result=cache.collect_github(NOW,read)
  self.assertTrue(result['completed_pr_history'])
  live=[item for item in result['items'] if item['completed']]
  self.assertEqual(len(live),2)
  self.assertTrue(all(item['state']=='merged' and item['last_activity_at']=='2026-09-02T00:00:00Z' for item in live))
  self.assertFalse(any(item['id'].endswith(':pr:3') for item in result['items']))
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
    if '/pulls?' in args[1]:
     started.set(); self.assertTrue(release.wait(5))
    return self.read(args)
   with ThreadPoolExecutor(max_workers=2) as pool:
    old=pool.submit(cache.cached_github,path,now=NOW,read=older_read)
    self.assertTrue(started.wait(5))
    newer=cache.cached_github(path,now=NOW+timedelta(minutes=1),read=self.read)
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
   self.assertTrue(cache.cached_github(p,now=NOW,read=self.read)['complete'])
  self.assertEqual(cache.dot_suggestions('Last commit: 2026-09-01'),{})
 def test_dot_suggestions_are_labels_not_automatic_dispositions(self):
  report='ALREADY LANDED ANOTHER WAY • 1\nsynthetic-old\nLast commit: 2026-09-01\nJOB G • 3/4: Abandoned work worth finishing • 12\nsynthetic-finish\nLast commit: 2026-09-01\n'
  r=cache.dot_suggestions(report);self.assertEqual(r['synthetic-old']['action'],'cancel');self.assertEqual(r['synthetic-finish']['action'],'progress')
if __name__=='__main__':unittest.main()
