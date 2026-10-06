#!/usr/bin/env python3
"""Offline suite for ops/jev_scorecard.py. No credential, no network, no spend.

The HTTP call to the local flash server is mocked through run_task's own
`chat_opener` hook (the same convention ops/typesafe_client.py's `ask(...,
opener=...)` uses), so this never dials out. Covers: extracting code from a
model reply, grading an implementation task and a write-tests (mutation) task
in a real temp directory, the batched-noul fuzzy grader against a fake Jev
client, and the deterministic summary arithmetic.
"""

import importlib.util
import io
import json
import unittest
import urllib.error
from pathlib import Path

OPS = Path(__file__).resolve().parent
MODULE_PATH = OPS / "jev_scorecard.py"
SPEC = importlib.util.spec_from_file_location("jev_scorecard", MODULE_PATH)
assert SPEC and SPEC.loader
sc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sc)


import os as _sem_os
import tempfile as _sem_tmp
from unittest.mock import patch as _sem_patch
class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        with _sem_tmp.TemporaryDirectory() as root, _sem_patch.dict(_sem_os.environ, CARR_JEV_SEMANTIC_CACHE=root+"/cache"):
            return super().run(result)


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _chat_opener_returning(content, usage=None):
    def opener(request, timeout=None):
        return _FakeResponse({"choices": [{"message": {"content": content}}],
                               "usage": usage or {"total_tokens": 42}})
    return opener


def _chat_opener_raising_http():
    def opener(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 500, "boom", {}, io.BytesIO(b"server exploded"))
    return opener


def _chat_opener_raising_connection():
    def opener(request, timeout=None):
        raise urllib.error.URLError("connection refused")
    return opener


IMPL_TASK = {
    "id": "t_add", "category": "demo", "lang": "py", "kind": "impl",
    "prompt": "write add(a, b)",
    "test": 'check("basic", lambda: add(1, 2) == 3)\n'
            'check("neg", lambda: add(-1, -1) == -2)\n',
}

MUTATION_TASK = {
    "id": "t_wrap", "category": "demo", "lang": "py", "kind": "mutation",
    "prompt": "write tests for f",
    "impl": "def f(x):\n    return x + 1\n",
    "mutants": ["def f(x):\n    return x + 2\n", "def f(x):\n    return x + 1\n"],
}


class ExtractCodeTests(SemanticTestCase):
    def test_pulls_the_fenced_block(self):
        text = "here you go\n```python\ndef f():\n    return 1\n```\nthanks"
        self.assertIn("def f():", sc.extract_code(text, lang="py"))

    def test_strips_a_think_block_first(self):
        text = "<think>plan the approach</think>```python\ndef g():\n    pass\n```"
        code = sc.extract_code(text, lang="py")
        self.assertNotIn("plan the approach", code)
        self.assertIn("def g():", code)

    def test_prefers_the_larger_block_when_untagged(self):
        text = "```\nshort\n```\n```\nmuch longer block of code here\n```"
        self.assertEqual(sc.extract_code(text).strip(), "much longer block of code here")

    def test_no_fence_returns_the_stripped_text(self):
        self.assertEqual(sc.extract_code("  bare code  \n"), "bare code")

    def test_a_short_lang_code_matches_a_full_fence_tag(self):
        text = "```javascript\nconsole.log(1)\n```"
        self.assertIn("console.log", sc.extract_code(text, lang="js"))


class LoadSuiteTests(SemanticTestCase):
    def test_the_real_suite_loads_and_is_not_empty(self):
        tasks = sc.load_suite()
        self.assertGreater(len(tasks), 5)
        self.assertTrue(all(t.get("id") and t.get("prompt") for t in tasks))

    def test_every_task_has_a_grading_path(self):
        for task in sc.load_suite():
            if task.get("kind") == "mutation":
                self.assertIn("impl", task)
                self.assertIn("mutants", task)
            else:
                self.assertIn("test", task)


class GradeImplTests(SemanticTestCase):
    def test_python_returned_containers_preserve_caller_owned_aliases(self):
        task = {'lang': 'py', 'test': '''data = [1]
check("nested caller identity", lambda: wrap(data)[0] is data)
'''}
        result = sc.grade_candidate(task, 'def wrap(data): return [data]\n')
        self.assertTrue(result['pass'], result)
        copied = sc.grade_candidate(task, 'def wrap(data): return [list(data)]\n')
        self.assertFalse(copied['pass'], copied)

    def test_python_positional_and_keyword_arguments_share_identity(self):
        task = {'lang': 'py', 'test': '''data = [1]
check("argument identity", lambda: same(data, b=data))
'''}
        result = sc.grade_candidate(task, 'def same(a, *, b): return a is b\n')
        self.assertTrue(result['pass'], result)

    def test_python_aliases_span_returned_values_and_input_updates(self):
        task = {'lang': 'py', 'test': '''child = {"value": 1}
data = [child, child]
result = mutate(data, child=child)
check("returned caller alias", lambda: result[0] is child)
check("input aliases", lambda: data[0] is child and data[1] is child)
check("new shared alias", lambda: result[1] is child["added"] and data[2] is result[1])
check("new value", lambda: result[1]["value"] == 3)
check("tuple alias", lambda: result[2][0] is child)
cycle = []; cycle.append(cycle)
check("cycle", lambda: wrap(cycle)[0] is cycle and cycle[0] is cycle)
'''}
        code = '''def wrap(data): return [data]
def mutate(data, *, child):
    if data[0] is not child or data[1] is not child: raise ValueError("lost alias")
    child["added"] = {"value": 3}
    data.append(child["added"])
    return [child, child["added"], (child,)]
'''
        result = sc.grade_candidate(task, code)
        self.assertTrue(result['pass'], result)

    def test_hidden_imports_cannot_load_candidate_into_grader(self):
        cases = {
            'py': ('from solution import add\ncheck("must fail", lambda: False)', '''import sys, json
if sys.argv[0] == '-':
    print(json.dumps({'completed':True, 'total':1, 'passed':1}))
    raise SystemExit(0)
def add(a,b): return a+b
'''),
            'js': ('const {add} = require("./solution"); check("must fail", () => false);', '''
if (!process.execArgv.includes('-e')) {
  console.log(JSON.stringify({completed:true,total:1,passed:1}));
  process.exit(0);
}
module.exports = {add:(a,b) => a+b};
'''),
        }
        for lang, (test, code) in cases.items():
            with self.subTest(lang=lang):
                self.assertFalse(sc.grade_candidate({'lang':lang, 'test':test}, code)['pass'])

    def test_deep_input_does_not_exhaust_the_grader_transport(self):
        task = {'lang':'py', 'test':'''data = 1
for _ in range(3000): data = [data]
check("deep", lambda: unwrap(data) == 1)
'''}
        code = 'def unwrap(data):\n    while isinstance(data, list): data = data[0]\n    return data\n'
        result = sc.grade_candidate(task, code)
        self.assertTrue(result['pass'], result)

    def test_identity_tuple_keys_exceptions_and_objects_keep_their_behavior(self):
        task = {'lang':'py', 'test':'''data = {(1,): [2]}
check("identity", lambda: identity(data) is data)
check("key", lambda: identity(data)[(1,)] == [2])
check("error", lambda: raises(ValueError, reject))
def state():
    c = Counter()
    return c.next() == 1 and c.next() == 2
check("state", state)
'''}
        code = '''def identity(data): return data
def reject(): raise ValueError('fixture')
class Counter:
    def __init__(self): self.n = 0
    def next(self):
        self.n += 1
        return self.n
'''
        self.assertTrue(sc.grade_candidate(task, code)['pass'])

    def test_hidden_assertions_observe_candidate_input_mutations(self):
        task = {'lang':'py', 'test':'''data = [2, 1]
check("result", lambda: sort_values(data) == [1, 2])
check("input unchanged", lambda: data == [2, 1])
'''}
        result = sc.grade_candidate(task, 'def sort_values(data):\n    data.sort()\n    return data\n')
        self.assertFalse(result['pass'], result)
        self.assertIn('1/2', result['subtests'])
        self.assertIn('FAIL input unchanged', result['detail'])
        self.assertTrue(sc.grade_candidate(task, 'def sort_values(data):\n    return sorted(data)\n')['pass'])

    def test_javascript_hidden_assertions_observe_candidate_input_mutations(self):
        task = {'lang':'js', 'test':'''const {sort_values} = require("./solution.js");
const data = [2, 1];
check("result", () => JSON.stringify(sort_values(data)) === '[1,2]');
check("input unchanged", () => JSON.stringify(data) === '[2,1]');
'''}
        result = sc.grade_candidate(task, 'module.exports = {sort_values:data => data.sort()};')
        self.assertFalse(result['pass'], result)
        self.assertEqual(result['subtests'], 'PASSED 1/2')
        self.assertIn('FAIL input unchanged', result['detail'])
        control = sc.grade_candidate(task, 'module.exports = {sort_values:data => [...data].sort()};')
        self.assertTrue(control['pass'], control)

    def test_javascript_argument_aliases_mutations_and_result_identity(self):
        task = {'lang':'js', 'test':'''const {mutate, identity, reject} = require("./solution.js");
const child = {value:1}; const data = [child, child]; const held = data[0];
check("input identity", () => identity(data) === data);
check("aliases", () => mutate(data, child) === child);
check("nested update", () => held.value === 2 && data[0] === held && data[1] === held);
check("new nested object", () => data[2] === held.added && held.added.value === 3);
check("exception", () => raises(() => reject(child)));
check("mutation before exception", () => held.value === 4);
const cycle = {}; cycle.self = cycle;
check("cycle", () => identity(cycle) === cycle && cycle.self === cycle);
'''}
        code = '''module.exports = {
  identity:data => data,
  mutate:(data, child) => {
    if (data[0] !== child || data[1] !== child) throw new Error('lost alias');
    child.value = 2; child.added = {value:3}; data.push(child.added); return child;
  },
  reject:child => { child.value = 4; throw new Error('fixture'); }
};'''
        result = sc.grade_candidate(task, code)
        self.assertTrue(result['pass'], result)

    def test_javascript_frozen_inputs_and_detached_alias_updates(self):
        task = {'lang':'js', 'test':'''const {identity, detach} = require("./solution.js");
const frozen = Object.freeze({value:1});
check("frozen identity", () => identity(frozen) === frozen);
const held = {value:1}; const data = [held];
check("detach", () => detach(data) === 7);
check("removed input", () => data.length === 0);
check("detached update", () => held.value === 2);
'''}
        code = 'module.exports = {identity:x => x, detach:data => {data[0].value = 2; data.pop(); return 7;}};'
        result = sc.grade_candidate(task, code)
        self.assertTrue(result['pass'], result)

    def test_javascript_non_ascii_arguments_keep_their_codepoints(self):
        task = {'lang':'js', 'test':'''const {codepoints} = require("./solution.js");
check("codepoints", () => JSON.stringify(codepoints("é漢😀")) === '[233,28450,128512]');
'''}
        result = sc.grade_candidate(task, 'module.exports = {codepoints:s => [...s].map(c => c.codePointAt(0))};')
        self.assertTrue(result['pass'], result)

    def test_javascript_non_ascii_returns_and_errors_keep_their_codepoints(self):
        task = {'lang':'js', 'test':'''const {text, reject} = require("./solution.js");
check("return", () => text() === "é漢😀");
check("error", () => { try { reject(); } catch (e) { return e.message === "é漢😀"; } return false; });
'''}
        code = 'module.exports = {text:() => "é漢😀", reject:() => {throw new Error("é漢😀");}};'
        result = sc.grade_candidate(task, code)
        self.assertTrue(result['pass'], result)

    def test_javascript_candidate_state_persists_between_assertions(self):
        task = {'lang':'js', 'test':'''const {next} = require("./solution.js");
check("first", () => next() === 1);
check("second", () => next() === 2);
'''}
        result = sc.grade_candidate(task, 'let n = 0; module.exports = {next:() => ++n};')
        self.assertTrue(result['pass'], result)

    def test_candidate_cannot_forge_completion_file_and_exit_before_assertions(self):
        candidates = {
            'py': '''import re, json, os
source = open('test_hidden.py').read()
path = re.search(r"['\\\"]([^'\\\"]*completion.json)['\\\"]", source).group(1)
with open(path, 'w') as handle:
    json.dump({'completed':True, 'total':1, 'passed':1}, handle)
os._exit(0)
''',
            'js': '''const fs = require('fs');
const source = fs.readFileSync('test_hidden.js', 'utf8');
const path = source.match(/['"]([^'"]*completion.json)['"]/)[1];
fs.writeFileSync(path, JSON.stringify({completed:true, total:1, passed:1}));
process.exit(0);
''',
        }
        for lang, code in candidates.items():
            with self.subTest(lang=lang):
                assertion = 'check("must fail", lambda: False)' if lang == 'py' else 'require("./solution.js"); check("must fail", () => false);'
                result = sc.grade_candidate({'lang':lang, 'test':assertion}, code)
                self.assertFalse(result['pass'], result)

    def test_javascript_assertions_are_counted_by_the_grader(self):
        task = {'lang':'js', 'test':'const {add} = require("./solution.js"); check("sum", () => add(1,2) === 3);'}
        self.assertTrue(sc.grade_candidate(task, 'module.exports = {add:(a,b) => a+b};')['pass'])
        self.assertFalse(sc.grade_candidate(task, 'module.exports = {add:(a,b) => a-b};')['pass'])

    def test_a_correct_candidate_passes(self):
        result = sc.grade_candidate(IMPL_TASK, "def add(a, b):\n    return a + b\n")
        self.assertTrue(result["pass"])
        self.assertIn("2/2", result["subtests"])

    def test_a_wrong_candidate_fails(self):
        result = sc.grade_candidate(IMPL_TASK, "def add(a, b):\n    return a - b\n")
        self.assertFalse(result["pass"])

    def test_a_crashing_candidate_fails_without_raising(self):
        result = sc.grade_candidate(IMPL_TASK, "raise SyntaxError this is not python")
        self.assertFalse(result["pass"])


class GradeMutationTests(SemanticTestCase):
    def test_a_thorough_suite_kills_every_mutant_and_passes(self):
        suite = (
            "import unittest\nfrom solution import f\n"
            "class T(unittest.TestCase):\n"
            "    def test_f(self):\n        self.assertEqual(f(1), 2)\n"
        )
        result = sc.grade_candidate(MUTATION_TASK, suite)
        self.assertTrue(result["correct_passes"])
        self.assertEqual(result["killed"], "1/2")
        self.assertFalse(result["pass"], "one mutant (identical to impl) cannot be killed")

    def test_a_suite_that_fails_the_correct_impl_does_not_pass(self):
        suite = (
            "import unittest\nfrom solution import f\n"
            "class T(unittest.TestCase):\n"
            "    def test_f(self):\n        self.assertEqual(f(1), 999)\n"
        )
        result = sc.grade_candidate(MUTATION_TASK, suite)
        self.assertFalse(result["correct_passes"])
        self.assertFalse(result["pass"])


class RunTaskTests(SemanticTestCase):
    def test_a_passing_reply_is_graded_and_reported(self):
        opener = _chat_opener_returning("```python\ndef add(a, b):\n    return a + b\n```")
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=opener)
        self.assertTrue(out["any_pass"])
        self.assertTrue(out["first_pass"])
        self.assertEqual(out["id"], "t_add")
        self.assertEqual(len(out["attempts"]), 1)

    def test_multiple_attempts_are_each_graded(self):
        opener = _chat_opener_returning("```python\ndef add(a, b):\n    return a + b\n```")
        out = sc.run_task(IMPL_TASK, attempts=3, chat_opener=opener)
        self.assertEqual(len(out["attempts"]), 3)
        self.assertTrue(out["any_pass"])

    def test_an_http_failure_is_recorded_on_the_attempt_not_raised(self):
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=_chat_opener_raising_http())
        self.assertFalse(out["any_pass"])
        self.assertIn("HTTP 500", out["attempts"][0]["chat_error"])

    def test_a_connection_failure_is_recorded_on_the_attempt_not_raised(self):
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=_chat_opener_raising_connection())
        self.assertFalse(out["any_pass"])
        self.assertIn("chat_error", out["attempts"][0])

    def test_a_reply_with_no_usable_code_fails_gracefully(self):
        opener = _chat_opener_returning("sorry, I cannot help with that")
        out = sc.run_task(IMPL_TASK, attempts=1, chat_opener=opener)
        self.assertFalse(out["any_pass"])


class GradeFuzzyTests(SemanticTestCase):
    class _FakeJudge:
        JudgeUnavailable = RuntimeError
        SHADOW_LOG = "unused-in-tests"

        def __init__(self, probs=None, fail=False):
            self.probs = probs or {}
            self.fail = fail
            self.calls = []
            self.records = []

        def judge(self, subject, questions, **kwargs):
            self.calls.append((subject, questions))
            if self.fail:
                raise self.JudgeUnavailable("synthetic outage")
            answers = {qid: {"type": "noul", "noul": self.probs.get(qid, 0.5)}
                       for qid in questions}
            return {"model": "jev-1.13.0", "answers": answers}

        def record(self, *a, **kw):
            self.records.append((a, kw))

    class _FakeClient:
        @staticmethod
        def noul(instructions, true=None, false=None):
            return {"type": "noul", "instructions": instructions,
                    "criteria": {"true": true, "false": false}}

    def test_all_subchecks_satisfied_is_true(self):
        judge = self._FakeJudge({"c0": 0.95, "c1": 0.9})
        out = sc.grade_fuzzy("some output", ["mentions X", "is polite"],
                              client=self._FakeClient, judge=judge)
        self.assertEqual(out["verdict"], "review_required")
        self.assertTrue(out["escalate"])
        self.assertEqual(len(judge.calls), 1, "every sub-check batched into one request")

    def test_one_failing_subcheck_makes_the_verdict_false(self):
        judge = self._FakeJudge({"c0": 0.95, "c1": 0.1})
        out = sc.grade_fuzzy("some output", ["mentions X", "is polite"],
                              client=self._FakeClient, judge=judge)
        self.assertEqual(out["verdict"], "review_required")

    def test_an_ambiguous_subcheck_escalates(self):
        judge = self._FakeJudge({"c0": 0.5})
        out = sc.grade_fuzzy("some output", ["mentions X"],
                              client=self._FakeClient, judge=judge)
        self.assertTrue(out["escalate"])

    def test_no_subchecks_is_vacuously_true_and_does_not_ask_jev(self):
        judge = self._FakeJudge(fail=True)
        out = sc.grade_fuzzy("some output", [], client=self._FakeClient, judge=judge)
        self.assertIs(out["verdict"], True)
        self.assertEqual(len(judge.calls), 0)

    def test_an_outage_reports_unavailable_not_an_exception(self):
        judge = self._FakeJudge(fail=True)
        out = sc.grade_fuzzy("some output", ["mentions X"],
                              client=self._FakeClient, judge=judge)
        self.assertEqual(out["verdict"], "unavailable")
        self.assertTrue(out["escalate"])


class SummarizeTests(SemanticTestCase):
    def test_counts_by_category_and_overall(self):
        results = [
            {"category": "parser", "any_pass": True, "first_pass": True},
            {"category": "parser", "any_pass": False, "first_pass": False},
            {"category": "js", "any_pass": True, "first_pass": False},
        ]
        out = sc.summarize(results)
        self.assertEqual(out["by_category"]["parser"], {"n": 2, "any_pass": 1, "first_pass": 1})
        self.assertEqual(out["by_category"]["js"], {"n": 1, "any_pass": 1, "first_pass": 0})
        self.assertEqual(out["overall"], {"n": 3, "any_pass": 2, "first_pass": 1})

    def test_empty_results_summarize_to_zero(self):
        out = sc.summarize([])
        self.assertEqual(out["overall"], {"n": 0, "any_pass": 0, "first_pass": 0})


class EntrypointTests(SemanticTestCase):
    def test_the_module_is_not_a_script_entrypoint(self):
        import re as _re
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertFalse(source.startswith("#!"), "no shebang")
        guard = _re.compile(r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:")
        self.assertIsNone(guard.search(source), "no main guard")


if __name__ == "__main__":
    unittest.main(verbosity=1)
