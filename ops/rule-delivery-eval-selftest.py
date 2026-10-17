#!/usr/bin/env python3
"""rule-delivery-eval-selftest.py -- acceptance test for the rule-delivery
evaluation harness (ops/rule_delivery_eval.py, CLI tools/rule-delivery-eval.py).

Written before the harness, and it is the harness's only proof that its
numbers mean what they say. Everything here is offline: no Jev request, no
record-layer call, no write outside a throwaway directory.

WHAT IS PROVEN:
  1. The scorer's arithmetic, on hand-counted cases: true/false positives and
     misses, precision, recall, F1, the per-stratum split, and that a gold rule
     OUTSIDE a path's responsibility universe is never charged to that path as
     a miss (a layer-zero rule is loaded at boot, not by the prompt path).
  2. Disputed labels are excluded from both sides of the count.
  3. Notification cases are scored apart from the pooled human numbers (their
     own delivered-on-empty and false-positive counts), so noise on machine
     turns cannot hide inside a human precision figure.
  4. Pack-level scoring.
  5. A PLANTED MIS-SCORED CASE: an expectation block whose numbers are wrong
     on purpose must be reported as a mismatch by check_expectations(), and a
     deliberately broken scorer (false positives and misses swapped) must fail
     the correct expectations. A checker that passes both would be vacuous.
  6. The real deterministic adapters (compiled prompt triggers, JIT PreToolUse
     rows and boot layer zero) run on the committed fixture
     against this checkout's real config and return only known rule ids.
  7. The Jev-backed adapters (the UserPromptSubmit judgment in
     ops/rule_trigger_delivery.py) run
     end to end against a FAKE client: the rule the fake says binds is
     delivered, every request carries the harness's calls-log sink, and no
     production log, cache or audit file in this checkout's out/ is created or
     modified (the dry-run guarantee).
  8. The committed fixture is small, synthetic and clean: about fifteen cases,
     every gold id is a known rule, and no email address, phone number or
     dollar figure appears in it.
  9. The CLI runs on the fixture with Jev off and writes a JSON and Markdown
     report to the directory it was given.
"""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "ops" / "rule_delivery_eval.py"
CLI = REPO / "tools" / "rule-delivery-eval.py"
FIXTURE = REPO / "ops" / "fixtures" / "rule-delivery-eval" / "cases.v1.json"

sys.path.append(str(REPO / "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
check = CHECKER.check


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------ synthetic world
META = {
    "p1": {"layer": "pack", "packs": ["engineering-git"]},
    "p2": {"layer": "pack", "packs": ["engineering-git"]},
    "p3": {"layer": "pack", "packs": ["client-deal"]},
    "z1": {"layer": "layer0", "packs": []},
    "c1": {"layer": "control", "packs": []},
}
CASES = [
    {"id": "h1", "stratum": "engineering", "prompt": "a", "tool_calls": [],
     "gold": ["p1", "z1"]},
    {"id": "h2", "stratum": "deals_clients", "prompt": "b", "tool_calls": [],
     "gold": ["p3"], "disputed": ["p2"]},
    {"id": "h3", "stratum": "engineering", "prompt": "c", "tool_calls": [],
     "gold": []},
    {"id": "n1", "stratum": "notifications", "prompt": "<task-notification>",
     "tool_calls": [], "gold": []},
]
# A path responsible for pack-layer rules only.
DELIVERIES = {
    "toy": {
        "h1": {"rules": {"p1", "p2"}},          # tp p1, fp p2; z1 not charged
        "h2": {"rules": {"p2"}},                # p2 disputed: ignored; fn p3
        "h3": {"rules": set()},                 # nothing, nothing owed
        "n1": {"rules": {"p1"}},                # notification noise
    },
    "packy": {
        "h1": {"rules": set(), "packs": {"engineering-git", "client-deal"}},
        "h2": {"rules": set(), "packs": set()},
        "h3": {"rules": set(), "packs": {"engineering-git"}},
        "n1": {"rules": set(), "packs": set()},
    },
}
UNIVERSES = {"toy": {"p1", "p2", "p3"}, "packy": {"p1", "p2", "p3"}}

# Hand-counted from the table above, independently of the harness.
CORRECT = {
    "toy": {"human.tp": 1, "human.fp": 1, "human.fn": 1,
            "human.precision": 0.5, "human.recall": 0.5, "human.f1": 0.5,
            "by_stratum.engineering.recall": 1.0,
            "by_stratum.deals_clients.recall": 0.0,
            "notifications.cases": 1, "notifications.cases_with_delivery": 1,
            "notifications.fp": 1},
    # gold packs: h1 {engineering-git}, h2 {client-deal}, h3 {}.
    # delivered packs: h1 {engineering-git, client-deal}, h2 {}, h3 {engineering-git}
    "packy": {"packs.tp": 1, "packs.fp": 2, "packs.fn": 1},
}


def test_scoring(ev):
    report = ev.score(CASES, DELIVERIES, UNIVERSES, META)
    mismatches = ev.check_expectations(report, CORRECT)
    check("scorer matches hand-counted numbers", not mismatches, mismatches)
    toy = report["paths"]["toy"]
    check("layer-zero gold is not charged to a pack-layer path",
          all(rid != "z1" for rid, _ in toy["misses"]), toy["misses"])
    check("disputed delivered rule is not a false positive",
          dict(toy["false_positives"]).get("p2") == 1, toy["false_positives"])
    check("the miss that is real is reported", dict(toy["misses"]).get("p3") == 1,
          toy["misses"])
    check("notification delivery is kept out of pooled human precision",
          toy["human"]["fp"] == 1 and toy["notifications"]["fp"] == 1, toy)
    case = report["per_case"]["h1"]["toy"]
    check("per-case detail names tp/fp/fn",
          case["tp"] == ["p1"] and case["fp"] == ["p2"] and case["fn"] == [], case)
    check("empty gold and empty delivery gives no division error",
          report["per_case"]["h3"]["toy"]["tp"] == [], report["per_case"]["h3"])

    # A correct delivery OUTSIDE the universe lifts precision, never recall.
    outside = ev.score(CASES, {"toy": {**DELIVERIES["toy"], "h1": {"rules": {"p1", "p2", "z1"}}}},
                       UNIVERSES, META)["paths"]["toy"]["human"]
    check("an out-of-universe correct delivery counts for precision only",
          outside["tp"] == 2 and outside["tp_in_universe"] == 1
          and outside["recall"] == 0.5 and abs(outside["precision"] - 2 / 3) < 1e-3, outside)

    # A delivered rule the labellers never saw is set aside, not charged.
    unseen = ev.score(CASES, DELIVERIES, UNIVERSES, META, labelled={"p1", "p3", "z1"})
    toy_unseen = unseen["paths"]["toy"]
    check("a delivered rule outside the labelled corpus is not a false positive",
          toy_unseen["human"]["fp"] == 0 and dict(toy_unseen["outside_labelled"]).get("p2") == 1,
          toy_unseen)

    # Re-scoring saved deliveries reproduces the numbers without re-running a path.
    again = ev.score(CASES, ev.deliveries_from_report(report), UNIVERSES, META)
    check("re-scoring a saved report reproduces the human numbers",
          again["paths"]["toy"]["human"] == toy["human"], again["paths"]["toy"]["human"])
    return report


def test_planted_misscore(ev, report):
    planted = copy.deepcopy(CORRECT)
    planted["toy"]["human.recall"] = 1.0      # PLANTED: the true value is 0.5
    planted["toy"]["human.fp"] = 0            # PLANTED: the true value is 1
    mismatches = ev.check_expectations(report, planted)
    check("planted wrong recall is caught",
          any("toy" in m and "human.recall" in m for m in mismatches), mismatches)
    check("planted wrong false-positive count is caught",
          any("toy" in m and "human.fp" in m for m in mismatches), mismatches)
    check("only the planted lines mismatch", len(mismatches) == 2, mismatches)

    # A broken scorer (fp and fn swapped) must fail the CORRECT expectations.
    original = ev.confusion

    def swapped(gold, delivered):
        tp, fp, fn = original(gold, delivered)
        return tp, fn, fp
    ev.confusion = swapped
    try:
        broken = ev.score(CASES, DELIVERIES, UNIVERSES, META)
    finally:
        ev.confusion = original
    caught = ev.check_expectations(broken, CORRECT)
    check("a scorer that swaps false positives and misses is caught", bool(caught), caught)


def test_fixture(ev):
    cases = ev.load_cases(FIXTURE)
    meta = ev.rule_meta(REPO)
    check("fixture has about fifteen cases", 12 <= len(cases) <= 20, len(cases))
    strata = {case["stratum"] for case in cases}
    check("fixture covers every stratum", strata >= set(ev.STRATA), strata)
    unknown = sorted({rid for case in cases for rid in case["gold"] if rid not in meta})
    check("every fixture gold id is a known rule", not unknown, unknown)
    raw = FIXTURE.read_text(encoding="utf-8")
    check("fixture carries no email address",
          not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", raw))
    check("fixture carries no phone number",
          not re.search(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b", raw))
    check("fixture carries no dollar figure", "$" not in raw)
    check("fixture declares itself synthetic",
          json.loads(raw).get("provenance", "").startswith("synthetic"))
    return cases, meta


def _snapshot(paths):
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
            for p in paths}


def test_deterministic_adapters(ev, cases, meta):
    guarded = [REPO / "out" / name for name in ev.GUARDED_OUT_FILES]
    before = _snapshot(guarded)
    adapters = ev.build_adapters(REPO, jev="off")
    names = {adapter["name"] for adapter in adapters}
    check("deterministic adapters are all present",
          names >= {"prompt_compiled", "jit_pretooluse", "boot_layer0"}, names)
    deliveries, errors = ev.run_adapters(cases, adapters)
    check("unanswered moments are reported explicitly; boot remains available",
          not errors['boot_layer0'] and not errors['boot_always_on']
          and all(reason=='rule_scope_selection_incomplete' for out in errors.values() for reason in out.values()), errors)
    for name, per_case in deliveries.items():
        unknown = sorted({rid for out in per_case.values() for rid in out["rules"]
                          if rid not in meta})
        check(f"{name} delivers only known rule ids", not unknown, unknown)
    tooled = [case for case in cases if case["tool_calls"]]
    check("fixture has tool-call cases for the JIT path", len(tooled) >= 3, len(tooled))
    fired = [cid for cid, out in deliveries["jit_pretooluse"].items() if out["rules"]]
    check("JIT is only scored on cases with tool calls",
          set(deliveries["jit_pretooluse"]) == {case["id"] for case in tooled},
          sorted(deliveries["jit_pretooluse"]))
    check("layered_triggers is present and scored only on cases with tool calls",
          set(deliveries.get("layered_triggers", {})) == {case["id"] for case in tooled},
          sorted(deliveries.get("layered_triggers", {})))
    check("layered_triggers delivers at least what the compiled table delivers",
          all(deliveries["jit_pretooluse"][cid]["rules"] <= out["rules"]
              for cid, out in deliveries["layered_triggers"].items()))
    layer0 = {rid for rid, row in meta.items() if row["layer"] == "layer0"}
    check("boot delivers exactly layer zero",
          all(out["rules"] == layer0 for out in deliveries["boot_layer0"].values()))
    # The live standing-context boot is sourced from rule-classes, while the
    # older enforcement-map layer0 is only a historical comparison path.
    classes = json.loads((REPO / "ops" / "config" / "rule-classes.v1.json").read_text())
    joe_boot = {rid for rid, row in classes["rules"].items()
                if (row.get("always_on") or rid in classes.get("keep_full_text", {})) and row.get("personal_to") in (None, "joe")}
    dell_boot = {rid for rid, row in classes["rules"].items()
                 if (row.get("always_on") or rid in classes.get("keep_full_text", {})) and row.get("personal_to") in (None, "dell")}
    check("live boot contract carries a nonempty always-on set", bool(joe_boot),
          len(joe_boot))
    check("boot adapter respects the sponsor's personal boundary",
          ev.boot_always_on_ids(REPO) == joe_boot
          and ev.boot_always_on_ids(REPO, sponsor="dell") == dell_boot
          and all(classes["rules"][rid].get("personal_to") != "dell" for rid in joe_boot))
    check("actual boot adapter is separate from legacy layer zero",
          "boot_always_on" in deliveries, sorted(deliveries))
    if "boot_always_on" in deliveries:
        check("actual boot carries exactly the Joe-scoped A and retained B/C rules",
              all(out["rules"] == joe_boot for out in deliveries["boot_always_on"].values())
              and all(classes["rules"][rid]["class"] == "a" or rid in classes.get("keep_full_text", {}) for rid in joe_boot))
        check("actual boot leaves D and unretained B/C rules indexed",
              not ({"5be2f462", "fa217e48", "fb2b263d"} & joe_boot))
        check("actual boot universe charges only its own rule set",
              ev.universes(meta, ["boot_always_on"], boot_ids=joe_boot)["boot_always_on"]
              == joe_boot)
    ev.add_system_rows(deliveries)
    check("scoped boot unions actual boot instead of legacy layer zero",
          all(joe_boot <= out["rules"] for out in deliveries["system_scoped_boot"].values()),
          sorted(deliveries["system_scoped_boot"]))
    after = _snapshot(guarded)
    check("deterministic run wrote no production log, cache or audit file",
          before == after, {k: (before[k], after[k]) for k in before if before[k] != after[k]})
    return deliveries


def test_jev_adapters(ev, meta):
    class Fake:
        noul = choice = score = staticmethod(lambda *a, **kw: {})
        def __init__(self):
            self.sinks = []
            self.caches = []
        def ask(self, state, questions, **kwargs):
            self.sinks.append(kwargs.get('calls_log'))
            self.caches.append(kwargs.get('cache_path'))
            return {'model':'jev-1.13.0', 'answers':{k:{'type':'choice','choice':'applies' if k=='bind_113b3833' else 'not_applicable','confidence':.99} for k in questions}}
    fake=Fake()
    before=_snapshot([REPO/'out'/name for name in ev.GUARDED_OUT_FILES])
    adapters=ev.build_adapters(REPO,jev='live',client_factory=lambda sink:ev.JevProxy(fake,sink))
    adapter=next(a for a in adapters if a['name']=='prompt_full')
    result=adapter['select']({'prompt':'Read doctrine before code work','tool_calls':[]})
    check('typed moment delivery preserves the admitted full rule',result['rules']=={'113b3833'},result)
    check('every evaluation request uses the no-log sink',bool(fake.sinks) and all(s==os.devnull for s in fake.sinks))
    check('transport cache is isolated from production', bool(fake.caches) and all('carr-rule-eval-' in path for path in fake.caches))
    after=_snapshot([REPO/'out'/name for name in ev.GUARDED_OUT_FILES])
    check('model evaluation wrote no production cache, log or audit',before==after)


def test_cli():
    with tempfile.TemporaryDirectory(prefix="rule-delivery-eval-selftest-") as tmp:
        result = subprocess.run(
            [sys.executable, str(CLI), "--cases", str(FIXTURE), "--jev", "off",
             "--out-dir", tmp],
            capture_output=True, text=True, timeout=300, cwd=str(REPO))
        check("CLI exits zero", result.returncode == 0, result.stderr[-2000:])
        report = Path(tmp) / "report.json"
        markdown = Path(tmp) / "report.md"
        check("CLI wrote report.json", report.exists())
        check("CLI wrote report.md", markdown.exists())
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            check("report names the system rows",
                  {"system_moment",
                   "system_scoped_boot", "boot_always_on", "boot_layer0"}
                  <= set(data["paths"]),
                  sorted(data["paths"]))
            check("report is marked dry-run", data.get("dry_run") is True)


def test_incomplete_selection(ev):
    adapters=[{'name':'lookup','select':lambda _: {'rules':{'11111111'},'complete':False}}]
    results,errors=ev.run_adapters([{'id':'uncertain'}],adapters)
    check('incomplete selection is a reported failure, never credited delivery',
          results['lookup']['uncertain']['rules']==set()
          and errors['lookup'].get('uncertain')=='rule_scope_selection_incomplete')


def main() -> int:
    ev = load(LIB, "rule_delivery_eval_for_selftest")
    report = test_scoring(ev)
    test_incomplete_selection(ev)
    test_planted_misscore(ev, report)
    cases, meta = test_fixture(ev)
    test_deterministic_adapters(ev, cases, meta)
    test_jev_adapters(ev, meta)
    test_cli()
    return CHECKER.summary(limit=20)


if __name__ == "__main__":
    raise SystemExit(main())
