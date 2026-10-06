#!/usr/bin/env python3
"""release-abandon-selftest.py — a release can be ended without shipping, and it
has to say why. Fixtures written before the verb (rule e65efc68).

WHAT PROMPTED IT. The first real releases went through ops.release on 2026-08-16
and left two candidates behind — one overtaken when main moved two commits before
Joe signed, one replaced by a version carrying the security evidence the approval
constraint requires. Both are inert: they cannot ship, because a deploy needs a
live approval matching a freshly recomputed plan hash. But nothing could move
them out of `candidate`, so the table kept two rows whose real status lived only
in a decision entry.

WHY `abandoned` AND NOT `superseded`, settled by the schema rather than by
preference. 0131 exempts only draft/candidate/abandoned from needing rebuild
evidence and an approval, so reaching `superseded` requires a full artifact
digest, dependency lock, plan hash, approver and expiry — a release that was
APPROVED, and usually one that shipped and was replaced by a later deploy. An
unapproved candidate overtaken before signing has none of that. It is abandoned,
and its reason names the successor in words.

WHAT MUST NOT BECOME POSSIBLE. Abandoning is a way to end a release, never a way
to erase one that shipped. A row that reached approved-and-deployed is history;
letting it be marked abandoned would let a deploy be written out of the record
after the fact, which is the opposite of what a release ledger is for.

WHERE THESE RUN. They need a Postgres carrying the schema and nothing more —
NOT Neon specifically. CI supplies a disposable loopback PostgreSQL service via
CARR_CI_DATABASE_URL admits this fixture, which owns a separate local cluster
so roles cannot leak into later CI classes. A developer push without that
explicit fixture DSN reports the database cases as not run. No metered Neon
branch substitutes for the disposable fixture.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import socket
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group

# The postgres CLIENT lookup, shared with ops/p1-rebuild-gate.py. Loading by path
# is how every ops gate reaches tools/db-tap.py, whose hyphenated filename cannot
# be imported normally.
_spec = importlib.util.spec_from_file_location("db_tap", REPO / "tools" / "db-tap.py")
if _spec is None or _spec.loader is None:
    sys.exit("release-abandon-selftest: could not load tools/db-tap.py")
db_tap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db_tap)
ABANDON_DB = "abandon_check"
PROVIDER = "cloudflare-workers"
PROVIDER_VERSION = "11111111-2222-4333-8444-555555555555"

PASSED: int = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def psql(dsn, *args):
    # Not the bare name: with no client installed that failed as FileNotFoundError
    # from inside subprocess, naming neither the missing dependency nor the fix.
    return subprocess.run([db_tap.psql_bin(), dsn, "-v", "ON_ERROR_STOP=1", *args],
                          capture_output=True, text=True, timeout=1800)


# Every ops-record credential is pinned to this disposable cluster. The tool
# loads a developer's db.env with setdefault, so an unset jobs credential could
# otherwise send a candidate fixture to Production. Current candidates use the
# jobs login; historical approval exercises still use Joe's authority login.
AUTHORITY_DSN: str | None = None
JOBS_DSN: str | None = None


def credential_names() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("ops_record", REPO / "tools" / "ops-record.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load ops-record credential inventory")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.credential_names()


def record(dsn, *args):
    isolated = {name: dsn for name in credential_names()}
    isolated["CARR_DB_AUTHORITY_JOE_URL"] = AUTHORITY_DSN or dsn
    isolated["CARR_DB_JOBS_URL"] = JOBS_DSN or dsn
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "ops-record.py"), *args],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, **isolated})


def provision_authority_principal(dsn: str) -> None:
    """Give this cluster the human authority and service login roles.

    `ops.authority_actor_slug()` maps `session_user` to a partner slug and admits
    only carr_authority_joe and carr_authority_dell, and EXECUTE on it is granted
    to the carr_authority bundle — so the role has to exist, have login, and hold
    that membership for any authority-connection command to work here. Its
    password is the base DSN's own, so nothing about the throwaway cluster's
    credentials is written down here.

    Current candidate inserts use carr_jobs; this authority login remains for
    historical approval fixture commands. Its grants still come from numbered
    migrations rather than being fabricated by this test.
    """
    global AUTHORITY_DSN, JOBS_DSN
    params = psycopg.conninfo.conninfo_to_dict(dsn)
    password = params.get("password")
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select 1 from pg_roles where rolname = 'carr_authority_joe'")
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL("create role carr_authority_joe login password {}")
                       .format(sql.Literal(password)))
            else:
                cursor.execute(
                    sql.SQL("alter role carr_authority_joe login password {}")
                       .format(sql.Literal(password)))
            cursor.execute("grant carr_authority to carr_authority_joe")
            cursor.execute("grant usage on schema ops to carr_authority_joe")
            cursor.execute(
                sql.SQL("alter role carr_jobs login password {}")
                   .format(sql.Literal(password)))
    AUTHORITY_DSN = psycopg.conninfo.make_conninfo(dsn, user="carr_authority_joe")
    JOBS_DSN = psycopg.conninfo.make_conninfo(dsn, user="carr_jobs")


@contextmanager
def isolated_ci_database(base_dsn: str) -> Iterator[str]:
    """Own a fresh cluster: databases alone do not isolate PostgreSQL roles.

    The gate fixture applies pending role migrations before the migration class.
    A sibling database leaves those cluster-global roles behind when dropped,
    causing a later role-creation migration to refuse the unknown login.
    Keep the CI loopback admission check, but never connect to its shared server.
    """
    params = psycopg.conninfo.conninfo_to_dict(base_dsn)
    if str(params.get("host") or "") not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("release-abandon isolation requires loopback PostgreSQL")
    spec = importlib.util.spec_from_file_location("release_abandon_local_pg", REPO / "ops/local-pg-ci.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("release-abandon local PostgreSQL helper unavailable")
    local_pg = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = local_pg
    spec.loader.exec_module(local_pg)
    binaries = local_pg.find_postgres_binaries()
    env = local_pg.scrub_cloud_environment(os.environ)

    def checked(args: list[str | Path]) -> None:
        result = fixture.run([str(arg) for arg in args], env=env,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError("release-abandon disposable PostgreSQL step failed: " + Path(str(args[0])).name)

    with postgres_fixture_group(), DisposablePostgres("release-abandon-", binaries.pg_ctl, env=env, directory=None) as fixture:
        root = fixture.root
        data = root / "data"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        checked([binaries.initdb, "-D", data, "-U", "carr_ci", "--auth=trust",
                 "--encoding=UTF8", "--no-locale"])
        checked([binaries.pg_ctl, "-D", data, "-l", root / "postgres.log",
                 "-o", f"-h 127.0.0.1 -p {port} -c unix_socket_directories= -c fsync=off", "-w", "start"])
        dsn = psycopg.conninfo.make_conninfo(host="127.0.0.1", port=port,
                                            user="carr_ci", dbname="postgres")
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("create role neondb_owner")
        yield dsn


def _cases(dsn: str) -> None:
    provision_authority_principal(dsn)
    record(dsn, "sync-registry")
    # Candidate intake verifies every environment before opening the database,
    # so every abandonment fixture uses a real staging manifest rather than a
    # synthetic shape that the release door must refuse. Each fixture uses a
    # distinct repository revision so their immutable source evidence differs.
    staging_manifests: dict[str, Path] = {}
    staging_error = ""
    fixture_keys = ("rel-abandon-a", "rel-abandon-b", "rel-malformed", "rel-successor")
    for offset, key in enumerate(fixture_keys, start=1):
        staging_built = subprocess.run(
            [sys.executable, str(REPO / "tools" / "release-manifest.py"),
             "build", "--sha", f"HEAD~{offset}", "--environment", "staging",
             "--performance-budget-ref", "runbook:worker-performance-v1",
             "--performance-budget-ms", "1500",
             "--recovery-strategy", "rollback",
             "--rollback-plan-ref", "runbook:rollback-worker-v1"],
            cwd=REPO, capture_output=True, text=True, timeout=300)
        if staging_built.returncode != 0:
            staging_error = (staging_built.stderr or staging_built.stdout).strip()[:160]
            break
        manifest_path = (Path(os.environ.get("TMPDIR", "/tmp")) /
                         f"abandon-manifest-{offset}.json")
        manifest_path.write_text(staging_built.stdout)
        staging_manifests[key] = manifest_path
    check("0. canonical staging source manifests build on distinct revisions",
          not staging_error and len(staging_manifests) == len(fixture_keys),
          staging_error)
    if staging_error or len(staging_manifests) != len(fixture_keys):
        return

    for k in fixture_keys:
        record(dsn, "release", "candidate", "--key", k,
               "--manifest", str(staging_manifests[k]),
               "--service", "carr-mcp", "--environment", "staging",
               "--test-evidence", "ref", "--security-evidence", "ref")
    # Production candidate intake now rebuilds the manifest before it opens a
    # DB connection. Build and bind the fixture through the canonical tool so
    # this test carries real source evidence instead of a synthetic digest that
    # the real intake path must correctly refuse.
    built = subprocess.run(
        [sys.executable, str(REPO / "tools" / "release-manifest.py"),
         "build", "--sha", "HEAD", "--environment", "production",
         "--performance-budget-ref", "runbook:worker-performance-v1",
         "--performance-budget-ms", "1500",
         "--recovery-strategy", "rollback",
         "--rollback-plan-ref", "runbook:rollback-worker-v1"],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    check("0a. canonical Production source manifest builds",
          built.returncode == 0, (built.stderr or built.stdout).strip()[:160])
    if built.returncode != 0:
        return
    production_mpath = Path(os.environ.get("TMPDIR", "/tmp")) / "abandon-production-manifest.json"
    source_mpath = Path(os.environ.get("TMPDIR", "/tmp")) / "abandon-production-source.json"
    source_mpath.write_text(built.stdout)
    bound = subprocess.run(
        [sys.executable, str(REPO / "tools" / "release-manifest.py"),
         "bind-provider", "--manifest", str(source_mpath),
         "--provider", PROVIDER, "--provider-version-id", PROVIDER_VERSION],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    check("0aa. canonical Production manifest binds the provider version",
          bound.returncode == 0, (bound.stderr or bound.stdout).strip()[:160])
    if bound.returncode != 0:
        return
    production_mpath.write_text(bound.stdout)
    candidate = record(dsn, "release", "candidate", "--key", "rel-shipped",
                       "--manifest", str(production_mpath), "--service", "carr-mcp",
                       "--environment", "production",
                       "--provider", PROVIDER, "--provider-version-id", PROVIDER_VERSION,
                       "--test-evidence", "ref",
                       "--security-evidence", "ref")
    check("0ab. verified Production candidate reaches the ledger",
          candidate.returncode == 0,
          (candidate.stderr or candidate.stdout).strip()[:160])
    if candidate.returncode != 0:
        return

    # Program 5 makes independent verification and rollback readiness
    # prerequisites for approval, rather than evidence added after deployment.
    # Only the two fixtures that will enter a promoted state need them; the
    # abandoned candidate remains a deliberately ordinary candidate.
    ready = psql(
        dsn, "-c",
        "update ops.release "
        "set verifier_actor='independent-selftest', "
        "    verifier_evidence_ref='ops/release-abandon-selftest.py#verification', "
        "    rollback_ready=true, "
        "    rollback_plan_ref='ops/release-abandon-selftest.py#rollback' "
        "where release_key='rel-abandon-b'; "
        "update ops.release "
        "set verifier_actor='independent-selftest', "
        "    verifier_evidence_ref='ops/release-abandon-selftest.py#verification' "
        "where release_key='rel-shipped'")
    check("0b. promoted fixtures carry verifier and rollback evidence",
          ready.returncode == 0, (ready.stderr or ready.stdout).strip()[:160])
    # ── 1. a candidate can be abandoned, with a reason ──────────────────
    r = record(dsn, "release", "abandon", "--key", "rel-abandon-a",
               "--reason", "superseded before approval by a later candidate")
    check("1. a candidate can be abandoned with a reason", r.returncode == 0,
          (r.stderr or r.stdout).strip()[:160])
    got = psql(dsn, "-At", "-c",
               "select state, abandoned_reason is not null, ended_at is not null "
               "from ops.release where release_key='rel-abandon-a'")
    check("1b. it lands as abandoned, with its reason and an end time",
          got.stdout.strip() == "abandoned|t|t", f"got {got.stdout.strip()!r}")

    # ── 2. no reason, no abandonment ────────────────────────────────────
    r = record(dsn, "release", "abandon", "--key", "rel-abandon-b")
    check("2. abandoning without a reason is REFUSED", r.returncode != 0,
          "a terminal state with no recorded reason is the thing this exists to prevent")

    # A malformed immutable candidate is never rewritten into the successor.
    # The governed path names an already-recorded same-target successor and
    # terminalizes only the old row, preserving its exact plan as evidence.
    before = psql(dsn, "-At", "-c",
                  "select plan_hash from ops.release where release_key='rel-malformed'")
    r = record(dsn, "release", "abandon", "--key", "rel-malformed",
               "--superseded-by", "rel-successor")
    after = psql(dsn, "-At", "-c",
                 "select state,plan_hash,abandoned_reason "
                 "from ops.release where release_key='rel-malformed'")
    fields = after.stdout.strip().split("|", 2)
    check("2a. named successor abandons the malformed candidate immutably",
          r.returncode == 0 and len(fields) == 3
          and fields[0] == "abandoned" and fields[1] == before.stdout.strip()
          and "rel-successor" in fields[2],
          (r.stderr or after.stdout).strip()[:200])

    r = record(dsn, "release", "abandon", "--key", "rel-abandon-b",
               "--superseded-by", "does-not-exist")
    still_candidate = psql(
        dsn, "-At", "-c",
        "select state from ops.release where release_key='rel-abandon-b'",
    )
    check("2b. absent successor refuses without changing the old candidate",
          r.returncode != 0 and still_candidate.stdout.strip() == "candidate")

    # ── 3. an APPROVED release can still be abandoned before it ships ──
    # Typed approval and recovery are exercised by staging-release-readback-gate.
    # This fixture owns only abandon's allowed-state boundary, so construct the
    # already-approved pre-deploy state directly rather than trying to recreate
    # Joe authority and a three-observation recovery bundle in this test.
    approved = psql(dsn, "-c",
                    "set session_replication_role=replica; "
                    "update ops.release set state='approved',approved_by_actor='joe',"
                    "approved_at=now(),approval_expires_at=now()+interval '1 hour' "
                    "where release_key='rel-abandon-b'; "
                    "set session_replication_role=origin")
    approved_state = psql(dsn, "-At", "-c",
                          "select state from ops.release where release_key='rel-abandon-b'")
    check("3a. approved pre-deploy fixture is constructed",
          approved.returncode == 0 and approved_state.stdout.strip() == "approved",
          (approved.stderr or approved.stdout or approved_state.stderr or
           f"state={approved_state.stdout.strip()!r}").strip()[:160])
    if approved.returncode != 0 or approved_state.stdout.strip() != "approved":
        return
    r = record(dsn, "release", "abandon", "--key", "rel-abandon-b",
               "--reason", "withdrawn after signing, before any deploy ran")
    check("3. an approved release can be abandoned before it ships",
          r.returncode == 0, (r.stderr or r.stdout).strip()[:160])

    # ── 4. history is not erasable ──────────────────────────────────────
    # The typed promotion/recovery path is a separate database gate. Here a
    # completed row is only the immutable historical fixture that abandon must
    # refuse; replica mode keeps this test focused on that state transition.
    completed = psql(dsn, "-c",
                     "set session_replication_role=replica; "
                     "update ops.release set state='complete',ended_at=now(),"
                     "approved_by_actor='joe',approved_at=now(),"
                     "approval_expires_at=now()+interval '1 hour' "
                     "where release_key='rel-shipped'; "
                     "set session_replication_role=origin")
    completed_state = psql(dsn, "-At", "-c",
                           "select state from ops.release where release_key='rel-shipped'")
    check("4a. completed historical fixture is constructed",
          completed.returncode == 0 and completed_state.stdout.strip() == "complete",
          (completed.stderr or completed.stdout or completed_state.stderr or
           f"state={completed_state.stdout.strip()!r}").strip()[:160])
    if completed.returncode != 0 or completed_state.stdout.strip() != "complete":
        return
    r = record(dsn, "release", "abandon", "--key", "rel-shipped",
               "--reason", "trying to erase a release that already shipped")
    check("4. a release that already shipped CANNOT be abandoned",
          r.returncode != 0,
          "abandoning is a way to END a release, never a way to erase one that shipped")

    # ── 5. an unknown key is a clear refusal, not a silent no-op ────────
    r = record(dsn, "release", "abandon", "--key", "rel-does-not-exist",
               "--reason", "this key was never recorded anywhere")
    check("5. an unknown release key is refused, not silently ignored",
          r.returncode != 0 and "rel-does-not-exist" in (r.stderr + r.stdout),
          (r.stderr or r.stdout).strip()[:160])



def run_cases(dsn: str) -> None:
    """Every assertion, against whatever Postgres was handed in."""
    if psql(dsn, "-f", str(REPO / "db" / "schema.sql")).returncode != 0:
        check("the schema loads so the rest can run", False)
        return
    mig = subprocess.run([sys.executable, str(REPO / "tools" / "migrate.py"),
                          "--apply", "--yes"], capture_output=True, text=True,
                         timeout=1800, env={**os.environ, "DATABASE_URL": dsn})
    check("0. schema and every migration apply, 0134 included",
          mig.returncode == 0,
          (mig.stderr or mig.stdout).strip().splitlines()[-1][:160]
          if (mig.stderr or mig.stdout) else "")
    if mig.returncode != 0:
        return
    _cases(dsn)


def legacy_approval_receipt_refusal(dsn: str) -> None:
    """Exercise 0205 against a populated 0202-shaped receipt table.

    A regenerated snapshot may already contain 0205.  This disposable cluster
    explicitly restores only 0205's receipt-table additions before applying the
    migration file directly; its schema_migrations ledger is intentionally not
    consulted, because raw file application is the behavior under test.
    """
    if psql(dsn, "-f", str(REPO / "db" / "schema.sql")).returncode != 0:
        check("0205 legacy-receipt fixture schema loads", False)
        return
    receipt_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    receipt_columns = psql(dsn, "-At", "-c",
                           "select count(*) from information_schema.columns "
                           "where table_schema='ops' and table_name='release_approval_receipt' "
                           "and column_name in ('verifier_actor','verifier_evidence_ref')")
    has_0205_receipt_columns = receipt_columns.stdout.strip() == "2"
    verifier_columns = (",verifier_actor,verifier_evidence_ref"
                        if has_0205_receipt_columns else "")
    verifier_values = (",'legacy-verifier','legacy:proof'"
                       if has_0205_receipt_columns else "")
    seed = psql(
        dsn, "-c",
        "set session_replication_role=replica; "
        "insert into ops.release_approval_receipt "
        "(id,idempotency_key,release_id,recovery_run_id,recovery_bundle_id,plan_hash,"
        " approved_by_actor,approved_at,approval_expires_at" + verifier_columns +
        ",approval_sha256,evidence_ref) "
        f"values ('{receipt_id}'::uuid,'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'::uuid,"
        " 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid,"
        " 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'::uuid,"
        " 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'::uuid,'legacy-plan','joe',now(),"
        " now()+interval '1 hour'" + verifier_values + ",'sha256:" + "1" * 64 + "',"
        " 'ops.program5-release-approval:sha256:" + "2" * 64 + "'); "
        "set session_replication_role=origin",
    )
    check("0205 legacy-receipt fixture seeds one preexisting append-only receipt",
          seed.returncode == 0, (seed.stderr or seed.stdout).strip()[:160])
    if seed.returncode != 0:
        return
    if has_0205_receipt_columns:
        # These are the only 0205 objects that depend on receipt verifier
        # columns. The generic append-only trigger/function remains installed.
        restore = psql(
            dsn, "-c",
            "drop trigger if exists program5_release_verifier_is_immutable on ops.release; "
            "drop function if exists ops.program5_release_verifier_is_immutable(); "
            "drop function if exists ops.approve_program5_release(text,text,uuid,integer); "
            "drop function if exists ops.approve_program5_release(text,text,uuid,integer,text,text); "
            "alter table ops.release_approval_receipt "
            "drop constraint if exists release_approval_receipt_verifier_actor_nonblank, "
            "drop constraint if exists release_approval_receipt_verifier_evidence_nonblank, "
            "drop constraint if exists release_approval_receipt_verifier_actor_canonical, "
            "drop constraint if exists release_approval_receipt_verifier_evidence_canonical; "
            "alter table ops.release_approval_receipt "
            "drop column verifier_actor, drop column verifier_evidence_ref",
        )
        check("legacy fixture restores only 0205 receipt-table additions",
              restore.returncode == 0, (restore.stderr or restore.stdout).strip()[:160])
        if restore.returncode != 0:
            return
    before = psql(dsn, "-At", "-c",
                  "select count(*),min(id::text) from ops.release_approval_receipt")
    applied = psql(dsn, "-f", str(REPO / "migrations" /
                                   "0205_program5_approval_verifier.sql"))
    detail = (applied.stderr + applied.stdout).strip()
    check("0205 refuses a populated legacy approval-receipt table in words",
          applied.returncode != 0
          and "populated 0202 evidence requires a separate audited versioned conversion" in detail,
          detail[-240:])
    after = psql(dsn, "-At", "-c",
                 "select count(*),min(id::text) from ops.release_approval_receipt")
    check("0205 refusal leaves the legacy append-only receipt unchanged",
          before.stdout.strip() == f"1|{receipt_id}"
          and after.stdout.strip() == before.stdout.strip(),
          f"before={before.stdout.strip()!r} after={after.stdout.strip()!r}")
    columns = psql(dsn, "-At", "-c",
                   "select count(*) from information_schema.columns "
                   "where table_schema='ops' and table_name='release_approval_receipt' "
                   "and column_name in ('verifier_actor','verifier_evidence_ref')")
    check("0205 refusal rolls its schema transaction back", columns.stdout.strip() == "0",
          f"new receipt columns after refusal: {columns.stdout.strip()!r}")


def main() -> int:
    # CI's throwaway Postgres first: it is cheaper, faster, and means these
    # fixtures actually run on the surface that gates the merge.
    ci_dsn = os.environ.get("CARR_CI_DATABASE_URL")
    if ci_dsn:
        print("release-abandon-selftest: using an owned disposable PostgreSQL cluster")
        try:
            with isolated_ci_database(ci_dsn) as legacy_dsn:
                legacy_approval_receipt_refusal(legacy_dsn)
            with isolated_ci_database(ci_dsn) as isolated_dsn:
                run_cases(isolated_dsn)
        except Exception:
            print("release-abandon-selftest: disposable PostgreSQL fixture unavailable",
                  file=sys.stderr)
            return 1
        print(f"\nrelease-abandon-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
        if FAILED:
            print("FAILURES: " + ", ".join(FAILED))
            return 1
        return 0

    print("release-abandon-selftest: database cases NOT RUN — "
          "CARR_CI_DATABASE_URL is absent; metered-provider fallback is disabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
