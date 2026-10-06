#!/usr/bin/env python3
# doctrine: engineering-workflow-sop
"""ci-gate-shards-selftest.py -- proves the gates class splits honestly.

WRITTEN BEFORE THE IMPLEMENTATION (rule e65efc68: a gate gets its selftest
first). It was run and seen to fail before ops/ci-gate-shards.py existed.

What a bad split costs, and why each block below exists. A shard assignment that
drops a test turns a required check green on coverage nobody ran, the
"eight green, zero executed" shape ops/ci.sh already shipped once. So the first
block proves exact-once coverage, and the failure-path block proves that a
broken durations file can only ever make the split WORSE BALANCED, never smaller.

    .venv/bin/python ops/ci-gate-shards-selftest.py     # exit 0 = all pass
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    RESULTS.append((bool(ok), name))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  -- {detail}"))


def load(name: str, filename: str):
    path = REPO / "ops" / filename
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SH = load("ci_gate_shards", "ci-gate-shards.py")
RF = load("ci_gate_durations_refresh", "ci-gate-durations-refresh.py")
EV = load("ci_evidence", "ci-evidence.py")


def synthetic(seed: int, count: int = 70):
    rng = random.Random(seed)
    tests = [f"ops/t{i:03d}-selftest.py" for i in range(count)]
    # A realistic skew: a few heavy suites, a long tail of sub-second ones.
    durations = {t: (rng.uniform(20, 90) if i % 17 == 0 else rng.uniform(0.2, 2.0))
                 for i, t in enumerate(tests)}
    return tests, durations


def write_durations(path: Path, tests: dict) -> None:
    path.write_text(json.dumps({"schema": SH.SCHEMA, "unit": "seconds", "tests": tests}))


def real_tests():
    import glob
    pats = ["ops/*-selftest.py", "tools/test-*.py", "tools/test_*.py", "tools/*-selftest.py",
            "tools/room-bridge/test_*_unit.py", "tools/room-bridge/test_activation_reliability.py"]
    out = []
    for p in pats:
        out += sorted(glob.glob(str(REPO / p)))
    return [str(Path(p).relative_to(REPO)) for p in out]


def test_module_exists():
    check("ops/ci-gate-shards.py exists", SH is not None)
    check("ops/ci-gate-durations-refresh.py exists", RF is not None)


def test_every_test_lands_in_exactly_one_shard():
    # (a) N = 1..4, synthetic AND the real tree, with and without durations.
    real = real_tests()
    check("the real tree has a large gates set to split", len(real) > 100, len(real))
    for label, tests, durations in (("synthetic", *synthetic(1)), ("real/no durations", real, {}),
                                    ("real/skewed", real, {t: (60.0 if i % 40 == 0 else 0.5)
                                                           for i, t in enumerate(real)})):
        for n in (1, 2, 3, 4):
            shards = [SH.select(tests, i, n, durations) for i in range(1, n + 1)]
            flat = [t for s in shards for t in s]
            check(f"{label} N={n}: union covers every test exactly once",
                  sorted(flat) == sorted(tests) and len(flat) == len(set(flat)),
                  {"missing": sorted(set(tests) - set(flat))[:3], "dupes": len(flat) - len(set(flat))})


def test_balance_within_longest_single_test():
    # (b) the LPT guarantee: max load - min load <= the longest single test.
    for seed in range(20):
        tests, durations = synthetic(seed)
        longest = max(durations.values())
        for n in (2, 3, 4):
            loads = [sum(durations[t] for t in SH.select(tests, i, n, durations))
                     for i in range(1, n + 1)]
            check(f"seed {seed} N={n}: shard sums differ by <= longest test",
                  max(loads) - min(loads) <= longest + 1e-9,
                  {"spread": max(loads) - min(loads), "longest": longest})


def test_unrecorded_test_still_runs_with_default_weight():
    # (c)
    tests, durations = synthetic(3)
    extra = ["ops/brand-new-selftest.py", "tools/test-also-new.py"]
    for n in (1, 2, 3):
        flat = [t for i in range(1, n + 1) for t in SH.select(tests + extra, i, n, durations)]
        check(f"N={n}: tests with no recorded duration still run",
              all(flat.count(e) == 1 for e in extra), [flat.count(e) for e in extra])
    check("the default weight is a positive constant",
          isinstance(SH.DEFAULT_WEIGHT_SECONDS, (int, float)) and SH.DEFAULT_WEIGHT_SECONDS > 0)
    # The default weight is what the balancer sees: one 10s test and six unknown
    # tests of default weight must not all pile onto the shard holding the 10s one.
    big = {"ops/big-selftest.py": SH.DEFAULT_WEIGHT_SECONDS * 6}
    unknown = [f"ops/u{i}-selftest.py" for i in range(6)]
    a = SH.select(["ops/big-selftest.py"] + unknown, 1, 2, big)
    b = SH.select(["ops/big-selftest.py"] + unknown, 2, 2, big)
    check("six default-weight tests balance one test of equal total weight",
          sorted([len(a), len(b)]) == [1, 6], (len(a), len(b)))


def test_single_shard_is_todays_behaviour():
    # (d) N = 1 returns the input untouched: same members, same order, even
    # when the durations file would have reordered or is garbage.
    tests, durations = synthetic(4)
    check("1/1 returns the input list unchanged and in order",
          SH.select(tests, 1, 1, durations) == tests)
    check("1/1 ignores durations entirely", SH.select(tests, 1, 1, {}) == tests)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "d.json"
        bad.write_text("{not json")
        out = subprocess.run([sys.executable, str(REPO / "ops/ci-gate-shards.py"), "select",
                              "--shard", "1/1", "--durations", str(bad)],
                             input="\n".join(tests) + "\n", capture_output=True, text=True)
        check("CLI 1/1 with a corrupt durations file echoes every test in order",
              out.returncode == 0 and out.stdout.split() == tests, out.stderr[-200:])
    body = (REPO / "ops/ci.sh").read_text()
    check("ci.sh gates class has no filter when --shard is absent",
          'SHARD=""' in body and re.search(r'\[ -n "\$SHARD" \]', body) is not None)


def test_a_bad_durations_file_never_skips_tests():
    # (e) missing / corrupt / wrong-schema / hostile values: deterministic, whole.
    tests, durations = synthetic(5)
    baseline = {n: [SH.select(tests, i, n, {}) for i in range(1, n + 1)] for n in (2, 3, 4)}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        cases = {
            "missing": d / "absent.json",
            "empty file": d / "empty.json",
            "not json": d / "garbage.json",
            "json list": d / "list.json",
            "wrong schema": d / "schema.json",
            "tests not a dict": d / "nodict.json",
            "a directory": d,
        }
        cases["empty file"].write_text("")
        cases["not json"].write_text("{not json")
        cases["json list"].write_text("[1,2,3]")
        cases["wrong schema"].write_text(json.dumps({"schema": "other/v9", "tests": durations}))
        cases["tests not a dict"].write_text(json.dumps({"schema": SH.SCHEMA, "tests": [1, 2]}))
        for label, path in cases.items():
            loaded, warning = SH.load_durations(path)
            check(f"{label}: loads as empty with a warning, never raises",
                  loaded == {} and bool(warning), (loaded, warning))
            for n in (2, 3, 4):
                shards = [SH.select(tests, i, n, loaded) for i in range(1, n + 1)]
                flat = [t for s in shards for t in s]
                check(f"{label} N={n}: every test still runs exactly once",
                      sorted(flat) == sorted(tests) and len(flat) == len(set(flat)))
                check(f"{label} N={n}: fallback split is the deterministic no-durations split",
                      shards == baseline[n])
        hostile = d / "hostile.json"
        values = {tests[0]: "slow", tests[1]: -3, tests[2]: float("nan"), tests[3]: 0,
                  tests[4]: True, tests[5]: None, tests[6]: 12.5}
        hostile.write_text(json.dumps({"schema": SH.SCHEMA, "tests": values}))
        loaded, warning = SH.load_durations(hostile)
        check("hostile per-test values are dropped, valid ones kept",
              loaded == {tests[6]: 12.5}, loaded)
        flat = [t for i in (1, 2) for t in SH.select(tests, i, 2, loaded)]
        check("a half-valid file still runs every test once",
              sorted(flat) == sorted(tests))
        good = d / "good.json"
        write_durations(good, durations)
        loaded, warning = SH.load_durations(good)
        check("a valid file loads without a warning", loaded == durations and not warning, warning)


def test_assignment_is_deterministic_and_order_independent():
    tests, durations = synthetic(6)
    first = [SH.select(tests, i, 3, durations) for i in range(1, 4)]
    again = [SH.select(tests, i, 3, durations) for i in range(1, 4)]
    check("same durations, same input -> identical split on a re-run", first == again)
    shuffled = list(tests)
    random.Random(9).shuffle(shuffled)
    other = [SH.select(shuffled, i, 3, durations) for i in range(1, 4)]
    check("input order does not move any test to a different shard",
          [set(a) for a in first] == [set(b) for b in other])
    check("within a shard the input order is preserved (stable reporting)",
          all(s == [t for t in tests if t in set(s)] for s in first))
    ties = [f"ops/tie{i}-selftest.py" for i in range(9)]
    equal = {t: 1.0 for t in ties}
    got = [set(SH.select(list(reversed(ties)), i, 3, equal)) for i in (1, 2, 3)]
    check("equal durations: input order cannot change the split",
          got == [set(SH.select(ties, i, 3, equal)) for i in (1, 2, 3)])


def test_shard_argument_parsing():
    for good, want in (("1/2", (1, 2)), ("2/2", (2, 2)), ("4/4", (4, 4)), ("1/1", (1, 1))):
        check(f"parse {good}", SH.parse_shard(good) == want)
    for bad in ("0/2", "3/2", "1/0", "a/b", "1", "1/2/3", "", "-1/2", "1/-2", " 1/2", "1/2 "):
        try:
            SH.parse_shard(bad)
            check(f"reject {bad!r}", False, "accepted")
        except ValueError:
            check(f"reject {bad!r}", True)


def run_ci(*args):
    env = dict(os.environ, CARR_CI_PORTABLE_ONLY="1")
    return subprocess.run(["bash", str(REPO / "ops/ci.sh"), *args], cwd=REPO,
                          capture_output=True, text=True, timeout=120, env=env)


def test_ci_sh_validates_shard_flag():
    for args in (["--only", "gates", "--shard", "3/2"], ["--only", "gates", "--shard", "x"],
                 ["--only", "secret", "--shard", "1/2"], ["--shard", "0/2", "--only", "gates"]):
        out = run_ci(*args)
        check(f"ci.sh {' '.join(args)} is refused with exit 64", out.returncode == 64,
              (out.returncode, out.stderr[-200:]))


def test_refresh_merges_timings_deterministically():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        def timing(name, tests):
            (d / name).write_text(json.dumps({"schema": RF.TIMINGS_SCHEMA, "unit": "seconds",
                                              "tests": tests}))
        timing("a.json", {"ops/a-selftest.py": 10.0, "ops/b-selftest.py": 1.0})
        timing("b.json", {"ops/a-selftest.py": 30.0, "ops/b-selftest.py": 3.0, "ops/c-selftest.py": 5.0})
        timing("c.json", {"ops/a-selftest.py": 20.0, "ops/b-selftest.py": 2.0})
        (d / "junk.json").write_text("{nope")
        merged, notes = RF.merge([d / n for n in ("a.json", "b.json", "c.json", "junk.json")])
        check("median over the recent runs", merged["ops/a-selftest.py"] == 20.0
              and merged["ops/b-selftest.py"] == 2.0, merged)
        check("a test seen in some runs is kept", merged["ops/c-selftest.py"] == 5.0, merged)
        check("a corrupt timing file is reported and skipped", any("junk.json" in n for n in notes), notes)
        out1, out2 = d / "o1.json", d / "o2.json"
        RF.write(out1, merged)
        RF.write(out2, dict(reversed(list(merged.items()))))
        check("output bytes are identical whatever the input order", out1.read_bytes() == out2.read_bytes())
        check("output validates against the loader",
              SH.load_durations(out1) == (merged, None))
        merged_none, _ = RF.merge([d / "junk.json"])
        check("no valid timing input yields no file rewrite", merged_none == {})
        keep = d / "keep.json"
        write_durations(keep, {"ops/a-selftest.py": 1.0})
        before = keep.read_bytes()
        rc = subprocess.run([sys.executable, str(REPO / "ops/ci-gate-durations-refresh.py"),
                             "--from-dir", str(d / "empty-dir-that-does-not-exist"),
                             "--output", str(keep)], capture_output=True, text=True).returncode
        check("with nothing to merge the committed file is left untouched and the run fails",
              rc != 0 and keep.read_bytes() == before, rc)


def test_committed_durations_file_is_loadable():
    path = REPO / "ops/config/ci-gate-durations.v1.json"
    loaded, warning = SH.load_durations(path)
    check("the committed durations file exists and loads without a warning",
          path.exists() and warning is None, warning)
    check("every recorded value is a positive finite number",
          all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in loaded.values()))


def test_evidence_reuse_needs_every_gates_shard():
    ci_yml = (REPO / ".github/workflows/ci.yml").read_text()
    groups = EV.class_groups()
    shard_groups = [g for g in groups if g.startswith("gates")]
    check("ci.yml runs gates as shards of one class", len(shard_groups) >= 2, shard_groups)
    check("every shard names its index and the total",
          all(re.fullmatch(r"gates --shard \d+/\d+", g) for g in shard_groups), shard_groups)
    check("the shards cover 1..N exactly",
          EV.shards_complete(groups), groups)
    check("a lone shard of two is NOT a complete gates class",
          not EV.shards_complete([g for g in groups if g != "gates --shard 2/2"]))
    check("mixed totals are not complete",
          not EV.shards_complete(["gates --shard 1/2", "gates --shard 2/3"]))
    check("an unsharded gates group is complete on its own",
          EV.shards_complete(["gates", "replay"]))
    run = {"name": "CI", "event": "pull_request", "status": "completed", "conclusion": "success",
           "id": 7, "run_attempt": 1, "head_sha": "h"}
    receipt = {"schema": "carr-ci-evidence/v1", "run_id": 7, "run_attempt": 1, "head_sha": "h",
               "tree_sha": "t", "contract": "c", "groups": groups}

    def jobs(skip=None):
        names = ["ops/ci.sh --strict"] + ["ops/ci.sh --strict --only " + g for g in groups]
        return [{"name": n, "conclusion": "success"} for n in names if n != skip]
    kw = dict(tree="t", head_tree="t", tested_tree="t", contract="c", groups=groups)
    check("both gates shards green -> evidence is reusable",
          EV.can_reuse(run, jobs(), receipt, **kw))
    for g in shard_groups:
        check(f"shard job '{g}' missing -> evidence is NOT reusable",
              not EV.can_reuse(run, jobs("ops/ci.sh --strict --only " + g), receipt, **kw))
    partial = [g for g in groups if g != shard_groups[-1]]
    check("a receipt recorded with a missing shard group is NOT reusable",
          not EV.can_reuse(run, jobs(), dict(receipt, groups=partial),
                           **dict(kw, groups=partial)))
    check("the workflow still carries the required context job name",
          "name: ops/ci.sh --strict\n" in ci_yml)


def main() -> int:
    tests = [test_module_exists]
    if SH is not None and RF is not None and EV is not None:
        tests += [test_every_test_lands_in_exactly_one_shard,
                  test_balance_within_longest_single_test,
                  test_unrecorded_test_still_runs_with_default_weight,
                  test_single_shard_is_todays_behaviour,
                  test_a_bad_durations_file_never_skips_tests,
                  test_assignment_is_deterministic_and_order_independent,
                  test_shard_argument_parsing, test_ci_sh_validates_shard_flag,
                  test_refresh_merges_timings_deterministically,
                  test_committed_durations_file_is_loadable,
                  test_evidence_reuse_needs_every_gates_shard]
    for t in tests:
        print(t.__name__)
        try:
            t()
        except Exception as exc:  # a crashing test is a failing test, never a skipped one
            check(f"{t.__name__} ran to completion", False, repr(exc))
    failed = sum(1 for ok, _ in RESULTS if not ok)
    print(f"\nci-gate-shards-selftest: {len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
