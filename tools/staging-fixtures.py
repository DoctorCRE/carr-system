#!/usr/bin/env python3
"""Install the minimal deterministic, invented staging G1 fixture.

Default mode is a read-only plan. ``--apply`` requires an explicit owner DSN
and replacement provider identities. No source row or identifier is printed.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import uuid
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit

FIXTURE_ID = uuid.uuid5(uuid.UUID("80d943c3-53af-4a13-9200-f3d8581a459a"),
                        "carr-clean-staging-replacement-party-v1")
PRODUCTION_PROJECT_ID = "steep-field-48688294"
REPO = pathlib.Path(__file__).resolve().parents[1]
NEONCTL = REPO / "mcp-server/node_modules/.bin/neonctl"
NEON_ORG_ID = "org-dry-dew-75906281"
Run = Callable[..., subprocess.CompletedProcess]


class FixtureRefusal(RuntimeError):
    pass


def validate_target(dsn: str, project_id: str, endpoint_id: str) -> None:
    parsed = urlsplit(dsn)
    host = (parsed.hostname or "").lower().rstrip(".")
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
    args = parser.parse_args()
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
