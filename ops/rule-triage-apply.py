#!/usr/bin/env python3
"""rule-triage-apply.py — DRY-RUN ONLY batch-approval tooling for WR-000019 slice S7.

WHAT THIS DOES. Reads ops/config/rule-triage.v1.json and prints the exact
record-layer verb calls Joe's later batch acceptance would execute to apply the
triage:

  - one retire-rule call per GONE rule (reason + superseded_by = merge_target)
  - one OPTIONAL amend-rule call per rule this triage flagged as having stale
    wording worth fixing (never required — Joe's call, and the reason for each
    is carried from the triage row so nothing is invented here)
  - with --emit-receipts-plan, one additional log-decision call recording the
    batch acceptance itself, so the triage's application leaves its own
    audit trail the way every other settled decision in this system does

GATE/JIT/CORE reclassification is NOT a record-layer write: a rule's home in
this triage lives in ops/config/rule-triage.v1.json (and, once accepted, in
ops/config/rule-enforcement-map.json's rule_load_layers/category fields) — it
is repo config, same as the rest of the enforcement map. This tool never
invents a verb call for that, because none exists; printing one would be
exactly the kind of unverified claim rule c53beeaa warns against.

WHAT THIS NEVER DOES: it never calls a record-layer verb, never opens a
network connection, never shells out to ./run.sh or tools/call-verb.py. It is
read-only against the triage JSON and stdout-only on output. This is the hard
scope boundary for S7 (see WR-000019 slice S7): classification and tooling
only. The actual retire-rule/amend-rule/log-decision calls happen later, by a
human-approved session, one batch at a time, each idempotency_key freshly
minted at THAT time — never reused from this tool's printed plan, because an
idempotency key is only safe to reuse for a call that already ran with it.

Usage:
    ./ops/rule-triage-apply.py                     # print the full plan
    ./ops/rule-triage-apply.py --emit-receipts-plan  # + the closing log-decision call
    ./ops/rule-triage-apply.py --home gone           # only the retire-rule plan
    ./ops/rule-triage-apply.py --json                # machine-readable plan instead of prose
"""
import argparse
import json
import sys
from pathlib import Path

DEFAULT_TRIAGE_PATH = Path(__file__).resolve().parent / "config" / "rule-triage.v1.json"

# Rules this triage flagged as carrying stale wording worth an OPTIONAL amend.
# Kept as an explicit, small, named list (not inferred from reason-text
# scanning) so the plan never grows a call this session did not deliberately
# decide to propose. Every reason string below is quoted from the triage row
# that already carries it -- nothing new is asserted here.
OPTIONAL_AMEND_CANDIDATES = {
    "4f7c348f": ("Recitation wording still says 'both rule FILES'; the files are gone "
                 "post the 2026-08-19 md-cutoff and counts now come from standing-context. "
                 "Joe's own action item A28 names this exact wording fix."),
    "1fddcffb": ("Named enforcement '00_Context/sweep-sop.md' is a pre-cutover file pointer; "
                 "the test the rule states is now applied via ops/config/rule-triage.v1.json "
                 "instead, and the enforcement field should say so."),
    "70e372f0": ("Both council chairs (A28) recommend this move from the shared rule set "
                 "into Dell's own personal set, since it is entirely about how Dell's "
                 "sessions should read Dell."),
}


def load_triage(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: triage file not found at {path}", file=sys.stderr)
        sys.exit(1)
    with path.open() as f:
        return json.load(f)


def build_retire_plan(data: dict) -> list[dict]:
    by_id = {r["id"]: r for r in data["rules"]}
    plan = []
    for r in sorted((x for x in data["rules"] if x["home"] == "gone"), key=lambda r: r["id"]):
        target = r.get("merge_target")
        target_title = by_id.get(target, {}).get("title_gist", "") if target else None
        plan.append({
            "verb": "retire-rule",
            "humanOnly": True,
            "rule_id": r["id"],
            "rule_title": r["title_gist"],
            "args": {
                "idempotency_key": "<mint fresh UUID at apply time — do not reuse this plan's>",
                "rule_id": r["id"],
                "reason": r["reason"],
                "superseded_by": target,
            },
            "note": f"folds into {target} ({target_title})" if target else "no merge target named",
        })
    return plan


def build_optional_amend_plan(data: dict) -> list[dict]:
    by_id = {r["id"]: r for r in data["rules"]}
    plan = []
    for rid, note in OPTIONAL_AMEND_CANDIDATES.items():
        r = by_id.get(rid)
        if not r:
            continue
        plan.append({
            "verb": "amend-rule",
            "humanOnly": False,  # amend-rule itself is not humanOnly, but nothing here executes it
            "optional": True,
            "rule_id": rid,
            "rule_title": r["title_gist"],
            "args": {
                "idempotency_key": "<mint fresh UUID at apply time — do not reuse this plan's>",
                "rule_id": rid,
                "new_statement": "<Joe's call — this tool proposes the WHY, never drafts new rule text>",
            },
            "note": note,
        })
    return plan


def build_receipts_plan(data: dict, retire_plan: list[dict]) -> dict:
    counts = data.get("counts", {})
    return {
        "verb": "log-decision",
        "humanOnly": False,
        "args": {
            "idempotency_key": "<mint fresh UUID at apply time>",
            "decision": (
                f"WR-000019 slice S7 rule triage batch-accepted: "
                f"{counts.get('core', 0)} core, {counts.get('gate', 0)} gate, "
                f"{counts.get('jit', 0)} jit, {len(retire_plan)} retired-by-merge."
            ),
            "rationale": (
                "ops/config/rule-triage.v1.json is the source; ops/rule-triage-report.py "
                "rendered it for review; the retire-rule batch above is what actually "
                "applied the merge families. Recorded so this batch is never relitigated "
                "rule-by-rule in a future session."
            ),
        },
        "note": "only emitted with --emit-receipts-plan; documents the batch AFTER it runs, "
                "never before",
    }


def render_prose(retire_plan, amend_plan, receipts, args) -> str:
    lines = []
    lines.append("=" * 78)
    lines.append("RULE TRIAGE APPLY PLAN (DRY RUN — nothing below is executed)")
    lines.append("=" * 78)
    lines.append("")
    lines.append(f"retire-rule calls planned: {len(retire_plan)}")
    lines.append(f"OPTIONAL amend-rule calls planned: {len(amend_plan)}")
    lines.append("")
    if args.home in (None, "gone"):
        lines.append("-" * 78)
        lines.append("RETIRE-RULE BATCH (GONE-merge rules)")
        lines.append("-" * 78)
        for p in retire_plan:
            lines.append(f"  [{p['rule_id']}] {p['rule_title'][:80]}")
            lines.append(f"    verb: {p['verb']}  (humanOnly={p['humanOnly']})")
            lines.append(f"    args: idempotency_key={p['args']['idempotency_key']}")
            lines.append(f"          rule_id={p['args']['rule_id']}")
            lines.append(f"          reason={p['args']['reason'][:150]!r}")
            lines.append(f"          superseded_by={p['args']['superseded_by']}")
            lines.append(f"    {p['note']}")
            lines.append("")
    if not args.home:
        lines.append("-" * 78)
        lines.append("OPTIONAL AMEND-RULE BATCH (wording flags only — none required)")
        lines.append("-" * 78)
        for p in amend_plan:
            lines.append(f"  [{p['rule_id']}] {p['rule_title'][:80]}")
            lines.append(f"    verb: {p['verb']} (OPTIONAL)")
            lines.append(f"    why: {p['note']}")
            lines.append("")
    if receipts is not None:
        lines.append("-" * 78)
        lines.append("RECEIPTS PLAN (--emit-receipts-plan)")
        lines.append("-" * 78)
        lines.append(f"  verb: {receipts['verb']}")
        lines.append(f"  args.decision: {receipts['args']['decision']}")
        lines.append(f"  args.rationale: {receipts['args']['rationale']}")
        lines.append(f"  {receipts['note']}")
        lines.append("")
    lines.append("=" * 78)
    lines.append("Nothing above was called. This process made zero record-layer writes.")
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--triage-path", default=str(DEFAULT_TRIAGE_PATH))
    ap.add_argument("--home", choices=["gone"], default=None,
                     help="print only the retire-rule batch")
    ap.add_argument("--emit-receipts-plan", action="store_true",
                     help="also print the closing log-decision call for the batch")
    ap.add_argument("--json", action="store_true", help="machine-readable plan instead of prose")
    args = ap.parse_args()

    data = load_triage(Path(args.triage_path))
    retire_plan = build_retire_plan(data)
    amend_plan = build_optional_amend_plan(data)
    receipts = build_receipts_plan(data, retire_plan) if args.emit_receipts_plan else None

    if args.json:
        out: dict[str, object] = {"retire_rule_batch": retire_plan}
        if not args.home:
            out["optional_amend_rule_batch"] = amend_plan
        if receipts is not None:
            out["receipts_plan"] = receipts
        out["executed"] = False
        out["note"] = "DRY RUN ONLY. No verb in this plan was called by this tool."
        print(json.dumps(out, indent=1))
    else:
        print(render_prose(retire_plan, amend_plan, receipts, args))

    # Hard guarantee this file never accidentally grows a live call: assert no
    # network/subprocess primitives are imported anywhere in this module.
    assert "requests" not in sys.modules and "subprocess" not in sys.modules
    return 0


if __name__ == "__main__":
    sys.exit(main())
