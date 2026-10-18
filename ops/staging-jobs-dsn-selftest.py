#!/usr/bin/env python3
"""ops/staging-jobs-dsn-selftest.py — the acceptance test for
tools/staging_jobs_dsn.py, and the regression guard for the credential-blinding
defect that file was written in response to.

NO DATABASE, NO NETWORK, ONE TIER. Everything here is a pure-function check or a
refusal proven before any connection is attempted; the staging endpoint resolver
is substituted so the suite never calls neonctl. That is deliberate: this runs in
ops/ci.sh's selftest loop on every push, and a gate that needs a provider is a
gate that goes yellow on a plane.

WHAT IT IS GUARDING, measured 2026-08-18. PR #288 (cd3d7386) moved
`tools/ops-record.py run` to connect("routine"), which reads CARR_DB_JOBS_URL and
ignores DATABASE_URL. Two suites had encoded the OLD credential order into their
"unreachable database" helpers — set DATABASE_URL to a dead port, delete
CARR_DB_JOBS_URL — so ops-record.py's _load_db_env() re-supplied the PRODUCTION
jobs DSN by setdefault and they went on testing against production. Tier 1 of
ops/scheduled-run-record-selftest.py wrote 46 fabricated SUCCEEDED rows into
production's ops.run over two days, and tier 2 of both suites recorded at
production's registry instead of staging's and failed every read-back.

The last block below is the check that would have caught it on day one: every
credential name the recorder declares must be blinded by both suites, and
blinded to somewhere unreachable. It reads ops-record.py's own list rather than
a copy, so a fourth mode added to DSN_FOR fails here until both suites blind it.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.loadpy import load_module_from_path  # noqa: E402

SJ = load_module_from_path("staging_jobs_dsn", os.path.join(REPO, "tools/staging_jobs_dsn.py"))
OPS_RECORD = load_module_from_path("sjd_ops_record", os.path.join(REPO, "tools/ops-record.py"))
SCHEDULED_RECORD = load_module_from_path(
    "sjd_scheduled_run_record_selftest", os.path.join(REPO, "ops/scheduled-run-record-selftest.py"))

STAGING_HOST = "ep-staging-probe-12345678.us-east-2.aws.neon.tech"
OWNER = f"postgresql://neondb_owner:pw@{STAGING_HOST}/neondb?sslmode=require&channel_binding=require"

PASSES: list[str] = []
FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSES.append(label)
        print(f"  ok    {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def stub_resolver(host: str = STAGING_HOST):
    """Stand in for db-tap's neonctl-backed resolver. Same shape it returns."""
    return lambda _env: ("proj-1", "br-1", host.split(".", 1)[0], host)


def refusal(fn, *args) -> str:
    try:
        fn(*args)
    except SJ.StagingJobsRefusal as exc:
        return str(exc)
    return ""


def main() -> int:
    print("staging-jobs-dsn-selftest — an ephemeral carr_jobs identity, and only "
          "ever against isolated staging\n")

    print("1. the target is proven, not asserted")
    original = SJ._db_tap._staging_runtime_target
    SJ._db_tap._staging_runtime_target = stub_resolver()
    try:
        check("the resolved staging endpoint is accepted",
              SJ.verify_staging_owner(OWNER) == (STAGING_HOST, 5432, "neondb"))
        pooled = OWNER.replace(STAGING_HOST, STAGING_HOST.replace(".", "-pooler.", 1))
        check("the pooled host of that same endpoint is accepted too",
              SJ.verify_staging_owner(pooled)[0] == STAGING_HOST)
        other = OWNER.replace(STAGING_HOST, "ep-somewhere-else-99.us-east-2.aws.neon.tech")
        check("any other host is refused — this is what keeps production out",
              "does not address the isolated staging read-write endpoint" in refusal(
                  SJ.verify_staging_owner, other))
        check("a DSN with no host is refused",
              "names no host" in refusal(SJ.verify_staging_owner, "postgresql:///neondb"))
        check("a DSN with no database is refused",
              "names no database" in refusal(
                  SJ.verify_staging_owner, f"postgresql://neondb_owner:pw@{STAGING_HOST}/"))

        print("\n2. minting refuses before it connects to anything")
        not_owner = OWNER.replace("neondb_owner", "carr_jobs")
        check("a non-owner DSN cannot be used to mint (build_role_uri's guard, "
              "which is a second independent reason a production JOBS credential "
              "can never be the input)",
              "cannot be used to construct" in refusal(SJ.mint, not_owner))
        check("a weak generated password is refused before any ALTER ROLE",
              "256 bits" in refusal(lambda: SJ.mint(OWNER, password_factory=lambda: "short")))
    finally:
        SJ._db_tap._staging_runtime_target = original

    print("\n3. the routine environment is the production credential shape")
    contract = SJ._forbidden_names()
    check("the forbidden list comes from ops/config/control-plane-provisioning.v1.json",
          "DATABASE_URL" in contract and "CARR_DB_EXPORTER_URL" in contract, str(contract))
    base = {name: "leaked" for name in contract + SJ.PG_LEAK_VARS}
    base["CARR_CORRELATION_ID"] = "keep-me"
    env = SJ.routine_env(base, "postgresql://carr_jobs:pw@host/db")
    check("every forbidden credential name is stripped",
          not any(name in env for name in contract), str(sorted(env)))
    check("every libpq re-aiming variable is stripped",
          not any(name in env for name in SJ.PG_LEAK_VARS), str(sorted(env)))
    check("unrelated variables survive — this is not env -i",
          env.get("CARR_CORRELATION_ID") == "keep-me")
    check("the jobs credential is what remains",
          env.get("CARR_DB_JOBS_URL") == "postgresql://carr_jobs:pw@host/db")

    print("\n4. REGRESSION — both scheduled-run suites blind EVERY credential "
          "the recorder reads")
    names = OPS_RECORD.credential_names()
    check("the recorder declares at least the jobs and owner names",
          "CARR_DB_JOBS_URL" in names and "DATABASE_URL" in names, str(names))
    for label, built in (("ops/scheduled-run-record-selftest.py",
                           SCHEDULED_RECORD.unreachable_db_env()),):
        missing = [n for n in names if n not in built]
        check(f"{label} sets every one of them (deleting a name is what let "
              f"db.env's setdefault re-supply production)", not missing, str(missing))
        reachable = [n for n in names
                     if urlsplit(built.get(n, "")).hostname not in ("127.0.0.1", "::1")
                     or urlsplit(built.get(n, "")).port != 1]
        check(f"{label} points every one at a port nothing listens on",
              not reachable, str(reachable))
        check(f"{label}'s jobs value still SPELLS carr_jobs, so what fails is the "
              f"connection and not the credential-shape check",
              OPS_RECORD._is_jobs_dsn(built.get("CARR_DB_JOBS_URL", "")))

    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    if FAILURES:
        print("\nSELFTEST NOT MET:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SELFTEST MET: the staging jobs identity is ephemeral, staging-only and "
          "proven so, and no suite can quietly regain a live production credential.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
