"""Score the structural partition against the regex scanner on labelled code.

THE QUESTION THIS ANSWERS is not "how many findings", because a finding count
is never proof of value: a generator that sends forty times more code to the
judge will produce more findings whether or not it finds more real problems.
It answers, per candidate generator, over the same hand-labelled corpus:

  coverage recall   of the gold-positive (item, question) pairs, how many sit
                    inside a region the generator sends at all. Deterministic,
                    offline, and the ceiling on judged recall: the judgment
                    cannot rule on lines it is never shown.
  precision/recall  of the JUDGED result, the region's score for the labelled
                    question against REPORT_AT. Needs the live Jev key; the
                    offline run reports these as not measured, never as zero.
  duplicate rate    regions that repeat another region's text, or overlap at
                    least half their lines with an earlier region in the same
                    file. Each duplicate is a request that learns nothing new.
  p95               partition seconds per file offline; judgment seconds per
                    request live.
  token cost        requests x characters per request, estimated at
                    CHARS_PER_TOKEN offline and taken from the vendor's own
                    `usage` live when the response carries it.

THE CORPUS is ops/fixtures/jev-code-review-pilot/corpus.v1.json: real
locations in tracked CARR files, each located by the exact text of one line
so a drifted file reports the item as stale instead of scoring the wrong
lines. Library only (no shebang, no main guard; see ops/typesafe_client.py).
The live measurement command is in cli()'s docstring.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO, "ops", "fixtures", "jev-code-review-pilot", "corpus.v1.json")
REPORT = os.path.join(REPO, "ops", "fixtures", "jev-code-review-pilot", "offline-report.v1.json")
OVERLAP_DUPLICATE = 0.5


def _read(path):
    with open(path, encoding="utf-8", errors="ignore") as handle:
        return handle.read()


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def modules():
    return (_load("jev_code_review_for_eval", "ops/jev_code_review.py"),
            _load("jev_code_partition_for_eval", "ops/jev_code_partition.py"))


def load_corpus(path=CORPUS, repo=REPO, reader=None):
    """Items with their current line resolved from `anchor`; an item whose
    anchor no longer matches is kept with line None and `stale` True."""
    data = json.loads(_read(path))
    read = reader or (lambda rel: _read(os.path.join(repo, rel)))
    items = []
    for item in data["items"]:
        item = dict(item)
        try:
            lines = read(item["path"]).splitlines()
        except OSError:
            lines = []
        hits = [k + 1 for k, text in enumerate(lines) if text == item["anchor"]]
        nth = int(item.get("occurrence") or 1)
        item["line"] = hits[nth - 1] if len(hits) >= nth else None
        item["stale"] = item["line"] is None
        items.append(item)
    return data, items


def span(region, context_before=None):
    """(start, end) of the lines a region actually sends. Partition regions
    carry end_line; a regex-scanner region is `line` minus its context,
    through as many lines as its (possibly truncated) code holds."""
    if "end_line" in region:
        return region.get("start_line", region["line"]), region["end_line"]
    before = 14 if context_before is None else context_before
    start = max(1, region["line"] - before)
    return start, start + region["code"].count("\n")


def covering(item, regions):
    def contains(location):
        start, end = span(location)
        return (location["path"] == item["path"]
                and start <= item["line"] <= end
                and item["line"] <= location.get("sent_end_line", end))

    return [r for r in regions if contains(r) or any(
        contains(location) for location in r.get("also_at_spans", ()))]


def duplicate_rate(regions, digest):
    """Share of regions that add no new lines: identical text to an
    earlier region anywhere, or at least half their lines already inside an
    earlier region in the same file."""
    if not regions:
        return 0.0
    seen_text, seen_lines, dup = set(), {}, 0
    for r in regions:
        start, end = span(r)
        lines = set(range(start, end + 1))
        prior = seen_lines.setdefault(r["path"], set())
        key = digest(r["code"])
        if key in seen_text or len(lines & prior) >= OVERLAP_DUPLICATE * len(lines):
            dup += 1
        seen_text.add(key)
        prior |= lines
    return dup / len(regions)


def coverage(items, regions):
    """Coverage recall over gold positives, and how many labelled negatives
    the generator would spend a request reading."""
    pos = pos_hit = neg = neg_sent = 0
    missed = []
    for item in items:
        if item["stale"]:
            continue
        sent = bool(covering(item, regions))
        for qid, gold in item["labels"].items():
            if gold:
                pos += 1
                pos_hit += sent
                if not sent:
                    missed.append(f'{item["id"]}:{qid}')
            else:
                neg += 1
                neg_sent += sent
    return {"gold_positives": pos, "positives_sent": pos_hit,
            "coverage_recall": (pos_hit / pos) if pos else None,
            "labelled_negatives": neg, "negatives_sent": neg_sent,
            "positives_never_sent": missed}


def judged(items, results, floor):
    """Precision and recall of judged scores against the gold labels. An
    item's prediction for a question is the highest score any covering region
    gave it; an item no region covers is a predicted no. Rows that errored are
    counted separately and never read as a no."""
    tp = fp = fn = tn = 0
    errors, per_q = 0, {}
    for item in items:
        if item["stale"]:
            continue
        cover = covering(item, results)
        if any("_error" in (r.get("scores") or {}) for r in cover):
            errors += 1
            continue
        for qid, gold in item["labels"].items():
            values = [r["scores"].get(qid) for r in cover
                      if isinstance((r.get("scores") or {}).get(qid), (int, float))]
            pred = bool(values) and max(values) >= floor
            q = per_q.setdefault(qid, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
            cell = ("tp" if pred else "fn") if gold else ("fp" if pred else "tn")
            q[cell] += 1
            tp, fp, fn, tn = (tp + (cell == "tp"), fp + (cell == "fp"),
                              fn + (cell == "fn"), tn + (cell == "tn"))
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "items_with_errors": errors,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "per_question": per_q}


def usage_tokens(usage):
    """Total tokens from a vendor usage block, or None when it names none."""
    if not isinstance(usage, dict):
        return None
    if isinstance(usage.get("total_tokens"), (int, float)):
        return usage["total_tokens"]
    parts = [v for k, v in usage.items()
             if k.endswith("_tokens") and isinstance(v, (int, float))]
    return sum(parts) if parts else None


def cost(regions, partition_mod, questions):
    chars = [partition_mod.request_chars(r, questions) for r in regions]
    return {"requests": len(regions), "request_chars_total": sum(chars),
            "estimated_tokens": round(sum(chars) / partition_mod.CHARS_PER_TOKEN),
            "estimate_basis": f"{partition_mod.CHARS_PER_TOKEN} chars per token; an estimate, not a vendor count"}


def generators(paths, jcr, part):
    t0 = time.perf_counter()
    regex = jcr.regions(paths)
    regex_seconds = time.perf_counter() - t0
    structural, stats = part.partition(paths)
    return {"regex_scanner": (regex, {"seconds_total": regex_seconds}),
            "structural_partition": (structural, stats)}


def offline_report(corpus_path=CORPUS, whole_tree=True):
    """Everything measurable without a key. Judged metrics are null with the
    reason, never zero."""
    jcr, part = modules()
    data, items = load_corpus(corpus_path)
    paths = sorted({i["path"] for i in items})
    report = {"schema": "carr-jev-code-review-pilot-report.v1",
              "corpus": os.path.relpath(corpus_path, REPO),
              "corpus_pinned_commit": data.get("pinned_commit"),
              "items": len(items), "stale_items": [i["id"] for i in items if i["stale"]],
              "report_at": jcr.REPORT_AT, "scope": {}, "judged": {
                  "status": "not_measured",
                  "reason": "needs the TypeSafe key; run the live command in "
                            "ops/jev_code_pilot_eval.py cli()"}}
    scopes = {"corpus_files": paths}
    if whole_tree:
        scopes["whole_tree"] = part.tracked_sources()
    for scope, scope_paths in scopes.items():
        out = {"files": len(scope_paths)}
        for name, (regions, stats) in generators(scope_paths, jcr, part).items():
            row = {"regions": len(regions),
                   "duplicate_rate": round(duplicate_rate(regions, part.digest), 4),
                   **cost(regions, part, jcr.QUESTIONS)}
            if name == "structural_partition":
                row["partition"] = {k: v for k, v in stats.items()
                                    if not k.startswith("partition_seconds")}
                row["partition_seconds_p95_per_file"] = stats["partition_seconds_p95"]
            else:
                row["scan_seconds_total"] = round(stats["seconds_total"], 3)
            if scope == "corpus_files":
                row["coverage"] = coverage(items, regions)
            out[name] = row
        report["scope"][scope] = out
    return report


def live_report(corpus_path=CORPUS, *, client=None, api_key=None, workers=4):
    """The judged half: both generators' corpus-file regions through the same
    batched judgment. Regions both generators send identically are judged
    once and the answer reused, so the comparison costs no double spend."""
    jcr, part = modules()
    data, items = load_corpus(corpus_path)
    paths = sorted({i["path"] for i in items})
    report = offline_report(corpus_path, whole_tree=False)
    report["judged"] = {"status": "not_measured"}
    cache = {}
    for name, (regions, _stats) in generators(paths, jcr, part).items():
        todo = [r for r in regions if (r["path"], r["code"]) not in cache]
        for row in part.review(todo, client=client, api_key=api_key, workers=workers):
            cache[(row["path"], row["code"])] = row
        results = [dict(r, **{k: cache[(r["path"], r["code"])][k]
                              for k in ("scores", "usage", "seconds")}) for r in regions]
        tokens = [usage_tokens(r["usage"]) for r in results]
        errors = sum("_error" in (r["scores"] or {}) for r in results)
        accuracy = judged(items, results, jcr.REPORT_AT)
        status = ("not_measured" if not results or errors == len(results) else
                  "incomplete" if errors else "measured")
        if status != "measured":
            accuracy.update({"tp": None, "fp": None, "fn": None, "tn": None,
                             "precision": None, "recall": None, "per_question": None})
        report["judged"][name] = {
            **accuracy, "status": status,
            "requests": len(results),
            "request_seconds_p95": part.percentile([r["seconds"] for r in results], 95),
            "vendor_tokens_total": (sum(t for t in tokens if t is not None)
                                    if any(t is not None for t in tokens) else None),
            "vendor_tokens_reported_for": sum(t is not None for t in tokens),
            "errors": errors}
    statuses = [report["judged"][name]["status"]
                for name in ("regex_scanner", "structural_partition")]
    report["judged"]["status"] = ("measured" if all(s == "measured" for s in statuses)
                                  else "not_measured" if all(s == "not_measured" for s in statuses)
                                  else "incomplete")
    if report["judged"]["status"] != "measured":
        report["judged"]["reason"] = "one or more judge requests failed or no regions were sent"
    return report


def _pct(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def render(report):
    """A short plain-text table for a terminal or a PR body."""
    out = []
    for scope, row in report["scope"].items():
        out.append(f"{scope} ({row['files']} files)")
        out.append("  generator              regions  dup-rate  est-tokens  coverage-recall")
        for name in ("regex_scanner", "structural_partition"):
            g = row[name]
            cov = g.get("coverage", {}).get("coverage_recall")
            out.append(f"  {name:<22} {g['regions']:>7}  {_pct(g['duplicate_rate']):>8}"
                       f"  {g['estimated_tokens']:>10}  {_pct(cov) if 'coverage' in g else '-':>15}")
    j = report["judged"]
    if j.get("status") == "not_measured" and "regex_scanner" not in j:
        out.append(f"judged precision/recall: not measured ({j.get('reason')})")
    else:
        for name in ("regex_scanner", "structural_partition"):
            g = j[name]
            out.append(f"{name}: {g['status']} precision {_pct(g['precision'])} recall {_pct(g['recall'])}"
                       f" p95 {g['request_seconds_p95']}s vendor tokens {g['vendor_tokens_total']}"
                       f" errors {g['errors']}")
    return "\n".join(out)


def cli(argv):
    """Offline:  python3 -c 'import sys; sys.path.insert(0, "ops"); import jev_code_pilot_eval as e; e.cli(sys.argv[1:])'
    Live:     the same command with --live (reads ~/.config/carr/typesafe.env,
              one request per distinct corpus-file region, both generators).
    --out PATH writes the JSON report; the table goes to stdout."""
    live = "--live" in argv
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    report = live_report() if live else offline_report()
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
            handle.write("\n")
    sys.stdout.write(render(report) + "\n")
    return report
