"""Offline suite for ops/jev_code_partition.py and ops/jev_code_pilot_eval.py.
No credential, no network.

What it pins: the partition is deterministic and covers every line it is
given; functions are read whole when they fit and sliced under the cap when
they do not; except handlers, comment runs and module-level code are cut
without overlapping what another partition already carries; exact repeats are
judged once; partitions reach the judge through jev_code_review._review, one
request per region with every question in it; a proposed edit is checked in a
throwaway worktree and never touches the live tree; and the pilot scorer's
arithmetic (coverage, precision/recall, duplicate rate) is right on cases
whose answer is known in advance.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "ops"))
from git_env import fixture_env  # noqa:E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


part = load("jev_code_partition_selftest", "ops/jev_code_partition.py")
pilot = load("jev_code_pilot_eval_selftest", "ops/jev_code_pilot_eval.py")
review = load("jev_code_review_partition_selftest", "ops/jev_code_review.py")


def covered_lines(parts):
    out = set()
    for p in parts:
        out |= set(range(p["line"], p["end_line"] + 1))
    return out


class SemanticTestCase(unittest.TestCase):
    def run(self, result=None):
        from unittest.mock import patch
        import os
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CARR_JEV_SEMANTIC_CACHE=tmp+'/cache'):
            return super().run(result)

class FakeClient:
    def __init__(self, value=0.9, fail_on=None):
        self.value, self.fail_on, self.calls = value, fail_on, []

    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}

    def ask(self, state, questions, timeout=None, api_key=None, **kwargs):
        self.calls.append((state, questions))
        if self.fail_on and self.fail_on in state["region"]["code"]:
            raise RuntimeError("vendor down")
        return {"model": "jev-1.13.0", "usage": {"input_tokens": 100, "output_tokens": 8},
                "answers": {qid: {"type": "noul", "noul": self.value} for qid in questions}}


PY = textwrap.dedent('''\
    import os

    # A comment run that sits directly on the function below,
    # four lines long so it counts as a block,
    # and should be read together with that function
    # rather than as a slice of its own.
    def load(path):
        try:
            return open(path).read()
        except OSError:
            return None


    class Box:
        @staticmethod
        def make(value):
            def inner():
                return value * 2
            return inner()


    # Module-level configuration follows. This comment run
    # is not on a function, so it starts its own slice
    # with the code under it, and the long span before it
    # ends where it begins.
    LIMIT = int(os.environ.get("LIMIT", "10"))
    NAMES = [name.strip() for name in os.environ.get("NAMES", "").split(",")]
    ''')

JS = textwrap.dedent('''\
    const pattern = /[{}]+/g;
    const template = `open { only ${"a string {"} and ${ `nested {` } here`;
    // a brace in a comment {
    function parse(text) {
      try {
        return JSON.parse(text);
      } catch {
        return null;
      }
    }
    export const handler = async (request) => {
      if (request.ok) {
        return "{";
      }
      return "}";
    };
    class Store {
      read(key) {
        for (const item of this.items) { if (item.key === key) return item; }
        return null;
      }
    }
    ''')


class TrackedSources(SemanticTestCase):
    def test_git_inventory_preserves_unicode_and_newline_paths(self):
        names = ['café.js', 'λ.py', 'two\nlines.mjs', 'a\rb.py',
                 'a\r\nb.py', 'plain.js']
        with tempfile.TemporaryDirectory() as tmp:
            env = fixture_env()
            subprocess.run(['git', 'init', '-q', tmp], env=env, check=True)
            subprocess.run(['git', 'config', 'core.quotePath', 'true'], cwd=tmp, env=env, check=True)
            for name in names + ['ignored.txt', 'node_modules/dependency.js']:
                target = Path(tmp, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('const x = 1;\n')
                subprocess.run(['git', 'add', '--', name], cwd=tmp, env=env, check=True)
            for scanner in (review, part):
                with self.subTest(scanner=scanner.__name__):
                    self.assertEqual(set(scanner.tracked_sources(tmp)), set(names))

    def test_noise_comes_from_the_review_tier_map(self):
        # ops/config/review-tiers.v1.json: generated registries, vendored and
        # minified code are noise; migrations are never noise.
        kept = ['src/app.js', 'migrations/node_modules/keep.js', 'migrations/0001_fix.py']
        noise = ['mcp-server/src/scac-mutation-registry.v9.generated.js', 'lib/vendor/x.js',
                 'static/app.min.js', 'node_modules/dep.js']
        with tempfile.TemporaryDirectory() as tmp:
            env = fixture_env()
            subprocess.run(['git', 'init', '-q', tmp], env=env, check=True)
            for name in kept + noise:
                target = Path(tmp, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('const x = 1;\n')
                subprocess.run(['git', 'add', '--', name], cwd=tmp, env=env, check=True)
            self.assertEqual(set(part.tracked_sources(tmp)), set(kept))


class PythonSpans(SemanticTestCase):
    def test_functions_methods_nested_and_handlers(self):
        spans = part.python_spans(PY)
        functions = sorted((s, e) for k, s, e in spans if k == "function")
        excepts = [(s, e) for k, s, e in spans if k == "except_block"]
        names = {PY.splitlines()[s - 1].strip() for s, _ in functions}
        self.assertIn("def load(path):", names)
        self.assertIn("@staticmethod", names, "a decorated def starts at its decorator")
        self.assertIn("def inner():", names)
        self.assertEqual(len(excepts), 1)

    def test_syntax_error_is_none_not_a_crash(self):
        self.assertIsNone(part.python_spans("def broken(:\n"))


class JsSpans(SemanticTestCase):
    def test_escaped_newline_in_quoted_string_counts_toward_span(self):
        for quote in ('"', "'"):
            for newline in ('\n', '\r\n'):
                source = f'const s = {quote}a\\{newline}b{quote};\nfunction f() {{\n  return 7;\n}}\n'
                with self.subTest(quote=quote, newline=newline):
                    self.assertEqual(part.js_spans(source), [("function", 3, 5)])

    def test_braces_in_strings_templates_regex_and_comments_are_skipped(self):
        spans = part.js_spans(JS)
        self.assertIsNotNone(spans, "the scanner must balance this file")
        lines = JS.splitlines()
        heads = {(k, lines[s - 1].strip().split("(")[0].strip()) for k, s, _ in spans}
        self.assertIn(("function", "function parse"), heads)
        self.assertIn(("function", "export const handler = async"), heads)
        self.assertIn(("function", "read"), heads)
        self.assertIn(("except_block", "} catch {"), heads)
        self.assertFalse(any(lines[s - 1].strip().startswith(("if", "for")) and k == "function"
                             for k, s, _ in spans), "control blocks are not functions")

    def test_unbalanced_file_returns_none(self):
        self.assertIsNone(part.js_spans("function f() {\n  return 1;\n"))


class PartitionText(SemanticTestCase):
    def test_multiline_python_handler_keeps_complete_header_through_partition(self):
        header = 'except (\n    ValueError,\n    OSError,\n):'
        body = [f'    recovered_{i} = {i}' for i in range(200)]
        source = 'try:\n    work()\n' + header + '\n' + '\n'.join(body) + '\n'
        regions, _ = part.partition(['x.py'], reader=lambda _: source)
        handlers = [r for r in regions if 'except_block' in r['kind']]
        self.assertGreater(len(handlers), 1)
        self.assertTrue(all(r['code'].startswith(header + '\n') for r in handlers))
        self.assertEqual([line for r in handlers for line in r['code'][len(header)+1:].splitlines()], body)
        self.assertTrue(all(r['chars'] <= part.MAX_REGION_CHARS for r in handlers))
        self.assertTrue(all(r['sent_end_line'] == r['end_line'] for r in handlers))

    def test_multiline_js_catch_keeps_complete_binding_through_partition(self):
        header = 'catch (\n  err\n) {'
        body = [f'  recovered_{i} = handle(err, {i});' for i in range(200)] + ['}']
        source = 'try {\n  work();\n}\n' + header + '\n' + '\n'.join(body) + '\n'
        regions, _ = part.partition(['x.js'], reader=lambda _: source)
        handlers = [r for r in regions if 'except_block' in r['kind']]
        self.assertGreater(len(handlers), 1)
        self.assertTrue(all(r['code'].startswith(header + '\n') for r in handlers))
        self.assertEqual([line for r in handlers for line in r['code'][len(header)+1:].splitlines()], body)
        self.assertTrue(all(r['chars'] <= part.MAX_REGION_CHARS for r in handlers))
        self.assertTrue(all(r['sent_end_line'] == r['end_line'] for r in handlers))

    def test_oversized_handler_header_preserves_body_through_partition(self):
        source = 'try:\n    work()\nexcept Exception: #' + 'a' * 2700 + '\n    recover()\n    record_failure()\n'
        regions, _ = part.partition(['x.py'], reader=lambda _: source)
        sent = [line for region in regions for line in region['code'].splitlines()]
        self.assertIn('    recover()', sent)
        self.assertIn('    record_failure()', sent)
        self.assertTrue(all(len(region['code']) <= part.MAX_REGION_CHARS for region in regions))
        bodies = [r for r in regions if '    recover()' in r['code']]
        self.assertTrue(all(r['sent_end_line'] == r['end_line'] for r in bodies))

    def test_single_overlong_handler_line_is_retained_with_truthful_coverage(self):
        text = 'try:\n    x()\nexcept Exception: recovered = "' + 'a' * 3000 + '"\n'
        parts = part.partition_text('handler.py', text)
        handlers = [p for p in parts if 'except_block' in p['kind']]
        self.assertEqual(len(handlers), 1)
        self.assertTrue(handlers[0]['code'].startswith('except Exception:'))
        self.assertLess(handlers[0]['sent_end_line'], 3)
        self.assertLessEqual(handlers[0]['chars'], part.MAX_REGION_CHARS)

    def test_long_handler_slices_keep_header_and_every_body_line(self):
        prefix = ''.join(f'value_{i} = {i}\n' for i in range(12)) + 'try:\n    x()\n'
        header = 'except Exception:'
        body = [f'    recovered_{i} = {i}' for i in range(200)]
        text = prefix + header + '\n' + '\n'.join(body) + '\n'
        parts = part.partition_text('handler.py', text)
        handlers = [p for p in parts if 'except_block' in p['kind']]
        self.assertGreater(len(handlers), 1)
        self.assertEqual(handlers[0]['line'], 15)
        self.assertTrue(all(p['code'].splitlines()[0] == header for p in handlers))
        sent = [line for p in handlers for line in p['code'].splitlines()[1:]]
        self.assertEqual(sent, body)
        self.assertTrue(all(p['chars'] <= part.MAX_REGION_CHARS for p in parts))
        for p in parts:
            if 'except_block' not in p['kind']:
                self.assertFalse(set(p['code'].splitlines()) & set(body))
        regions, _ = part.partition(['handler.py'], reader=lambda _: text)
        self.assertTrue(all(p['code'].splitlines()[0] == header
                            for p in regions if 'except_block' in p['kind']))

    def test_python_file_is_covered_without_overlap(self):
        stats = {}
        parts = part.partition_text("x.py", PY, stats)
        substantive = {k for k, line in enumerate(PY.splitlines(), 1) if line.strip()}
        self.assertLessEqual(substantive, covered_lines(parts), "every non-blank line is sent")
        for a in parts:
            for b in parts:
                if a is not b:
                    overlap = set(range(a["line"], a["end_line"] + 1)) & \
                        set(range(b["line"], b["end_line"] + 1))
                    self.assertFalse(overlap, (a["kind"], b["kind"]))
        kinds = {p["kind"] for p in parts}
        self.assertIn("comment_block+function+except_block", kinds,
                      "comment on a function is read with it; the handler is folded in")
        self.assertIn("comment_block", kinds)
        self.assertEqual(stats.get("except_folded"), 1)

    def test_long_function_is_sliced_under_the_cap(self):
        body = "".join(f"    value_{i} = compute_something_long({i}, 'padding padding')\n"
                       for i in range(120))
        text = "def big():\n" + body + "    return value_0\n"
        parts = part.partition_text("big.py", text)
        self.assertTrue(parts)
        self.assertTrue(all(p["kind"].startswith("function_part") for p in parts))
        self.assertTrue(all(p["chars"] <= part.MAX_REGION_CHARS for p in parts))
        self.assertEqual(covered_lines(parts), set(range(1, text.count("\n") + 1)))

    def test_handler_in_a_long_module_span_is_folded_not_duplicated(self):
        text = "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n"
        parts = part.partition_text("m.py", text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "long_span+except_block")

    def test_trivial_partitions_are_dropped_and_counted(self):
        stats = {}
        parts = part.partition_text("t.js", "{\n}\n", stats)
        self.assertEqual(parts, [])
        self.assertEqual(stats["trivial"], 1)

    def test_deterministic(self):
        self.assertEqual(part.partition_text("x.py", PY), part.partition_text("x.py", PY))
        self.assertEqual(part.partition_text("x.js", JS), part.partition_text("x.js", JS))


class DedupeAndPack(SemanticTestCase):
    def test_distinct_string_literal_whitespace_is_not_deduped(self):
        files = {"a.py": "def label():\n    return 'a b'\n",
                 "b.py": "def label():\n    return 'a  b'\n"}
        regions, stats = part.partition(sorted(files), reader=files.__getitem__)
        self.assertEqual({r["path"] for r in regions}, {"a.py", "b.py"})
        self.assertEqual(stats["exact_duplicates"], 0)

    def test_packed_region_sends_every_claimed_source_line(self):
        shared = "def shared():\n    return 7919\n"
        source = ("def first():\n    return 104729\n\n" + shared + "\n"
                  "def last():\n    return 1299709\n")
        files = {"a.py": shared, "b.py": source}
        regions, _ = part.partition(sorted(files), reader=files.__getitem__)
        anchor_line = source.splitlines().index("    return 7919") + 1
        spanning = [r for r in regions if r["path"] == "b.py"
                    and r["line"] <= anchor_line <= r["end_line"]]
        self.assertTrue(spanning, "the packed location claims the middle function")
        self.assertTrue(any("    return 7919" in r["code"] for r in spanning),
                        "a claimed line must be present in the code sent to Jev")

    def test_exact_repeat_across_files_is_judged_once(self):
        files = {"a.py": PY, "b.py": PY}
        regions, stats = part.partition(sorted(files), reader=files.__getitem__)
        self.assertEqual(stats["exact_duplicates"], len(part.partition_text("a.py", PY)))
        self.assertTrue(all(r["path"] == "a.py" for r in regions))
        self.assertTrue(any(r.get("also_at") for r in regions), "the twin location is kept")

    def test_repeated_location_keeps_its_own_full_line_bounds(self):
        source = "def shared():\n    return 7\n"
        regions, _ = part.partition(["a.py", "b.py"],
                                    reader={"a.py": source, "b.py": source}.__getitem__)
        self.assertEqual(len(regions), 1)
        item = {"id": "repeat", "path": "b.py", "line": 2, "stale": False,
                "labels": {"failure_leaves_no_trace": True}}
        self.assertEqual(pilot.coverage([item], regions)["positives_sent"], 1)
        scored = [dict(regions[0], scores={"failure_leaves_no_trace": 0.9})]
        self.assertEqual(pilot.judged([item], scored, 0.55)["tp"], 1)

    def test_truncated_physical_line_is_not_scored_as_fully_sent(self):
        source = "VALUE = '" + "x" * (part.MAX_REGION_CHARS + 100) + "'\n"
        regions, _ = part.partition(["long.py"], reader={"long.py": source}.__getitem__)
        self.assertTrue(regions)
        self.assertTrue(all(len(r["code"]) <= part.MAX_REGION_CHARS for r in regions))
        item = {"id": "long", "path": "long.py", "line": 1, "stale": False,
                "labels": {"failure_leaves_no_trace": True}}
        self.assertEqual(pilot.coverage([item], regions)["positives_sent"], 0)
        scored = [dict(r, scores={"failure_leaves_no_trace": 0.9}) for r in regions]
        self.assertEqual(pilot.judged([item], scored, 0.55)["tp"], 0)

    def test_truncated_repeat_does_not_hide_a_complete_variant(self):
        files = {"a.py": "VALUE" + " " * (part.MAX_REGION_CHARS + 10) + "= 7\n",
                 "b.py": "VALUE = 7\n"}
        regions, _ = part.partition(sorted(files), reader=files.__getitem__)
        self.assertEqual(len(regions), 2, "a partial reading cannot stand in for the full text")
        item = {"id": "complete", "path": "b.py", "line": 1, "stale": False,
                "labels": {"failure_leaves_no_trace": True}}
        self.assertEqual(pilot.coverage([item], regions)["positives_sent"], 1)

    def test_small_neighbours_pack_under_the_cap_and_never_across_files(self):
        small = "\n\n".join(f"def helper_{i}(value):\n    return value + {i} * 7919\n"
                            for i in range(150))
        files = {"p.py": small, "q.py": "def other(value):\n    return value - 104729\n"}
        regions, _ = part.partition(sorted(files), reader=files.__getitem__)
        self.assertLess(len(regions), 150)
        self.assertGreater(len([r for r in regions if r["path"] == "p.py"]), 1,
                           "the cap splits a long run of helpers")
        self.assertTrue(all(len(r["code"]) <= part.MAX_REGION_CHARS for r in regions))
        self.assertEqual({r["path"] for r in regions}, {"p.py", "q.py"})
        self.assertLessEqual({k for k, text in enumerate(small.splitlines(), 1) if text.strip()},
                             covered_lines([r for r in regions if r["path"] == "p.py"]))


class Review(SemanticTestCase):
    def test_context_trimming_preserves_entire_multiline_signature(self):
        source = '#' + 'x' * 2700 + '\nif os.path.exists("x"):\n    # intervening context\n    with open("x") as f:\n        consume(f.read())\n'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'x.py').write_text(source)
            regions = review.regions(['x.py'], repo=tmp)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['kind'], 'check_then_use')
        self.assertIn('if os.path.exists("x"):', regions[0]['code'].splitlines())
        self.assertIn('    with open("x") as f:', regions[0]['code'].splitlines())
        self.assertLessEqual(len(regions[0]['code']), review.MAX_REGION_CHARS)
        self.assertEqual(regions[0]['start_line'], 2)
        self.assertGreaterEqual(regions[0]['sent_end_line'], 4)

    def test_nearby_candidates_split_when_union_exceeds_cap(self):
        source = 'time.sleep(1)\n' + ('#' + 'a' * 200 + '\n') * 19 + 'v = str(value or "")\n'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'scan.py').write_text(source)
            regions = review.regions(['scan.py'], repo=tmp)
        self.assertEqual(len(regions), 2)
        sent = [line for region in regions for line in region['code'].splitlines()]
        self.assertIn('time.sleep(1)', sent)
        self.assertIn('v = str(value or "")', sent)
        self.assertTrue(all(len(r['code']) <= review.MAX_REGION_CHARS for r in regions))

    def test_non_swallowing_handlers_and_ordinary_coercion_stay_quiet(self):
        source = 'try:\n    risky()\nexcept Exception:\n    raise\nresult = str(value)\n'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'scan.py').write_text(source)
            self.assertEqual(review.regions(['scan.py'], repo=tmp), [])

    def test_merged_context_preserves_trailing_blank_lines(self):
        source = 'time.sleep(1)\nv = str(value or "")\n\n'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'scan.py').write_text(source)
            regions = review.regions(['scan.py'], repo=tmp)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]['code'], source.rstrip('\n') + '\n')
        self.assertEqual(regions[0]['sent_end_line'], 3)

    def test_bare_except_pass_is_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "scan.py").write_text('try:\n    risky()\nexcept:\n    pass\n')
            regions = review.regions(["scan.py"], repo=tmp)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["kind"], "swallowed_failure")
        self.assertIn('except:', regions[0]["code"].splitlines())

    def test_large_preceding_context_keeps_flagged_statement(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "scan.py").write_text('#' + 'a' * 2700 + '\ntime.sleep(1)\n')
            regions = review.regions(["scan.py"], repo=tmp)
        self.assertEqual(len(regions), 1)
        self.assertIn('time.sleep(1)', regions[0]["code"].splitlines())
        self.assertEqual((regions[0]["start_line"], regions[0]["sent_end_line"]), (2, 2))
        self.assertLessEqual(len(regions[0]["code"]), review.MAX_REGION_CHARS)

    def test_nearby_candidates_send_both_flagged_lines(self):
        source = 'time.sleep(1)\n' + 'x = 1\n' * 19 + 'v = str(value or "")\n'
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "scan.py").write_text(source)
            regions = review.regions(["scan.py"], repo=tmp)
        self.assertEqual(len(regions), 1)
        self.assertIn('time.sleep(1)', regions[0]["code"].splitlines())
        self.assertIn('v = str(value or "")', regions[0]["code"].splitlines())
        self.assertEqual((regions[0]["start_line"], regions[0]["sent_end_line"]), (1, 21))

    def test_one_request_per_region_with_every_question(self):
        regions, _ = part.partition(["x.py"], reader={"x.py": PY}.__getitem__)
        client = FakeClient(value=0.7)
        rows = part.review(regions, client=client, workers=2)
        self.assertEqual(len(client.calls), len(regions))
        for state, questions in client.calls:
            self.assertEqual(set(questions), set(review.QUESTIONS))
            self.assertEqual(set(state["region"]), {"path", "line", "why_it_was_flagged", "code"})
        self.assertTrue(all(r["scores"]["failure_leaves_no_trace"] == 0.7 for r in rows))
        self.assertTrue(all(r["usage"]["input_tokens"] == 100 and r["seconds"] >= 0 for r in rows))

    def test_a_failed_request_is_an_error_row_not_an_abort(self):
        regions, _ = part.partition(["x.py"], reader={"x.py": PY}.__getitem__)
        rows = part.review(regions, client=FakeClient(fail_on="LIMIT"), workers=1)
        errors = [r for r in rows if "_error" in r["scores"]]
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(rows), len(regions))


class VerifyEdit(SemanticTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "ops"))
        self.env = fixture_env()
        self.target = os.path.join(self.repo, "ops", "thing.py")
        Path(self.target).write_text("def f():\n    return 1\n")
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                      "commit", "-qm", "seed"]):
            subprocess.run(argv, cwd=self.repo, env=self.env, check=True,
                           capture_output=True)
        self.before = Path(self.target).read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, text, **kw):
        return part.verify_edit({"path": "ops/thing.py", "new_text": text},
                                repo=self.repo, env=self.env, **kw)

    def assertLiveTreeUntouched(self):
        self.assertEqual(Path(self.target).read_bytes(), self.before)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.repo,
                                env=self.env, capture_output=True, text=True).stdout
        self.assertEqual(status, "")
        worktrees = subprocess.run(["git", "worktree", "list"], cwd=self.repo, env=self.env,
                                   capture_output=True, text=True).stdout.strip().splitlines()
        self.assertEqual(len(worktrees), 1, "the throwaway worktree is removed")

    def test_broken_edit_fails_and_is_not_applied(self):
        result = self.verify("def f(:\n")
        self.assertEqual(result["verdict"], "fails_checks")
        self.assertFalse(result["applied_to_live_tree"])
        self.assertLiveTreeUntouched()

    def test_sound_edit_passes_and_is_still_not_applied(self):
        result = self.verify("def f():\n    return 2\n")
        self.assertEqual(result["verdict"], "passes_checks", result)
        self.assertLiveTreeUntouched()

    def test_a_check_that_cannot_run_is_not_a_pass(self):
        result = self.verify("def f():\n    return 2\n",
                             checks=lambda rel, wt: [("syntax", [sys.executable, "-m", "py_compile", rel]),
                                                     ("lint", None)])
        self.assertEqual(result["verdict"], "not_verified")
        self.assertLiveTreeUntouched()

    def test_paths_outside_the_repo_are_refused(self):
        for bad in ("../escape.py", "/etc/passwd"):
            result = part.verify_edit({"path": bad, "new_text": "x"}, repo=self.repo, env=self.env)
            self.assertEqual(result["verdict"], "not_verified")
            self.assertIn("error", result)

    def test_tracked_symlink_cannot_overwrite_an_external_file(self):
        outside = Path(self.tmp.name, "outside.py")
        outside.write_bytes(self.before)
        os.unlink(self.target)
        os.symlink(outside, self.target)
        subprocess.run(["git", "add", "ops/thing.py"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@example.invalid",
                        "-c", "user.name=t", "commit", "-qm", "symlink fixture"],
                       cwd=self.repo, env=self.env, check=True, capture_output=True)
        result = self.verify("def f():\n    return 2\n")
        self.assertEqual(result["verdict"], "not_verified", result)
        self.assertIn("error", result)
        self.assertEqual(outside.read_bytes(), self.before)
        self.assertLiveTreeUntouched()

    def test_tracked_directory_symlink_cannot_overwrite_an_external_file(self):
        outside_dir = Path(self.tmp.name, "outside")
        outside_dir.mkdir()
        outside = outside_dir / "target.py"
        outside.write_text("ORIGINAL\n")
        os.symlink(outside_dir, os.path.join(self.repo, "ops", "linked"))
        subprocess.run(["git", "add", "ops/linked"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@example.invalid",
                        "-c", "user.name=t", "commit", "-qm", "directory symlink fixture"],
                       cwd=self.repo, env=self.env, check=True, capture_output=True)
        result = part.verify_edit({"path": "ops/linked/target.py", "new_text": "CHANGED\n"},
                                  repo=self.repo, env=self.env,
                                  checks=lambda _rel, _wt: [("ok", [sys.executable, "-c", "pass"])])
        self.assertEqual(result["verdict"], "not_verified", result)
        self.assertIn("error", result)
        self.assertEqual(outside.read_text(), "ORIGINAL\n")
        self.assertLiveTreeUntouched()


class PilotScoring(SemanticTestCase):
    def items(self):
        return [{"id": "a", "path": "f.py", "line": 10, "stale": False,
                 "labels": {"failure_leaves_no_trace": True, "swallow_is_wrong_here": False}},
                {"id": "b", "path": "f.py", "line": 40, "stale": False,
                 "labels": {"failure_leaves_no_trace": False}},
                {"id": "c", "path": "f.py", "line": 90, "stale": True,
                 "labels": {"failure_leaves_no_trace": True}}]

    def test_coverage_counts_only_what_is_sent(self):
        regions = [{"path": "f.py", "line": 5, "end_line": 20, "code": ""}]
        cov = pilot.coverage(self.items(), regions)
        self.assertEqual((cov["gold_positives"], cov["positives_sent"]), (1, 1))
        self.assertEqual((cov["labelled_negatives"], cov["negatives_sent"]), (2, 1))
        self.assertEqual(pilot.coverage(self.items(), [])["positives_never_sent"],
                         ["a:failure_leaves_no_trace"])

    def test_regex_region_span_is_its_context_window(self):
        region = {"path": "f.py", "line": 30, "code": "\n".join(["x"] * 25)}
        self.assertEqual(pilot.span(region), (16, 40))

    def test_regex_scanner_does_not_claim_a_truncated_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = 'VALUE = str(value or "")' + " " * (review.MAX_REGION_CHARS + 100)
            Path(tmp, "long.py").write_text(source + "\n")
            regions = review.regions(["long.py"], repo=tmp)
        self.assertTrue(regions)
        item = {"id": "regex-long", "path": "long.py", "line": 1,
                "stale": False, "labels": {"truthy_coercion_bug": True}}
        self.assertEqual(pilot.coverage([item], regions)["positives_sent"], 0)

    def test_judged_precision_recall_and_uncovered_is_a_no(self):
        results = [{"path": "f.py", "line": 5, "end_line": 20, "code": "",
                    "scores": {"failure_leaves_no_trace": 0.9, "swallow_is_wrong_here": 0.8}},
                   {"path": "f.py", "line": 35, "end_line": 45, "code": "",
                    "scores": {"failure_leaves_no_trace": 0.2}}]
        got = pilot.judged(self.items(), results, 0.55)
        self.assertEqual((got["tp"], got["fp"], got["fn"], got["tn"]), (1, 1, 0, 1))
        self.assertEqual((got["precision"], got["recall"]), (0.5, 1.0))
        missing = pilot.judged(self.items(), results[1:], 0.55)
        self.assertEqual(missing["fn"], 1, "an item no region covers is a predicted no")

    def test_an_errored_region_is_never_read_as_a_no(self):
        results = [{"path": "f.py", "line": 5, "end_line": 20, "code": "",
                    "scores": {"_error": "vendor down"}}]
        got = pilot.judged(self.items(), results, 0.55)
        self.assertEqual(got["items_with_errors"], 1)
        self.assertEqual(got["fn"] + got["tn"], 1, "only item b, uncovered, is scored")

    def test_duplicate_rate(self):
        regions = [{"path": "f.py", "line": 1, "end_line": 10, "code": "a"},
                   {"path": "f.py", "line": 6, "end_line": 12, "code": "b"},
                   {"path": "g.py", "line": 1, "end_line": 3, "code": "a"},
                   {"path": "g.py", "line": 20, "end_line": 30, "code": "c"}]
        self.assertEqual(pilot.duplicate_rate(regions, part.digest), 0.5)

    def test_usage_tokens(self):
        self.assertEqual(pilot.usage_tokens({"total_tokens": 7}), 7)
        self.assertEqual(pilot.usage_tokens({"input_tokens": 5, "output_tokens": 2}), 7)
        self.assertIsNone(pilot.usage_tokens({"model": "x"}))
        self.assertIsNone(pilot.usage_tokens(None))


class Corpus(SemanticTestCase):
    def test_each_covered_anchor_is_in_code_sent_to_the_judge(self):
        data, items = pilot.load_corpus()
        regions, _ = part.partition(sorted({item["path"] for item in items}))
        for item in items:
            if item["stale"]:
                continue
            for region in pilot.covering(item, regions):
                self.assertIn(item["anchor"], region["code"].splitlines(),
                              (item["id"], region["path"], region["line"]))

    def test_labels_are_real_questions_and_items_resolve(self):
        data, items = pilot.load_corpus()
        self.assertGreaterEqual(len(items), 30)
        for item in items:
            self.assertTrue(set(item["labels"]) <= set(review.QUESTIONS), item["id"])
            self.assertTrue(set(item["disputed"]) <= set(review.QUESTIONS), item["id"])
            self.assertFalse(set(item["labels"]) & set(item["disputed"]), item["id"])
            self.assertTrue(item["rationale"].strip(), item["id"])
        stale = [i["id"] for i in items if i["stale"]]
        # A drifted file makes its item stale; the report names it. More than
        # a quarter stale means the corpus needs re-anchoring before any
        # measurement off it is worth reading.
        self.assertLess(len(stale), len(items) / 4, f"re-anchor the corpus: {stale}")
        positives = sum(v for i in items for v in i["labels"].values())
        self.assertGreaterEqual(positives, 10)

    def test_offline_report_never_reports_judged_metrics(self):
        report = pilot.offline_report(whole_tree=False)
        self.assertEqual(report["judged"]["status"], "not_measured")
        row = report["scope"]["corpus_files"]
        for name in ("regex_scanner", "structural_partition"):
            self.assertIn("coverage_recall", row[name]["coverage"])
            self.assertGreater(row[name]["estimated_tokens"], 0)
        self.assertEqual(row["structural_partition"]["coverage"]["coverage_recall"], 1.0)

    def test_live_path_plumbing_with_a_fake_judge(self):
        # A judge that says yes to everything: recall is then exactly the
        # coverage ceiling, and each distinct region is asked about once.
        client = FakeClient(value=0.99)
        report = pilot.live_report(client=client, workers=4)
        judged = report["judged"]
        self.assertEqual(judged["status"], "measured")
        cov = report["scope"]["corpus_files"]
        for name in ("regex_scanner", "structural_partition"):
            self.assertAlmostEqual(judged[name]["recall"], cov[name]["coverage"]["coverage_recall"])
            self.assertEqual(judged[name]["errors"], 0)
            self.assertEqual(judged[name]["vendor_tokens_total"], 108 * judged[name]["requests"])
        distinct = {(r["region"]["path"], r["region"]["code"]) for r, _q in client.calls}
        self.assertEqual(len(client.calls), len(distinct), "no region is judged twice")

    def test_all_failed_live_requests_are_not_reported_as_measured(self):
        class DownClient(FakeClient):
            def ask(self, state, questions, timeout=None, api_key=None, **kwargs):
                self.calls.append((state, questions))
                raise RuntimeError("vendor down")

        report = pilot.live_report(client=DownClient(), workers=2)
        self.assertEqual(report["judged"]["status"], "not_measured")
        for name in ("regex_scanner", "structural_partition"):
            row = report["judged"][name]
            self.assertEqual(row["errors"], row["requests"])
            self.assertIsNone(row["precision"])
            self.assertIsNone(row["recall"])

    def test_partial_live_failure_does_not_publish_full_run_accuracy(self):
        class FlakyClient(FakeClient):
            def ask(self, state, questions, timeout=None, api_key=None, **kwargs):
                if not self.calls:
                    self.calls.append((state, questions))
                    raise RuntimeError("first request failed")
                return super().ask(state, questions, timeout=timeout, api_key=api_key)

        report = pilot.live_report(client=FlakyClient(), workers=1)
        self.assertNotEqual(report["judged"]["status"], "measured")
        for name in ("regex_scanner", "structural_partition"):
            row = report["judged"][name]
            if row["errors"]:
                self.assertIsNone(row["precision"])
                self.assertIsNone(row["recall"])

    def test_committed_snapshot_is_labelled_offline(self):
        snap = json.loads((REPO / "ops/fixtures/jev-code-review-pilot/offline-report.v1.json")
                          .read_text())
        self.assertEqual(snap["judged"]["status"], "not_measured")
        self.assertIn("measured_at_commit", snap)


class LibraryShape(SemanticTestCase):
    def test_no_new_script_entrypoint(self):
        guard = re.compile(r"if\s+__name__\s*==\s*[\"']" + "__" + r"main__[\"']\s*:")
        for rel in ("ops/jev_code_partition.py", "ops/jev_code_pilot_eval.py"):
            source = (REPO / rel).read_text()
            self.assertFalse(source.startswith("#!"), rel)
            self.assertIsNone(guard.search(source), rel)


if __name__ == "__main__":
    unittest.main(verbosity=1)
