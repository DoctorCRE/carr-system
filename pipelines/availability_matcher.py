#!/usr/bin/env python3
"""
availability_matcher.py — ORDER 14(c), wave2-design §D4. Nightly join of
`availability` rows against open `space_search` specs.

WHAT A MATCH IS AND IS NOT
  A match is a DIGEST LINE. It is written to a report file and nothing else:
  nothing is emailed, texted, pushed, or shown to a client. This script has
  no send path. Follow-up messages use a separate sending tool when Joe asks
  (decision 41906a30); without an ask, the default is a draft.

THE HONEST-REPORT POSTURE (§C, and the reason this file exists before its data)
  Both tables are empty today. That is not a failure and it is not silence: the
  run reports "0 availabilities on record — matcher armed, nothing to match".
  Machinery ships now and speaks honestly below its evidence; conclusions stay
  deferred. Same discipline as the learning jobs' evidence floors.

THE SPEC CONTRACT, AND WHY UNKNOWN KEYS ARE LOUD
  `space_search.spec` is jsonb — "polygon description, size band, filters" — and
  no row has ever been written, so its real shape is not knowable today. The
  matcher understands the keys below, which are exactly the ones the schema can
  answer:

    min_sf, max_sf        -> space.area_amount
    cities                -> building.city   (list, case-insensitive)
    states                -> building.state  (list, case-insensitive)
    sub_types             -> building.sub_type (list)
    max_rate_sf_yr        -> availability.rate_norm_sf_yr
    statuses              -> availability.status (default: available only)

  ANY OTHER KEY IN A SPEC IS REPORTED AS UNHANDLED, per search, in the digest.
  A filter the matcher silently ignores is worse than one it cannot apply: it
  produces confident matches that do not meet the client's actual requirement.
  Absent key = no constraint on that axis, and the digest says which constraints
  actually bit.

  Rate filtering uses `rate_norm_sf_yr` only. Gross-basis rows carry
  norm_owed=true until a tool normalizes them [A5]; those are counted and named
  as "rate not comparable yet" rather than being quietly passed or quietly
  dropped.

Usage:
  DATABASE_URL=... .venv/bin/python pipelines/availability_matcher.py
Read-only — this script has no write path at all. Writes out/availability-matches.md,
one STABLE filename overwritten every run (never an accumulator), and prints its
one-line summary to stdout, which the nightly chain appends to out/nightly.log.

EXIT CODES
  0  ran (including the empty-table state, which is a report, not a failure)
  78 EX_CONFIG — no database URL. The nightly chain treats 78 as NOT CONFIGURED.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out"
REPORT = OUT / "availability-matches.md"

KNOWN_KEYS = {"min_sf", "max_sf", "cities", "states", "sub_types",
              "max_rate_sf_yr", "statuses"}
DEFAULT_STATUSES = ("available",)


def lower_set(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    return {str(x).strip().lower() for x in v if str(x).strip()}


def matches(spec, av):
    """Returns (ok, applied, failed). `applied` names every constraint that was
    actually testable, so the digest can say what the match means."""
    applied, failed = [], []

    def test(name, ok):
        applied.append(name)
        if not ok:
            failed.append(name)

    statuses = lower_set(spec.get("statuses")) or set(DEFAULT_STATUSES)
    test("status", (av["status"] or "").lower() in statuses)

    if spec.get("min_sf") is not None:
        test("min_sf", av["area"] is not None and float(av["area"]) >= float(spec["min_sf"]))
    if spec.get("max_sf") is not None:
        test("max_sf", av["area"] is not None and float(av["area"]) <= float(spec["max_sf"]))
    cities = lower_set(spec.get("cities"))
    if cities:
        test("cities", (av["city"] or "").lower() in cities)
    states = lower_set(spec.get("states"))
    if states:
        test("states", (av["state"] or "").lower() in states)
    subs = lower_set(spec.get("sub_types"))
    if subs:
        test("sub_types", (av["sub_type"] or "").lower() in subs)
    if spec.get("max_rate_sf_yr") is not None:
        # normalized-or-owed [A5]: an un-normalized gross rate is NOT silently
        # passed and NOT silently dropped — it fails the test and the digest
        # names why.
        test("max_rate_sf_yr",
             av["rate_norm"] is not None
             and float(av["rate_norm"]) <= float(spec["max_rate_sf_yr"]))

    return (not failed), applied, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-statuses", action="store_true",
                    help="consider every availability row, not just the newest per space")
    a = ap.parse_args()

    # [ORDER 19a] CARR_DB_JOBS_URL first: one nightly-jobs role for every
    # unattended pipeline. The exporter credential is kept as a fallback because
    # it still runs the honest-empty report, and the older per-script name stays
    # accepted so nothing that already works stops working.
    url = (os.environ.get("CARR_DB_JOBS_URL")
           or os.environ.get("CARR_DB_MATCHER_URL")
           or os.environ.get("CARR_DB_EXPORTER_URL")
           or os.environ.get("DATABASE_URL"))
    if not url:
        print("availability_matcher: NOT CONFIGURED — no database URL "
              "(CARR_DB_JOBS_URL, CARR_DB_MATCHER_URL, CARR_DB_EXPORTER_URL or "
              "DATABASE_URL). Nothing attempted.", file=sys.stderr)
        return 78

    OUT.mkdir(exist_ok=True)
    L = []
    A = L.append

    with psycopg.connect(url) as conn:
        try:
            n_av = conn.execute("select count(*) from availability").fetchone()[0]
        except psycopg.errors.InsufficientPrivilege:
            # MEASURED, not anticipated: the exporter credential — the only DB
            # credential that exists on this Mac — is views-only by design
            # (amendment 11), and no view exposes availability or space_search.
            # Say so precisely and exit NOT CONFIGURED; a stack trace at 2am
            # tells the morning nothing, and a chain FAIL every night trains
            # people to stop reading the log.
            print("availability_matcher: NOT CONFIGURED — this credential is "
                  "views-only and no view exposes `availability` / `space_search`. "
                  "The answer exists as of ORDER 19a: the `carr_jobs` role holds "
                  "exactly the reads this needs (availability, space_search, and "
                  "column-scoped space / building / client / party). Set "
                  "CARR_DB_JOBS_URL in ~/.config/carr/db.env. "
                  "Nothing attempted, nothing written.", file=sys.stderr)
            return 78
        n_se = conn.execute(
            "select count(*) from space_search where status='open'").fetchone()[0]

        searches, avails, rows, unhandled, owed = [], [], [], [], 0
        if n_av and n_se:
            searches = conn.execute("""
                select s.id, s.spec, coalesce(c.roster_ref,''), p.name
                  from space_search s
                  join client c on c.id = s.client_id
                  join party p on p.id = c.party_id
                 where s.status = 'open'
                 order by c.roster_ref""").fetchall()
            # Availability is APPEND-ONLY: re-pulls append, never overwrite. The
            # NEWEST observation per space is the current truth; --all-statuses
            # walks the whole history instead.
            #
            # `distinct on`, not `observed_at = max(observed_at)`: two rows
            # written in ONE transaction share an observed_at, because the column
            # defaults to now() and now() is the TRANSACTION timestamp. The max()
            # form then returns BOTH and the digest shows a superseded rate beside
            # its replacement. Measured on the rehearsal branch, not reasoned
            # about. distinct on with an explicit tiebreak is deterministic under
            # a tie; real pulls land in separate transactions and never tie.
            pick = ("" if a.all_statuses else
                    "distinct on (av.space_id)")
            order = ("av.observed_at desc" if a.all_statuses else
                     "av.space_id, av.observed_at desc, av.id desc")
            avails = conn.execute("""
                select %s av.id, av.status, av.rate_norm_sf_yr, av.norm_owed,
                       av.available_on, av.observed_at::date, av.source,
                       sp.area_amount, sp.suite,
                       b.city, b.state, b.sub_type, b.address, b.name
                  from availability av
                  join space sp on sp.id = av.space_id
                  join building b on b.id = sp.building_id
                 order by %s""" % (pick, order)).fetchall()
            owed = sum(1 for r in avails if r[3])

            for sid, spec, ref, cname in searches:
                spec = spec or {}
                extra = sorted(set(spec) - KNOWN_KEYS)
                if extra:
                    unhandled.append((ref, cname, extra))
                for av in avails:
                    rec = dict(id=av[0], status=av[1], rate_norm=av[2], owed=av[3],
                               available_on=av[4], observed=av[5], source=av[6],
                               area=av[7], suite=av[8], city=av[9], state=av[10],
                               sub_type=av[11], address=av[12], bname=av[13])
                    ok, applied, _failed = matches(spec, rec)
                    if ok:
                        rows.append((ref, cname, rec, applied))

    A("# Availability × space-search matches — nightly digest")
    A("")
    A("Generated %s · **nothing here has been sent to anyone.** These are digest "
      "lines for Joe to read and act on; the matcher has no send path."
      % datetime.now(timezone.utc).isoformat(timespec="seconds"))
    A("")
    if not n_av:
        A("**%d availabilities on record — matcher armed, nothing to match.** "
          "(%d open space searches.) The join runs nightly and will report the "
          "moment either side has data; an empty run is a report, not a failure."
          % (n_av, n_se))
        A("")
    elif not n_se:
        A("**%d availabilities on record, but 0 open space searches — matcher "
          "armed, nothing to match against.**" % n_av)
        A("")
    else:
        A("Availabilities considered: **%d** (of %d rows on record) · open searches: "
          "**%d** · matches: **%d**" % (len(avails), n_av, n_se, len(rows)))
        if owed:
            A("")
            A("_%d availability row(s) carry `norm_owed` — a gross rate that no tool has "
              "normalized yet. Any search filtering on rate excludes them, by design: an "
              "un-normalized rate is not comparable and is never assumed to pass._" % owed)
        A("")
        A("## Matches")
        A("")
        if rows:
            A("| client | space | city | area | rate $/sf/yr | status | observed | filters applied |")
            A("|---|---|---|---|---|---|---|---|")
            for ref, cname, r, applied in rows:
                where = ", ".join(x for x in [r["bname"], r["address"], r["suite"]] if x)
                A("| %s %s | %s | %s | %s | %s | %s | %s | %s |"
                  % (ref, cname, where, r["city"] or "", r["area"] or "",
                     r["rate_norm"] if r["rate_norm"] is not None else "not comparable",
                     r["status"], r["observed"], ", ".join(applied) or "none"))
        else:
            A("None. Every open search was tested against every current availability "
              "and nothing cleared its filters.")
        A("")
        if unhandled:
            A("## Spec keys this matcher does NOT understand")
            A("")
            A("A filter that is silently ignored produces confident matches that do not "
              "meet the requirement. These are named instead:")
            A("")
            A("| search | unhandled keys |")
            A("|---|---|")
            for ref, cname, extra in unhandled:
                A("| %s %s | %s |" % (ref, cname, ", ".join("`%s`" % k for k in extra)))
            A("")

    REPORT.write_text("\n".join(L) + "\n")

    if not n_av:
        print("availability matcher — 0 availabilities on record, matcher armed, "
              "nothing to match (%d open searches) · report %s" % (n_se, REPORT))
    else:
        print("availability matcher — availabilities %d · open searches %d · matches %d "
              "· unhandled spec keys on %d search(es) · report %s"
              % (n_av, n_se, len(rows), len(unhandled), REPORT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
