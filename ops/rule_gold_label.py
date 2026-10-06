"""Sampled semantic label proposals, cached once per bounded case. Exact universal policy labels stay in code; omitted candidates are explicitly unjudged. Independent adjudication supplies gold, not model self-agreement."""

import hashlib
import json
import math
import os
import re
import unicodedata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

YES_AT = 0.75
NO_AT = 0.35
STATEMENT_CHARS = 1500
LABEL_CAP = 20
TEST_FRACTION = 0.30
DEFAULT_SEED = "rule-delivery-eval-v2"
RULE_CLASSES = ("always_on", "action_point", "topic", "gate_named")

SYSTEM_NOTE = ("An AI assistant (Claude Code) works for the two partners of a small "
               "healthcare commercial real estate brokerage. It works in the firm's own "
               "software repository and record layer (deals, clients, tours, rules), and "
               "can run tools. `turn` is ONE turn: the message it received (from a partner, "
               "or a machine notification) and the tool calls it made in reply.")


# ------------------------------------------------------------------ the question

def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def call_line(call):
    """One readable line for a tool call: its name and its salient input."""
    name = call.get("tool_name") or "?"
    inp = call.get("tool_input") or {}
    if isinstance(inp, dict):
        for key in ("command", "file_path", "url", "description", "prompt", "query", "action"):
            if isinstance(inp.get(key), str) and inp[key].strip():
                return f"{name}: {_clip(inp[key], 240)}"
        if inp:
            return f"{name}: {_clip(json.dumps(inp, sort_keys=True), 240)}"
    return name


def audience(case):
    """Who reads the prose this turn ends with. A subagent brief opens a
    subagent session whose reply goes to the orchestrating agent; every other
    case is a turn in a partner's session (a notification arrives in one), so
    its closing prose is read by the partner."""
    return "orchestrator" if case.get("origin") == "subagent-brief" else "partner"


def opens_session(case):
    """A subagent brief is the first message of a new session; the other cases
    are single turns taken from inside a running session."""
    return case.get("origin") == "subagent-brief"


def case_state(case):
    is_machine = case["prompt"].lstrip().startswith("<task-notification>")
    turn = {"from": ("machine notification" if is_machine else
                     "orchestrating agent (a subagent brief)" if opens_session(case) else
                     "partner"),
            "message": case["prompt"][:6000],
            "tool_calls": [call_line(c) for c in (case.get("tool_calls") or [])[:20]]}
    if "origin" in case:
        turn["reply_read_by"] = audience(case)
        turn["opens_a_session"] = opens_session(case)
    return {"system": SYSTEM_NOTE, "turn": turn}


# THE STRICT QUESTION (the gold standard since the 2026-09-27 re-label). The
# first labelling asked whether a rule "binds on the turn"; a stricter
# re-judgment of a sample put 97 of 263 gold pairs at p <= 0.35, so the gold
# leaned padded, and padded gold would tune the system to over-deliver. The
# question now asks about the ACTION: is the rule's trigger met by what this
# turn asks for or does, so that doing this turn while ignoring the rule would
# violate it?
def rule_question(tsc, rule, limit=STATEMENT_CHARS):
    text = _clip(rule["statement"], limit)
    return tsc.noul(
        "A standing rule for the assistant:\n<<<\n" + text + "\n>>>\n\n"
        "Does this rule bind the ACTION taken in `turn`? Answer yes only if the rule's own "
        "trigger is met by what this turn asks for or does (its message, its tool calls, "
        "and the reply it ends with, read by `reply_read_by`), so that handling exactly "
        "this turn while ignoring the rule would VIOLATE it. Topical overlap, general "
        "relevance, or a rule that would bind some later or different action does not "
        "count.",
        true="The rule's trigger is met by this turn's action; ignoring it here would violate it.",
        false="Ignoring this rule on this turn would not violate it; its trigger is not met here.")


# RULES WITH A UNIVERSAL TRIGGER get one written policy, applied uniformly,
# instead of a per-case judgment (the first labelling was inconsistent across
# cases: gold on about half the partner turns and on a third to all of the
# subagent briefs). {rule id: (predicate name, reason)}. The label is the
# predicate's value; Jev is not asked about these rules.
UNIVERSAL_POLICY = {
    "5be2f462": ("partner_prose", "binds every session's prose to a partner; a partner-session "
                                  "turn ends in prose the partner reads, a subagent brief's "
                                  "reply goes to the orchestrating agent"),
    "7e9739f2": ("partner_prose", "a hard rule on every reply to the partner; not on a reply "
                                  "to an orchestrating agent"),
    "0156e9fa": ("partner_prose", "fires on closing any message to the partner"),
    "b3ea627f": ("partner_prose", "fires at the moment a session closes a message to the "
                                  "partner"),
    "4f7c348f": ("opens_session", "recitation happens at session open, before the first tool "
                                  "batch; a subagent brief opens a session, a mid-session "
                                  "partner turn does not"),
}
POLICY_PREDICATES = {
    "partner_prose": lambda case: audience(case) == "partner",
    "opens_session": opens_session,
}


def policy_labels(case):
    """{rule id: bool} for the rules UNIVERSAL_POLICY settles."""
    return {rid: bool(POLICY_PREDICATES[pred](case))
            for rid, (pred, _reason) in UNIVERSAL_POLICY.items()}


def _semantic():
    import importlib.util
    spec = importlib.util.spec_from_file_location("jev_semantic", os.path.join(REPO, "ops", "jev_semantic.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roster_binding(rules):
    material = sorted((r["id"], r["statement"]) for r in rules)
    return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode()).hexdigest()

def label_case(case, rules, tsc, *, calls_log, timeout=120.0):
    """{rule id: probability} for one bounded case in one request. Rules settled by UNIVERSAL_POLICY are not asked: they get 1.0 or
    0.0 from the policy. Returns (probabilities, usage) with usage
    {"input_tokens", "output_tokens", "requests"}."""
    probs, usage = {}, {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    state = case_state(case)
    for rid, value in policy_labels(case).items():
        if any(r["id"] == rid for r in rules):
            probs[rid] = 1.0 if value else 0.0
    rules = [r for r in rules if r["id"] not in UNIVERSAL_POLICY]
    words = set(re.findall(r"[a-z]{3,}", str(state).lower()))
    ranked = sorted(rules, key=lambda r: (-len(words & set(re.findall(r"[a-z]{3,}", r["statement"].lower()))), r["id"]))
    part = ranked[:LABEL_CAP]
    usage["unjudged"] = [r["id"] for r in ranked[LABEL_CAP:]]
    if part:
        answer = _semantic().ask(state, {r["id"]: rule_question(tsc, r) for r in part},
                                 client=tsc, caller="rule_gold_label", version="case-v2",
                                 calls_log=calls_log, timeout=timeout, facets=["evidence_matching"])
        for rid, row in answer["answers"].items():
            probs[rid] = round(float(row["noul"]), 4)
        usage.update({"input_tokens": 0, "output_tokens": 0} if answer.get("cache_hit") else answer.get("usage") or {})
        usage["cached_observation"] = answer.get("cached_observation") or (answer.get("usage") if answer.get("cache_hit") else None)
        usage["requests"] = 0 if answer.get("cache_hit") else 1
    return probs, usage


def second_pass(rule, cases, tsc, *, calls_log, timeout=120.0):
    """The other side of the question: the rule is the state and each borderline
    case is a noul, worded more strictly. {case id: probability}, usage."""
    state = {"system": SYSTEM_NOTE, "rule": {"id": rule["id"],
                                             "statement": _clip(rule["statement"], 6000)}}
    questions = {}
    for case in cases:
        s = case_state(case)["turn"]
        calls = "; ".join(s["tool_calls"]) or "none"
        reader = s.get("reply_read_by")
        questions[case["id"]] = tsc.noul(
            f"Turn from {s['from']}:\n<<<\n{_clip(s['message'], 1800)}\n>>>\n"
            f"Tool calls made in reply: {calls}\n"
            + (f"The reply this turn ends with is read by: {reader}.\n" if reader else "")
            + "\nDoes `rule` bind the ACTION taken in this turn — is its trigger actually met "
            "by what the turn asks for or does, so that handling this turn while ignoring "
            "the rule would violate it? Topical overlap, or a rule that would bind a later "
            "or different action, is not enough.",
            true="The rule's trigger is met by this turn's action; ignoring it here would violate it.",
            false="The rule's trigger is not met by this turn's action.")
    answer = _semantic().ask(state, questions, client=tsc, caller="rule_gold_label", version="second-pass-v1", calls_log=calls_log, timeout=timeout,
                     facets=["evidence_matching"])
    u = {} if answer.get("cache_hit") else answer.get("usage") or {}
    return ({cid: round(float(row["noul"]), 4) for cid, row in (answer.get("answers") or {}).items()},
            {"input_tokens": int(u.get("input_tokens") or 0),
             "output_tokens": int(u.get("output_tokens") or 0), "requests": 0 if answer.get("cache_hit") else 1,
             "cached_observation": answer.get("cached_observation") or (answer.get("usage") if answer.get("cache_hit") else None)})


# ------------------------------------------------------------------ doctrine (second target)
#
# Lexical search selects bounded sections. Documents and sections share one
# speculative question set; every proposed label needs independent adjudication.
SECTION_CHARS = 1200


def document_question(tsc, doc):
    return tsc.noul(
        f"A doctrine document of the firm (standing procedure, playbook or reference):\n"
        f"<<<\nTitle: {_clip(doc.get('title') or '', 200)}\n"
        f"Opening: {_clip(doc.get('opening') or '', 400)}\n>>>\n\n"
        "Does this document GOVERN `turn` — would an assistant handling exactly this turn need "
        "to follow or consult a section of it to act correctly? General topical relevance "
        "is not enough.",
        true="A section of this document governs how this turn must be handled.",
        false="This document does not govern this turn.")


def section_question(tsc, section):
    return tsc.noul(
        f"A section of the firm's doctrine ({_clip(section.get('doc_title') or section['doc'], 160)}"
        f" — {_clip(section.get('title') or '', 160)}):\n<<<\n"
        f"{_clip(section.get('text') or '', SECTION_CHARS)}\n>>>\n\n"
        "Does this section GOVERN `turn`: does it state a procedure, constraint or standard "
        "that an assistant handling exactly this turn must follow or check to act correctly? "
        "Background that is merely on the same topic does not govern.",
        true="This section governs how this turn must be handled.",
        false="This section does not govern this turn.")


def doctrine_label_case(case, documents, sections, tsc, *, calls_log, search_refs=(),
                        timeout=120.0):
    """({doc id: p}, {section ref: p}, usage) in one cached question set. `documents` rows carry id, title,
    opening; `sections` rows carry ref, doc, doc_title, title, text. Refs and
    doc ids are opaque store ids (slugs carry names; see the CLI catalog)."""
    state = case_state(case)
    words = set(re.findall(r"[a-z]{3,}", str(state).lower()))
    by_ref = {row["ref"]: row for row in sections}
    ranked = sorted(sections, key=lambda row: (-len(words & set(re.findall(r"[a-z]{3,}", str(row).lower()))), row["ref"]))
    refs = list(dict.fromkeys([ref for ref in search_refs if ref in by_ref]+[row["ref"] for row in ranked]))[:LABEL_CAP]
    picked = [by_ref[ref] for ref in refs]
    qs = {"s"+str(i): section_question(tsc,row) for i,row in enumerate(picked)}
    doc_ids = {row["doc"] for row in picked}
    docs = sorted([d for d in documents if d["id"] in doc_ids], key=lambda d:d["id"])
    qs.update({"d"+str(i): document_question(tsc,row) for i,row in enumerate(docs)})
    if not qs:
        return {}, {}, {"input_tokens":0,"requests":0,"shortlist":0}
    answer = _semantic().ask(state, qs, client=tsc, caller="rule_gold_label", version="doctrine-case-v2",
                            calls_log=calls_log, timeout=timeout)
    bodies = answer["answers"]
    doc_p = {d["id"]:float(bodies["d"+str(i)]["noul"]) for i,d in enumerate(docs)}
    sec_p = {row["ref"]:float(bodies["s"+str(i)]["noul"]) for i,row in enumerate(picked)}
    usage = {"input_tokens":0 if answer.get("cache_hit") else int((answer.get("usage") or {}).get("input_tokens") or 0),
             "requests":0 if answer.get("cache_hit") else 1, "shortlist":len(picked),
             "unjudged": [row["ref"] for row in sections if row["ref"] not in refs]}

    return doc_p, sec_p, usage


# ------------------------------------------------------------------ bands and gold

def band(p, yes_at=YES_AT, no_at=NO_AT):
    if p >= yes_at:
        return "gold"
    if p <= no_at:
        return "not"
    return "borderline"


def borderlines(probs, yes_at=YES_AT, no_at=NO_AT):
    """[(case id, rule id, p)] for every pair strictly inside the band."""
    return sorted((cid, rid, p) for cid, row in probs.items() for rid, p in row.items()
                  if band(p, yes_at, no_at) == "borderline")


# ------------------------------------------------------------------ the review band (round 3)
#
# The strict re-label adjudicated only 0.35 < p < 0.75, so the same situation
# got two labels depending on which side of 0.35 the first pass fell (a
# second review found clear dispatch turns unreviewed at p <= 0.35 for the
# model-routing rule, and auto-gold at p >= 0.75 that contradicted that rule's
# own adjudicated trigger). The gold now comes from a REVIEW SET, per rule:
#   * every pair with p > the rule's lower bound (REVIEW_LOW, or
#     REVIEW_LOW_EXTENDED for a rule whose in-band gold rate was high), with
#     NO auto-gold at the top: p >= 0.75 is reviewed like the rest;
#   * every case carrying the rule's exact ACTION SIGNAL, whatever its p;
#   * a rule in ACTION_POLICY (an `exact` entry: its trigger IS the action) is
#     settled by the signal alone, like UNIVERSAL_POLICY;
#   * a `signal_implies_gold` entry settles gold on every case carrying the
#     signal (e.g. every subagent spawn binds the model-routing rule), so the
#     same action never gets two labels; the rule's other cases are reviewed.
# The signals live in ops/fixtures/rule-delivery-eval/action-signals.v2.json,
# written from each rule's statement, not from the production trigger table.
#
# FLOORS (round 4). A third review found same-feature pairs on both sides of
# the lower bound for rules still dense at it. So: rules flagged that way get
# a floor below 0 (every case reviewed); for every other rule, the pairs just
# below its bound are sampled (sample_below_floor), adjudicated, and the gold
# rate published; a sample above 10% gold lowers the floor again. The floors
# and every sample's gold rate are committed in review-floors.v2.json.
# A pair in the review set is gold iff its written adjudication says so;
# outside it, not gold. Jev (strict second pass) is evidence only: it scored
# some clearly binding pairs at 0.12-0.18, so it never decides.

REVIEW_LOW = 0.25
REVIEW_LOW_EXTENDED = 0.20


def signal_hit(case, signal):
    """Does any tool call in `case` carry the action `signal`
    ({"tools": [regex over tool_name], "input_regex": regex or None})?"""
    for call in case.get("tool_calls") or []:
        name = call.get("tool_name") or ""
        if not any(re.search(t, name) for t in signal.get("tools") or ()):
            continue
        pattern = signal.get("input_regex")
        if pattern is None or re.search(pattern, json.dumps(call.get("tool_input") or {},
                                                             sort_keys=True), re.I):
            return True
    return False


def signal_pairs(cases, signals):
    """{(case id, rule id)} for every case carrying a rule's action signal."""
    return {(c["id"], s["rule"]) for c in cases for s in signals if signal_hit(c, s)}


def load_action_signals(path):
    """The committed action-signal table (action-signals.v2.json): a list of
    {rule, mode exact|review, tools, input_regex, signal_implies_gold, ...}."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    signals = doc["signals"]
    for s in signals:
        if s.get("mode") not in ("exact", "review"):
            raise ValueError(f"signal for {s.get('rule')}: mode must be exact or review")
        for t in s.get("tools") or ():
            re.compile(t)
        if s.get("input_regex"):
            re.compile(s["input_regex"])
    return signals


def action_settled(cases, signals):
    """Labels the action signals settle, applied the same way on every case
    whatever its score. Returns (settled rules, {case id: {rule id: bool}}):
      * a rule with any `exact` entry (ACTION_POLICY) is settled on every case:
        gold iff the case carries one of the rule's exact signals;
      * a `signal_implies_gold` entry settles gold on the cases carrying it;
        the rule's other cases stay in the ordinary review."""
    exact_rules = {s["rule"] for s in signals if s.get("mode") == "exact"}
    labels = {}
    for c in cases:
        row = {}
        for rid in exact_rules:
            row[rid] = any(signal_hit(c, s) for s in signals
                           if s["rule"] == rid and s.get("mode") == "exact")
        for s in signals:
            if s["rule"] in exact_rules or not s.get("signal_implies_gold"):
                continue
            if signal_hit(c, s):
                row[s["rule"]] = True
        labels[c["id"]] = row
    return exact_rules, labels


def load_review_floors(path):
    """The committed per-rule review floors (review-floors.v2.json):
    {"floors": {rule id: floor}, "samples": [...]}. A floor below 0 reviews
    every case of the rule."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    floors = doc["floors"]
    for rid, v in floors.items():
        if not isinstance(v, (int, float)) or v >= REVIEW_LOW:
            raise ValueError(f"floor for {rid} must be a number below REVIEW_LOW, got {v!r}")
    return floors


def sample_below_floor(probs, rid, floor, exclude, n):
    """The `n` unreviewed pairs of rule `rid` just below `floor` (highest p
    first, ties at the n-th score included), and the new floor that puts
    exactly them into the review set: the highest p left below them (or -1
    when none is left). `exclude` holds pairs already in the review set.
    Returns (sampled pairs, new floor)."""
    below = sorted(((row[rid], cid) for cid, row in probs.items()
                    if rid in row and row[rid] <= floor and (cid, rid) not in exclude),
                   reverse=True)
    if not below:
        return [], floor
    cut = below[min(n, len(below)) - 1][0]
    picked = [(cid, rid) for p, cid in below if p >= cut]
    rest = [p for p, _cid in below if p < cut]
    return picked, (max(rest) if rest else -1.0)


def review_plan(cases, probs, signals, extended_rules, floors=None):
    """Everything the review-set scheme needs, from its committed inputs.
    Returns {"low_by_rule", "signals_hit", "settled_rules", "settled_labels",
    "review"}: the per-rule lower bound (a committed floor from `floors` when
    the rule has one, else REVIEW_LOW_EXTENDED for `extended_rules`, else
    REVIEW_LOW), the (case, rule) pairs carrying a signal, the rules and
    per-case labels settled by policy (UNIVERSAL_POLICY and the action
    signals), and the review set itself."""
    rules = {rid for row in probs.values() for rid in row}
    extended = set(extended_rules)
    floors = floors or {}
    low = {rid: (floors[rid] if rid in floors
                 else REVIEW_LOW_EXTENDED if rid in extended else REVIEW_LOW) for rid in rules}
    exact_rules, act = action_settled(cases, signals)
    settled_rules = set(UNIVERSAL_POLICY) | exact_rules
    settled_labels = {}
    for c in cases:
        row = dict(policy_labels(c))
        row.update(act.get(c["id"]) or {})
        # only rules that were live (labelled) on the labelling date
        settled_labels[c["id"]] = {rid: v for rid, v in row.items() if rid in rules}
    hits = signal_pairs(cases, signals)
    settled_pairs = {(cid, rid) for cid, row in settled_labels.items() for rid in row}
    return {"low_by_rule": low, "signals_hit": hits, "settled_rules": settled_rules,
            "settled_labels": settled_labels,
            "review": review_set(probs, low, hits, settled_rules, settled_pairs)}


def review_set(probs, low_by_rule, signals_hit, settled_rules=(), settled_pairs=()):
    """Every non-policy semantic proposal requires independent adjudication.
    Score floors can prioritize review but cannot create negative gold labels.
    Exact policy rules and exact signal-settled pairs remain in code."""
    out = set()
    for cid, row in probs.items():
        for rid, p in row.items():
            if rid in settled_rules or (cid, rid) in settled_pairs:
                continue
            out.add((cid, rid))  # both positive and negative proposals need review
    return out


def adjudication_case_binding(case):
    """Identity of the evidence the adjudicator saw, excluding gold and split.

    The independent output must echo this binding. Excluding gold/probabilities
    prevents circular evidence; excluding split keeps adjudication blind to the
    held-out partition. Full tool inputs are included, not call_line's preview.
    """
    evidence = {"case_id": case["id"], "prompt": case.get("prompt", ""),
                "origin": case.get("origin"), "tool_calls": case.get("tool_calls") or []}
    encoded = json.dumps(evidence, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return {"case_id": case["id"], "input_sha256": hashlib.sha256(encoded).hexdigest()}


def validate_adjudication_bindings(adjudications, cases):
    """Refuse unbound, shifted, stale, duplicated or malformed decisions."""
    bindings = {c["id"]: adjudication_case_binding(c) for c in cases}
    if len(bindings) != len(cases):
        raise ValueError("duplicate case id in adjudication inputs")
    seen = set()
    for row in adjudications:
        pair = (row.get("case"), row.get("rule"))
        if (not all(isinstance(v, str) and v for v in pair)
                or type(row.get("gold")) is not bool):
            raise ValueError("adjudication requires case, rule and boolean gold")
        if pair in seen:
            raise ValueError(f"duplicate adjudication for {pair}")
        seen.add(pair)
        expected = bindings.get(pair[0])
        if expected is None or row.get("case_binding") != expected:
            raise ValueError(f"adjudication case binding mismatch for {pair}")


def gold_sets_reviewed(probs, adjudications, low_by_rule, signals_hit, settled_labels,
                       settled_rules, *, cases):
    """{case id: sorted gold ids} under the review-set scheme.
    `settled_labels` is {case id: {rule id: bool}} for every policy-settled
    pair (UNIVERSAL_POLICY, ACTION_POLICY, and signal-implied gold);
    `settled_rules` are the rules settled on every case. A review pair without
    an adjudication is an error; an adjudication outside the review set is an
    error too (it would be a label nobody can reproduce)."""
    validate_adjudication_bindings(adjudications, cases)
    decided = {(a["case"], a["rule"]): a["gold"] for a in adjudications}
    settled_pairs = {(cid, rid) for cid, row in settled_labels.items() for rid in row}
    need = review_set(probs, low_by_rule, signals_hit, settled_rules, settled_pairs)
    missing = sorted(need - set(decided))
    extra = sorted(set(decided) - need)
    if missing or extra:
        raise ValueError(f"{len(missing)} review pairs lack an adjudication (first "
                         f"{missing[:3]}); {len(extra)} adjudications fall outside the review "
                         f"set (first {extra[:3]})")
    out = {}
    for cid in probs:
        gold = {rid for rid, v in (settled_labels.get(cid) or {}).items() if v}
        gold |= {rid for (c, rid), v in decided.items() if c == cid and v}
        out[cid] = sorted(gold)
    return out


def gold_sets(probs, adjudications, yes_at=YES_AT, no_at=NO_AT):
    """Gold requires independent adjudication for every semantic pair, at every score."""
    decided = {(a["case"], a["rule"]): bool(a["gold"]) for a in adjudications}
    missing = []
    out = {}
    for cid, row in probs.items():
        gold = []
        for rid, p in row.items():
            if (cid, rid) not in decided:
                missing.append((cid, rid))
            elif decided[(cid, rid)]:
                gold.append(rid)
        out[cid] = sorted(gold)
    if missing:
        raise ValueError(f"{len(missing)} semantic labels have no adjudication, "
                         f"first {missing[:5]}")
    return out


# ------------------------------------------------------------------ split

def _unit(seed, case_id):
    digest = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(1 << 64)


def assign_splits(cases, seed=DEFAULT_SEED, fraction=TEST_FRACTION):
    """{case id: "train"|"test"}: per stratum, the ceil(fraction n) cases with
    the smallest seeded hash are test."""
    by = {}
    for case in cases:
        by.setdefault(case["stratum"], []).append(case["id"])
    out = {}
    for ids in by.values():
        ranked = sorted(ids, key=lambda cid: (_unit(seed, cid), cid))
        k = math.ceil(fraction * len(ranked))
        for i, cid in enumerate(ranked):
            out[cid] = "test" if i < k else "train"
    return out


def split_for_new_case(case_id, seed=DEFAULT_SEED, fraction=TEST_FRACTION):
    """A case added after the split (the intake) is placed by hash threshold,
    so no existing case moves."""
    return "test" if _unit(seed, case_id) < fraction else "train"


# ------------------------------------------------------------------ rule classes

def rule_classes(repo=REPO, rule_ids=None):
    """{rule id: class} by the ordered questions in the module docstring."""
    with open(os.path.join(repo, "ops", "config", "rule-enforcement-map.json"),
              "r", encoding="utf-8") as handle:
        emap = json.load(handle)
    with open(os.path.join(repo, "ops", "config", "rule-jit-triggers.v1.json"),
              "r", encoding="utf-8") as handle:
        jit = {rid for row in json.load(handle).get("triggers") or []
               for rid in row.get("rule_ids") or []}
    layers = emap.get("rule_load_layers") or {}
    controls = emap.get("rule_controls") or {}
    ids = list(rule_ids) if rule_ids is not None else list(layers)
    out = {}
    for rid in ids:
        layer = (layers.get(rid) or {}).get("load_layer")
        if layer == "layer0":
            out[rid] = "always_on"
        elif layer == "control":
            out[rid] = "gate_named"
        elif rid in jit or (controls.get(rid) or {}).get("enforcement_class") == "surfacing":
            out[rid] = "action_point"
        else:
            out[rid] = "topic"
    return out


GUIDANCE_MANIFEST = os.path.join("audits", "guidance-migration-manifest.v1.tsv")


def rule_groups(repo=REPO, rule_ids=None):
    """{rule id: "guidance_deferred"|"other"}. A REPORTING split, not a label:
    the rules the August guidance migration retyped as judgment_ambient
    guidance, which standing-context now defers to consumers never built.
    They are still live rules and already in the dense gold."""
    import csv
    with open(os.path.join(repo, GUIDANCE_MANIFEST), "r", encoding="utf-8") as handle:
        deferred = {row["source_id"].strip() for row in csv.DictReader(handle, delimiter="\t")
                    if (row.get("source_id") or "").strip()}
    ids = list(rule_ids) if rule_ids is not None else sorted(deferred)
    return {rid: ("guidance_deferred" if rid in deferred else "other") for rid in ids}


# ------------------------------------------------------------------ hygiene

# What a committed case must never carry. Deliberately broad: a false alarm
# costs a reworded case, a miss puts private material in the repository.
SCRUB_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "phone": r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b",
    # Two or more digits, or a unit: a shell positional ($1 in awk) is not money.
    "dollar": r"\$\s?\d{2,}|\$\s?\d+(?:\.\d+)?\s?(?:k|m|mm|million|/)",
    "square_feet": r"\b\d[\d,]*\s?(?:sf|sq\.? ?ft|square feet|rsf|usf)\b",
    "url_host": r"https?://(?!example\.com|x\.com/example)[\w.-]+",
    # A bare host with no scheme: an internal suffix (.local, .lan, .internal,
    # .home.arpa, a tailnet), or any dotted name on a common public TLD other
    # than the reserved example domains. A file name (report.json) has no TLD
    # from this list, so it passes.
    "bare_host": (r"\b(?:[a-z0-9-]+\.)+(?:local|lan|internal|intranet|corp|home\.arpa|ts\.net)\b"
                  r"|\b(?!example\.(?:com|org|net)\b)(?:[a-z0-9-]+\.)+"
                  r"(?:com|net|org|io|dev|ai|app|co|us|cloud|xyz)\b"),
    # A machine name in the house style (<owner>s-mac-studio, a macbook-air).
    "machine_name": r"\b[a-z0-9]+s?-(?:mac|macbook|mbp|imac)(?:-[a-z0-9]+)*\b",
    "ip": r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    "credential_path": (r"(?:~/\.config/|\.env\b|\.age\b|typesafe\.env|api[_-]?key\s*="
                        r"|\bid_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?\b"
                        r"|(?:^|[\s/~\"'])\.ssh(?:/|\b)|\bauthorized_keys\b|\bknown_hosts\b"
                        r"|\.gnupg\b|\bprivate[_-]?key\b|\.(?:pem|p12|pfx|key)\b)"),
    "token_like": r"\b(?:sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9]{12,}",
}


#: Letters NFKD does not decompose into a base letter plus a mark.
_FOLD_EXTRA = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
                             "ß": "ss", "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "þ": "th",
                             "Þ": "th", "ð": "d", "Ð": "d", "ı": "i"})


def fold(text):
    """Lower-case with accents removed (NFKD, combining marks dropped, and the
    few letters NFKD leaves whole mapped to their plain spelling), so an
    accented name and its plain spelling match each other either way."""
    decomposed = unicodedata.normalize("NFKD", (text or "").translate(_FOLD_EXTRA))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def name_findings(text, names=()):
    """The record names (person or practice) `text` carries, matched as whole
    words, case-insensitively and accent-insensitively (both sides folded).
    A word boundary is any non-letter, non-digit character, so a name after a
    newline, a tab or punctuation still matches. The names come from the
    record layer at intake time and are never written anywhere."""
    hits = []
    folded = fold(text)
    for name in names:
        name = (name or "").strip()
        if len(name) < 3:
            continue
        if re.search(r"(?<![^\W_])" + re.escape(fold(name)) + r"(?![^\W_])", folded):
            hits.append(name)
    return hits


def scrub_findings(text, extra_names=()):
    """[(pattern name, match)] for anything a committed case must not carry.
    `extra_names` are person or practice names (from the record) to refuse as
    well; a name hit is reported as ("name", "<withheld>") so the refusal
    never echoes the name it caught."""
    hits = []
    for name, pattern in SCRUB_PATTERNS.items():
        for m in re.finditer(pattern, text, flags=re.I):
            hits.append((name, m.group(0)))
    hits += [("name", "<withheld>") for _ in name_findings(text, extra_names)]
    return hits


# Name tokens too generic to refuse on their own (a practice called "X Family
# Dental" must not make every prompt about dental work refusable).
GENERIC_NAME_TOKENS = frozenset("""
the and of for at in on llc inc pllc pa pc md dds dmd do lp ltd co corp group groups
medical medicine dental dentistry health healthcare clinic clinics center centre centers
care family practice practices partners partner associates services service office offices
building plaza suite pediatric pediatrics orthopedic orthopedics physical therapy vision eye
eyecare surgery surgical specialists specialty urgent primary women womens children childrens
imaging lab labs pharmacy wellness rehab rehabilitation hospital hospitals institute south
north east west gulf coast bay beach city county new first street road avenue drive lease
deal renewal expansion relocation sale site property properties space tenant landlord
""".split())


OWNER_ROLE_WORDS = frozenset({"agent", "local", "none", "null", "unassigned", "owner", "system",
                              "bot", "session", "machine"})


def common_words(path="/usr/share/dict/words"):
    """Lower-case English words, to keep ordinary words out of the name terms.
    The system word list's lower-case entries when it exists (proper nouns
    there are capitalised, so they stay distinctive), always joined with
    GENERIC_NAME_TOKENS."""
    words = set(GENERIC_NAME_TOKENS)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            words |= {w.strip() for w in handle if w.strip() and w.strip().islower()}
    except OSError:
        pass
    return words


def record_name_terms(rows, common=None):
    """Name strings to refuse, from record rows [{"name": ..., "kind": ...}].

    kind "partner" (a deal's or lead's owner): every alphabetic token of three
    letters or more except the role words in OWNER_ROLE_WORDS, even a common
    word, since a partner's first name is the likeliest name to slip into a
    prompt. kind "person" or "practice" (a client, a lead): the
    full name when it has two or more words or is not a common word, plus each
    title-case token of four letters or more that is not a common word (a
    surname alone still identifies). kind "deal" (a deal's own name): the full
    name only, and only when it has two or more words. Ordinary words drawn
    from free-text record names ("from", "code", "Studio") are dropped, or
    every prompt would be refused."""
    common = common_words() if common is None else common
    terms = set()
    for row in rows:
        full = " ".join(str(row.get("name") or "").split())
        kind = row.get("kind")
        if len(full) < 3:
            continue
        multiword = len(full.split()) >= 2
        if kind == "partner":
            # Owner labels are first names, sometimes wrapped in a role label
            # ("Agent (<name>-local)"): keep every alphabetic token but the
            # role words.
            terms.update(tok for tok in re.findall(r"[^\W\d_]{3,}", full)
                         if fold(tok) not in OWNER_ROLE_WORDS)
            continue
        if kind == "deal":
            if multiword:
                terms.add(full)
            continue
        if multiword or fold(full) not in common:
            terms.add(full)
        # Title-case tokens of four letters or more, any script (an accented
        # surname is a term on its own too). A hyphenated or apostrophe name
        # is kept whole, and each of its parts is considered as well.
        for word in re.findall(r"[^\W\d_]+(?:['-][^\W\d_]+)*", full):
            for tok in dict.fromkeys([word, *re.split(r"['-]", word)]):
                if (len(tok) >= 4 and tok[0].isupper() and tok[1:2].islower()
                        and fold(tok) not in common):
                    terms.add(tok)
    return sorted(terms)


def longest_shared_run(a, b):
    """Length in words of the longest run of consecutive words two texts share
    (case- and punctuation-insensitive): the verbatim-copy test."""
    wa = re.findall(r"[a-z0-9']+", (a or "").lower())
    wb = re.findall(r"[a-z0-9']+", (b or "").lower())
    if not wa or not wb:
        return 0
    best = 0
    prev = [0] * (len(wb) + 1)
    for x in wa:
        cur = [0] * (len(wb) + 1)
        for j, y in enumerate(wb, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


# ------------------------------------------------------------------ regression intake

MAX_SHARED_RUN = 5


def _string_leaves(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _string_leaves(key)
            yield from _string_leaves(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _string_leaves(value)


def validate_intake(doc, case_id, missed_rule, *, live_rules, prompt=None, stratum=None,
                    tool_calls=None, source_text=None, extra_names=()):
    """Every refusal the intake makes, run BEFORE anything leaves the machine
    (the Jev labelling pass comes after this returns). Returns (prompt,
    stratum, tool_calls, new case id); raises ValueError naming the kind of
    problem, never echoing a caught name or the live turn's words.

    Refused: an unknown rule; a live reference without a paraphrase and a
    stratum; a prompt OR any string inside the tool calls that shares more
    than MAX_SHARED_RUN consecutive words with the live turn; anything
    scrub_findings() catches (emails, phones, figures, URL and bare hosts,
    machine names, IPs, credential, key and ssh paths, tokens, and the
    person and practice names in `extra_names`); a miss already present."""
    if missed_rule not in live_rules:
        raise ValueError(f"{missed_rule} is not a live rule")
    existing = {c["id"]: c for c in doc.get("cases") or []}
    base = existing.get(case_id)
    if base is None and (not prompt or not stratum):
        raise ValueError("a live reference needs --prompt (a paraphrase) and --stratum")
    prompt = prompt or base["prompt"]
    stratum = stratum or base["stratum"]
    calls = tool_calls if tool_calls is not None else ((base or {}).get("tool_calls") or [])
    if source_text is not None:
        run = longest_shared_run(prompt, source_text)
        if run > MAX_SHARED_RUN:
            raise ValueError(f"the prompt shares a {run}-word run with the live turn: "
                             "paraphrase it")
        for text in _string_leaves(calls):
            run = longest_shared_run(text, source_text)
            if run > MAX_SHARED_RUN:
                raise ValueError(f"a tool-call input shares a {run}-word run with the live "
                                 "turn: paraphrase it")
    # Every check runs on the parsed values (the prompt and each string leaf
    # of the tool calls, keys included), never on JSON text: serialising
    # would turn a newline or tab before a name into "\n"/"\t" glued to it,
    # and escape accented letters to \uXXXX.
    hits = []
    for text in [prompt, *_string_leaves(calls)]:
        hits += scrub_findings(text, extra_names)
    if hits:
        # Kinds only: the refusal never repeats the material it caught.
        kinds = sorted({kind for kind, _ in hits})
        raise ValueError(f"the case carries material that may not be committed: "
                         f"{', '.join(kinds)} (reword it; the matched text is not shown)")
    slug = re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")[:24]
    new_id = f"reg-{slug}-{missed_rule}"
    if new_id in existing:
        raise ValueError(f"{new_id} already exists: this miss is already in the benchmark")
    return prompt, stratum, calls, new_id


def intake_case(doc, case_id, missed_rule, *, live_rules, prompt=None, stratum=None,
                tool_calls=None, source_text=None, probs=None, extra_names=(),
                seed=None):
    """A LIVE MISS BECOMES A BENCHMARK CASE. Returns the new case dict (the
    caller appends it). Raises ValueError when the case may not be committed.

    `case_id` is either a case already in `doc` (a benchmark shape that missed
    live: the new case copies it) or a live reference (a drift-observer event,
    a transcript turn) whose partner words are `source_text` — then `prompt`
    must be a PARAPHRASE of it: no run of more than MAX_SHARED_RUN words in
    common, nothing scrub_findings() refuses.

    `missed_rule` is gold by observation. Copied gold remains explicit source
    evidence; every additional Jev proposal is disputed pending adjudication.
    Numeric confidence never promotes an intake label."""

    existing = {c["id"]: c for c in doc.get("cases") or []}
    base = existing.get(case_id)
    prompt, stratum, calls, new_id = validate_intake(
        doc, case_id, missed_rule, live_rules=live_rules, prompt=prompt, stratum=stratum,
        tool_calls=tool_calls, source_text=source_text, extra_names=extra_names)
    gold = set((base or {}).get("gold") or [])
    disputed = sorted(rid for rid in (probs or {}) if rid in live_rules
                      and rid != missed_rule and rid not in gold)
    labels = "partial"
    gold.add(missed_rule)
    return {"id": new_id, "stratum": stratum,
            "split": split_for_new_case(new_id, seed or DEFAULT_SEED),
            "origin": "live-miss", "live_ref": case_id, "missed_rule": missed_rule,
            "labels": labels, "prompt": prompt, "tool_calls": calls,
            "gold": sorted(gold), "gold_doctrine": [], "disputed": disputed,
            "adjudicated": []}
