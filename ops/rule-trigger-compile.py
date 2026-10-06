#!/usr/bin/env python3
"""rule-trigger-compile.py — compile ops/config/rule-jev-triggers.v1.json.

Jev is asked once per pack-layer rule (ops/rule_trigger_compile.py explains
what and why); run-time delivery then matches the stored triggers with no Jev
call. Afterwards run ops/rule-jit-compile.py so the trigger table picks the
rows up.

USAGE:
  ops/rule-trigger-compile.py --changed    # compile only rules whose statement
                                           # digest changed, or that are new;
                                           # drop rules no longer pack-layer
  ops/rule-trigger-compile.py --backfill   # recompile every pack-layer rule
  ops/rule-trigger-compile.py --check      # offline: fail when any rule is
                                           # uncompiled, stale or undeliverable
  ops/rule-trigger-compile.py --preview ID # print one rule's candidates; no Jev

Every compile mode spends exactly one Jev request per rule it compiles and
prints the count. --check needs no credential and is what CI runs.
"""
import argparse
import importlib.util
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--changed", action="store_true")
    mode.add_argument("--backfill", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--preview", metavar="RULE_ID")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--history", default=None,
                        help="selector log to mine candidates from (default: the "
                             "canonical checkout's out/jev-rule-select.jsonl)")
    parser.add_argument("--history-before", type=int, default=None,
                        help="use only log rows before this index (held-out replay)")
    args = parser.parse_args(argv)

    rtc = _load("rule_trigger_compile")
    corpus = rtc.load_json(rtc.CORPUS_PATH)
    emap = rtc.load_json(rtc.MAP_PATH)
    rules = rtc.pack_rules(corpus, emap)
    doc = rtc.load_compiled()

    if args.check:
        problems = rtc.coverage_problems(doc, rules)
        if doc is None:
            problems.insert(0, f"{rtc.OUTPUT_PATH} is missing or malformed")
        elif os.path.exists(rtc.OUTPUT_PATH):
            with open(rtc.OUTPUT_PATH, "rb") as handle:
                if handle.read() != rtc.canonical_bytes(doc):
                    problems.append("file is not in canonical form (hand-edited?)")
        if problems:
            print("rule-trigger-compile --check: FAIL")
            for line in problems[:40]:
                print(f"  {line}")
            print("  THE MOVE: ops/rule-trigger-compile.py --changed, then "
                  "ops/rule-jit-compile.py, and commit both files")
            return 1
        counts = doc["counts"]
        print(f"rule-trigger-compile --check: OK — {counts['rules']} pack-layer rules "
              f"compiled against their current text ({json.dumps(counts, sort_keys=True)})")
        return 0

    pack_keywords = {name: pack.get("triggers", []) for name, pack in
                     (emap.get("rule_packs") or {}).items()}
    verbs = rtc.known_verbs()
    history = rtc.load_history(args.history or rtc.canonical_history_log(),
                               before=args.history_before)
    by_id = {r["id"]: r for r in rules}

    if args.preview:
        rule = by_id.get(args.preview)
        if rule is None:
            print(f"{args.preview} is not an active pack-layer rule")
            return 1
        cands, near = rtc.candidates(rule, all_rules=rules, pack_keywords=pack_keywords,
                                     verbs=verbs, history=history)
        for kind, value, origin in cands:
            print(f"  {kind:8} {origin:28} {value}")
        print(f"  near-miss probes: {near}")
        return 0

    todo = sorted(by_id) if args.backfill else rtc.stale_or_missing(doc, rules)
    kept = {rid: entry for rid, entry in ((doc or {}).get("rules") or {}).items()
            if rid in by_id and rid not in todo}
    if not todo:
        print("rule-trigger-compile: nothing to compile; 0 Jev requests")
        if doc is not None and set((doc.get("rules") or {})) != set(kept):
            with open(rtc.OUTPUT_PATH, "wb") as handle:
                handle.write(rtc.canonical_bytes(rtc.document(list(kept.values()))))
        return 0

    sys.path.insert(0, os.path.join(REPO, "ops"))
    import typesafe_client as tsc  # noqa: E402
    import concurrent.futures as cf

    def one(rid):
        return rtc.compile_rule(by_id[rid], all_rules=rules, pack_keywords=pack_keywords,
                                verbs=verbs, history=history, client=tsc, ask=tsc.ask)

    compiled, failed = dict(kept), []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(one, rid): rid for rid in todo}
        for future in cf.as_completed(futures):
            rid = futures[future]
            try:
                compiled[rid] = future.result()
            except Exception as exc:  # the id is enough; the error may carry a URL
                failed.append(rid)
                print(f"  {rid}: compile failed ({type(exc).__name__})", file=sys.stderr)
    with open(rtc.OUTPUT_PATH, "wb") as handle:
        handle.write(rtc.canonical_bytes(rtc.document(list(compiled.values()))))
    print(f"rule-trigger-compile: {len(todo)} Jev requests, {len(todo) - len(failed)} "
          f"compiled, {len(failed)} failed; wrote {rtc.OUTPUT_PATH}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
