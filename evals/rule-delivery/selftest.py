#!/usr/bin/env python3
"""selftest.py — the eval's own health checks (claude-api eval-audit sections 1, 2, 4, 5).

Run before trusting any number from run_eval.py. Exits nonzero on the first
failed check."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run_eval as R  # noqa: E402

GUARDED = ("rule-trigger-delivery.jsonl", "rule-prompt-delivered.json", "jev-judge.jsonl",
           "jev-rule-select.jsonl", "jev-calls.jsonl")


def snapshot():
    out = os.path.join(REPO, "out")
    return {name: (os.path.getmtime(os.path.join(out, name)), os.path.getsize(os.path.join(out, name)))
            for name in GUARDED if os.path.exists(os.path.join(out, name))}


COUNT = [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    if not ok:
        print("FAIL " + name + (f"  {detail}" if detail else ""))
        raise SystemExit(1)
    if not name.startswith(("hard ", "real-replay bash")):
        print("PASS " + name + (f"  {detail}" if detail else ""))


def main():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="merge-path-") as scratch:
        probe = (
            "import os, sys; "
            f"sys.path.insert(0, {HERE!r}); "
            "import run_eval; "
            "command = 'cd ' + os.getcwd() + ' && git status --short'; "
            "assert run_eval.read_only_command(command), command"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=scratch,
                                capture_output=True, text=True)
        check("read-only command allowed from a path containing merge",
              result.returncode == 0, result.stderr or result.stdout)

    with tempfile.TemporaryDirectory() as scratch:
        train_row = {"prompt_id": "train-case", "split": "train", "prompt": "train", "detail": {}}
        test_row = {"prompt_id": "test-case", "split": "test", "prompt": "test", "detail": {}}
        R.write_run(scratch, "candidate", [train_row])
        R.write_run(scratch, "candidate", [test_row])
        with open(os.path.join(scratch, "candidate", "results.jsonl"), encoding="utf-8") as handle:
            saved = [json.loads(line) for line in handle]
        check("final test run preserves frozen train results", saved == [test_row, train_row])
    expectations = R.load_expectations()
    retired_case = {"id": "retirement-regression", "prompt": "", "tool_calls": [],
                    "required": ["1fcaa63a", "11111111", "22222222"],
                    "gold": ["1fcaa63a", "11111111", "22222222"],
                    "disputed": []}
    historical_label = {"expected": retired_case["required"], "allowed": retired_case["gold"],
                        "should_not_fire": False}
    historical = {"cases": {retired_case["id"]: historical_label,
                            "retired-only": {"expected": ["1fcaa63a"], "allowed": ["1fcaa63a"],
                                             "should_not_fire": False}},
                  "labelled": list(retired_case["gold"])}
    historical_bytes = json.dumps(historical, sort_keys=True)
    retirement_world = R.World(historical)
    check("retirement reconciles frozen grading without hiding active rules",
          retirement_world.expected(retired_case) == ["11111111", "22222222"]
          and retirement_world.label(retired_case)["allowed"] == ["11111111", "22222222"]
          and retirement_world.owed == {"11111111", "22222222"}
          and retirement_world.labelled == {"11111111", "22222222"}
          and retirement_world.derive(retired_case)["expected"] == ["11111111", "22222222"]
          and retirement_world.label({"id": "retired-only"}) == {
              "expected": [], "allowed": [], "should_not_fire": True}
          and json.dumps(historical, sort_keys=True) == historical_bytes)
    missing = R.grade(retirement_world, retired_case, [])
    check("unregistered missing-active rule remains a replay failure", "22222222" in missing["missed"])
    with tempfile.TemporaryDirectory() as scratch:
        path = os.path.join(scratch, "pending-gold.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema": "rule-delivery-pending-gold/v1",
                       "rules": {"1fcaa63a": {}, "11111111": {}}}, handle)
        check("retired pending-gold rules do not become missing-active errors",
              R.pending_gold_ids({"11111111": "Synthetic active rule"}, path) == {"11111111"})
        try:
            R.pending_gold_ids({}, path)
            check("pending-gold still refuses unknown active rules", False)
        except ValueError:
            check("pending-gold still refuses unknown active rules", True)
    try:
        R.paired_bootstrap([{"prompt_id": "a"}, {"prompt_id": "b"}], [{"prompt_id": "a"}], lambda rs: len(rs))
        check("paired bootstrap refuses unequal cohorts", False)
    except R.CohortError:
        check("paired bootstrap refuses unequal cohorts", True)
    before = snapshot()
    cases = R.load_cases("all")
    world = R.World(expectations)
    retirements = world.selection.retirements
    active_expectations = retirements.expectations(expectations)
    denominators = []
    for field in ("expected", "allowed"):
        frozen_count = sum(len(label[field]) for label in expectations["cases"].values())
        active_count = sum(len(label[field]) for label in active_expectations["cases"].values())
        denominators.append(f"{field}_rules={frozen_count}->{active_count} (-{frozen_count-active_count})")
    frozen_labelled, active_labelled = len(expectations["labelled"]), len(active_expectations["labelled"])
    denominators.append(f"labelled_rules={frozen_labelled}->{active_labelled} (-{frozen_labelled-active_labelled})")
    frozen_owes = sum(bool(label["expected"]) for label in expectations["cases"].values())
    active_owes = sum(bool(label["expected"]) for label in active_expectations["cases"].values())
    denominators.append(f"owes_cases={frozen_owes}->{active_owes} (-{frozen_owes-active_owes})")
    print(f"INFO retirement denominator adjustment ({len(retirements.ids)} retired rules): "
          + "; ".join(denominators))
    for split in ("train", "test"):
        frozen_count = sum(len(label["expected"]) for label in expectations["cases"].values()
                           if label["split"] == split)
        active_count = sum(len(label["expected"]) for label in active_expectations["cases"].values()
                           if label["split"] == split)
        print(f"INFO {split} expected denominator: {frozen_count}->{active_count} (-{frozen_count-active_count})")
    availability_cases = [case for case in cases if case["source"] == "v2" and case["split"] == "test"]
    frozen_availability = sum(len(case["gold"]) for case in availability_cases)
    active_availability = sum(len(retirements.active(case["gold"])) for case in availability_cases)
    print(f"INFO test full-text availability denominator: {frozen_availability}->{active_availability} "
          f"(-{frozen_availability-active_availability})")
    check("frozen expectations match the live labels", not R.expectation_drift(world, cases, expectations))
    fresh = [R.observe(c, R.replay(world, c)) for c in sorted(cases, key=lambda c: c["id"])]
    pairs = []
    for case in cases:
        old = world.selection.fixture['cases'][case['id']]
        current = [world.prompt_ids(case)] + [world.tool_ids(case, i, call) for i, call in enumerate(case['tool_calls'])]
        if current != [retirements.active(ids) for ids in old['events']] or case['input_sha256'] != old['input_sha256']:
            pairs.append(case['id'])
    frozen_deliveries = sum(len(ids) for row in world.selection.fixture['cases'].values() for ids in row['events'])
    active_deliveries = sum(len(retirements.active(ids))
                            for row in world.selection.fixture['cases'].values() for ids in row['events'])
    check('paired routing preserves all 339 cases, input hashes and event order', len(cases) == 339 and not pairs,
          f"deliveries={frozen_deliveries}->{active_deliveries} (-{frozen_deliveries-active_deliveries}); mismatches={pairs}")
    from unittest.mock import patch
    structural_calls={json.dumps(entry['call'],sort_keys=True)
                      for entry in world.selection.fixture['structural_calls'] if retirements.active(entry['ids'])}
    probe = next((case, i, call) for case in cases for i, call in enumerate(case['tool_calls'])
                 if json.dumps(call,sort_keys=True) in structural_calls)
    with patch('lib.rule_scope_lookup.structural_matches', return_value=False):
        mutant = R.World(expectations)
        damaged = mutant.tool_ids(*probe)
    check('paired replay detects disabled structural selection',
          damaged != retirements.active(world.selection.fixture['cases'][probe[0]['id']]['events'][probe[1]+1]))
    import shutil
    from pathlib import Path
    portable = next((case, i, call) for case in cases for i, call in enumerate(case['tool_calls'])
                    if REPO in json.dumps(call))
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        for relative in ('ops/fixtures/rule-scope-compatibility.v1.json',
                         'ops/config/rule-selection-corpus.v1.json',
                         'ops/config/rule-retirements.v1.json',
                         'ops/fixtures/rule-delivery-eval/scope-backfills.json'):
            target=root/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(Path(REPO)/relative,target)
        from ops.rule_scope_replay import FrozenSelection
        relocated=FrozenSelection(root)
        event=dict(hook_event_name='PreToolUse',task=portable[0]['prompt'],**portable[2])
        event=json.loads(json.dumps(event).replace(REPO,str(root)))
        result=relocated.select(event)
        check('paired replay survives a relocated checkout',result['complete']
              and [r['id'] for r in result['rules']]==world.tool_ids(*portable))
    review_case = {"id": "surface-review-read", "prompt": "",
                   "tool_calls": [{"tool_name": "Read", "tool_input": {
                       "file_path": os.path.join(REPO, "dealroom", "public", "index.html")}}],
                   "required": ["67580c28", "9293d609", "b7ec8f3b"],
                   "gold": ["67580c28", "9293d609", "b7ec8f3b"], "disputed": []}
    from lib.rule_scope_lookup import select_rules
    fixture = world.selection.fixture
    catalog = dict(world.selection.catalog, rules=[dict(id=rid, statement='Synthetic scope contract',
                    personal_to=None, scope=scope) for rid, scope in fixture['structural_scopes'].items()
                    if rid not in retirements.ids])
    structural_mismatches = []
    for index, entry in enumerate(fixture['structural_calls']):
        result = select_rules(catalog, dict(hook_event_name='PreToolUse', **entry['call']))
        if not result['complete'] or [rule['id'] for rule in result['rules']] != retirements.active(entry['ids']):
            structural_mismatches.append(index)
    frozen_structural = sum(len(entry['ids']) for entry in fixture['structural_calls'])
    active_structural = sum(len(retirements.active(entry['ids'])) for entry in fixture['structural_calls'])
    check('paired structural routing preserves all 418 calls',
          len(fixture['structural_calls']) == 418 and not structural_mismatches,
          f"deliveries={frozen_structural}->{active_structural} (-{frozen_structural-active_structural}); mismatches={structural_mismatches}")
    result = select_rules(catalog, dict(tool_name='Read',tool_input=review_case['tool_calls'][0]['tool_input']))
    check('stored structural contract delivers page-review rules',
          set(review_case['required']) <= {r['id'] for r in result['rules']})

    # -- task design: split frozen, disjoint, both halves non-trivial
    with open(os.path.join(HERE, "split.json"), "r", encoding="utf-8") as handle:
        frozen = json.load(handle)
    now = {c["id"]: c["split"] for c in sorted(cases, key=lambda c: c["id"])}
    check("split frozen (ids and splits unchanged since baseline)", now == frozen["split"])
    check("split sha matches", hashlib.sha256(json.dumps(now, sort_keys=True).encode())
          .hexdigest() == frozen["sha256"])
    for split in ("train", "test"):
        sub = [c for c in cases if c["split"] == split]
        owes = [c for c in sub if world.expected(c)]
        quiet = [c for c in sub if not world.expected(c)]
        check(f"{split}: has owed and should-not-fire cases", len(owes) >= 20 and len(quiet) >= 20,
              f"owes={len(owes)} quiet={len(quiet)}")
    check("case ids unique", len({c["id"] for c in cases}) == len(cases))

    # -- hard cases: labels valid, one-sentence reasons, quiet kinds require nothing
    with open(R.HARD_CASES, "r", encoding="utf-8") as handle:
        hard = json.load(handle)["cases"]
    live = set(world.statements)
    for row in hard:
        active_hard_ids = retirements.active(set(row["required"]) | set(row["acceptable"]))
        check(f"hard {row['id']}: ids are live rules",
              set(active_hard_ids) <= live)
        check(f"hard {row['id']}: has a one-sentence reason", row["why"].strip().endswith(".")
              and row["why"].count(". ") == 0)
        if row["kind"] in ("should_not_fire", "near_miss"):
            check(f"hard {row['id']}: quiet kinds require and accept nothing",
                  not row["required"] and not row["acceptable"])
    kinds = {row["kind"] for row in hard}
    check("hard set covers should-not-fire, near-miss and positive", kinds == {
        "should_not_fire", "near_miss", "positive"})

    # -- recorded traces: the routine-read class is real and read-only
    replay = R.replay_cases()
    check("real-replay routine reads loaded", len(replay) >= 40, f"n={len(replay)}")
    for case in replay:
        for call in case["tool_calls"]:
            if call["tool_name"] == "Bash":
                check("real-replay bash is read-only", R.read_only_command(
                    call["tool_input"]["command"]))
    check("read_only_command rejects a push", not R.read_only_command("git push origin main"))
    check("read_only_command rejects a redirect", not R.read_only_command("ls > out.txt"))

    # -- harness: oracle, null, flood through the grader
    def grade_with(delivered_fn):
        rows = []
        for case in cases:
            events = [{"kind": "prompt", "tool": None, "ids": delivered_fn(case),
                       "fresh": delivered_fn(case), "tokens": 0.0, "overflow": False}]
            g = R.grade(world, case, events)
            rows.append({"prompt_id": case["id"], "detail": g, "split": case["split"]})
        return rows
    oracle = R.summarize(grade_with(lambda c: world.expected(c)))
    check("oracle: recall 1.0", abs(oracle["recall_micro"] - 1.0) < 1e-9)
    check("oracle: zero false deliveries", oracle["false_rules"] == 0)
    null = R.summarize(grade_with(lambda c: []))
    check("null: recall 0.0", null["recall_micro"] == 0.0)
    check("null: zero false deliveries", null["false_rules"] == 0 and null["sn_dirty_cases"] == 0)
    flood = R.summarize(grade_with(lambda c: sorted(world.owed)))
    check("flood: recall 1.0 but false deliveries and dirty should-not-fire cases",
          flood["recall_micro"] == 1.0 and flood["false_rules"] > 0 and flood["sn_dirty_cases"] > 0)
    disputed_case = next((c for c in cases if retirements.active(c["disputed"])), None)
    if disputed_case is not None:
        d = retirements.active(disputed_case["disputed"])[0]
        g = R.grade(world, disputed_case, [{"kind": "prompt", "tool": None, "ids": [d],
                                            "fresh": [d], "tokens": 0.0, "overflow": False}])
        check("disputed rule is neither hit nor false", d not in g["false"] and d not in g["hit"])

    # -- mechanism wired: the score depends on the triggers
    rows = R.run("all", "selftest", None)
    real = R.summarize(rows)
    check("real layer scores above the null", real["recall_micro"] > 0.05,
          f"recall={real['recall_micro']:.3f}")
    World = R.World
    saved_p, saved_t = World.prompt_ids, World.tool_ids
    World.prompt_ids = lambda self, case: []
    World.tool_ids = lambda self, case, i, call: []
    try:
        dead = R.summarize(R.run("all", "selftest", None))
    finally:
        World.prompt_ids, World.tool_ids = saved_p, saved_t
    check("disabling delivery drops recall to 0", dead["recall_micro"] == 0.0)

    # -- determinism and headline recompute
    again = R.summarize(R.run("all", "selftest", None))
    check("two runs agree exactly", json.dumps(real, sort_keys=True, default=str)
          == json.dumps(again, sort_keys=True, default=str))
    hit = sum(len(r["detail"]["hit"]) for r in rows)
    exp = sum(len(r["detail"]["expected"]) for r in rows)
    check("headline recomputes from raw rows", abs(real["recall_micro"] - hit / exp) < 1e-12)
    check("tokens are positive when anything is delivered", real["tokens_total"] > 0)

    # -- dry run: nothing written to production logs
    check("no production log or cache written", snapshot() == before)
    print(f"selftest ok ({COUNT[0]} checks)")


if __name__ == "__main__":
    main()
