#!/usr/bin/env python3
"""Jev value report: what Jev costs against the outcomes it can prove it changed.

Read-only and deterministic: no model calls, no record-layer calls. Reads
out/jev-calls.jsonl (the billed-call log), out/jev-judge.jsonl (per-check
verdicts), out/jev-required-actions-gate.jsonl, local git history, the price
in ops/config/jev-cost-guard.v1.json, and an optional GitHub CI snapshot that
`--fetch-ci` saves through `gh api` (REST) to out/jev-value-ci-snapshot.json.

THE COUNTING RULES:
  * Only billed-call receipts establish spend. Unique server_receipt_id /
    receipt_id joins attribute those receipts to judge kinds. Unlinked judge
    rows are observations, never extra spend or a subtraction from the hub.
  * Typed pre-call refusals and deterministic prefilters are free. Receipts
    with token usage are measured; other failures have unknown billing.
  * Input dollars are measured tokens at the configured input rate. Missing,
    unreadable or partial call evidence and unknown billing prevent complete
    cost estimates. Output dollars and agent tokens saved are unmeasured.
  * Positive fix-commit attribution is a proxy for value, not a causal audit:
    Jev <finder verb> <defect noun>, or fixes <defect noun> Jev <finder verb>.
    Inspect subject and body and reject negative/non-fix claims. Commit
    windows use committer (delivery) timestamps, matching git log's selection.
  * CI value proxies require a complete run_started_at window of retained
    attempt history. Low = one median round per attributed finding; high =
    one median PR's CI rounds. Neither is measured time saved.
  * Fixtures require explicit provenance; missing provenance stays unknown.

Usage: tools/jev-value-report.py [--days 7 | --since D --until D] [--json]
       tools/jev-value-report.py --fetch-ci [--days 14]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE_ROOT))
from lib.github_reader import GitHubReader, GitHubUnreadable  # noqa: E402
PRICE_CONFIG = SOURCE_ROOT / "ops" / "config" / "jev-cost-guard.v1.json"
CI_SNAPSHOT = "jev-value-ci-snapshot.json"
FETCH_HINT = "run tools/jev-value-report.py --fetch-ci to save GitHub CI run history"
OUTPUT_PRICE_SOURCE = ("ops/config/jev-cost-guard.v1.json carries only "
                       "price_usd_per_million_input_tokens; the output rate belongs beside it, "
                       "taken from the TypeSafe invoice or its price_source_url")
TOKENS_SAVED_SOURCE = ("not measured: needs per-session Claude usage from transcripts, "
                       "compared across the Jev-off holdout")

# Shared normalization for call receipts and judge observations. Only typed
# pre-call failures prove zero spend; transport/vendor failures are unknown.
NO_CALL_ERROR = re.compile(
    r"HTTP 40[23]\b|HTTP 429\b|daily[ _]paid[ _]call[ _]cap|billing_exhausted|"
    r"(?:^|:)(?:network|auth_failed|holdout|rate_limited)$|missing[ _-]credential|"
    r"credential.*(?:missing|cannot|not found)|cannot read.*credential|"
    r"synthetic network|could not reach", re.I)

# What each call site's question is, which decides the recommendation when
# no outcome can be proven. mechanical: the inputs already hold the answer and
# code can compute it. per_turn: fires on routine turns. judgment_point:
# asked at a decision. Fixture classification comes only from provenance. Unlisted names fall into review / ad_hoc_named by name.
NATURE = {
    "judge:supervise.stuck_and_drift": "mechanical",
    "judge:supervise.test_picker": "mechanical",
    "judge:supervise.path_repair": "mechanical",
    "judge:supervise.planted_instruction": "mechanical",
    "judge:supervise.review_triage": "mechanical",
    "judge:supervise.bug_locator": "mechanical",
    "judge:command_handoff": "mechanical",
    "judge:build_advisory": "per_turn",
    "judge:rule_select": "per_turn",
    "judge:supervise.tool_boundary": "per_turn",
    "judge:supervise.failure_triage": "per_turn",
    "judge:supervise.done_claim": "per_turn",
    "judge:supervise.stop_boundary": "per_turn",
    "judge:supervise.test_quality": "per_turn",
    "judge:requirement_checklist": "per_turn",
    "judge:executor_tier": "per_turn",
    "judge:post_write_task_fit": "per_turn",
    "judge:post_write_task_fit_shadow": "per_turn",
    "judge:unattributed": "per_turn",
    "jev_build_advisory": "per_turn",
    "jev_change_tolls": "per_turn",
    "legacy_unattributed": "per_turn",
    "judge:supervise.escalation_router": "judgment_point",
    "judge:supervise.effort_picker": "judgment_point",
    "judge:supervise.best_of": "judgment_point",
    "judge:supervise.ambiguity_stop": "judgment_point",
    "judge:supervise.example_picker": "judgment_point",
    "judge:supervise.context_picker": "judgment_point",
    "judge:supervise.plan_split": "judgment_point",
    "judge:supervise.notebook_recall": "judgment_point",
    "judge:supervise.runaway_thinking": "judgment_point",
    "review": "judgment_point",
    "jev_deal_read": "judgment_point",
    "ad_hoc_named": "judgment_point",
    "gate:jev_required_actions": "gate",
}
REVIEWISH = re.compile(r"review|diagnos|evidence|confirm|triage|defect", re.I)
JEV_FINDER = re.compile(r"\bJev(?:'s\s+[\w-]+)?\s+(?:flagged|caught|found|spotted|identified|surfaced|noticed)\b")
DEFECT_NOUN = r"(?:bug|gap|defect|error|regression)"
Z_ALPHA, Z_BETA = 1.959964, 0.841621  # two-sided 5%, 80% power
EFFECT_SHARE = 0.25  # detect a 25% change in mean CI rounds per PR


def site_for(name, judge_kind=False):
    if judge_kind and f"judge:{name}" in NATURE:
        return f"judge:{name}"
    if not judge_kind:
        if name is None:
            return "legacy_unattributed"
        if name == "jev_code_review":
            return "review"
        if name in NATURE:
            return name
    return "review" if REVIEWISH.search(name or "") else "ad_hoc_named"


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def in_window(value, start, end):
    stamp = parse_time(value)
    return stamp is not None and start <= stamp < end


def _int(value):
    return value if type(value) is int and value >= 0 else None


def _new_site():
    return {"attempts": 0, "cache_hits": 0, "not_billed": 0, "paid": 0, "usable": 0,
            "billing_unknown": 0, "input_tokens": 0,
            "output_tokens": 0, "wait_ms": 0, "observations": 0, "observation_cache_hits": 0,
            "outcomes_verified": 0, "blocks": 0, "evidence": [],
            "traffic_class": "unknown", "attribution_complete": True, "note": None}


def _billing(row, tokens_in):
    if row.get("cache_hit") is True:
        return "cache"
    if (row.get("holdout") is True or row.get("note") == "deterministic_prefilter"
            or NO_CALL_ERROR.search(str(row.get("error") or ""))):
        return "not_billed"
    return "measured" if tokens_in is not None else "unknown"


def _fields(row, *, judge=False):
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    tokens_in, tokens_out = _int(row.get("input_tokens")), _int(row.get("output_tokens"))
    if tokens_in is None:
        tokens_in, tokens_out = _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))
    usable = (not row.get("error") and bool(row.get("answers")) if judge
              else row.get("usable", row.get("ok")) is True)
    return {"billing": _billing(row, tokens_in), "usable": usable,
            "tokens_in": tokens_in, "tokens_out": tokens_out}


def _judge_fields(row):
    return _fields(row, judge=True)


def _tally(site, *, billing, usable, tokens_in, tokens_out):
    if billing == "cache":
        site["cache_hits"] += 1
        return
    site["attempts"] += 1
    if billing == "not_billed":
        site["not_billed"] += 1
    elif billing == "unknown":
        site["billing_unknown"] += 1
    else:
        site["paid"] += 1
        site["usable"] += bool(usable)
        site["input_tokens"] += tokens_in
        site["output_tokens"] += tokens_out or 0


def _traffic(row):
    # Explicit provenance only. A production-capable kind is not a fixture.
    ref = row.get("subject_ref")
    provenance = row.get("traffic_class")
    if provenance is None and isinstance(ref, dict):
        provenance = ref.get("traffic_class")
        if provenance is None and ref.get("fixture") is True:
            provenance = "fixture"
    return provenance if provenance in ("fixture", "production") else "unknown"


def _site_key(base, traffic):
    return base if traffic == "unknown" else f"{base} [{traffic}]"


def ci_coverage(ci, start, end):
    if not isinstance(ci, dict):
        return "CI history absent"
    coverage = ci.get("coverage") or {}
    began, ended, fetched = (parse_time(coverage.get("start")),
                             parse_time(coverage.get("end")), parse_time(ci.get("fetched_at")))
    if (coverage.get("complete") is not True or coverage.get("time_basis") != "run_started_at"
            or not began or not ended or not fetched or began > start or ended < end or fetched < end):
        return "CI coverage incomplete: snapshot does not cover this run_started_at window"
    return None


def baseline(ci, commits, start, end):
    base = {"ci_round_minutes_median": None, "ci_rounds_per_pr_median": None,
            "ci_rounds_per_pr_mean": None, "ci_rounds_per_pr_sd": None, "ci_failed_rounds_per_pr_mean": None,
            "prs_with_ci": 0, "merged_prs": None, "time_to_merge_hours_median": None,
            "reverts": sum(1 for c in commits if (c.get("subject") or "").startswith("Revert")),
            "main_canary_failures": None, "holdout_n_per_arm": None,
            "coverage_issue": ci_coverage(ci, start, end)}
    if base["coverage_issue"]:
        return base
    runs = [r for r in ci.get("runs") or [] if in_window(r.get("run_started_at"), start, end)]
    rounds, minutes, failed = defaultdict(int), [], defaultdict(int)
    seen = set()
    for run in runs:
        if run.get("id") is not None:
            key = (run["id"], run.get("run_attempt"))
            if key in seen:
                continue
            seen.add(key)
        if run.get("name") != "CI" or run.get("event") != "pull_request":
            continue
        if run.get("conclusion") not in ("success", "failure", "timed_out", "action_required", "startup_failure"):
            continue
        began, ended = parse_time(run.get("run_started_at")), parse_time(run.get("updated_at"))
        if began and ended and ended >= began:
            minutes.append((ended - began).total_seconds() / 60)
        rounds[run.get("head_branch")] += 1
        failed[run.get("head_branch")] += run["conclusion"] != "success"
    counts = list(rounds.values())
    if minutes:
        base["ci_round_minutes_median"] = round(statistics.median(minutes), 1)
    if counts:
        base["prs_with_ci"] = len(counts)
        base["ci_rounds_per_pr_median"] = statistics.median(counts)
        base["ci_rounds_per_pr_mean"] = statistics.fmean(counts)
        base["ci_failed_rounds_per_pr_mean"] = round(statistics.fmean(failed.values()), 2)
    if len(counts) >= 2:
        sd = statistics.stdev(counts)
        base["ci_rounds_per_pr_sd"] = round(sd, 3)
        delta = EFFECT_SHARE * base["ci_rounds_per_pr_mean"]
        if sd > 0 and delta > 0:
            base["holdout_n_per_arm"] = math.floor(2 * (Z_ALPHA + Z_BETA) ** 2 * sd ** 2 / delta ** 2) + 1
    base["main_canary_failures"] = sum(1 for r in runs if "canary" in (r.get("name") or "").lower()
                                       and r.get("conclusion") == "failure")
    merged = [p for p in ci.get("pulls") or [] if in_window(p.get("merged_at"), start, end)]
    base["merged_prs"] = len(merged)
    hours = [(parse_time(p["merged_at"]) - parse_time(p["created_at"])).total_seconds() / 3600
             for p in merged if parse_time(p.get("created_at"))]
    if hours:
        base["time_to_merge_hours_median"] = round(statistics.median(hours), 2)
    return base


def recommend(key, site):
    if site["outcomes_verified"]:
        return "keep"
    nature = NATURE.get(key.split(" [", 1)[0], "judgment_point")
    if site["traffic_class"] == "fixture":
        return "remove"
    return {"mechanical": "replace with code",
            "per_turn": "move to a judgment point", "gate": "move to a judgment point"
            }.get(nature, "keep (unproven; holdout decides)")


def positive_attribution(commit):
    """Commit-message convention: Jev <finder verb> <positive defect noun>.

    Search subject and body for a positive defect claim. Any negation inside
    the claim's sentence denies it, and so does a denial of the claimed
    defect noun anywhere in subject or body ("found no bug"). Negating a
    different noun ("no regression" after a fixed bug) is validation prose and
    keeps the claim. Earlier non-detection also keeps a later positive claim.
    Messages denying or deferring a fix remain excluded. This
    is commit-attributed evidence, not an independently verified causal outcome.
    """
    message = "\n".join(str(commit.get(k) or "") for k in ("subject", "body"))
    if re.search(r"\b(?:no\s+(?:fix|change)|not\s+fixed|defer(?:red)?|unfixed)\b",
                 message, re.I):
        return None
    for match in JEV_FINDER.finditer(message):
        claim = re.split(r"[.;\n]", message[match.end():], maxsplit=1)[0]
        prefix = re.split(r"[.;\n]", message[:match.start()])[-1]
        attribution = prefix + match.group(0) + claim
        if re.search(rf"\b(?:nothing|none|{_denial(DEFECT_NOUN + '|finding|issue')})\b",
                     attribution, re.I):
            continue
        if found := re.search(rf"\b{DEFECT_NOUN}\b", claim, re.I):
            noun, quote = found.group(0), match.group(0) + claim
        elif fixed := re.search(rf"\bfix(?:es|ed)?\b.*\b({DEFECT_NOUN})\b", prefix, re.I):
            noun, quote = fixed.group(1), attribution.strip()
        else:
            continue
        if not _denies_defect(message, noun):
            return quote
    return None


def _denial(nouns):
    """Regex for "no <noun>" or "not a <noun>", singular or plural.

    "No bugs remain" reports the state after a fix, so it is not a denial.
    """
    return (rf"(?:no\s+(?:{nouns})s?\b(?!\s+(?:remain|left|anymore|any\s+more))"
            rf"|not\s+(?:(?:a|an)\s+)?(?:{nouns})s?"
            rf"|did\s+not\s+(?:find|identify|confirm)\s+(?:(?:a|an|any)\s+)?(?:{nouns})s?)")


def _denies_defect(message, noun):
    for sentence in re.split(r"[.;\n]", message):
        for denial in re.finditer(rf"\b{_denial(re.escape(noun))}\b", sentence, re.I):
            # A historical qualifier must attach to this non-detection.
            if (re.match(r"did\s+not\b", denial.group(0), re.I)
                    and (re.search(r"(?:\b(?:earlier|previously)\b\s+"
                                   r"|^\s*(?:earlier|previously)\b\s*,\s*)"
                                   r"(?:(?:(?:our|the)\s+)?(?:tests?|checks?|reviews?|investigations?)\s*)?$",
                                   sentence[:denial.start()], re.I)
                         or re.match(r"\s+(?:earlier|previously|before\s+(?:this|the)\s+review)\b",
                                     sentence[denial.end():], re.I))):
                continue
            return True
    return False


def build_report(sources, start, end):
    sites = defaultdict(_new_site)
    calls = [r for r in sources.get("calls") or [] if in_window(r.get("ts"), start, end)]
    judges = [r for r in sources.get("judge") or []
              if in_window(r.get("at"), start, end) and r.get("kind")]
    # Only unique exact receipt IDs establish a join. Timestamps/token counts
    # cannot tell whether a judge row came from legacy, review, hub or no call.
    receipt_judges = defaultdict(list)
    receipt_calls = defaultdict(int)
    for row in judges:
        if isinstance(row.get("receipt_id"), str) and row["receipt_id"]:
            receipt_judges[row["receipt_id"]].append(row)
    for row in calls:
        if isinstance(row.get("server_receipt_id"), str) and row["server_receipt_id"]:
            receipt_calls[row["server_receipt_id"]] += 1
    matched = set()
    for row in calls:
        receipt = row.get("server_receipt_id")
        candidates = receipt_judges.get(receipt, []) if isinstance(receipt, str) else []
        linked = (candidates[0] if len(candidates) == 1 and receipt_calls[receipt] == 1
                  and _judge_fields(candidates[0])["billing"] == "measured"
                  and _fields(row)["billing"] == "measured" else None)
        if linked:
            key = site_for(linked["kind"], judge_kind=True)
            traffic = _traffic(linked)
            matched.add(id(linked))
        else:
            key = "judge:unattributed" if row.get("caller") == "jev_judge" else site_for(row.get("caller"))
            traffic = _traffic(row)
        site = sites[_site_key(key, traffic)]
        site["traffic_class"] = traffic
        _tally(site, **_fields(row))
    for row in judges:
        traffic = _traffic(row)
        site = sites[_site_key(site_for(row["kind"], judge_kind=True), traffic)]
        site["traffic_class"] = traffic
        site["observations"] += 1
        site["wait_ms"] += _int(row.get("elapsed_ms")) or 0
        if row.get("cache_hit") is True:
            site["observation_cache_hits"] += 1
        if id(row) not in matched:
            if _judge_fields(row)["billing"] in ("measured", "unknown"):
                site["attribution_complete"] = False
            site["note"] = "judge observations have no unique billed-call receipt link; spend stays with its receipt caller"
        ref = row.get("subject_ref")
        if row["kind"] == "post_write_task_fit" and isinstance(ref, dict) and ref.get("would_block") is True:
            site["blocks"] += 1
            site["note"] = "would_block is a judgment observation; no stopped write or avoided defect is verified"
    for row in sources.get("gate") or []:
        if in_window(row.get("ts"), start, end) and row.get("status") == "required" and row.get("missing"):
            site = sites["gate:jev_required_actions"]
            site["blocks"] += 1
            site["note"] = "each block forces a Jev call before the turn may end; excluded from value"
    commits = [c for c in sources.get("commits") or [] if in_window(c.get("date"), start, end)]
    for commit in commits:
        attribution = positive_attribution(commit)
        if attribution:
            sites["review"]["outcomes_verified"] += 1
            sites["review"]["evidence"].append({"ref": commit["sha"], "quote": attribution,
                                              "status": "commit_attributed"})
    base = baseline(sources.get("ci"), commits, start, end)
    price = sources.get("price") or {}
    rate_in = price.get("price_usd_per_million_input_tokens")
    rate_in = (float(rate_in) if type(rate_in) in (int, float)
               and math.isfinite(rate_in) and rate_in >= 0 else None)
    source_status = sources.get("source_status") or {}
    cost_status = sources.get("calls_status", "complete")
    complete_cost = cost_status in ("complete", "empty")
    totals = _new_site()
    for field, value in totals.items():
        if type(value) is int:
            totals[field] = sum(site[field] for site in sites.values())
    round_min, rounds_pr = base["ci_round_minutes_median"], base["ci_rounds_per_pr_median"]
    for key, site in list(sites.items()) + [("total", totals)]:
        site["usd_input_measured"] = None if rate_in is None else site["input_tokens"] * rate_in / 1_000_000
        site["usd_input"] = (site["usd_input_measured"] if complete_cost and not site["billing_unknown"]
                             and site["attribution_complete"] else None)
        site["usable_rate"] = site["usable"] / site["paid"] if complete_cost and site["paid"] else None
        verified = site["outcomes_verified"]
        site["minutes_saved"] = (None if not verified or round_min is None or rounds_pr is None
                                 else (verified * round_min, verified * rounds_pr * round_min))
        site["minutes_per_usd"] = (None if not site["minutes_saved"] or not site["usd_input"]
                                   else tuple(m / site["usd_input"] for m in site["minutes_saved"]))
        if key != "total":
            site["recommendation"] = recommend(key, site)
    observed_totals = {field: totals[field] for field in
                       ("attempts", "cache_hits", "not_billed", "paid", "usable", "billing_unknown",
                        "input_tokens", "output_tokens")}
    if not complete_cost:
        for field in observed_totals:
            totals[field] = None
    return {"window": {"start": start.isoformat(), "end": end.isoformat()},
            "price": {"usd_per_million_input": rate_in, "usd_per_million_output": None,
                      "input_source": str(price.get("price_source_url") or "absent"),
                      "output_source": OUTPUT_PRICE_SOURCE},
            "sites": dict(sites), "totals": totals, "observed_cost_totals": observed_totals,
            "cost_complete": complete_cost, "baseline": base, "source_status": source_status,
            "judge_hub": {"calls_log_input_tokens": sum(_fields(r)["tokens_in"] or 0 for r in calls
                                                         if r.get("caller") == "jev_judge"),
                          "matched_judge_rows": len(matched), "unlinked_judge_rows": len(judges) - len(matched)},
            "unreadable": sources.get("unreadable") or {}}


def _fmt_int(n):
    return f"{n:,}" if isinstance(n, int) else "-"


def _fmt_usd(v):
    return "UNKNOWN" if v is None else f"${v:,.3f}"


def _fmt_range(pair):
    return "-" if not pair else f"{pair[0]:,.0f}–{pair[1]:,.0f}"


def render(report):
    out = []
    w, price, totals, base = report["window"], report["price"], report["totals"], report["baseline"]
    out.append(f"JEV VALUE REPORT  {w['start'][:16]} → {w['end'][:16]} UTC")
    if price["usd_per_million_input"] is None:
        out.append("dollars: UNKNOWN — no price_usd_per_million_input_tokens in ops/config/jev-cost-guard.v1.json")
    else:
        out.append(f"input-token price: ${price['usd_per_million_input']}/M ({price['input_source']})")
    out.append(f"output-token price: UNKNOWN — {price['output_source']}")
    for path, n in sorted(report["unreadable"].items()):
        out.append(f"{path}: {n} unreadable line{'s' if n != 1 else ''} skipped")
    out.append("")
    out.append("COST (billed-call log, all callers)")
    rate = totals["usable_rate"]
    out.append(f"  attempts {_fmt_int(totals['attempts'])} · not billed (refused/cap) {_fmt_int(totals['not_billed'])}"
               f" · paid {_fmt_int(totals['paid'])} · usable {_fmt_int(totals['usable'])}"
               f" ({'-' if rate is None else f'{rate:.0%}'}) · cache hits {_fmt_int(totals['cache_hits'])}")
    out.append(f"  tokens in {_fmt_int(totals['input_tokens'])} · out {_fmt_int(totals['output_tokens'])}")
    out.append(f"  dollars (input only) {_fmt_usd(totals['usd_input'])} · output dollars UNKNOWN")
    out.append(f"  billing unknown {_fmt_int(totals['billing_unknown'])}; only measured receipts count as paid")
    if not report["cost_complete"]:
        out.append("  cost UNKNOWN/incomplete — billed-call source could not be fully read")
    for path, status in sorted(report["source_status"].items()):
        if status["status"] not in ("complete", "empty"):
            out.append(f"  source {path}: {status['status']}")
    hub = report["judge_hub"]
    out.append(f"  jev_judge hub: {hub['matched_judge_rows']} uniquely linked judge rows; "
               f"{hub['unlinked_judge_rows']} unlinked observations (no additional spend)")
    out.append("")
    out.append("VERDICT TABLE (per call site; value counted only where linked to an outcome)")
    header = (f"  {'site':<36} {'paid':>7} {'usable':>7} {'tok in':>11} {'$ in':>8} {'wait h':>7}"
              f" {'attrib':>6} {'min saved':>11} {'min/$':>13}  recommendation")
    out.append(header)
    order = sorted(report["sites"].items(), key=lambda kv: (-kv[1]["input_tokens"], -kv[1]["blocks"], kv[0]))
    for key, s in order:
        rate = s["usable_rate"]
        out.append(f"  {key:<36} {_fmt_int(s['paid']):>7} {'-' if rate is None else f'{rate:.0%}':>7}"
                   f" {_fmt_int(s['input_tokens']):>11} {_fmt_usd(s['usd_input']):>8}"
                   f" {s['wait_ms'] / 3_600_000:>7.2f} {s['outcomes_verified']:>6}"
                   f" {_fmt_range(s['minutes_saved']):>11} {_fmt_range(s['minutes_per_usd']):>13}"
                   f"  {s['recommendation']}")
    out.append("")
    out.append("EVIDENCE")
    for key, s in order:
        if s["outcomes_verified"]:
            refs = ", ".join(f"{e['ref']} (\"{e['quote']}\")" for e in s["evidence"])
            out.append(f"  {key}: {s['outcomes_verified']} finding(s) attributed by fix commit: {refs}")
            if s["minutes_saved"] is None:
                out.append(f"  {key}: minutes saved UNKNOWN — no CI history; {FETCH_HINT}")
        elif s["blocks"]:
            out.append(f"  {key}: {s['blocks']} block(s), not counted as value — {s['note'] or 'correctness unverified'}")
        else:
            out.append(f"  {key}: insufficient evidence — no verdict links to a later change, fix or CI routing"
                       + (f" ({s['note']})" if s["note"] else ""))
    out.append(f"  agent tokens saved, every site: {TOKENS_SAVED_SOURCE}")
    out.append("")
    out.append("MEASURED BASELINE (for value ranges and the holdout)")
    if base["ci_round_minutes_median"] is None:
        out.append(f"  {base['coverage_issue'] or 'No eligible completed CI attempts'} — {FETCH_HINT}")
    else:
        out.append(f"  median CI round {base['ci_round_minutes_median']} min · rounds per PR median "
                   f"{base['ci_rounds_per_pr_median']} mean {base['ci_rounds_per_pr_mean']:.2f}"
                   f" sd {base['ci_rounds_per_pr_sd']} · failed rounds per PR mean "
                   f"{base['ci_failed_rounds_per_pr_mean']} · PRs with CI {base['prs_with_ci']}")
        out.append(f"  merged PRs {base['merged_prs']} · median time to merge {base['time_to_merge_hours_median']} h"
                   f" · main canary failures {base['main_canary_failures']} · reverts {base['reverts']}")
        out.append(f"  holdout n per arm to detect a {EFFECT_SHARE:.0%} change in CI rounds/PR"
                   f" (α=.05 two-sided, power .8): {base['holdout_n_per_arm']}")
    out.append("")
    out.append("METHOD: value is a CI-time proxy from positive fix-commit attribution, not measured time saved. "
               "Unknown billing is unpriced. Wait h sums recorded judge latency including cache hits.")
    proven, saved = totals["outcomes_verified"], totals["minutes_saved"]
    low, high = saved or (0, 0)
    wait = totals["wait_ms"] / 3_600_000
    out.append(f"TOTAL: cost {_fmt_usd(totals['usd_input'])} input-only + output UNKNOWN,"
               f" {_fmt_int(totals['paid'])} paid calls, {wait:.1f} h Jev wait; attributed value"
               f" {proven} finding(s) worth {'UNKNOWN' if proven and not saved else f'{low:,.0f}–{high:,.0f}'}"
               f" CI minutes; {sum(1 for s in report['sites'].values() if not s['outcomes_verified'])}"
               f" of {len(report['sites'])} sites have insufficient evidence.")
    return "\n".join(out)


def data_root():
    try:
        common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                cwd=SOURCE_ROOT, capture_output=True, text=True, check=True,
                                timeout=5).stdout.strip()
        return Path(common).parent if common else SOURCE_ROOT
    except (OSError, subprocess.SubprocessError):
        return SOURCE_ROOT


def read_jsonl(path, unreadable, source_status):
    rows, bad = [], 0
    status = "complete"
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("non-object row")
                    rows.append(row)
                except ValueError:
                    bad += 1
    except FileNotFoundError:
        status = "absent"
    except (OSError, UnicodeError):
        status = "read_failed" if not rows else "partial"
    if bad:
        unreadable[str(path)] = bad
        status = "partial"
    elif status == "complete" and not rows:
        status = "empty"
    source_status[str(path)] = {"status": status, "rows_read": len(rows), "bad_lines": bad}
    return rows


def read_commits(root, start, end):
    ref = "origin/main"
    if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=root,
                      capture_output=True, timeout=10).returncode:
        ref = "HEAD"
    text = subprocess.run(["git", "log", ref, f"--since={start.isoformat()}", f"--until={end.isoformat()}",
                           "--format=%h%x1f%cI%x1f%s%x1f%b%x1e"], cwd=root, capture_output=True,
                          text=True, check=True, timeout=60).stdout
    commits = []
    for record in text.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2], "body": parts[3]})
    return commits


def _gh(args):
    """`gh ...` stdout through lib/github_reader (retried, redacted, bounded)."""
    return GitHubReader(cwd=str(SOURCE_ROOT), timeout=60).text(args[1:])


def _gh_json(gh, endpoint):
    return json.loads(gh(["gh", "api", endpoint]))


def fetch_ci(root, start, end, gh=_gh):
    """Enumerate retained runs WITHOUT search filters (no 1,000-result cap).

    Creation-date searches omit older-created reruns. Traverse all retained
    runs instead, check total_count against distinct IDs, and hydrate each
    eligible run's attempt endpoints. Any truncated/error result leaves the
    previous snapshot intact. Coverage uses each attempt's run_started_at.
    """
    runs_by_id, page, expected = {}, 1, None
    while True:
        payload = _gh_json(gh, f"repos/{{owner}}/{{repo}}/actions/runs?page={page}&per_page=100")
        count, batch = _int(payload.get("total_count")), payload.get("workflow_runs")
        if count is None or not isinstance(batch, list):
            raise ValueError("incomplete CI run listing")
        if expected is None:
            expected = count
        elif expected != count:
            raise ValueError("incomplete CI listing: total_count changed during fetch; retry")
        if not batch:
            break
        for row in batch:
            if not isinstance(row, dict) or _int(row.get("id")) is None or row["id"] in runs_by_id:
                raise ValueError("incomplete CI listing: missing or repeated run ID")
            runs_by_id[row["id"]] = row
        if len(runs_by_id) == expected:
            break
        if len(runs_by_id) > expected:
            raise ValueError("incomplete CI listing: inconsistent total_count")
        page += 1
    if len(runs_by_id) != expected:
        raise ValueError("truncated CI history: total_count exceeds fetched run IDs")
    runs = []
    complete = True
    for row in runs_by_id.values():
        latest_start = parse_time(row.get("run_started_at"))
        if latest_start is None:
            raise ValueError("incomplete CI run timestamp")
        if latest_start < start:
            continue
        attempts = _int(row.get("run_attempt"))
        if not attempts:
            raise ValueError("incomplete CI attempt count")
        for attempt in range(1, attempts + 1):
            detail = _gh_json(gh, f"repos/{{owner}}/{{repo}}/actions/runs/{row['id']}/attempts/{attempt}")
            if (detail.get("id") != row["id"] or detail.get("run_attempt") != attempt
                    or not parse_time(detail.get("run_started_at"))):
                raise ValueError("incomplete CI attempt identity or timestamp")
            if in_window(detail["run_started_at"], start, end):
                if detail.get("status") != "completed" or not detail.get("conclusion"):
                    complete = False
                runs.append({k: detail.get(k) for k in ("id", "name", "event", "conclusion", "status",
                                                       "head_branch", "run_started_at", "updated_at", "run_attempt")})
    pulls, page = [], 1
    while True:
        batch = _gh_json(gh, "repos/{owner}/{repo}/pulls?state=closed&per_page=100"
                             f"&sort=updated&direction=desc&page={page}")
        if not isinstance(batch, list):
            raise ValueError("incomplete PR history")
        pulls.extend({k: row.get(k) for k in ("number", "created_at", "merged_at")} for row in batch
                     if in_window(row.get("merged_at"), start, end))
        if len(batch) < 100:
            break
        page += 1
    fetched = datetime.now(timezone.utc)
    snapshot = {"fetched_at": fetched.isoformat(),
                "coverage": {"start": start.isoformat(), "end": end.isoformat(),
                             "time_basis": "run_started_at", "complete": complete and fetched >= end,
                             "retained_run_count": expected, "discovery": "unfiltered_all_retained_runs"},
                "runs": runs, "pulls": pulls}
    # Serialize before opening a temporary sibling. Readers always see one
    # complete snapshot, including when serialization/write/replace fails.
    serialized = json.dumps(snapshot)
    path = Path(root) / "out" / CI_SNAPSHOT
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{CI_SNAPSHOT}.", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return path, snapshot


def _day(text):
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--since", help="UTC date, inclusive (overrides --days)")
    parser.add_argument("--until", help="UTC date, exclusive (default now)")
    parser.add_argument("--root", help="directory holding out/ (default: the main checkout)")
    parser.add_argument("--price-config", default=str(PRICE_CONFIG))
    parser.add_argument("--ci-snapshot", help=f"default out/{CI_SNAPSHOT}")
    parser.add_argument("--fetch-ci", action="store_true", help="save CI history via gh api, then report")
    parser.add_argument("--no-git", action="store_true", help="skip local git history")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.days <= 0:
            raise ValueError("--days must be positive")
        end = _day(args.until) if args.until else datetime.now(timezone.utc)
        start = _day(args.since) if args.since else end - timedelta(days=args.days)
        if start >= end:
            raise ValueError("--since must precede --until")
    except ValueError as exc:
        parser.error(str(exc))
    root = Path(args.root) if args.root else data_root()
    out = root / "out"
    unreadable, source_status = {}, {}
    ci = None
    if args.fetch_ci:
        try:
            path, ci = fetch_ci(root, start, end)
        except (OSError, ValueError, subprocess.SubprocessError, GitHubUnreadable) as exc:
            print(f"CI fetch failed ({type(exc).__name__}); previous snapshot preserved", file=sys.stderr)
            return 1
        print(f"saved {len(ci['runs'])} runs and {len(ci['pulls'])} PRs to {path}", file=sys.stderr)
    else:
        snapshot = Path(args.ci_snapshot) if args.ci_snapshot else out / CI_SNAPSHOT
        try:
            ci = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            ci = None
    try:
        price = json.loads(Path(args.price_config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        price = None
    sources = {
        "calls": read_jsonl(out / "jev-calls.jsonl", unreadable, source_status),
        "judge": read_jsonl(out / "jev-judge.jsonl", unreadable, source_status),
        "gate": [row for gate in sorted(out.glob("jev-*-gate.jsonl")) for row in read_jsonl(gate, unreadable, source_status)],
        "commits": [] if args.no_git else read_commits(root, start, end),
        "ci": ci, "price": price, "unreadable": unreadable, "source_status": source_status,
        "calls_status": source_status[str(out / "jev-calls.jsonl")]["status"],
    }
    report = build_report(sources, start, end)
    print(json.dumps(report, indent=2, default=list) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
