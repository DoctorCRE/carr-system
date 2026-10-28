#!/usr/bin/env python3
"""forgetting-check.py — the health rows for loop #212 (migration 0071).

Invoked by tools/health-check.py the same way ops/rules-live-check.py is (venv
python, stdlib parent). Prints ONE line per lane, first line first:

  OK|WARN forgetting — <expired> expired + <unstamped> unstamped re-verifies · ingest new=<n> oldest=<d>d · growth: <table +delta/d, ...>

Also WRITES today's growth snapshot (idempotent ON CONFLICT DO NOTHING) via the
jobs credential — the snapshot ride-along is what makes slope computable at all,
and the health check is the one daily process guaranteed to run.

Exit 0 = OK, 1 = WARN thresholds crossed, 2 = SKIP (no credential / table not
yet migrated — prints SKIP line, never a traceback).
"""

import os
import sys

TRACKED = ["event", "tool_call", "tool_read_count", "record_flag", "ingest_inbox",
           "activity", "source_capture", "loop_item"]
# tool_call is the exception to the no-DELETE posture below since migration
# 1048: a 30-day idempotency replay window, enforced by its tool_call_expire
# trigger (up to 20 expired rows per insert). Its growth line should flatten
# near 30 days of writes; a rising slope means the trigger stopped running.
# tool_read_count (1041): the read-side sibling of tool_call, one counter per
# day, verb, actor, sponsor, door and outcome; it replaced 0108's row-per-read
# tool_read_call, which stopped growing. Like tool_call, record_flag,
# activity, source_capture and loop_item above, carr_jobs holds no SELECT on
# it (1041 grants select only to carr_writer/carr_reader/carr_exporter,
# deliberately not carr_jobs — ORDER 19(a)), so this entry's count query is
# skipped the same way its siblings' already are.
WARN_INGEST_NEW = 50          # unprocessed intake rows before the row goes amber
WARN_EXPIRED = 25             # re-verify queue depth before amber

ENV = os.path.expanduser("~/.config/carr/db.env")


def main() -> int:
    if not os.path.exists(ENV):
        print("SKIP: no db.env")
        return 2
    url = None
    for line in open(ENV):
        if line.startswith("CARR_DB_JOBS_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        print("SKIP: no CARR_DB_JOBS_URL in db.env")
        return 2
    try:
        import psycopg
        c = psycopg.connect(url)
        cur = c.cursor()
    except Exception as e:
        print(f"SKIP: connect failed ({type(e).__name__}: {str(e).splitlines()[0]})")
        return 2

    try:
        cur.execute("select 1 from information_schema.tables where table_name='growth_snapshot'")
        if not cur.fetchone():
            print("SKIP: migration 0071 not applied here yet")
            return 2

        # snapshot ride-along (idempotent per day)
        for t in TRACKED:
            try:
                cur.execute(f"select count(*) from {t}")
                count_row = cur.fetchone()
                if count_row is None:
                    raise RuntimeError(f"count query for {t} returned no row")
                n = count_row[0]
            except Exception:
                c.rollback()
                continue   # a table the jobs role cannot count is skipped, not fatal
            cur.execute(
                "insert into growth_snapshot (taken_on, table_name, row_count) "
                "values (current_date, %s, %s) on conflict do nothing", (t, n))
        c.commit()

        cur.execute("select reason, count(*) from v_expired_verification group by reason")
        rev: dict[str, int] = dict(cur.fetchall())
        expired, unstamped = rev.get("expired", 0), rev.get("unstamped_volatile", 0)

        cur.execute("select status, n, oldest_age_days from v_ingest_backlog")
        backlog = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        new_n, new_age = backlog.get("new", (0, 0))

        cur.execute(
            "select table_name, delta, days_between from v_growth_slope "
            " where taken_on = current_date and delta is not null")
        slopes = [f"{t} +{d}/{max(db or 1,1)}d" for t, d, db in cur.fetchall() if d]

        warn = (new_n > WARN_INGEST_NEW) or ((expired + unstamped) > WARN_EXPIRED)
        tag = "WARN" if warn else "OK"
        slope_txt = ", ".join(slopes) if slopes else "first snapshot today, slope from tomorrow"
        print(f"{tag} forgetting — {expired} expired + {unstamped} unstamped re-verifies · "
              f"ingest new={new_n} oldest={new_age}d · growth: {slope_txt} · "
              f"on breach: enrichment-weekly consumes v_expired_verification; "
              f"intake backlog routes to loop #354, triage the unfiled calendar rows")
        return 1 if warn else 0
    except Exception as e:
        print(f"SKIP: query failed ({type(e).__name__}: {str(e).splitlines()[0]})")
        return 2


if __name__ == "__main__":
    sys.exit(main())
