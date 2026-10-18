"""Offline regression tests for the semantic request seam and source checker."""
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Conformance(unittest.TestCase):
    def test_try_prefix_calls_reach_exception_handlers(self):
        checker = load('check-jev-conformance')
        for call in ("ask(state, {questions}, caller='fixture', version='v1')",
                     "evaluate(JudgmentRequest(state, {questions}, caller='fixture', version='v1'))"):
            source = "from jev_semantic import ask, evaluate, JudgmentRequest\n" + f"""def run(state, qa, qb):
 try:
  {call.format(questions='qa')}
  raise ValueError()
 except ValueError:
  {call.format(questions='qb')}
"""
            with self.subTest(call=call):
                self.assertEqual(checker.python_errors(source),
                                 ['7: fanout: combine all questions for this state'])

    def test_exception_handlers_retain_prefix_request_bindings(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(a, b, qa, qb):
 request = R(a, qb, caller='fixture', version='v1')
 try:
  request = R(b, qb, caller='fixture', version='v1')
  E(R(b, qa, caller='fixture', version='v1'))
  request = R(a, qb, caller='fixture', version='v1')
 except ValueError:
  E(request)
"""
        self.assertEqual(checker.python_errors(source),
                         ['9: fanout: combine all questions for this state'])

    def test_nested_try_prefix_calls_reach_exception_handlers(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import ask
def run(state, qa, qb, flag):
 try:
  if flag:
   ask(state, qa, caller='fixture', version='v1')
   raise ValueError()
 except ValueError:
  ask(state, qb, caller='fixture', version='v1')
"""
        self.assertEqual(checker.python_errors(source),
                         ['8: fanout: combine all questions for this state'])

    def test_handler_bodies_and_else_are_exclusive(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import ask
def run(state, qa, qb):
 try:
  may_fail()
 except ValueError:
  ask(state, qa, caller='fixture', version='v1')
 except TypeError:
  ask(state, qb, caller='fixture', version='v1')
 else:
  ask(state, qb, caller='fixture', version='v1')
"""
        self.assertEqual(checker.python_errors(source), [])
        self.assertEqual(checker.python_errors(source +
                         " ask(state, qa, caller='fixture', version='v1')\n"),
                         ['11: fanout: combine all questions for this state'])

    def test_try_else_and_finally_preserve_executed_calls(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import ask
def run(state, other, qa, qb):
 try:
  ask(state, qa, caller='fixture', version='v1')
 except ValueError:
  ask(other, qb, caller='fixture', version='v1')
 else:
  ask(state, qb, caller='fixture', version='v1')
 finally:
  ask(other, qa, caller='fixture', version='v1')
"""
        self.assertEqual(checker.python_errors(source), [
            '10: fanout: combine all questions for this state',
            '8: fanout: combine all questions for this state'])

    def test_try_handler_distinct_states_remain_accepted(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import ask
def run(state, other, qa, qb):
 try:
  ask(state, qa, caller='fixture', version='v1')
  raise ValueError()
 except ValueError:
  ask(other, qb, caller='fixture', version='v1')
"""
        self.assertEqual(checker.python_errors(source), [])
        self.assertEqual(checker.python_errors(source.replace('ask(other, qb,', 'ask(state, qb,')),
                         ['7: fanout: combine all questions for this state'])

    def test_except_star_handlers_can_execute_together(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import ask
def run(state, other, qa, qb):
 try:
  may_fail()
 except* ValueError:
  ask(state, qa, caller='fixture', version='v1')
 except* TypeError:
  ask(state, qb, caller='fixture', version='v1')
"""
        self.assertEqual(checker.python_errors(source),
                         ['8: fanout: combine all questions for this state'])
        self.assertEqual(checker.python_errors(source.replace('ask(state, qb,', 'ask(other, qb,')), [])

    def test_unhandled_try_prefix_bindings_reach_finally(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(a, b, qa, qb):
 request = R(a, qb, caller='fixture', version='v1')
 try:
  E(R(a, qa, caller='fixture', version='v1'))
  request = R(b, qb, caller='fixture', version='v1')
 finally:
  E(request)
"""
        self.assertEqual(checker.python_errors(source),
                         ['8: fanout: combine all questions for this state'])

    def test_nested_call_effects_reach_handlers_before_assignment(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(a, b, qa, qb):
 request = R(a, qb, caller='fixture', version='v1')
 try:
  request = build_request(E(R(a, qa, caller='fixture', version='v1')), b)
 except ValueError:
  E(request)
"""
        self.assertEqual(checker.python_errors(source), [
            '7: cache: semantic call needs caller/version',
            '7: fanout: combine all questions for this state'])

    def test_failed_match_guard_keeps_calls_for_later_cases(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(state, qa, qb):
 match state:
  case _ if E(R(state, qa, caller='fixture', version='v1')) and False:
   pass
  case _:
   E(R(state, qb, caller='fixture', version='v1'))
"""
        self.assertEqual(checker.python_errors(source),
                         ['7: fanout: combine all questions for this state'])
        next_guard = source.replace('  case _:\n',
            "  case _ if E(R(state, qb, caller='fixture', version='v1')):\n").replace(
            "   E(R(state, qb, caller='fixture', version='v1'))", '   pass')
        self.assertEqual(checker.python_errors(next_guard),
                         ['6: fanout: combine all questions for this state'])
        distinct = source.replace('def run(state, qa, qb):', 'def run(state, other, qa, qb):').replace(
            'R(state, qb,', 'R(other, qb,')
        self.assertEqual(checker.python_errors(distinct), [])

    def test_failed_match_guard_keeps_calls_after_unmatched_case(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(state, qa, qb):
 match state:
  case _ if E(R(state, qa, caller='fixture', version='v1')) and False:
   pass
 E(R(state, qb, caller='fixture', version='v1'))
"""
        self.assertEqual(checker.python_errors(source),
                         ['6: fanout: combine all questions for this state'])

    def test_failed_guard_skips_mutually_exclusive_later_patterns(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(state, qa, qb, flag):
 match flag:
  case True if E(R(state, qa, caller='fixture', version='v1')) and False:
   pass
  case False:
   E(R(state, qb, caller='fixture', version='v1'))
"""
        self.assertEqual(checker.python_errors(source), [])
        # 1 == True, so a value pattern can still follow a failed True guard.
        for pattern in ('1', '_', 'False | True', 'None | (True as x)'):
            with self.subTest(pattern=pattern):
                self.assertEqual(checker.python_errors(source.replace('case False:', f'case {pattern}:')),
                                 ['7: fanout: combine all questions for this state'])
        unmatched = source.replace("  case False:\n   E(R(state, qb, caller='fixture', version='v1'))\n",
                                   "  case False:\n   pass\n E(R(state, qb, caller='fixture', version='v1'))\n")
        self.assertEqual(checker.python_errors(unmatched),
                         ['8: fanout: combine all questions for this state'])

    def test_match_case_bodies_do_not_flow_into_later_cases(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(state, qa, qb):
 match state:
  case True:
   E(R(state, qa, caller='fixture', version='v1'))
  case _ if E(R(state, qb, caller='fixture', version='v1')):
   pass
"""
        self.assertEqual(checker.python_errors(source), [])

    def test_literal_state_reassignments_preserve_equivalence(self):
        checker = load('check-jev-conformance')
        for literal in ("{'text': 'same', 'nested': [1, None]}",
                        "['same', -1]", "('same', 1)", "'same'", "42"):
            source = f"""from jev_semantic import JudgmentRequest as R, evaluate as E
def run(qa, qb):
 state = {literal}
 E(R(state, qa, caller='fixture', version='v1'))
 state: object = {literal}
 E(R(state, qb, caller='fixture', version='v1'))
"""
            with self.subTest(literal=literal):
                self.assertEqual(checker.python_errors(source),
                                 ['6: fanout: combine all questions for this state'])
        distinct = source.replace('state: object = 42', 'state: object = 43')
        self.assertEqual(checker.python_errors(distinct), [])

    def test_exhaustive_match_preserves_request_bindings(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(a, b, q, flag):
 match flag:
  case True:
   request = R(a, q, caller='fixture', version='v1')
  case _:
   request = R(b, q, caller='fixture', version='v1')
 E(request)
"""
        self.assertEqual(checker.python_errors(source), [])
        self.assertEqual(checker.python_errors(source +
                         " E(R(a, q, caller='fixture', version='v1'))\n"),
                         ['9: fanout: combine all questions for this state'])
        for pattern in ('selected', '_ as selected', 'True | selected',
                        '(True | False) as selected'):
            with self.subTest(pattern=pattern):
                single = source.replace('case True:', f'case {pattern}:')
                single = single[:single.index('  case _:')] + ' E(request)\n'
                expected = (['6: cache: semantic call needs caller/version']
                            if pattern == '(True | False) as selected' else [])
                self.assertEqual(checker.python_errors(single), expected)

    def test_partial_and_guarded_matches_keep_unmatched_bindings(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(a, b, q, flag):
 request = R(a, q, caller='fixture', version='v1')
 E(request)
 match flag:
  case True:
   request = R(b, q, caller='fixture', version='v1')
 E(request)
"""
        for pattern in ('True', '_ if flag'):
            with self.subTest(pattern=pattern):
                self.assertEqual(checker.python_errors(source.replace('case True:', f'case {pattern}:')),
                                 ['8: fanout: combine all questions for this state'])
        no_prior = source.replace(" request = R(a, q, caller='fixture', version='v1')\n E(request)\n", '')
        self.assertEqual(checker.python_errors(no_prior),
                         ['6: cache: semantic call needs caller/version'])

    def test_request_bindings_are_resolved_at_each_call(self):
        checker = load('check-jev-conformance')
        prefix = "from jev_semantic import JudgmentRequest as R, evaluate as E\n"
        distinct = prefix + """def run(a, b, q):
 r = R(a, q, caller='fixture', version='v1')
 E(r)
 r = R(b, q, caller='fixture', version='v1')
 E(r)
"""
        with self.subTest(case='distinct states'):
            self.assertEqual(checker.python_errors(distinct), [])
        split = prefix + """def run(a, b, qa, qb, adapter):
 r = R(a, qa, caller='fixture', version='v1')
 E(r, adapter=adapter)
 s = R(a, qb, caller='fixture', version='v1')
 E(s, adapter=adapter)
 r = R(b, qa, caller='fixture', version='v1')
 return r
"""
        self.assertEqual(checker.python_errors(split),
                         ['6: fanout: combine all questions for this state'])

    def test_annotated_requests_and_branch_bindings(self):
        checker = load('check-jev-conformance')
        prefix = 'from jev_semantic import JudgmentRequest, evaluate\n'
        annotated = prefix + """def run(state, questions):
 request: JudgmentRequest = JudgmentRequest(state, questions, caller='fixture', version='v1')
 evaluate(request)
"""
        with self.subTest(case='annotated request'):
            self.assertEqual(checker.python_errors(annotated), [])
        branch = prefix + """def run(a, b, q, flag):
 request = JudgmentRequest(a, q, caller='fixture', version='v1')
 evaluate(request)
 if flag:
  request = JudgmentRequest(b, q, caller='fixture', version='v1')
 evaluate(request)
"""
        self.assertEqual(checker.python_errors(branch),
                         ['7: fanout: combine all questions for this state'])
        exclusive = prefix + """def run(a, q, flag):
 if flag:
  evaluate(JudgmentRequest(a, q, caller='fixture', version='v1'))
 else:
  evaluate(JudgmentRequest(a, q, caller='fixture', version='v1'))
"""
        self.assertEqual(checker.python_errors(exclusive), [])

    def test_known_transports_named_evaluate_keep_model_and_cache_checks(self):
        checker = load('check-jev-conformance')
        for module, target in (('typesafe_client', 'ask'), ('jev_judge', 'judge')):
            source = f'from {module} import {target} as evaluate\ndef run(state, questions):\n return evaluate(state, questions)\n'
            with self.subTest(module=module):
                self.assertEqual(checker.python_errors(source), [
                    '3: cache: use jev_semantic.ask with complete input key',
                    '3: model: pin jev-1.13.0'])
        self.assertEqual(checker.python_errors(
            "import typesafe_client\ndef evaluate(state):\n return state\ndef run(state):\n return evaluate(state)"), [])

    def test_dynamic_semantic_imports_keep_loop_and_provenance_checks(self):
        checker = load('check-jev-conformance')
        for loader in ("import importlib\n", "from importlib import import_module\n"):
            factory = 'importlib.import_module' if loader.startswith('import ') else 'import_module'
            source = loader + f"""def run(state, question_sets):
 api = {factory}('jev_semantic')
 for questions in question_sets:
  api.evaluate(api.JudgmentRequest(state, questions, caller='fixture', version='v1'))
"""
            with self.subTest(loader=loader):
                self.assertEqual(checker.python_errors(source),
                                 ['5: fanout: loop repeats unchanged state'])

    def test_loop_with_new_evidence_and_frozen_request_state(self):
        checker = load('check-jev-conformance')
        source = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(states, questions):
 for state in states:
  evidence = {'text': state}
  request = R(evidence, questions, caller='fixture', version='v1')
  E(request)
"""
        self.assertEqual(checker.python_errors(source), [])
        frozen = """from jev_semantic import JudgmentRequest as R, evaluate as E
def run(a, b, q):
 state = a
 request = R(state, q, caller='fixture', version='v1')
 state = b
 E(request)
 E(R(a, q, caller='fixture', version='v1'))
"""
        self.assertEqual(checker.python_errors(frozen),
                         ['7: fanout: combine all questions for this state'])

    def test_branch_loaded_transport_cannot_inherit_semantic_exemption(self):
        checker = load('check-jev-conformance')
        source = """import importlib
def run(state, questions, flag):
 if flag:
  api = importlib.import_module('jev_semantic')
 else:
  api = importlib.import_module('typesafe_client')
 api.ask(state, questions, caller='fixture', version='v1')
"""
        self.assertEqual(checker.python_errors(source), [
            '7: cache: use jev_semantic.ask with complete input key',
            '7: model: pin jev-1.13.0'])

    def test_scan_separates_cache_probe_suites_from_production_violations(self):
        checker = load('check-jev-conformance')
        # CI collects both test naming styles. Cache probes deliberately repeat
        # requests and may load the interface dynamically to inject transports.
        probes = """from importlib import import_module
api = import_module('jev_semantic')
def probe(state, questions):
 api.ask(state, questions, caller='fixture', version='v1')
 api.ask(state, questions, caller='fixture', version='v1')
"""
        violations = """import typesafe_client as api
def run(state, questions):
 api.ask(state, questions)
 api.ask(state, questions)
"""
        sources = {
            'tools/test-cache.py': probes,
            'tools/test_cache.py': probes,
            'tools/cache-runtime.py': violations,
            'tools/contest-cache.py': violations,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for rel, source in sources.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            with patch.object(checker.subprocess, 'check_output',
                              return_value='\n'.join(sources)):
                errors = checker.scan(root)
        self.assertEqual(errors, [rel + ':' + error
                         for rel in ('tools/cache-runtime.py', 'tools/contest-cache.py')
                         for error in checker.python_errors(violations)])
        for kind in ('model', 'cache', 'fanout'):
            self.assertTrue(any(kind in error for error in errors), errors)

    def test_semantic_imports_and_independent_function_scopes(self):
        checker = load('check-jev-conformance')
        for source in (
            "from jev_semantic import ask as ask_once\ndef run(s,q):\n return ask_once(s,q,caller='x',version='v1')",
            "import jev_semantic as api\ndef run(s,q):\n return api.ask(s,q,caller='x',version='v1')",
            "import jev_semantic as semantic\ndef outer(s,q):\n def a():\n  return semantic.ask(s,q,caller='x',version='v1')\n def b():\n  return semantic.ask(s,q,caller='x',version='v1')",
        ):
            with self.subTest(source=source):
                self.assertEqual(checker.python_errors(source), [])
        # Imports inside sibling functions cannot confer a semantic exemption.
        source = """def a(s,q):
 from jev_semantic import ask
 return ask(s,q,caller='x',version='v1')
def b(s,q):
 from typesafe_client import ask
 return ask(s,q,caller='x',version='v1')
"""
        errors = checker.python_errors(source)
        self.assertTrue(any('6: model' in error for error in errors), errors)

    def test_request_receipt_interface_preserves_provenance_and_fanout_checks(self):
        checker = load('check-jev-conformance')
        valid = """from jev_semantic import evaluate as run, JudgmentRequest as Request
def boundary(state, questions):
 request = Request(state, questions, caller='jev_session_watch', version='v1')
 return run(request)
"""
        self.assertEqual(checker.python_errors(valid), [])
        invalid = """import jev_semantic as semantic
def boundary(state, questions):
 semantic.evaluate(semantic.JudgmentRequest(state, questions))
 semantic.evaluate(semantic.JudgmentRequest(state, questions))
"""
        errors = checker.python_errors(invalid)
        for kind in ('caller', 'version', 'fanout'):
            self.assertTrue(any(kind in error for error in errors), errors)
        keyword_state = """import jev_semantic as semantic
def boundary(state, questions):
 one = semantic.JudgmentRequest(state=state, questions=questions, caller='x', version='v1')
 two = semantic.JudgmentRequest(state=state, questions=questions, caller='x', version='v1')
 semantic.evaluate(one)
 semantic.evaluate(two)
"""
        self.assertTrue(any('fanout' in error for error in checker.python_errors(keyword_state)))

    def test_loop_cannot_repeat_unchanged_state(self):
        checker = load('check-jev-conformance')
        errors = checker.python_errors("""import typesafe_client as tsc

def boundary(state, question_sets):
    for questions in question_sets:
        tsc.ask(state, questions, model='jev-1.13.0', cache_key=key)
""")
        self.assertTrue(any('fanout' in error for error in errors))

    def test_cache_binds_all_inputs_and_order(self):
        api = load('jev_semantic')
        calls = []
        def fake(state, questions, **kw):
            calls.append((state, questions, kw))
            return {'model': api.MODEL, 'answers': {k: ({'type':'choice','choice':'a'} if questions[k]['type']=='choice' else {'type':'noul','noul':.9}) for k in questions}}
        with tempfile.TemporaryDirectory() as tmp:
            opts = dict(transport=fake, caller='fixture', version='v1', cache_path=tmp+'/cache.json')
            q = {'q': {'type':'choice', 'instructions':'Which?', 'criteria': {'z':'Z', 'a':'A'}}}
            a = api.ask({'text':'one'}, q, **opts)
            b = api.ask({'text':'one'}, q, **opts)
            self.assertTrue(b['cache_hit'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2]['model'], 'jev-1.13.0')
            self.assertEqual(list(calls[0][1]['q']['criteria']), ['a','z'])
            api.ask({'text':'two'}, q, **opts)
            api.ask({'text':'one'}, q, **{**opts, 'version':'v2'})
            api.ask({'text':'one'}, {'q':{**q['q'], 'instructions':'Changed'}}, **opts)
            self.assertEqual(len(calls), 4)
            self.assertTrue(a['advisory_only'])

    def test_injected_client_and_concurrent_repeat_cross_production_seam(self):
        from concurrent.futures import ThreadPoolExecutor
        import time
        api = load('jev_semantic')
        client = object()
        calls = []
        def fake(state, questions, **options):
            self.assertIs(options['client'], client)
            calls.append(state)
            time.sleep(.01)
            return {'model':api.MODEL,'answers':{'q':{'type':'noul','noul':.9}}}
        with tempfile.TemporaryDirectory() as tmp:
            def run(_):
                return api.ask({'text':'same'}, {'q':{'type':'noul'}}, caller='fixture',
                               version='v1',client=client,transport=fake,cache_path=tmp+'/cache')
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(run,range(4)))
            self.assertEqual(len(calls),1)
            self.assertEqual(sum(r.get('cache_hit',False) for r in results),3)
            with self.assertRaises(ValueError):
                api.ask({'text':'x'*100001},{'q':{'type':'noul'}},caller='fixture',
                        version='v1',transport=fake,cache_path=tmp+'/cache')
            self.assertEqual(len(calls),1)

    def test_flash_batches_only_bounded_semantic_questions(self):
        import os
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('flash_script',ROOT.parent/'tools/flash-script.py')
        flash = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(flash)
        calls = []
        class Client:
            @staticmethod
            def noul(instructions,**options):
                return {'type':'noul','instructions':instructions}
        class Judge:
            @staticmethod
            def judge(state,questions,**options):
                calls.append((state,questions,options))
                return {'model':'jev-1.13.0','answers':{k:{'type':'noul','noul':.99} for k in questions}}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CARR_JEV_SEMANTIC_CACHE=tmp+'/cache'):
            judge = flash.Jev(judge=Judge,client=Client)
            judge.flags('q'*10000,'out'*5000,'preview'*5000)
            judge.flags('q'*10000,'out'*5000,'preview'*5000)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2]['model'],'jev-1.13.0')
        self.assertEqual(len(calls[0][0]['question']),6000)
        self.assertEqual(set(calls[0][1]),{'misparsed','exploring','answer_ready'})

    def test_partial_and_wrong_model_never_cached(self):
        api = load('jev_semantic')
        with tempfile.TemporaryDirectory() as tmp:
            for response in ({'answers':{}}, {'model':'jev-latest', 'answers':{'q':{}}}):
                with self.assertRaises(ValueError):
                    api.ask({}, {'q':{'type':'noul'}}, transport=lambda *a, **k: response,
                            caller='fixture', version='v1', cache_path=tmp+'/cache')

    def test_file_fixture_has_all_three_violation_kinds(self):
        checker=load('check-jev-conformance')
        errors=checker.python_errors((ROOT/'fixtures/jev-conformance/violations.py').read_text())
        self.assertTrue(all(any(kind in error for error in errors) for kind in ('model','cache','fanout')))

    def test_worker_violations_and_split_cached_requests(self):
        checker=load('check-jev-conformance')
        errors=checker.javascript_errors('async function run(){ await askJev(request); }')
        self.assertTrue(all(any(kind in e for e in errors) for kind in ('model','cache')))
        errors=checker.javascript_errors('async function run(){ await cachedSemanticAsk(askJev,request,"v1"); await cachedSemanticAsk(askJev,request,"v1"); }')
        self.assertTrue(any('fanout' in e for e in errors))

    def test_checker_rejects_each_violation(self):
        checker = load('check-jev-conformance')
        examples = {
            'model': 'import typesafe_client as ts\ndef run(s,q):\n ts.ask(s,q,cache_key="x")',
            'cache': 'import typesafe_client as ts\ndef run(s,q):\n ts.ask(s,q,model="jev-1.13.0")',
            'semantic-fanout': 'import jev_semantic\ndef run(s,q):\n jev_semantic.ask(s,q,caller="x",version="v1")\n jev_semantic.ask(s,q,caller="x",version="v1")',
            'fanout': 'import typesafe_client as ts\ndef run(s,q):\n ts.ask(s,q,model="jev-1.13.0",cache_key="x")\n ts.ask(s,q,model="jev-1.13.0",cache_key="x")',
        }
        for rule, source in examples.items():
            with self.subTest(rule=rule):
                self.assertTrue(any(rule.split("-")[-1] in e for e in checker.python_errors(source)))
        self.assertEqual(checker.python_errors('import jev_semantic\ndef run(s,q):\n return jev_semantic.ask(s,q,caller="x",version="v1")'), [])

if __name__ == '__main__':
    unittest.main()
