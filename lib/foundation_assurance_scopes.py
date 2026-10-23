"""Read-only replacement scope and receipt support for foundation assurance evidence."""

from __future__ import annotations
import hashlib
import json
import pathlib
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlsplit


REPO = pathlib.Path(__file__).resolve().parents[1]
NEONCTL = REPO / "mcp-server/node_modules/.bin/neonctl"
RELEASE_MANIFEST = REPO / "tools/release-manifest.py"
ENVIRONMENTS = REPO / "ops/config/environments.json"
CONTRACT_MIGRATION = "0322_clean_staging_replacement_contract.sql"
MIGRATION_FILENAME_RE = re.compile(r"^[0-9]{4}[a-z]?_[a-z0-9_.-]+\.sql$")
MIGRATION_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PRODUCTION_PROJECT_ID = "steep-field-48688294"
NEON_ORG_ID = "org-dry-dew-75906281"
STAGING_NAME = "carr-staging"
PRODUCTION_BRANCH_NAME = "production"
STAGING_BRANCH_NAME = "main"
OWNER_ROLE = "neondb_owner"
READ_FUNCTION = "ops.read_staging_replacement_project_receipt"
Run = Callable[..., subprocess.CompletedProcess]
SAFE_ENV_NAMES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL",
                  "SSL_CERT_FILE", "SSL_CERT_DIR")


class ReplacementRefusal(RuntimeError):
    pass


def safe_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {name: environ[name] for name in SAFE_ENV_NAMES if environ.get(name)}


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
    if spec != CandidateSpec(18, "aws-us-east-1", Decimal("0.25"), Decimal("2")):
        raise ReplacementRefusal("tracked candidate spec is not the reviewed PG18 profile")
    return spec


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


def call_json(cur: Any, function: str, operation_id: uuid.UUID) -> dict[str, Any]:
    if function != READ_FUNCTION:
        raise ReplacementRefusal("only the replacement receipt reader is available")
    cur.execute(f"select {READ_FUNCTION}(%s)", (operation_id,))
    row = cur.fetchone()
    if row is None or not isinstance(row[0], dict):
        raise ReplacementRefusal("SQL function returned invalid JSON")
    return row[0]
