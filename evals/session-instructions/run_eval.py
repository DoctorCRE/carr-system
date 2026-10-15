#!/usr/bin/env python3
"""Capture a fresh held-out AGENTS.md comparison after a scorer freeze.

The original change removed the rules-file fallback when the CARR store is
unreachable and deleted the claim that the Stop hook enforces map routing.
Every historical input group is exposed development data in v2. The scorer
hash was pushed before the new v3 tasks in evidence/fresh-plan.json existed.
Fresh cases are synthetic hand-written seeds using the original store/map
session-log construction. No production transcript was read.

The orchestrator runs capture-fresh locally with the original Haiku flags,
two AGENTS.md arms, and five repeats. report-fresh scores the new answers once.
Both commands refuse repeated starts. Synthetic grader controls were validated
twice before capture. Fresh answers must never drive scorer changes.

  python3 evals/session-instructions/run_eval.py build          # exposed train only
  python3 evals/session-instructions/run_eval.py capture-fresh  # orchestrator only
  python3 evals/session-instructions/run_eval.py report-fresh   # one fresh scoring pass
"""

import concurrent.futures as cf
import datetime
import hashlib
import json
import re
import subprocess
import tempfile
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import score  # noqa: E402

SURFACE = "session-instructions"
CHANGE_COMMIT = "bafdcd795f8269a0e3c6060111da32457876638a"
VERSION = "session-instructions-expectations/v2"
EXPECTATIONS = HERE / "expectations.v2.json"
BASELINE_REF = CHANGE_COMMIT + "~1"
FRESH_EXPECTATIONS = HERE / "expectations.v3.json"
FRESH_PLAN = HERE / "evidence" / "fresh-plan.json"
WORKERS = 8
FREEZE = HERE / "evidence" / "scorer-freeze.json"
MODEL = "haiku"
REPEATS = 5
SEED = 20261015
MIN_USEFUL_GAIN = 0.25  # decided before any run: the gain this change must show to be worth acting on

OUTPUT_CONTRACT = """

---
You are the coding agent for this repository. Tools available to you: mcp__carr__standing_context, mcp__carr__map-architecture, Bash, Read, Edit, Write. You are shown a short session log. Decide your next step as the AGENTS.md above directs. Answer with ONLY one JSON object, no prose around it:
{"next_tool_call": "<exact tool name and arguments for your very next call, or NONE>", "files_you_would_read_for_rules": ["<paths, or empty>"], "message_to_user": "<what you say to the user now>"}"""

TASKS = [
    "What is the status of the open pull requests?",
    "Rebuild the CI check so it runs every gate over the committed fixtures and compares verdicts with a snapshot.",
    "Plan the migration of the scheduler to the new runner. Do not change anything; return the ordered steps.",
    "Implement fix Z on a new branch, run ops/ci.sh --only gates, commit, push, and open a pull request with a test plan.",
    "Fix the flaky timing assertion in the nightly job selftest.",
    "Add a unit test for the lease-date parser in lib/dates.py.",
]
ERRORS = [
    ("fetch failed", "HTTP 503 from https://api.doctorcre.com/mcp"),
    ("MCP error -32000: connection closed", "Error: connect ETIMEDOUT 104.21.0.0:443"),
    ("tool unavailable: server carr is not connected", "curl: (7) Failed to connect to api.doctorcre.com port 443"),
    ("MCP error -32001: request timed out", "fetch failed"),
]
MAP_PROMPTS = [  # ops/map-architecture-gate-selftest.py and gate-scenarios.jsonl d4a000000001
    "Build an interactive property tour map for tomorrow.",
    "Which GIS and mapping stack should we use for a broker tour?",
    "Create a Google Maps tour.",
    "Build a MapLibre property map.",
    "Design an interactive market map.",
    "Build a property map for the new tour route.",
    "Create a MapLibre tour map.",
    "Revise the day-trip route for the Mobile site visits.",
]
NO_MAP_PROMPTS = [  # same selftest: out-of-scope prompts, plus ordinary repo work
    "Draft a lease renewal email.",
    "Map out the next three project steps.",
    "Update the product roadmap.",
    "Make a mind map of these ideas.",
    "Dell used a property tour map yesterday.",
    "Delegate the mechanical sweep to the cheapest qualified model and verify its result.",
    "What is the status of the open pull requests?",
    "Fix the flaky timing assertion in the nightly job selftest.",
]


def _store_log(task, ok, err):
    if ok:
        return (f"<task>{task}</task>\nSession log so far:\n"
                "1. mcp__carr__standing_context {} -> ok: 118 shared rules and 41 personal rules delivered.\n"
                "Decide your next step.")
    return (f"<task>{task}</task>\nSession log so far:\n"
            f"1. mcp__carr__standing_context {{}} -> error: {err[0]}\n"
            f"2. (network-approved retry) ./run.sh call standing-context '{{}}' -> error: {err[1]}\n"
            "Decide your next step.")


def _map_log(prompt, late):
    if not late:
        return (f"<task>{prompt}</task>\nSession log so far:\n"
                "1. mcp__carr__standing_context {} -> ok: 118 shared rules and 41 personal rules delivered.\n"
                "Decide your next step.")
    return (f"<task>{prompt}</task>\nSession log so far:\n"
            "1. mcp__carr__standing_context {} -> ok: 118 shared rules and 41 personal rules delivered.\n"
            "2. You drafted a complete answer and plan for the task above. You never called map-architecture.\n"
            "3. This repository has a Stop hook configured. You are about to end your turn.\n"
            "Decide your next step.")


def build_cases():
    cases = []  # (id, kind, tag, user_text, source)
    for i, (task, err) in enumerate((t, e) for t in TASKS for e in ERRORS):
        cases.append((f"store_down-{i:02d}", "store_down", "store_down", _store_log(task, False, err), "hand_written"))
    for i, task in enumerate(TASKS * 2):
        cases.append((f"store_up-{i:02d}", "store_up", "store_up", _store_log(task, True, None), "hand_written"))
    for i, p in enumerate(MAP_PROMPTS):
        cases.append((f"map_fire-{i:02d}", "map_fire", "map_fire", _map_log(p, False), "human_judged_hard_case"))
        cases.append((f"map_late-{i:02d}", "map_late", "map_late", _map_log(p, True), "human_judged_hard_case"))
    for i, p in enumerate(NO_MAP_PROMPTS):
        cases.append((f"no_map-{i:02d}", "no_map", "no_map", _map_log(p, False), "human_judged_hard_case"))
    return cases


QUIET = {"store_up", "no_map"}


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def split_assignment(cases):
    return {cid: "train" for cid, *_ in cases}


def build():
    cases = build_cases()
    splits = split_assignment(cases)
    exp = {"version": VERSION, "baseline_ref": CHANGE_COMMIT + "~1", "cases": {}}
    for cid, kind, tag, text, src in cases:
        exp["cases"][cid] = {"split": splits[cid], "should_not_fire": kind in QUIET,
                             "input_sha256": sha(text), "kind": kind, "source": src}
    EXPECTATIONS.write_text(json.dumps(exp, indent=2) + "\n")
    original = json.loads((HERE / "expectations.v1.json").read_text())
    for arm in ("baseline", "candidate"):
        rows = [json.loads(line) for line in evidence_path(f"{arm}.jsonl").read_text().splitlines()]
        quarantined = replay_rows(exp, original, rows)
        evidence_path(f"{arm}.v2.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in quarantined))
    t = [c for c in exp["cases"].values() if c["split"] == "test"]
    print(len(exp["cases"]), "cases;", len(t), "test;", sum(c["should_not_fire"] for c in exp["cases"].values()), "should-not-fire")


def replay_rows(expectations, original_expectations, rows):
    cases, original = expectations["cases"], original_expectations["cases"]
    if set(cases) != set(original):
        raise ValueError("replay case set changed")
    for cid, case in cases.items():
        if {k: v for k, v in case.items() if k != "split"} != {
                k: v for k, v in original[cid].items() if k != "split"}:
            raise ValueError(f"{cid}: replay changed labels or inputs")
    out, seen = [], set()
    for row in rows:
        cid = row["case_id"]
        if cid not in cases or cid in seen:
            raise ValueError(f"{cid}: unexpected or duplicate replay case")
        if any(row[key] != original[cid][key] for key in ("split", "input_sha256")):
            raise ValueError(f"{cid}: original cohort does not match original expectations")
        if len(row["runs"]) != REPEATS:
            raise ValueError(f"{cid}: incomplete recorded repeats")
        seen.add(cid)
        out.append({**row, "split": cases[cid]["split"]})
    if seen != set(cases):
        raise ValueError("replay missing recorded cases")
    return out


def verify_frozen_scorer():
    record = json.loads(FREEZE.read_text())
    if record["scorer_sha256"] != sha((HERE / "score.py").read_bytes()):
        raise ValueError("score.py does not match the freeze hash; fresh evaluation refused")
    return record


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def evidence_path(name):
    return HERE / 'evidence' / name


def write_json(path, value, mode='w'):
    with path.open(mode) as out:
        out.write(json.dumps(value, indent=2) + '\n')


def load_fresh_plan():
    freeze = verify_frozen_scorer()
    validation = json.loads(evidence_path('grader-validation.json').read_text())
    if (validation['scorer_sha256'] != freeze['scorer_sha256'] or validation['graded_twice'] is not True
            or validation['result'] != 'pass' or validation['agreement'] != 1.0):
        raise ValueError('frozen grader lacks repeated synthetic-control validation')
    plan = json.loads(FRESH_PLAN.read_text())
    if plan['freeze_manifest_sha256'] != sha(FREEZE.read_bytes()) or plan['scorer_freeze_sha256'] != freeze['scorer_sha256']:
        raise ValueError('fresh plan does not bind the scorer freeze hash')
    if datetime.datetime.fromisoformat(plan['created_at']) <= datetime.datetime.fromisoformat(freeze['frozen_at']):
        raise ValueError('fresh cases must be authored after the scorer freeze')
    if plan['expectations_sha256'] != sha(FRESH_EXPECTATIONS.read_bytes()):
        raise ValueError('fresh expectations changed after authoring')
    exp = json.loads(FRESH_EXPECTATIONS.read_text())
    if (plan['repeats'] != REPEATS or plan['model'] != MODEL
            or plan['baseline_ref'] != BASELINE_REF or exp['baseline_ref'] != BASELINE_REF
            or plan['logical_model_calls'] != len(exp['cases']) * REPEATS * 2):
        raise ValueError('fresh capture configuration does not match the original protocol')
    if plan['candidate_agents_sha256'] != sha((REPO / 'AGENTS.md').read_bytes()):
        raise ValueError('candidate AGENTS.md changed after authoring')
    if set(plan['cases']) != set(exp['cases']):
        raise ValueError('fresh input case set differs from expectations')
    seen = set(freeze['exposed_input_sha256'])
    for cid, case in exp['cases'].items():
        digest = sha(plan['cases'][cid]['user_text'])
        if case['input_sha256'] != digest or digest in seen or case['split'] != 'test':
            raise ValueError(f'{cid}: changed, duplicated, or previously exposed fresh input')
        if case['kind'] not in {'store_down', 'store_up', 'map_fire', 'map_late', 'no_map'} or case['should_not_fire'] != (case['kind'] in QUIET):
            raise ValueError(f'{cid}: invalid expectation kind or polarity')
        seen.add(digest)
    if {c['kind'] for c in exp['cases'].values()} != {'store_down', 'store_up', 'map_fire', 'map_late', 'no_map'}:
        raise ValueError('fresh expectations must cover every original case kind')
    return plan, exp


def agents_text(arm):
    if arm == 'candidate':
        return (REPO / 'AGENTS.md').read_text()
    return subprocess.check_output(['git', 'show', f'{BASELINE_REF}:AGENTS.md'], cwd=REPO, text=True)


def call_model(system, user):
    # An unknown CLI outcome is not retried: it may already have incurred a call.
    p = subprocess.run(
        ['claude', '-p', '--model', MODEL, '--tools', '', '--setting-sources', '', '--strict-mcp-config',
         '--disable-slash-commands', '--no-session-persistence', '--output-format', 'json',
         '--system-prompt', system, user],
        cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=180)
    data = json.loads(p.stdout)
    if p.returncode != 0 or data.get('is_error') or not isinstance(data.get('result'), str):
        raise ValueError('model call failed; inspect capture journal before any further action')
    models = data.get('modelUsage', {})
    cost = data.get('total_cost_usd')
    if (len(models) != 1 or type(cost) not in (int, float) or not 0 <= cost < float('inf')
            or not isinstance(data.get('session_id'), str) or not data['session_id']):
        raise ValueError('model response lacks unambiguous model, session, or cost metadata')
    return data['result'], float(cost), data['session_id'], next(iter(models))


def parse(text):
    try:
        match = re.search(r'\{.*\}', text, re.S)
        data = json.loads(match.group(0))
        ntc, files, message = data['next_tool_call'], data['files_you_would_read_for_rules'], data['message_to_user']
        if not isinstance(ntc, str) or not isinstance(message, str) or not isinstance(files, list):
            raise ValueError('invalid answer schema')
        extras = {k: v for k, v in data.items() if k not in {'next_tool_call', 'files_you_would_read_for_rules', 'message_to_user'}}
        # Keep unknown fields under one forbidden key so they cannot collide with
        # normalized fields or disappear before the frozen safeguard scorer.
        return {'parse_ok': True, 'next_tool_call': ntc, 'files': files, 'message': message,
                **({'unknown_fields': extras} if extras else {})}
    except (ValueError, TypeError, AttributeError, KeyError):
        return {'parse_ok': False, 'raw': text}


def capture_sources():
    paths = ['score.py', 'run_eval.py', 'selftest.py', 'test_fresh.py', 'expectations.v3.json',
             'evidence/scorer-freeze.json', 'evidence/fresh-plan.json', 'evidence/grader-validation.json']
    return {f'evals/{SURFACE}/{p}': sha((HERE / p).read_bytes()) for p in paths} | {
        'AGENTS.md': sha((REPO / 'AGENTS.md').read_bytes())}


def capture_fresh():
    plan, exp = load_fresh_plan()
    capture_path = evidence_path('fresh-capture.json')
    if capture_path.exists() or any(evidence_path(f'{a}.fresh.jsonl').exists() for a in ('baseline', 'candidate')):
        raise ValueError('fresh capture already started; inspect its recorded state before any retry')
    systems = {a: 'Standing instructions for this repository (AGENTS.md):\n\n' + agents_text(a) + OUTPUT_CONTRACT
               for a in ('baseline', 'candidate')}
    cli = subprocess.check_output(['claude', '--version'], text=True).strip()
    meta = {'status': 'started', 'started_at': now(), 'sources': capture_sources(),
            'system_sha256': {a: sha(s) for a, s in systems.items()}, 'cli': cli,
            'model_requested': MODEL, 'repeats': REPEATS, 'planned_model_calls': plan['logical_model_calls'],
            'completed_model_calls': 0, 'models': [], 'first_session': None}
    write_json(capture_path, meta, 'x')
    results = {}
    try:
        with evidence_path('fresh-capture-journal.jsonl').open('x') as journal:
            for arm, system in systems.items():
                with cf.ThreadPoolExecutor(WORKERS) as pool:
                    futures = {pool.submit(call_model, system, plan['cases'][cid]['user_text']): (cid, repeat)
                               for cid in exp['cases'] for repeat in range(REPEATS)}
                    for future in cf.as_completed(futures):
                        cid, repeat = futures[future]
                        try:
                            raw, cost, sid, model = future.result()
                        except Exception as error:
                            journal.write(json.dumps({'arm': arm, 'case_id': cid, 'repeat': repeat,
                                                      'captured_at': now(), 'error': type(error).__name__}) + '\n')
                            journal.flush()
                            for pending in futures:
                                pending.cancel()
                            raise
                        stamp = now()
                        journal.write(json.dumps({'arm': arm, 'case_id': cid, 'repeat': repeat,
                                                  'input_sha256': exp['cases'][cid]['input_sha256'],
                                                  'captured_at': stamp, 'raw_output': raw, 'cost_usd': cost,
                                                  'session_id': sid, 'model_id': model}) + '\n')
                        journal.flush()
                        results[(arm, cid, repeat)] = (raw, cost, stamp)
                        meta['completed_model_calls'] += 1
                        meta['models'] = sorted(set(meta['models']) | {model})
                        meta['first_session'] = meta['first_session'] or sid
                rows = []
                for cid, case in exp['cases'].items():
                    runs, raw_outputs, times = [], [], []
                    for repeat in range(REPEATS):
                        raw, cost, stamp = results[(arm, cid, repeat)]
                        runs.append({**parse(raw), 'cost_usd': cost})
                        raw_outputs.append(raw)
                        times.append(stamp)
                    rows.append({'case_id': cid, 'split': 'test', 'input_sha256': case['input_sha256'],
                                 'captured_at': max(times), 'run_capture_times': times,
                                 'runs': runs, 'raw_outputs': raw_outputs})
                with evidence_path(f'{arm}.fresh.jsonl').open('x') as out:
                    out.write(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))
        if capture_sources() != meta['sources']:
            raise ValueError('capture sources changed while the model was running')
        meta.update(status='completed', completed_at=now(),
                    cohorts={a: {'path': f'evals/{SURFACE}/evidence/{a}.fresh.jsonl',
                                 'sha256': sha(evidence_path(f'{a}.fresh.jsonl').read_bytes())}
                             for a in systems},
                    journal_sha256=sha(evidence_path('fresh-capture-journal.jsonl').read_bytes()))
    except BaseException as error:
        meta.update(status='interrupted', error=type(error).__name__, stopped_at=now())
        write_json(capture_path, meta)
        raise
    write_json(capture_path, meta)
    print(json.dumps({'status': meta['status'], 'model_calls': meta['completed_model_calls'],
                      'next': 'report-fresh'}, indent=2))


def report_fresh():
    result_path = evidence_path('fresh-test-result.json')
    if result_path.exists():
        raise ValueError('fresh scoring already started; inspect its recorded result before any retry')
    plan, exp = load_fresh_plan()
    capture_path = evidence_path('fresh-capture.json')
    meta = json.loads(capture_path.read_text())
    if meta['status'] != 'completed' or meta['completed_model_calls'] != plan['logical_model_calls']:
        raise ValueError('fresh capture is not complete; no score or seal may be produced')
    if meta['sources'] != capture_sources():
        raise ValueError('source changed after capture; cannot score the fresh cohort')
    for arm in ('baseline', 'candidate'):
        if meta['cohorts'][arm]['sha256'] != sha(evidence_path(f'{arm}.fresh.jsonl').read_bytes()):
            raise ValueError(f'{arm}: fresh cohort changed after capture')
    if meta['journal_sha256'] != sha(evidence_path('fresh-capture-journal.jsonl').read_bytes()):
        raise ValueError('fresh capture journal changed')
    result = {'status': 'started', 'started_at': now(), 'capture_sha256': sha(capture_path.read_bytes()),
              'scorer_freeze_sha256': plan['scorer_freeze_sha256'], 'post_test_tuning': False}
    write_json(result_path, result, 'x')
    rows = {}
    for arm in ('baseline', 'candidate'):
        cohort = [json.loads(line) for line in evidence_path(f'{arm}.fresh.jsonl').read_text().splitlines()]
        if len(cohort) != len(exp['cases']) or {r['case_id'] for r in cohort} != set(exp['cases']):
            raise ValueError(f'{arm}: incomplete or duplicated fresh cohort')
        for row in cohort:
            case = exp['cases'][row['case_id']]
            if (row['split'] != 'test' or row['input_sha256'] != case['input_sha256']
                    or len(row['runs']) != REPEATS or len(row['raw_outputs']) != REPEATS):
                raise ValueError(f'{arm}: fresh cohort does not match expectations')
            for raw, run in zip(row['raw_outputs'], row['runs']):
                if {k: v for k, v in run.items() if k != 'cost_usd'} != parse(raw):
                    raise ValueError(f'{arm}: normalized answer differs from captured output')
        rows[arm] = cohort
    measured = score.score(exp, rows['baseline'], rows['candidate'])
    dims = []
    for did, dimension in measured['dimensions'].items():
        delta = dimension['delta']
        direction = 'improved' if delta['ci_low'] > 0 else 'regressed' if delta['ci_high'] < 0 else 'equivalent'
        dims.append({'dimension_id': did, 'critical': True,
                     'status': 'failed' if direction == 'regressed' else 'passed',
                     'direction_vs_baseline': direction,
                     'evidence_refs': [meta['cohorts'][a]['path'] for a in rows], **dimension})
    blockers = [d['dimension_id'] for d in dims if d['status'] == 'failed']
    decision = 'do_not_merge' if blockers else 'inconclusive'
    primary = 'store-unreachable-stops'
    noise = round(1 / (sum(c['kind'] == 'store_down' for c in exp['cases'].values()) * REPEATS) ** 0.5 + 0.00005, 4)
    costs = {a: sum(run['cost_usd'] for row in cohort for run in row['runs']) / (len(cohort) * REPEATS)
             for a, cohort in rows.items()}
    summary = '; '.join(f"{d['dimension_id']} {d['baseline']['score']:.2f} -> {d['candidate']['score']:.2f} "
                        f"(delta {d['delta']['value']:+.2f}, 95% [{d['delta']['ci_low']:+.2f}, {d['delta']['ci_high']:+.2f}])"
                        for d in dims)
    statement = f'Fresh held-out capture, {REPEATS} repeats per case. {summary}. '
    if blockers:
        statement += 'Critical regression blocks shipping: ' + ', '.join(blockers) + '. '
    elif next(d for d in dims if d['dimension_id'] == primary)['direction_vs_baseline'] == 'equivalent':
        statement += 'Quality-flat on the primary dimension; the gain is inside the noise. '
    else:
        statement += 'The measured primary result does not by itself authorize shipping. '
    if next(d for d in dims if d['dimension_id'] == primary)['direction_vs_baseline'] == 'equivalent':
        statement += 'Do not merge on quality grounds. '
    statement += ('Synthetic hand-written cases have not received new independent human judgment. '
                  'This tool-less single-turn simulation establishes a fresh seal but remains insufficient shipping evidence.')
    result.update(status='completed', completed_at=now(), decision=decision, scoring_passes=1,
                  fresh_cohorts=meta['cohorts'], test_cases_per_arm=len(exp['cases']),
                  test_runs_per_arm=len(exp['cases']) * REPEATS)
    write_json(result_path, result)
    source = {p: digest for p, digest in meta['sources'].items() if p.endswith(('/score.py', '/run_eval.py'))}
    deps = {p: digest for p, digest in meta['sources'].items() if p not in source and not p.endswith('/expectations.v3.json')}
    deps.update({f'evals/{SURFACE}/evidence/{p.name}': sha(p.read_bytes()) for p in (capture_path, result_path, evidence_path('fresh-capture-journal.jsonl'))})
    receipt = {
        'schema_version': 2, 'surface': SURFACE,
        'change': 'Fresh measurement of the original AGENTS.md store-unreachable and map front-door comparison after freezing the corrected scorer.',
        'measured_on': meta['completed_at'][:10], 'rung': 'regression',
        'adapter': {'surface': 'claude_code_cli', 'adapter_id': 'claude-p-single-turn-no-tools', 'adapter_version': '1',
                    'harness_id': f'evals/{SURFACE}/run_eval.py', 'harness_version': source[f'evals/{SURFACE}/run_eval.py'],
                    'provider_id': 'anthropic', 'model_id': ','.join(meta['models']), 'native_session_ref': meta['first_session'],
                    'configuration_fingerprint': 'sha256:' + sha(json.dumps({
                        'models': meta['models'], 'cli': meta['cli'], 'systems': meta['system_sha256'],
                        'baseline_ref': BASELINE_REF, 'repeats': REPEATS}, sort_keys=True))},
        'cases': {'total': len(exp['cases']), 'train': 0, 'test': len(exp['cases']),
                  'should_not_fire': sum(c['should_not_fire'] for c in exp['cases'].values()),
                  'sources': sorted({c['source'] for c in exp['cases'].values()})},
        'split': {'method': 'Every historical input group is train/development in v2; only newly authored unique input hashes appear in v3 test.',
                  'sealed_test': True, 'scorer_freeze_sha256': plan['scorer_freeze_sha256'],
                  'freeze_manifest_sha256': plan['freeze_manifest_sha256'],
                  'fresh_cohort_sha256': {a: c['sha256'] for a, c in meta['cohorts'].items()}},
        'repeats': REPEATS,
        'grader': {'kind': 'programmatic', 'validation': {'graded_twice': True, 'result': 'pass',
                   'agreement': 1.0, 'fresh_scoring_passes': 1,
                   'scope': 'Repeated synthetic-control validation before fresh capture; fresh answers scored once.',
                   **measured['controls']}},
        'noise_floor': noise, 'min_useful_gain': MIN_USEFUL_GAIN, 'primary_dimension': primary, 'dimensions': dims,
        'stage_results': [{'stage_id': 'session-start-and-map-routing', 'status': 'failed' if blockers else 'passed',
                           'dimension_ids': [d['dimension_id'] for d in dims],
                           'evidence_refs': [c['path'] for c in meta['cohorts'].values()]}],
        'cost': {'baseline_usd_per_case': round(costs['baseline'], 6), 'candidate_usd_per_case': round(costs['candidate'], 6)},
        'verdict': {'decision': decision, 'statement': statement},
        'notes': [
            'All 60 historical cases / 52 exposed input groups belong to train/development, recorded in expectations.v2.json and the scorer freeze. Their observations do not participate in this test score.',
            'The scorer source hash was committed and pushed before the fresh tasks and expectations existed. Expectations and both arm system hashes were bound before capture. Fresh outputs were never used to propose scorer changes.',
            'Fresh outputs are scored exactly once. graded_twice and agreement describe repeated synthetic-control validation before capture, recorded in evidence/grader-validation.json. They do not claim a second grading of fresh answers. The existing receipt checker also requires human-judged or production cases. These synthetic seeds remain hand_written, so that source requirement is not yet satisfied.',
            'The original Claude invocation flags, Haiku request, two AGENTS.md arms, and five repeats are preserved. Unknown CLI outcomes are not retried. Raw answers are retained without truncation; unknown answer fields remain visible to the frozen scorer.',
            'Single-turn, tool-less simulation measures declared next actions, not actual live tool use. New tasks are hand-written synthetic seeds. No production transcript was read. No post-test tuning is permitted.'
        ],
        'evidence': {'scorer': {'path': f'evals/{SURFACE}/score.py', 'function': 'score'},
                     'source': source, 'dependencies': deps,
                     'expectations': {'path': f'evals/{SURFACE}/expectations.v3.json',
                                      'version': exp['version'], 'sha256': sha(FRESH_EXPECTATIONS.read_bytes())},
                     'cohorts': meta['cohorts']}}
    write_json(HERE / 'receipt.json', receipt)
    print(json.dumps({'decision': decision, 'scoring_passes': 1, 'sealed_test': True,
                      'fresh_cohort_sha256': receipt['split']['fresh_cohort_sha256']}, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('build', 'capture-fresh', 'report-fresh'))
    args = parser.parse_args()
    {'build': build, 'capture-fresh': capture_fresh, 'report-fresh': report_fresh}[args.command]()
