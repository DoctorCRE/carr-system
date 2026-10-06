#!/usr/bin/env python3
"""Mechanical judgment regression tests. All transports are explosive fakes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), REPO / 'ops' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NoModel:
    def __getattr__(self, name):
        raise AssertionError('model access: ' + name)


class DeterministicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def receipt(self, name):
        return {'path':name,'sha256':hashlib.sha256((self.root/name).read_bytes()).hexdigest()}

    def test_receipt_reads_bytes_and_expected_json_not_just_status(self):
        from lib.acceptance_checks import evaluate
        artifact = self.root / 'result.json'
        artifact.write_text('{"count":2}')
        criteria = [{'id':'count', 'kind':'json_equals', 'path':'result.json', 'pointer':'/count', 'value':2}]
        receipts = [self.receipt('result.json')]
        self.assertEqual(evaluate(criteria, root=self.root, receipts=receipts)['status'], 'passed')
        artifact.write_text('{"count":1}')
        self.assertEqual(evaluate(criteria, root=self.root, receipts=receipts)['status'], 'failed')
        self.assertEqual(evaluate(criteria, root=self.root, receipts=[self.receipt('result.json')])['status'], 'failed')

    def test_observed_artifacts_need_no_worker_receipt(self):
        from lib.acceptance_checks import evaluate
        (self.root/'result.json').write_text('{"count":2}')
        criterion = {'id':'count', 'kind':'json_equals', 'path':'result.json', 'pointer':'/count', 'value':2}
        self.assertEqual(evaluate([criterion], root=self.root)['status'], 'passed')
        self.assertEqual(evaluate([{**criterion,'value':3}], root=self.root)['status'], 'failed')
        self.assertEqual(evaluate([{**criterion,'path':'missing'}], root=self.root)['status'], 'failed')

    def test_empty_or_semantic_criteria_never_authorize_completion(self):
        from lib.acceptance_checks import evaluate
        for criteria in ([], ['make it delightful'], [{'kind':'semantic','text':'good'}]):
            self.assertEqual(evaluate(criteria, root=self.root)['status'], 'needs_review')

    def test_check_criteria_cannot_be_satisfied_by_claimed_results(self):
        from lib.acceptance_checks import evaluate
        criterion = {'id':'ci','kind':'check','command':'false','source_sha':'a'*40,'output_contains':'tests passed'}
        result = evaluate([criterion], root=self.root)
        self.assertEqual(result['status'], 'needs_review')

    def test_artifact_expected_digest_and_containment(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('content')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        receipts=[self.receipt('output')]
        criterion={'id':'output','kind':'artifact','path':'output'}
        self.assertEqual(evaluate([criterion],root=self.root,receipts=receipts)['status'],'needs_review')
        self.assertEqual(evaluate([{**criterion,'sha256':digest}],root=self.root,receipts=receipts)['status'],'passed')
        self.assertEqual(evaluate([{**criterion,'sha256':'0'*64}],root=self.root,receipts=receipts)['status'],'failed')
        for path_value in ('../outside', str(self.root.parent/'outside')):
            self.assertEqual(evaluate([{**criterion,'path':path_value}],root=self.root,receipts=receipts)['status'],'failed')

    def test_duplicate_receipts_and_unknown_criterion_never_pass(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('content')
        receipt=self.receipt('output')
        criterion={'id':'output','kind':'contains','path':'output','text':'content'}
        self.assertEqual(evaluate([criterion],root=self.root,receipts=[receipt,receipt])['status'],'failed')
        self.assertEqual(evaluate([criterion,criterion],root=self.root,receipts=[receipt])['status'],'failed')
        self.assertEqual(evaluate([{'id':'semantic','kind':'good'}],root=self.root)['status'],'needs_review')

    def test_json_boolean_does_not_equal_integer_and_escaped_pointer_matches(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('{"a/b":{"~flag":true}}')
        criterion={'id':'output','kind':'json_equals','path':'output','pointer':'/a~1b/~0flag','value':True}
        self.assertEqual(evaluate([criterion],root=self.root)['status'],'passed')
        self.assertEqual(evaluate([{**criterion,'value':1}],root=self.root)['status'],'failed')

    def test_json_comparison_is_type_strict_at_every_depth(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output'
        cases=[('{"count":true}',{'count':1},'failed'),('{"count":1}',{'count':True},'failed'),
               ('[[0,false]]',[[0,0]],'failed'),('{"x":1.0}',{'x':1},'failed'),
               ('{"a":[1,{"b":null}]}',{'a':[1,{'b':None}]},'passed')]
        for text,expected,status in cases:
            with self.subTest(text=text,expected=expected):
                path.write_text(text)
                criterion={'id':'output','kind':'json_equals','path':'output','pointer':'','value':expected}
                self.assertEqual(evaluate([criterion],root=self.root)['status'],status)

    def test_json_pointer_array_tokens_follow_rfc6901(self):
        from lib.acceptance_checks import evaluate
        path=self.root/'output';path.write_text('{"list":[0,1],"~":{"/":2}}')
        for pointer,value,status in [('/list/1',1,'passed'),('/list/-1',1,'failed'),('/list/+1',1,'failed'),
                                     ('/list/01',1,'failed'),('/list/-',1,'failed'),('/list/2',1,'failed'),
                                     ('/~0/~1',2,'passed'),('/~2',2,'failed'),('list',1,'failed')]:
            with self.subTest(pointer=pointer):
                criterion={'id':'output','kind':'json_equals','path':'output','pointer':pointer,'value':value}
                self.assertEqual(evaluate([criterion],root=self.root)['status'],status)

    def test_nonregular_artifact_fails_without_blocking(self):
        import os, subprocess
        fifo=self.root/'fifo';os.mkfifo(fifo)
        script=('import json,sys;sys.path.insert(0,sys.argv[1]);from lib.acceptance_checks import evaluate;'
                'print(json.dumps(evaluate([{"id":"x","kind":"contains","path":"fifo","text":"x"}],root=sys.argv[2])))')
        run=subprocess.run([sys.executable,'-c',script,str(REPO),str(self.root)],capture_output=True,text=True,timeout=10)
        self.assertEqual(json.loads(run.stdout)['status'],'failed',run.stderr)
        (self.root/'dir').mkdir()
        from lib.acceptance_checks import evaluate
        self.assertEqual(evaluate([{'id':'d','kind':'contains','path':'dir','text':'x'}],root=self.root)['status'],'failed')

    def test_stalled_open_and_read_have_enforced_deadlines_in_stop_callers(self):
        import os, time, types
        from lib import acceptance_checks as acceptance
        (self.root/'output').write_text('content')
        criterion={'id':'output','kind':'contains','path':'output','text':'content'}
        prompt=json.dumps({'acceptance_contract':{'criteria':[criterion]}})
        recs=[{'type':'user','message':{'content':prompt}}]
        req=load('jev_requirements');done=load('jev_done_checks')
        for operation in ('open','read'):
            proxy=types.SimpleNamespace(**{name:getattr(os,name) for name in
                ('open','read','fstat','close','O_RDONLY','O_NONBLOCK')})
            original=getattr(os,operation)
            def stalled(*args, original=original):
                time.sleep(0.6)
                return original(*args)
            setattr(proxy,operation,stalled)
            # Inject the same stalled syscall into the disposable reader too.
            program=acceptance._READER_PROGRAM.replace(
                'spec.loader.exec_module(module)\n','spec.loader.exec_module(module)\n'
                f"module.os.{operation}=lambda *a: __import__('time').sleep(60)\n")
            with patch.object(acceptance,'os',proxy), \
                 patch.object(acceptance,'_READER_PROGRAM',program,create=True), \
                 patch.object(acceptance,'READ_TIMEOUT',0.05,create=True), \
                 patch.object(req,'_acceptance',return_value=acceptance), \
                 patch.object(done,'_sibling_lib',return_value=acceptance):
                callers=[lambda:acceptance.evaluate([criterion],root=self.root,deadline=time.monotonic()+0.05),
                         lambda:req.check({'cwd':str(self.root)},recs),
                         lambda:done.check_done_claim('Done.',{'claim_scope':'current_completion',
                             'criteria':[criterion],'root':str(self.root)})['detail']]
                for index,caller in enumerate(callers):
                    with self.subTest(operation=operation,caller=index):
                        start=time.monotonic();result=caller();elapsed=time.monotonic()-start
                        self.assertLess(elapsed,0.3,result)
                        self.assertEqual(result['status'],'failed',result)
                        self.assertIn('deadline',result['criteria'][0]['reason'])

    def test_stalled_path_resolution_has_enforced_deadline_in_stop_callers(self):
        import os, time
        from lib import acceptance_checks as acceptance
        (self.root/'output').write_text('content')
        criterion={'id':'output','kind':'contains','path':'output','text':'content'}
        prompt=json.dumps({'acceptance_contract':{'criteria':[criterion]}})
        recs=[{'type':'user','message':{'content':prompt}}]
        req=load('jev_requirements');done=load('jev_done_checks')
        # Root, artifact and reader-module resolution each lstat a named path.
        for target in ('output', self.root.name, 'acceptance_checks.py'):
            original=os.lstat
            def stalled(path,*args,target=target,**kwargs):
                if os.path.basename(os.fsdecode(path)) == target:
                    time.sleep(0.6)
                return original(path,*args,**kwargs)
            injection=(f"_lstat=module.os.lstat\n"
                       f"module.os.lstat=lambda p,*a,**k: (__import__('time').sleep(60) "
                       f"if module.os.path.basename(module.os.fsdecode(p))=={target!r} else _lstat(p,*a,**k))\n")
            program=acceptance._READER_PROGRAM.replace(
                'spec.loader.exec_module(module)\n','spec.loader.exec_module(module)\n'+injection)
            with patch('os.lstat',stalled), \
                 patch.object(acceptance,'_READER_PROGRAM',program), \
                 patch.object(acceptance,'READ_TIMEOUT',0.05), \
                 patch.object(req,'_acceptance',return_value=acceptance), \
                 patch.object(done,'_sibling_lib',return_value=acceptance):
                callers=[lambda:acceptance.evaluate([criterion],root=self.root,deadline=time.monotonic()+0.05),
                         lambda:acceptance.evaluate([criterion],root=self.root,
                             receipts=[self.receipt('output')],deadline=time.monotonic()+0.05),
                         lambda:req.check({'cwd':str(self.root)},recs),
                         lambda:done.check_done_claim('Done.',{'claim_scope':'current_completion',
                             'criteria':[criterion],'root':str(self.root)})['detail']]
                for index,caller in enumerate(callers):
                    with self.subTest(target=target,caller=index):
                        start=time.monotonic();result=caller();elapsed=time.monotonic()-start
                        self.assertLess(elapsed,0.3,result)
                        if target == 'acceptance_checks.py':
                            continue  # the reader's own path is never looked up at call time
                        self.assertEqual(result['status'],'failed',result)
                        self.assertIn('deadline',result['criteria'][0]['reason'])

    def test_replaced_artifact_fifo_is_checked_after_open(self):
        from lib import acceptance_checks as acceptance
        path=self.root/'output';path.write_text('content')
        # Replace after path validation, before opening in the reader.
        program=acceptance._READER_PROGRAM.replace(
            'spec.loader.exec_module(module)\n','spec.loader.exec_module(module)\n'
            "_read=module._read_regular\n"
            "def _swap(path,limit):\n"
            "    module.os.unlink(path);module.os.mkfifo(path);return _read(path,limit)\n"
            "module._read_regular=_swap\n")
        with patch.object(acceptance,'_READER_PROGRAM',program):
            result=acceptance.evaluate([{'id':'x','kind':'contains','path':'output','text':'content'}],root=self.root)
        self.assertEqual(result['status'],'failed')
        self.assertIn('regular file',result['criteria'][0]['reason'])

    def test_reader_deadline_works_from_a_thread(self):
        import concurrent.futures
        from lib import acceptance_checks as acceptance
        (self.root/'output').write_text('content')
        with patch.object(acceptance,'_READER_PROGRAM','import time;time.sleep(60)'), \
             patch.object(acceptance,'READ_TIMEOUT',0.05), \
             concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(acceptance.evaluate,[{'id':'x','kind':'contains','path':'output','text':'content'}],
                               root=self.root).result(timeout=0.5)
        self.assertEqual(result['status'],'failed')
        self.assertIn('deadline',result['criteria'][0]['reason'])

    def test_prose_handoff_advisory_reaches_both_hook_entrypoints(self):
        import subprocess
        prose='mlx-serve is ready once the Homebrew tap is trusted; trust it, then install.'
        transcript=self.root/'turn.jsonl'
        transcript.write_text('\n'.join(json.dumps(row) for row in [
            {'type':'user','message':{'content':'yes install it'}},
            {'type':'assistant','message':{'content':prose}}])+'\n')
        for hook,payload in [
            ('conduct-stop-gate',{'transcript_path':str(transcript),'session_id':'selftest'}),
            ('escalation-gate',{'transcript_path':str(transcript),'session_id':'selftest',
                'tool_name':'AskUserQuestion','tool_input':{'questions':[{'question':prose,'options':[]}]}})]:
            with self.subTest(hook=hook):
                run=subprocess.run([sys.executable,str(REPO/'hooks'/(hook+'.py'))],
                    input=json.dumps(payload),capture_output=True,text=True,timeout=10)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertIn('handoff_review',run.stdout,run.stderr)
                self.assertIn('needs_review',run.stdout)
                self.assertNotIn('"decision": "block"',run.stdout)

    def test_artifact_read_honors_deadline(self):
        import time
        from lib.acceptance_checks import evaluate
        (self.root/'output').write_text('content')
        criterion={'id':'output','kind':'contains','path':'output','text':'content'}
        result=evaluate([criterion],root=self.root,deadline=time.monotonic()-1)
        self.assertEqual(result['status'],'failed')

    def test_progress_changing_results_edits_and_dedupe(self):
        watch=load('jev_session_watch')
        events=[]
        for i in range(3):
            events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Read','input':{'file_path':'x'}}]}},
                       {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':str(i)}]}}]
        path=self.root/'t';path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'ok')
        for e in events:
            for b in e['message']['content']:
                if b['type']=='tool_result':b['content']='same'
        path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'stuck')
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'ok')
        events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':'edit','name':'Edit','input':{}}]}},
                   {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'edit','content':'edited'}]}}]
        path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'state'))['verdict'],'ok')

    def test_failed_edits_do_not_reset_progress_detection(self):
        watch=load('jev_session_watch')
        for unchanged_artifacts in (False,True):
            with self.subTest(unchanged_artifacts=unchanged_artifacts):
                events=[]
                for i in range(3):
                    events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Edit','input':{'file_path':'x'}}]}},
                               {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'is_error':True,'content':'PermissionError: denied'}]}}]
                path=self.root/'failed_edits';path.write_text('\n'.join(json.dumps(e) for e in events))
                kwargs={'artifact_before':{'x':'same'},'artifact_after':{'x':'same'}} if unchanged_artifacts else {}
                result=watch.watch_progress(str(path),'task',client=NoModel(),state_dir=str(self.root/str(unchanged_artifacts)),**kwargs)
                self.assertEqual(result['verdict'],'stuck')

    def test_pending_calls_never_pull_pre_edit_results_across_an_edit(self):
        watch=load('jev_session_watch')
        def use(i,name='Read',inp=None):
            return {'type':'assistant','message':{'content':[{'type':'tool_use','id':i,'name':name,'input':inp or {'file_path':'x'}}]}}
        def result(i,text):
            return {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':i,'content':text}]}}
        events=[]
        for i in range(3):
            events += [use(f'r{i}'),result(f'r{i}','same')]
        events += [use('edit','Edit',{}),result('edit','edited')]
        events += [use(f'p{i}',inp={'file_path':f'y{i}'}) for i in range(4)]
        path=self.root/'pending';path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'p'))['verdict'],'ok')
        # Results that arrive out of order after the edit still count, once.
        events += [result('p3','late'),result('p1','late')]
        path.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(watch.watch_progress(str(path),'task',state_dir=str(self.root/'q'))['verdict'],'ok')

    def test_progress_detects_repeat_without_artifact_delta(self):
        watch = load('jev_session_watch')
        events = []
        for i in range(3):
            events += [{'type':'assistant','message':{'content':[{'type':'tool_use','id':str(i),'name':'Read','input':{'file_path':'x'}}]}},
                       {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':str(i),'content':'same'}]}}]
        transcript = self.root/'t.jsonl'
        transcript.write_text('\n'.join(json.dumps(e) for e in events))
        with patch.object(watch,'_judge',side_effect=AssertionError('paid')):
            result = watch.watch_progress(str(transcript),'task',client=NoModel(),state_dir=str(self.root/'state'))
        self.assertEqual(result['verdict'],'stuck')

    def test_progress_elapsed_budget_and_observed_delta(self):
        watch = load('jev_session_watch')
        transcript = self.root/'t'; transcript.write_text('')
        result = watch.watch_progress(str(transcript),'task',elapsed_seconds=61,budget_seconds=60,
                                      artifact_before={'x':'a'},artifact_after={'x':'a'},state_dir=str(self.root))
        self.assertEqual(result['verdict'],'stuck')
        result = watch.watch_progress(str(transcript),'task',elapsed_seconds=61,budget_seconds=60,
                                      artifact_before={'x':'a'},artifact_after={'x':'b'},state_dir=str(self.root))
        self.assertEqual(result['verdict'],'ok')

    def test_repairs_are_candidates_with_no_semantic_guess(self):
        watch = load('jev_session_watch')
        self.assertEqual(watch.repair_path('bad/x.py',str(self.root),files=['src/x.py'],client=NoModel())['detail']['repaired_path'],'src/x.py')
        result = watch.repair_path('bad/x.py',str(self.root),files=['a/x.py','b/x.py'],client=NoModel())
        self.assertEqual(result['verdict'],'needs_review')
        self.assertEqual(watch.repair_name('helo',['hello'],'',client=NoModel())['verdict'],'needs_review')
        self.assertEqual(watch.triage_failure('python x','ModuleNotFoundError: x',1,client=NoModel())['verdict'],'environment')
        self.assertEqual(watch.triage_failure('cmd','Permission denied',1,client=NoModel())['verdict'],'permission')
        self.assertEqual(watch.triage_failure('cmd','mystery',1,client=NoModel())['verdict'],'needs_review')

    def test_changed_path_test_mapping_never_asks_model(self):
        watch = load('jev_session_watch')
        result = watch.pick_tests(['src/foo.py'],str(self.root),files=['ops/foo-selftest.py','ops/bar-selftest.py'],client=NoModel())
        self.assertEqual(result['detail']['tests'],['ops/foo-selftest.py'])

    def test_done_claim_needs_criteria_and_keeps_unresolved_test_failures(self):
        done = load('jev_done_checks')
        self.assertEqual(done.check_done_claim('Done',{'test_exit_code':0})['verdict'],'needs_review')
        evidence = {'claim_scope':'tests','test_history':[{'command':'a','exit_code':1},{'command':'b','exit_code':0}]}
        self.assertEqual(done.check_done_claim('Tests passed',evidence)['verdict'],'unsupported')
        evidence['test_history'].append({'command':'a','exit_code':0})
        self.assertEqual(done.check_done_claim('Tests passed',evidence)['verdict'],'needs_review')

    def test_semantic_test_quality_and_facts_abstain(self):
        done = load('jev_done_checks')
        self.assertEqual(done.check_test_quality('def test_x():\n assert call() == 2','','')['verdict'],'needs_review')
        self.assertEqual(done.fact_check('X works',[{'ref':'x','text':'X works'}])['verdict'],'needs_review')

    def test_identical_calls_are_not_tautologies(self):
        done = load('jev_done_checks')
        self.assertEqual(done.check_test_quality('def test_x():\n assert call() == call()','','')['verdict'],'needs_review')

    def test_requirement_contract_completes_through_installed_stop_payload(self):
        req = load('jev_requirements')
        (self.root/'result.json').write_text('{"count":2}')
        prompt = json.dumps({'acceptance_contract':{'criteria':[
            {'id':'count','kind':'json_equals','path':'result.json','pointer':'/count','value':2}]}})
        recs = [{'type':'user','message':{'content':prompt}}]
        payload = {'cwd':str(self.root),'transcript_path':str(self.root/'t.jsonl'),'session_id':'s',
                   'hook_event_name':'Stop','stop_hook_active':False}
        self.assertEqual(req.check(payload,recs)['status'],'passed')
        (self.root/'result.json').write_text('{"count":1}')
        result = req.check(payload,recs)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['unmet'][0]['text'],'count')

    def test_no_inert_handoff_or_requirement_compatibility_interfaces(self):
        import inspect
        self.assertFalse((REPO/'ops/jev_handoff.py').exists())
        for hook in ('conduct-stop-gate','escalation-gate'):
            text=(REPO/'hooks'/(hook+'.py')).read_text()
            self.assertNotIn('jev_handoff',text,hook)
        req=load('jev_requirements')
        for name in ('changed_paths','test_output','evaluate_requirements','MUTATION_TOOLS','TEST_COMMAND'):
            self.assertFalse(hasattr(req,name),name)
        self.assertEqual(list(inspect.signature(req.check).parameters),['payload','recs'])
        done=load('jev_done_checks')
        for function in (done.triage_review,done.inspect_stop_boundary,done.check_done_claim,
                         done.check_test_quality,done.fact_check,done.build_handoff):
            params=set(inspect.signature(function).parameters)
            self.assertFalse(params & {'client','judge_module','cache_path','now','state_dir','receipt_path'},function.__name__)
        notebook=load('jev_notebook')
        for function in (notebook.recall_mistakes,notebook.classify_kind):
            params=set(inspect.signature(function).parameters)
            self.assertFalse(params & {'client','judge','shortlist'},function.__name__)

    def test_tolls_use_path_contract_dependencies_not_transport(self):
        tolls = load('jev_change_tolls')
        result = tolls.owed({'files':{'edited':['ops/ci.sh','mcp-server/src/tools.js','hooks/delegation-gate.py']}},client=NoModel())
        self.assertTrue({'ci_sh_reseal','inventory_reseal','gate_rebless'} <= {name for _,name,_ in result})
        self.assertEqual(tolls.owed({'files':{'edited':['README.md']}},client=NoModel()),[])

    def test_tolls_follow_sealed_source_locators_outside_server(self):
        tolls=load('jev_change_tolls')
        result=tolls.owed({'files':{'edited':['bin/deploy-worker.sh']}},client=NoModel())
        self.assertIn('inventory_reseal',[name for _,name,_ in result])

    def test_notebook_uses_exact_error_or_artifact_not_topic_overlap(self):
        notebook = load('jev_notebook')
        path = self.root/'notebook'
        notebook.record_mistake('import','edit code','ModuleNotFoundError: widgets','install it',source='fixture',notebook_path=str(path))
        notebook.record_mistake('path','edit code','missing src/data.json','fix path',source='fixture',notebook_path=str(path))
        self.assertEqual(notebook.recall_mistakes('ModuleNotFoundError: widgets',notebook_path=str(path))['verdict'],[0])
        self.assertEqual(notebook.recall_mistakes('edit code',notebook_path=str(path))['verdict'],[])
        self.assertEqual(notebook.classify_kind('unknown failure','',notebook_path=str(path))['verdict'],'needs_review')

    def completion(self, instructions, artifacts, **extra):
        from lib.headless_tasks import verify_completion
        receipt = self.root/'receipt'
        receipt.write_text(json.dumps({'schema':'carr-headless-completion/v1','task_id':'t','run_id':'r',
                                       'outcome':'completed','artifacts':artifacts,**extra}))
        return verify_completion(receipt,'t','r',instructions)

    def test_headless_typed_acceptance_never_asks_model(self):
        output = self.root/'output'; output.write_text('{"count":2}')
        criteria = [{'id':'count','kind':'json_equals','path':str(output),'pointer':'/count','value':2}]
        instructions = json.dumps({'acceptance_contract':{'criteria':criteria}})
        artifacts = [{'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}]
        with patch('ops.jev_judge.judge',side_effect=AssertionError('paid')):
            self.assertEqual(self.completion(instructions,artifacts),'completed')
            with self.assertRaisesRegex(ValueError,'needs review'):
                self.completion('make it good',artifacts)

    def test_headless_worker_check_claims_are_not_acceptance(self):
        unrelated = self.root/'notes'; unrelated.write_text('unrelated')
        artifacts = [{'path':str(unrelated),'sha256':hashlib.sha256(unrelated.read_bytes()).hexdigest()}]
        criterion = {'id':'ci','kind':'check','command':'false','source_sha':'a'*40,'output_contains':'tests passed'}
        instructions = json.dumps({'acceptance_contract':{'criteria':[criterion]}})
        forged = {'command':'false','source_sha':'a'*40,'exit_code':0,'output':'tests passed'}
        # A current-run forgery and a replay of an old successful claim both fail.
        for checks in ([forged], [{**forged,'run_id':'old-run'}]):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                self.completion(instructions,artifacts,checks=checks)

    def test_headless_nonregular_artifact_fails_without_blocking(self):
        import os, subprocess
        fifo=self.root/'fifo';os.mkfifo(fifo)
        script=('import json,sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;'
                'from lib.headless_tasks import verify_completion\n'
                'r=Path(sys.argv[2])/"receipt";r.write_text(json.dumps({"schema":"carr-headless-completion/v1","task_id":"t",'
                '"run_id":"r","outcome":"completed","artifacts":[{"path":sys.argv[3],"sha256":"0"*64}]}))\n'
                'try:\n verify_completion(r,"t","r","{}")\nexcept ValueError as e:\n print("refused",e)')
        run=subprocess.run([sys.executable,'-c',script,str(REPO),str(self.root),str(fifo)],capture_output=True,text=True,timeout=10)
        self.assertIn('refused',run.stdout,run.stderr)

    def test_supervisor_stop_observes_contract_artifacts_from_installed_payload(self):
        spec=importlib.util.spec_from_file_location('typed_supervisor',REPO/'hooks/jev-supervisor.py')
        hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
        (self.root/'result.json').write_text('{"count":2}')
        criterion={'id':'count','kind':'json_equals','path':'result.json','pointer':'/count','value':2}
        prompt=json.dumps({'acceptance_contract':{'criteria':[criterion]}})
        from types import SimpleNamespace
        captured=[]
        def inspect(final,evidence,*args):captured.append(evidence);return []
        payload={'last_assistant_message':'Done','cwd':str(self.root),'session_id':'s','hook_event_name':'Stop',
                 'transcript_path':str(self.root/'missing.jsonl')}
        with patch.object(hook,'_task',return_value=prompt),patch.object(hook,'_git_root',return_value=None),patch.object(hook,'_lib',return_value=SimpleNamespace(inspect_stop_boundary=inspect)):
            hook.stop(payload,hook.Run())
        done=load('jev_done_checks')
        self.assertEqual(done.check_done_claim('Done',captured[0])['verdict'],'supported')
        (self.root/'result.json').write_text('{"count":3}')
        self.assertEqual(done.check_done_claim('Done',captured[0])['verdict'],'unsupported')

    def test_supervisor_runs_deterministic_stop_without_paid_registration(self):
        spec = importlib.util.spec_from_file_location('det_supervisor', REPO/'hooks/jev-supervisor.py')
        hook = importlib.util.module_from_spec(spec); spec.loader.exec_module(hook)
        import io
        with patch.object(hook.POLICY,'call_site_enabled',return_value=False), patch.object(hook,'stop') as stop, patch.object(hook,'judgment_point',return_value=True), patch.object(hook.sys,'stdin',io.StringIO(json.dumps({'hook_event_name':'Stop','session_id':'fixture'}))):
            hook.main()
        stop.assert_called_once()

    def test_slice_matches_production_member_projection(self):
        marker = load('slice-done-marker').Marker(call=NoModel(),git_run=NoModel())
        def member(i):
            # Exactly the six fields ops.read_slice_done_state projects.
            return {'id':f'member-{i}','release_key':'rel','commit_sha':str(i)*40,'pr_number':i,
                    'subject':'does everything','attribution':'explicit'}
        item={'proposed_id':'V5-F08'}
        self.assertEqual(marker.match(item,['a','b'],[member(1)]),{'a':'member-1','b':'member-1'})
        self.assertEqual(marker.match(item,['a'],[member(1),member(2)]),{'a':None})
        self.assertEqual(marker.match(item,['a'],[]),{'a':None})


if __name__ == '__main__':
    unittest.main()
