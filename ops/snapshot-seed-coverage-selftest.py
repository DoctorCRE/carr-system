#!/usr/bin/env python3
"""Paired suite for ops/snapshot-seed-coverage.py.

REWRITTEN 2026-08-27 after an independent fresh-context review FAILED the first
version on two blind spots this repository's own migrations already exercised:
a function defined and then CALLED at migration time (its body was stripped as
"runtime code"), and row-landing DML that is not the literal words INSERT INTO.
Every gap that review named has a case below, and the two decisive ones have a
case built from the real counterexample rather than a synthetic stand-in.

PROVEN TO FAIL, NOT MERELY TO PASS. The check this exercises exists because five
snapshot traps were each discovered days late by a db-gate in CI. A sixth-instance
detector that only ever returns clean would be worse than nothing: it would read
as coverage. So every case below drives a real refusal with a real payload, and
the negative controls pin the two ways a false alarm would show up — a migration
that is still PENDING loses nothing on a rebuild, and a table that is classified
is not a finding.

Fully offline: it composes synthetic migrations and synthetic artifacts in a
temporary directory. It never reads production, never reads db/schema.sql, and
never writes inside the repository.
"""

import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile
import contextlib
import io
import time

REPO = pathlib.Path(__file__).resolve().parent.parent

# INSERT provenance belongs to one live database's transactions. A rebuild
# carries schema, never old xid8 values that could collide with its own xids.
coverage = json.loads((REPO / "ops/config/snapshot-seed-coverage.json").read_text())
assert "ops.rule_insert_provenance" in coverage["excluded"]


def load_module():
    spec = importlib.util.spec_from_file_location(
        "snapshot_seed_coverage", REPO / "ops" / "snapshot-seed-coverage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER_HEADER = "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n"


def artifact(applied, data_blocks=()):
    """A minimal snapshot: DDL that MENTIONS tables, then the data region.

    The DDL half is not padding. A COMMENT quoting `insert into party ...` and a
    function body containing `insert into ops.run ...` are both real shapes from
    db/schema.sql, and both made an early version of this check report business
    and runtime tables as carried. They stay here as the regression.
    """
    ddl = (
        "--\n-- PostgreSQL database dump\n--\n\n"
        "CREATE FUNCTION public.record_run() RETURNS void\n"
        "    LANGUAGE plpgsql\n"
        "    AS $$\nbegin\n  insert into ops.run (kind) values ('x');\nend $$;\n\n"
        "COMMENT ON FUNCTION public.org_party_id(p_name text) IS "
        "'Replaces the blind `insert into party ... values (''org'', $1)` at tools.js.';\n\n"
    )
    ledger = LEDGER_HEADER + "".join(
        f"{name}\tabc123\t2026-08-01 00:00:00+00\n" for name in applied) + "\\.\n\n\n"
    return ddl + ledger + "".join(data_blocks)


def copy_block(table, columns="(a, b)"):
    return f"COPY {table} {columns} FROM stdin;\n1\tvalue\n\\.\n\n\n"


def empty_copy_block(table, columns="(a, b)"):
    """The shape pg_dump --data-only emits for a table holding NO rows.

    The header is identical to a block with rows. Matching it proved the table was
    considered, never that anything was carried, so a carried table emptied in
    production read as present.
    """
    return f"COPY {table} {columns} FROM stdin;\n\\.\n\n\n"


def insert_block(table):
    return f"-- CARR APPENDED BLOCK\ninsert into {table} (a) values ('x') on conflict do nothing;\n\n"


def build_repo(tmp, migrations, classification):
    root = pathlib.Path(tmp)
    (root / "migrations").mkdir(parents=True, exist_ok=True)
    (root / "ops" / "config").mkdir(parents=True, exist_ok=True)
    for name, body in migrations.items():
        (root / "migrations" / name).write_text(body, encoding="utf-8")
    (root / "ops" / "config" / "snapshot-seed-coverage.json").write_text(
        json.dumps(classification), encoding="utf-8")
    return str(root)


FAILURES: list[str] = []


def case(name, ok):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


def summarise(failures):
    return " || ".join(f.split("\n")[0] for f in failures)


def main():
    module = load_module()
    print("ops/snapshot-seed-coverage-selftest.py")

    seeding = ("create table ops.widget (k text);\n"
               "insert into ops.widget (k) values ('alpha'),('beta');\n")
    # A migration whose ONLY migration-time insert is a self-test probe inside a DO
    # block that asserts the row does not survive. ops.rule_pack is exactly this
    # shape in 0291, and reading it as a seed would be a false alarm.
    probe_only = ("do $$\nbegin\n"
                  "  insert into ops.probe_only (k) values ('p');\n"
                  "  raise exception 'must not survive';\n"
                  "exception when others then null;\nend $$;\n")
    runtime_only = ("create function ops.f() returns void language plpgsql as $$\n"
                    "begin\n  insert into ops.never_seeded (k) values ('r');\nend $$;\n")

    with tempfile.TemporaryDirectory() as tmp:
        # Lead UUIDs and creation transactions are business evidence, never
        # reference vocabulary in a structure-only rebuild.
        lead_table = "ops.doc_whats_new_lead_creation"
        classification = json.loads((REPO / "ops/config/snapshot-seed-coverage.json").read_text())
        reason = classification["excluded"].get(lead_table)
        lead_repo = build_repo(tmp + "/catchup", {
            "0766_doc_whats_new_repair.sql": (REPO / "migrations/0766_doc_whats_new_repair.sql").read_text(),
        }, {"carried": {}, "excluded": {lead_table: reason} if reason else {}})
        lead_artifact = artifact(["0766_doc_whats_new_repair.sql"])
        case("catch-up creation evidence is classified and omitted from a rebuild",
             module.check(lead_repo, lead_artifact) == [])
        case("catch-up creation evidence refuses if copied into the snapshot",
             "EXCLUDED" in summarise(module.check(lead_repo, lead_artifact + copy_block(lead_table))))
        # ---------------------------------------------------------------- 1
        repo = build_repo(tmp + "/a", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_seed.sql"]))
        case("the sixth instance: an unclassified seeded table refuses",
             len(found) == 1 and "UNCLASSIFIED" in found[0] and "ops.widget" in found[0])
        case("the refusal names the migration that seeded it",
             found and "0100_seed.sql" in found[0])

        # ---------------------------------------------------------------- 2
        found = module.check(repo, artifact([]))
        case("negative control: a PENDING seeding migration is not a finding "
             "(it still replays, so a rebuild loses nothing)", found == [])

        pending_excluded = build_repo(
            tmp + "/pending-excluded",
            {"0100_seed.sql": seeding},
            {"carried": {}, "excluded": {"ops.widget": "future runtime evidence"}},
        )
        case(
            "excluded permits a decision before its seeding migration is applied",
            module.check(pending_excluded, artifact([])) == [],
        )

        repo = build_repo(
            tmp + "/pending-carried",
            {"0100_seed.sql": seeding},
            {
                "carried": {},
                "carried_after_apply": {"ops.widget": "bounded future config"},
                "excluded": {},
            },
        )
        case(
            "carried_after_apply permits absence only while its seed migration is pending",
            module.check(repo, artifact([])) == [],
        )
        case(
            "carried_after_apply refuses data before the migration ledger entry",
            any(
                "PRESENT BEFORE ITS LEDGER ENTRY" in finding
                for finding in module.check(repo, artifact([], [copy_block("ops.widget")]))
            ),
        )
        case(
            "carried_after_apply requires rows immediately after ledger application",
            any(
                "CARRIED AFTER APPLY BUT ABSENT" in finding
                for finding in module.check(repo, artifact(["0100_seed.sql"]))
            ),
        )
        case(
            "carried_after_apply passes after the applied seed and bounded rows agree",
            module.check(
                repo,
                artifact(["0100_seed.sql"], [copy_block("ops.widget")]),
            ) == [],
        )
        stale_pending = build_repo(
            tmp + "/pending-stale",
            {"0100_runtime.sql": runtime_only},
            {
                "carried": {},
                "carried_after_apply": {"ops.widget": "stale declaration"},
                "excluded": {},
            },
        )
        case(
            "carried_after_apply refuses a declaration with no applied or pending seed",
            any(
                "HAS NO SOURCE SEED" in finding
                for finding in module.check(stale_pending, artifact([]))
            ),
        )

        # ---------------------------------------------------------------- 3
        repo = build_repo(tmp + "/b", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime data"}})
        case("negative control: an explicitly excluded table is not a finding",
             module.check(repo, artifact(["0100_seed.sql"])) == [])

        # ---------------------------------------------------------------- 4
        repo = build_repo(tmp + "/c", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "bounded config"}, "excluded": {}})
        case("a declared-carried table absent from the artifact refuses",
             any("DECLARED CARRIED BUT ABSENT" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"]))))
        case("a declared-carried table present as a COPY block passes",
             module.check(repo, artifact(["0100_seed.sql"],
                                         [copy_block("ops.widget")])) == [])
        case("a declared-carried table present as an appended insert passes",
             module.check(repo, artifact(["0100_seed.sql"],
                                         [insert_block("ops.widget")])) == [])

        # ---------------------------------------------------------------- 5
        repo = build_repo(tmp + "/d", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "business data"}})
        case("an excluded table that APPEARS in the artifact refuses "
             "(the snapshot widening into business data)",
             any("DECLARED EXCLUDED BUT PRESENT" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"],
                                             [copy_block("ops.widget")]))))

        # ---------------------------------------------------------------- 6
        repo = build_repo(tmp + "/e",
                          {"0100_probe.sql": probe_only, "0101_runtime.sql": runtime_only},
                          {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_probe.sql", "0101_runtime.sql"]))
        case("a runtime insert inside a FUNCTION body is not read as a seed",
             not any("ops.never_seeded" in f for f in found))
        case("an insert inside a DO block IS read as a seed "
             "(a DO block runs at migration time and can seed)",
             any("ops.probe_only" in f for f in found))

        # ---------------------------------------------------------------- 7
        repo = build_repo(tmp + "/f", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "x"}, "excluded": {"ops.widget": "y"}})
        try:
            module.check(repo, artifact(["0100_seed.sql"]))
            case("a table classified both carried and excluded is rejected", False)
        except ValueError:
            case("a table classified both carried and excluded is rejected", True)

        # ---------------------------------------------------------------- 8
        repo = build_repo(tmp + "/g", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "x"}})
        try:
            module.check(repo, "-- no ledger here\n")
            case("an artifact with no migration ledger is refused, never passed", False)
        except LookupError:
            case("an artifact with no migration ledger is refused, never passed", True)

        # ---------------------------------------------------------------- 9
        # The DDL-region regression: `party` and `ops.run` are named only in a
        # COMMENT and a function body, and must not read as carried.
        repo = build_repo(tmp + "/h",
                          {"0100_biz.sql": "insert into party (kind) values ('org');\n"
                                           "insert into ops.run (kind) values ('x');\n"},
                          {"carried": {}, "excluded": {"party": "business data",
                                                       "ops.run": "runtime records"}})
        case("a table named only in DDL prose or a function body is not 'carried'",
             module.check(repo, artifact(["0100_biz.sql"])) == [])

        # ---------------------------------------------------------------- G1
        # THE DECISIVE GAP. A migration defines a function whose body inserts, then
        # CALLS it. The body is not runtime code here — it ran. Modelled on
        # migrations/0247_system_rule_scope_binding.sql, which does exactly this and
        # which the first version of the checker saw as empty.
        called = ("create function ops.sync_bindings() returns void language plpgsql as $$\n"
                  "begin\n  insert into ops.rule_control_binding (rule_id) values (1);\nend $$;\n"
                  "revoke all on function ops.sync_bindings() from public;\n"
                  "select ops.sync_bindings();\n")
        repo = build_repo(tmp + "/g1", {"0100_called.sql": called},
                          {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_called.sql"]))
        case("a function DEFINED AND CALLED at migration time has its inserts counted",
             any("ops.rule_control_binding" in f for f in found))

        # The same function, never called: its body is runtime code and must not count.
        uncalled = ("create function ops.sync_bindings() returns void language plpgsql as $$\n"
                    "begin\n  insert into ops.rule_control_binding (rule_id) values (1);\nend $$;\n"
                    "revoke all on function ops.sync_bindings() from public;\n")
        repo = build_repo(tmp + "/g1b", {"0100_uncalled.sql": uncalled},
                          {"carried": {}, "excluded": {}})
        case("a function DEFINED BUT NEVER CALLED does not count as a seed",
             module.check(repo, artifact(["0100_uncalled.sql"])) == [])

        # REVOKE/GRANT/COMMENT name a function without invoking it. Reading those as
        # calls made an early draft report 88 seeded tables instead of the real 58.
        case("REVOKE ON FUNCTION is not read as a call",
             not any("ops.rule_control_binding" in f
                     for f in module.check(repo, artifact(["0100_uncalled.sql"]))))

        # Transitively: a called function calling another.
        chained = ("create function ops.inner() returns void language plpgsql as $$\n"
                   "begin\n  insert into ops.deep_table (k) values (1);\nend $$;\n"
                   "create function ops.outer_fn() returns void language plpgsql as $$\n"
                   "begin\n  perform ops.inner();\nend $$;\n"
                   "select ops.outer_fn();\n")
        repo = build_repo(tmp + "/g1c", {"0100_chain.sql": chained},
                          {"carried": {}, "excluded": {}})
        case("the called-function closure is transitive (called calls called)",
             any("ops.deep_table" in f for f in module.check(repo, artifact(["0100_chain.sql"]))))

        # ---------------------------------------------------------------- G2
        for label, body, table in (
            ("MERGE", "merge into ops.merged_table t using src s on t.k=s.k "
                      "when not matched then insert (k) values (s.k);\n", "ops.merged_table"),
            ("COPY ... FROM", "copy ops.copied_table (k, v) from stdin;\n1\tx\n\\.\n", "ops.copied_table"),
            ("CREATE TABLE ... AS SELECT",
             "create table ops.derived_table as select k from ops.source_table;\n", "ops.derived_table"),
        ):
            repo = build_repo(tmp + "/g2" + label[:4], {"0100_dml.sql": body},
                              {"carried": {}, "excluded": {}})
            case(f"{label} lands rows and is counted as a seed",
                 any(table in f for f in module.check(repo, artifact(["0100_dml.sql"]))))

        # TEMP tables vanish with the session and can never be in a snapshot. Thirteen
        # migrations build them for before/after comparison; counting them is a pure
        # false alarm, and a check that cries wolf gets switched off.
        temp = ("create temp table _org_before as select id from party;\n"
                "create temporary table lt_before as select id from lead;\n")
        repo = build_repo(tmp + "/g2temp", {"0100_temp.sql": temp},
                          {"carried": {}, "excluded": {}})
        case("CREATE TEMP TABLE ... AS is NOT counted (scratch, never in a snapshot)",
             module.check(repo, artifact(["0100_temp.sql"])) == [])

        # ---------------------------------------------------------------- R2
        # A privilege statement may list MANY functions. Masking only the first left
        # the rest looking like calls, and 49 of 153 live detections were routine
        # bodies admitted that way. Modelled on 0153_control_plane_resilience.sql.
        grant_list = ("create function ops.record_obs() returns void language plpgsql as $$\n"
                      "begin\n  insert into ops.provider_observation (k) values (1);\nend $$;\n"
                      "create function ops.put_cache() returns void language plpgsql as $$\n"
                      "begin\n  insert into ops.cognition_result_cache (k) values (1);\nend $$;\n"
                      "grant execute on function\n  ops.record_obs(),\n  ops.put_cache()\nto carr_jobs;\n")
        repo = build_repo(tmp + "/r2a", {"0100_grants.sql": grant_list},
                          {"carried": {}, "excluded": {}})
        case("a comma-continued GRANT ON FUNCTION list is not read as calls",
             module.check(repo, artifact(["0100_grants.sql"])) == [])

        quoted_sig = ("create function ops.record_lease() returns void language plpgsql as $$\n"
                      "begin\n  insert into lease (k) values (1);\nend $$;\n"
                      "do $$\nbegin\n"
                      "  if not has_function_privilege('carr_writer','ops.record_lease()','execute')\n"
                      "  then raise exception 'missing'; end if;\nend $$;\n")
        repo = build_repo(tmp + "/r2b", {"0100_quoted.sql": quoted_sig},
                          {"carried": {}, "excluded": {}})
        case("a signature inside a quoted string is a name, not a call",
             module.check(repo, artifact(["0100_quoted.sql"])) == [])

        for label, body, table in (
            ("INSERT INTO ONLY", "insert into only ops.parent_table (k) values (1);\n", "ops.parent_table"),
            ("COPY with no column list", "copy ops.bare_copy from stdin;\n1\n\\.\n", "ops.bare_copy"),
        ):
            repo = build_repo(tmp + "/r2" + label[:6], {"0100_dml.sql": body},
                              {"carried": {}, "excluded": {}})
            case(f"{label} names the real table",
                 any(table in f for f in module.check(repo, artifact(["0100_dml.sql"]))))

        repo = build_repo(tmp + "/r2cte", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "bounded config"}, "excluded": {}})
        cte = artifact(["0100_seed.sql"],
                       ["-- CARR APPENDED BLOCK\nwith s as (select 1) insert into ops.widget (a) "
                        "select 1 from s on conflict do nothing;\n\n"])
        case("a carried table emitted behind a CTE still reads as present",
             module.check(repo, cte) == [])

        repo = build_repo(tmp + "/r2dead", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime",
                                                       "ops.long_gone": "nothing writes this any more"}})
        case("a classification entry nothing seeds any more is reported, not left to rot",
             any("NO LONGER APPLIES" in f and "ops.long_gone" in f
                 for f in module.check(repo, artifact(["0100_seed.sql"]))))

        repo = build_repo(tmp + "/r2bound", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        above = artifact(["0100_seed.sql"]).replace(
            LEDGER_HEADER, "insert into public.client (id) values (1);\n\n" + LEDGER_HEADER, 1)
        case("an INSERT above the ledger refuses; the boundary is not COPY-only",
             any("DATA REGION BOUNDARY MOVED" in f for f in module.check(repo, above)))

        # ---------------------------------------------------------------- R3
        # normalise() folds public.foo and foo together. Nothing exercised it, so
        # deleting the fold passed all 45 cases while producing 25 failures on the
        # real db/schema.sql — the same untested-surface disease that hid the main()
        # mutants, one layer down.
        qualified = "insert into public.qualified_table (k) values (1);\n"
        repo = build_repo(tmp + "/r3norm", {"0100_q.sql": qualified},
                          {"carried": {}, "excluded": {"qualified_table": "runtime"}})
        case("a migration writing public.foo is satisfied by the bare name foo",
             module.check(repo, artifact(["0100_q.sql"])) == [])
        repo = build_repo(tmp + "/r3norm2", {"0100_q.sql": "insert into bare_table (k) values (1);\n"},
                          {"carried": {"bare_table": "vocabulary"}, "excluded": {}})
        case("an artifact carrying COPY public.foo satisfies a bare-name carried entry",
             module.check(repo, artifact(["0100_q.sql"],
                                         [copy_block("public.bare_table")])) == [])
        repo = build_repo(tmp + "/r3norm3", {"0100_q.sql": "insert into ops.kept (k) values (1);\n"},
                          {"carried": {}, "excluded": {"kept": "runtime"}})
        case("ops.foo is NOT folded to foo — only the public schema is implicit",
             any("ops.kept" in f for f in module.check(repo, artifact(["0100_q.sql"]))))

        # CREATE PROCEDURE bodies were attributed to the last CREATE FUNCTION header,
        # so a procedure that writes could be credited to the wrong routine or lost.
        proc = ("create function ops.helper() returns void language plpgsql as $$\n"
                "begin\n  perform 1;\nend $$;\n"
                "create procedure ops.seed_it() language plpgsql as $$\n"
                "begin\n  insert into ops.proc_table (k) values (1);\nend $$;\n"
                "call ops.seed_it();\n")
        repo = build_repo(tmp + "/r3proc", {"0100_proc.sql": proc},
                          {"carried": {}, "excluded": {}})
        case("a CREATE PROCEDURE body that is CALLed is counted as a seed",
             any("ops.proc_table" in f for f in module.check(repo, artifact(["0100_proc.sql"]))))

        # COPY with no column list, on the ARTIFACT side this time. It was fixed for
        # migrations and left broken for the snapshot, so a carried table emitted that
        # way read as absent.
        repo = build_repo(tmp + "/r3copy", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "bounded config"}, "excluded": {}})
        case("a carried table emitted as COPY with no column list reads as present",
             module.check(repo, artifact(["0100_seed.sql"],
                                         ["COPY ops.widget FROM stdin;\n1\n\\.\n\n\n"])) == [])

        # The boundary must see every row-landing form above the ledger, not INSERT
        # and COPY alone.
        repo = build_repo(tmp + "/r3bound", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        for label, statement in (
            ("MERGE", "merge into public.client t using s on t.k=s.k when not matched then insert (k) values (s.k);\n"),
            ("CREATE TABLE AS", "create table public.client_copy as select * from public.client;\n"),
        ):
            above = artifact(["0100_seed.sql"]).replace(
                LEDGER_HEADER, statement + "\n" + LEDGER_HEADER, 1)
            case(f"{label} above the ledger refuses; the boundary is not INSERT/COPY only",
                 any("DATA REGION BOUNDARY MOVED" in f for f in module.check(repo, above)))

        # ---------------------------------------------------------------- R4
        # ONE APOSTROPHE IN A COMMENT DISARMED THE WHOLE CHECK. The literal mask was
        # a regex over text that still held SQL comments, so an apostrophe in English
        # prose paired with a real string's opening quote and masked everything
        # between — including the CALL. 102 of 264 applied migrations already had odd
        # apostrophe parity. Found by the fourth independent review.
        called = ("create function ops.seed_it() returns void language plpgsql as $$\n"
                  "begin\n  insert into ops.throwaway_lookup (k) values (1);\nend $$;\n"
                  "{C}\n"
                  "select ops.seed_it();\n"
                  "comment on table ops.throwaway_lookup is 'a bounded lookup';\n")
        for label, comment in (
            ("plain", "-- seeds the reviewer throwaway lookup table."),
            ("with an apostrophe", "-- seeds the reviewer's throwaway lookup table."),
            ("apostrophe in a block comment", "/* the builder's note */"),
            ("two apostrophes", "-- the reviewer's and the builder's table."),
        ):
            repo = build_repo(tmp + "/r4" + label[:6].replace(" ", ""),
                              {"0100_c.sql": called.replace("{C}", comment)},
                              {"carried": {}, "excluded": {}})
            case(f"a comment {label} cannot disarm the check",
                 any("ops.throwaway_lookup" in f
                     for f in module.check(repo, artifact(["0100_c.sql"]))))

        # The same scan fixes two things nobody had reported.
        repo = build_repo(tmp + "/r4com", {"0100_x.sql": "-- insert into ops.ghost (k) values (1);\n"},
                          {"carried": {}, "excluded": {}})
        case("a commented-out INSERT is not counted as a seed",
             module.check(repo, artifact(["0100_x.sql"])) == [])
        repo = build_repo(tmp + "/r4str", {"0100_y.sql": "select 'insert into ops.ghost values (1)';\n"},
                          {"carried": {}, "excluded": {}})
        case("an INSERT inside a string literal is not counted as a seed",
             module.check(repo, artifact(["0100_y.sql"])) == [])

        # Bodies are scanned too. Storing them raw put the apostrophe bug straight
        # back one level down.
        nested = ("create function ops.helper() returns void language plpgsql as $$\n"
                  "begin\n  -- the builder's note\n"
                  "  insert into ops.nested_table (k) values (1);\nend $$;\n"
                  "select ops.helper();\n"
                  "comment on table ops.nested_table is 'x';\n")
        repo = build_repo(tmp + "/r4nest", {"0100_n.sql": nested},
                          {"carried": {}, "excluded": {}})
        case("a comment with an apostrophe INSIDE a routine body cannot disarm it",
             any("ops.nested_table" in f for f in module.check(repo, artifact(["0100_n.sql"]))))

        # Scanner branches a whole-module sweep proved unpinned.
        repo = build_repo(tmp + "/r4blk",
                          {"0100_b.sql": "/* insert into ops.ghost (k) values (1); */\n"
                                         "insert into ops.real_table (k) values (1);\n"},
                          {"carried": {}, "excluded": {"ops.real_table": "runtime"}})
        found = module.check(repo, artifact(["0100_b.sql"]))
        case("DML inside a block comment is not counted",
             not any("ops.ghost" in f for f in found))

        # PostgreSQL nests block comments. Getting the nesting wrong ends the comment
        # at the first */ and spills the rest back into the scanned text.
        repo = build_repo(tmp + "/r4nest2",
                          {"0100_bn.sql": "/* outer /* inner */ insert into ops.ghost2 (k) values (1); */\n"
                                          "insert into ops.real2 (k) values (1);\n"},
                          {"carried": {}, "excluded": {"ops.real2": "runtime"}})
        case("a NESTED block comment stays a comment to its true end",
             not any("ops.ghost2" in f for f in module.check(repo, artifact(["0100_bn.sql"]))))

        # '' is an escaped quote, not the end of the literal. Getting that wrong
        # shifts every following quote boundary and can mask a call.
        esc = ("create function ops.seed_it() returns void language plpgsql as $$\n"
               "begin\n  insert into ops.escaped_table (k) values (1);\nend $$;\n"
               "comment on table ops.escaped_table is 'it''s bounded';\n"
               "select ops.seed_it();\n")
        repo = build_repo(tmp + "/r4esc", {"0100_e.sql": esc}, {"carried": {}, "excluded": {}})
        # NOTE on the one mutant this suite deliberately does not chase: forcing the
        # '' branch false is an EQUIVALENT mutant. Either way both quote characters
        # are consumed — the escape branch takes them as a pair, and without it the
        # first ends one literal and the second opens another that closes at the same
        # place. Same masked span, same result. A case cannot distinguish them
        # because there is nothing to distinguish.
        case("a doubled quote inside a literal does not shift the boundaries",
             any("ops.escaped_table" in f for f in module.check(repo, artifact(["0100_e.sql"]))))

        # A lone dollar sign is not a dollar quote. Treating it as one crashed the scan.
        lone = ("select 1 where x = $ and y = 1;\n"
                "insert into ops.after_dollar (k) values (1);\n")
        repo = build_repo(tmp + "/r4dol", {"0100_d.sql": lone}, {"carried": {}, "excluded": {}})
        case("a lone dollar sign does not derail the scan",
             any("ops.after_dollar" in f for f in module.check(repo, artifact(["0100_d.sql"]))))

        # ---------------------------------------------------------------- R5
        # Branches a 448-mutant sweep proved change the answer on the real corpus
        # while every case stayed green — the same untested-branch shape that hid
        # the normalise() fold.

        # CREATE TABLE is only a seed when it has an AS SELECT tail. Without that
        # test every plain CREATE TABLE becomes a seed, and the check would report
        # most of the schema.
        repo = build_repo(tmp + "/r5ct",
                          {"0100_ct.sql": "create table ops.plain_table (k text primary key);\n"},
                          {"carried": {}, "excluded": {}})
        case("a plain CREATE TABLE with no AS SELECT is not a seed",
             module.check(repo, artifact(["0100_ct.sql"])) == [])

        # A bare slash is division, not the start of a block comment. Treating it
        # as one swallows the rest of the migration.
        repo = build_repo(tmp + "/r5div",
                          {"0100_div.sql": "insert into ops.divided (k) select 10 / 2;\n"
                                           "insert into ops.after_slash (k) values (1);\n"},
                          {"carried": {},
                           "excluded": {"ops.divided": "runtime", "ops.after_slash": "runtime"}})
        case("a division slash does not open a block comment and swallow the rest",
             module.check(repo, artifact(["0100_div.sql"])) == [])

        # The character immediately after a closing quote belongs to the next
        # statement. Consuming it too shifts every following boundary.
        # Two literals separated by one character. Consuming a character too many
        # after the closing quote eats the comma AND the next opening quote, so what
        # follows is read inside-out and every later boundary shifts — masking the
        # insert entirely. One adjacent statement is not enough to show this; the
        # second literal is what makes the shift observable.
        repo = build_repo(tmp + "/r5adj",
                          {"0100_adj.sql": "select 'a','b';\n"
                                           "insert into ops.adjacent (k) values (1);\n"
                                           "comment on table ops.t is 'y';\n"},
                          {"carried": {}, "excluded": {}})
        case("adjacent string literals do not shift every boundary after them",
             any("ops.adjacent" in f for f in module.check(repo, artifact(["0100_adj.sql"]))))

        # ---------------------------------------------------------------- R6
        # E'...' TAKES BACKSLASH ESCAPES; a plain literal does not. Knowing only the
        # doubled-quote form ended the literal at the escaped quote and read the rest
        # of the migration inside-out, silencing a plainly top-level INSERT. R4's
        # apostrophe class, one escape form over, and worse: it swallowed real DML
        # rather than only a call.
        repo = build_repo(tmp + "/r6esc",
                          {"0100_e.sql": "comment on table t is E'the reviewer\\'s registry';\n"
                                         "insert into ops.new_registry (k) values (1);\n"},
                          {"carried": {}, "excluded": {}})
        case("an E-string with a backslash-escaped quote does not silence the migration",
             any("ops.new_registry" in f for f in module.check(repo, artifact(["0100_e.sql"]))))
        # A backslash in an ORDINARY literal is a plain character, not an escape.
        repo = build_repo(tmp + "/r6esc2",
                          {"0100_p.sql": "comment on table t is 'a plain \\ backslash';\n"
                                         "insert into ops.plain_after (k) values (1);\n"},
                          {"carried": {}, "excluded": {}})
        case("a backslash in an ordinary literal is not read as an escape",
             any("ops.plain_after" in f for f in module.check(repo, artifact(["0100_p.sql"]))))

        # `do language plpgsql $$ … $$` is the same statement as `do $$ … $$`.
        repo = build_repo(tmp + "/r6do",
                          {"0100_d.sql": "do language plpgsql $$\nbegin\n"
                                         "  insert into ops.other_registry (k) values (1);\nend $$;\n"},
                          {"carried": {}, "excluded": {}})
        case("DO LANGUAGE plpgsql is recognised as a DO block, not a string literal",
             any("ops.other_registry" in f for f in module.check(repo, artifact(["0100_d.sql"]))))

        # NOT equivalent after all. A previous revision of this suite argued that
        # consuming one character past a closing quote could not be distinguished,
        # and that was WRONG: when the next character opens a block comment, the
        # mutant reads the comment's contents as code.
        repo = build_repo(tmp + "/r6bnd",
                          {"0100_g.sql": "select 'x'/* insert into ops.ghost_block (k) values (1); */\n"
                                         "insert into ops.real_one (k) values (1);\n"},
                          {"carried": {}, "excluded": {"ops.real_one": "runtime"}})
        case("a block comment opening right after a closing quote stays a comment",
             module.check(repo, artifact(["0100_g.sql"])) == [])

        # Expression-level branches a wider sweep than ours found unpinned. Each was
        # confirmed to change the answer before its case was written.
        repo = build_repo(tmp + "/r6q",
                          {"0100_q.sql": 'insert into "ops"."quoted_ident" (k) values (1);\n'},
                          {"carried": {}, "excluded": {"ops.quoted_ident": "runtime"}})
        case("a double-quoted identifier is normalised to the same table as a bare one",
             module.check(repo, artifact(["0100_q.sql"])) == [])

        repo = build_repo(tmp + "/r6up",
                          {"0100_u.sql": "INSERT INTO OPS.UPPER_TABLE (K) VALUES (1);\n"},
                          {"carried": {}, "excluded": {"ops.upper_table": "runtime"}})
        case("an UPPERCASE table name folds to the same entry as a lowercase one",
             module.check(repo, artifact(["0100_u.sql"])) == [])

        # A dollar-quoted STRING (not a routine body, not a DO block) is a literal.
        # Failing to blank it reads its contents as code.
        repo = build_repo(tmp + "/r6ds",
                          {"0100_ds.sql": "select $tag$ insert into ops.ghost_dollar (k) values (1); $tag$;\n"
                                          "insert into ops.real_dollar (k) values (1);\n"},
                          {"carried": {}, "excluded": {"ops.real_dollar": "runtime"}})
        case("a dollar-quoted string literal is blanked, not read as code",
             module.check(repo, artifact(["0100_ds.sql"])) == [])

        # A routine whose short name is a substring of another must not be dragged
        # in by the longer one's call.
        subs = ("create function ops.sync() returns void language plpgsql as $$\n"
                "begin\n  insert into ops.short_one (k) values (1);\nend $$;\n"
                "create function ops.sync_all() returns void language plpgsql as $$\n"
                "begin\n  insert into ops.long_one (k) values (1);\nend $$;\n"
                "select ops.sync_all();\n")
        repo = build_repo(tmp + "/r6sub", {"0100_s.sql": subs}, {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_s.sql"]))
        case("calling the longer routine does not drag in the one whose name it contains",
             any("ops.long_one" in f for f in found) and not any("ops.short_one" in f for f in found))

        # M30. A ROUTINE BODY IS SCANNED, NOT STORED RAW, and nothing held that.
        # The module docstring credited the has_function_privilege case with
        # pinning it; storing bodies raw survives that case and the committed
        # artifact both, which is a defence documented as tested that no case
        # actually killed. A body carrying an INSERT inside a STRING LITERAL
        # separates them: scanned, the literal is blanked and the table is
        # invisible; raw, the literal reads as a statement and a routine that
        # never touches the table reports it.
        raw_body = ("create function ops.logger() returns void language plpgsql as $$\n"
                    "begin\n"
                    "  raise notice 'ran: insert into ops.literal_ghost (k) values (1);';\n"
                    "  insert into ops.logger_real (k) values (1);\n"
                    "end $$;\n"
                    "select ops.logger();\n")
        repo = build_repo(tmp + "/r6m30", {"0100_m30.sql": raw_body}, {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_m30.sql"]))
        case("an INSERT inside a string literal in a routine body is not a write",
             any("ops.logger_real" in f for f in found)
             and not any("ops.literal_ghost" in f for f in found))

        # M37. THE SHORT-NAME CALL TEST IS THE UNDER-DETECTION DIRECTION. A
        # schema-qualified routine called WITHOUT its schema, which search_path
        # makes legal and which this corpus does, is still a call: matching only
        # the qualified name loses the body, and with it every table the body
        # seeds. That is a seeded table going unclassified in silence.
        short = ("create function ops.seed_unqualified() returns void language plpgsql as $$\n"
                 "begin\n  insert into ops.reached_by_short_name (k) values (1);\nend $$;\n"
                 "select seed_unqualified();\n")
        repo = build_repo(tmp + "/r6m37", {"0100_m37.sql": short}, {"carried": {}, "excluded": {}})
        case("an unqualified call to a schema-qualified routine still seeds its tables",
             any("ops.reached_by_short_name" in f
                 for f in module.check(repo, artifact(["0100_m37.sql"]))))

        # THE BACKWARD-LOOKING TESTS ARE BOUNDED NOW, so the thing to pin is that
        # the bound cannot blind them. Both are anchored to the end of the
        # accumulated buffer, so a megabyte of preceding text must change nothing:
        # a routine body and a DO block sitting behind far more than HEAD_TAIL
        # characters of filler still have to be seen for what they are.
        filler = "-- filler comment line to push the buffer past the tail bound\n" * 30000
        far = (filler
               + "create function ops.far_routine() returns void language plpgsql as $$\n"
               + "begin\n  insert into ops.far_routine_seed (k) values (1);\nend $$;\n"
               + "select ops.far_routine();\n"
               + filler
               + "do language plpgsql $$\nbegin\n"
               + "  insert into ops.far_do_seed (k) values (1);\nend $$;\n")
        repo = build_repo(tmp + "/r6far", {"0100_far.sql": far}, {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_far.sql"]))
        case("a routine body a megabyte behind the tail bound is still a routine",
             any("ops.far_routine_seed" in f for f in found))
        case("a DO block a megabyte behind the tail bound is still a DO block",
             any("ops.far_do_seed" in f for f in found))

        # DYNAMIC SQL. `execute '<statement>'` is a string that lands rows, and
        # blanking it detected nothing. The negative control matters as much as
        # the positive one: a literal that is NOT an execute argument stays inert,
        # or every migration mentioning a table in prose becomes a false alarm.
        dyn = ("do $$ begin execute 'insert into ops.dyn_seed values (1)'; end $$;\n"
               "do $$ begin raise notice 'insert into ops.dyn_prose values (1)'; end $$;\n")
        repo = build_repo(tmp + "/r6dyn", {"0100_dyn.sql": dyn}, {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_dyn.sql"]))
        case("execute of a string literal is a seed, not inert text",
             any("ops.dyn_seed" in f for f in found))
        case("a literal that is not an execute argument stays inert",
             not any("ops.dyn_prose" in f for f in found))

        fmt = "do $$ begin execute format('insert into ops.fmt_seed values (%L)', 1); end $$;\n"
        repo = build_repo(tmp + "/r6fmt", {"0100_fmt.sql": fmt}, {"carried": {}, "excluded": {}})
        case("execute format() reads the literal table name in its format string",
             any("ops.fmt_seed" in f for f in module.check(repo, artifact(["0100_fmt.sql"]))))

        # T42/T43. _scanned's docstring claims a body's own routines and DO blocks
        # are folded in with it. Nothing held that claim.
        nested = ("create function ops.outer_fn() returns void language plpgsql as $outer$\n"
                  "begin\n"
                  "  do $d$ begin insert into ops.nested_do_seed values (1); end $d$;\n"
                  "end $outer$;\n"
                  "select ops.outer_fn();\n")
        repo = build_repo(tmp + "/r6nest", {"0100_nest.sql": nested}, {"carried": {}, "excluded": {}})
        case("a DO block nested inside a called routine body is folded in",
             any("ops.nested_do_seed" in f for f in module.check(repo, artifact(["0100_nest.sql"]))))

        dollar_exec = ("do $$ begin execute $q$ insert into ops.dollar_dyn_seed values (1); $q$; end $$;\n")
        repo = build_repo(tmp + "/r6de", {"0100_de.sql": dollar_exec}, {"carried": {}, "excluded": {}})
        case("execute of a DOLLAR-quoted body is a seed too",
             any("ops.dollar_dyn_seed" in f for f in module.check(repo, artifact(["0100_de.sql"]))))

        # T43. The other half of _scanned's claim: a routine DEFINED AND CALLED
        # inside a called routine's body is reached when the outer one runs, so
        # its body has to be folded in too. The DO-block case above cannot hold
        # this; only a nested routine can.
        nested_fn = ("create function ops.outer2() returns void language plpgsql as $o$\n"
                     "begin\n"
                     "  create function ops.inner2() returns void language plpgsql as $i$\n"
                     "  begin insert into ops.nested_routine_seed values (1); end $i$;\n"
                     "  perform ops.inner2();\n"
                     "end $o$;\n"
                     "select ops.outer2();\n")
        repo = build_repo(tmp + "/r6nf", {"0100_nf.sql": nested_fn}, {"carried": {}, "excluded": {}})
        case("a routine defined and called inside a called routine body is folded in",
             any("ops.nested_routine_seed" in f
                 for f in module.check(repo, artifact(["0100_nf.sql"]))))

        # T22. When CREATE_ROUTINE misses the header - a quoted identifier, for one
        # - the `as` branch has no name to file the body under. It must still SCAN
        # the body: the fallback is what stops a seed vanishing with its header.
        quoted = ('create function ops."Quoted Fn"() returns void language plpgsql as $$\n'
                  "begin\n  insert into ops.quoted_hdr_seed values (1);\nend $$;\n")
        repo = build_repo(tmp + "/r6q", {"0100_q.sql": quoted}, {"carried": {}, "excluded": {}})
        case("a routine body whose header the regex misses is still scanned",
             any("ops.quoted_hdr_seed" in f for f in module.check(repo, artifact(["0100_q.sql"]))))

        # T51. An unterminated dollar-quote is kept verbatim rather than dropped,
        # so statements after the break are still read.
        unterm = ("insert into ops.before_unterm values (1);\n"
                  "select $$ never closed\n"
                  "insert into ops.after_unterm values (1);\n")
        repo = build_repo(tmp + "/r6ut", {"0100_ut.sql": unterm}, {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_ut.sql"]))
        case("an unterminated dollar-quote is kept verbatim, not dropped",
             any("ops.after_unterm" in f for f in found))

        # T46. COPY blocks are EXCISED before scanning, not merely blanked as
        # literals. copy_blocks says the span exists because COPY data is not SQL
        # and one apostrophe in a data row sends a literal scanner inside-out. The
        # jsonb case passes on literal blanking, so it cannot hold this. An
        # ODD number of apostrophes in the data is what separates them: excised,
        # the statement after the block is read normally; scanned, that lone quote
        # opens a literal that swallows it.
        odd_quote = "COPY public.deal_phase (name) FROM stdin;\nO'Brien\n\\.\n\n"
        after = "insert into party (name) values ('acme');\n\n"
        repo_ex = build_repo(tmp + "/r6ex",
                             {"0100_ex.sql": "insert into deal_phase (name) values ('x');\n"},
                             {"carried": {"deal_phase": "closed vocabulary"},
                              "excluded": {"party": "business data"}})
        found = module.check(repo_ex, artifact(["0100_ex.sql"], [odd_quote + after]))
        case("an apostrophe in COPY data cannot hide the statement after the block",
             any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))

        # T44. tables_with_data reads the artifact's ROUTINE BODIES too. A routine
        # in the snapshot whose body writes an excluded table is that table
        # arriving in a tracked file, and dropping the routine scan hid it.
        body_write = ("CREATE FUNCTION public.sneak() RETURNS void LANGUAGE plpgsql\n"
                      "    AS $sneak$\nbegin\n  insert into party (name) values ('x');\nend $sneak$;\n\n")
        repo_b = build_repo(tmp + "/r6bw",
                            {"0100_bw.sql": "insert into deal_phase (name) values ('x');\n"},
                            {"carried": {}, "excluded": {"party": "business data"}})
        found = module.check(repo_b, artifact(["0100_bw.sql"], [body_write]))
        case("an excluded table written from a routine body in the artifact is present",
             any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))

        # T49. The COPY header pattern is anchored to a line start. Unanchored, a
        # COPY header appearing mid-line INSIDE another block's data would open a
        # phantom block and take the rest of the region with it.
        # The phantom must HIDE something to be worth a case. deal_phase is
        # CARRIED and genuinely absent here; a mid-line header for it inside
        # another table's data would mint rows it does not have and silence the
        # absence, so the anchored form must refuse and the unanchored one pass.
        phantom = ("COPY public.some_other (name) FROM stdin;\n"
                   "note: COPY public.deal_phase (name) FROM stdin; is quoted here\n"
                   "\\.\n\n")
        repo_ph = build_repo(tmp + "/r6ph",
                             {"0100_ph.sql": "insert into deal_phase (name) values ('x');\n"},
                             {"carried": {"deal_phase": "closed vocabulary"}, "excluded": {}})
        found = module.check(repo_ph, artifact(["0100_ph.sql"], [phantom]))
        case("a COPY header quoted mid-line inside data cannot mint rows for a carried table",
             any("DECLARED CARRIED BUT ABSENT" in f and "deal_phase" in f for f in found))

        # T13. The data region STARTS AT the ledger's COPY header, not after it,
        # so copy_blocks excises the ledger's own rows before anything scans them.
        # Starting after the header leaves that data loose in the region.
        case("the data region starts at the ledger COPY header, so its rows are excised",
             module.data_region(artifact(["0100_x.sql"])).startswith("COPY public.schema_migrations"))

        # Pad each whitespace position independently and all of them together.
        # Both literal spellings and bare/format EXECUTE use the same contract.
        for quote in ("'insert into ops.compound_exec values (1)'",
                      "$q$insert into ops.compound_exec values (1)$q$"):
            for tokens in (("execute",), ("execute", "format", "(")):
                for padded_gap in (*range(len(tokens)), None):
                    prefix = "".join(token + (" " * 5000 if padded_gap in (gap, None) else " ")
                                     for gap, token in enumerate(tokens))
                    sql = "do $$ begin " + prefix + quote + (");" if "format" in tokens else ";") + " end $$;"
                    case(f"EXECUTE prefix gaps {tokens}/{padded_gap}/{quote[0]} preserve seeds",
                         "ops.compound_exec" in module.written_tables(sql))
                    repo_gap = build_repo(tmp + "/prefix-gap", {"0100_gap.sql": sql},
                                          {"carried": {}, "excluded": {}})
                    case("compound EXECUTE padding cannot bypass classification",
                         any("UNCLASSIFIED" in f for f in module.check(repo_gap, artifact(["0100_gap.sql"]))))
        for tokens in (("do",), ("do", "language", "plpgsql")):
            for padded_gap in (*range(len(tokens)), None):
                prefix = "".join(token + ("\n\t" * 2500 if padded_gap in (gap, None) else " ")
                                 for gap, token in enumerate(tokens))
                sql = prefix + "$$ begin insert into ops.compound_do values (1); end $$;"
                case(f"DO prefix gaps {tokens}/{padded_gap} preserve seeds",
                     "ops.compound_do" in module.written_tables(sql))

        for newline in ("\n", "\r\n"):
            for suffix in ("suffix", " ", "\t"):
                block = newline.join(["COPY public.review_data (value) FROM stdin;",
                                      "\\." + suffix, "a'b", "\\.", "",
                                      "insert into party values (1);", ""])
                repo_marker = build_repo(tmp + "/marker", {"0100_marker.sql": "insert into party values (1);"},
                                         {"carried": {}, "excluded": {"party": "runtime data"}})
                found = module.check(repo_marker, artifact(["0100_marker.sql"], [block]))
                case(f"COPY marker suffix {suffix!r} with {newline!r} is row data",
                     any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))

        # THE TAIL BOUND IS COUNTED IN SIGNIFICANT CHARACTERS. Every consumer looks
        # back past optional whitespace, so a raw-character bound measures the
        # whitespace rather than the token, and a long enough run loses the seed.
        # 4200 spaces missed where 4000 detected, which is the missed-seed
        # direction this file is not allowed to fail in.
        pad = " " * 9000
        padded = ("do $$ begin execute" + pad + "'insert into ops.padded_exec values (1)'; end $$;\n"
                  "create function ops.padded_fn() returns void language plpgsql as" + pad +
                  "$$ begin insert into ops.padded_body values (1); end $$;\n"
                  "select ops.padded_fn();\n")
        repo = build_repo(tmp + "/r6pad", {"0100_pad.sql": padded}, {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_pad.sql"]))
        case("whitespace between EXECUTE and its literal cannot push the seed out of the window",
             any("ops.padded_exec" in f for f in found))
        case("whitespace between AS and a routine body cannot push the seed out of the window",
             any("ops.padded_body" in f for f in found))

        # N5. The boundary refusal names the EARLIEST data statement in the segment,
        # not the first pattern in WRITES_ANYWHERE that happens to match. #803 fixed
        # the ordering ACROSS segments and left this half with no case. The two must
        # disagree to be worth testing: MERGE comes first in the text and second in
        # the pattern list, so min() names it and hits[0] names the INSERT.
        two_stmts = ("merge into ops.earliest_merge t using (select 1) s on true "
                     "when not matched then insert values (1);\n"
                     "insert into ops.later_insert (k) values (1);\n\n")
        repo_ord = build_repo(tmp + "/r6ord",
                              {"0100_ord.sql": "insert into deal_phase (name) values ('x');\n"},
                              {"carried": {}, "excluded": {}})
        art = artifact(["0100_ord.sql"])
        art = art.replace("COPY public.schema_migrations", two_stmts + "COPY public.schema_migrations", 1)
        found = module.check(repo_ord, art)
        case("the boundary refusal names the earliest statement, not the first pattern to match",
             any("ops.earliest_merge" in f for f in found))

        # N20. The COPY block's TERMINATOR is newline-anchored, the twin of the
        # header anchor T49 pinned. A backslash-dot inside a data row would end
        # the block early and expose its tail to the SQL scanner.
        early_end = ("COPY public.deal_phase (name) FROM stdin;\n"
                     "a\\.b\n"
                     "O'Brien\n"
                     "\\.\n\ninsert into party (name) values ('acme');\n\n")
        repo_t = build_repo(tmp + "/r6term",
                            {"0100_t.sql": "insert into deal_phase (name) values ('x');\n"},
                            {"carried": {"deal_phase": "closed vocabulary"},
                             "excluded": {"party": "business data"}})
        found = module.check(repo_t, artifact(["0100_t.sql"], [early_end]))
        case("a backslash-dot inside a data row does not end the COPY block early",
             any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))

        # N3. EXECUTE_ARGUMENT needs its leading word boundary. Without it an
        # identifier merely ENDING in execute -- last_execute, pre_execute -- turns
        # the literal beside it into statements, which is the false-alarm direction.
        false_exec = "select last_execute 'insert into ops.ghost_exec values (1)' from ops.real_one;\n"
        repo_fe = build_repo(tmp + "/r6fe", {"0100_fe.sql": false_exec}, {"carried": {}, "excluded": {}})
        case("a word merely ENDING in execute does not make its literal into statements",
             not any("ops.ghost_exec" in f for f in module.check(repo_fe, artifact(["0100_fe.sql"]))))

        # Doubled quotes decode to one quoted value containing SQL-looking text.
        doubled = ("do $$ begin execute "
                   "'insert into ops.dq_first values (''; insert into ops.dq_phantom values (1)'')'; "
                   "end $$;\n")
        repo_dq = build_repo(tmp + "/r6dq", {"0100_dq.sql": doubled}, {"carried": {}, "excluded": {}})
        found = module.check(repo_dq, artifact(["0100_dq.sql"]))
        case("a dynamic literal's doubled quotes are undoubled before it is scanned",
             any("ops.dq_first" in f for f in found)
             and not any("ops.dq_phantom" in f for f in found))

        # CTAS column lists contain names, never typed definitions. Exercise a
        # valid wide header beyond the former cutoff, plus a smaller control.
        for width in (60, 400):
            cols = [f"col_{i:04}_long_name" for i in range(width)]
            wide_ctas = ("create table ops.wide_ctas (" + ", ".join(cols)
                         + ") as select " + ", ".join("1 as " + c for c in cols) + ";")
            repo_ct = build_repo(tmp + f"/r6ct{width}", {"0100_ct.sql": wide_ctas},
                                 {"carried": {}, "excluded": {}})
            case(f"a valid {width}-column CREATE TABLE AS is still a seed",
                 any("UNCLASSIFIED" in f and "ops.wide_ctas" in f
                     for f in module.check(repo_ct, artifact(["0100_ct.sql"]))))

        # N16. SECURITY LABEL ON FUNCTION names a routine without calling it, the
        # same shape as GRANT and REVOKE. Losing that alternative makes a defined
        # but never-called routine look invoked, and its body a seed.
        seclabel = ("create function ops.labelled() returns void language plpgsql as $$\n"
                    "begin\n  insert into ops.seclabel_ghost (k) values (1);\nend $$;\n"
                    "security label on function ops.labelled() is 'classified';\n")
        repo_sl = build_repo(tmp + "/r6sl", {"0100_sl.sql": seclabel}, {"carried": {}, "excluded": {}})
        case("SECURITY LABEL ON FUNCTION names a routine, it does not call it",
             not any("ops.seclabel_ghost" in f for f in module.check(repo_sl, artifact(["0100_sl.sql"]))))

        # A header inside COPY data is a row of the outer table. It must not
        # fabricate row evidence for a carried table that is absent.
        nested_copy = ("COPY public.outer_tbl (a) FROM stdin;\n"
                       "COPY public.inner_tbl (b) FROM stdin;\n"
                       "datarow\n\\.\n\n"
                       "insert into party (name) values ('acme');\n")
        blocks, _rest = module.copy_blocks(nested_copy)
        case("a COPY header in row data produces only the outer block",
             len(blocks) == 1 and blocks[0][0] == "outer_tbl")
        repo_nested = build_repo(tmp + "/r6nested",
                                 {"0100_nested.sql": "insert into inner_tbl values (1);"},
                                 {"carried": {"inner_tbl": "required vocabulary"},
                                  "excluded": {"party": "runtime data"}})
        found = module.check(repo_nested, artifact(["0100_nested.sql"], [nested_copy]))
        case("a nested COPY header cannot mint carried rows",
             any("DECLARED CARRIED BUT ABSENT" in f and "inner_tbl" in f for f in found))
        case("SQL after an outer COPY block remains visible",
             any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))
        case("blanking COPY spans preserves artifact length",
             len(module._blank(nested_copy, [(b[2], b[3]) for b in blocks])) == len(nested_copy))
        case("blanking overlapping spans preserves length independently of COPY parsing",
             len(module._blank("abcdefghij", [(1, 6), (3, 8)])) == 10)

        # Whitespace is legal around the schema separator.
        repo = build_repo(tmp + "/r6sp",
                          {"0100_sp.sql": "insert into ops . spaced_table (k) values (1);\n"},
                          {"carried": {}, "excluded": {"ops.spaced_table": "runtime"}})
        case("whitespace around the schema separator folds to the same table",
             module.check(repo, artifact(["0100_sp.sql"])) == [])

        # The routine-body keyword is matched case-insensitively: AS and DO are
        # spelled either way in this corpus.
        repo = build_repo(tmp + "/r6kw",
                          {"0100_kw.sql": "CREATE FUNCTION ops.f() RETURNS void LANGUAGE plpgsql AS $$\n"
                                          "BEGIN\n  insert into ops.upper_kw (k) values (1);\nEND $$;\n"
                                          "SELECT ops.f();\n"},
                          {"carried": {}, "excluded": {}})
        case("an uppercase AS still introduces a routine body",
             any("ops.upper_kw" in f for f in module.check(repo, artifact(["0100_kw.sql"]))))

        # The boundary check anchors to the start of a line. Without that anchor a
        # table merely NAMED mid-line above the ledger reads as data and every real
        # snapshot refuses.
        repo = build_repo(tmp + "/r6anc", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        midline = artifact(["0100_seed.sql"]).replace(
            "COMMENT ON FUNCTION",
            "COMMENT ON TABLE public.client IS 'see insert into public.client for the pattern';\nCOMMENT ON FUNCTION", 1)
        case("a table named mid-line above the ledger is not read as data",
             module.check(repo, midline) == [])

        # ------------------------------------------------------- SWEEP RESIDUE
        # Found by a mutation sweep over the whole module rather than over the last
        # fix: these two branches could be broken with the entire suite still green.
        repo = build_repo(tmp + "/swept", {"0100_seed.sql": seeding},
                          {"carried": {}, "carried_subset": {"ops.widget": "program rows only"},
                           "excluded": {},
                           "carried_subset_must_not_contain": {
                               "ops.widget": {"broken": "([unclosed"}}})
        case("an unusable scope pattern is reported, never silently skipped",
             any("UNUSABLE SCOPE PATTERN" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"], [insert_block("ops.widget")]))))
        case("the boundary check returns nothing when there is no ledger to anchor to",
             module.check_region_boundary("-- no ledger here\n") is None)

        # ---------------------------------------------------------------- MAIN
        # main() is the ONLY surface bin/schema-snapshot.sh consumes. Testing check()
        # alone let a mutant that returns 0 unconditionally, or swallows the stderr
        # payload, pass all 34 cases while the guard refused nothing.
        repo = build_repo(tmp + "/mainok", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        path_clean = os.path.join(tmp, "clean.sql")
        open(path_clean, "w").write(artifact(["0100_seed.sql"]))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_clean = module.main(["prog", repo, path_clean])
        case("main() returns 0 and says nothing when the snapshot is sound",
             rc_clean == 0 and err.getvalue() == "")

        repo_bad = build_repo(tmp + "/mainbad", {"0100_seed.sql": seeding},
                              {"carried": {}, "excluded": {}})
        # main() FAILING OPEN is the whole risk: bin/schema-snapshot.sh tests only
        # the exit status, so a zero from the could-not-run path writes the file
        # when the check never ran. A malformed classification is the cheapest way
        # to reach that path.
        broken = build_repo(tmp + "/mainbroken", {"0100_seed.sql": seeding},
                            {"carried": {"ops.widget": "x"}, "excluded": {"ops.widget": "y"}})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_broken = module.main(["prog", broken, path_clean])
        case("main() returns NON-ZERO when the check could not run at all",
             rc_broken != 0)
        case("main() says why it could not run", "could not run" in err.getvalue())

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_bad = module.main(["prog", repo_bad, path_clean])
        text = err.getvalue()
        case("main() returns NON-ZERO on a finding, so the generator actually stops",
             rc_bad != 0)
        case("main() writes the refusal and the table name to stderr",
             "REFUSING" in text and "ops.widget" in text)
        # Wrapped: a broken argc guard makes main() index past the end of argv, and
        # an uncaught traceback here would take the whole suite down instead of
        # reporting one failed case.
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc_args = module.main(["prog", repo])
            case("main() refuses a wrong argument count instead of proceeding", rc_args != 0)
        except Exception:                                    # noqa: BLE001 - any crash is a fail
            case("main() refuses a wrong argument count instead of proceeding", False)

        # ---------------------------------------------------------------- G5
        repo = build_repo(tmp + "/g5", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        moved = artifact(["0100_seed.sql"]).replace(
            LEDGER_HEADER, copy_block("public.actor", "(id, slug)") + LEDGER_HEADER, 1)
        case("a COPY above the ledger refuses, because the data-region boundary moved",
             any("DATA REGION BOUNDARY MOVED" in f for f in module.check(repo, moved)))

        # ---------------------------------------------------------------- G6
        repo = build_repo(tmp + "/g6", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        case("a ledger entry with no file on disk is reported, not silently skipped",
             any("NOT ON DISK" in f for f in
                 module.check(repo, artifact(["0100_seed.sql", "0999_renamed_away.sql"]))))

        # ---------------------------------------------------------------- G3
        repo = build_repo(tmp + "/g3", {"0100_seed.sql": seeding},
                          {"carried": {}, "carried_subset": {"ops.widget": "program rows only"},
                           "excluded": {},
                           "carried_subset_must_not_contain": {
                               "ops.widget": {"sourced captures": r'"ref":\s*"WR-0\d{5}"'}}})
        clean = artifact(["0100_seed.sql"], [insert_block("ops.widget")])
        case("a carried_subset table within its declared scope passes",
             module.check(repo, clean) == [])
        breached = artifact(["0100_seed.sql"], [
            '-- CARR APPENDED BLOCK\ninsert into ops.widget select * from '
            'jsonb_populate_record(null::ops.widget, \'{"ref": "WR-000017"}\'::jsonb);\n\n'])
        case("a carried_subset render that widens past its scope refuses",
             any("SCOPE BREACHED" in f for f in module.check(repo, breached)))
        repo = build_repo(tmp + "/g3b", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "x"}, "excluded": {},
                           "carried_subset_must_not_contain": {"ops.widget": {"a": "b"}}})
        try:
            module.check(repo, artifact(["0100_seed.sql"], [copy_block("ops.widget")]))
            case("a scope rule naming a non-subset table is rejected", False)
        except ValueError:
            case("a scope rule naming a non-subset table is rejected", True)

        # ---------------------------------------------------------------- G7
        repo = build_repo(tmp + "/g7", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": ""}, "excluded": {}})
        try:
            failures = module.check(repo, artifact(["0100_seed.sql"]))
            case("a carried entry with an empty reason refuses cleanly, never tracebacks",
                 any("DECLARED CARRIED BUT ABSENT" in f for f in failures))
        except KeyError:
            case("a carried entry with an empty reason refuses cleanly, never tracebacks", False)

        # --------------------------------------------------------------- 9b
        # carried_subset: a table carried IN PART by a scoped render. It must be
        # present like anything carried, and must NOT be held to the
        # excluded-must-be-absent rule, which would refuse the render itself.
        repo = build_repo(tmp + "/i", {"0100_seed.sql": seeding},
                          {"carried": {}, "carried_subset": {"ops.widget": "program rows only"},
                           "excluded": {}})
        case("a carried_subset table present in the artifact passes",
             module.check(repo, artifact(["0100_seed.sql"],
                                         [insert_block("ops.widget")])) == [])
        case("a carried_subset table ABSENT from the artifact refuses",
             any("DECLARED CARRIED BUT ABSENT" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"]))))
        # NOT a restatement of the case above: that one drives a clean artifact, this
        # one drives an artifact where the table is ABSENT, and pins that the failure
        # reported is the carried-but-absent one and never "unclassified".
        absent = module.check(repo, artifact(["0100_seed.sql"]))
        case("a carried_subset table reports as carried-but-absent, never unclassified",
             absent and not any("UNCLASSIFIED" in f for f in absent))
        repo = build_repo(tmp + "/j", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "x"},
                           "carried_subset": {"ops.widget": "y"}, "excluded": {}})
        try:
            module.check(repo, artifact(["0100_seed.sql"], [copy_block("ops.widget")]))
            case("a table in two buckets is rejected whichever pair it is", False)
        except ValueError:
            case("a table in two buckets is rejected whichever pair it is", True)
        repo = build_repo(
            tmp + "/j-after-apply",
            {"0100_seed.sql": seeding},
            {
                "carried": {"ops.widget": "already carried"},
                "carried_after_apply": {"ops.widget": "future carried"},
                "excluded": {},
            },
        )
        try:
            module.check(repo, artifact(["0100_seed.sql"], [copy_block("ops.widget")]))
            case("carried_after_apply cannot overlap another classification bucket", False)
        except ValueError:
            case("carried_after_apply cannot overlap another classification bucket", True)

    # -------------------------------------------------------------------- 9b
    # Carried-presence must be a ROW test, not a NAME test. pg_dump --data-only
    # emits the COPY header for a table with no rows, so an emptied carried table
    # used to pass clean -- this file's own trap arriving by deletion. Found by the
    # sixth independent review, reproduced against the real artifact by deleting the
    # 8 rows of deal_phase and the 2 of retrieval_ranking_policy and leaving their
    # headers, which changed the output by nothing at all.
    with tempfile.TemporaryDirectory() as tmp:
        carried_widget = {"carried": {"ops.widget": "vocabulary a rebuild needs"}, "excluded": {}}
        repo = build_repo(tmp + "/rows", {"0100_seed.sql": seeding}, carried_widget)

        found = module.check(repo, artifact(["0100_seed.sql"], [copy_block("ops.widget")]))
        case("negative control: a carried table with rows in its COPY block passes",
             found == [])

        found = module.check(repo, artifact(["0100_seed.sql"], [empty_copy_block("ops.widget")]))
        case("a carried table whose COPY block is EMPTY refuses, because a header "
             "carries nothing", len(found) == 1 and "DECLARED CARRIED BUT ABSENT" in found[0]
             and "ops.widget" in found[0])
        case("the emptied-table refusal does not claim to know the database state",
             found and "no longer holds the rows" not in found[0])

        # Row text arriving as a jsonb string literal is DATA. The appended blocks
        # pass whole rows this way, and reading the region raw let an `insert into`
        # inside one of those literals register as a table being present -- the
        # apostrophe class of finding 4, one region over.
        # party is SEEDED by the migration and EXCLUDED as business data, so if the
        # literal's text registered as a statement the artifact would look like it
        # had widened into business data -- the one outcome the snapshot's whole
        # design prevents. A table that is merely unclassified could not show this:
        # nothing fires for it, so the mutant would survive the case.
        both = ("create table ops.widget (k text);\n"
                "insert into ops.widget (k) values ('alpha');\n"
                "insert into party (name) values ('acme');\n")
        repo_both = build_repo(tmp + "/lit", {"0100_seed.sql": both},
                               {"carried": {"ops.widget": "vocabulary a rebuild needs"},
                                "excluded": {"party": "business data"}})
        literal = ("-- CARR APPENDED BLOCK\n"
                   "insert into ops.widget select * from jsonb_populate_record("
                   "null::ops.widget, '{\"note\": \"replaces insert into party values (1)\"}'"
                   "::jsonb) on conflict do nothing;\n\n")
        found = module.check(repo_both, artifact(["0100_seed.sql"], [literal]))
        case("row text inside a jsonb literal is data, not a statement, so it "
             "cannot make an excluded table look present", found == [])

        # The optional OR REPLACE clause decides whether a body is attributed to its
        # routine or folded into always-executing top-level text. Deleting it left
        # every case green while real detection went from 90 tables to 170.
        defined_not_called = ("create or replace function ops.g() returns void language plpgsql as $$\n"
                              "begin\n  insert into ops.only_in_body (k) values ('x');\nend $$;\n")
        case("a routine defined with CREATE OR REPLACE and never called is not a seed",
             "ops.only_in_body" not in module.written_tables(defined_not_called))
        case("the same routine IS a seed once the migration calls it",
             "ops.only_in_body" in module.written_tables(defined_not_called + "select ops.g();\n"))

    # -------------------------------------------------------------------- 9c
    # A DO BLOCK ABOVE THE LEDGER EXECUTES ON A REBUILD. check_region_boundary
    # threw its do_bodies away, and tables_with_data never reads above the ledger,
    # so rows landed by such a block were invisible to BOTH checks at once - the
    # excluded-but-present direction silently disarmed. Not hypothetical:
    # bin/schema-snapshot.sh emits the CARR ROLE PREAMBLE as a DO block above the
    # ledger, so the construct is already in the real artifact and only its
    # contents have been harmless. Found by the seventh independent review.
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp + "/do", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime data"}})

        preamble = ("do $$\nbegin\n"
                    "  insert into party (name) values ('acme');\n"
                    "end $$;\n\n")
        found = module.check(repo, preamble + artifact(["0100_seed.sql"]))
        case("a DO block above the ledger that lands rows moves the data-region "
             "boundary and refuses",
             any("DATA REGION BOUNDARY" in f for f in found))

        # The negative control that keeps this from becoming a false alarm: a
        # routine DEFINITION above the ledger is DDL, not data. The DDL half is
        # full of function bodies whose own INSERTs start a line, and reading them
        # as data made an earlier version refuse every real snapshot.
        definition = ("create function public.later() returns void language plpgsql\n"
                      "    as $$\nbegin\n  insert into party (name) values ('x');\nend $$;\n\n")
        case("negative control: a routine DEFINITION above the ledger is DDL and "
             "does not move the boundary",
             module.check(repo, definition + artifact(["0100_seed.sql"])) == [])

        # THE E-STRING TAIL. The scan reads two characters to decide whether a
        # literal takes backslash escapes, and used to reach them by rejoining the
        # whole buffer once per literal - quadratic, 37s on the real artifact.
        # Shortening the window is the obvious fix and is wrong at two characters:
        # truncation turns what preceded them into START OF STRING and the
        # pattern's own ^ alternative fires where the full buffer refused.
        # THROUGH THE REAL CALL PATH, not through _tail with a hardcoded width. A
        # case that passes its own window length cannot see the window the scanner
        # actually uses, so shortening it back to two survived that case untouched.
        # This drives the scanner instead. `case` ends a line with an alphanumeric
        # before its final e, so the full buffer says "not an E-string" and a
        # two-character view says it is. Believing the shorter view makes the
        # backslash an escape, the literal runs on to the NEXT quote, and the
        # insert after it disappears.
        swallowed = ("do $$\nbegin\n"
                     "  insert into ops.before_it (k) values ('a');\n"
                     "  if x = case\n"
                     "'a\\' then\n"
                     "  end if;\n"
                     "  insert into ops.after_it (k) values ('b');\n"
                     "end $$;\n")
        landed = module.written_tables(swallowed)
        case("a plain literal after a line ending in 'case' does not swallow the "
             "statement after it", "ops.after_it" in landed and "ops.before_it" in landed)
        escaped_call = ("create function ops.h() returns void language plpgsql as $$\n"
                        "begin\n  -- E'the reviewer\\'s registry' must not swallow the call\n"
                        "  insert into ops.landed (k) values (E'a\\'b');\nend $$;\n"
                        "select ops.h();\n")
        case("an E-string with a backslash-escaped quote still leaves the call visible",
             "ops.landed" in module.written_tables(escaped_call))

    # PostgreSQL concatenates newline-separated adjacent literals. Doubling
    # their count must stay below quadratic growth; use best-of-two to reduce
    # scheduling noise, plus a small allowance for timer resolution.
    timings = []
    for count in (16000, 32000):
        sql = "select " + "'x'\n" * count + ";"
        samples = []
        for _ in range(2):
            start = time.perf_counter()
            module.scan_sql(sql)
            samples.append(time.perf_counter() - start)
        timings.append(min(samples))
    case(f"adjacent literal scanning scales below quadratic ({timings})",
         timings[1] < timings[0] * 3.2 + 0.03)

    # -------------------------------------------------------------------- 9d
    # A COPY WITH NO TERMINATOR used to blank the region to end of file, so a
    # truncated artifact silently hid every statement after it - including an
    # excluded table's rows, which is the one thing the presence direction exists
    # to catch. Silence is the worst available answer to a malformed artifact.
    # Found by the seventh independent review as an untested branch.
    with tempfile.TemporaryDirectory() as tmp:
        repo = build_repo(tmp + "/trunc", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "vocabulary a rebuild needs"},
                           "excluded": {"party": "business data"}})

        truncated = "COPY ops.widget (a, b) FROM stdin;\n1\tvalue\n"   # no terminator
        found = module.check(repo, artifact(["0100_seed.sql"], [truncated]))
        case("a COPY block that never ends is reported, not silently swallowed",
             any("COPY BLOCK NEVER ENDS" in f for f in found))

        # The damage the old behaviour did, pinned directly: rows for an EXCLUDED
        # table sitting after the unterminated block must still be seen.
        hidden = truncated + "\ninsert into party (name) values ('acme');\n"
        found = module.check(repo, artifact(["0100_seed.sql"], [hidden]))
        case("statements after an unterminated COPY are still read, so an excluded "
             "table cannot hide behind one",
             any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))

        # A file-based COPY has no inline block to count, so presence of the
        # statement is all there is and it stays a name test. Nothing in this
        # artifact takes that path today; the case exists so the asymmetry is a
        # decision on the record rather than an accident nobody noticed.
        from_file = "COPY party (name) FROM '/tmp/party.csv';\n\n"
        found = module.check(repo, artifact(["0100_seed.sql"], [from_file]))
        case("a file-based COPY still registers the table as present",
             any("DECLARED EXCLUDED BUT PRESENT" in f and "party" in f for f in found))

        # ...and the same statement is NOT enough for a CARRIED table. The two
        # directions read different sets on purpose: presence stays broad, because
        # an excluded table named in the artifact is business data arriving and a
        # name is reason enough to stop. Carried is a ROW test, because the failure
        # there is a snapshot that cannot rebuild its own database, and a file the
        # snapshot does not contain rebuilds nothing.
        carried_from_file = "COPY deal_phase (name) FROM '/tmp/phase.csv';\n\n"
        repo_cf = build_repo(tmp + "/r6cf", {"0100_cf.sql": "insert into deal_phase (name) values ('x');\n"},
                             {"carried": {"deal_phase": "closed vocabulary"}, "excluded": {}})
        found = module.check(repo_cf, artifact(["0100_cf.sql"], [carried_from_file]))
        case("a carried table is NOT satisfied by a file-based COPY, which carries no rows",
             any("DECLARED CARRIED BUT ABSENT" in f and "deal_phase" in f for f in found))

    # -------------------------------------------------------------------- 10
    # The live classification must actually cover the live tree, or the check
    # would refuse every real snapshot and get switched off.
    config = REPO / "ops" / "config" / "snapshot-seed-coverage.json"
    doc = json.loads(config.read_text(encoding="utf-8"))
    carried, excluded = doc["carried"], doc["excluded"]
    subset = doc.get("carried_subset") or {}
    case("no table appears in two buckets in the live classification",
         not (set(carried) & set(excluded)) and not (set(carried) & set(subset))
         and not (set(subset) & set(excluded)))
    case("every carried_subset entry names the scope it carries",
         all("only" in str(v).lower() for v in subset.values()))
    case("every live classification entry states a reason",
         all(str(v).strip() for v in
             list(carried.values()) + list(subset.values()) + list(excluded.values())))
    case("source-merge plan scope is operational authority history, never snapshot seed",
         "ops.source_merge_plan_scope" in excluded
         and "accepted-plan" in excluded["ops.source_merge_plan_scope"]
         and "ops.source_merge_plan_scope" not in carried
         and "ops.source_merge_plan_scope" not in subset)

    if FAILURES:
        print(f"\nFAILED {len(FAILURES)}: {'; '.join(FAILURES)}")
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
