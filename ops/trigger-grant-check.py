#!/usr/bin/env python3
"""ops/trigger-grant-check.py — an invoker-rights trigger's reads must be
granted to the role that fires it (rule 5409731b).

THE RULE: a new trigger changes the permission surface of every verb that
writes its table. Before that migration ships, list every table the trigger
body reads and confirm the WRITING role holds SELECT on each. A plain
foreign-key check runs as the referenced table's owner and needs no grant; an
invoker-rights plpgsql trigger runs AS THE CALLER and dies on any ungranted
read.

WHAT IT COST WHEN SKIPPED, from the rule's own text. 0060's side-validation
trigger read participant_role, which 0017 had left ungranted on the reasoning
that foreign-key checks run as owner — true, and irrelevant to a trigger read.
set-lead broke silently in production for FIVE DAYS (2026-08-03 to 2026-08-08).
It was invisible because the existing lead rows were import-written and every
rehearsal ran as owner, and grants never fire for the owner. That is the 0076
blind spot the rule names, and it is why this check must run AS A ROLE QUESTION
against a real database rather than as a read-through of the SQL.

WHY THIS ASKS POSTGRES INSTEAD OF READING THE MIGRATIONS. A static version was
prototyped first, and it reported ZERO gaps — not because there were none, but
because its grant parsing found no writers at all, so the comparison loop never
ran once. Green meant "parsed nothing". The permission model here is genuinely
subtle: 0004's `grant ... on all tables in schema public` is a ONE-TIME grant
that does not reach any table created afterwards, which is exactly why
participant_role, created in 0060, was ungranted while dozens of older tables
were fine. Reimplementing that in a regex is where false confidence lives.

So the database answers both halves it can answer. pg_trigger and pg_proc
enumerate the triggers, their functions, and whether each is SECURITY DEFINER.
has_table_privilege() and has_column_privilege() answer every permission
question. The only textual step left is pulling table names out of prosrc, and
even those are intersected with pg_class, so a CTE name, an alias or a plpgsql
keyword cannot become a phantom table.

THE COLUMN-GRANT TRAP, which cost two wrong designs before this one. 0078 fixed
the incident with a COLUMN-scoped grant: `grant select (side) on
participant_role to carr_writer`. has_table_privilege() returns FALSE for that,
so a TABLE-ONLY test reports today's healthy schema as broken — a false refusal
aimed at a fix already in place.

The obvious repair, "table-level OR any column-level", was written next and the
fixture killed it. Revoking exactly the grant 0078 added left an unrelated
`select (slug)` in place, so "any column" stayed true and the check passed while
the trigger would still have died in production. It under-detected the one
incident it exists to catch.

So the question is asked per COLUMN: the identifiers in the function body,
intersected with the read table's real column list, must each be SELECT-able by
the writing role. Intersecting with pg_attribute is what keeps a phantom name
from being required, and NEW.x / OLD.x are stripped first because those are the
TRIGGERING table's columns rather than the read table's. Where a body names the
table but none of its columns (`select 1 from t`, `count(*)`), any read access
is enough — there is no column to be precise about.

SKIPS WITHOUT A DATABASE rather than passing. A check that cannot look and
reports OK is worse than one that says it did not run.

Fixtures: ops/trigger-grant-selftest.py.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

# The postgres CLIENT lookup uses tools/db-tap.py. Loading by path
# is how every ops gate reaches tools/db-tap.py, whose hyphenated filename cannot
# be imported normally.
_REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("db_tap", _REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("trigger-grant-check: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)

# `from x` / `join x` in a function body. Every hit is intersected with
# pg_class before it counts, so this can be generous without inventing tables.
READ = re.compile(r"\b(?:from|join)\s+([a-z_][\w.]*)", re.I)

# The application roles. carr_ci is the harness's own login, not an app role.
ROLE_PREFIX = "carr\\_%"
EXCLUDED_ROLES = {"carr_ci"}

SEP = "\x1f"


def psql(dsn, sql):
    # Not the bare name: with no client installed that failed as FileNotFoundError
    # from inside subprocess, naming neither the missing dependency nor the fix.
    p = subprocess.run([db_tap.psql_bin(), "-d", dsn, "-At", "-F", SEP,
                        "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return [line.split(SEP) for line in p.stdout.strip().split("\n") if line]


def main():
    dsn = os.environ.get("CARR_CI_DATABASE_URL", "").strip()
    if not dsn:
        print("trigger-grant: SKIP — no CARR_CI_DATABASE_URL. This asks a live "
              "database whether a role can read what a trigger reads, and there "
              "is no text-only version of that question worth trusting.")
        return 0

    try:
        # SCHEMA-QUALIFIED THROUGHOUT. A bare name is not resolvable: this
        # database has an `ops` schema as well as `public`, and '<name>'::regclass
        # follows search_path, so the first full run died on
        # `relation "capability_verification" does not exist`. Bodies name tables
        # bare, so a bare -> qualified map is kept alongside.
        rows = psql(dsn, """
            select n.nspname, c.relname from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where c.relkind in ('r','p','v','m')
              and n.nspname not in ('pg_catalog','information_schema')""")
        qualified = {f"{ns}.{rel}" for ns, rel in rows}
        by_bare: dict[str, list[str]] = {}
        for ns, rel in rows:
            by_bare.setdefault(rel.lower(), []).append(f"{ns}.{rel}")

        triggers = psql(dsn, r"""
            select n.nspname || '.' || c.relname, t.tgname, p.proname, p.prosecdef,
                   replace(replace(p.prosrc, E'\n', ' '), E'\t', ' ')
            from pg_trigger t
            join pg_class c on c.oid = t.tgrelid
            join pg_namespace n on n.oid = c.relnamespace
            join pg_proc  p on p.oid = t.tgfoid
            where not t.tgisinternal
            order by 1, 2""")

        roles = [r[0] for r in psql(dsn, f"""
            select rolname from pg_roles
            where rolname like '{ROLE_PREFIX}' order by 1""")
            if r[0] not in EXCLUDED_ROLES]
    except RuntimeError as exc:
        print(f"trigger-grant: CANNOT READ the database — {exc}", file=sys.stderr)
        return 1

    def writes(role, table):
        r = psql(dsn, f"select has_table_privilege('{role}','{table}','INSERT') "
                      f"or has_table_privilege('{role}','{table}','UPDATE')")
        return r[0][0] == "t"

    def columns_of(table):
        return [r[0] for r in psql(dsn, f"""
            select attname from pg_attribute
            where attrelid = '{table}'::regclass
              and attnum > 0 and not attisdropped""")]

    def missing_reads(role, table, body):
        """The columns of `table` this body names that `role` cannot SELECT.

        WHY COLUMNS AND NOT THE TABLE. The 0078 fix is column-scoped, so a
        table-level test alone reports today's healthy schema as broken. But
        "table-level OR any column" was tried first and the fixture killed it:
        revoking exactly the grant 0078 added still left an unrelated
        `select (slug)` in place, so the check passed while the trigger would
        still have died in production. Under-detecting the one incident this
        exists to catch is not a trade worth taking.

        WHICH COLUMNS. Identifiers in the body intersected with the read
        table's real column list, so a phantom name cannot be required. NEW.x
        and OLD.x are stripped first — those are the TRIGGERING table's
        columns, not this one's.
        """
        if psql(dsn, f"select has_table_privilege('{role}','{table}','SELECT')"
                )[0][0] == "t":
            return []
        stripped = re.sub(r"\b(?:new|old)\.\w+", " ", body, flags=re.I)
        named = set(re.findall(r"[a-z_][\w]*", stripped, re.I))
        wanted = [c for c in columns_of(table) if c.lower() in
                  {n.lower() for n in named}]
        if not wanted:
            # The body names the table but none of its columns by name —
            # `select 1 from t`, `count(*)`. Any read at all is enough.
            any_col = psql(dsn, f"""
                select coalesce(bool_or(has_column_privilege(
                         '{role}','{table}',a.attname,'SELECT')), false)
                from pg_attribute a
                where a.attrelid = '{table}'::regclass
                  and a.attnum > 0 and not a.attisdropped""")[0][0] == "t"
            return [] if any_col else ["(any column)"]
        return [c for c in wanted if psql(
            dsn, f"select has_column_privilege('{role}','{table}','{c}','SELECT')"
        )[0][0] != "t"]

    findings = []
    checked = 0
    for table, tgname, proname, secdef, src in triggers:
        if secdef == "t":
            continue                      # runs as owner; grants never apply
        # Resolve each bare name the body mentions to a real qualified table.
        # An ambiguous name prefers the trigger table's own schema.
        schema = table.split(".")[0]
        reads = set()
        for raw in READ.findall(src):
            if "." in raw and raw in qualified:
                reads.add(raw)
                continue
            candidates = by_bare.get(raw.split(".")[-1].lower(), [])
            if len(candidates) == 1:
                reads.add(candidates[0])
            elif candidates:
                same = [c for c in candidates if c.startswith(schema + ".")]
                reads.add(same[0] if same else candidates[0])
        reads.discard(table)
        if not reads:
            continue
        checked += 1
        for role in roles:
            if not writes(role, table):
                continue                  # this role cannot fire it
            for read_table in sorted(reads):
                gaps = missing_reads(role, read_table, src)
                if gaps:
                    findings.append((tgname, table, read_table, role, proname,
                                     gaps))

    if not findings:
        print(f"trigger-grant: OK — {len(triggers)} trigger(s), {checked} of them "
              f"invoker-rights with table reads; every writing role can read "
              f"what its trigger reads")
        return 0

    print(f"TRIGGER READS A TABLE ITS FIRING ROLE CANNOT SELECT — "
          f"{len(findings)} case(s)\n")
    print("An invoker-rights trigger runs AS THE CALLER. The role below can write")
    print("the table the trigger guards, so the trigger fires for it, and it has")
    print("no SELECT on a table the trigger body reads. In production that write")
    print("path dies — silently, if nothing exercises it as that role.\n")
    for tgname, table, read_table, role, proname, gaps in findings:
        print(f"  trigger {tgname} on {table} (function {proname})")
        print(f"      reads {read_table}; role {role} writes {table} but cannot "
              f"SELECT {', '.join(gaps)} on {read_table}")
    print("\nFIX: grant the read to the writing role in the SAME migration that")
    print("adds the trigger. A column-scoped grant is enough and is preferred —")
    print("0078 fixed the 2026-08-03 outage with `grant select (side) on")
    print("participant_role to carr_writer`.")
    print("\nRehearsing as the owner cannot catch this: grants never fire for the")
    print("owner, which is why set-lead was broken for five days with every")
    print("rehearsal green.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
