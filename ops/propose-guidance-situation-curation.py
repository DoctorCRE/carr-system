#!/usr/bin/env python3
"""Propose the reviewed situation curation the Guidance Registry is waiting on.

WHY THIS EXISTS (2026-08-23). The typed Guidance Registry has been built,
migrated, gated and tested, and it has never held a single item. Its activation
function refuses for two stated reasons, and only one of them was ever the real
blocker:

    constitution      between 5 and 10 active items   — 5 candidates are reviewed
    coverage          zero failures                    — 204 on 2026-08-23

Coverage is 204 because the registry is empty, and it is empty because its
importer refuses: every one of the 13 reviewed DOCTRINE guidance records needs
an approved WR-AI-006 situation bridge, and production carries one approved
concept of the thirteen. ops/guidance-situation-curation-preflight.py names each missing
one exactly. Nothing had ever proposed them, so the whole machine sat waiting on
a step nobody had been asked to take — the dormant-machine failure this work was
told not to repeat.

WHAT IT DOES. Reads audits/guidance-situation-curation-review.v1.json — the
REVIEWED curation package, already covering all 13 doctrine records — and fires
the machine-callable half of the curation path for everything production is
missing: propose-retrieval-concept, propose-retrieval-phrase and
propose-retrieval-mapping. Every field comes from the reviewed file; this script
invents nothing, and refuses rather than guessing when a row is incomplete.

WHAT IT CANNOT DO, by design. approve-retrieval-proposals is human-only and
atomic: it promotes the batch, re-runs the golden retrieval suite, and rolls the
whole batch back on any regression. So this leaves ONE approval for Joe instead
of thirty-nine decisions, which is the shape rule 14e0408b asks for.

    ops/propose-guidance-situation-curation.py            # dry run: what is missing
    ops/propose-guidance-situation-curation.py --apply    # fire the proposals
    ops/propose-guidance-situation-curation.py --pending  # the exact approval call
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib import record_call  # noqa: E402
REVIEW = REPO / "audits" / "guidance-situation-curation-review.v1.json"
KEY_PREFIX = "guidance-situation-curation-20260823"
# The proposal verbs store idempotency_key as a UUID column, so a readable key
# is refused at the type boundary. A v5 name-uuid keeps the property that
# matters — the same logical proposal produces the same key on every run, so a
# re-run after a partial failure replays rather than duplicating — while being
# a uuid the column accepts.
KEY_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def key(*parts: str) -> str:
    return str(uuid.uuid5(KEY_NS, ":".join((KEY_PREFIX,) + parts)))


def call(verb: str, args: dict) -> dict:
    """One deployed verb through the sanctioned local door (run.sh call)."""
    result = record_call.call_verb(verb, args, timeout=None)
    payload = result.reply
    if result.kind not in (record_call.OK, record_call.REFUSED):
        raise RuntimeError(f"{verb}: no payload — {result.detail}")
    if not result.ok or not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(f"{verb}: refused — {json.dumps(payload)[:300]}")
    return payload


def missing_from_preflight() -> tuple[set[str], set[str]]:
    """Concepts and bridges production lacks, read from the PREFLIGHT's own
    output rather than from a second query of my own.

    There is no read verb for the curation tables — the same missing read side
    the 2026-08-23 council named for the guidance registry itself. The preflight
    is the surface that already reports this, and asking it is what keeps this
    script and the check that gates the import from ever disagreeing about what
    is missing (rule a8c55a47)."""
    result = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), str(REPO / "tools" / "db-tap.py"),
         "run", "ops/guidance-situation-curation-preflight.py"],
        capture_output=True, text=True, cwd=REPO)
    start = result.stdout.find("{")
    if start < 0:
        raise RuntimeError("preflight produced no JSON; run it directly to see why")
    report = json.loads(result.stdout[start:])
    concepts, bridges = set(), set()
    for error in report.get("errors", []):
        if ": approved concept is absent: " in error:
            concepts.add(error.split(": approved concept is absent: ")[1].strip())
        elif ": approved doctrine bridge is absent: " in error:
            bridges.add(error.split(": approved doctrine bridge is absent: ")[1].strip())
    return concepts, bridges


def pending_batch() -> int:
    """Print the ONE human call that promotes everything pending.

    approve-retrieval-proposals wants the exact ids, each one's current version,
    and the active golden-suite digest — three things a person should not have to
    assemble by hand from a database they cannot query. Producing it here is the
    same reasoning as rule e313a3ca: a step the system can do is not a decision,
    and handing it over as homework costs a partner's attention for nothing.
    The APPROVAL itself stays his, because it is atomic and it promotes law.
    """
    query = REPO / "out" / "retrieval-pending.sql"
    query.parent.mkdir(parents=True, exist_ok=True)
    query.write_text(
        "select p.id, p.version, p.idempotency_key::text, r.golden_suite_digest\n"
        "  from retrieval_proposal p\n"
        "  cross join (select golden_suite_digest from retrieval_ranking_policy\n"
        "               where is_default and status='active' limit 1) r\n"
        " where p.status='pending'\n"
        " order by case p.proposal_type when 'concept' then 1 when 'phrase' then 2\n"
        "               else 3 end, p.created_at, p.id;\n", encoding="utf-8")
    result = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), str(REPO / "tools" / "db-tap.py"),
         "sql", str(query)], capture_output=True, text=True, cwd=REPO)
    rows = [line.split("|") for line in result.stdout.splitlines()
            if line.count("|") == 3]
    if not rows:
        print("propose-guidance-situation-curation: nothing pending "
              + result.stderr.strip()[-200:], file=sys.stderr)
        return 1

    # THE BATCH IS SCOPED TO THIS PACKAGE'S OWN PROPOSALS. Approval is atomic —
    # it promotes everything in the call, re-runs the golden suite and rolls the
    # whole batch back on any regression — so sweeping in another session's
    # pending curation would ask Joe to approve work nobody put in front of him,
    # and would let an unrelated regression roll back the registry's unblock.
    # Anything else pending is NAMED rather than silently included or silently
    # dropped: a proposal nobody mentions is a proposal nobody approves.
    mine = set()
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    for row in review["doctrine_guidance"]:
        mine.add(key("concept", row["concept_key"]))
        mine.add(key("phrase", row["concept_key"]))
        for mapping in row.get("mappings", []):
            mine.add(key("mapping", row["concept_key"], mapping["section_address"]))
    ours = [r for r in rows if r[2] in mine]
    theirs = [r for r in rows if r[2] not in mine]
    if not ours:
        print("propose-guidance-situation-curation: none of this package's proposals "
              "are pending — they are already approved, or were never fired.")
        return 0

    digest = ours[0][3]
    payload = {"idempotency_key": key("approve", digest, str(len(ours))),
               "proposal_ids": [r[0] for r in ours],
               "base_versions": {r[0]: int(r[1]) for r in ours},
               "golden_suite_digest": digest}
    print(f"{len(ours)} pending proposal(s) from the reviewed curation package. "
          "Joe's one call, from a session authenticated as him (human-only verb):\n")
    print("./run.sh call approve-retrieval-proposals '"
          + json.dumps(payload, separators=(",", ":")) + "'")
    if theirs:
        print(f"\nAlso pending, and NOT in the call above because they belong to other "
              f"curation work: {len(theirs)} proposal(s) — "
              + ", ".join(r[0] for r in theirs))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--pending", action="store_true",
                        help="print the exact human approval call for what is pending")
    args = parser.parse_args()

    if args.pending:
        return pending_batch()

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    rows = review["doctrine_guidance"]
    concepts_missing, bridges_missing = missing_from_preflight()

    planned: list[tuple[str, dict]] = []
    for row in rows:
        concept = row["concept_key"]
        if concept in concepts_missing:
            planned.append(("propose-retrieval-concept", {
                "idempotency_key": key("concept", row["concept_key"]),
                "reason": "Reviewed WR-AI-006 curation package for the typed Guidance "
                          "Registry; the registry cannot import without this bridge.",
                "concept_key": concept, "label": row["label"],
                "definition": row["definition"]}))
            planned.append(("propose-retrieval-phrase", {
                "idempotency_key": key("phrase", row["concept_key"]),
                "reason": "The reviewed real-world phrase for this concept.",
                "concept_key": concept, "phrase": row["phrase"], "match_mode": "fts",
                "weight": 1, "source": "manual",
                "source_ref": "audits/guidance-situation-curation-review.v1.json"}))
        for mapping in row.get("mappings", []):
            address = mapping["section_address"]
            if (concept not in concepts_missing
                    and f"{concept} -> {address}" not in bridges_missing):
                continue
            planned.append(("propose-retrieval-mapping", {
                "idempotency_key": key("mapping", row["concept_key"], address),
                "reason": "The reviewed doctrine bridge for this concept.",
                "concept_key": concept, "section_address": address,
                "role": mapping["role"], "weight": mapping["weight"],
                "rationale": mapping["rationale"]}))

    if not planned:
        print("propose-guidance-situation-curation: nothing missing — production "
              "already carries every reviewed concept, phrase and bridge.")
        return 0

    counts: dict[str, int] = {}
    for verb, _ in planned:
        counts[verb] = counts.get(verb, 0) + 1
    print("propose-guidance-situation-curation: "
          + ", ".join(f"{v.split('-', 1)[1]}={n}" for v, n in sorted(counts.items()))
          + f" ({len(planned)} proposal(s))")

    if not args.apply:
        for verb, payload in planned:
            print(f"  would {verb}: {payload.get('concept_key')}"
                  + (f" -> {payload['section_address']}" if "section_address" in payload else ""))
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    made = []
    for verb, payload in planned:
        result = call(verb, payload)
        row = result.get("proposal") or {}
        made.append({"verb": verb, "concept_key": payload.get("concept_key"),
                     "proposal_id": row.get("id"), "version": row.get("version"),
                     "replay": bool(result.get("replay"))})
        print(f"  {verb}: {payload.get('concept_key')} -> {made[-1]['proposal_id']}"
              + (" (replay)" if made[-1]["replay"] else ""))
    print(json.dumps({"proposed": len(made), "proposals": made}, indent=2, sort_keys=True))
    print("\nNEXT AND LAST STEP IS JOE'S: approve-retrieval-proposals is human-only and "
          "atomic — it promotes the batch, re-runs the golden retrieval suite and rolls "
          "the whole batch back on any regression.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
