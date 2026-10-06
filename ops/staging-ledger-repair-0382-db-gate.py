#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Disposable-PostgreSQL crash-recovery proof for the staging 0382 repair."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qsl, urlparse

import psycopg
from psycopg.conninfo import conninfo_to_dict


REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "staging-ledger-repair-0382.py"
SELFTEST = REPO / "ops" / "staging-ledger-repair-0382-selftest.py"
MIGRATION_NAME = "0382_standing_guidance_reader_boundary.sql"
EXPECTED_SHA256 = "a6ffe5f29e9224f263b0c6a90c414b4828915a5ed3265e52e8fadbe31ef8c2bc"


def refuse(message: str) -> NoReturn:
    raise RuntimeError(message)


def run(command: list[str], env: dict[str, str], label: str) -> str:
    result = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        refuse(f"{label} failed: {detail[-1][:240] if detail else 'no output'}")
    return result.stdout


def require_disposable_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    try:
        conninfo = conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise RuntimeError("0382 repair gate requires valid explicit conninfo") from exc
    overriding = {"host", "hostaddr", "dbname", "user", "port"}
    if any(key.lower() in overriding for key, _value in parse_qsl(parsed.query)):
        refuse("0382 repair gate refuses target overrides in URI query parameters")
    if conninfo.get("service") or conninfo.get("servicefile"):
        refuse("0382 repair gate refuses libpq service indirection")
    hosts = [str(conninfo.get(key) or "") for key in ("host", "hostaddr")]
    hosts = [host for host in hosts if host]
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not hosts
        or any("," in value for value in hosts)
        or "," in str(conninfo.get("port") or "")
        or conninfo.get("dbname") != "carr_ci"
        or conninfo.get("user") != "carr_ci"
    ):
        refuse("0382 repair gate requires one explicit disposable carr_ci target")
    for host in hosts:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            refuse("0382 repair gate refuses every non-loopback target")


def connected_identity_is_disposable(
    identity: tuple[object, ...] | None,
    environment: dict[str, str],
) -> bool:
    if identity is None or identity[:3] != ("carr_ci", "carr_ci", True):
        return False
    try:
        addresses = [ipaddress.ip_address(str(value)) for value in identity[3:5]]
    except ValueError:
        return False
    if len(addresses) != 2:
        return False
    if all(address.is_loopback for address in addresses):
        return True
    hosted = (
        environment.get("GITHUB_ACTIONS", "").lower() == "true"
        and environment.get("CI", "").lower() == "true"
        and environment.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and environment.get("GITHUB_REPOSITORY") == "jbookout/carr-system"
    )
    # GitHub's PostgreSQL service is reached through an explicit localhost
    # port published from Docker.  The server and client therefore observe the
    # runner's private bridge addresses, not loopback, even though libpq's
    # effective target was already proven loopback above.
    return hosted and all(address.is_private and not address.is_global for address in addresses)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    require_disposable_dsn(dsn)

    env = dict(os.environ)
    env["DATABASE_URL"] = dsn
    run([sys.executable, str(SELFTEST)], env, "pre-recovery boundary selftest")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select current_user,current_database(),r.rolsuper,"
                "pg_catalog.host(pg_catalog.inet_server_addr()),"
                "pg_catalog.host(pg_catalog.inet_client_addr()) "
                "from pg_catalog.pg_roles r where r.rolname=current_user"
            )
            identity = cur.fetchone()
            if not connected_identity_is_disposable(identity, env):
                refuse("0382 repair gate requires the disposable carr_ci superuser")
            cur.execute(
                "select sha256 from public.schema_migrations where filename=%s",
                (MIGRATION_NAME,),
            )
            if cur.fetchone() != (EXPECTED_SHA256,):
                refuse("disposable schema lacks the exact recorded 0382 digest")
            # Model the only dangerous crash window: exact 0382 DDL committed,
            # but its ledger insert did not.  This mutation is confined to the
            # disposable carr_ci database and the repair restores it below.
            cur.execute(
                "delete from public.schema_migrations where filename=%s",
                (MIGRATION_NAME,),
            )
        conn.commit()

    output = run([sys.executable, str(TOOL), "--apply"], env, "crash recovery")
    if "action=replay_and_record" not in output or "replayed exact 0382 bytes" not in output:
        refuse("crash recovery did not prove exact immutable replay before recording")

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select sha256 from public.schema_migrations where filename=%s",
                (MIGRATION_NAME,),
            )
            if cur.fetchone() != (EXPECTED_SHA256,):
                refuse("crash recovery did not restore the exact 0382 ledger digest")

    run([sys.executable, str(SELFTEST)], env, "post-recovery boundary selftest")
    noop = run([sys.executable, str(TOOL)], env, "recorded-state idempotency")
    if "already recorded with the immutable digest" not in noop:
        refuse("recorded exact state was not a permanent no-op")
    print("staging-ledger-repair-0382-db-gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
