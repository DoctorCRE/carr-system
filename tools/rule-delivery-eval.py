#!/usr/bin/env python3
"""rule-delivery-eval.py — replay labelled cases through every rule-delivery
path and report precision, recall and F1 per path and for the system.

The logic lives in ops/rule_delivery_eval.py (a library, for the reason
ops/typesafe_client.py documents); this is its command line.

    tools/rule-delivery-eval.py                       # committed synthetic fixture, Jev off
    tools/rule-delivery-eval.py --cases ops/fixtures/rule-delivery-eval/cases.v2.json \
        --split train                                 # the v2 benchmark, train split only
    tools/rule-delivery-eval.py --cases out/rule-delivery-eval/gold.jsonl \\
        --jev live --workers 4 --corpus out/rule-delivery-eval/corpus-live.json

DRY RUN ALWAYS. No production log, cache or audit file is written (see the
library docstring); the only files written are report.json and report.md in
--out-dir. With --jev live the Jev-backed paths make real Jev requests (about
29 per human case) whose call receipts go to --calls-log (default: nowhere).

Real labelled situations contain partners' words and must stay under out/
(gitignored). The committed fixture under ops/fixtures/ is synthetic.
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "ops", "fixtures", "rule-delivery-eval", "cases.v1.json")


def _library():
    import importlib.util
    path = os.path.join(REPO, "ops", "rule_delivery_eval.py")
    spec = importlib.util.spec_from_file_location("rule_delivery_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gold_label():
    import importlib.util
    path = os.path.join(REPO, "ops", "rule_gold_label.py")
    spec = importlib.util.spec_from_file_location("rule_gold_label", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _classes(meta):
    """{rule id: always_on|action_point|topic|gate_named}, by the ordered
    questions in ops/rule_gold_label.py."""
    return _gold_label().rule_classes(REPO, list(meta))


def _groups(meta):
    """{rule id: guidance_deferred|other}: the reporting split for the rules
    audits/guidance-migration-manifest.v1.tsv retyped as deferred guidance."""
    return _gold_label().rule_groups(REPO, list(meta))


def _doctrine_labelled(cases_path):
    """{case id: set of doctrine refs its labellers judged} from the labels file
    committed beside a v2 cases file (its `doctrine` shortlist probabilities),
    or None when there is none. A delivered ref outside it is set aside."""
    path = os.path.join(os.path.dirname(os.path.abspath(cases_path)), "labels.v2.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        doctrine = json.load(handle).get("doctrine")
    if not isinstance(doctrine, dict):
        return None
    return {cid: set(refs) for cid, refs in doctrine.items()}


def _is_v2(path, ev):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            head = json.load(handle)
    except ValueError:
        return False
    return isinstance(head, dict) and head.get("schema") == ev.CASES_SCHEMA_V2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default=FIXTURE)
    parser.add_argument("--jev", choices=("off", "live"), default="off")
    parser.add_argument("--workers", type=int, default=1,
                        help="cases judged concurrently on the Jev-backed paths")
    parser.add_argument("--corpus", help="live corpus JSON ({rules:[{id, statement}]}) "
                        "for one-line summaries and untagged live ids")
    parser.add_argument("--calls-log", default=os.devnull,
                        help="where Jev call receipts go (default: discarded)")
    parser.add_argument("--out-dir", default=os.path.join(REPO, "out", "rule-delivery-eval"))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--split", choices=("train", "test", "all"),
                        help="which split of a v2 cases file to score. Defaults to test "
                        "for a v2 file (the held-out numbers); tuning reads train only; "
                        "'all' scores both. A v1 file has no split.")
    parser.add_argument("--doctrine-search", action="store_true",
                        help="also run the doctrine search door (search-doctrine through "
                        "./run.sh call, read-only) as a doctrine-only path")
    parser.add_argument("--rescore", help="a saved report.json: re-score its deliveries "
                        "against --cases instead of running the paths again (no Jev)")
    args = parser.parse_args(argv)

    ev = _library()
    if args.split is None and _is_v2(args.cases, ev):
        args.split = "test"
    if args.split == "all":
        args.split = None
    cases = ev.load_cases(args.cases, split=args.split)
    statements, live_ids = {}, []
    if args.corpus:
        with open(args.corpus, "r", encoding="utf-8") as handle:
            rows = json.load(handle).get("rules") or []
        statements = {row["id"]: row.get("statement") or "" for row in rows}
        live_ids = list(statements)
    else:
        with open(os.path.join(REPO, "ops", "config", "rule-selection-corpus.v1.json"),
                  "r", encoding="utf-8") as handle:
            statements = {row["id"]: row.get("statement") or ""
                          for row in json.load(handle).get("rules") or []}
    meta = ev.rule_meta(REPO, corpus=live_ids)
    if args.rescore:
        with open(args.rescore, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        deliveries, errors = ev.deliveries_from_report(saved), saved.get("errors") or {}
        jev_mode = saved.get("jev")
    else:
        adapters = ev.build_adapters(REPO, jev=args.jev, calls_log=args.calls_log,
                                     doctrine_search=args.doctrine_search)
        deliveries, errors = ev.run_adapters(cases, adapters, workers=max(1, args.workers))
        jev_mode = args.jev
    ev.add_system_rows(deliveries)
    report = ev.score(cases, deliveries,
                      ev.universes(meta, list(deliveries),
                                   boot_ids=ev.boot_always_on_ids(REPO)), meta,
                      labelled=set(live_ids) if live_ids else None,
                      classes=_classes(meta), groups=_groups(meta),
                      doctrine_labelled=_doctrine_labelled(args.cases),
                      doctrine_paths=set(ev.DOCTRINE_PATHS) & set(deliveries))
    report["split"] = args.split
    report["dry_run"] = True
    report["jev"] = jev_mode
    report["errors"] = {name: errs for name, errs in errors.items() if errs}
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
    markdown = ev.render_markdown(report, statements, top=args.top)
    with open(os.path.join(args.out_dir, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(markdown)
    print(markdown)
    if report["errors"]:
        print("adapter errors (scored as empty delivery):",
              json.dumps({k: len(v) for k, v in report["errors"].items()}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
