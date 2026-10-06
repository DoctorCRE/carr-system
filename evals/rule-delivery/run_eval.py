#!/usr/bin/env python3
"""run_eval.py — replay events through CARR's deterministic rule-delivery layer.

The system under test is the trigger/pack layer with the Jev judgment OFF:

  prompt event  ops/rule_trigger_delivery.advise (compiled prompt_regex rows
                plus always-on; rank/ask stubbed to raise, so no Jev call),
                then the UserPromptSubmit hook's own pack-layer filter
                (lib/rule_delivery_preuse.semantic_delivery);
  tool event    hooks/rule-pack-preuse-reselection.py's own decision code:
                the scheduled rail (_matches), the route rail
                (routed_rule_ids over ops/config/rule-routes.v1.json) and the
                compiled-table rail (matched_triggers), unioned as process()
                unions them, deduped per (rule, tool) inside one case as
                lib/rule_routes.fresh_ids dedupes per session.

It calls the production functions; it does not re-implement them. It writes
nothing to production logs or caches (log_path=os.devnull, no session id).

GRADER (programmatic, expected rule ids per case):
  expected  = gold - disputed - boot-delivered rules, restricted to rules the
              trigger layer owes (a trigger/path route, or pack-layer member).
              Frozen per case in expectations.v1.json (--freeze-expectations)
              and graded from there, so a change to the routes cannot move its
              own denominator; a drift from the live derivation needs a new
              expectations version.
  recall    = |delivered & expected| / |expected|, per case and micro.
  false     = delivered rules outside the case's full gold set (and not
              disputed). A should-not-fire case has no expected rule.
  tokens    = estimated context tokens of the receipts the events would inject
              (render replicated offline; chars / 4, no offline tokenizer).

RECEIPT EVIDENCE: --observe writes one raw observation per case (what was
delivered, no grades); score_receipt() grades a baseline and a candidate
observation cohort against the frozen expectations and is the scorer
ops/check-eval-receipt.py re-runs. make_report.py produces both.

Usage:
  run_eval.py --variant baseline --split train
  run_eval.py --variant v1 --split all
  run_eval.py --compare baseline v1 --split test
  run_eval.py --observe OUT.jsonl [--trace-reads READS.json]
  run_eval.py --freeze-expectations
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

V2_CASES = os.path.join(REPO, "ops", "fixtures", "rule-delivery-eval", "cases.v2.json")
HARD_CASES = os.path.join(HERE, "hard_cases.v1.json")
EXPECTATIONS = os.path.join(HERE, "expectations.v1.json")
EXPECTATIONS_VERSION = "rule-delivery-expectations/v1"
RUNS = os.path.join(HERE, "runs")
CHARS_PER_TOKEN = 4.0
METRIC_IDS = ("recall", "false_deliveries", "tokens")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------- cases

def input_sha256(prompt, tool_calls):
    """What a case feeds the system under test, checkout-independent ({{REPO}} unexpanded)."""
    payload = json.dumps({"prompt": prompt, "tool_calls": tool_calls}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hard_split(case_id):
    """Deterministic 50/50 split for the hand-written cases, fixed by id."""
    digest = hashlib.sha256(("rule-delivery-hard-v1:" + case_id).encode()).digest()
    return "test" if digest[0] % 2 else "train"


def load_cases(split=None):
    """Every case as {id, source, stratum, split, prompt, tool_calls, gold,
    disputed, kind, note}. v2 cases keep the split the repo fixed on
    2026-09-27 (ops/rule_gold_label.assign_splits); hard cases are split by
    hash of their id above."""
    ev = _load(os.path.join(REPO, "ops", "rule_delivery_eval.py"), "rde_cases")
    cases = []
    for case in ev.load_cases(V2_CASES):
        cases.append({"id": case["id"], "source": "v2", "stratum": case["stratum"],
                      "split": case["split"], "prompt": case["prompt"],
                      "tool_calls": case["tool_calls"], "gold": case["gold"],
                      "disputed": case["disputed"], "kind": "trace", "note": "",
                      "input_sha256": input_sha256(case["prompt"], case["tool_calls"])})
    with open(HARD_CASES, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    for row in doc["cases"]:
        cases.append({"id": row["id"], "source": "hard", "stratum": row["kind"],
                      "split": hard_split(row["id"]), "prompt": row.get("prompt", ""),
                      "tool_calls": row.get("tool_calls", []),
                      "required": sorted(row["required"]),
                      "gold": sorted(set(row["required"]) | set(row["acceptable"])),
                      "disputed": [],
                      "kind": row["kind"], "note": row["why"],
                      "input_sha256": input_sha256(row.get("prompt", ""), row.get("tool_calls", []))})
    cases.extend(replay_cases())
    return [c for c in cases if split in (None, "all", c["split"])]


READ_ONLY_HEAD = re.compile(
    r"^(?:ls|cat|head|tail|wc|grep|rg|pwd|date|which|stat|file|echo|cd|"
    r"sed -n|git (?:status|log|diff|show|rev-parse|--version|branch --show-current|"
    r"branch --list)|find(?!.*(?:-delete|-exec)))\b")
MUTATING = re.compile(r"[>]|\b(?:rm|mv|cp|push|merge|commit|checkout|reset|curl|tee|kill|"
                      r"install|mkdir|touch|chmod)\b|git branch -[dD]")


def read_only_command(command):
    """True when every &&/;/| segment of a recorded Bash command is a plain read."""
    segments = [s.strip() for s in re.split(r"&&|;|\|\|?|\n", command) if s.strip()]
    return bool(segments) and not MUTATING.search(command) and all(
        READ_ONLY_HEAD.match(s) for s in segments)


def replay_cases():
    """Recorded tool calls from ops/fixtures/real-replay that are routine reads.

    These fixture reads target routine source/history paths. Their quiet labels
    apply to those paths only; a read of a CARR surface can bind review rules
    and is checked separately in selftest.py. The fixtures carry no rule labels."""
    out = []
    base = os.path.join(REPO, "ops", "fixtures", "real-replay")

    def fix(value):
        if isinstance(value, str):
            return value.replace("{{REPO}}", REPO)
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value
    for fname, keep in (("read-calls.jsonl", lambda row: True),
                        ("bash-commands.jsonl",
                         lambda row: read_only_command(row["tool_input"].get("command", "")))):
        with open(os.path.join(base, fname), "r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if not keep(row):
                    continue
                case_id = f"rr-{fname.split('-')[0]}-{row['id']}"
                raw = [{"tool_name": row["tool_name"], "tool_input": row["tool_input"]}]
                out.append({"id": case_id, "source": "real-replay", "stratum": "routine_read",
                            "split": hard_split(case_id), "prompt": "",
                            "input_sha256": input_sha256("", raw),
                            "tool_calls": [{"tool_name": row["tool_name"],
                                            "tool_input": fix(row["tool_input"])}],
                            "gold": [], "disputed": [], "required": [], "kind": "should_not_fire",
                            "note": "Recorded routine read: inspecting files or history binds no taught rule."})
    return out


# ----------------------------------------------------------------- world

class World:
    """Everything the replay reads, loaded once from the working tree.

    Grading labels come from the frozen expectations when given (a case they
    label is graded by them and nothing else); derive() is the live labelling
    they were frozen from."""

    def __init__(self, expectations=None):
        from lib import rule_delivery_preuse as preuse
        from lib import rule_routes
        self.preuse, self.rule_routes = preuse, rule_routes
        ev = _load(os.path.join(REPO, "ops", "rule_delivery_eval.py"), "rde_world")
        self.ev = ev
        self.rtd = ev._quiet_rule_trigger_delivery(REPO, "eval")
        self.hook = _load(os.path.join(REPO, "hooks", "rule-pack-preuse-reselection.py"),
                          "preuse_hook_world")
        with open(os.path.join(REPO, "ops", "config", "rule-selection-corpus.v1.json"),
                  "r", encoding="utf-8") as handle:
            self.statements = {r["id"]: r.get("statement") or ""
                               for r in json.load(handle)["rules"]}
        meta = ev.rule_meta(REPO)
        self.layer = {rid: row["layer"] for rid, row in meta.items()}
        with open(os.path.join(REPO, "ops/config/rule-classes.v1.json"), encoding="utf-8") as handle:
            classes = json.load(handle)["rules"]
        self.frozen_jit_boot_ids = {rid for rid, row in classes.items() if row["always_on"]
                     and row.get("personal_to") in (None, "joe")} | {
            rid for rid, row in meta.items() if row["layer"] == "layer0"}
        routes = self.rule_routes.load_routes(__import__("pathlib").Path(REPO))["rules"]
        owed = set()
        for rid, entry in routes.items():
            if any(route.get("kind") in ("trigger", "path_rule") for route in entry["routes"]):
                owed.add(rid)
        owed |= {rid for rid, layer in self.layer.items() if layer == "pack"}
        self.owed = owed - self.frozen_jit_boot_ids
        if expectations:
            self.owed = {rid for row in expectations["cases"].values() for rid in row["expected"]}
        self.envelope = _load(os.path.join(REPO, "ops", "machine_envelope.py"),
                              "rde_envelope").is_machine_envelope
        self.pinned = (expectations or {}).get("cases", {})
        self.labelled = (set(expectations["labelled"]) if expectations
                         else set(self.statements))

    def derive(self, case):
        """The live labels for a case: what the trigger layer owes it, from this tree."""
        if "required" in case:  # hand-written case: the labeller already said which
            expected = sorted(case["required"])
        else:
            expected = sorted((set(case["gold"]) - set(case["disputed"])) & self.owed)
        return {"split": case.get("split"), "input_sha256": case.get("input_sha256"),
                "cohort": "envelope" if self.envelope(case["prompt"]) else "human",
                "expected": expected,
                "allowed": sorted(set(case["gold"]) | set(case["disputed"])),
                "should_not_fire": not expected}

    def label(self, case):
        return self.pinned.get(case["id"]) or self.derive(case)

    def expected(self, case):
        return list(self.label(case)["expected"])

    # ---- delivery

    def prompt_ids(self, case):
        if not case["prompt"].strip():
            return []
        rows = self.rtd.advise(case["prompt"], session_id=None, log_path=os.devnull,
                               rank=_raise, ask=_raise)
        kept, _packs = self.preuse.semantic_delivery(__import__("pathlib").Path(REPO),
                                                     [row["id"] for row in rows])
        return sorted(kept)

    def tool_ids(self, case, index, call):
        payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                   "tool_input": call.get("tool_input"), "session_id": "rule-delivery-eval",
                   "tool_use_id": f"eval-{case['id']}-{index}"}
        if self.hook._matches(payload):
            return sorted(self.hook.scheduled_rule_ids())
        rows = self.hook.matched_triggers(payload)
        routed = self.hook.routed_rule_ids(payload)
        table = self.preuse.merge_trigger_delivery(rows)[2] if rows else []
        return sorted(set(routed) | set(table))

    # ---- token cost

    def receipt_chars(self, ids, *, prompt):
        """Characters of the receipt an event with these fresh ids injects.

        Replicates hooks/rule-pack-preuse-reselection._route_delivery's render
        (same keys, the real ROUTE_INSTRUCTION, the real fit_rules under the
        real cap) with fixed-width stand-ins for digests and identities. The
        prompt receipt has the same skeleton plus probabilities."""
        rr = self.rule_routes
        pad = "0" * 64
        base = {"schema": rr.ROUTE_RECEIPT_SCHEMA, "client": "claude",
                "session_id": "0" * 36, "turn_id": None, "tool_use_id": "toolu_" + "0" * 22,
                "tool_name": "Bash", "tool_input_sha256": pad, "routes_digest": pad,
                "triggers_digest": pad, "map_digest": pad, "source_digest": pad,
                "trigger_ids": [], "route_rule_ids": [], "packs": [],
                "identity": {"agent_principal_id": "0" * 36,
                             "runtime_principal": "joe-local", "sponsoring_human_id": "0" * 36},
                "rule_ids": list(ids), "not_found": [], "instruction": rr.ROUTE_INSTRUCTION,
                "rule_delivery": {"mode": "shadow", "declared_packs": [], "packs_not_found": []},
                "receipt_id": pad}

        def render(full, overflow):
            row = dict(base, rules=[{"id": r["id"], "statement": r["statement"]} for r in full],
                       overflow=overflow)
            if prompt:
                row["probabilities"] = {r["id"]: 1.0 for r in full}
            return self.preuse.canonical(row).decode("utf-8")
        rules = [{"id": rid, "statement": self.statements.get(rid, "")} for rid in sorted(ids)]
        full, overflow = rr.fit_rules(rules, render, always_on=set())
        return rr.context_chars(render(full, overflow)), bool(overflow)


def _raise(*_args, **_kwargs):
    raise RuntimeError("Jev judgment disabled for the deterministic eval")


# ----------------------------------------------------------------- replay

def replay(world, case):
    """[{kind, ids, fresh, tokens, overflow}] for the case's events, in order."""
    events = []
    ids = world.prompt_ids(case)
    if case["prompt"].strip():
        chars, over = world.receipt_chars(ids, prompt=True) if ids else (0, False)
        events.append({"kind": "prompt", "tool": None, "ids": ids, "fresh": ids,
                       "tokens": chars / CHARS_PER_TOKEN, "overflow": over})
    seen = set()
    for index, call in enumerate(case["tool_calls"]):
        got = world.tool_ids(case, index, call)
        fresh = [rid for rid in got if (rid, call["tool_name"]) not in seen]
        seen.update((rid, call["tool_name"]) for rid in fresh)
        chars, over = world.receipt_chars(fresh, prompt=False) if fresh else (0, False)
        events.append({"kind": "tool", "tool": call["tool_name"], "ids": got, "fresh": fresh,
                       "tokens": chars / CHARS_PER_TOKEN, "overflow": over})
    return events


def observe(case, events, boot_ids=None):
    """The raw observation of one case: what was delivered, never how it grades."""
    if boot_ids is None:
        boot_ids = _load(os.path.join(REPO, "ops/rule_delivery_eval.py"), "rde_boot").boot_always_on_ids(REPO)
    return {"case_id": case["id"], "split": case.get("split"), "input_sha256": case.get("input_sha256"),
            "available": sorted(set(boot_ids) | {rid for ev in events for rid in ev["ids"]}),
            "delivered": sorted({rid for ev in events for rid in ev["ids"]}),
            "prompt_delivered": sorted({rid for ev in events if ev["kind"] == "prompt" for rid in ev["ids"]}),
            "events": len(events), "tokens": sum(ev["tokens"] for ev in events),
            "over_cap_events": sum(1 for ev in events if ev["overflow"])}


def grade_observation(label, obs, labelled):
    """One case graded against its label. A false delivery is a labelled rule
    outside the case's allowed set; prompt_false counts the prompt event alone."""
    expected, allowed = set(label["expected"]), set(label["allowed"])
    delivered = set(obs["delivered"])
    hit = delivered & expected
    false = {rid for rid in delivered - allowed if rid in labelled}
    prompt_false = {rid for rid in set(obs["prompt_delivered"]) - allowed if rid in labelled}
    return {"expected": sorted(expected), "delivered": sorted(delivered),
            "hit": sorted(hit), "missed": sorted(expected - delivered),
            "false": sorted(false), "prompt_false": sorted(prompt_false),
            "events": obs["events"], "tokens": obs["tokens"],
            "recall": (len(hit) / len(expected)) if expected else None,
            "over_cap_events": obs["over_cap_events"]}


def grade(world, case, events):
    return grade_observation(world.label(case), observe(case, events), world.labelled)


def run(split, variant, out_dir=None):
    world = World(load_expectations())
    rows = []
    for case in load_cases(split):
        events = replay(world, case)
        g = grade(world, case, events)
        rows.append({"prompt_id": case["id"], "rep": 0, "prompt": case["prompt"][:400],
                     "tags": [case["stratum"], case["split"], case["source"],
                              "should_not_fire" if not g["expected"] else "owes_rules"],
                     "split": case["split"], "source": case["source"],
                     "stratum": case["stratum"], "kind": case["kind"], "note": case["note"],
                     "tool_names": [c["tool_name"] for c in case["tool_calls"]],
                     "status": "ok", "stop_reason": "end_turn", "model": "deterministic",
                     "detail": g,
                     "grade": _grade_dict(g)})
    if out_dir:
        write_run(out_dir, variant, rows)
    return rows


def _grade_dict(g):
    """Per-case metrics for the report. recall exists only where a rule is owed
    and clean only where none is (a should-not-fire case), so each column's
    mean is over the cases it is about."""
    grade = {"false_deliveries": len(g["false"]), "tokens": round(g["tokens"], 1)}
    if g["expected"]:
        grade["recall"] = g["recall"]
    else:
        grade["clean"] = 0.0 if g["false"] else 1.0
    return grade


def write_run(out_dir, variant, rows):
    vdir = os.path.join(out_dir, variant)
    os.makedirs(os.path.join(vdir, "traces"), exist_ok=True)
    result_path = os.path.join(vdir, "results.jsonl")
    existing = {}
    if os.path.exists(result_path):
        with open(result_path, encoding="utf-8") as handle:
            existing = {row["prompt_id"]: row for row in (json.loads(line) for line in handle if line.strip())}
    existing.update({row["prompt_id"]: row for row in rows})
    with open(result_path, "w", encoding="utf-8") as handle:
        for row in (existing[key] for key in sorted(existing)):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    for row in rows:
        trace = [{"role": "user", "content": row["prompt"] or "(tool-only turn)"},
                 {"role": "assistant", "content": json.dumps(row["detail"], indent=1)}]
        with open(os.path.join(vdir, "traces", f"{row['prompt_id']}_rep0.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(trace, handle)


# ----------------------------------------------------------------- expectations

def freeze_expectations(world, cases):
    """The grading labels for every case, as the live tree derives them now."""
    return {"schema": "rule-delivery-expectations", "version": EXPECTATIONS_VERSION,
            "rule": ("expected = gold - disputed - boot-delivered, restricted to rules the trigger layer "
                     "owes; hand cases use their required list. allowed = gold + disputed. cohort = "
                     "envelope when ops/machine_envelope classifies the prompt, else human. labelled = "
                     "every rule in the selection corpus; only those count as false deliveries."),
            "labelled": sorted(world.labelled),
            "cases": {c["id"]: world.derive(c) for c in sorted(cases, key=lambda c: c["id"])}}


def load_expectations(path=EXPECTATIONS):
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    if doc.get("version") != EXPECTATIONS_VERSION:
        raise ValueError(f"{path} is version {doc.get('version')!r}, this harness grades {EXPECTATIONS_VERSION!r}")
    return doc


def expectation_drift(world, cases, expectations):
    """Case ids whose live labels no longer match the frozen ones."""
    pinned = expectations["cases"]
    return sorted(c["id"] for c in cases if pinned.get(c["id"]) != world.derive(c))


def observe_all():
    world = World()
    boot_ids = world.ev.boot_always_on_ids(REPO)
    return [observe(case, replay(world, case), boot_ids) for case in sorted(load_cases("all"), key=lambda c: c["id"])]


def trace_repo_reads():
    """Record every file under the repository this process opens from now on."""
    repo = os.path.realpath(REPO)
    root = repo + os.sep
    seen = set()

    def hook(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.realpath(os.fsdecode(args[0]))
            if path.startswith(root):
                seen.add(os.path.relpath(path, repo))
                if path.endswith(".pyc"):
                    try:
                        source = importlib.util.source_from_cache(path)
                    except ValueError:
                        source = path[:-1]
                    if os.path.isfile(source):
                        seen.add(os.path.relpath(source, repo))
    sys.addaudithook(hook)
    return seen


# ----------------------------------------------------------------- receipt scorer

class CohortError(ValueError):
    pass


def paired_cohorts(expectations, baseline_rows, candidate_rows):
    """Both arms indexed by case id, refusing anything but the exact labelled set."""
    cases = expectations["cases"]

    def index(rows, arm):
        out = {}
        for row in rows:
            cid = row["case_id"]
            if cid in out:
                raise CohortError(f"{arm} repeats case {cid}")
            if cid not in cases:
                raise CohortError(f"{arm} carries unlabelled case {cid}")
            if (row["split"], row["input_sha256"]) != (cases[cid]["split"], cases[cid]["input_sha256"]):
                raise CohortError(f"{arm} case {cid} differs from its label in split or input")
            out[cid] = row
        missing = sorted(set(cases) - set(out))
        if missing:
            raise CohortError(f"{arm} is missing {len(missing)} labelled case(s), first {missing[0]}")
        return out
    return index(baseline_rows, "baseline"), index(candidate_rows, "candidate")


def _prompt_clean(rows):
    return _mean(0.0 if r["detail"]["prompt_false"] else 1.0 for r in rows)


RECEIPT_DIMENSIONS = (
    # (dimension id, test-split cohort, statistic)
    ("envelope-prompt-clean-delivery", "envelope", _prompt_clean),
    ("human-required-recall", "human", lambda rs: summarize(rs)["recall_micro"]),
    ("human-delivery-precision", "human", lambda rs: summarize(rs)["precision"]),
)


def score_receipt(expectations, baseline_rows, candidate_rows):
    """Every measured number in receipt.json, from the two observation cohorts.

    Each dimension is scored on the test split of its cohort: the point
    estimate, a case-bootstrap interval per arm, and the paired-bootstrap delta.
    The controls push an oracle (deliver exactly the expected rules) and a null
    (deliver nothing) through the same grader."""
    base, cand = paired_cohorts(expectations, baseline_rows, candidate_rows)
    cases, labelled = expectations["cases"], set(expectations["labelled"])

    def graded(rows):
        return {cid: {"prompt_id": cid, "split": cases[cid]["split"],
                      "detail": grade_observation(cases[cid], row, labelled)} for cid, row in rows.items()}
    gb, gc = graded(base), graded(cand)
    dims = {}
    with open(V2_CASES, encoding="utf-8") as handle:
        fixture = json.load(handle)
    availability_ids = {c["id"]: set(c["gold"]) for c in fixture["cases"] if c["split"] == "test"}
    def availability_row(observed, cid):
        gold = availability_ids[cid]
        return dict(observed, prompt_id=cid, availability_hits=len(set(observed["available"]) & gold),
                    availability_required=len(gold))
    def availability(rows):
        return sum(r["availability_hits"] for r in rows) / sum(r["availability_required"] for r in rows)
    ab = [availability_row(base[cid], cid) for cid in sorted(availability_ids)]
    ac = [availability_row(cand[cid], cid) for cid in sorted(availability_ids)]
    b_lo, b_hi = boot_ci(ab, availability)
    c_lo, c_hi = boot_ci(ac, availability)
    delta, low, high = paired_bootstrap(ab, ac, availability)
    dims["full-text-availability"] = {"baseline": {"score": availability(ab), "ci_low": b_lo, "ci_high": b_hi},
        "candidate": {"score": availability(ac), "ci_low": c_lo, "ci_high": c_hi},
        "delta": {"value": delta, "ci_low": low, "ci_high": high}}
    for dim_id, cohort, stat in RECEIPT_DIMENSIONS:
        ids = sorted(cid for cid, c in cases.items() if c["split"] == "test" and c["cohort"] == cohort)
        b, n = [gb[i] for i in ids], [gc[i] for i in ids]
        (b_lo, b_hi), (n_lo, n_hi) = boot_ci(b, stat), boot_ci(n, stat)
        point, lo, hi = paired_bootstrap(b, n, stat)
        dims[dim_id] = {"baseline": {"score": stat(b), "ci_low": b_lo, "ci_high": b_hi},
                        "candidate": {"score": stat(n), "ci_low": n_lo, "ci_high": n_hi},
                        "delta": {"value": point, "ci_low": lo, "ci_high": hi}}

    def control(deliver):
        rows = [{"detail": grade_observation(case, {"delivered": deliver(case), "prompt_delivered": [],
                                                    "events": 1, "tokens": 0.0, "over_cap_events": 0},
                                             labelled)} for case in cases.values()]
        return summarize(rows)["recall_micro"]
    return {"dimensions": dims,
            "controls": {"oracle_pass_rate": control(lambda c: c["expected"]),
                         "null_pass_rate": control(lambda c: [])}}


# ----------------------------------------------------------------- metrics

def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else float("nan")


def summarize(rows):
    """Aggregate metrics for a set of result rows."""
    d = [r["detail"] for r in rows]
    owes = [x for x in d if x["expected"]]
    quiet = [x for x in d if not x["expected"]]
    n_exp = sum(len(x["expected"]) for x in d)
    n_hit = sum(len(x["hit"]) for x in d)
    n_del = sum(len(x["delivered"]) for x in d)
    n_false = sum(len(x["false"]) for x in d)
    events = sum(x["events"] for x in d)
    return {
        "cases": len(rows), "owes_cases": len(owes), "should_not_fire_cases": len(quiet),
        "events": events,
        "recall_micro": n_hit / n_exp if n_exp else float("nan"),
        "recall_macro": _mean(x["recall"] for x in owes),
        "expected_rules": n_exp, "hit_rules": n_hit,
        "delivered_rules": n_del, "false_rules": n_false,
        "precision": (n_del - n_false) / n_del if n_del else float("nan"),
        "false_per_event": n_false / events if events else float("nan"),
        "sn_dirty_cases": sum(1 for x in quiet if x["false"]),
        "sn_false_rules": sum(len(x["false"]) for x in quiet),
        "sn_any_delivery_cases": sum(1 for x in quiet if x["delivered"]),
        "tokens_total": sum(x["tokens"] for x in d),
        "tokens_per_event": sum(x["tokens"] for x in d) / events if events else float("nan"),
        "over_cap_events": sum(x["over_cap_events"] for x in d),
    }


def per_case_vectors(rows):
    return {r["prompt_id"]: r["detail"] for r in rows}


def paired_bootstrap(base_rows, new_rows, stat, reps=2000, seed=20260929):
    """(delta, lo, hi): 95% percentile interval of stat(new) - stat(base) over
    cases resampled with replacement, same resample for both variants.

    The two arms must be the same cases: a case dropped from one arm would
    otherwise vanish from both, and a deleted failing row would raise the score."""
    b_ids = [r["prompt_id"] for r in base_rows]
    n_ids = [r["prompt_id"] for r in new_rows]
    if len(set(b_ids)) != len(b_ids) or len(set(n_ids)) != len(n_ids) or set(b_ids) != set(n_ids):
        only_b, only_n = sorted(set(b_ids) - set(n_ids)), sorted(set(n_ids) - set(b_ids))
        raise CohortError(f"paired comparison needs identical case cohorts: {len(only_b)} only in base, "
                          f"{len(only_n)} only in new, or a repeated case")
    ids = sorted(b_ids)
    b = {r["prompt_id"]: r for r in base_rows}
    n = {r["prompt_id"]: r for r in new_rows}
    rng = random.Random(seed)
    point = stat([n[i] for i in ids]) - stat([b[i] for i in ids])
    draws = []
    for _ in range(reps):
        pick = [rng.choice(ids) for _ in ids]
        draws.append(stat([n[i] for i in pick]) - stat([b[i] for i in pick]))
    draws.sort()
    return point, draws[int(0.025 * reps)], draws[int(0.975 * reps) - 1]


def boot_ci(rows, stat, reps=2000, seed=20260929):
    rng = random.Random(seed)
    ids = list(range(len(rows)))
    draws = sorted(stat([rows[rng.choice(ids)] for _ in ids]) for _ in range(reps))
    return draws[int(0.025 * reps)], draws[int(0.975 * reps) - 1]


STATS = {
    "false_per_event": lambda rs: summarize(rs)["false_per_event"],
    "recall_micro": lambda rs: summarize(rs)["recall_micro"],
    "false_rules": lambda rs: summarize(rs)["false_rules"],
    "sn_false_rules": lambda rs: summarize(rs)["sn_false_rules"],
    "tokens_per_event": lambda rs: summarize(rs)["tokens_per_event"],
    "precision": lambda rs: summarize(rs)["precision"],
}


def verdict(base, new, goal):
    """Keep/revert call for one round, from the TRAIN paired intervals.

    goal "recall": recall_micro must rise (train interval lower bound above 0)
    while false deliveries on should-not-fire cases and false deliveries per
    event do not rise. goal "tokens": tokens_per_event must fall (test interval
    upper bound below 0) while recall does not fall and neither false measure
    rises. The test split is for one final report, never candidate selection."""
    reasons, keep = [], True
    b, n = load_run(base, "train"), load_run(new, "train")
    d = {"train": {name: paired_bootstrap(b, n, stat) for name, stat in STATS.items()}}
    target, sign = (("recall_micro", 1) if goal == "recall" else ("tokens_per_event", -1))
    t_pt, t_lo, t_hi = d["train"][target]
    if not ((t_lo > 0) if sign > 0 else (t_hi < 0)):
        keep = False
        reasons.append(f"train {target} delta {t_pt:+.4f} [{t_lo:+.4f}, {t_hi:+.4f}] not clear of zero")
    if d["train"]["sn_false_rules"][0] > 0:
        keep = False
        reasons.append(f"train should-not-fire false deliveries rose {d['train']['sn_false_rules'][0]:+.0f}")
    if d["train"]["false_per_event"][0] > 1e-9:
        keep = False
        reasons.append(f"train false deliveries per event rose {d['train']['false_per_event'][0]:+.4f}")
    if goal == "tokens" and d["train"]["recall_micro"][0] < -1e-9:
        keep = False
        reasons.append(f"train recall fell {d['train']['recall_micro'][0]:+.4f}")
    return keep, reasons, d


def load_run(variant, split=None):
    path = os.path.join(RUNS, variant, "results.jsonl")
    with open(path, "r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [r for r in rows if split in (None, "all", r["split"])]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--split", default="all", choices=("train", "test", "all"))
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "NEW"))
    parser.add_argument("--print", action="store_true", help="print the summary only; write nothing")
    parser.add_argument("--verdict", nargs=3, metavar=("BASE", "NEW", "GOAL"),
                        help="keep/revert call for NEW against BASE; GOAL is recall or tokens")
    parser.add_argument("--observe", metavar="OUT", help="write one raw observation per case, sorted by id")
    parser.add_argument("--trace-reads", metavar="OUT", help="with --observe: write the repository files read")
    parser.add_argument("--freeze-expectations", action="store_true",
                        help=f"write {os.path.basename(EXPECTATIONS)} from the live labels")
    args = parser.parse_args(argv)
    if args.observe:
        reads = trace_repo_reads() if args.trace_reads else None
        rows = observe_all()
        with open(args.observe, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        if reads is not None:
            with open(args.trace_reads, "w", encoding="utf-8") as handle:
                json.dump(sorted(reads), handle, indent=1)
        return 0
    if args.freeze_expectations:
        doc = freeze_expectations(World(), load_cases("all"))
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        if os.path.exists(EXPECTATIONS):
            with open(EXPECTATIONS, encoding="utf-8") as handle:
                current = handle.read()
            if current != text and json.loads(current).get("version") == EXPECTATIONS_VERSION:
                print(f"refusing: the labels changed but {EXPECTATIONS_VERSION} is already frozen; "
                      f"relabelling is a new version (bump EXPECTATIONS_VERSION and the file name)",
                      file=sys.stderr)
                return 1
        with open(EXPECTATIONS, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"froze {len(doc['cases'])} cases as {EXPECTATIONS_VERSION}")
        return 0
    if args.verdict:
        keep, reasons, _d = verdict(*args.verdict)
        print("KEEP" if keep else "REVERT", "; ".join(reasons))
        return 0
    if args.compare:
        base, new = args.compare
        for split in (("train", "test") if args.split == "all" else (args.split,)):
            b, n = load_run(base, split), load_run(new, split)
            print(f"== {split}: {base} -> {new}")
            for name, stat in STATS.items():
                point, lo, hi = paired_bootstrap(b, n, stat)
                print(f"  {name:18s} {stat(b):9.4f} -> {stat(n):9.4f}  delta {point:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
        return 0
    rows = run(args.split, args.variant, None if args.print else RUNS)
    for split in ("train", "test"):
        sub = [r for r in rows if r["split"] == split]
        if sub:
            print(split, json.dumps(summarize(sub), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
