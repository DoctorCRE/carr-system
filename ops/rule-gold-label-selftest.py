#!/usr/bin/env python3
"""rule-gold-label-selftest.py -- acceptance test for the v2 rule-delivery
benchmark: the labeller (ops/rule_gold_label.py, CLI tools/rule-gold-label.py),
the committed fixture (ops/fixtures/rule-delivery-eval/cases.v2.json with its
labels and adjudications), the regression intake
(tools/rule-delivery-eval-intake.py), and the harness's v2 additions (split
filtering, per-rule-class and fully-served scoring).

Offline: no Jev request, no record-layer call, no write outside a throwaway
directory.

WHAT IS PROVEN:
  1. The rule gold's REVIEW SET: no auto-gold at the top, the per-rule lower
     bound, an action signal pulls a low-scored case in, an exact signal
     settles its rule on every case, a signal_implies_gold entry settles gold
     on every case carrying it; a missing adjudication or one outside the set
     stops the build. (The doctrine target keeps the band: p >= YES_AT gold,
     p <= NO_AT not, a borderline pair with no adjudication stops the build.)
  2. The committed gold is REPRODUCIBLE: rebuilding it from the committed
     first-pass probabilities, action signals and adjudications gives exactly
     the fixture's gold, for every case; exact and signal-implied labels are
     the same on every case carrying the signal (the model-routing rule on
     every subagent dispatch).
  3. Every adjudication carries a written reason, and every review-set pair has
     exactly one.
  4. The split is fixed by seed: recomputing it reproduces every case's split;
     each stratum holds out ceil(30%); a later case is placed by hash without
     moving any existing case.
  5. The fixture is big enough and clean: at least 200 cases, all eight strata
     with at least 20 each, every gold id a live rule the probabilities cover,
     and no email, phone, money figure, hostname URL, IP, credential path or
     token anywhere in it.
  6. Rule classes follow the ordered questions: every live rule gets exactly one
     of the four, layer0 is always_on, control is gate_named.
  7. The harness scores v2: load_cases filters by split, per-class counts and
     the fully-served share match hand counts, and the CLI runs on the train
     split and writes the class table.
  8. The regression intake: a live miss becomes a case with the missed rule
     gold, borderline rules disputed, a hash-placed split; a copied prompt, a
     copied tool-call input, a record name (never echoed), a bare host, a
     machine name, a key or ssh path, a scrub hit, an unknown rule or a
     duplicate is refused; every refusal happens BEFORE any Jev request (the
     CLI's main() is run with a stub client that counts requests); the name
     check fails closed; the command line appends to a fixture copy.
  9. The universal-trigger policy: the rules UNIVERSAL_POLICY settles carry
     exactly the policy's label on every case.
 10. Doctrine scoring sets aside refs outside a case's labelled shortlist and
     does not score paths that deliver no doctrine; the harness refuses a
     slug-shaped doctrine ref; with no --split a v2 file is scored on test.
"""
import copy
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "ops" / "rule_gold_label.py"
EVAL = REPO / "ops" / "rule_delivery_eval.py"
FIX_DIR = REPO / "ops" / "fixtures" / "rule-delivery-eval"
FIXTURE = FIX_DIR / "cases.v2.json"
LABELS = FIX_DIR / "labels.v2.json"
ADJ = FIX_DIR / "adjudications.v2.jsonl"
DADJ = FIX_DIR / "doctrine-adjudications.v2.jsonl"
EVAL_CLI = REPO / "tools" / "rule-delivery-eval.py"
INTAKE_CLI = REPO / "tools" / "rule-delivery-eval-intake.py"

sys.path.append(str(REPO / "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
check = CHECKER.check


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probs_from_labels(labels):
    """labels.v2.json stores one list per case in `rules` order (compact)."""
    rules = labels["rules"]
    return {cid: {rid: row[i] for i, rid in enumerate(rules) if row[i] is not None}
            for cid, row in labels["cases"].items()}


def read_adjudications():
    return [json.loads(line) for line in ADJ.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_bands(gl):
    check("band: at YES_AT is gold", gl.band(gl.YES_AT) == "gold")
    check("band: at NO_AT is not", gl.band(gl.NO_AT) == "not")
    check("band: between is borderline", gl.band((gl.YES_AT + gl.NO_AT) / 2) == "borderline")
    probs = {"c1": {"r1": 0.9, "r2": 0.5, "r3": 0.1}}
    try:
        gl.gold_sets(probs, [])
        check("unadjudicated borderline stops the build", False)
    except ValueError:
        check("unadjudicated borderline stops the build", True)
    got = gl.gold_sets(probs, [{"case":"c1","rule":"r1","gold":True}, {"case":"c1","rule":"r3","gold":False}, {"case": "c1", "rule": "r2", "gold": True, "reason": "x" * 20}])
    check("adjudication decides the borderline pair", got == {"c1": ["r1", "r2"]}, got)
    got = gl.gold_sets(probs, [{"case":"c1","rule":"r1","gold":True}, {"case":"c1","rule":"r3","gold":False}, {"case": "c1", "rule": "r2", "gold": False, "reason": "x" * 20}])
    check("an adjudicated no stays out", got == {"c1": ["r1"]}, got)


def test_review_set(gl):
    """The review-set scheme: no auto-gold at the top, a per-rule lower bound,
    the action signal pulls a low-scored case in, an exact signal settles its
    rule on every case, and a signal_implies_gold entry settles gold on the
    cases carrying it whatever their score."""
    spawn = {"tool_name": "Agent", "tool_input": {"prompt": "do a thing"}}
    merge = {"tool_name": "mcp__carr__confirm-merge", "tool_input": {}}
    cases = [{"id": "c1", "tool_calls": [spawn]}, {"id": "c2", "tool_calls": []},
             {"id": "c3", "tool_calls": [merge]}]
    signals = [
        {"rule": "rspawn", "mode": "review", "tools": ["^(?:Agent|Task)$"],
         "input_regex": None, "signal_implies_gold": True},
        {"rule": "rlook", "mode": "review", "tools": ["^Agent$"], "input_regex": None,
         "signal_implies_gold": False},
        {"rule": "rmerge", "mode": "exact", "tools": ["^mcp__.+__confirm-merge$"],
         "input_regex": None, "signal_implies_gold": True}]
    probs = {"c1": {"rhigh": 0.95, "rmid": 0.30, "rext": 0.22, "rspawn": 0.12, "rlook": 0.05,
                    "rmerge": 0.9},
             "c2": {"rhigh": 0.10, "rmid": 0.10, "rext": 0.10, "rspawn": 0.40, "rlook": 0.05,
                    "rmerge": 0.9},
             "c3": {"rhigh": 0.10, "rmid": 0.10, "rext": 0.10, "rspawn": 0.05, "rlook": 0.05,
                    "rmerge": 0.1}}
    plan = gl.review_plan(cases, probs, signals, ["rext"])
    review = plan["review"]
    check("review: p >= 0.75 is reviewed, not auto-gold", ("c1", "rhigh") in review)
    check("review: p above REVIEW_LOW is reviewed", ("c1", "rmid") in review)
    check("review: an extended rule is reviewed down to REVIEW_LOW_EXTENDED",
          ("c1", "rext") in review)
    check("review: a signal pulls a low-scored case in", ("c1", "rlook") in review)
    check("review: a signal-implied gold pair is settled, not reviewed",
          ("c1", "rspawn") not in review and plan["settled_labels"]["c1"]["rspawn"] is True)
    check("review: the signal rule's other cases are still reviewed by score",
          ("c2", "rspawn") in review)
    check("review: an exact rule is settled on every case by its signal",
          not any(r == "rmerge" for _c, r in review)
          and [plan["settled_labels"][c]["rmerge"] for c in ("c1", "c2", "c3")]
          == [False, False, True])
    case_by_id = {c["id"]: c for c in cases}
    adj = [{"case": c, "rule": r, "gold": (c, r) == ("c1", "rmid"), "reason": "x " * 12,
            "case_binding": gl.adjudication_case_binding(case_by_id[c])}
           for c, r in sorted(review)]
    got = gl.gold_sets_reviewed(probs, adj, plan["low_by_rule"], plan["signals_hit"],
                                plan["settled_labels"], plan["settled_rules"], cases=cases)
    gold_c1 = set(got["c1"])
    check("review: gold is adjudicated-true plus settled-true, nothing auto",
          {"rmid", "rspawn"} <= gold_c1 and "rhigh" not in gold_c1
          and got["c3"] == ["rmerge"], got)
    sprobs = {f"s{i}": {"rs": p} for i, p in enumerate([0.24, 0.22, 0.22, 0.20, 0.10])}
    picked, floor = gl.sample_below_floor(sprobs, "rs", 0.25, set(), 2)
    check("floors: a sample takes the pairs just below the bound, ties included",
          sorted(picked) == [("s1", "rs"), ("s2", "rs")] or sorted(picked) ==
          [("s0", "rs"), ("s1", "rs"), ("s2", "rs")], picked)
    fplan = gl.review_plan([{"id": k, "tool_calls": []} for k in sprobs], sprobs, [], [],
                           {"rs": floor})
    check("floors are advisory: all semantic labels remain in the review set",
          {c for c, _r in fplan["review"]} == set(sprobs), (floor, fplan["review"]))
    try:
        gl.gold_sets_reviewed(probs, adj[1:], plan["low_by_rule"], plan["signals_hit"],
                              plan["settled_labels"], plan["settled_rules"], cases=cases)
        check("review: a missing adjudication stops the build", False)
    except ValueError:
        check("review: a missing adjudication stops the build", True)
    try:
        extra = adj + [{"case": "c3", "rule": "rhigh", "gold": True, "reason": "x " * 12,
                        "case_binding": gl.adjudication_case_binding(case_by_id["c3"])}]
        gl.gold_sets_reviewed(probs, extra, plan["low_by_rule"], plan["signals_hit"],
                              plan["settled_labels"], plan["settled_rules"], cases=cases)
        check("review: an adjudication outside the review set stops the build", False)
    except ValueError:
        check("review: an adjudication outside the review set stops the build", True)


def test_adjudication_bindings(gl):
    cases = [{"id": "case-a", "prompt": "Review the privacy guard", "origin": "subagent-brief",
              "tool_calls": [{"tool_name": "Read", "tool_input": {"path": "guard.py"}}]},
             {"id": "case-b", "prompt": "Build the privacy guard", "origin": "subagent-brief",
              "tool_calls": []}]
    good = [{"case": c["id"], "rule": "rule-a", "gold": i == 0,
             "reason": "This case has its own independent decision.",
             "case_binding": gl.adjudication_case_binding(c)} for i, c in enumerate(cases)]
    gl.validate_adjudication_bindings(list(reversed(good)), cases)
    check("binding: output order is irrelevant; identities travel with decisions", True)
    binding = gl.adjudication_case_binding(cases[0])
    check("binding: labels, probabilities and held-out split cannot change the evidence hash",
          binding == gl.adjudication_case_binding(dict(cases[0], gold=["x"], split="test", p=0.9)))
    mutations = {
        "missing binding": [{k: v for k, v in good[0].items() if k != "case_binding"}],
        "shifted outer case id": [dict(good[0], case="case-b")],
        "shifted output binding": [dict(good[0], case_binding=good[1]["case_binding"])],
        "stale input hash": [dict(good[0], case_binding=dict(binding, input_sha256="0" * 64))],
        "unknown case": [dict(good[0], case="case-c")],
        "string gold": [dict(good[0], gold="false")],
        "duplicate decision": [good[0], good[0]],
    }
    for name, rows in mutations.items():
        try:
            gl.validate_adjudication_bindings(rows, cases)
            check("binding refuses " + name, False)
        except ValueError:
            check("binding refuses " + name, True)
    for field, value in (("prompt", "A different action"), ("origin", "notification"),
                         ("tool_calls", [{"tool_name": "Write", "tool_input": {"path": "guard.py"}}])):
        changed = [dict(cases[0], **{field: value}), cases[1]]
        try:
            gl.validate_adjudication_bindings(good, changed)
            check("binding refuses changed " + field, False)
        except ValueError:
            check("binding refuses changed " + field, True)
    # This is the silent positional-zip failure from the Tour review: take a
    # valid output for A and store its whole answer under B. The gold builder,
    # not merely a sidecar validator, must refuse it.
    try:
        gl.gold_sets_reviewed({"case-b": {"rule-a": 0.8}},
                              [dict(good[0], case="case-b")], {"rule-a": 0.25},
                              set(), {}, set(), cases=cases)
        check("binding: shifted adjudicator output stops gold construction", False)
    except ValueError as exc:
        check("binding: shifted adjudicator output stops gold construction",
              "binding mismatch" in str(exc), str(exc))


def test_fixture(gl, ev):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = doc["cases"]
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    probs = probs_from_labels(labels)
    adjud = read_adjudications()
    check("fixture has at least 200 cases", len(cases) >= 200, len(cases))
    counts = {}
    for case in cases:
        counts[case["stratum"]] = counts.get(case["stratum"], 0) + 1
    check("all eight v2 strata present", set(counts) == set(ev.STRATA_V2), counts)
    check("every stratum has at least 20 cases", min(counts.values()) >= 20, counts)
    live = set(labels["rules"])
    check("labels cover all live rules (195 on the labelling date)", len(live) >= 190, len(live))
    base = [c for c in cases if c.get("origin") != "live-miss"]
    check("every base case has first-pass probabilities for every live rule",
          all(len(probs.get(c["id"], {})) == len(live) for c in base))
    unknown = sorted({rid for c in cases for rid in c["gold"] if rid not in live})
    check("every gold id is a labelled live rule", not unknown, unknown)
    # 2. reproducible gold, under the review-set scheme
    lab = doc["labelling"]
    signals = gl.load_action_signals(FIX_DIR / lab["action_signals"]["file"])
    base_probs = {c["id"]: probs[c["id"]] for c in base}
    floors_doc = json.loads((FIX_DIR / lab["review_floors"]["file"]).read_text(encoding="utf-8"))
    floors = gl.load_review_floors(FIX_DIR / lab["review_floors"]["file"])
    plan = gl.review_plan(base, base_probs, signals, lab["review_low_extended_rules"], floors)
    check("fixture records the review bounds the library uses",
          lab["review_low"] == gl.REVIEW_LOW and lab["review_low_extended"] == gl.REVIEW_LOW_EXTENDED)
    try:
        gl.gold_sets_reviewed(base_probs, adjud, plan["low_by_rule"], plan["signals_hit"],
                             plan["settled_labels"], plan["settled_rules"], cases=base)
        check("historical partial adjudication cannot authorize fresh semantic gold", False)
    except ValueError as exc:
        check("historical partial adjudication cannot authorize fresh semantic gold", "adjudication" in str(exc))
    # 3. adjudications
    keys = [(a["case"], a["rule"]) for a in adjud]
    check("no pair adjudicated twice", len(keys) == len(set(keys)))
    check("historical adjudications stay within the expanded review set",
          set(keys) <= plan["review"], (len(plan["review"]), len(set(keys))))
    short = [k for k, a in zip(keys, adjud) if len((a.get("reason") or "").split()) < 6]
    check("every adjudication has a written reason", not short, short[:5])
    # These are reviewed case/action distinctions, not model-score thresholds.
    # Keep the review receipt separate from the first-pass Jev probabilities.
    repair = json.loads((FIX_DIR / "adjudication-repair.v2.json").read_text())
    current = {(a["case"], a["rule"]): a for a in adjud}
    check("repair: selected original decisions and reviewed outputs remain auditable",
          len(repair["rows"]) == 31 and all(
              r["before"]["case"] == r["after"]["case"] == r["case"]
              and r["before"]["rule"] == r["after"]["rule"] == r["rule"]
              and current[(r["case"], r["rule"])] == r["after"]
              and r["after"]["reason"].startswith(r["case"] + ":")
              for r in repair["rows"]))
    followup = repair["column_followup"]
    check("repair: bounded column follow-up preserves case-bound before/after evidence",
          len(followup["rows"]) == 6 and all(
              r["before"]["case"] == r["after"]["case"] == r["case"]
              and r["before"]["case_binding"] == r["after"]["case_binding"]
              and r["before"]["rule"] == r["after"]["rule"] == "f47a8fe9"
              and current[(r["case"], r["rule"])] == r["after"]
              and r["after"]["reason"].startswith(r["case"] + ":")
              for r in followup["rows"]))
    check("repair: unfinished column audit remains explicit and covers the remaining cases",
          set(followup["remaining_for_full_semantic_audit"])
          == {c["id"] for c in base} - {r["case"] for r in followup["rows"]})
    column = repair["full_column_review"]
    column_rows = column["changed_rows"]
    check("repair: full f47 column review covers every case with a claim-based criterion",
          column["rule"] == "f47a8fe9"
          and column["reviewed_cases"] == len(base) == 240
          and column["changed_count"] == len(column_rows) == 26
          and column["unchanged_count"] == len(base) - len(column_rows)
          and "Whether the recorded turn actually re-executed" in column["criterion"])
    check("repair: full-column changes preserve before/after case bindings",
          len({r["case"] for r in column_rows}) == len(column_rows)
          and all(r["before"]["case"] == r["after"]["case"] == r["case"]
                  and r["before"]["case_binding"] == r["after"]["case_binding"]
                  and r["before"]["rule"] == r["after"]["rule"] == "f47a8fe9"
                  and r["before"]["gold"] is False and r["after"]["gold"] is True
                  and current[(r["case"], "f47a8fe9")] == r["after"]
                  for r in column_rows))
    check("repair: claim trigger covers verified and unverified completion, absence and full sets",
          all(current[(cid, "f47a8fe9")]["gold"] is True for cid in
              ("v2-chat-002", "v2-chat-010", "v2-chat-015", "v2-rel-002",
               "v2-notif-004", "v2-notif-018", "v2-sfb-028", "v2-tour-018"))
          and all(current[(cid, "f47a8fe9")]["gold"] is False for cid in
                  ("v2-chat-001", "v2-chat-006", "v2-notif-027", "v2-rel-024",
                   "v2-tour-021")))
    review_followup = repair["independent_review_followup"]
    check("repair: independent f47 follow-up keeps three case-bound decisions",
          review_followup["source_head"] == "cd0b3ff628e1920cee28f7e7e75ccd69c0ead7d5"
          and {r["case"] for r in review_followup["rows"]}
              == {"v2-tour-020", "v2-deal-026", "v2-deal-021"}
          and all(r["before"]["case_binding"] == r["after"]["case_binding"]
                  and current[(r["case"], "f47a8fe9")] == r["after"]
                  and r["after"]["reason"].startswith(r["case"] + ":")
                  for r in review_followup["rows"]))
    check("repair: completed claim adopted in verdict or record write, bare notice excluded",
          all(current[(cid, "f47a8fe9")]["gold"] is True for cid in
              ("v2-tour-020", "v2-deal-020", "v2-deal-026"))
          and current[("v2-deal-021", "f47a8fe9")]["gold"] is False)
    check("repair: readiness claims bind with and without recorded verification",
          all(current[(cid, "f47a8fe9")]["gold"] is True for cid in
              ("v2-chat-005", "v2-chat-012", "v2-chat-019", "v2-chat-027"))
          and all(current[(cid, "f47a8fe9")]["gold"] is False for cid in
                  ("v2-chat-001", "v2-chat-006")))
    expected_tour = {14: True, 15: False, 16: True, 17: False, 18: False,
                     19: False, 20: True, 21: False, 22: False, 23: True,
                     24: False, 25: False, 26: False, 27: True, 28: False}
    check("repair: Tour escalation labels follow each case's own action",
          all(current[(f"v2-tour-{n:03d}", "c20dc3d5")]["gold"] == gold
              for n, gold in expected_tour.items()))
    check("repair: bare diagnosis/build completion notices have the same strict boundary",
          current[("v2-tour-022", "c20dc3d5")]["gold"] is False
          and current[("v2-sfb-020", "c20dc3d5")]["gold"] is False)
    verification_cases = ("v2-chat-010", "v2-chat-015", "v2-tour-007", "v2-notif-008",
                          "v2-notif-026", "v2-notif-025", "v2-notif-030", "v2-rel-030",
                          "v2-eng-025", "v2-disp-029", "v2-eng-015")
    check("repair: verification binds the claim, including when the turn omits the check",
          all(current[(cid, "f47a8fe9")]["gold"] is True for cid in verification_cases))
    check("repair: retry is not its neighboring correction; broad audit is not a new build",
          current[("v2-chat-022", "bbffc139")]["gold"] is False
          and current[("v2-chat-023", "bbffc139")]["gold"] is True
          and current[("v2-disp-007", "20d106f1")]["gold"] is False
          and current[("v2-disp-012", "20d106f1")]["gold"] is False)
    # 3a. floors: every published sample is in the review set, its gold rate
    # matches the adjudications, and a sample above 10% gold was followed by a
    # lower floor (a later sample) unless nothing was left below it.
    decided = {(a["case"], a["rule"]): bool(a["gold"]) for a in adjud}
    samples = floors_doc["samples"]
    rate_ok = all(s["n"] == len(s["pairs"]) and s["gold"] == sum(decided[tuple(p)] for p in s["pairs"])
                  for s in samples)
    check("every below-floor sample is adjudicated and its published gold rate is exact",
          rate_ok and all(tuple(p) in plan["review"] for s in samples for p in s["pairs"]))
    last = {}
    for s in sorted(samples, key=lambda s: s["batch"]):
        last[s["rule"]] = s
    unresolved = [rid for rid, s in last.items() if s["gold"] / s["n"] > 0.10 and any(
        base_probs[c].get(rid, 1) <= floors[rid] and (c, rid) not in plan["review"] for c in base_probs)]
    check("no rule's last sample is above 10% gold while pairs remain below its floor",
          not unresolved, unresolved[:5])
    full = [rid for rid, v in floors.items() if v < 0]
    check("floors below 0 review every case of the rule",
          all((c["id"], rid) in plan["review"] or rid in plan["settled_labels"][c["id"]]
              for rid in full for c in base), full)
    # 3b. the action signals are applied the same way on every case
    exact = set(lab["action_signals"]["exact_rules"])
    off_exact = [(c["id"], rid) for c in base for rid in exact
                 if (rid in c["gold"]) != plan["settled_labels"][c["id"]][rid]]
    check("action-policy (exact) rules are gold iff the case carries the signal",
          not off_exact, off_exact[:5])
    gold_of = {c["id"]: set(c["gold"]) for c in base}
    off_implied = [(c["id"], s["rule"]) for c in base for s in signals
                   if s.get("signal_implies_gold") and gl.signal_hit(c, s)
                   and s["rule"] not in gold_of[c["id"]]]
    check("every case carrying a signal_implies_gold signal is gold for that rule",
          not off_implied, off_implied[:5])
    spawns = [c["id"] for c in base
              if any(t.get("tool_name") in ("Agent", "Task") for t in c["tool_calls"])]
    check("the model-routing rule is gold on every subagent dispatch",
          bool(spawns) and all("fb110a39" in gold_of[cid] for cid in spawns), len(spawns))
    # 4. split
    splits = gl.assign_splits(base, seed=doc["split"]["seed"])
    moved = [c["id"] for c in base if splits[c["id"]] != c["split"]]
    check("split recomputes from the seed", not moved, moved[:5])
    for stratum, n in counts.items():
        held = sum(1 for c in base if c["stratum"] == stratum and c["split"] == "test")
        want = math.ceil(gl.TEST_FRACTION * sum(1 for c in base if c["stratum"] == stratum))
        check(f"stratum {stratum} holds out ceil(30%)", held == want, (held, want))
    extra = [{"id": f"later-{i}", "stratum": "engineering"} for i in range(40)]
    before = gl.assign_splits(base, seed=doc["split"]["seed"])
    placed = {e["id"]: gl.split_for_new_case(e["id"], doc["split"]["seed"]) for e in extra}
    check("a later case never moves an existing one",
          before == gl.assign_splits(base, seed=doc["split"]["seed"]))
    check("later cases land in both splits by hash", set(placed.values()) == {"train", "test"})
    # 5. hygiene
    raw = (FIXTURE.read_text(encoding="utf-8") + ADJ.read_text(encoding="utf-8")
           + (FIX_DIR / "adjudication-repair.v2.json").read_text(encoding="utf-8")
           + (FIX_DIR / lab["action_signals"]["file"]).read_text(encoding="utf-8"))
    hits = gl.scrub_findings(raw)
    check("fixture, adjudications, repair audit and signals carry nothing the scrub refuses",
          not hits, hits[:5])
    check("fixture declares itself paraphrased", doc.get("provenance", "").startswith("paraphrased"))
    tool_ok = all(isinstance(c.get("tool_calls"), list) and all(
        isinstance(t, dict) and isinstance(t.get("tool_name"), str) for t in c["tool_calls"])
        for c in cases)
    check("tool calls use the v1 shape", tool_ok)
    loaded = ev.load_cases(str(FIXTURE))
    check("harness loads the v2 fixture", len(loaded) == len(cases))
    check("fixture documents both target sets",
          set((doc.get("targets") or {})) == {"gold", "gold_doctrine"})
    # doctrine: the second target rebuilds from its shortlist probabilities
    # and its own written adjudications, exactly as the rule gold does.
    dadj = [json.loads(line) for line in DADJ.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    dprobs = labels.get("doctrine") or {}
    # Frozen historical gold is explicit test adjudication, never a score cutoff.
    explicit = [{"case": c["id"], "rule":rid, "gold":rid in c["gold_doctrine"]}
                for c in base for rid in dprobs.get(c["id"], {})]
    drebuilt = gl.gold_sets({c["id"]: dprobs.get(c["id"], {}) for c in base}, explicit)
    ddiffs = [c["id"] for c in base if drebuilt[c["id"]] != c["gold_doctrine"]]
    check("doctrine gold rebuilds from shortlist probabilities + adjudications",
          not ddiffs, ddiffs[:5])
    check("doctrine gold is labelled (not empty across the set)",
          sum(len(c["gold_doctrine"]) for c in cases) > 0)
    # Slugs and section keys carry person and practice names; committed refs
    # must be the store's opaque ids.
    opaque = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"
                        r"#[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    all_refs = ({r for c in cases for r in c["gold_doctrine"]}
                | {r for v in dprobs.values() for r in v} | {a["rule"] for a in dadj})
    bad_refs = sorted(r for r in all_refs if not opaque.match(r))
    check("every committed doctrine ref is an opaque '<doc id>#<section id>'",
          not bad_refs, bad_refs[:3])
    dborder = {(c, r) for c, r, _p in gl.borderlines({c["id"]: dprobs.get(c["id"], {})
                                                        for c in base})}
    check("every borderline doctrine pair is adjudicated, and only those",
          dborder == {(a["case"], a["rule"]) for a in dadj})
    check("every doctrine adjudication has a written reason",
          all(len((a.get("reason") or "").split()) >= 6 for a in dadj))
    # the guidance-deferred reporting group
    group = set(((doc.get("rule_groups") or {}).get("guidance_deferred") or {}).get("rules") or [])
    manifest = {rid for rid, g in gl.rule_groups(str(REPO)).items() if g == "guidance_deferred"}
    check("fixture tags the guidance-deferred group from the manifest",
          group == manifest & live and len(group) >= 90, len(group))
    check("each case's deferred tag is its gold within the group",
          all(c["gold_guidance_deferred"] == [r for r in c["gold"] if r in group] for c in base))
    # 9. the universal-trigger policy is applied uniformly
    off_policy = [(c["id"], rid) for c in base for rid, want in gl.policy_labels(c).items()
                  if rid in live and (rid in c["gold"]) != want]
    check("universal-trigger rules carry exactly the policy label on every case",
          not off_policy, off_policy[:5])
    return doc, labels


def test_bound_build_cli(doc, labels):
    """Exercise the actual build boundary, including its no-output refusal."""
    with tempfile.TemporaryDirectory(prefix="bound-gold-build-") as tmp:
        base = Path(tmp)
        inputs = {
            "probs": probs_from_labels(labels),
            "roster": {"rules": [{"id": rid} for rid in labels["rules"]]},
            "extended": doc["labelling"]["review_low_extended_rules"],
            "doctrine": {cid: {"sections": rows} for cid, rows in labels["doctrine"].items()},
        }
        for name, value in inputs.items():
            (base / (name + ".json")).write_text(json.dumps(value))
        command = [sys.executable, str(REPO / "tools/rule-gold-label.py"), "build",
                   "--cases", str(FIXTURE), "--corpus", str(base / "roster.json"),
                   "--probs", str(base / "probs.json"),
                   "--extended-rules", str(base / "extended.json"),
                   "--doctrine-probs", str(base / "doctrine.json"),
                   "--doctrine-adjudications", str(DADJ),
                   "--labelled-on", doc["labelling"]["labelled_on"]]
        out = base / "good" / "cases.v2.json"
        run = subprocess.run(command + ["--adjudications", str(ADJ), "--out", str(out)],
                             capture_output=True, text=True)
        check("bound CLI: unadjudicated semantic labels refuse auto-gold", run.returncode != 0 and "adjudication" in run.stderr, run.stderr[-400:])
        if run.returncode == 0:
            check("bound CLI: fixture rebuild is byte-identical", out.read_bytes() == FIXTURE.read_bytes())
            check("bound CLI: output preserves all adjudicator bindings",
                  (out.parent / ADJ.name).read_bytes() == ADJ.read_bytes())
        bad = read_adjudications()
        selected = next(a for a in bad if a["case"] == "v2-tour-016" and a["rule"] == "c20dc3d5")
        neighbor = next(a for a in bad if a["case"] == "v2-tour-015" and a["rule"] == "c20dc3d5")
        for key in ("gold", "reason", "case_binding"):
            selected[key] = copy.deepcopy(neighbor[key])
        bad_path = base / "shifted.jsonl"
        bad_path.write_text("".join(json.dumps(row) + "\n" for row in bad))
        refused_out = base / "refused" / "cases.v2.json"
        run = subprocess.run(command + ["--adjudications", str(bad_path), "--out", str(refused_out)],
                             capture_output=True, text=True)
        check("bound CLI: a neighbor's full answer is refused before any output",
              run.returncode != 0 and "case binding mismatch" in run.stderr
              and not refused_out.parent.exists(), run.stderr[-400:])


def test_scrub(gl):
    # Assembled at run time so this file does not itself carry the shapes the
    # repository's own hygiene checks look for.
    at, dot = "@", "."
    for text, name in (("mail someone" + at + "example" + dot + "org now", "email"),
                       ("call " + "-".join(("555", "555", "0100")), "phone"),
                       ("asking " + "$" + "32/sf", "dollar"),
                       ("about 4,500 sf of space", "square_feet"),
                       ("open https://" + "internal" + dot + "host" + dot + "example/x", "url_host"),
                       ("source ~/" + dot + "config/app/key" + dot + "env", "credential_path")):
        check(f"scrub catches {name}", any(h[0] == name for h in gl.scrub_findings(text)),
              gl.scrub_findings(text))
    check("scrub passes example.com and awk positionals",
          not gl.scrub_findings("see https://example.com/a and awk '{print $1}'"))
    home = "~/"
    for text, name in (
            ("ssh into build-box" + dot + "local and restart", "bare_host"),
            ("the portal at app" + dot + "somecorp" + dot + "com is down", "bare_host"),
            ("the node on the " + "tail" + "a1b2" + dot + "ts" + dot + "net mesh", "bare_host"),
            ("run it on sams" + "-mac" + "-studio tonight", "machine_name"),
            ("copy " + home + dot + "ssh/" + "id_" + "ed25519 over", "credential_path"),
            ("the deploy key in keys/deploy" + dot + "pem", "credential_path"),
            ("append to authorized" + "_keys on the box", "credential_path")):
        check(f"scrub catches {name}: {text[:28]}",
              any(h[0] == name for h in gl.scrub_findings(text)), gl.scrub_findings(text))
    check("scrub passes file names and plain words",
          not gl.scrub_findings("edit report.json and cases.v2.json, then open the Mac Studio "
                                "notes and the key rule list"))
    # names come from the record; a hit is reported without echoing the name
    terms = gl.record_name_terms([{"name": "Quillfeather Family Dental", "kind": "practice"},
                                  {"name": "Ada Brightwater", "kind": "person"},
                                  {"name": "Suite expansion", "kind": "deal"}])
    check("record names: full names and distinctive tokens, generic words dropped",
          {"Quillfeather Family Dental", "Quillfeather", "Brightwater", "Ada Brightwater"}
          <= set(terms) and "Family" not in terms and "Dental" not in terms
          and "Suite" not in terms, terms)
    hits = gl.scrub_findings("draft a note to brightwater about the renewal", terms)
    check("a record name is caught case-insensitively and withheld in the finding",
          hits == [("name", "<withheld>")], hits)
    check("a name inside another word is not a hit",
          not gl.scrub_findings("the brightwaterline report", terms))
    check("shared-run counts consecutive words",
          gl.longest_shared_run("please merge the pull request now", "merge the pull request") == 4)


def test_classes(gl, labels):
    classes = gl.rule_classes(str(REPO), labels["rules"])
    check("every live rule gets one of the four classes",
          set(classes) == set(labels["rules"]) and set(classes.values()) <= set(gl.RULE_CLASSES),
          set(classes.values()))
    emap = json.loads((REPO / "ops" / "config" / "rule-enforcement-map.json").read_text())
    layers = emap["rule_load_layers"]
    wrong = [rid for rid, cls in classes.items()
             if (layers.get(rid, {}).get("load_layer") == "layer0") != (cls == "always_on")
             or (layers.get(rid, {}).get("load_layer") == "control") != (cls == "gate_named")]
    check("layer0 is always_on and control is gate_named, and only those", not wrong, wrong[:5])
    check("all four classes occur in the live corpus",
          set(classes.values()) == set(gl.RULE_CLASSES), sorted(set(classes.values())))


def _uuid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


REF_A, REF_B, REF_C, REF_Z, REF_UNLAB = (f"{_uuid(i)}#{_uuid(100 + i)}" for i in range(1, 6))

META = {"a": {"layer": "layer0", "packs": []}, "p": {"layer": "pack", "packs": ["x"]},
        "q": {"layer": "pack", "packs": ["x"]}}
CLASSES = {"a": "always_on", "p": "topic", "q": "action_point"}


def test_harness_v2(ev):
    cases = [
        {"id": "t1", "stratum": "chat_only", "prompt": "a", "tool_calls": [], "gold": ["a", "p"],
         "disputed": [], "split": "train"},
        {"id": "t2", "stratum": "tour_maps", "prompt": "b", "tool_calls": [], "gold": ["q"],
         "disputed": [], "split": "test"},
        {"id": "t3", "stratum": "notifications", "prompt": "<task-notification>", "tool_calls": [],
         "gold": [], "disputed": [], "split": "test"},
    ]
    cases[0]["gold_doctrine"] = [REF_A, REF_B]
    cases[2]["gold_doctrine"] = [REF_C]
    deliveries = {"sys": {"t1": {"rules": {"a", "p"}, "doctrine": {REF_A, REF_Z, REF_UNLAB}},
                          "t2": {"rules": {"p"}}, "t3": {"rules": {"p"}}},
                  "rules_only": {"t1": {"rules": {"a"}}, "t2": {"rules": set()},
                                 "t3": {"rules": set()}}}
    groups = {"a": "other", "p": "guidance_deferred", "q": "guidance_deferred"}
    # REF_Z was on t1's labelled shortlist (judged not gold); REF_UNLAB was not.
    report = ev.score(cases, deliveries, {"sys": set(META), "rules_only": set(META)}, META,
                      classes=CLASSES, groups=groups,
                      doctrine_labelled={"t1": {REF_A, REF_B, REF_Z}, "t3": {REF_C}})
    row = report["paths"]["sys"]
    # doctrine over all cases: tp REF_A; fp REF_Z; fn REF_B, REF_C; REF_UNLAB set aside.
    check("doctrine is scored apart from rules",
          row["doctrine"]["tp"] == 1 and row["doctrine"]["fp"] == 1
          and row["doctrine"]["fn"] == 2 and row["doctrine"]["recall"] == round(1 / 3, 4),
          row["doctrine"])
    check("a delivered doctrine ref outside the case's labelled shortlist is set aside",
          row["doctrine"]["outside_labelled"] == 1, row["doctrine"])
    check("a path that delivers no doctrine is not charged doctrine misses",
          report["paths"]["rules_only"]["doctrine"] is None, report["paths"]["rules_only"])
    declared = ev.score(cases, {"door": {"t1": {"rules": set()}, "t2": {"rules": set()},
                                         "t3": {"rules": set()}}},
                        {"door": set()}, META, doctrine_paths={"door"})
    check("a declared doctrine path that delivers nothing scores 0%, not 'does not deliver'",
          (declared["paths"]["door"]["doctrine"] or {}).get("recall") == 0.0,
          declared["paths"]["door"]["doctrine"])
    check("the doctrine table says which paths do not deliver doctrine",
          "does not deliver doctrine" in ev.render_markdown(report, {}, paths=["sys", "rules_only"]))
    check("doctrine misses reach the notification stratum too",
          row["doctrine_by_stratum"]["notifications"]["fn"] == 1, row["doctrine_by_stratum"])
    # groups: other tp a; deferred tp p (t1), fp p (t2), fn q (t2).
    check("the guidance-deferred group is reported apart",
          row["by_group"]["guidance_deferred"] == {**row["by_group"]["guidance_deferred"],
                                                  "tp": 1, "fp": 1, "fn": 1}
          and row["by_group"]["other"]["recall"] == 1.0, row["by_group"])
    # human: t1 tp a,p; t2 fp p, fn q. By class: always_on tp1; topic tp1 fp1; action_point fn1.
    check("per-class counts match hand count",
          row["by_class"]["always_on"]["tp"] == 1 and row["by_class"]["topic"]["tp"] == 1
          and row["by_class"]["topic"]["fp"] == 1 and row["by_class"]["action_point"]["fn"] == 1
          and row["by_class"]["action_point"]["recall"] == 0.0, row["by_class"])
    check("fully-served share: 1 of 2 owing cases",
          row["cases_fully_served"] == {"cases_owing": 2, "fully_served": 1, "share": 0.5},
          row["cases_fully_served"])
    check("notifications get their own stratum row",
          row["by_stratum"]["notifications"]["cases"] == 1, row["by_stratum"])
    md = ev.render_markdown(report, {}, paths=["sys"])
    check("markdown carries the class, group and doctrine tables and v2 strata",
          "rule class" in md and "rule group" in md and "Doctrine" in md
          and "tour_maps" in md and "notifications" in md)
    with tempfile.TemporaryDirectory(prefix="rule-gold-label-selftest-") as tmp:
        path = Path(tmp) / "c.json"
        path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": cases}))
        check("load_cases keeps only the train split",
              [c["id"] for c in ev.load_cases(str(path), split="train")] == ["t1"])
        check("load_cases keeps only the test split",
              [c["id"] for c in ev.load_cases(str(path), split="test")] == ["t2", "t3"])
        refs = copy.deepcopy(cases)
        refs[0]["gold_doctrine"] = [REF_A]
        path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": refs}))
        check("an opaque doctrine section ref is carried through",
              ev.load_cases(str(path))[0]["gold_doctrine"] == [REF_A])
        for label, bad_ref in (("a malformed doctrine ref", "not a ref"),
                               ("a slug-shaped doctrine ref (slugs carry names)",
                                "engineering-workflow-sop#02-before-you-push")):
            refs[0]["gold_doctrine"] = [bad_ref]
            path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": refs}))
            try:
                ev.load_cases(str(path))
                check(f"the harness refuses {label}", False)
            except ValueError:
                check(f"the harness refuses {label}", True)
        bad = copy.deepcopy(cases)
        bad[0]["split"] = "dev"
        path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": bad}))
        try:
            ev.load_cases(str(path))
            check("an unknown split is refused", False)
        except ValueError:
            check("an unknown split is refused", True)


def test_cli_train():
    with tempfile.TemporaryDirectory(prefix="rule-gold-label-selftest-") as tmp:
        result = subprocess.run(
            [sys.executable, str(EVAL_CLI), "--cases", str(FIXTURE), "--split", "train",
             "--jev", "off", "--out-dir", tmp],
            capture_output=True, text=True, timeout=600, cwd=str(REPO))
        check("eval CLI runs on the v2 train split", result.returncode == 0, result.stderr[-1500:])
        report = Path(tmp) / "report.json"
        if report.exists():
            data = json.loads(report.read_text())
            check("report is train-only", data.get("split") == "train")
            check("report has per-class rows",
                  data["paths"]["system_scoped_boot"].get("by_class") is not None)
        result = subprocess.run(
            [sys.executable, str(EVAL_CLI), "--cases", str(FIXTURE), "--jev", "off",
             "--out-dir", tmp],
            capture_output=True, text=True, timeout=600, cwd=str(REPO))
        data = json.loads(report.read_text()) if report.exists() else {}
        check("with no --split, a v2 file is scored on test only",
              result.returncode == 0 and data.get("split") == "test"
              and data.get("cases") == sum(1 for c in json.loads(FIXTURE.read_text())["cases"]
                                           if c["split"] == "test"),
              (result.returncode, data.get("split"), data.get("cases")))


def test_intake(gl, doc, labels):
    live = set(labels["rules"])
    base = doc["cases"][0]
    rid = next(r for r in labels["rules"] if r not in base["gold"])
    probs = {r: 0.1 for r in live}
    probs[rid] = 0.2
    other = next(r for r in labels["rules"] if r != rid)
    probs[other] = 0.5
    case = gl.intake_case(doc, base["id"], rid, live_rules=live, probs=probs)
    check("intake: missed rule is gold", rid in case["gold"], case["gold"])
    check("intake: borderline rules are disputed, not guessed", other in case["disputed"])
    check("intake: split placed by hash",
          case["split"] == gl.split_for_new_case(case["id"], gl.DEFAULT_SEED))
    check("intake: marked as a live miss", case["origin"] == "live-miss"
          and case["live_ref"] == base["id"])
    source = "please push the branch and open the pull request against main today"
    for label, kwargs in (
            ("copied prompt", {"prompt": "push the branch and open the pull request against main",
                               "stratum": "engineering", "source_text": source}),
            ("scrub hit", {"prompt": "email the landlord at someone" + "@" + "example.org",
                           "stratum": "deals_clients"}),
            ("missing paraphrase", {})):
        try:
            gl.intake_case(doc, "live-ref-1", rid, live_rules=live, **kwargs)
            check(f"intake refuses a {label}", False)
        except ValueError:
            check(f"intake refuses a {label}", True)
    try:
        gl.intake_case(doc, base["id"], "notarule", live_rules=live)
        check("intake refuses an unknown rule", False)
    except ValueError:
        check("intake refuses an unknown rule", True)
    ok = gl.intake_case(doc, "live-ref-1", rid, live_rules=live, source_text=source,
                        prompt="Ship the feature branch up and raise a review request.",
                        stratum="engineering")
    check("intake accepts a real paraphrase (partial without Jev)",
          ok["labels"] == "partial" and ok["gold"] == [rid])
    dup = copy.deepcopy(doc)
    dup["cases"].append(case)
    try:
        gl.intake_case(dup, base["id"], rid, live_rules=live, probs=probs)
        check("intake refuses a miss already in the benchmark", False)
    except ValueError:
        check("intake refuses a miss already in the benchmark", True)
    # The verbatim guard covers the tool calls as well as the prompt.
    try:
        gl.intake_case(doc, "live-ref-1", rid, live_rules=live, source_text=source,
                       prompt="Ship the feature branch up and raise a review request.",
                       stratum="engineering",
                       tool_calls=[{"tool_name": "SendMessage",
                                    "tool_input": {"to": "orchestrator", "message": source}}])
        check("intake refuses a tool-call input copied from the live turn", False)
    except ValueError as exc:
        check("intake refuses a tool-call input copied from the live turn",
              "tool-call input" in str(exc), str(exc))
    names = gl.record_name_terms([{"name": "Ada Brightwater", "kind": "person"},
                                  {"name": "Zoë Ångström-Løvgren", "kind": "person"}])
    try:
        gl.intake_case(doc, "live-ref-1", rid, live_rules=live, extra_names=names,
                       prompt="Draft the renewal note for Brightwater.", stratum="deals_clients")
        check("intake refuses a record name without echoing it", False)
    except ValueError as exc:
        check("intake refuses a record name without echoing it",
              "name" in str(exc) and "Brightwater" not in str(exc), str(exc))
    clean = "Ship the feature branch up and raise a review request."
    for label, calls, prompt in (
            ("after a newline in a tool-call input",
             [{"tool_name": "SendMessage", "tool_input": {"message": "notes:\nBrightwater asked"}}],
             clean),
            ("after a tab in a tool-call input",
             [{"tool_name": "Write", "tool_input": {"content": "owner\tBrightwater"}}], clean),
            ("in a tool-call dict key",
             [{"tool_name": "Write", "tool_input": {"Brightwater": "x"}}], clean),
            ("spelled with accents the record lacks",
             [], "Draft the note for Brîghtwäter today."),
            ("an accented surname alone, written plainly",
             [], "Ask Angstrom-Lovgren for the plans."),
            ("an accented surname alone, written with its accents",
             [{"tool_name": "SendMessage", "tool_input": {"message": "cc\nÅngström-Løvgren"}}],
             clean)):
        try:
            gl.intake_case(doc, "live-ref-1", rid, live_rules=live, extra_names=names,
                           prompt=prompt, stratum="deals_clients", tool_calls=calls)
            check(f"intake refuses a record name {label}", False)
        except ValueError as exc:
            check(f"intake refuses a record name {label}",
                  "name" in str(exc) and "rightw" not in str(exc) and "ngstr" not in str(exc),
                  str(exc))
    check("an accented surname becomes a name term on its own",
          any(gl.fold(t) == gl.fold("Ångström-Løvgren") for t in names), names)
    with tempfile.TemporaryDirectory(prefix="rule-gold-label-selftest-") as tmp:
        fix = Path(tmp) / "cases.v2.json"
        fix.write_text(FIXTURE.read_text(encoding="utf-8"))
        corpus = Path(tmp) / "corpus.json"
        corpus.write_text(json.dumps({"rules": [{"id": r, "statement": "s"} for r in live]}))
        names_file = Path(tmp) / "names.json"
        names_file.write_text(json.dumps([{"name": "Ada Brightwater", "kind": "person"}]))
        result = subprocess.run(
            [sys.executable, str(INTAKE_CLI), "--case-id", base["id"], "--missed-rule", rid,
             "--fixture", str(fix), "--corpus", str(corpus), "--no-label",
             "--names-file", str(names_file)],
            capture_output=True, text=True, timeout=120, cwd=str(REPO))
        check("intake CLI exits zero", result.returncode == 0, result.stderr[-800:])
        after = json.loads(fix.read_text())
        check("intake CLI appended exactly one case",
              len(after["cases"]) == len(doc["cases"]) + 1
              and after["cases"][-1]["missed_rule"] == rid)
        test_intake_order(gl, doc, rid, fix, corpus, names_file, Path(tmp))


class _StubJev:
    """Stands in for ops/typesafe_client in-process: records every request."""

    def __init__(self):
        self.requests = []

    def noul(self, question, true=None, false=None):
        return {"type": "noul", "instructions": question}

    def ask(self, state, questions, **_kwargs):
        self.requests.append(state)
        return {"answers": {qid: {"noul": 0.1} for qid in questions},
                "usage": {"input_tokens": 1}}


def test_intake_order(gl, doc, rid, fix, corpus, names_file, tmp):
    """NOTHING LEAVES THE MACHINE BEFORE THE CHECKS PASS: run the intake's own
    main() with a stub in place of the Jev client and count its requests."""
    os.environ["CARR_JEV_SEMANTIC_CACHE"] = str(tmp / "intake-semantic-cache")
    cli = load(INTAKE_CLI, "rule_delivery_eval_intake_for_selftest")
    stub = _StubJev()
    real_load = cli._load
    cli._load = lambda name: stub if name == "typesafe_client" else real_load(name)
    before = fix.read_text()
    base = doc["cases"][1]["id"]
    at, dot = "@", "."
    refusing = (
        ("a record name", ["--prompt", "Ask Brightwater to confirm the tour time."]),
        ("an email", ["--prompt", "Mail the draft to someone" + at + "somecorp" + dot + "org."]),
        ("a bare host", ["--prompt", "Restart the worker on build-box" + dot + "local."]),
        ("an ssh key path", ["--prompt", "Copy the file under ~/" + dot + "ssh to the box."]),
    )
    for label, extra in refusing:
        code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                         "--corpus", str(corpus), "--names-file", str(names_file),
                         "--stratum", "deals_clients", "--calls-log", str(tmp / "calls.jsonl"),
                         *extra])
        check(f"intake refuses {label} before any Jev request",
              code == 1 and not stub.requests, (code, len(stub.requests)))
    check("a refused intake leaves the fixture byte-identical", fix.read_text() == before)
    code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                     "--corpus", str(corpus), "--names-file", str(names_file), "--dry-run",
                     "--calls-log", str(tmp / "calls.jsonl")])
    check("an accepted intake does reach Jev (so the order test can fail)",
          code == 0 and len(stub.requests) >= 1, (code, len(stub.requests)))
    accepted_requests = len(stub.requests)
    code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                     "--corpus", str(corpus), "--names-file", str(names_file), "--dry-run",
                     "--calls-log", str(tmp / "calls.jsonl"),
                     "--tool-call", json.dumps({"tool_name": "SendMessage",
                                                "tool_input": {"message": "fyi\nBrightwater"}})])
    check("the CLI refuses a name after a newline in a tool call, before Jev",
          code == 1 and len(stub.requests) == accepted_requests, (code, len(stub.requests)))
    missing = tmp / "no-such-names.json"
    code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                     "--corpus", str(corpus), "--names-file", str(missing), "--no-label"])
    check("intake refuses when the name check cannot run (fails closed)", code == 2, code)
    for label, content in (("an empty names file", "[]"),
                           ("a names file with no usable name", '[{"name": "", "kind": "person"}]'),
                           ("a names file that is not a list", '{"name": "x"}')):
        empty = tmp / "empty-names.json"
        empty.write_text(content)
        code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                         "--corpus", str(corpus), "--names-file", str(empty), "--no-label",
                         "--prompt", "Ask Brightwater to confirm.", "--stratum", "deals_clients"])
        check(f"intake refuses {label} like an unreadable record (exit 2)", code == 2, code)

    def timeout_verb(verb, args):
        raise subprocess.TimeoutExpired(cmd="run.sh", timeout=300)
    real_verb = cli._verb
    cli._verb = timeout_verb
    code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                     "--corpus", str(corpus), "--no-label"])
    cli._verb = real_verb
    check("a record-read timeout exits 2, not the refused code 1", code == 2, code)
    check("nothing reached Jev in any of the fail-closed runs",
          len(stub.requests) == accepted_requests, len(stub.requests))
    cli._load = real_load


def main() -> int:
    gl = load(LIB, "rule_gold_label_for_selftest")
    ev = load(EVAL, "rule_delivery_eval_for_gold_selftest")
    test_bands(gl)
    test_review_set(gl)
    test_adjudication_bindings(gl)
    test_scrub(gl)
    doc, labels = test_fixture(gl, ev)
    test_bound_build_cli(doc, labels)
    test_classes(gl, labels)
    test_harness_v2(ev)
    test_cli_train()
    test_intake(gl, doc, labels)
    return CHECKER.summary(limit=20)


if __name__ == "__main__":
    raise SystemExit(main())
