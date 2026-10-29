#!/usr/bin/env python3
"""ops/vendor-level-drift-check.py — surface where a vendor's recorded
relationship level and the countable evidence disagree.

THE RULE (faf1b643, Joe, after asking "what makes a relationship go from 0 to 1?
is it simply making an attempt to contact? or actually meeting with them? we need
to establish measureables for each level"):

    0 Prospective  identified, no two-way contact
    1 Building     the first TWO-WAY contact has happened — they replied, or you
                   met. An outbound attempt alone does NOT promote anyone,
                   because you can email fifty people and have fifty
                   relationships that do not exist.
    2 Established  value has moved in EITHER direction at least once — an
                   introduction, a referral, or a deal
    3 Core         reciprocal AND repeated — value both ways, more than once

THE LEVEL STAYS A HUMAN JUDGMENT and is stored, not computed: a relationship can
matter for reasons no event count can see. The rule says so in its own words, and
this check obeys it — it CHANGES NOTHING. It reports where the evidence and the
record disagree, in either direction, because both directions are worth a look: a
Core vendor with nothing passing between you for months, and a Building vendor
who has quietly sent three referrals.

WHY THIS EXISTS. The rule names its own mechanism — `v_vendor_level_suggestion`
— and that view has been in the database since migration 0052, refined through
0053 and 0065, and rebuilt in 0069 to exclude merged rows. Nothing outside the
migrations has ever read it. A view nobody queries is the failure rule d8c9b1f0
names: a finding that only exists in a record has not reached the partner.

WHAT COUNTS AS A DISAGREEMENT is the view's own `disagrees` column, which
requires BOTH sides to be known. A crude `recorded is distinct from suggested`
comparison reports 239 rows on today's data, and 243 of the 301 rows are simply
vendors with no evidence at all — mostly Prospective vendors correctly recorded
as Prospective. Reporting those as findings would bury the 16 that matter, and a
check that cries on day one gets muted on day one.

RUN IT:
    ./.venv/bin/python ops/vendor-level-drift-check.py
    ./.venv/bin/python ops/vendor-level-drift-check.py --json

Exits 0 whether or not it finds drift: the level is a human call and a
disagreement is a prompt, never an error. Exits 78 (house convention) with no
database credential, so an unconfigured environment SKIPS rather than fails.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.credential_file import read_env_file  # noqa: E402

LEVEL_NAME = {0: "Prospective", 1: "Building", 2: "Established", 3: "Core", None: "unrecorded"}


def db_url() -> str | None:
    """Same resolution order as every other ops reader in this repo."""
    url = os.environ.get("CARR_DB_EXPORTER_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    env = os.path.expanduser("~/.config/carr/db.env")
    if os.path.exists(env):
        return read_env_file(env, syntax="prefix", duplicates="first", names=("CARR_DB_EXPORTER_URL",)).get("CARR_DB_EXPORTER_URL")
    return None


def remedy(row: dict) -> str:
    """What to actually DO about this row, in words, not codes.

    The two directions need different answers, and the overstated case usually
    is NOT "the level is wrong". A vendor sitting at Established or Core with
    zero recorded value movement is far more often a vendor whose referrals were
    never written down — and an unrecorded referral edge earns that vendor
    nothing in the reciprocity ledger, which is the ledger's whole basis.
    """
    rec, sug = row["recorded"], row["suggested"]
    if rec is not None and sug is not None and rec > sug:
        if row["they_gave"] == 0 and row["we_gave"] == 0:
            return ("No introduction or referral is recorded for them in either direction, "
                    "which is what levels 2 and 3 are counted from. Either the edges were "
                    "never written down — record them with link-parties, naming the broker "
                    "as via_party — or the level is generous and should come down.")
        return ("The recorded level sits above what the counted events support. Check whether "
                "an introduction or referral is missing from the record before changing it.")
    if rec is not None and sug is not None and sug > rec:
        return (f"Two-way contact has happened ({row['two_way']} event(s)) but the level still "
                f"reads {LEVEL_NAME[rec]}. If the contact was real, this is a promotion to "
                f"{LEVEL_NAME[sug]}.")
    return "Recorded and suggested differ; look at the evidence columns."


def rank(row: dict) -> tuple:
    """Worst first, and 'worst' means most overstated at the highest level.

    A vendor recorded as Core with no recorded value movement is the loudest
    thing on this list: Core is the level Joe defined as reciprocal AND
    repeated, so the claim is the largest and the evidence is the thinnest.
    """
    rec, sug = row["recorded"], row["suggested"]
    overstated = rec is not None and sug is not None and rec > sug
    gap = (rec - sug) if (rec is not None and sug is not None) else 0
    # negative sorts ascending -> biggest first
    return (0 if overstated else 1, -(rec or 0) if overstated else 0, -abs(gap))


def classify(rows: list[dict]) -> dict:
    """Split the view's rows into what a human should look at and what is noise.

    Pure, so the selftest can exercise it with no database. Every bucket here is
    the view's OWN `signal` value — this function does not re-derive the levels,
    because the rule's arithmetic belongs in one place and that place is the
    view.
    """
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        buckets.setdefault(r.get("signal") or "unknown", []).append(r)
    findings = sorted([r for r in rows if r.get("disagrees")], key=rank)
    return {
        "findings": findings,
        "unjudged_with_evidence": buckets.get("unjudged_with_evidence", []),
        "counts": {k: len(v) for k, v in sorted(buckets.items())},
        "total_rows": len(rows),
    }


def render(result: dict) -> list[str]:
    out: list[str] = []
    f = result["findings"]
    counts = result["counts"]
    if not f and not result["unjudged_with_evidence"]:
        out.append(f"vendor-level-drift: OK — no vendor's recorded level disagrees with its "
                   f"evidence ({result['total_rows']} active vendor(s) checked)")
        return out

    out.append(f"vendor-level-drift: {len(f)} vendor(s) whose recorded level and counted "
               f"evidence disagree, of {result['total_rows']} active")
    out.append("")
    out.append("THE LEVEL IS A HUMAN JUDGMENT — this changes nothing and asks for a look.")
    out.append("")
    for r in f:
        rec, sug = r["recorded"], r["suggested"]
        out.append(f"  {r['vendor_ref']}  {r['name']}")
        out.append(f"      recorded {LEVEL_NAME[rec]} ({rec}) · evidence supports "
                   f"{LEVEL_NAME[sug]} ({sug})")
        out.append(f"      two-way contact {r['two_way']} · attempts only {r['attempts_only']} "
                   f"· they gave {r['they_gave']} · we gave {r['we_gave']}")
        out.append(f"      {remedy(r)}")
        out.append("")

    un = result["unjudged_with_evidence"]
    if un:
        out.append(f"  {len(un)} vendor(s) carry evidence but NO recorded level at all. Not a "
                   f"disagreement — nobody has judged them yet:")
        out.append("      " + ", ".join(sorted(r["vendor_ref"] for r in un)))
        out.append("")

    quiet = counts.get("no_evidence", 0)
    if quiet:
        out.append(f"  ({quiet} vendor(s) have no countable events at all and are not listed — "
                   f"mostly Prospective vendors correctly recorded as Prospective.)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    url = db_url()
    if not url:
        print("vendor-level-drift: NOT CONFIGURED (no database credential)", file=sys.stderr)
        return 78

    import psycopg
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("""select vendor_ref, name, recorded, suggested, disagrees, signal,
                              evidence_events, two_way, attempts_only, they_gave, we_gave
                         from v_vendor_level_suggestion""")
        # cur.description is Optional in psycopg's stubs; a select that returned
        # has one, and an empty one means the query shape changed under us.
        assert cur.description is not None, "the view returned no column description"
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    result = classify(rows)
    if args.json:
        print(json.dumps({"counts": result["counts"],
                          "findings": result["findings"],
                          "unjudged_with_evidence": [r["vendor_ref"] for r in
                                                     result["unjudged_with_evidence"]]},
                         indent=2, default=str))
    else:
        print("\n".join(render(result)))
    # Always 0: a disagreement is a prompt for a human, never a failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
