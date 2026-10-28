#!/usr/bin/env python3
"""
trigger-grant-selftest.py — fixtures for ops/trigger-grant-check.py, written
before it (rule e65efc68).

THE RULE, 5409731b: a new trigger changes the permission surface of every verb
that writes its table. Before the migration ships, list every table the trigger
body reads and confirm the WRITING role holds SELECT on each. A plain foreign-key
check runs as the referenced table's owner and needs no grant; an invoker-rights
plpgsql trigger runs AS THE CALLER and dies on any ungranted read.

WHAT IT COST WHEN SKIPPED, from the rule's own text: 0060's side-validation
trigger read participant_role, which 0017 had left ungranted. set-lead broke
silently in production for five days (2026-08-03 to 2026-08-08), invisible
because the existing lead rows were import-written and every rehearsal ran as
owner. Grants never fire for the owner, so rehearsing as owner cannot catch it.

WHY THIS ASKS POSTGRES INSTEAD OF READING THE SQL. A static version was
prototyped first and it reported ZERO gaps — not because there were none, but
because its grant parsing found no writers at all and the loop never ran. Green
meant "parsed nothing". The permission model here is genuinely subtle: 0004's
`grant ... on all tables in schema public` is a ONE-TIME grant that does not
reach any table created afterwards, which is precisely why participant_role
(created in 0060) was ungranted. Reimplementing that in a regex is where false
confidence lives. has_table_privilege() and has_column_privilege() are the
authority, and pg_trigger/pg_proc enumerate the triggers, so the only textual
step left is pulling table names out of prosrc — and even those are validated
against pg_class, so a CTE name or a keyword cannot become a phantom table.

THE COLUMN-GRANT TRAP, which a table-level check gets wrong in the direction
that matters. 0078 fixed the incident with `grant select (side) on
participant_role to carr_writer`. has_table_privilege() returns FALSE for that
— so a table-only check flags today's healthy schema as broken, which is a
false DENY on a fix that is already in place. Access is table-level OR any
column-level.

THE STATED GAP, documented rather than papered over: a column grant that is too
NARROW — present, but not covering the column the trigger actually reads — is
not detected, because deciding which columns a trigger body reads means parsing
plpgsql, and a guess there would be exactly the kind that gets a check deleted.
What is caught is no read access at all, which is what the incident was.

WHAT MUST STAY TRUE:
  1. The historical incident is caught: revoke the grant that fixed it and the
     check fails, naming the trigger, the table read, and the role.
  2. Today's schema PASSES, column grant and all. This is the direction a
     table-only check loses.
  3. A SECURITY DEFINER function is never flagged — it does not run as caller.
  4. A role that cannot write the trigger's table is never asked about.
  5. Names in prosrc that are not real tables never become findings.
  6. With no database it SKIPS rather than passing, because a check that cannot
     look and reports OK is worse than one that says it did not run.

RUNNING IT. Needs a throwaway Postgres; skips cleanly without one:

    CARR_CI_DATABASE_URL=postgres://…/throwaway .venv/bin/python ops/trigger-grant-selftest.py
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "ops" / "trigger-grant-check.py"

# The postgres CLIENT lookup uses tools/db-tap.py. Loading by path
# is how every ops gate reaches tools/db-tap.py, whose hyphenated filename cannot
# be imported normally.
_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("trigger-grant-selftest: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

passed = 0
failures: list[str] = []


import pathlib, sys  # noqa: E401
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root, for lib/
from lib.gate_check import Check  # noqa: E402

check = Check(failures=failures, counters={"passed": "pass"}, ns=globals())


print("\nops/trigger-grant-check.py — an invoker-rights trigger's reads are "
      "granted to the role that fires it (5409731b)")

if not CHECK.exists():
    print(f"  FAIL  the check does not exist at {CHECK}")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)

DSN = os.environ.get("CARR_CI_DATABASE_URL", "")


def run(env=None):
    p = subprocess.run([sys.executable, str(CHECK)], capture_output=True,
                       text=True, env=dict(os.environ, **(env or {})))
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ── 6. no database means SKIP, never a silent pass ─────────────────────────
rc, out = run({"CARR_CI_DATABASE_URL": ""})
check("with no database it SKIPS and says so", rc == 0 and "skip" in out.lower(),
      out[:140])

def psql(sql, dsn=DSN):
    # Not the bare name: with no client installed that failed as FileNotFoundError
    # from inside subprocess, naming neither the missing dependency nor the fix.
    return subprocess.run([db_tap.psql_bin(), "-d", dsn, "-At", "-c", sql],
                          capture_output=True, text=True)


def anchored():
    """Is the CARR schema actually loaded in this database?

    THE GATES CLASS RUNS THIS SUITE, and it inherits whatever
    CARR_CI_DATABASE_URL is set for the run — which on a full ops/ci.sh is the
    throwaway the MIGRATION class is about to build, and is empty when the
    gates class reaches it. The first full run failed here for exactly that
    reason: 0 triggers, so every database case failed against an empty
    database. A precondition it cannot meet is a SKIP, and it says so, because
    a suite that quietly passes on an empty database is worse than one that
    does not run.
    """
    r = psql("select to_regclass('public.participant_role') is not null")
    return r.returncode == 0 and r.stdout.strip() == "t"


if not DSN or not anchored():
    why = ("no CARR_CI_DATABASE_URL" if not DSN
           else "the database at CARR_CI_DATABASE_URL has no CARR schema loaded")
    print(f"\n  SKIP — database cases not run: {why}.")
    print(f"\n{passed} check(s) passed"
          + (f", {len(failures)} FAILED: {failures}" if failures else ""))
    sys.exit(1 if failures else 0)


# ── 2. today's schema passes, column grant and all ─────────────────────────
rc, out = run()
check("today's schema PASSES (the 0078 fix is a COLUMN grant)", rc == 0, out[:400])
check("the passing line says what it checked",
      "trigger" in out.lower(), out[:160])

# ── 1. the historical incident, seeded back in ─────────────────────────────
# Revoke exactly what 0078 added and the five-day production outage reappears.
psql("revoke select (side) on participant_role from carr_writer")
rc, out = run()
check("revoking the 0078 grant makes the check FAIL", rc != 0, out[:300])
check("the failure names the trigger", "deal_participant_side" in out, out[:300])
check("the failure names the table read", "participant_role" in out, out[:300])
check("the failure names the role that fires it", "carr_writer" in out, out[:300])
psql("grant select (side) on participant_role to carr_writer")

rc, _ = run()
check("restoring the grant makes it pass again", rc == 0,
      "both directions, or the carve-out stops closing")

# ── 3. SECURITY DEFINER is never flagged ───────────────────────────────────
psql("""
create table if not exists zz_probe_read (id int primary key, v text);
create table if not exists zz_probe_write (id int primary key);
create or replace function zz_probe_definer() returns trigger language plpgsql
  security definer as $fn$ begin perform 1 from zz_probe_read; return new; end $fn$;
drop trigger if exists zz_probe_definer_t on zz_probe_write;
create trigger zz_probe_definer_t before insert on zz_probe_write
  for each row execute function zz_probe_definer();
grant insert on zz_probe_write to carr_writer;
""")
rc, out = run()
check("a SECURITY DEFINER trigger is not flagged", rc == 0, out[:300])

# ── 4. a role that cannot write the table is never asked about ─────────────
psql("""
create or replace function zz_probe_invoker() returns trigger language plpgsql
  as $fn$ begin perform 1 from zz_probe_read; return new; end $fn$;
drop trigger if exists zz_probe_definer_t on zz_probe_write;
drop trigger if exists zz_probe_invoker_t on zz_probe_write;
create trigger zz_probe_invoker_t before insert on zz_probe_write
  for each row execute function zz_probe_invoker();
revoke all on zz_probe_write from carr_writer;
revoke all on zz_probe_read from carr_writer;
""")
rc, out = run()
check("a trigger whose table no app role can write is not flagged", rc == 0,
      out[:300])

# now let carr_writer write it, with no read grant — this MUST be caught
psql("grant insert on zz_probe_write to carr_writer")
rc, out = run()
check("giving a role write access with no read grant IS caught", rc != 0,
      out[:300])

# ── 5. a name in prosrc that is not a table is never a finding ─────────────
psql("""
revoke all on zz_probe_write from carr_writer;
create or replace function zz_probe_cte() returns trigger language plpgsql
  as $fn$ begin perform (with zz_not_a_table as (select 1 x)
                         select count(*) from zz_not_a_table); return new; end $fn$;
drop trigger if exists zz_probe_invoker_t on zz_probe_write;
drop trigger if exists zz_probe_cte_t on zz_probe_write;
create trigger zz_probe_cte_t before insert on zz_probe_write
  for each row execute function zz_probe_cte();
grant insert on zz_probe_write to carr_writer;
""")
rc, out = run()
check("a CTE name in the body is not treated as a table", rc == 0, out[:300])
check("the phantom name appears in no finding", "zz_not_a_table" not in out,
      out[:200])

# ── clean up the probe objects ─────────────────────────────────────────────
psql("""
drop trigger if exists zz_probe_cte_t on zz_probe_write;
drop function if exists zz_probe_cte();
drop function if exists zz_probe_invoker();
drop function if exists zz_probe_definer();
drop table if exists zz_probe_write;
drop table if exists zz_probe_read;
""")

rc, out = run()
check("the tree is left exactly as it was found (clean pass)", rc == 0, out[:200])

print(f"\n{passed} check(s) passed"
      + (f", {len(failures)} FAILED: {failures}" if failures else ""))
sys.exit(1 if failures else 0)
