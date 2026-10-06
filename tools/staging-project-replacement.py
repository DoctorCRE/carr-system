#!/usr/bin/env python3
"""Build and attest one clean, isolated staging-replacement project.

``plan`` is the read-only default. ``prepare --apply`` is the only mutating
phase: it may create one deterministic Neon project, reconstruct the exact
merged tree, seed invented fixtures, prove G1 row-ID isolation from Production,
and record the clean-staging contract's immutable receipt. It never renames, updates, or
deletes any project. Existing staging remains untouched as rollback evidence.

Provider output, row IDs, passwords, and DSNs are captured and never printed.
Later staging use remains owned by existing credential and Worker deploy doors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit

psycopg: Any
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.platform_metering import MeteringRefusal, authorize_metered_execution  # noqa:E402
from tools import staging_database_credential as credential_helper  # noqa:E402

NEONCTL = REPO / "mcp-server/node_modules/.bin/neonctl"
RELEASE_MANIFEST = REPO / "tools/release-manifest.py"
SCHEMA = REPO / "db/schema.sql"
ENVIRONMENTS = REPO / "ops/config/environments.json"
METERING_POLICY = REPO / "ops/config/platform-metering.v1.json"
CONTRACT_MIGRATION = "0322_clean_staging_replacement_contract.sql"
MIGRATION_FILENAME_RE = re.compile(r"^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$")
MIGRATION_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PRODUCTION_PROJECT_ID = "steep-field-48688294"
NEON_ORG_ID = "org-dry-dew-75906281"
STAGING_NAME = "carr-staging"
PRODUCTION_BRANCH_NAME = "production"
STAGING_BRANCH_NAME = "main"
OWNER_ROLE = "neondb_owner"
CLIENT_TABLES = ("party", "client", "deal", "lead", "vendor")
PREPARE_FUNCTION = "ops.prepare_staging_replacement_project"
RECORD_FUNCTION = "ops.record_staging_replacement_project"
READ_FUNCTION = "ops.read_staging_replacement_project_receipt"
Run = Callable[..., subprocess.CompletedProcess]
SAFE_ENV_NAMES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL",
                  "SSL_CERT_FILE", "SSL_CERT_DIR")


class ReplacementRefusal(RuntimeError):
    pass


def safe_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {name: environ[name] for name in SAFE_ENV_NAMES if environ.get(name)}


@dataclass(frozen=True)
class SourceContract:
    git_sha: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class CandidateSpec:
    postgres_version: int
    region_id: str
    compute_min_cu: Decimal
    compute_max_cu: Decimal


@dataclass(frozen=True)
class ProviderScope:
    project_id: str
    project_name: str
    branch_id: str
    endpoint_id: str
    endpoint_host: str
    postgres_version: int | None = None
    region_id: str | None = None
    compute_min_cu: Decimal | None = None
    compute_max_cu: Decimal | None = None


@dataclass(frozen=True)
class SecretDsn:
    scope: ProviderScope
    value: str = field(repr=False, compare=False)


def _run(args: Sequence[str], *, run: Run = subprocess.run,
         env: Mapping[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return run(list(args), capture_output=True, text=True, timeout=timeout,
                   env=dict(env) if env is not None else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReplacementRefusal("dependency did not complete; output suppressed") from exc


def _success(result: subprocess.CompletedProcess, label: str) -> str:
    if result.returncode:
        raise ReplacementRefusal(f"{label} failed (rc={result.returncode}); output suppressed")
    return str(result.stdout or "")


def _json(result: subprocess.CompletedProcess, label: str) -> Any:
    try:
        return json.loads(_success(result, label))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReplacementRefusal(f"{label} returned invalid JSON; output suppressed") from exc


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ReplacementRefusal(f"provider {key} response has the wrong shape")
    return rows


def _decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReplacementRefusal(f"provider {label} is not numeric") from exc


def provider_environment(environ: Mapping[str, str]) -> dict[str, str]:
    env = safe_environment(environ)
    if environ.get("NEON_API_KEY"):
        env["NEON_API_KEY"] = environ["NEON_API_KEY"]
    env["PATH"] = "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + environ.get("PATH", "")
    if not env.get("NEON_API_KEY"):
        try:
            lines = (pathlib.Path.home() / ".config/carr/db.env").read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            if line.startswith("NEON_API_KEY="):
                env["NEON_API_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not env.get("NEON_API_KEY"):
        raise ReplacementRefusal("NEON_API_KEY is required")
    return env


def candidate_name(operation_id: uuid.UUID) -> str:
    return f"carr-staging-replacement-{operation_id.hex}"


def validate_migration_ledger(manifest: Mapping[str, Any]) -> str:
    """Return the exact highest migration after binding the complete ledger."""
    ledger = manifest.get("migration_ledger")
    if not isinstance(ledger, dict) or not ledger or CONTRACT_MIGRATION not in ledger:
        raise ReplacementRefusal("source contract lacks the replacement contract migration")
    rows = list(ledger.items())
    if rows != sorted(rows) or any(not isinstance(filename, str)
            or not MIGRATION_FILENAME_RE.fullmatch(filename)
            or not isinstance(file_sha256, str)
            or not MIGRATION_SHA256_RE.fullmatch(file_sha256)
            for filename, file_sha256 in rows):
        raise ReplacementRefusal("source contract migration ledger entries are not canonical")
    material = "".join(f"{filename}\0{file_sha256}\n" for filename, file_sha256 in rows)
    digest = "sha256:" + hashlib.sha256(material.encode()).hexdigest()
    if manifest.get("migration_count") != len(rows) \
            or manifest.get("migration_highest") != rows[-1][0] \
            or manifest.get("migration_ledger_sha256") != digest:
        raise ReplacementRefusal("source contract migration ledger boundary is not exact")
    return rows[-1][0]


def load_candidate_spec() -> CandidateSpec:
    try:
        root = json.loads(ENVIRONMENTS.read_text())
        rows = [row for row in root["environments"] if row.get("name") == "staging"]
        raw = rows[0]["database"]["replacement_candidate_spec"] if len(rows) == 1 else None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReplacementRefusal("tracked candidate spec is unreadable") from exc
    if not isinstance(raw, dict) or set(raw) != {"postgres_version", "region_id", "compute_min_cu", "compute_max_cu"}:
        raise ReplacementRefusal("tracked candidate spec keys are not exact")
    spec = CandidateSpec(int(raw["postgres_version"]), str(raw["region_id"]),
                         _decimal(raw["compute_min_cu"], "compute minimum"),
                         _decimal(raw["compute_max_cu"], "compute maximum"))
    if spec != CandidateSpec(18, "aws-us-east-1", Decimal("0.25"), Decimal("8")):
        raise ReplacementRefusal("tracked candidate spec is not the reviewed PG18 profile")
    return spec


def validate_source(sha: str, *, run: Run = subprocess.run) -> SourceContract:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReplacementRefusal("--sha must be a full lowercase SHA")
    def git(*args: str, accept: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        result = _run(["git", "-C", str(REPO), *args], run=run)
        if result.returncode not in accept:
            raise ReplacementRefusal("git source check failed; output suppressed")
        return result
    if _success(git("rev-parse", "HEAD"), "HEAD read").strip() != sha:
        raise ReplacementRefusal("checkout HEAD is not the requested SHA")
    if git("symbolic-ref", "-q", "HEAD", accept=(0, 1)).returncode == 0:
        raise ReplacementRefusal("source checkout must be detached")
    if _success(git("status", "--porcelain=v1", "--untracked-files=all"), "cleanliness").strip():
        raise ReplacementRefusal("source checkout is not clean")
    if git("merge-base", "--is-ancestor", sha, "origin/main", accept=(0, 1)).returncode:
        raise ReplacementRefusal("SHA is not reachable from origin/main")
    manifest = _json(_run([sys.executable, str(RELEASE_MANIFEST), "source-contract", "--sha", sha],
                          run=run), "source contract")
    exact = {"git_sha": sha, "tree_mode": "full", "tree_tuple": ["mode", "type", "object", "path"]}
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in exact.items()):
        raise ReplacementRefusal("source contract identity is not exact")
    validate_migration_ledger(manifest)
    for key in ("source_tree_sha256", "artifact_sha256", "config_sha256", "dependency_sha256",
                "migration_ledger_sha256"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get(key) or "")):
            raise ReplacementRefusal(f"source contract {key} is invalid")
    if manifest["source_tree_sha256"] == manifest["artifact_sha256"]:
        raise ReplacementRefusal("full source tree and deployed artifact digests are overloaded")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("source_tree_oid") or "")) \
            or not isinstance(manifest.get("source_tree_entry_count"), int) \
            or manifest["source_tree_entry_count"] <= 0:
        raise ReplacementRefusal("source tree identity is invalid")
    return SourceContract(sha, manifest)


def list_projects(*, run: Run, environ: Mapping[str, str]) -> list[dict[str, Any]]:
    return _rows(_json(_run([str(NEONCTL), "projects", "list", "--org-id", NEON_ORG_ID,
                             "--output", "json"], run=run, env=provider_environment(environ)),
                       "project list"), "projects")


def resolve_scope(project: Mapping[str, Any], *, expected_branch_name: str,
                  run: Run, environ: Mapping[str, str]) -> ProviderScope:
    project_id, name = str(project.get("id") or ""), str(project.get("name") or "")
    env = provider_environment(environ)
    branches = _rows(_json(_run([str(NEONCTL), "branches", "list", "--project-id", project_id,
                                 "--output", "json"], run=run, env=env), "branch list"), "branches")
    mains = [r for r in branches if r.get("name") == expected_branch_name
             and r.get("default") is True
             and str(r.get("project_id") or "") == project_id]
    if not project_id or not name or len(mains) != 1:
        raise ReplacementRefusal("provider project/default branch is not exact")
    branch_id = str(mains[0].get("id") or "")
    endpoints = _rows(_json(_run([str(NEONCTL), "api",
        f"/projects/{project_id}/branches/{branch_id}/endpoints", "--output", "json"],
        run=run, env=env), "endpoint list"), "endpoints")
    endpoints = [r for r in endpoints if str(r.get("branch_id") or "") == branch_id
                 and r.get("type") in {"read_write", "read-write", "rw"}]
    if len(endpoints) != 1:
        raise ReplacementRefusal("read-write endpoint is not exact")
    ep = endpoints[0]
    eid, host = str(ep.get("id") or ""), str(ep.get("host") or "").lower().rstrip(".")
    if not eid.startswith("ep-") or not host.startswith(eid + ".") or not host.endswith(".neon.tech"):
        raise ReplacementRefusal("endpoint identity is invalid")
    return ProviderScope(project_id, name, branch_id, eid, host,
        int(project["pg_version"]) if project.get("pg_version") is not None else None,
        str(project["region_id"]) if project.get("region_id") is not None else None,
        _decimal(ep["autoscaling_limit_min_cu"], "compute minimum") if "autoscaling_limit_min_cu" in ep else None,
        _decimal(ep["autoscaling_limit_max_cu"], "compute maximum") if "autoscaling_limit_max_cu" in ep else None)


def validate_candidate_spec(scope: ProviderScope, spec: CandidateSpec) -> None:
    if (scope.postgres_version, scope.region_id, scope.compute_min_cu, scope.compute_max_cu) != \
            (spec.postgres_version, spec.region_id, spec.compute_min_cu, spec.compute_max_cu):
        raise ReplacementRefusal("candidate provider readback disagrees with tracked spec")


def resolve_existing_scopes(operation_id: uuid.UUID, *, run: Run,
                            environ: Mapping[str, str]) -> tuple[ProviderScope, ProviderScope, ProviderScope | None]:
    projects = list_projects(run=run, environ=environ)  # exactly one metadata fetch
    def one(key: str, value: str) -> dict[str, Any]:
        rows = [r for r in projects if str(r.get(key) or "") == value]
        if len(rows) != 1:
            raise ReplacementRefusal("provider project identity did not resolve exactly once")
        return rows[0]
    production = resolve_scope(one("id", PRODUCTION_PROJECT_ID),
                               expected_branch_name=PRODUCTION_BRANCH_NAME,
                               run=run, environ=environ)
    old = resolve_scope(one("name", STAGING_NAME), expected_branch_name=STAGING_BRANCH_NAME,
                        run=run, environ=environ)
    found = [r for r in projects if r.get("name") == candidate_name(operation_id)]
    if len(found) > 1:
        raise ReplacementRefusal("candidate name is not unique")
    candidate = resolve_scope(found[0], expected_branch_name=STAGING_BRANCH_NAME,
                              run=run, environ=environ) if found else None
    scopes = [production, old] + ([candidate] if candidate else [])
    for attr in ("project_id", "branch_id", "endpoint_id", "endpoint_host"):
        values = [getattr(s, attr) for s in scopes]
        if len(values) != len(set(values)):
            raise ReplacementRefusal(f"provider {attr} overlaps environments")
    if candidate:
        validate_candidate_spec(candidate, load_candidate_spec())
    return production, old, candidate


def prove_provider_preservation(operation_id: uuid.UUID, expected_production: ProviderScope,
                                expected_old: ProviderScope, expected_candidate: ProviderScope, *,
                                run: Run, environ: Mapping[str, str]) -> None:
    production, old, candidate = resolve_existing_scopes(
        operation_id, run=run, environ=environ)
    if production != expected_production or old != expected_old or candidate != expected_candidate:
        raise ReplacementRefusal("final provider readback changed Production, old staging, or candidate identity")


def create_candidate(operation_id: uuid.UUID, source: SourceContract, spec: CandidateSpec, *,
                     local_checks_green: bool, run: Run, environ: Mapping[str, str]) -> None:
    try:
        policy = json.loads(METERING_POLICY.read_text())
        authorize_metered_execution(policy, "neon-standing-project-create", {
            "local_checks_green": local_checks_green, "candidate_sha": source.git_sha,
            "operation_id": str(operation_id), "standing_project_create_count": 1})
    except (OSError, ValueError, TypeError, MeteringRefusal) as exc:
        raise ReplacementRefusal(f"project-create metering refused: {exc}") from exc
    cu = f"{spec.compute_min_cu}-{spec.compute_max_cu}"
    payload = _json(_run([str(NEONCTL), "projects", "create", "--name", candidate_name(operation_id),
        "--org-id", NEON_ORG_ID, "--database", "neondb", "--role", OWNER_ROLE,
        "--pg-version", str(spec.postgres_version), "--region-id", spec.region_id, "--cu", cu,
        "--set-context=false", "--output", "json"], run=run,
        env=provider_environment(environ), timeout=180), "project create")
    if not isinstance(payload, dict):
        raise ReplacementRefusal("project create response shape is invalid")


def derive_dsn(scope: ProviderScope, role: str, *, run: Run,
               environ: Mapping[str, str]) -> SecretDsn:
    raw = _success(_run([str(NEONCTL), "connection-string", scope.branch_id,
        "--project-id", scope.project_id, "--role-name", role, "--database-name", "neondb",
        "--endpoint-type", "read_write"], run=run, env=provider_environment(environ)),
        "DSN derivation").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ReplacementRefusal("DSN is invalid; value suppressed") from exc
    if not raw or "\n" in raw or "\r" in raw or parsed.scheme not in {"postgres", "postgresql"} \
            or not parsed.password or parsed.fragment or unquote(parsed.username or "") != role \
            or (parsed.hostname or "").lower().rstrip(".") != scope.endpoint_host \
            or parsed.port not in {None, 5432} or unquote(parsed.path.lstrip("/")) != "neondb":
        raise ReplacementRefusal("DSN escapes pinned scope; value suppressed")
    return SecretDsn(scope, raw)


def reconstruct_candidate(owner: SecretDsn, *, run: Run, environ: Mapping[str, str],
                          psql_bin: str = "psql") -> None:
    parsed = urlsplit(owner.value)
    env = safe_environment(environ)
    env.update({"PGHOST": owner.scope.endpoint_host, "PGPORT": "5432", "PGDATABASE": "neondb",
                "PGUSER": OWNER_ROLE, "PGPASSWORD": unquote(parsed.password or ""),
                "PGSSLMODE": "require", "PGCHANNELBINDING": "require"})
    _success(_run([psql_bin, "--single-transaction", "-v", "ON_ERROR_STOP=1", "-q",
                   "-f", str(SCHEMA)],
                  run=run, env=env, timeout=1800), "schema reconstruction")


def apply_candidate_migrations(owner: SecretDsn, source: SourceContract, *, run: Run,
                               environ: Mapping[str, str]) -> None:
    child = safe_environment(environ); child["DATABASE_URL"] = owner.value
    highest = validate_migration_ledger(source.manifest)
    _success(_run([str(REPO / ".venv/bin/python"), str(REPO / "tools/migrate.py"), "--apply", "--yes",
                   "--through", highest],
                  run=run, env=child, timeout=3600), "migration reconstruction")


def install_fixtures(owner: SecretDsn, *, run: Run, environ: Mapping[str, str]) -> None:
    fixture = provider_environment(environ)
    fixture["DATABASE_URL"] = owner.value
    fixture["CARR_STAGING_REPLACEMENT_PROJECT_ID"] = owner.scope.project_id
    fixture["CARR_STAGING_REPLACEMENT_ENDPOINT_ID"] = owner.scope.endpoint_id
    _success(_run([str(REPO / ".venv/bin/python"), str(REPO / "tools/staging-fixtures.py"), "--apply"],
                  run=run, env=fixture, timeout=300), "staging fixtures")


def sync_control_plane(owner: SecretDsn, *, run: Run,
                       environ: Mapping[str, str]) -> None:
    bootstrap_env = safe_environment(environ)
    bootstrap_env["DATABASE_URL"] = owner.value
    _success(_run([str(REPO / ".venv/bin/python"), str(REPO / "tools/control-plane.py"), "sync"],
                  run=run, env=bootstrap_env, timeout=900), "control-plane sync")
    readback_env = safe_environment(environ)
    readback_env["DATABASE_URL"] = owner.value
    _success(_run([str(REPO / ".venv/bin/python"), str(REPO / "ops/control-plane-registry-gate.py")],
                  run=run, env=readback_env, timeout=900), "control-plane readback")


def candidate_reconstruction_state(owner: SecretDsn, source: SourceContract, *, connect: Any) -> str:
    """Prove endpoint reachability and refuse every ambiguous partial tree."""
    with connect(owner.value) as conn, conn.cursor() as cur:
        cur.execute("select session_user,current_database(),current_setting('server_version_num')::int")
        identity = tuple(cur.fetchone() or ())
        if len(identity) != 3 or identity[0] != OWNER_ROLE or identity[1] != "neondb" \
                or not 180000 <= int(identity[2]) < 190000:
            raise ReplacementRefusal("candidate reachability/PG18 identity proof failed")
        cur.execute("""select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where c.relkind in ('r','p') and n.nspname in ('public','ops')""")
        count = int(cur.fetchone()[0])
        if count == 0:
            return "empty"
        cur.execute("select to_regclass('public.schema_migrations')")
        row = cur.fetchone()
        if row is None or row[0] is None:
            raise ReplacementRefusal("partial candidate has no migration ledger")
        live = exact_ledger(cur)
        source_rows = list(source.manifest["migration_ledger"].items())
        live_rows = list(live["migration_ledger"].items())
        if live_rows != source_rows[:len(live_rows)]:
            raise ReplacementRefusal("partial candidate ledger is not an exact source prefix")
        return "reconstructed" if live_rows == source_rows else "prefix"
    raise ReplacementRefusal("candidate is neither empty nor an exact resumable reconstruction")


def provision_scoped_credentials(owner: SecretDsn, operation_id: uuid.UUID, *, connect: Any) -> tuple[str, str]:
    """Create only the two least-privilege receipt identities in a private root."""
    if psycopg is None:
        raise ReplacementRefusal("psycopg is required for scoped credentials")
    from psycopg import sql
    root = pathlib.Path.home() / ".config/carr/staging-replacements" / str(operation_id)
    profiles = (("CARR_DB_JOBS_URL", "carr_jobs", None, root / "jobs.env"),
                ("CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL",
                 "carr_program5_forward_fix_verifier",
                 "carr_program5_forward_fix_verifiers", root / "verifier.env"))
    values = []
    with credential_helper.exclusive_lock(root / ".credential.lock"):
        for key, role, bundle, path in profiles:
            paths = credential_helper.CredentialPaths(
                final=path, pending=pathlib.Path(str(path) + ".pending"))
            stored = credential_helper.prepare_pending(
                paths, key=key, role_name=role, owner_uri=owner.value,
                expected_endpoint=owner.scope.endpoint_host, expected_port=5432,
                expected_database="neondb")
            with connect(owner.value, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute("select 1 from pg_roles where rolname=%s", (role,))
                exists = cur.fetchone() is not None
                if role == "carr_jobs" and not exists:
                    raise ReplacementRefusal("exact schema lacks carr_jobs")
                statement = "alter role {} login password {}" if exists else "create role {} login password {}"
                cur.execute(sql.SQL(statement).format(sql.Identifier(role), sql.Literal(stored.password)))
                if bundle:
                    cur.execute(sql.SQL("grant {} to {}").format(sql.Identifier(bundle), sql.Identifier(role)))
            value = stored.value
            parsed = urlsplit(value)
            if unquote(parsed.username or "") != role or (parsed.hostname or "").lower() != owner.scope.endpoint_host:
                raise ReplacementRefusal("scoped credential escapes candidate endpoint")
            with connect(value) as conn, conn.cursor() as cur:
                cur.execute("select session_user,current_user,pg_has_role(session_user,%s,'member')",
                            (bundle or role,))
                row = tuple(cur.fetchone() or ())
                if row[:2] != (role, role) or (bundle is not None and row[2] is not True):
                    raise ReplacementRefusal("scoped credential identity proof failed")
            if stored.state == "pending":
                credential_helper.promote_pending(paths, key=key, expected_value=value)
            values.append(value)
    return values[0], values[1]


def exact_ledger(cur: Any) -> dict[str, Any]:
    cur.execute("select filename,sha256 from public.schema_migrations order by filename collate \"C\"")
    rows = [(str(a), str(b)) for a, b in cur.fetchall()]
    material = "".join(f"{a}\0{b}\n" for a, b in rows)
    return {"migration_ledger": dict(rows), "migration_count": len(rows),
            "migration_highest": rows[-1][0] if rows else None,
            "migration_ledger_sha256": "sha256:" + hashlib.sha256(material.encode()).hexdigest()}


def client_row_ids(conn: Any) -> dict[str, tuple[str, ...]]:
    result = {}
    with conn.cursor() as cur:
        for table in CLIENT_TABLES:
            cur.execute("select to_regclass(%s)", (f"public.{table}",))
            row = cur.fetchone()
            if row is None or row[0] is None:
                raise ReplacementRefusal(f"candidate lacks {table}")
            cur.execute(f"select id::text from public.{table} order by id")
            result[table] = tuple(str(r[0]) for r in cur.fetchall())
    return result


def production_overlap_count(ids: Mapping[str, Sequence[str]], conn: Any) -> int:
    total = 0
    try:
        conn.read_only = True
    except Exception as exc:
        raise ReplacementRefusal("Production connection cannot be forced read-only") from exc
    with conn.cursor() as cur:
        cur.execute("show transaction_read_only")
        if tuple(cur.fetchone() or ()) != ("on",):
            raise ReplacementRefusal("Production connection is not read-only")
        for table in CLIENT_TABLES:
            values = tuple(ids.get(table, ()))
            if values:
                cur.execute(f"select count(*) from public.{table} where id=any(%s::uuid[])", (list(values),))
                total += int(cur.fetchone()[0])
    return total


def read_production_overlap(production: ProviderScope,
                            ids: Mapping[str, Sequence[str]], *, run: Run,
                            environ: Mapping[str, str], connect: Any) -> int:
    """Read Production through the sanctioned owner tap under a forced RO transaction."""
    production_read = derive_dsn(production, OWNER_ROLE, run=run, environ=environ)
    with connect(production_read.value) as conn:
        return production_overlap_count(ids, conn)


PREPARE_KEYS = frozenset({"schema_version", "tree_mode", "git_sha", "source_tree_oid",
    "source_tree_sha256", "source_tree_entry_count", "artifact_sha256", "config_sha256",
    "dependency_sha256", "migration_ledger", "migration_count", "migration_highest",
    "migration_ledger_sha256", "prior_staging_project_id", "replacement_project_id",
    "replacement_branch_id", "replacement_endpoint_id", "expected_synthetic_data_count",
    "expected_production_overlap_count"})
OBSERVATION_KEYS = frozenset({"schema_version", "git_sha", "source_tree_oid", "source_tree_sha256",
    "source_tree_entry_count", "artifact_sha256", "config_sha256", "dependency_sha256",
    "prior_staging_project_id", "replacement_project_id", "replacement_branch_id",
    "replacement_endpoint_id", "synthetic_data_count", "production_overlap_count"})


def prepare_payload(source: SourceContract, old: ProviderScope, candidate: ProviderScope,
                    synthetic_count: int) -> dict[str, Any]:
    m = source.manifest
    highest = validate_migration_ledger(m)
    payload = {"schema_version": "clean-staging-replacement-contract.v1", "tree_mode": "full",
        "git_sha": source.git_sha, "source_tree_oid": m["source_tree_oid"],
        "source_tree_sha256": m["source_tree_sha256"], "source_tree_entry_count": m["source_tree_entry_count"],
        "artifact_sha256": m["artifact_sha256"], "config_sha256": m["config_sha256"],
        "dependency_sha256": m["dependency_sha256"], "migration_ledger": m["migration_ledger"],
        "migration_count": m["migration_count"], "migration_highest": m["migration_highest"],
        "migration_ledger_sha256": m["migration_ledger_sha256"],
        "prior_staging_project_id": old.project_id, "replacement_project_id": candidate.project_id,
        "replacement_branch_id": candidate.branch_id, "replacement_endpoint_id": candidate.endpoint_id,
        "expected_synthetic_data_count": synthetic_count, "expected_production_overlap_count": 0}
    if set(payload) != PREPARE_KEYS or synthetic_count <= 0 \
            or m["migration_highest"] != highest:
        raise ReplacementRefusal("prepare payload is not exact")
    return payload


def observation_payload(prepared: Mapping[str, Any], synthetic_count: int, overlap: int) -> dict[str, Any]:
    result = {k: prepared[k] for k in OBSERVATION_KEYS - {"schema_version", "synthetic_data_count",
                                                          "production_overlap_count"}}
    result.update({"schema_version": "clean-staging-replacement-observation.v1",
                   "synthetic_data_count": synthetic_count, "production_overlap_count": overlap})
    if set(result) != OBSERVATION_KEYS or synthetic_count <= 0 or overlap != 0:
        raise ReplacementRefusal("observation payload is not exact")
    return result


def call_json(cur: Any, function: str, operation_id: uuid.UUID,
              payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if function not in {PREPARE_FUNCTION, RECORD_FUNCTION, READ_FUNCTION}:
        raise ReplacementRefusal("unknown SQL function")
    if payload is None:
        cur.execute(f"select {function}(%s)", (operation_id,))
    else:
        cur.execute(f"select {function}(%s,%s::jsonb)",
                    (operation_id, json.dumps(dict(payload), sort_keys=True)))
    row = cur.fetchone()
    if row is None or not isinstance(row[0], dict):
        raise ReplacementRefusal("SQL function returned invalid JSON")
    return row[0]


def validate_receipt_readback(readback: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if any(readback.get(key) != value for key, value in expected.items()):
        raise ReplacementRefusal("immutable receipt readback disagrees")
    observed_at = readback.get("observed_at")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(readback.get("receipt_sha256") or "")) \
            or not isinstance(observed_at, str) or not observed_at.strip():
        raise ReplacementRefusal("immutable receipt digest or observation time is invalid")


def prepare_candidate(source: SourceContract, operation_id: uuid.UUID, production: ProviderScope,
                      old: ProviderScope, candidate: ProviderScope, owner: SecretDsn,
                      jobs_dsn: str, verifier_dsn: str, *, connect: Any,
                      run: Run, environ: Mapping[str, str]) -> dict[str, Any]:
    with connect(owner.value) as conn:
        live = exact_ledger(conn.cursor())
        for key in ("migration_ledger", "migration_count", "migration_highest", "migration_ledger_sha256"):
            if live[key] != source.manifest[key]:
                raise ReplacementRefusal(f"final live ledger disagrees on {key}")
        ids = client_row_ids(conn)
    synthetic = sum(len(rows) for rows in ids.values())
    prepared = prepare_payload(source, old, candidate, synthetic)
    overlap = read_production_overlap(production, ids, run=run,
                                      environ=environ, connect=connect)
    observation = observation_payload(prepared, synthetic, overlap)
    with connect(jobs_dsn) as conn, conn.cursor() as cur:
        stated = call_json(cur, PREPARE_FUNCTION, operation_id, prepared); conn.commit()
    contract_id = stated.get("contract_id")
    if not contract_id or stated.get("state") not in {"prepared", "observed"}:
        raise ReplacementRefusal("prepare receipt state is invalid")
    with connect(verifier_dsn) as conn, conn.cursor() as cur:
        recorded = call_json(cur, RECORD_FUNCTION, operation_id, observation); conn.commit()
        if recorded.get("state") != "observed" or recorded.get("contract_id") != contract_id \
                or not re.fullmatch(r"ops\.staging-replacement-project:sha256:[0-9a-f]{64}",
                                    str(recorded.get("evidence_ref") or "")):
            raise ReplacementRefusal("recorded receipt state is invalid")
        receipt_id = uuid.UUID(str(recorded.get("receipt_id")))
        readback = call_json(cur, READ_FUNCTION, receipt_id)
    expected = {"contract_id": contract_id, "receipt_id": recorded["receipt_id"],
        "evidence_ref": recorded["evidence_ref"], "git_sha": source.git_sha,
        "source_tree_oid": source.manifest["source_tree_oid"],
        "source_tree_sha256": source.manifest["source_tree_sha256"],
        "source_tree_entry_count": source.manifest["source_tree_entry_count"],
        "artifact_sha256": source.manifest["artifact_sha256"], "config_sha256": source.manifest["config_sha256"],
        "dependency_sha256": source.manifest["dependency_sha256"],
        "prior_staging_project_id": old.project_id,
        "replacement_project_id": candidate.project_id, "replacement_branch_id": candidate.branch_id,
        "replacement_endpoint_id": candidate.endpoint_id, "live_migration_ledger": source.manifest["migration_ledger"],
        "live_migration_count": source.manifest["migration_count"],
        "live_migration_highest": source.manifest["migration_highest"],
        "live_migration_ledger_sha256": source.manifest["migration_ledger_sha256"],
        "synthetic_data_count": synthetic, "production_overlap_count": 0}
    validate_receipt_readback(readback, expected)
    return {"receipt_id": str(receipt_id), "evidence_ref": recorded.get("evidence_ref")}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", nargs="?", choices=("plan", "prepare"), default="plan")
    parser.add_argument("--sha", required=True); parser.add_argument("--operation-id", required=True, type=uuid.UUID)
    parser.add_argument("--candidate-operation-id", type=uuid.UUID)
    parser.add_argument("--apply", action="store_true"); parser.add_argument("--local-checks-green", action="store_true")
    args = parser.parse_args(argv)
    if args.phase == "plan" and (args.apply or args.local_checks_green):
        parser.error("plan is read-only")
    if args.phase == "prepare" and not (args.apply and args.local_checks_green):
        parser.error("prepare requires --apply --local-checks-green")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        candidate_operation_id = args.candidate_operation_id or args.operation_id
        source, spec = validate_source(args.sha), load_candidate_spec()
        production, old, candidate = resolve_existing_scopes(
            candidate_operation_id, run=subprocess.run, environ=os.environ)
        if candidate is None and candidate_operation_id != args.operation_id:
            raise ReplacementRefusal("successor receipt candidate does not exist")
        if args.phase == "plan":
            print(json.dumps({"ok": True, "phase": "plan", "mutated": False,
                "operation_id": str(args.operation_id),
                "receipt_operation_id": str(args.operation_id),
                "candidate_operation_id": str(candidate_operation_id),
                "candidate_name": candidate_name(candidate_operation_id),
                "candidate_exists": candidate is not None, "standing_project_create_count": 0 if candidate else 1,
                "old_staging_untouched": True, "next_phase": "prepare --apply --local-checks-green",
                "later_doors": ["bin/staging-secrets.sh", "bin/deploy-worker.sh staging"]}, sort_keys=True))
            return 0
        if candidate is None:
            create_candidate(candidate_operation_id, source, spec, local_checks_green=True,
                             run=subprocess.run, environ=os.environ)
            production, old, candidate = resolve_existing_scopes(candidate_operation_id,
                                                                  run=subprocess.run, environ=os.environ)
        if candidate is None:
            raise ReplacementRefusal("candidate creation had no exact readback")
        validate_candidate_spec(candidate, spec)
        owner = derive_dsn(candidate, OWNER_ROLE, run=subprocess.run, environ=os.environ)
        if psycopg is None:
            raise ReplacementRefusal("psycopg is required")
        state = candidate_reconstruction_state(owner, source, connect=psycopg.connect)
        if state == "empty":
            reconstruct_candidate(owner, run=subprocess.run, environ=os.environ)
            apply_candidate_migrations(owner, source, run=subprocess.run, environ=os.environ)
        elif state == "prefix":
            apply_candidate_migrations(owner, source, run=subprocess.run, environ=os.environ)
        install_fixtures(owner, run=subprocess.run, environ=os.environ)
        jobs_dsn, verifier_dsn = provision_scoped_credentials(
            owner, candidate_operation_id, connect=psycopg.connect)
        sync_control_plane(owner, run=subprocess.run, environ=os.environ)
        receipt = prepare_candidate(source, args.operation_id, production, old, candidate, owner,
                                    jobs_dsn, verifier_dsn, connect=psycopg.connect,
                                    run=subprocess.run, environ=os.environ)
        prove_provider_preservation(candidate_operation_id, production, old, candidate,
                                    run=subprocess.run, environ=os.environ)
        print(json.dumps({"ok": True, "phase": "prepare", "mutated": True,
                          "receipt_operation_id": str(args.operation_id),
                          "candidate_operation_id": str(candidate_operation_id),
                          "old_staging_untouched": True, **receipt}, sort_keys=True))
        return 0
    except credential_helper.CredentialRefusal:
        print("staging-project-replacement: REFUSED — credential operation failed; detail suppressed",
              file=sys.stderr)
        return 2
    except (ReplacementRefusal, OSError, IndexError) as exc:
        print(f"staging-project-replacement: REFUSED — {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("staging-project-replacement: REFUSED — operation failed; detail suppressed",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
