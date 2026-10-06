#!/usr/bin/env python3
"""rule-gold-label.py — dense gold labels for the rule-delivery benchmark.

The procedure (scheme, bands, split, rule classes) is documented in
ops/rule_gold_label.py; this is its command line. Five steps, each resumable:

    label        Jev first pass: every case against EVERY live rule
                 (case as state, one noul per rule, ~100 rules a request).
    second-pass  Jev from the other side on every BORDERLINE pair
                 (NO_AT < p < YES_AT): the rule as state, the case as the noul.
    doctrine-catalog / doctrine-search / doctrine-label
                 The doctrine target (shortlist then label; see the library).
                 Section refs are opaque store ids, never slugs.
    borderlines  Write the adjudication worklist: the REVIEW SET (every pair
                 above the rule's lower bound, and every case carrying the
                 rule's action signal), with both probabilities.
    build        Assemble cases.v2.json from the drafted cases, the first-pass
                 probabilities, the action signals and the adjudications (a
                 review pair with no written decision, or a decision outside
                 the review set, is an error), with the seeded 30% test split.
    spot-check   Pull a stratified sample of labels for a human to check.

    tools/rule-gold-label.py label --cases drafts.jsonl --corpus corpus-live.json \\
        --probs out/rule-delivery-eval/v2/probs.json
    tools/rule-gold-label.py build --cases drafts.jsonl --probs ... \\
        --adjudications adjudications.jsonl --out ops/fixtures/rule-delivery-eval/cases.v2.json

Jev spend: every request's receipt goes to --calls-log (default
out/rule-delivery-eval/jev-label-calls.jsonl), and each step prints the input
tokens it used. Nothing here writes a production log or cache.
"""
import argparse
import concurrent.futures as cf
import importlib.util
import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CALLS = os.path.join(REPO, "out", "rule-delivery-eval", "jev-label-calls.jsonl")
FIXTURE_SCHEMA = "rule-delivery-eval-cases/v2"
PROVENANCE = ("paraphrased: every prompt is a paraphrased SHAPE of a real turn (local session "
              "history, the drift observer's log, the delivery and selector logs) or of a "
              "recorded failure (defect classes, staged defects, incidents), or is grounded in "
              "a record-layer verb's contract. No partner words, no person, client or practice "
              "names, no hostnames, no deal figures, no credentials. Sources stay in the "
              "labeller's private scratch.")


def _load(name):
    path = os.path.join(REPO, "ops", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_cases(path):
    if path.endswith(".jsonl"):
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)["cases"]


def read_rules(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)["rules"]


def _read_json(path, default):
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def cmd_label(args, gl, tsc):
    cases, rules = read_cases(args.cases), read_rules(args.corpus)
    probs = _read_json(args.probs, {})
    coverage_path = args.probs + ".coverage.json"
    coverage = _read_json(coverage_path, {})
    roster = {r["id"] for r in rules}
    todo = [c for c in cases if (c["id"] not in probs or
            set(coverage.get(c["id"], {}).get("judged", [])) != set(probs[c["id"]]) or
            set(probs[c["id"]]) | set(coverage.get(c["id"], {}).get("unjudged", [])) != roster or
            coverage.get(c["id"], {}).get("case_binding") != gl.adjudication_case_binding(c) or
            coverage.get(c["id"], {}).get("roster_binding") != gl.roster_binding(rules))]
    spent = {"input_tokens": 0, "requests": 0}

    def one(case):
        return case["id"], gl.label_case(case, rules, tsc, calls_log=args.calls_log)
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for n, (cid, (p, usage)) in enumerate(pool.map(one, todo), 1):
            probs[cid] = p
            coverage[cid] = {"judged": sorted(p), "unjudged": sorted(usage.get("unjudged") or []),
                             "case_binding": gl.adjudication_case_binding(next(c for c in cases if c["id"] == cid)),
                             "roster_binding": gl.roster_binding(rules)}
            spent["input_tokens"] += usage["input_tokens"]
            spent["requests"] += usage["requests"]
            if n % 10 == 0:
                _write_json(args.probs, probs)
                _write_json(coverage_path, coverage)
    _write_json(args.probs, probs)
    _write_json(coverage_path, coverage)
    print(json.dumps({"labelled": len(todo), "cases": len(probs), **spent}))


def cmd_doctrine_catalog(args, gl):
    """Read every doctrine document once (doctrine-index, then read-doctrine)
    into a private catalog of active sections. Refs are OPAQUE store ids,
    '<document id>#<section id>': slugs and section keys carry person and
    practice names, and these refs end up in the committed fixture."""
    ev = _load("rule_delivery_eval")
    docs = ev._run_verb(REPO, "doctrine-index", {}).get("documents") or []

    def one(doc):
        try:
            return doc, ev._run_verb(REPO, "read-doctrine", {"document": doc["slug"]})
        except ValueError:
            return doc, {}
    catalog = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for doc, body in pool.map(one, docs):
            for s in body.get("sections") or []:
                if s.get("status") not in (None, "active") or not s.get("section_id"):
                    continue
                catalog.append({"ref": f"{doc['id']}#{s['section_id']}", "doc": doc["id"],
                                "doc_title": doc.get("title"),
                                "content_class": doc.get("content_class"),
                                "title": s.get("title") or "",
                                "text": (s.get("body") or {}).get("text") or ""})
    _write_json(args.catalog, catalog)
    print(json.dumps({"documents": len(docs), "sections": len(catalog)}))


def cmd_doctrine_search(args, gl):
    """The deterministic shortlist half: search-doctrine on each case's text,
    through the same ./run.sh call door a session uses. {case id: [refs]},
    refs opaque as in the catalog."""
    ev = _load("rule_delivery_eval")
    cases = read_cases(args.cases)
    out = _read_json(args.search, {})

    def one(case):
        try:
            return case["id"], sorted(ev.doctrine_search_refs(REPO, case["prompt"], args.limit))
        except ValueError:
            return case["id"], []
    todo = [c for c in cases if c["id"] not in out]
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for cid, refs in pool.map(one, todo):
            out[cid] = refs
    _write_json(args.search, out)
    print(json.dumps({"searched": len(todo), "with_hits": sum(1 for v in out.values() if v)}))


def cmd_doctrine_label(args, gl, tsc):
    cases = read_cases(args.cases)
    catalog = _read_json(args.catalog, [])
    documents, seen = [], set()
    for s in catalog:
        if s["doc"] not in seen:
            seen.add(s["doc"])
            documents.append({"id": s["doc"], "title": s.get("doc_title"),
                              "opening": s.get("text") or ""})
    search = _read_json(args.search, {})
    out = _read_json(args.doctrine_probs, {})
    todo = [c for c in cases if c["id"] not in out]
    spent = {"input_tokens": 0, "requests": 0}

    def one(case):
        return case["id"], gl.doctrine_label_case(case, documents, catalog, tsc,
                                                  calls_log=args.calls_log,
                                                  search_refs=search.get(case["id"]) or [])
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for n, (cid, (docs, secs, usage)) in enumerate(pool.map(one, todo), 1):
            out[cid] = {"documents": docs, "sections": secs, "shortlist": usage["shortlist"]}
            spent["input_tokens"] += usage["input_tokens"]
            spent["requests"] += usage["requests"]
            if n % 10 == 0:
                _write_json(args.doctrine_probs, out)
    _write_json(args.doctrine_probs, out)
    print(json.dumps({"labelled": len(todo), **spent}))


def cmd_second(args, gl, tsc):
    cases = {c["id"]: c for c in read_cases(args.cases)}
    rules = {r["id"]: r for r in read_rules(args.corpus)}
    probs = _read_json(args.probs, {})
    second = _read_json(args.second, {})
    by_rule = {}
    for cid, rid, _p in gl.borderlines(probs):
        if cid in cases and rid in rules and rid not in second.get(cid, {}):
            by_rule.setdefault(rid, []).append(cases[cid])
    spent = {"input_tokens": 0, "requests": 0}

    def one(item):
        rid, group = item
        out = {}
        for start in range(0, len(group), 40):
            p, usage = gl.second_pass(rules[rid], group[start:start + 40], tsc,
                                      calls_log=args.calls_log)
            out.update(p)
            spent["input_tokens"] += usage["input_tokens"]
            spent["requests"] += usage["requests"]
        return rid, out
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for rid, out in pool.map(one, sorted(by_rule.items())):
            for cid, p in out.items():
                second.setdefault(cid, {})[rid] = p
    _write_json(args.second, second)
    print(json.dumps({"rules": len(by_rule),
                      "pairs": sum(len(v) for v in by_rule.values()), **spent}))


def cmd_borderlines(args, gl):
    """The adjudication worklist: the REVIEW SET (see the library), with both
    probabilities and whether the case carries the rule's action signal."""
    drafts = read_cases(args.cases)
    probs = _read_json(args.probs, {})
    second = _read_json(args.second, {})
    signals = gl.load_action_signals(args.signals)
    case_probs = {c["id"]: probs[c["id"]] for c in drafts}
    plan = gl.review_plan(drafts, case_probs, signals, _read_json(args.extended_rules, []),
                          _floors(args, gl))
    by_id = {c["id"]: c for c in drafts}
    rows = [{"case": cid, "rule": rid,
             "case_binding": gl.adjudication_case_binding(by_id[cid]),
             "p_first": case_probs[cid][rid],
             "p_second": second.get(cid, {}).get(rid),
             "action_signal": (cid, rid) in plan["signals_hit"]}
            for cid, rid in sorted(plan["review"])]
    with open(args.out, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    print(json.dumps({"review_pairs": len(rows), "cases": len({r['case'] for r in rows}),
                      "rules": len({r['rule'] for r in rows})}))


def _floors(args, gl):
    """The committed per-rule review floors, or {} when there is no floors file."""
    path = getattr(args, "floors", None)
    return gl.load_review_floors(path) if path and os.path.exists(path) else {}


def _read_adjudications(path):
    with open(path, "r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    for row in rows:
        if not isinstance(row.get("reason"), str) or len(row["reason"].strip()) < 10:
            raise ValueError(f"adjudication {row.get('case')}/{row.get('rule')} has no written reason")
    return rows


def cmd_build(args, gl):
    drafts = read_cases(args.cases)
    probs = _read_json(args.probs, {})
    adjudications = _read_adjudications(args.adjudications)
    signals = gl.load_action_signals(args.signals)
    extended = sorted(_read_json(args.extended_rules, []))
    case_probs = {c["id"]: probs[c["id"]] for c in drafts}
    floors = _floors(args, gl)
    plan = gl.review_plan(drafts, case_probs, signals, extended, floors)
    gold = gl.gold_sets_reviewed(case_probs, adjudications, plan["low_by_rule"],
                                 plan["signals_hit"], plan["settled_labels"],
                                 plan["settled_rules"], cases=drafts)
    dprobs = {cid: row["sections"] for cid, row in _read_json(args.doctrine_probs, {}).items()}
    dadj = _read_adjudications(args.doctrine_adjudications) if args.doctrine_adjudications else []
    dgold = gl.gold_sets({c["id"]: dprobs.get(c["id"], {}) for c in drafts}, dadj) if dprobs else {}
    splits = gl.assign_splits(drafts, seed=args.seed)
    decided = {(a["case"], a["rule"]) for a in adjudications}
    rules = [r["id"] for r in read_rules(args.corpus)]
    groups = gl.rule_groups(REPO, rules)
    deferred = sorted(rid for rid, grp in groups.items() if grp == "guidance_deferred")
    cases = []
    for draft in drafts:
        cid = draft["id"]
        cases.append({
            "id": cid, "stratum": draft["stratum"], "split": splits[cid],
            "origin": draft.get("origin"),
            "prompt": draft["prompt"], "tool_calls": draft.get("tool_calls") or [],
            "gold": gold[cid],
            # Tag: the gold rules that belong to the guidance-deferred group
            # (a reporting split; they are already in `gold`).
            "gold_guidance_deferred": [rid for rid in gold[cid] if rid in set(deferred)],
            # Second target set: doctrine section refs, opaque store ids
            # ("<document id>#<section id>").
            "gold_doctrine": dgold.get(cid, []),
            "adjudicated": sorted(rid for (c, rid) in decided if c == cid),
            "judged_rules": sorted(set(case_probs[cid]) | set(plan["settled_labels"].get(cid) or {})),
            "unjudged_rules": sorted(set(rules) - set(case_probs[cid]) - set(plan["settled_labels"].get(cid) or {})),
            "disputed": sorted(set(draft.get("disputed") or [])),
            "doctrine_judged": sorted(dprobs.get(cid) or {}),
        })
    doc = {"schema": FIXTURE_SCHEMA, "provenance": PROVENANCE,
           "labelling": {"scheme": "case as state, one Jev noul per live rule "
                                   "(ops/rule_gold_label.py)",
                         "question": "strict: does the rule bind the ACTION taken in this "
                                     "turn, so that ignoring it here would violate it "
                                     "(re-labelled 2026-09-27 after a review found the "
                                     "first, looser question padded the gold)",
                         "universal_policy": {rid: {"label_is": pred, "reason": reason}
                                              for rid, (pred, reason)
                                              in sorted(gl.UNIVERSAL_POLICY.items())},
                         "live_rules": len(rules),
                         "adjudication_binding": "Each rule adjudication echoes its worklist "
                                     "case id and SHA256 of id, origin, prompt and full tool "
                                     "inputs. Build refuses missing or mismatched bindings. "
                                     "Legacy binding import alone is not semantic review.",
                         "review": "per rule, every pair with first-pass p above review_low "
                                   "(review_low_extended for the listed rules), and every case "
                                   "carrying the rule's action signal, gets a written "
                                   "adjudication that decides; there is no auto-gold. Pairs "
                                   "outside the review set are not gold. A second Jev pass is "
                                   "evidence only (round 3, 2026-09-27, after a review found "
                                   "the 0.35-0.75 band labelled the same situation two ways)",
                         "review_low": gl.REVIEW_LOW,
                         "review_low_extended": gl.REVIEW_LOW_EXTENDED,
                         "review_low_extended_rules": extended,
                         "review_floors": {
                             "file": os.path.basename(args.floors) if floors else None,
                             "rule": "a committed floor replaces the rule's lower bound; below 0 "
                                     "reviews every case. Floors come from adjudicated samples "
                                     "just below the bound (gold rate published per sample); a "
                                     "sample above 10% gold lowered the floor again"},
                         "action_signals": {
                             "file": os.path.basename(args.signals),
                             "exact_rules": sorted({s["rule"] for s in signals
                                                    if s["mode"] == "exact"}),
                             "rule": "an exact rule is gold iff the case carries its signal; a "
                                     "signal_implies_gold entry makes every case carrying it gold; "
                                     "other entries only put the case in the review set"},
                         "doctrine_band": {"yes_at": gl.YES_AT, "no_at": gl.NO_AT},
                         "doctrine": "shortlist then label (ops/rule_gold_label.py): Jev noul per "
                                     "doctrine document, plus search-doctrine hits, then a Jev "
                                     "noul per shortlisted section; borderline pairs adjudicated "
                                     "in writing. Recall of doctrine gold is bounded by the "
                                     "shortlist.",
                         "labelled_on": args.labelled_on},
           "split": {"seed": args.seed, "test_fraction": gl.TEST_FRACTION,
                     "rule": "per stratum, the ceil(0.3 n) cases with the smallest "
                             "sha256(seed:id) are test; later cases by hash threshold",
                     "counts": {s: sum(1 for c in cases if c["split"] == s)
                                for s in ("train", "test")}},
           "note": "Tuning may read only split == train. Gold ids are live rules on the "
                   "labelling date; re-label rather than edit a case to fit a path.",
           "targets": {"gold": "independently adjudicated or policy-settled rules; judged_rules bounds each case",
                       "gold_doctrine": "doctrine section refs '<document id>#<section id>' "
                                        "(store ids, not slugs: slugs and section keys carry "
                                        "names; resolve with read-doctrine). Second target "
                                        "set, shortlist-then-label; scored apart"},
           "rule_groups": {"guidance_deferred": {
               "source": gl.GUIDANCE_MANIFEST,
               "note": "Rules retyped as judgment_ambient guidance in August and deferred by "
                       "standing-context to consumers never built. A reporting split only.",
               "rules": deferred}},
           "cases": cases}
    _write_json(args.out, doc)
    # The first-pass probabilities and the adjudications are committed beside
    # the fixture, so the gold can be rebuilt and re-examined without Jev.
    labels = {"schema": "rule-delivery-eval-labels/v2",
              "note": "First-pass Jev probability per case, one entry per rule in `rules` order. "
                      "`doctrine` holds the shortlisted sections' probabilities per case.",
              "rules": rules,
              "cases": {c["id"]: [probs[c["id"]].get(rid) for rid in rules] for c in drafts},
              "doctrine": {c["id"]: dprobs.get(c["id"], {}) for c in drafts}}
    base = os.path.dirname(os.path.abspath(args.out))
    with open(os.path.join(base, "labels.v2.json"), "w", encoding="utf-8") as handle:
        json.dump(labels, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
    for name, rows in (("adjudications.v2.jsonl", adjudications),
                       ("doctrine-adjudications.v2.jsonl", dadj)):
        with open(os.path.join(base, name), "w", encoding="utf-8") as handle:
            for row in sorted(rows, key=lambda a: (a["case"], a["rule"])):
                out = {"case": row["case"], "rule": row["rule"], "gold": bool(row["gold"]),
                       "reason": row["reason"].strip()}
                if "case_binding" in row:
                    out["case_binding"] = row["case_binding"]
                # jev_misfire: the adjudicator overruled a clear Jev score
                # (gold below 0.30, or not gold at 0.75+); the reason says why.
                if "jev_misfire" in row:
                    out["jev_misfire"] = bool(row["jev_misfire"])
                handle.write(json.dumps(out, sort_keys=True) + "\n")
    print(json.dumps({"cases": len(cases), **doc["split"]["counts"],
                      "gold_pairs": sum(len(c["gold"]) for c in cases),
                      "doctrine_pairs": sum(len(c["gold_doctrine"]) for c in cases)}))


def cmd_spot(args, gl):
    doc = _read_json(args.fixture, {})
    probs = _read_json(args.probs, {})
    adjud = {(a["case"], a["rule"]): a for a in _read_adjudications(args.adjudications)}
    rules = {r["id"]: r for r in read_rules(args.corpus)}
    rng = random.Random(args.seed)
    by = {}
    for case in doc["cases"]:
        by.setdefault(case["stratum"], []).append(case)
    picks = []
    strata = sorted(by)
    # Round-robin over strata; alternate gold, adjudicated, and confident-not
    # labels so the sample checks every way a label was reached.
    kinds = ("gold_confident", "adjudicated", "not_confident_but_nearest")
    i = 0
    while len(picks) < args.n:
        stratum = strata[i % len(strata)]
        kind = kinds[(i // len(strata)) % len(kinds)]
        case = rng.choice(by[stratum])
        p = probs.get(case["id"], {})
        if kind == "gold_confident":
            pool = [r for r in case["gold"] if p.get(r, 0) >= gl.YES_AT]
        elif kind == "adjudicated":
            pool = list(case.get("adjudicated") or [])
        else:
            pool = sorted((r for r in p if p[r] <= gl.NO_AT), key=lambda r: -p[r])[:3]
        i += 1
        if not pool:
            continue
        rid = rng.choice(pool)
        a = adjud.get((case["id"], rid))
        picks.append({"case": case["id"], "stratum": stratum, "split": case["split"],
                      "prompt": case["prompt"], "tool_calls": case["tool_calls"],
                      "rule": rid, "rule_statement": rules.get(rid, {}).get("statement"),
                      "label": rid in case["gold"], "how": kind, "p_first": p.get(rid),
                      "adjudication_reason": a and a["reason"]})
    _write_json(args.out, {"note": "25 labels, stratified, for a human spot-check. "
                                   "Mark each 'agree' or 'disagree' with a reason.",
                           "labels": picks})
    print(json.dumps({"spot_check": len(picks), "out": args.out}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cases", required=True, help="drafted cases (.jsonl) or a v2 fixture")
    common.add_argument("--corpus", required=True, help="live corpus {rules:[{id, statement}]}")
    common.add_argument("--probs", required=True, help="first-pass probabilities (JSON)")
    common.add_argument("--calls-log", default=DEFAULT_CALLS)
    common.add_argument("--workers", type=int, default=4)
    sub.add_parser("label", parents=[common])
    p = sub.add_parser("second-pass", parents=[common])
    p.add_argument("--second", required=True)
    p = sub.add_parser("doctrine-catalog", parents=[common])
    p.add_argument("--catalog", required=True, help="output: private section catalog (JSON)")
    p = sub.add_parser("doctrine-search", parents=[common])
    p.add_argument("--search", required=True, help="{case id: [doctrine refs]} (JSON)")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("doctrine-label", parents=[common])
    p.add_argument("--catalog", required=True,
                   help="[{ref, doc, doc_title, title, text}] for every live section")
    p.add_argument("--search", required=True)
    p.add_argument("--doctrine-probs", required=True)
    p = sub.add_parser("borderlines", parents=[common])
    p.add_argument("--second", required=True)
    p.add_argument("--signals", default=os.path.join(
        REPO, "ops", "fixtures", "rule-delivery-eval", "action-signals.v2.json"))
    p.add_argument("--extended-rules", required=True)
    p.add_argument("--floors", default=os.path.join(
        REPO, "ops", "fixtures", "rule-delivery-eval", "review-floors.v2.json"))
    p.add_argument("--out", required=True)
    p = sub.add_parser("build", parents=[common])
    p.add_argument("--adjudications", required=True)
    p.add_argument("--signals", default=os.path.join(
        REPO, "ops", "fixtures", "rule-delivery-eval", "action-signals.v2.json"),
        help="action-signal table (JSON)")
    p.add_argument("--extended-rules", required=True,
                   help="JSON list of rule ids reviewed down to REVIEW_LOW_EXTENDED")
    p.add_argument("--floors", default=os.path.join(
        REPO, "ops", "fixtures", "rule-delivery-eval", "review-floors.v2.json"),
                   help="per-rule review floors (JSON); ignored if the file is absent")
    p.add_argument("--out", required=True)
    p.add_argument("--seed", default=None)
    p.add_argument("--labelled-on", default=None)
    p.add_argument("--doctrine-probs", default=None)
    p.add_argument("--doctrine-adjudications", default=None)
    p = sub.add_parser("spot-check", parents=[common])
    p.add_argument("--fixture", required=True)
    p.add_argument("--adjudications", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--n", type=int, default=25)
    p.add_argument("--seed", default="spot-check")
    args = parser.parse_args(argv)

    gl = _load("rule_gold_label")
    if getattr(args, "seed", None) is None and args.cmd == "build":
        args.seed = gl.DEFAULT_SEED
    if args.cmd in ("label", "second-pass", "doctrine-label"):
        tsc = _load("typesafe_client")
        return {"label": cmd_label, "second-pass": cmd_second,
                "doctrine-label": cmd_doctrine_label}[args.cmd](args, gl, tsc) or 0
    handler = {"borderlines": cmd_borderlines, "build": cmd_build, "spot-check": cmd_spot,
               "doctrine-search": cmd_doctrine_search,
               "doctrine-catalog": cmd_doctrine_catalog}
    return handler[args.cmd](args, gl) or 0


if __name__ == "__main__":
    sys.exit(main())
