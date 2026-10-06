#!/usr/bin/env python3
"""rule-triage-report.py — render ops/config/rule-triage.v1.json as a human-readable
review document for Joe.

WHY THIS EXISTS (WR-000019 slice S7): 219 active rules is too many to review as a
raw JSON diff. This renders the triage grouped by home (gate/jit/core/gone), with
counts, the merged restatement families spelled out by name, and the one
contradiction this slice resolved (14e0408b vs aa411351, per action item A28).

This script only READS ops/config/rule-triage.v1.json and PRINTS. It performs no
record-layer calls and no writes — the classification and tooling boundary for
S7 is: produce the artifact and the review surface, never apply it. Joe's batch
acceptance (via rule-triage-apply.py's plan, executed by a human-approved run)
is what actually retires/amends/approves anything in the record layer.

Usage:
    ./ops/rule-triage-report.py                  # print to stdout
    ./ops/rule-triage-report.py --out FILE        # write to FILE instead
    ./ops/rule-triage-report.py --home gate       # print only one home's section
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_TRIAGE_PATH = Path(__file__).resolve().parent / "config" / "rule-triage.v1.json"

HOME_ORDER = ["core", "gate", "jit", "gone"]
HOME_TITLE = {
    "core": "CORE — always loaded, needed every session",
    "gate": "GATE — a machine already checks (or can check) this",
    "jit": "JIT — real judgment, delivered at the situational moment",
    "gone": "GONE — merged into a surviving rule (restatement or resolved contradiction)",
}

MERGE_FAMILY_IDS = {
    "14e0408b", "634a2d94", "dff58fef", "8117b414", "aa411351",
    "e065aa82", "3fa422b7", "75c2e4c9", "581cb3fe", "006a7eaa",
}
MERGE_FAMILY_SURVIVOR = "aa411351"

SECOND_MERGE_FAMILY = {"d367188d": "0f38532e"}
THIRD_MERGE_FAMILY = {"937252fb": "97326357"}


def load_triage(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: triage file not found at {path}", file=sys.stderr)
        sys.exit(1)
    with path.open() as f:
        return json.load(f)


def render_header(data: dict) -> list[str]:
    lines = []
    lines.append("=" * 78)
    lines.append("RULE TRIAGE REVIEW — WR-000019 slice S7")
    lines.append("=" * 78)
    lines.append("")
    lines.append(data.get("generated_note", ""))
    lines.append("")
    counts = data.get("counts", {})
    total = sum(counts.values())
    lines.append(f"TOTAL RULES: {total}  (target: 219 active)")
    for home in HOME_ORDER:
        lines.append(f"  {home.upper():5s} {counts.get(home, 0):4d}")
    core_count = counts.get("core", 0)
    cap_note = "OK (<=35 cap, 15-20 target)" if core_count <= 35 else "OVER CAP — needs rework"
    lines.append(f"  core cap check: {core_count} {cap_note}")
    lines.append("")
    return lines


def render_merge_families(data: dict) -> list[str]:
    by_id = {r["id"]: r for r in data["rules"]}
    lines = []
    lines.append("-" * 78)
    lines.append("MERGED RESTATEMENT FAMILIES")
    lines.append("-" * 78)
    lines.append("")
    lines.append("Family 1 — the 'stop asking permission' family (9 rules -> 1 survivor):")
    lines.append(f"  SURVIVOR: {MERGE_FAMILY_SURVIVOR} — "
                  f"{by_id.get(MERGE_FAMILY_SURVIVOR, {}).get('title_gist', '')}")
    for rid in sorted(MERGE_FAMILY_IDS - {MERGE_FAMILY_SURVIVOR}):
        r = by_id.get(rid)
        if not r:
            continue
        lines.append(f"    GONE-merge {rid}: {r['title_gist'][:90]}")
        lines.append(f"        -> {r['reason']}")
    lines.append("")
    lines.append("Family 2 — single-source-of-truth restatement:")
    for rid, target in SECOND_MERGE_FAMILY.items():
        r = by_id.get(rid, {})
        lines.append(f"  GONE-merge {rid} -> {target}: {r.get('reason', '')}")
    lines.append("")
    lines.append("Family 3 — 'capability proven live, not just green tests' restatement:")
    for rid, target in THIRD_MERGE_FAMILY.items():
        r = by_id.get(rid, {})
        lines.append(f"  GONE-merge {rid} -> {target}: {r.get('reason', '')}")
    lines.append("")
    return lines


def render_contradiction(data: dict) -> list[str]:
    lines = []
    lines.append("-" * 78)
    lines.append("CONTRADICTION RESOLVED")
    lines.append("-" * 78)
    lines.append("")
    lines.append(
        "14e0408b (\"the COO seat decides and reports; it does not ask permission on\n"
        "obvious yeses\") drew its no-escalation gate around 'genuine forks where two\n"
        "defensible paths lead to materially different work.' aa411351's own text says\n"
        "this clause became a loophole: a session finds an internal call hard, relabels\n"
        "it a genuine fork, and hands it up anyway. aa411351 replaces the fork clause\n"
        "with an audience test (client-facing / public-facing / money / destructive-\n"
        "irreversible) and states explicitly that difficulty is not a ticket to escalate.\n"
        "Resolution: kept aa411351 as the survivor (Joe's later, tighter ruling);\n"
        "14e0408b and the rest of the merge family above fold into it. This matches\n"
        "action item A28's own note that both council chairs flagged this exact pair\n"
        "as a collapse candidate."
    )
    lines.append("")
    return lines


def render_home_section(data: dict, home: str) -> list[str]:
    rows = [r for r in data["rules"] if r["home"] == home]
    rows.sort(key=lambda r: r["id"])
    lines = []
    lines.append("-" * 78)
    lines.append(f"{HOME_TITLE[home]}  ({len(rows)} rules)")
    lines.append("-" * 78)
    for r in rows:
        lines.append(f"  [{r['id']}] {r['title_gist'][:100]}")
        lines.append(f"      reason: {r['reason']}")
        if r.get("carrying_control"):
            lines.append(f"      carrying_control: {r['carrying_control']}")
        if r.get("merge_target"):
            lines.append(f"      merge_target: {r['merge_target']}")
        if r.get("jit_trigger_hint"):
            lines.append(f"      jit_trigger_hint: {r['jit_trigger_hint']}")
        lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--triage-path", default=str(DEFAULT_TRIAGE_PATH))
    ap.add_argument("--out", default=None, help="write report to this file instead of stdout")
    ap.add_argument("--home", choices=HOME_ORDER, default=None,
                    help="print only this one home's section (skips header/families)")
    args = ap.parse_args()

    data = load_triage(Path(args.triage_path))

    lines: list[str] = []
    if args.home:
        lines += render_home_section(data, args.home)
    else:
        lines += render_header(data)
        lines += render_merge_families(data)
        lines += render_contradiction(data)
        for home in HOME_ORDER:
            lines += render_home_section(data, home)

    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
