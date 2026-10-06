#!/usr/bin/env python3
"""selftest.py — the eval's own health checks (claude-api eval-audit sections 1, 2, 4, 5).

Run before trusting any number from run_eval.py. Exits nonzero on the first
failed check. The receipt checks hold the producer, the checked-in receipt
and ops/check-eval-receipt.py to one schema and one evidence chain."""
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run_eval as R  # noqa: E402
import make_report as M  # noqa: E402

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
    original_load = R.load_run
    try:
        R.load_run = lambda variant, split=None: (_ for _ in ()).throw(
            AssertionError("candidate verdict read held-out test")) if split == "test" else original_load(variant, split)
        R.verdict("baseline", "v3", "recall")
        check("candidate verdict reads train only", True)
    finally:
        R.load_run = original_load
    import subprocess
    train_report = subprocess.run([sys.executable, os.path.join(HERE, "run_eval.py"),
                                   "--compare", "baseline", "v3", "--split", "train"],
                                  capture_output=True, text=True, check=True).stdout
    check("round comparison excludes held-out test", "== train:" in train_report and "== test:" not in train_report)
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        train_row = {"prompt_id": "train-case", "split": "train", "prompt": "train", "detail": {}}
        test_row = {"prompt_id": "test-case", "split": "test", "prompt": "test", "detail": {}}
        R.write_run(scratch, "candidate", [train_row])
        R.write_run(scratch, "candidate", [test_row])
        with open(os.path.join(scratch, "candidate", "results.jsonl"), encoding="utf-8") as handle:
            saved = [json.loads(line) for line in handle]
        check("final test run preserves frozen train results", saved == [test_row, train_row])
    # -- receipt: one schema across producer, checked-in receipt and gate
    with open(M.RECEIPT, encoding="utf-8") as handle:
        receipt = json.load(handle)
    spec = importlib.util.spec_from_file_location("check_eval_receipt", os.path.join(REPO, "ops", "check-eval-receipt.py"))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    errs = gate.validate_receipt(receipt, M.SURFACE, __import__("pathlib").Path(REPO))
    check("checked-in receipt passes the eval-receipt gate, recomputed from its evidence", not errs, "; ".join(errs[:3]))
    check("receipt binds the harness on disk", receipt["evidence"]["source"] == M.manifest(M.SOURCE))
    expectations = R.load_expectations()
    cohorts = {arm: M.read_jsonl(os.path.join(REPO, M.COHORTS[arm])) for arm in M.COHORTS}
    rebuilt = M.assemble_receipt(receipt, expectations, cohorts["baseline"], cohorts["candidate"],
                                 receipt["evidence"]["source"], receipt["evidence"]["dependencies"],
                                 measured_on=receipt["measured_on"],
                                 session_ref=receipt["adapter"]["native_session_ref"])
    check("producer reassembles the checked-in receipt from its cohorts", rebuilt == receipt)
    check("receipt discloses contaminated historical holdout",
          any("historical reuse" in note for note in receipt["notes"]))
    short = [r for r in cohorts["candidate"] if r["case_id"] != cohorts["candidate"][0]["case_id"]]
    try:
        R.score_receipt(expectations, cohorts["baseline"], short)
        check("scorer refuses a candidate cohort with a case deleted", False)
    except R.CohortError as exc:
        check("scorer refuses a candidate cohort with a case deleted", "missing" in str(exc))
    try:
        R.paired_bootstrap([{"prompt_id": "a"}, {"prompt_id": "b"}], [{"prompt_id": "a"}], lambda rs: len(rs))
        check("paired bootstrap refuses unequal cohorts", False)
    except R.CohortError:
        check("paired bootstrap refuses unequal cohorts", True)
    before = snapshot()
    cases = R.load_cases("all")
    world = R.World(expectations)
    check("frozen expectations match the live labels", not R.expectation_drift(world, cases, expectations))
    fresh = [R.observe(c, R.replay(world, c)) for c in sorted(cases, key=lambda c: c["id"])]
    check("candidate cohort is a fresh replay of this tree", fresh == cohorts["candidate"])
    review_case = {"id": "surface-review-read", "prompt": "",
                   "tool_calls": [{"tool_name": "Read", "tool_input": {
                       "file_path": os.path.join(REPO, "dealroom", "public", "index.html")}}],
                   "required": ["67580c28", "9293d609", "b7ec8f3b"],
                   "gold": ["67580c28", "9293d609", "b7ec8f3b"], "disputed": []}
    review_grade = R.grade(world, review_case, R.replay(world, review_case))
    check("eval labels a page review as owed and delivered",
          not review_grade["missed"] and set(review_case["required"]) <= set(review_grade["delivered"]))

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
        check(f"hard {row['id']}: ids are live rules",
              set(row["required"]) | set(row["acceptable"]) <= live)
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
    disputed_case = next((c for c in cases if c["disputed"]), None)
    if disputed_case is not None:
        d = disputed_case["disputed"][0]
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
