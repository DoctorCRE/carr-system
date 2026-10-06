"""Synthetic public-interface regressions for PR 1531's blocking review."""
import importlib.util
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

def load(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem.replace('-', '_'), ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

Q = {'q': {'type': 'noul'}}
def answer(*args, **kw):
    return {'model': 'jev-1.13.0', 'answers': {'q': {'noul': .9}}}

class ReviewContracts(unittest.TestCase):
    def test_proposals_never_compile_or_deliver(self):
        rtc = load('ops/rule_trigger_compile.py')
        delivery = load('ops/rule_trigger_delivery.py')
        rule = {'id': 'synthetic', 'statement': 'Synthetic rule', 'packs': []}
        proposal = {'id': rule['id'], 'statement_sha256': rtc.sha256_text(rule['statement']),
                    'mode': 'review_required', 'proposed_mode': 'triggered',
                    'triggers': {'keywords': {'synthetic': .9}}}
        doc = rtc.document([proposal])
        self.assertEqual(rtc.trigger_rows(doc), [])
        self.assertEqual(rtc.stale_or_missing(doc, [rule]), ['synthetic'])
        self.assertTrue(any('review' in p for p in rtc.coverage_problems(doc, [rule])))
        with tempfile.TemporaryDirectory() as tmp, patch.object(delivery, 'judge_budgeted', return_value=({}, {'calls':0, 'rank_status':'unavailable', 'bind_status':'unavailable', 'judged':[], 'overflow':[]})), patch.object(delivery, 'prompt_rows', return_value=[
                {'kind': 'prompt_regex', 'pattern': 'synthetic', 'source': 'jev_compiled', 'rule_ids': ['synthetic']} ]):
            result = delivery.advise('synthetic', compiled=doc, rules=[rule], envelope=False,
                                     delivered_cache=tmp+'/delivered', log_path=tmp+'/log')
        self.assertEqual(result, [])

    def flash(self, intake, best=None, force=False, effort=None, test='true'):
        fr = load('tools/flash-run.py')
        rows, attempts, applied = [], [], []
        notebook = NS(recall_mistakes=lambda *_: {}, record_mistake=Mock())
        best = best or {'verdict': 'a', 'escalate': False}
        libs = {'jev_intake': intake, 'jev_notebook': notebook,
                'jev_best_of': NS(select_candidate=lambda *_: best),
                'jev_done_checks': NS(triage_review=lambda *_: {})}
        candidate = dict(id='a', patch='synthetic patch', code_or_diff='synthetic',
                         probe_results={'patch_lines': 1}, test_output='failed', test_exit_code=1, elapsed_s=0)
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            args = NS(cwd=tmp, task='Change synthetic.py', test=test, sandbox='off', no_learn=True,
                      force=force, effort=effort, no_rules=True, attempts=1, all_attempts=False,
                      think='off', escalate='suggest', dry_run=False, keep=False)
            replacements = dict(_lib=lambda n: libs[n], _append=lambda _, row: rows.append(row),
                                _tracked_files=lambda _: [], _read_examples=lambda: [],
                                run_attempt=lambda *a, **k: (attempts.append(a) or candidate),
                                apply_patch=lambda *a: (applied.append(a) or (True, '')),
                                _run_test=lambda *a, **k: (1, 'failed'),
                                escalate=lambda *a, **k: {'handoff': None, 'outcome': 'suggested'}, _say=lambda *_: None)
            for name, value in replacements.items():
                stack.enter_context(patch.object(fr, name, value))
            code = fr.cmd_run(args)
        return code, rows, attempts, applied

    def intake(self, **overrides):
        values = dict(check_ambiguity=lambda *_: {'verdict': 'clear', 'escalate': False},
                      route_task=lambda *a, **k: {'verdict': 'local', 'escalate': False},
                      pick_effort=lambda *_: {'verdict': 'low', 'escalate': False}, pick_context=lambda *_: {})
        values.update(overrides)
        return NS(**values)

    def test_flash_stops_pending_intake_at_each_actual_caller(self):
        pending = {'verdict': 'review_required', 'escalate': True, 'detail': {'advisory_verdict': 'high'}}
        for name in ('check_ambiguity', 'route_task', 'pick_effort'):
            with self.subTest(name=name):
                code, rows, attempts, applied = self.flash(self.intake(**{name: lambda *a, **k: pending}))
                self.assertNotEqual(code, 0)
                self.assertEqual(attempts, [])
                self.assertEqual(applied, [])
                self.assertIn('review', rows[-1]['outcome'])

    def test_advisory_best_of_is_separate_and_flash_never_applies_it(self):
        bo = load('ops/jev_best_of.py')
        judge = NS(judge=lambda *a, **k: {'answers': {'pick': {'type': 'choice', 'choice': 'a', 'confidence': .99}}},
                   record=Mock(), SHADOW_LOG='unused')
        client = NS(choice=lambda instructions, criteria: {'type': 'choice', 'criteria': criteria})
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CARR_JEV_SEMANTIC_CACHE=tmp+'/cache'):
            best = bo.select_candidate('Synthetic task', [{'id': 'a', 'test_exit_code': 1}], judge=judge, client=client)
        self.assertEqual(best['verdict'], 'review_required')
        self.assertEqual(best['detail']['advisory_candidate_id'], 'a')
        # Also refuse older-shaped escalated IDs, including the no-test fallback.
        for selection in (best, {'verdict': 'a', 'escalate': True}):
            for test in ('true', None):
                code, rows, attempts, applied = self.flash(self.intake(), best=selection, test=test)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(applied, [])
                self.assertIn('review', rows[-1]['outcome'])

    def test_flash_refuses_escalated_candidate_id_and_no_test_fallback(self):
        for test in ('true', None):
            code, rows, attempts, applied = self.flash(self.intake(), best={'verdict': 'a', 'escalate': True}, test=test)
            self.assertEqual(applied, [])

    def test_runtime_refuses_proposal_even_if_old_rows_remain(self):
        rtc = load('ops/rule_trigger_compile.py')
        delivery = load('ops/rule_trigger_delivery.py')
        rule = {'id': 'synthetic', 'statement': 'Synthetic rule', 'packs': []}
        doc = {'rules': {'synthetic': {'id': 'synthetic', 'mode': 'review_required',
                            'statement_sha256': rtc.sha256_text(rule['statement']),
                            'triggers': {'keywords': {'synthetic': .9}}}}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(delivery, 'judge_budgeted', return_value=({}, {'calls':0, 'rank_status':'unavailable', 'bind_status':'unavailable', 'judged':[], 'overflow':[]})), patch.object(delivery, 'prompt_rows', return_value=[
                {'kind': 'prompt_regex', 'pattern': 'synthetic', 'source': 'jev_compiled', 'rule_ids': ['synthetic']} ]):
            result = delivery.advise('synthetic', compiled=doc, rules=[rule], envelope=False,
                                     delivered_cache=tmp+'/delivered', log_path=tmp+'/log')
        self.assertEqual(result, [])

    def test_sparse_coverage_persists_and_resume_does_not_relabel(self):
        cli = load('tools/rule-gold-label.py')
        rules = [{'id': f'r{i}', 'statement': 'synthetic'} for i in range(21)]
        case = {'id': 'case', 'prompt': 'Synthetic turn', 'stratum': 'partner'}
        gl = NS(label_case=Mock(return_value=({r['id']: .1 for r in rules[:20]},
                          {'input_tokens': 0, 'requests': 0, 'unjudged': ['r20']})))
        real_gl = load('ops/rule_gold_label.py')
        gl.adjudication_case_binding = real_gl.adjudication_case_binding
        gl.roster_binding = real_gl.roster_binding
        with tempfile.TemporaryDirectory() as tmp:
            args = NS(cases='unused', corpus='unused', probs=tmp+'/probs.json', calls_log='unused', workers=1)
            with patch.object(cli, 'read_cases', return_value=[case]), patch.object(cli, 'read_rules', return_value=rules):
                cli.cmd_label(args, gl, None)
                coverage = json.loads(Path(args.probs+'.coverage.json').read_text())
                self.assertEqual(coverage['case']['unjudged'], ['r20'])
                self.assertEqual(set(coverage['case']['judged']), {r['id'] for r in rules[:20]})
                cli.cmd_label(args, gl, None)
            self.assertEqual(gl.label_case.call_count, 1)

    def test_unjudged_and_disputed_pairs_survive_loading_and_scoring(self):
        ev = load('ops/rule_delivery_eval.py')
        case = {'id': 'case', 'prompt': 'Synthetic', 'stratum': next(s for s in ev.STRATA if s not in ev.MACHINE_STRATA),
                'gold': [], 'judged_rules': ['r1'], 'unjudged_rules': ['r2'], 'disputed': ['r3']}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'cases.json'
            path.write_text(json.dumps({'schema': ev.CASES_SCHEMA, 'cases': [case]}))
            cases = ev.load_cases(path)
        report = ev.score(cases, {'path': {'case': {'rules': ['r2', 'r3']}}},
                          {'path': {'r1', 'r2', 'r3'}}, {r: {} for r in ('r1', 'r2', 'r3')}, labelled={'r1', 'r2', 'r3'})
        self.assertEqual(report['per_case']['case']['path']['fp'], [])

    def test_sparse_labels_build_and_score_through_the_cli(self):
        cli = load('tools/rule-gold-label.py')
        gl = load('ops/rule_gold_label.py')
        ev = load('ops/rule_delivery_eval.py')
        rules = [{'id': f'r{i:02}', 'statement': 'Synthetic rule'} for i in range(21)]
        case = {'id': 'case', 'prompt': 'Synthetic turn', 'stratum': 'engineering', 'disputed': ['r01']}
        probs = {'case': {r['id']: .1 for r in rules[:20]}}
        adjudications = [{'case': 'case', 'rule': r['id'], 'gold': False, 'reason': 'Independently reviewed synthetic pair',
                         'case_binding': gl.adjudication_case_binding(case)} for r in rules[:20]]
        with tempfile.TemporaryDirectory() as tmp:
            args = NS(cases='unused', corpus='unused', probs=tmp+'/probs', adjudications=tmp+'/adjudications',
                      signals=tmp+'/signals', extended_rules=None, floors=None, doctrine_probs=None,
                      doctrine_adjudications=None, seed='synthetic', labelled_on='2026-10-04', out=tmp+'/built.json')
            Path(args.probs).write_text(json.dumps(probs))
            Path(args.adjudications).write_text('\n'.join(json.dumps(a) for a in adjudications))
            Path(args.signals).write_text(json.dumps({'signals': []}))
            with patch.object(cli, 'read_cases', return_value=[case]), patch.object(cli, 'read_rules', return_value=rules), patch.object(gl, 'rule_groups', return_value={}):
                cli.cmd_build(args, gl)
            built = ev.load_cases(args.out)
            self.assertEqual(built[0]['unjudged_rules'], ['r20'])
            report = ev.score(built, {'path': {'case': {'rules': ['r20', 'r01']}}},
                              {'path': {r['id'] for r in rules}}, {r['id']: {} for r in rules}, labelled={r['id'] for r in rules})
            self.assertEqual(report['per_case']['case']['path']['fp'], [])

    def test_second_pass_cli_counts_cache_reads_as_zero_requests(self):
        import io
        from contextlib import redirect_stdout
        cli = load('tools/rule-gold-label.py')
        gl = NS(borderlines=lambda p: [('case', 'r', .5)], second_pass=lambda *a, **k: ({'case': .1}, {'input_tokens': 0, 'requests': 0}))
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli, 'read_cases', return_value=[{'id': 'case'}]), patch.object(cli, 'read_rules', return_value=[{'id': 'r'}]):
            args = NS(cases='unused', corpus='unused', probs=tmp+'/probs', second=tmp+'/second', calls_log='unused', workers=1)
            out = io.StringIO()
            with redirect_stdout(out):
                cli.cmd_second(args, gl, None)
            self.assertEqual(json.loads(out.getvalue())['requests'], 0)

    def test_unrelated_keys_and_cached_reads_are_not_blocked(self):
        api = load('ops/jev_semantic.py')
        started, release = Event(), Event()
        def slow(*a, **k):
            started.set()
            release.wait(1)
            return answer()
        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=2) as pool:
            opts = dict(caller='fixture', version='v1', cache_path=tmp+'/cache')
            api.ask({'text': 'cached'}, Q, transport=answer, **opts)
            future = pool.submit(api.ask, {'text': 'slow'}, Q, transport=slow, **opts)
            self.assertTrue(started.wait(1))
            try:
                cached = api.ask({'text': 'cached'}, Q, transport=answer, timeout=.05, **opts)
                other = api.ask({'text': 'other'}, Q, transport=answer, timeout=.05, **opts)
                self.assertTrue(cached['cache_hit'])
                self.assertIn('answers', other)
            finally:
                release.set()
                future.result()
            # Both concurrent storage writes must survive.
            self.assertTrue(api.ask({'text': 'other'}, Q, transport=answer, **opts)['cache_hit'])
            self.assertTrue(api.ask({'text': 'slow'}, Q, transport=answer, **opts)['cache_hit'])

    def test_deadline_includes_wait_and_transport(self):
        import fcntl
        api = load('ops/jev_semantic.py')
        state = {'text': 'same'}
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp+'/cache'
            key = api.cache_key(state, Q, 'fixture', 'v1')
            # Lock both paths so this regression exercises the old and new claim.
            with open(path+'.lock', 'a') as storage, open(path+'.'+key+'.lock', 'a') as request_lock:
                for lock in (storage, request_lock):
                    fcntl.flock(lock, fcntl.LOCK_EX)
                seen = []
                def transport(*a, **kw):
                    seen.append(kw)
                    return answer()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    start = time.monotonic()
                    future = pool.submit(api.ask, state, Q, caller='fixture', version='v1',
                                         cache_path=path, timeout=.5, transport=transport)
                    time.sleep(.05)
                    for lock in (storage, request_lock):
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    future.result()
                self.assertLess(seen[0]['timeout'], .49)
                self.assertLessEqual(seen[0]['deadline'], start+.51)

    def test_python_snapshots_state_before_waiting(self):
        import fcntl
        api = load('ops/jev_semantic.py')
        state = {'text': 'before'}
        seen, entered = [], Event()
        real_key = api.cache_key
        def key(*a):
            value = real_key(*a)
            entered.set()
            return value
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp+'/cache'
            digest = real_key(state, Q, 'fixture', 'v1')
            with open(path+'.lock', 'a') as storage, open(path+'.'+digest+'.lock', 'a') as claim:
                for lock in (storage, claim):
                    fcntl.flock(lock, fcntl.LOCK_EX)
                with ThreadPoolExecutor(max_workers=1) as pool, patch.object(api, 'cache_key', side_effect=key):
                    future = pool.submit(api.ask, state, Q, caller='fixture', version='v1', cache_path=path,
                                         transport=lambda s, *a, **k: (seen.append(s.copy()) or answer()))
                    self.assertTrue(entered.wait(1))
                    state['text'] = 'after'
                    for lock in (storage, claim):
                        fcntl.flock(lock, fcntl.LOCK_UN)
                    future.result()
            self.assertEqual(seen, [{'text': 'before'}])

    def test_label_cache_hits_charge_zero_for_both_passes(self):
        gl = load('ops/rule_gold_label.py')
        fake = NS(ask=lambda s, q, **k: {'cache_hit': True, 'usage': {'input_tokens': 1000, 'output_tokens': 10},
                                      'answers': {key: {'noul': .1} for key in q}})
        tsc = NS(noul=lambda *a, **k: {'type': 'noul'})
        case = {'id': 'case', 'prompt': 'Synthetic'}
        rule = {'id': 'r', 'statement': 'Synthetic'}
        with patch.object(gl, '_semantic', return_value=fake):
            for usage in (gl.label_case(case, [rule], tsc, calls_log='unused')[1],
                          gl.second_pass(rule, [case], tsc, calls_log='unused')[1]):
                self.assertEqual(usage['input_tokens'], 0)
                self.assertEqual(usage['requests'], 0)

    def test_checker_accepts_imported_interface_and_independent_nested_scopes(self):
        checker = load('ops/check-jev-conformance.py')
        for source in ("from jev_semantic import ask as semantic_ask\ndef f(state, questions):\n return semantic_ask(state, questions, caller='x', version='v1')",
                       "import jev_semantic as semantic\ndef outer(state, questions):\n def a():\n  return semantic.ask(state, questions, caller='x', version='v1')\n def b():\n  return semantic.ask(state, questions, caller='x', version='v1')"):
            with self.subTest(source=source):
                self.assertEqual(checker.python_errors(source), [])

    def test_intake_only_builds_the_submitted_shared_questions(self):
        intake = load('ops/jev_intake.py')
        built = []
        client = NS(noul=lambda *a, **k: (built.append(a) or {'type': 'noul'}),
                    score=lambda *a, **k: (built.append(a) or {'type': 'score', 'criteria': ['low', 'medium', 'high']}))
        judge = NS(judge=lambda s, q, **k: {'answers': {key: {row['type']: .1} for key, row in q.items()}},
                   read=lambda *a, **k: {'value': .1, 'confidence': .9, 'outcome': 'no', 'escalate': False}, record=Mock())
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CARR_JEV_SEMANTIC_CACHE=tmp+'/cache'), patch.object(intake, '_judge', return_value=judge):
            for call in (intake.pick_effort, intake.check_ambiguity, intake.route_task):
                built.clear()
                call('Synthetic task', client=client)
                self.assertEqual(len(built), 6)

if __name__ == '__main__':
    unittest.main()
