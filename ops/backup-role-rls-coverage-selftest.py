#!/usr/bin/env python3
"""The nightly backup must read every RLS-guarded row — contract + live proof.

bin/backup-dump.sh dumps with --enable-row-security (row_security=on) so the
carr_backup role's read of ops.work_request is not refused by 0324's RLS
(WR-000044, decision 11376c54). That flag trades a fail-CLOSED behavior for a
fail-OPEN one: with row_security on, a table whose RLS hides rows from
carr_backup is dumped SHORT and SILENT rather than erroring.

TWO LAYERS, one file:

  SOURCE CONTRACT (always, no database) — every table in public+ops with RLS
  enabled must carry a permissive carr_backup SELECT policy with no row filter
  (USING (true)); and the bounded guard that owns pg_dump must pass
  --enable-row-security. Today ops.work_request is the only RLS table; 0475 adds its
  policy. This fails loudly the moment a future migration enables RLS on a
  public/ops table without a carr_backup read-all policy — before it can
  silently shrink the backup.

  LIVE PROOF (only when CARR_LOCAL_PG_DSN names a disposable PostgreSQL) — on a
  cluster with migrations applied, creates carr_backup (mirroring 0119, since
  db/schema.sql deliberately omits it — that omission was the original
  false-green), applies 0475, and proves: (a) the COMPLETE sealed SCAC v10
  census 4th block (roles+memberships+ownership, 0471's count=52/sha256:345871
  block) is byte-identical before and after the policy, with a BYPASSRLS
  positive control showing the SAME digest MOVES then returns — the census is
  bypass-sensitive but policy-insensitive; (b) row_security=off reproduces the
  nightly refusal and row_security=on gives full-row parity; (c) Joe's
  guardrails: carr_backup stays SELECT-only, NOSUPERUSER/NOCREATEDB/NOCREATEROLE/
  NOREPLICATION/NOBYPASSRLS, no bundle membership, zero write/DDL, no '^carr_'
  app role carries BYPASSRLS.

This is a *-selftest.py (not a *-local-pg-acceptance.py) deliberately: it is a
proof harness, not a wired mutation ingress, so it is excluded from the SCAC
script-entrypoint census (ops/scac-mutation-inventory.mjs isScriptEntrypoint)
and adds nothing to reseal. ci.sh runs the source contract; a reviewer runs the
live proof with a disposable DSN. Committed evidence:
ops/backup-role-rls-coverage-evidence.txt.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
BACKUP_SCRIPT = ROOT / "bin" / "backup-dump.sh"
GUARD_SCRIPT = ROOT / "bin" / "backup-guard.py"
MIGRATION_0475 = MIGRATIONS / "0475_backup_role_work_request_rls_read_policy.sql"

FAIL: list[str] = []
PASS = 0

WRITE_PRIVS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
BUNDLE_ROLES = ("carr_writer", "carr_reader", "carr_jobs", "carr_exporter", "carr_authority")

# The COMPLETE 4th SCAC v10 census block from migration 0471 (its
# scac_mutation_catalog_v10_current(), the block returning count=52 and
# sha256:345871...): role_rows UNION ALL membership_rows UNION ALL
# ownership_rows, aggregated and hashed as ONE set — copied VERBATIM from 0471
# lines 270-281 (only the final projection is widened to also return the count).
# Role ATTRIBUTES (incl. bypass_rls) live in role_rows; a table policy appears in
# none of the three sets. Absolute count/digest are environment-specific; the
# proof is the INVARIANCE (identical across the policy) and the CONTRAST (moves
# under BYPASSRLS), both deterministic.
CENSUS_V10_BLOCK4 = """
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci'
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*)::int, 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') from observed
"""


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS
    if condition:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}  {detail}")


def conninfo_text(value: str | int | None, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"CARR_LOCAL_PG_DSN {name} must be text")
    return value


# ── SOURCE CONTRACT (no database) ────────────────────────────────────────────

def normalized(sql: str) -> str:
    no_comments = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"\s+", " ", no_comments.lower()).strip()


ENABLE_RE = re.compile(r"alter table (?:only )?([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*) enable row level security")
DISABLE_RE = re.compile(r"alter table (?:only )?([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*) disable row level security")


def backup_policy_re(table: str) -> re.Pattern[str]:
    t = re.escape(table)
    return re.compile(
        r"create policy [a-z_][a-z0-9_]* on " + t
        + r" for select to carr_backup using \(\s*true\s*\)"
    )


def source_contract() -> None:
    corpus = "\n".join(
        normalized(p.read_text(encoding="utf-8"))
        for p in sorted(MIGRATIONS.glob("*.sql"))
    )
    enabled = set(ENABLE_RE.findall(corpus))
    disabled = set(DISABLE_RE.findall(corpus))
    rls_tables = sorted(enabled - disabled)

    check("at least one public/ops table has RLS (sanity: ops.work_request)",
          "ops.work_request" in rls_tables, f"rls_tables={rls_tables}")

    scoped = [t for t in rls_tables if t.split(".", 1)[0] in ("public", "ops")]
    for table in scoped:
        check(
            f"RLS table {table} has a permissive carr_backup read-all policy in migrations",
            bool(backup_policy_re(table).search(corpus)),
            f"no `create policy ... on {table} for select to carr_backup using (true)` found — "
            "with --enable-row-security this table would dump SHORT and SILENT",
        )

    script = BACKUP_SCRIPT.read_text(encoding="utf-8")
    guard = GUARD_SCRIPT.read_text(encoding="utf-8") if GUARD_SCRIPT.is_file() else ""
    check("the pg_dump command owner enables row security",
          "--enable-row-security" in guard,
          "row_security must be on so the carr_backup policy applies instead of the dump erroring")
    check("the routine wrapper invokes the bounded backup guard",
          "backup-guard.py" in script,
          "coverage cannot be inferred from migrations alone; every dump must run the guard")
    check("pg_dump imports the guard's exported snapshot",
          "--snapshot" in guard,
          "the guarded census and pg_dump must share the exported snapshot")
    check("pg_dump emits verbose TOC metadata for observed table-OID equality",
          "--verbose" in guard,
          "promotion requires class-1259 TABLE OIDs from the actual dump stream")


def require_disposable_local_dsn(dsn: str) -> str:
    """Refuse any live fixture target that is not literal loopback or owned socket.

    `localhost` is intentionally not accepted: a fixture must name the transport
    it relies on, and must never inherit a production or tunnelled hostname from
    an ordinary DATABASE_URL.
    """
    from psycopg.conninfo import conninfo_to_dict

    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("CARR_LOCAL_PG_DSN must be a PostgreSQL URI")
    info = conninfo_to_dict(dsn)
    if info.get("service") or info.get("servicefile"):
        raise ValueError("service-based DSNs are not fixture authority")
    host = conninfo_text(info.get("host"), "host")
    hostaddr = conninfo_text(info.get("hostaddr"), "hostaddr")
    port = conninfo_text(info.get("port"), "port")
    if any("," in value for value in (host, hostaddr, port)):
        raise ValueError("multi-host DSNs are not permitted")
    if hostaddr and hostaddr not in {"127.0.0.1", "::1"}:
        raise ValueError("effective hostaddr is not literal loopback")
    if host in {"127.0.0.1", "::1"} and (not hostaddr or hostaddr == host):
        return dsn
    if host.startswith("/"):
        resolved = Path(host).resolve(strict=True)
        st = resolved.stat()
        if resolved.is_dir() and st.st_uid == os.getuid() and not (st.st_mode & 0o022):
            return dsn
    raise ValueError(
        "CARR_LOCAL_PG_DSN must use literal 127.0.0.1/::1 or an owned, non-writable Unix socket directory"
    )


def local_dsn_contract() -> None:
    def refused(raw: str) -> bool:
        try:
            require_disposable_local_dsn(raw)
        except (OSError, ValueError):
            return True
        return False

    check("live fixture accepts literal IPv4 loopback",
          require_disposable_local_dsn("postgresql://fixture@127.0.0.1:5432/fixture") != "")
    check("live fixture rejects localhost, remote query override and service DSNs",
          refused("postgresql://fixture@localhost:5432/fixture")
          and refused("postgresql://fixture@127.0.0.1:5432/fixture?host=remote.invalid")
          and refused("postgresql:///fixture?service=production"))
    with tempfile.TemporaryDirectory(prefix="carr-backup-role-socket-") as raw:
        socket_dir = Path(raw)
        socket_dir.chmod(0o700)
        dsn = "postgresql:///fixture?host=" + quote(str(socket_dir), safe="")
        check("live fixture accepts only an owned non-writable Unix socket directory",
              require_disposable_local_dsn(dsn) == dsn)
        socket_dir.chmod(0o777)
        check("live fixture rejects a writable Unix socket directory", refused(dsn))


# ── LIVE PROOF (requires a disposable CARR_LOCAL_PG_DSN) ──────────────────────

def one(cur, sql: str, args: tuple = ()):
    row = cur.execute(sql, args).fetchone()
    return None if row is None else row[0]


def census_block(cur) -> tuple[int, str]:
    row = cur.execute(CENSUS_V10_BLOCK4).fetchone()
    assert row is not None
    return int(row[0]), str(row[1])


def live_proof(dsn: str) -> None:
    import psycopg  # lazy: the source contract needs no database or driver

    def count_as_backup(table: str, row_security: str):
        try:
            with psycopg.connect(dsn) as c, c.cursor() as cur:
                cur.execute(f"set row_security = {row_security}")
                cur.execute("set role carr_backup")
                return True, one(cur, f"select count(*) from {table}")
        except psycopg.Error as exc:
            return False, exc

    with psycopg.connect(dsn) as conn:
        # Mirror production: carr_backup exists (0119), SELECT-only, no elevation.
        with conn.cursor() as cur:
            cur.execute("""
                do $$ begin
                  if not exists (select 1 from pg_roles where rolname='carr_backup') then
                    execute 'create role carr_backup login password ''placeholder-not-used''';
                  end if;
                end $$;
            """)
            cur.execute("grant usage on schema public, ops to carr_backup")
            cur.execute("grant select on all tables in schema public to carr_backup")
            cur.execute("grant select on all tables in schema ops to carr_backup")
        conn.commit()

        with conn.cursor() as cur:
            count_before, digest_before = census_block(cur)
            bypass_before = one(cur, "select rolbypassrls from pg_roles where rolname='carr_backup'")
        print(f"  ..    full v10 census block BEFORE policy: count={count_before} digest={digest_before}")

        # Apply the fix (idempotent; carr_backup now exists so the policy is created).
        with conn.cursor() as cur:
            cur.execute(MIGRATION_0475.read_text(encoding="utf-8"))
        conn.commit()

        with conn.cursor() as cur:
            count_after, digest_after = census_block(cur)
            policy_present = one(cur, """
                select exists(select 1 from pg_policy
                  where polname='carr_backup_full_read' and polrelid='ops.work_request'::regclass)
            """)
            bypass_after = one(cur, "select rolbypassrls from pg_roles where rolname='carr_backup'")
        print(f"  ..    full v10 census block AFTER  policy: count={count_after} digest={digest_after}")

        check("migration 0475 creates carr_backup_full_read on ops.work_request", bool(policy_present))
        check("FULL SCAC v10 census block (roles+memberships+ownership) is byte-IDENTICAL across the policy",
              (count_before, digest_before) == (count_after, digest_after),
              f"before=({count_before},{digest_before}) after=({count_after},{digest_after})")
        check("carr_backup did NOT gain BYPASSRLS (fix is a policy, not a role attribute)",
              bypass_before is False and bypass_after is False)

        # POSITIVE CONTROL: the census IS bypass-sensitive. Flip carr_backup to
        # BYPASSRLS, prove the SAME full block digest MOVES, then restore.
        with conn.cursor() as cur:
            cur.execute("alter role carr_backup bypassrls")
        conn.commit()
        with conn.cursor() as cur:
            count_bypass, digest_bypass = census_block(cur)
        print(f"  ..    full v10 census block WITH BYPASSRLS: count={count_bypass} digest={digest_bypass}")
        check("CONTRAST: flipping carr_backup to BYPASSRLS MOVES the same census block digest",
              digest_bypass != digest_after,
              f"policy_digest={digest_after} bypass_digest={digest_bypass} (must differ)")
        check("CONTRAST: the count is unchanged by BYPASSRLS (only the attribute bit moves the hash)",
              count_bypass == count_after, f"count_after={count_after} count_bypass={count_bypass}")
        with conn.cursor() as cur:
            cur.execute("alter role carr_backup nobypassrls")
        conn.commit()
        with conn.cursor() as cur:
            count_restored, digest_restored = census_block(cur)
        check("CONTRAST: restoring NOBYPASSRLS returns the census block to the identical digest",
              (count_restored, digest_restored) == (count_after, digest_after),
              f"restored=({count_restored},{digest_restored}) expected=({count_after},{digest_after})")

        # Reproduce the failure, then prove the fix, on the real table.
        off_ok, off_val = count_as_backup("ops.work_request", "off")
        print(f"  ..    row_security=off as carr_backup -> "
              f"{'REFUSED: ' + str(off_val).strip() if not off_ok else 'read ' + str(off_val) + ' rows'}")
        check("row_security=off: carr_backup read of ops.work_request is REFUSED (the nightly failure)",
              (not off_ok) and "row-level security" in str(off_val).lower(),
              f"ok={off_ok} val={off_val}")

        on_ok, on_val = count_as_backup("ops.work_request", "on")
        with conn.cursor() as cur:
            true_wr = one(cur, "select count(*) from ops.work_request")
            true_siep = one(cur, "select count(*) from ops.work_request "
                                 "where program_key='carr-system-integrity-elimination-v1'")
        print(f"  ..    row_security=on  as carr_backup -> read {on_val} rows; true count={true_wr}; "
              f"SIEP-keyed rows={true_siep}")
        check("row_security=on: carr_backup reads ops.work_request without error", on_ok, str(on_val))
        check("row_security=on: carr_backup sees EVERY work_request row (parity, no silent omission)",
              on_ok and on_val == true_wr, f"backup={on_val} true={true_wr}")
        check("parity is non-vacuous: work_request has rows to omit", (true_wr or 0) > 0, f"true={true_wr}")
        check("SIEP-program rows are visible to the backup too",
              on_ok and on_val == true_wr, f"siep_true={true_siep} backup_total={on_val} true={true_wr}")

        # Completeness invariant across ALL RLS tables in scope.
        with conn.cursor() as cur:
            tables = [r[0] for r in cur.execute("""
                select n.nspname||'.'||c.relname
                  from pg_class c join pg_namespace n on n.oid=c.relnamespace
                 where c.relrowsecurity and n.nspname in ('public','ops') and c.relkind in ('r','p')
                 order by 1
            """).fetchall()]
        check("scope sanity: ops.work_request is among the RLS tables", "ops.work_request" in tables,
              f"tables={tables}")
        for table in tables:
            t_ok, t_val = count_as_backup(table, "on")
            with conn.cursor() as cur:
                true_n = one(cur, f"select count(*) from {table}")
            check(f"coverage: carr_backup (row_security on) sees all rows of {table}",
                  t_ok and t_val == true_n,
                  f"backup={t_val} true={true_n} — no carr_backup read-all policy would dump this short")

        # Joe's guardrails: the grant widened nothing.
        with conn.cursor() as cur:
            attrs = cur.execute("""
                select rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin
                  from pg_roles where rolname='carr_backup'
            """).fetchone()
            assert attrs is not None, "carr_backup must exist by now"
            rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin = attrs
            check("carr_backup is NOSUPERUSER", rolsuper is False)
            check("carr_backup is NOCREATEDB", rolcreatedb is False)
            check("carr_backup is NOCREATEROLE", rolcreaterole is False)
            check("carr_backup is NOREPLICATION", rolreplication is False)
            check("carr_backup is NOBYPASSRLS", rolbypassrls is False)
            check("carr_backup can log in (unchanged)", rolcanlogin is True)

            in_bundle = one(cur, """
                select exists(select 1 from pg_auth_members m
                    join pg_roles g on g.oid=m.member join pg_roles r on r.oid=m.roleid
                   where g.rolname='carr_backup' and r.rolname = any(%s))
            """, (list(BUNDLE_ROLES),))
            check("carr_backup is in no privilege bundle", in_bundle is False)

            write_grants = one(cur, """
                select count(*) from information_schema.role_table_grants
                 where grantee='carr_backup' and privilege_type = any(%s)
            """, (list(WRITE_PRIVS),))
            check("carr_backup holds zero write/DDL-adjacent table grants", write_grants == 0,
                  f"count={write_grants}")

            # Mirror 0471's own exclusion of carr_ci (the disposable bootstrap
            # superuser, absent from production): no REAL app role may bypass RLS.
            unexpected_bypass = cur.execute("""
                select rolname from pg_roles
                 where rolbypassrls and rolname ~ '^carr_' and rolname <> 'carr_ci'
                 order by rolname
            """).fetchall()
            check("no '^carr_' app role carries BYPASSRLS (fix introduced none anywhere)",
                  unexpected_bypass == [], f"roles={[r[0] for r in unexpected_bypass]}")


def main() -> int:
    source_contract()
    local_dsn_contract()

    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "").strip()
    if dsn:
        try:
            dsn = require_disposable_local_dsn(dsn)
        except (OSError, ValueError) as exc:
            print(f"backup-role-rls-coverage-selftest: REFUSED live fixture: {exc}", file=sys.stderr)
            return 78
        print("  --    CARR_LOCAL_PG_DSN present: running live disposable-Postgres proof")
        live_proof(dsn)
    else:
        print("  --    CARR_LOCAL_PG_DSN absent: source contract only "
              "(set it to a disposable cluster for the live proof)")

    if FAIL:
        print(f"\nbackup-role-rls-coverage-selftest: {len(FAIL)} FAILED of {PASS + len(FAIL)}: {FAIL}",
              file=sys.stderr)
        return 1
    print(f"\nbackup-role-rls-coverage-selftest: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
