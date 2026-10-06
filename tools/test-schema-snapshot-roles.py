#!/usr/bin/env python3
"""test-schema-snapshot-roles.py — db/schema.sql must carry the roles its
grants depend on.

WHY THIS EXISTS. db/schema.sql is written by `pg_dump --schema-only --no-owner
--no-acl`, and those flags deliberately drop every OWNER TO and GRANT — an
embedded grant names roles a fresh database has never heard of, and the first
such statement aborts the load. Roles are also CLUSTER-level, so no schema dump
would carry them anyway.

The privilege bundles carr_reader, carr_writer and carr_exporter must remain
NOLOGIN for exactly this reason. carr_jobs is different: it is the unattended
runtime identity, so a rebuilt environment must create it as LOGIN (or convert
the old NOLOGIN placeholder) with a random password that is never printed.

THE TRAP, walked into on 2026-08-14. That only holds while 0115 is PENDING. The
snapshot refresh moved 0115 into the ledger as already-applied, so CI stopped
running it against the rebuilt database, the roles were never created, and the
next migration to grant anything — 0117 — died with `role "carr_jobs" does not
exist`. The failure surfaced one migration later than its cause, on a branch
that had not touched either migration.

The snapshot is supposed to be the way a fresh environment gets built. A build
declaration that omits the roles its own later migrations grant to is not one,
and the gap is invisible until a role-creating migration ages into it — which is
to say, it would have come back.
"""
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO, "db", "schema.sql")
GENERATOR = os.path.join(REPO, "bin", "schema-snapshot.sh")

# The roles the system's own migrations grant to. carr_exporter joined on
# 2026-08-14: 0006 created it, the ledger absorbed 0006, and the grants
# section (test-schema-snapshot-grants.py) carries its privileges — which
# need a role to attach to. neondb_owner is excluded on purpose: it is
# Neon's, exists on every Neon project and on no vanilla Postgres, and
# .github/workflows/ci.yml creates it for that reason.
NOLOGIN_BUNDLES = [
    "carr_reader", "carr_writer", "carr_exporter", "carr_authority",
    "carr_device_evidence", "carr_calendar_prebrief_jobs",
    "carr_calendar_prebrief_canary_jobs", "carr_calendar_prebrief_attestors",
    "carr_calendar_prebrief_email_resolver", "carr_program5_forward_fix_verifiers",
]
EXPECTED_ROLES = [*NOLOGIN_BUNDLES, "carr_jobs"]

failures: list[str] = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def main():
    if not os.path.exists(SNAPSHOT):
        print(f"FAIL: {SNAPSHOT} not present")
        return 1
    sql = open(SNAPSHOT).read()
    generator = open(GENERATOR).read() if os.path.exists(GENERATOR) else ""

    head = sql[:20000]
    for role in EXPECTED_ROLES:
        check(f"the snapshot creates {role}",
              re.search(rf"create role .*{role}|'{role}'", head, re.I) is not None)

    for role in NOLOGIN_BUNDLES:
        check(f"{role} remains a NOLOGIN privilege bundle",
              re.search(rf"create role %I nologin.*?{role}|{role}.*?create role %I nologin",
                        head, re.I | re.S) is not None)

    check("carr_jobs is created LOGIN with an unprinted random placeholder",
          re.search(r"create role %I login password %L", head, re.I) is not None
          and re.search(r"replace\(gen_random_uuid\(\)::text \|\| gen_random_uuid\(\)::text, '-', ''\)",
                        head, re.I) is not None)
    check("an old NOLOGIN carr_jobs is converted to LOGIN with a fresh placeholder",
          re.search(r"alter role %I login password %L", head, re.I) is not None
          and re.search(r"rolcanlogin", head, re.I) is not None)
    check("an already-login carr_jobs leaves its password unchanged",
          re.search(r"elsif not jobs_can_login", head, re.I) is not None)

    generated = re.search(r"cat > \"\$TMP\" <<'ROLES'\n(.*?)\nROLES", generator, re.S)
    conditional = re.search(
        r"cat >> \"\$TMP\" <<'CANONICAL_OWNERSHIP_ROLES'\n"
        r"(?P<body>.*?)\nCANONICAL_OWNERSHIP_ROLES",
        generator,
        re.S,
    )
    conditional_gate = (
        'if [ "$CANONICAL_OWNERSHIP_ACTIVATION_APPLIED" = t ]; then\n'
        'cat >> "$TMP" <<\'CANONICAL_OWNERSHIP_ROLES\''
    ) in generator and "filename='0532a_canonical_ownership_lease_activation.sql'" in generator
    issuer_in_snapshot_ledger = re.search(
        r"^0532a_canonical_ownership_lease_activation\.sql\t[0-9a-f]{64}\t",
        sql,
        re.M,
    ) is not None
    expected_preamble = generated.group(1).strip() if generated else None
    if expected_preamble is not None and issuer_in_snapshot_ledger:
        expected_preamble = (
            expected_preamble + "\n\n\n" + conditional.group("body").strip()
            if conditional and conditional_gate else None
        )
    # Once 0756 is absorbed, the generated bootstrap also carries Dot's
    # passwordless role before role-bound policies from pg_dump.
    dot = re.search(r"cat >> \"\$TMP\" <<'DOT_READER_ROLES'\n(?P<body>.*?)\nDOT_READER_ROLES", generator, re.S)
    dot_applied = re.search(r"^0756_dot_reader\.sql\t[0-9a-f]{64}\t", sql, re.M) is not None
    if expected_preamble is not None and dot_applied:
        expected_preamble = expected_preamble + "\n" + dot.group("body").strip() if dot else None
    preamble_end = sql.find("--\n-- PostgreSQL database dump")
    check("the snapshot generator carries the exact checked-in role preamble",
          expected_preamble is not None and preamble_end > 0
          and expected_preamble == sql[:preamble_end].strip())

    normalizer = re.search(r"EOF_NORMALIZER='\n(.*?)\n'", generator, re.S)
    if normalizer is None:
        eof_shape_ok = False
    else:
        normalized = subprocess.run(
            ["awk", normalizer.group(1)],
            input="first\n\nsecond\n  \n\n",
            text=True,
            capture_output=True,
            check=False,
        )
        eof_shape_ok = (
            normalized.returncode == 0
            and normalized.stdout == "first\n\nsecond\n"
        )
    check("the snapshot EOF normalizer preserves interior blanks and emits one LF",
          eof_shape_ok)

    check("creating them is idempotent, so loading the snapshot onto a cluster "
          "that already has them is not an error",
          re.search(r"pg_roles", head, re.I) is not None)

    # 0475 creates this policy only on production-like clusters where the
    # externally provisioned carr_backup login exists. pg_dump renders the
    # resulting policy as an unconditional CREATE POLICY, which makes the
    # portable snapshot unloadable on vanilla CI PostgreSQL unless the checked-
    # in artifact preserves 0475's conditional role boundary.
    normalized_sql = re.sub(r"\s+", " ", sql.lower())
    check("the external carr_backup policy is conditional on that role existing",
          re.search(
              r"if exists \(select 1 from pg_roles where rolname = 'carr_backup'\) then "
              r"create policy carr_backup_full_read on ops\.work_request "
              r"for select to carr_backup using \(true\); end if",
              normalized_sql,
          ) is not None)
    # EVERY carr_backup policy, not only 0475's. 0573 added one on
    # public.memory_item; pg_dump rendered it unconditionally and hosted CI
    # refused the snapshot with 'role "carr_backup" does not exist'.
    check("the memory_item carr_backup policy is conditional on that role existing",
          re.search(
              r"if exists \(select 1 from pg_roles where rolname = 'carr_backup'\) then "
              r"create policy carr_backup_full_read_memory_item on public\.memory_item "
              r"for select to carr_backup using \(true\); end if",
              normalized_sql,
          ) is not None)
    check("no carr_backup policy in the snapshot is unconditional",
          re.search(r"^CREATE POLICY \S+ ON \S+ .*\bTO carr_backup\b", sql, re.M) is None)
    check("the generator owns the carr_backup policy rewrite instead of relying "
          "on a hand-patched snapshot",
          "FOR SELECT TO carr_backup USING" in generator
          and "emit_carr_backup_policy(words[3], words[5])" in generator
          and 'print "do $carr_backup_snapshot_policy$"' in generator
          and 'pg_dump\'s exit status cannot be hidden behind a' in generator)

    policy_filter = re.search(r"if ! awk '\n(function emit_carr_backup_policy.*?)\n' \"\$SCHEMA_BODY\"", generator, re.S)
    for existing in (False, True):
        fixture = ""
        for policy, table in (("carr_backup_full_read", "ops.work_request"),
                              ("carr_backup_full_read_memory_item", "public.memory_item")):
            schema, name = table.split(".")
            if existing:
                fixture += f"CREATE POLICY {policy} ON {table} FOR SELECT TO carr_backup USING (true);\n"
            fixture += f"-- Name: {name}; Type: ROW SECURITY; Schema: {schema}; Owner: -\n"
        filtered = subprocess.run(["awk", policy_filter.group(1)] if policy_filter else ["false"],
                                  input=fixture, text=True, capture_output=True, check=False)
        check(f"the generator preserves both backup policies exactly once (existing={existing})",
              filtered.returncode == 0
              and filtered.stdout.count("create policy carr_backup_full_read on ops.work_request") == 1
              and filtered.stdout.count("create policy carr_backup_full_read_memory_item on public.memory_item") == 1
              and filtered.stdout.count("if exists (select 1 from pg_roles") == 2)

    # The schema body installs deferred policy-epoch triggers before the
    # appended data seeds are restored.  The migration ledger arrives before
    # the rule-delivery policy and sealed SCAC registry, so autocommitting the
    # ledger COPY makes the trigger inspect a deliberately incomplete restore.
    # Keep the whole appended data region in one transaction: the deferred
    # triggers then settle against the final coherent seed set at COMMIT.
    restore_begin = sql.find("-- CARR SNAPSHOT DATA RESTORE TRANSACTION BEGIN")
    ledger_copy = sql.find(
        "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;"
    )
    registry_seed = sql.find("insert into ops.scac_mutation_registry_version")
    restore_commit = sql.rfind("-- CARR SNAPSHOT DATA RESTORE TRANSACTION COMMIT")
    check("the appended data restore is one deferred-trigger transaction",
          restore_begin >= 0
          and ledger_copy > restore_begin
          and registry_seed > ledger_copy
          and restore_commit > registry_seed
          and re.search(r"\bBEGIN;", sql[restore_begin:ledger_copy], re.I) is not None
          and re.search(r"\bCOMMIT;", sql[restore_commit:], re.I) is not None)
    registry_entry_tail = sql.rfind("insert into ops.scac_mutation_registry_entry")
    registry_trigger_enable = sql.find(
        "alter table ops.scac_mutation_registry_entry enable trigger"
    )
    check("deferred epoch events settle before sealed registry triggers re-enable",
          registry_entry_tail > registry_seed
          and registry_trigger_enable > registry_entry_tail
          and re.search(
              r"set constraints all immediate;",
              sql[registry_entry_tail:registry_trigger_enable],
              re.I,
          ) is not None)
    # 2026-09-23: rendering every sealed SCAC version's full entry set grew the
    # file to 114.8 MB and GitHub refused the push (GH001, 100 MiB). The
    # generator now writes each distinct entry once; this ceiling makes the
    # next regression a test failure instead of a refused push.
    snapshot_bytes = os.path.getsize(SNAPSHOT)
    check("the snapshot stays far below GitHub's 100 MiB file limit",
          snapshot_bytes < 50 * 1024 * 1024, f"{snapshot_bytes} bytes")
    check("SCAC entries are restored from a deduplicated pool",
          "create temporary table carr_scac_entry_pool" in sql
          and sql.count("insert into ops.scac_mutation_registry_entry select r.* from") >= 1
          and "drop table pg_temp.carr_scac_entry_pool;" in sql)
    check("the generator owns the deferred-trigger restore transaction",
          "CARR SNAPSHOT DATA RESTORE TRANSACTION BEGIN" in generator
          and "CARR SNAPSHOT DATA RESTORE TRANSACTION COMMIT" in generator
          and "set constraints all immediate;" in generator)

    # Ordering is the whole point: a grant that runs before its role exists
    # fails, and pg_dump puts the schema body after whatever we prepend.
    # Compared by LINE NUMBER over statement lines only — the first cut of this
    # test compared character offsets across the whole file and matched the word
    # GRANT inside the bootstrap's own comment, failing on a correct file.
    lines = sql.split("\n")
    role_line = next((i for i, ln in enumerate(lines)
                      if not ln.lstrip().startswith("--")
                      and any(f"'{r}'" in ln for r in EXPECTED_ROLES)), None)
    grant_line = next((i for i, ln in enumerate(lines)
                       if re.match(r"\s*grant\s", ln, re.I)), None)
    check("the role bootstrap precedes any grant statement in the file",
          role_line is not None and (grant_line is None or role_line < grant_line),
          f"role at line {role_line}, first grant at line {grant_line}")

    # The snapshot must still be exactly what it claims: structure, the ledger,
    # and the reference vocabulary. A role bootstrap is a build declaration, not
    # business data, but the guard against widening belongs here too.
    check("no business tables were swept in with it",
          not re.search(r"^COPY public\.(party|deal|client|lead|loop_item|event)\b",
                        sql, re.M))

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
