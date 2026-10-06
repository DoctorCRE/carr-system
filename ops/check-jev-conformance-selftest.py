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
