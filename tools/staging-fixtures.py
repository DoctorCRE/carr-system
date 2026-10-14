#!/usr/bin/env python3
"""Install the minimal deterministic, invented staging G1 fixture.

Default mode is a read-only plan. ``--apply`` requires an explicit owner DSN
and replacement provider identities. No source row or identifier is printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import uuid
from decimal import Decimal
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, unquote, urlsplit

FIXTURE_ID = uuid.uuid5(uuid.UUID("80d943c3-53af-4a13-9200-f3d8581a459a"),
                        "carr-clean-staging-replacement-party-v1")
INVOICE_NAMESPACE = uuid.UUID("4acbfa5b-1f38-47af-aac0-1c4688cfc479")
PLAN_SCHEMA = "doctorcre-staging-records-plan.v1"
STAGING_ORIGIN = "https://doctorcre-app-staging.joe-bookout-carr-us.workers.dev"
MAX_PLAN_BYTES = 1_048_576
PRODUCTION_PROJECT_ID = "steep-field-48688294"
REPO = pathlib.Path(__file__).resolve().parents[1]
NEONCTL = REPO / "mcp-server/node_modules/.bin/neonctl"
NEON_ORG_ID = "org-dry-dew-75906281"
Run = Callable[..., subprocess.CompletedProcess]


class FixtureRefusal(RuntimeError):
    pass


def _private_plan_path(path: pathlib.Path) -> pathlib.Path:
    if ".." in pathlib.Path(path).parts:
        raise FixtureRefusal("invoice plan path contains parent traversal")
    path = pathlib.Path(os.path.abspath(path))
    cursor = pathlib.Path(path.anchor)
    try:
        for part in path.parts[1:-1]:
            cursor /= part
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise FixtureRefusal("invoice plan path has an unsafe ancestor")
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not info.st_mode & stat.S_ISVTX:
                raise FixtureRefusal("invoice plan path has an untrusted writable ancestor")
    except OSError as exc:
        raise FixtureRefusal("invoice plan ancestor is unavailable") from exc
    return path


def load_private_plan(path: pathlib.Path, expected_app_sha: str, expected_carr_sha: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_app_sha or "") or not re.fullmatch(r"[0-9a-f]{40}", expected_carr_sha or ""):
        raise FixtureRefusal("invoice mode requires explicit full source pins")
    path = _private_plan_path(path)
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
            raise FixtureRefusal("platform cannot safely open a no-follow nonblocking invoice plan")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | os.O_NONBLOCK
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise FixtureRefusal("invoice plan must be an owned private regular file")
            if info.st_size > MAX_PLAN_BYTES:
                raise FixtureRefusal("invoice plan exceeds the bounded size")
            raw = stream.read(MAX_PLAN_BYTES + 1)
        if len(raw) > MAX_PLAN_BYTES:
            raise FixtureRefusal("invoice plan exceeds the bounded size")
        value = json.loads(raw)
    except FixtureRefusal:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise FixtureRefusal("invoice plan is unreadable or malformed") from exc
    if not isinstance(value, dict) or value.get("schema") != PLAN_SCHEMA or value.get("origin") != STAGING_ORIGIN:
        raise FixtureRefusal("invoice plan schema or staging origin is not exact")
    release = value.get("release")
    if not isinstance(release, dict) or release.get("source_commit") != expected_app_sha or release.get("carr_source_commit") != expected_carr_sha:
        raise FixtureRefusal("invoice plan differs from the explicitly expected source pair")
    if value.get("state") != "complete" or "inflight" not in value or value.get("inflight") is not None:
        raise FixtureRefusal("invoice plan is incomplete or has an unsettled write")
    if value.get("current_step") != "readback" or not isinstance(value.get("keys"), dict) or not isinstance(value.get("receipts"), dict):
        raise FixtureRefusal("invoice plan is missing the app setup completion fields")
    run = value.get("run")
    if not isinstance(run, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", run):
        raise FixtureRefusal("invoice plan run identifier is invalid")
    observed = value.get("observed_at")
    if not isinstance(observed, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", observed):
        raise FixtureRefusal("invoice plan observation timestamp is not canonical")
    try:
        observed_at = dt.datetime.strptime(observed, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise FixtureRefusal("invoice plan observation timestamp is invalid") from exc
    names, records = value.get("names"), value.get("records")
    party = records.get("party") if isinstance(records, dict) else None
    client = records.get("client") if isinstance(records, dict) else None
    invoice = records.get("invoice") if isinstance(records, dict) else None
    if not isinstance(names, dict) or not isinstance(party, dict) or not isinstance(client, dict) or not isinstance(invoice, dict):
        raise FixtureRefusal("invoice plan lacks the app-created fixture identity chain")
    if party.get("id") != str(FIXTURE_ID) or party.get("name") != "Synthetic Staging Fixture" or client.get("name") != "Synthetic Staging Fixture":
        raise FixtureRefusal("invoice plan party is not the exact synthetic QA party")
    try:
        uuid.UUID(client.get("id", ""))
        uuid.UUID(invoice.get("deal_id", ""))
    except (ValueError, AttributeError, TypeError) as exc:
        raise FixtureRefusal("invoice plan client or deal identifier is invalid") from exc
    expected_name = f"Synthetic staging QA {run} closed deal"
    if names.get("invoice") != expected_name or invoice.get("name") != expected_name:
        raise FixtureRefusal("invoice plan deal name does not bind to its run")
    value["_invoice_date"] = observed_at.date()
    return value


def invoice_commission_values(plan: Mapping[str, Any]) -> tuple[Any, ...]:
    run = plan["run"]
    deal_id = uuid.UUID(plan["records"]["invoice"]["deal_id"])
    commission_id = uuid.uuid5(INVOICE_NAMESPACE, f"staging-invoice-commission:{run}")
    invoice_date = plan.get("_invoice_date")
    if invoice_date is None:
        invoice_date = dt.datetime.strptime(plan["observed_at"], "%Y-%m-%dT%H:%M:%S.%fZ").date()
    return commission_id, deal_id, Decimal("1.00"), invoice_date, None


def install_invoice_fixture(conn: Any, plan: Mapping[str, Any]) -> int:
    """Seed and verify a synthetic invoice. Provenance is system attribution plus DB readback, not an MCP event/receipt."""
    values = invoice_commission_values(plan)
    commission_id, deal_id, gross, invoiced_on, _ = values
    try:
        with conn.cursor() as cur:
            cur.execute("select session_user,current_user")
            if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner"):
                raise FixtureRefusal("fixture installation requires exact owner session")
            cur.execute("select id from public.actor where slug='system'")
            actor = cur.fetchone()
            if actor is None:
                raise FixtureRefusal("exact schema seed lacks system actor")
            cur.execute("select current_date")
            today = cur.fetchone()
            if not today or invoiced_on > today[0]:
                raise FixtureRefusal("recorded invoice observation date is after database current date")
            cur.execute("""select d.id::text,d.name,d.phase,d.outcome,d.owner,cl.id::text,cl.merged_into,
                       p.id::text,p.name,p.deleted_at,p.merged_into
                  from public.deal d join public.client cl on cl.id=d.client_id
                  join public.party p on p.id=cl.party_id
                 where d.id=%s for update of d,cl,p""", (deal_id,))
            deal = cur.fetchone()
            expected_name = plan["records"]["invoice"]["name"]
            if not deal or len(deal) != 11 or tuple(deal[:3]) != (str(deal_id), expected_name, "closed") \
                    or deal[3] not in (None, "won") or deal[4] != "joe" or tuple(deal[5:]) != (
                        str(uuid.UUID(plan["records"]["client"]["id"])), None, str(FIXTURE_ID),
                        "Synthetic Staging Fixture", None, None):
                raise FixtureRefusal("app-owned closed QA deal and active fixture-party lineage did not read back exactly")
            cur.execute("""select id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by
                           from public.commission where deal_id=%s for update""", (deal_id,))
            existing = cur.fetchall()
            expected_without_actor = (commission_id, deal_id, gross, "invoiced", invoiced_on, None, 1)
            if len(existing) > 1:
                raise FixtureRefusal("QA deal has ambiguous existing commission rows")
            if existing:
                row = tuple(existing[0])
                if row[:7] != expected_without_actor or row[7] != actor[0]:
                    raise FixtureRefusal("existing commission differs from the exact unpaid synthetic invoice; no row was changed")
                inserted = 0
            else:
                cur.execute("""insert into public.commission
                    (id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by)
                    values (%s,%s,%s,'invoiced',%s,null,1,%s) on conflict (id) do nothing returning id""",
                    (commission_id, deal_id, gross, invoiced_on, actor[0]))
                inserted = int(cur.fetchone() is not None)
                cur.execute("""select id,deal_id,gross_amount,status,invoiced_on,received_on,version,created_by
                               from public.commission where id=%s""", (commission_id,))
                row = cur.fetchone()
                if not row or tuple(row) != (*expected_without_actor, actor[0]):
                    raise FixtureRefusal("deterministic invoice commission collision or readback mismatch")
            cur.execute("""select deal_id,commission_id,gross_amount,status,commission_invoiced_on,received_on
                           from public.v_invoice_tracker where deal_id=%s and commission_id=%s""",
                        (deal_id, commission_id))
            tracker = cur.fetchone()
            if not tracker or tuple(tracker) != (deal_id, commission_id, gross, "invoiced", invoiced_on, None):
                raise FixtureRefusal("invoice tracker projection did not read back the exact unpaid fixture")
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise


def validate_target(dsn: str, project_id: str, endpoint_id: str, *,
                    environ: Mapping[str, str] = os.environ) -> None:
    parsed = urlsplit(dsn)
    host = (parsed.hostname or "").lower().rstrip(".")
    # libpq permits URI query parameters and PG* environment defaults to
    # select a different socket, address, port, service or database than the
    # endpoint host/path this installer reviewed. Reject routing inputs before
    # provider verification or opening a database connection; SSL and auth
    # parameters remain available.
    routing_options = {"host", "hostaddr", "service", "servicefile", "port", "dbname"}
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    routing_environment = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE")
    if query_keys & routing_options or any(environ.get(name) for name in routing_environment):
        raise FixtureRefusal("owner DSN uses an unsupported libpq target override")
    if project_id in {"", PRODUCTION_PROJECT_ID}:
        raise FixtureRefusal("replacement project identity is absent or forbidden")
    if not endpoint_id.startswith("ep-") or not host.startswith(endpoint_id + ".") \
            or not host.endswith(".neon.tech") or parsed.scheme not in {"postgres", "postgresql"} \
            or not parsed.password or parsed.port not in {None, 5432} or parsed.fragment \
            or unquote(parsed.path.lstrip("/")) != "neondb" \
            or unquote(parsed.username or "") != "neondb_owner":
        raise FixtureRefusal("owner DSN does not match the replacement endpoint")


def validate_provider_binding(dsn: str, project_id: str, endpoint_id: str, *,
                              run: Run = subprocess.run,
                              environ: Mapping[str, str] = os.environ) -> None:
    """Bind caller declarations to the provider before any fixture write."""
    source_env = dict(environ)
    if not source_env.get("NEON_API_KEY"):
        raise FixtureRefusal("NEON_API_KEY is required for provider-bound fixture installation")
    env = {name: source_env[name] for name in
           ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL")
           if source_env.get(name)}
    env["NEON_API_KEY"] = source_env["NEON_API_KEY"]
    env["PATH"] = "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    def provider(args: list[str], label: str) -> Any:
        try:
            result = run(args, capture_output=True, text=True, timeout=120, env=env)
            if result.returncode:
                raise FixtureRefusal(f"provider {label} failed; output suppressed")
            return json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise FixtureRefusal(f"provider {label} is unavailable; output suppressed") from exc
    projects_raw = provider([str(NEONCTL), "projects", "list", "--org-id", NEON_ORG_ID,
                             "--output", "json"], "project readback")
    projects = projects_raw if isinstance(projects_raw, list) else projects_raw.get("projects", []) \
        if isinstance(projects_raw, dict) else []
    matches = [row for row in projects if isinstance(row, dict) and row.get("id") == project_id]
    if len(matches) != 1 or not str(matches[0].get("name") or "").startswith("carr-staging-replacement-"):
        raise FixtureRefusal("provider project is not one exact replacement candidate")
    branches_raw = provider([str(NEONCTL), "branches", "list", "--project-id", project_id,
                             "--output", "json"], "branch readback")
    branches = branches_raw if isinstance(branches_raw, list) else branches_raw.get("branches", []) \
        if isinstance(branches_raw, dict) else []
    branches = [row for row in branches if isinstance(row, dict) and row.get("name") == "main"
                and row.get("default") is True and row.get("project_id") == project_id]
    if len(branches) != 1 or not branches[0].get("id"):
        raise FixtureRefusal("provider replacement branch is not exact")
    branch_id = str(branches[0]["id"])
    endpoints_raw = provider([str(NEONCTL), "api",
        f"/projects/{project_id}/branches/{branch_id}/endpoints", "--output", "json"],
        "endpoint readback")
    endpoints = endpoints_raw if isinstance(endpoints_raw, list) else endpoints_raw.get("endpoints", []) \
        if isinstance(endpoints_raw, dict) else []
    endpoints = [row for row in endpoints if isinstance(row, dict) and row.get("id") == endpoint_id
                 and row.get("branch_id") == branch_id
                 and row.get("type") in {"read_write", "read-write", "rw"}]
    host = (urlsplit(dsn).hostname or "").lower().rstrip(".")
    if len(endpoints) != 1 or str(endpoints[0].get("host") or "").lower().rstrip(".") != host:
        raise FixtureRefusal("provider endpoint does not own the supplied DSN")


def install(conn: Any) -> int:
    with conn.cursor() as cur:
        cur.execute("select session_user,current_user")
        if tuple(cur.fetchone() or ()) != ("neondb_owner", "neondb_owner"):
            raise FixtureRefusal("fixture installation requires exact owner session")
        cur.execute("select id from public.actor where slug='system'")
        actor = cur.fetchone()
        if actor is None:
            raise FixtureRefusal("exact schema seed lacks system actor")
        cur.execute("""insert into public.party
            (id,kind,name,created_by,updated_by,contact_state,notes_path)
            values (%s,'org','Synthetic Staging Fixture',%s,%s,'active',
                    'fixtures/clean-staging-replacement-v1')
            on conflict (id) do nothing""", (FIXTURE_ID, actor[0], actor[0]))
        cur.execute("""select count(*) from public.party where id=%s and kind='org'
            and name='Synthetic Staging Fixture'
            and notes_path='fixtures/clean-staging-replacement-v1'""", (FIXTURE_ID,))
        if tuple(cur.fetchone() or ()) != (1,):
            raise FixtureRefusal("deterministic fixture readback is not exact")
    conn.commit()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--invoice-plan", type=pathlib.Path)
    parser.add_argument("--expected-app-sha")
    parser.add_argument("--expected-carr-sha")
    args = parser.parse_args()
    invoice_args = (args.expected_app_sha, args.expected_carr_sha)
    if bool(args.invoice_plan) != all(invoice_args) or (any(invoice_args) and not args.invoice_plan):
        parser.error("invoice mode requires --invoice-plan and both explicit full source pins")
    if args.invoice_plan:
        try:
            plan = load_private_plan(args.invoice_plan, args.expected_app_sha, args.expected_carr_sha)
            if not args.apply:
                print("staging-fixtures: PLAN invoice_rows=1 mutated=false")
                return 0
            dsn = os.environ.get("DATABASE_URL", "")
            project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
            endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
            validate_target(dsn, project, endpoint)
            validate_provider_binding(dsn, project, endpoint)
            import psycopg
            with psycopg.connect(dsn) as conn:
                count = install_invoice_fixture(conn, plan)
            print(f"staging-fixtures: PASS invoice_rows={count}")
            return 0
        except (FixtureRefusal, ImportError) as exc:
            print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
            return 2
        except Exception:
            print("staging-fixtures: REFUSED — invoice fixture operation failed; details suppressed", file=sys.stderr)
            return 2
    if not args.apply:
        print("staging-fixtures: PLAN synthetic_rows=1 mutated=false")
        return 0
    dsn = os.environ.get("DATABASE_URL", "")
    project = os.environ.get("CARR_STAGING_REPLACEMENT_PROJECT_ID", "")
    endpoint = os.environ.get("CARR_STAGING_REPLACEMENT_ENDPOINT_ID", "")
    try:
        validate_target(dsn, project, endpoint)
        validate_provider_binding(dsn, project, endpoint)
        import psycopg
        with psycopg.connect(dsn) as conn:
            count = install(conn)
        print(f"staging-fixtures: PASS synthetic_rows={count}")
        return 0
    except (FixtureRefusal, ImportError) as exc:
        print(f"staging-fixtures: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
