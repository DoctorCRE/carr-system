#!/usr/bin/env python3
"""Cut the staging Worker over to a receipted replacement candidate.

Read-only planning is the default. ``--apply`` is the only mutating path. The
caller must supply the full candidate-operation UUID, immutable receipt UUID,
and merged source SHA; provider scopes, roles, DSNs, grants, and Worker names
remain server- and repository-derived authority rather than caller choices.

STAGING ONLY, AND THAT IS THE DESIGN RATHER THAN A GAP. Every scope this tool
resolves is refused if it lands on the Production project id. Migration 0502
creates the Gate Zero producer seat WITHOUT a password, and this tool ADOPTS
that state for STAGING as its third login profile. The production credential
for ``carr_gate_zero_producer`` and the production Worker secret
DATABASE_URL_GATE_ZERO_WRITER are Joe's own act at a console he controls,
through the equivalent production path already written down: Neon Database SOP
section `01-connections-and-roles` for the role's password, then Cloudflare Edge
SOP section `02-secrets-and-tokens` for the per-secret procedure that sets it on
the production Worker and verifies it from the consumer. No session, subagent or
scheduled lane holds the Cloudflare token or the Production owner credential,
and nothing in this branch provisions Production.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

try:
    import psycopg
    from psycopg import sql
except ImportError:
    sys.exit("staging app_writer provisioner requires the repo virtualenv (psycopg)")


REPO = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = REPO / "db/schema.sql"
MIGRATIONS = REPO / "migrations"
STAGING_PROJECT_NAME = "carr-staging"
STAGING_BRANCH_NAME = "main"
APP_ROLE = "app_writer"
BUNDLE_ROLE = "carr_writer"
READER_ROLE = "app_reader"
READER_BUNDLE_ROLE = "carr_reader"
# The Gate Zero producer seat. Standing-rule amendment 9 (2026-09-14): seat-only
# write is enforced by CONNECTION ROLE, so the one verb that records a Gate Zero
# read-only outcome runs on its own login role rather than on app_writer.
# migrations/0502_gate_zero_read_only_outcome.sql creates the NOLOGIN bundle,
# grants it the sole EXECUTE on ops.gate_zero_record_read_only_outcome, and
# derives the producing seat from session_user. The LOGIN role and its secret are
# provisioned HERE, out of band, because a rebuilt schema must never mint a
# credential -- the same split 0315 uses for the forward-fix verifier.
# It is a LOGIN role with NO bundle, which is the one departure from the pair
# above and is measured rather than preferred: a NOLOGIN `carr_*` bundle enters
# the SCAC sealed role_authority projection, and PostgreSQL roles are cluster-
# wide while db/schema.sql is a database artifact, so a new bundle invalidates
# the snapshot seal for every other database in the same cluster until the
# snapshot is regenerated. The migration's header carries the measurement.
GATE_ZERO_PRODUCER_ROLE = "carr_gate_zero_producer"
FOUNDATION_ASSURANCE_ORACLE_ROLE = "carr_foundation_assurance_oracle"
LOCK_KEY = 7301961134306001
BOOTSTRAP_SUPERUSER_OID = 10
WRANGLER = REPO / "mcp-server/node_modules/.bin/wrangler"
WRANGLER_CONFIG = REPO / "mcp-server/wrangler.toml"

FORBIDDEN_ENV = (
    "CARR_BREAK_GLASS",
    "DATABASE_URL",
    "CARR_DB_OWNER_URL",
    "CARR_DB_WRITER_URL",
    "CARR_DB_READER_URL",
    "CARR_DB_JOBS_URL",
    "CARR_DB_AUTHORITY_URL",
    "CARR_DB_STAGING_WRITER_URL",
    "CARR_DB_STAGING_READER_URL",
    "CARR_DB_STAGING_GATE_ZERO_WRITER_URL",
    "CARR_DB_STAGING_FOUNDATION_ASSURANCE_WRITER_URL",
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSFILE",
)

# This is an executable postflight matrix, not a second source of grants. The
# applied ACL source remains db/schema.sql's generated CARR GRANTS section.
REQUIRED_IMPORTER_PRIVILEGES: tuple[tuple[str, str], ...] = (
    ("public.actor", "SELECT"),
    ("public.doctrine_document", "SELECT"),
    ("public.doctrine_document", "INSERT"),
    ("public.doctrine_slug_alias", "SELECT"),
    ("public.doctrine_migration_batch", "SELECT"),
    ("public.doctrine_migration_batch", "INSERT"),
    ("public.doctrine_migration_batch", "UPDATE"),
    ("public.doctrine_review_policy", "SELECT"),
    ("public.doctrine_change_set", "SELECT"),
    ("public.doctrine_change_set", "INSERT"),
    ("public.doctrine_section", "SELECT"),
    ("public.doctrine_section", "INSERT"),
    ("public.doctrine_section", "UPDATE"),
    ("public.doctrine_revision", "SELECT"),
    ("public.doctrine_revision", "INSERT"),
    ("public.doctrine_meta", "SELECT"),
    ("public.doctrine_meta", "UPDATE"),
    ("public.doctrine_snapshot", "SELECT"),
    ("public.doctrine_snapshot", "INSERT"),
    ("public.doctrine_snapshot", "UPDATE"),
)


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_tap = load_module("staging_app_writer_db_tap", REPO / "tools/db-tap.py")
PRODUCTION_PROJECT_ID = str(db_tap.PROJECTS["production"]["id"])
if not PRODUCTION_PROJECT_ID:
    raise RuntimeError("db-tap Production project pin is empty")
snapshot_grants = load_module(
    "staging_app_writer_snapshot_grants", REPO / "tools/schema_snapshot_grants.py"
)
credential = load_module(
    "staging_database_credential", REPO / "tools/staging_database_credential.py"
)
replacement = load_module(
    "staging_project_replacement_for_worker_cutover",
    REPO / "tools/staging-project-replacement.py",
)

REPLACEMENT_VERIFIER_KEY = "CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL"
REPLACEMENT_VERIFIER_ROLE = "carr_program5_forward_fix_verifier"
WORKER_DATABASE_SECRET_NAMES = (
    "DATABASE_URL_READER", "DATABASE_URL_WRITER", "DATABASE_URL_GATE_ZERO_WRITER",
    "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER",
)
WORKER_ENV_ALLOWLIST = (
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "LOGNAME", "SHELL",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CLOUDFLARE_API_TOKEN",
)
REPLACEMENT_RECEIPT_KEYS = frozenset({
    "contract_id", "receipt_id", "evidence_ref", "receipt_sha256", "git_sha",
    "source_tree_oid", "source_tree_sha256", "source_tree_entry_count",
    "artifact_sha256", "config_sha256", "dependency_sha256",
    "prior_staging_project_id", "replacement_project_id", "replacement_branch_id",
    "replacement_endpoint_id", "live_migration_ledger", "live_migration_count",
    "live_migration_highest", "live_migration_ledger_sha256", "synthetic_data_count",
    "production_overlap_count", "observed_at",
})


def _wrangler_account_id() -> str:
    with WRANGLER_CONFIG.open("rb") as handle:
        value = tomllib.load(handle).get("account_id")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise RuntimeError("wrangler.toml must pin one lowercase 32-hex Cloudflare account_id")
    return value


CLOUDFLARE_ACCOUNT_ID = _wrangler_account_id()


class ProvisioningRefusal(RuntimeError):
    """The requested action is outside the exact isolated-staging contract."""


@dataclass(frozen=True)
class ProviderScope:
    project_id: str
    branch_id: str
    endpoint_id: str
    endpoint_host: str
    port: int
    database: str


@dataclass(frozen=True)
class ReplacementTarget:
    candidate_operation_id: uuid.UUID
    receipt_id: uuid.UUID
    expected_sha: str


@dataclass(frozen=True)
class ReplacementBinding:
    target: ReplacementTarget
    production: Any
    old: Any
    candidate: Any
    source_manifest: dict[str, Any]
    receipt: dict[str, Any]
    owner: "ScopedDsn"


@dataclass(frozen=True)
class ScopedDsn:
    """A secret DSN bound to the immutable provider scope that produced it."""

    scope: ProviderScope
    role_name: str
    endpoint: str
    port: int
    database: str
    value: str = field(repr=False, compare=False)


@dataclass(frozen=True)
class SeedState:
    proposal_status: tuple[tuple[str, int], ...]
    target_count: int
    batch_count: int


@dataclass(frozen=True)
class RoleAuthority:
    can_login: bool
    inherits_privileges: bool
    powerful_attributes: tuple[str, ...]
    role_config: tuple[str, ...]
    memberships: tuple[tuple[str, bool, bool, bool], ...]
    reachable_roles: tuple[str, ...]
    direct_acl_facts: tuple[tuple[str, str, str, bool], ...]
    owned_objects: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class LoginProfile:
    label: str
    login_role: str
    # None for a DIRECT-GRANT profile: the canonical grants attach to the login
    # role itself rather than to a NOLOGIN capability bundle it is a member of.
    bundle_role: str | None
    secret_name: str
    # True for a role a MIGRATION creates with no password. That is the one
    # state `create role` cannot reach and `reuse` cannot describe: the role is
    # already there and no credential has ever existed for it. Such a profile
    # may be ADOPTED -- the owner connection sets the password itself -- and no
    # other profile may be, because for a role this tool created, role-present
    # and credential-absent means a credential was lost rather than never
    # minted, and silently rotating it would cut off whatever is still using it.
    created_by_migration: bool = False

    @property
    def grant_role(self) -> str:
        """The role the canonical grant plan is read for and validated against."""
        return self.bundle_role or self.login_role


@dataclass(frozen=True)
class AdoptedSeat:
    """One migration-created seat whose password is prepared but not committed.

    It was a bare three-tuple until 2026-09-13, which is the shape that made the
    commit-failure gap easy to miss: `(profile, file_profile, stored)` says
    nothing about which member carries the DSN that has already been published
    to the Worker, so the reader cannot see what a failed commit strands. Named
    fields say it.
    """

    profile: LoginProfile
    credential_file: Any
    stored: Any


@dataclass(frozen=True)
class ProfileClosure:
    login: RoleAuthority
    # None for a DIRECT-GRANT profile, which has no bundle behind its login role.
    bundle: RoleAuthority | None
    creator_edges: tuple[tuple[str, bool, bool, bool, int], ...]


# Reader first, then writer, then the seat: run_profile_sequence relies on this
# order so each completed profile stays recoverable if the next boundary refuses.
# The Gate Zero seat is LAST because it is the narrowest and the newest; nothing
# else in the Worker reads its secret.
PROFILES = (
    LoginProfile("reader", READER_ROLE, READER_BUNDLE_ROLE, "DATABASE_URL_READER"),
    LoginProfile("writer", APP_ROLE, BUNDLE_ROLE, "DATABASE_URL_WRITER"),
    LoginProfile("gate_zero_producer", GATE_ZERO_PRODUCER_ROLE,
                 None, "DATABASE_URL_GATE_ZERO_WRITER", created_by_migration=True),
    LoginProfile("foundation_assurance_oracle", FOUNDATION_ASSURANCE_ORACLE_ROLE,
                 None, "DATABASE_URL_FOUNDATION_ASSURANCE_WRITER",
                 created_by_migration=True),
)


Run = Callable[..., subprocess.CompletedProcess]
Connect = Callable[[str], Any]


ROLE_ACL_FACTS_SQL = """
with target as (select oid from pg_roles where rolname=%s)
select 'database',d.datname::text,lower(a.privilege_type),a.is_grantable
  from pg_database d cross join lateral aclexplode(d.datacl) a
  join target on target.oid=a.grantee
union all
select 'schema',n.nspname,lower(a.privilege_type),a.is_grantable
  from pg_namespace n cross join lateral aclexplode(n.nspacl) a
  join target on target.oid=a.grantee
union all
select case c.relkind when 'S' then 'sequence' else 'table' end,
       n.nspname||'.'||c.relname,lower(a.privilege_type),a.is_grantable
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
  cross join lateral aclexplode(c.relacl) a join target on target.oid=a.grantee
union all
select 'column',n.nspname||'.'||c.relname||'('||att.attname||')',lower(a.privilege_type),a.is_grantable
  from pg_attribute att join pg_class c on c.oid=att.attrelid
  join pg_namespace n on n.oid=c.relnamespace
  cross join lateral aclexplode(att.attacl) a join target on target.oid=a.grantee
 where not att.attisdropped
union all
select 'function',n.nspname||'.'||p.proname||'('||oidvectortypes(p.proargtypes)||')',
       lower(a.privilege_type),a.is_grantable
  from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  cross join lateral aclexplode(p.proacl) a join target on target.oid=a.grantee
union all
select 'type',n.nspname||'.'||t.typname,lower(a.privilege_type),a.is_grantable
  from pg_type t join pg_namespace n on n.oid=t.typnamespace
  cross join lateral aclexplode(t.typacl) a join target on target.oid=a.grantee
union all
select 'language',l.lanname,lower(a.privilege_type),a.is_grantable
  from pg_language l cross join lateral aclexplode(l.lanacl) a
  join target on target.oid=a.grantee
union all
select 'foreign_data_wrapper',f.fdwname,lower(a.privilege_type),a.is_grantable
  from pg_foreign_data_wrapper f cross join lateral aclexplode(f.fdwacl) a
  join target on target.oid=a.grantee
union all
select 'foreign_server',s.srvname,lower(a.privilege_type),a.is_grantable
  from pg_foreign_server s cross join lateral aclexplode(s.srvacl) a
  join target on target.oid=a.grantee
union all
select 'large_object',l.oid::text,lower(a.privilege_type),a.is_grantable
  from pg_largeobject_metadata l cross join lateral aclexplode(l.lomacl) a
  join target on target.oid=a.grantee
union all
select 'default_acl',coalesce(n.nspname,'*')||':'||d.defaclobjtype::text||':'||owner.rolname,
       lower(a.privilege_type),a.is_grantable
  from pg_default_acl d left join pg_namespace n on n.oid=d.defaclnamespace
  join pg_roles owner on owner.oid=d.defaclrole
  cross join lateral aclexplode(d.defaclacl) a join target on target.oid=a.grantee
order by 1,2,3,4
"""


def reject_unsafe_environment(environ: Mapping[str, str]) -> None:
    present = sorted(key for key in FORBIDDEN_ENV if environ.get(key))
    if present:
        raise ProvisioningRefusal(
            "break-glass and ambient database credentials are forbidden: "
            + ", ".join(present)
        )


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and key in payload:
        rows = payload[key]
    else:
        raise ProvisioningRefusal(f"provider {key} response is missing its rows array")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ProvisioningRefusal(f"provider {key} response has the wrong shape")
    return rows


def _provider_run(
    args: Sequence[str], *, run: Run = subprocess.run, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess:
    try:
        result = run(
            list(args), capture_output=True, text=True, timeout=60,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal(
            "provider command did not complete; all provider output suppressed"
        ) from exc
    if result.returncode != 0:
        # Provider role creation output may contain a generated password. Never
        # include stdout/stderr even on failure.
        raise ProvisioningRefusal(
            f"provider command failed without exposing output (rc={result.returncode})"
        )
    return result


def _provider_json(
    args: Sequence[str], *, run: Run = subprocess.run, env: Mapping[str, str] | None = None
) -> Any:
    result = _provider_run(args, run=run, env=env)
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvisioningRefusal("provider returned non-JSON output; output suppressed") from exc


def provider_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    result["PATH"] = "/usr/local/opt/node@22/bin:/opt/homebrew/bin:" + environ.get("PATH", "")
    key = db_tap._neon_api_key()
    if key:
        result["NEON_API_KEY"] = key
    return result


def worker_environment(environ: Mapping[str, str]) -> dict[str, str]:
    ambient = environ.get("CLOUDFLARE_ACCOUNT_ID")
    if ambient and ambient != CLOUDFLARE_ACCOUNT_ID:
        raise ProvisioningRefusal("ambient Cloudflare account differs from the pinned CARR account")
    result = {name: environ[name] for name in WORKER_ENV_ALLOWLIST if environ.get(name)}
    result["CLOUDFLARE_ACCOUNT_ID"] = CLOUDFLARE_ACCOUNT_ID
    return result


def _canonical_uuid4(value: str, label: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ProvisioningRefusal(f"{label} must be a full canonical UUIDv4") from exc
    if str(parsed) != value or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise ProvisioningRefusal(f"{label} must be a full canonical UUIDv4")
    return parsed


def replacement_target(
    candidate_operation_id: str, receipt_id: str, expected_sha: str, *,
    run: Run = subprocess.run,
) -> ReplacementTarget:
    candidate = _canonical_uuid4(candidate_operation_id, "candidate operation id")
    receipt = _canonical_uuid4(receipt_id, "receipt id")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ProvisioningRefusal("expected merged SHA must be a full lowercase commit SHA")
    try:
        result = run(
            ["git", "-C", str(REPO), "merge-base", "--is-ancestor", expected_sha,
             "origin/main"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("merged source ancestry check did not complete; output suppressed") from exc
    if result.returncode != 0:
        raise ProvisioningRefusal("expected source SHA is not reachable from origin/main; output suppressed")
    return ReplacementTarget(candidate, receipt, expected_sha)


def replacement_credential_root(operation_id: uuid.UUID) -> pathlib.Path:
    if operation_id.version != 4 or operation_id.variant != uuid.RFC_4122:
        raise ProvisioningRefusal("candidate operation id must be UUIDv4")
    return pathlib.Path.home() / ".config/carr/staging-replacements" / str(operation_id)


def worker_cutover_lock_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config/carr/.staging-worker-cutover.lock"


def load_replacement_source_manifest(
    target: ReplacementTarget, *, run: Run = subprocess.run,
) -> dict[str, Any]:
    try:
        result = run(
            [sys.executable, str(replacement.RELEASE_MANIFEST), "source-contract",
             "--sha", target.expected_sha],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("merged source contract did not complete; output suppressed") from exc
    if result.returncode != 0:
        raise ProvisioningRefusal("merged source contract failed; output suppressed")
    try:
        manifest = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvisioningRefusal("merged source contract was not JSON; output suppressed") from exc
    exact = {"git_sha": target.expected_sha, "tree_mode": "full",
             "tree_tuple": ["mode", "type", "object", "path"]}
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in exact.items()):
        raise ProvisioningRefusal("merged source contract identity is not exact")
    replacement.validate_migration_ledger(manifest)
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("source_tree_oid") or "")) \
            or not isinstance(manifest.get("source_tree_entry_count"), int) \
            or manifest["source_tree_entry_count"] <= 0:
        raise ProvisioningRefusal("merged source tree identity is invalid")
    for key in ("source_tree_sha256", "artifact_sha256", "config_sha256",
                "dependency_sha256", "migration_ledger_sha256"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get(key) or "")):
            raise ProvisioningRefusal(f"merged source contract {key} is invalid")
    return manifest


def validate_replacement_receipt(
    target: ReplacementTarget, production: Any, old: Any, candidate: Any,
    receipt: Mapping[str, Any], source_manifest: Mapping[str, Any],
) -> None:
    if set(receipt) != REPLACEMENT_RECEIPT_KEYS:
        raise ProvisioningRefusal("immutable replacement receipt projection is not exact")
    if production.project_id != replacement.PRODUCTION_PROJECT_ID \
            or old.project_name != replacement.STAGING_NAME \
            or candidate.project_name != replacement.candidate_name(target.candidate_operation_id):
        raise ProvisioningRefusal("replacement provider project identities are not exact")
    for attribute in ("project_id", "branch_id", "endpoint_id", "endpoint_host"):
        values = [getattr(scope, attribute) for scope in (production, old, candidate)]
        if len(values) != len(set(values)):
            raise ProvisioningRefusal(f"replacement provider {attribute} identities overlap")
    try:
        contract_id = uuid.UUID(str(receipt["contract_id"]))
        observed_receipt = uuid.UUID(str(receipt["receipt_id"]))
    except (ValueError, AttributeError) as exc:
        raise ProvisioningRefusal("replacement receipt UUID projection is invalid") from exc
    if contract_id.version != 4 or contract_id.variant != uuid.RFC_4122 \
            or str(contract_id) != str(receipt["contract_id"]) \
            or observed_receipt != target.receipt_id \
            or str(observed_receipt) != str(receipt["receipt_id"]):
        raise ProvisioningRefusal("replacement receipt identity disagrees")
    expected_source = {
        "git_sha": target.expected_sha,
        "source_tree_oid": source_manifest.get("source_tree_oid"),
        "source_tree_sha256": source_manifest.get("source_tree_sha256"),
        "source_tree_entry_count": source_manifest.get("source_tree_entry_count"),
        "artifact_sha256": source_manifest.get("artifact_sha256"),
        "config_sha256": source_manifest.get("config_sha256"),
        "dependency_sha256": source_manifest.get("dependency_sha256"),
        "live_migration_ledger": source_manifest.get("migration_ledger"),
        "live_migration_count": source_manifest.get("migration_count"),
        "live_migration_highest": source_manifest.get("migration_highest"),
        "live_migration_ledger_sha256": source_manifest.get("migration_ledger_sha256"),
    }
    expected_scope = {
        "prior_staging_project_id": old.project_id,
        "replacement_project_id": candidate.project_id,
        "replacement_branch_id": candidate.branch_id,
        "replacement_endpoint_id": candidate.endpoint_id,
        "production_overlap_count": 0,
    }
    if source_manifest.get("git_sha") != target.expected_sha \
            or any(receipt.get(key) != value for key, value in {
                **expected_source, **expected_scope}.items()):
        raise ProvisioningRefusal("replacement receipt disagrees with source or provider scope")
    if not re.fullmatch(r"ops\.staging-replacement-project:sha256:[0-9a-f]{64}",
                        str(receipt.get("evidence_ref") or "")) \
            or not re.fullmatch(r"sha256:[0-9a-f]{64}",
                                str(receipt.get("receipt_sha256") or "")) \
            or not isinstance(receipt.get("observed_at"), str) \
            or not receipt["observed_at"].strip() \
            or not isinstance(receipt.get("synthetic_data_count"), int) \
            or isinstance(receipt.get("synthetic_data_count"), bool) \
            or receipt["synthetic_data_count"] <= 0:
        raise ProvisioningRefusal("replacement receipt evidence projection is invalid")
    try:
        replacement.validate_migration_ledger(source_manifest)
    except replacement.ReplacementRefusal as exc:
        raise ProvisioningRefusal("replacement receipt migration ledger is invalid") from exc


def _local_scope(scope: Any) -> ProviderScope:
    return ProviderScope(
        scope.project_id, scope.branch_id, scope.endpoint_id, scope.endpoint_host,
        5432, "neondb",
    )


def resolve_replacement_binding(
    target: ReplacementTarget, *, run: Run = subprocess.run,
    environ: Mapping[str, str], connect: Connect = psycopg.connect,
) -> ReplacementBinding:
    try:
        production, old, candidate = replacement.resolve_existing_scopes(
            target.candidate_operation_id, run=run, environ=environ)
    except replacement.ReplacementRefusal as exc:
        raise ProvisioningRefusal("replacement provider readback refused; output suppressed") from exc
    if candidate is None:
        raise ProvisioningRefusal("exact replacement candidate does not exist; no fallback allowed")
    source_manifest = load_replacement_source_manifest(target, run=run)
    root = replacement_credential_root(target.candidate_operation_id)
    verifier_path = root / "verifier.env"
    verifier = credential.load_for_endpoint_id(
        credential.CredentialPaths(
            final=verifier_path, pending=pathlib.Path(str(verifier_path) + ".pending")),
        key=REPLACEMENT_VERIFIER_KEY, role_name=REPLACEMENT_VERIFIER_ROLE,
        expected_endpoint_id=candidate.endpoint_id, expected_port=5432,
        expected_database="neondb",
    )
    conn = connect(verifier.value)
    try:
        cur = conn.cursor()
        cur.execute("begin transaction read only")
        cur.execute(
            "select session_user,current_user,"
            "pg_has_role(session_user,'carr_program5_forward_fix_verifiers','member')"
        )
        if tuple(cur.fetchone() or ()) != (
            REPLACEMENT_VERIFIER_ROLE, REPLACEMENT_VERIFIER_ROLE, True,
        ):
            raise ProvisioningRefusal("replacement verifier identity is not exact")
        receipt = replacement.call_json(
            cur, replacement.READ_FUNCTION, target.receipt_id)
        conn.rollback()
    finally:
        conn.close()
    validate_replacement_receipt(
        target, production, old, candidate, receipt, source_manifest)
    try:
        secret_owner = replacement.derive_dsn(
            candidate, replacement.OWNER_ROLE, run=run, environ=environ)
    except replacement.ReplacementRefusal as exc:
        raise ProvisioningRefusal("replacement owner DSN derivation refused; output suppressed") from exc
    local = _local_scope(candidate)
    owner = ScopedDsn(
        local, replacement.OWNER_ROLE, candidate.endpoint_host, 5432, "neondb",
        secret_owner.value,
    )
    return ReplacementBinding(
        target, production, old, candidate, dict(source_manifest), dict(receipt), owner)


def load_rollback_worker_values(old: Any) -> dict[str, str | None]:
    live_names = read_worker_database_secret_names()
    values: dict[str, str | None] = {}
    for login_profile in PROFILES:
        file_profile = credential.profile(login_profile.label)
        try:
            stored = credential.load_existing(
                file_profile.paths, key=file_profile.key,
                role_name=file_profile.role_name,
                expected_endpoint=old.endpoint_host, expected_port=5432,
                expected_database="neondb",
            )
        except credential.CredentialRefusal as exc:
            if (
                str(exc) == "staging credential is absent"
                and login_profile.created_by_migration
                and login_profile.secret_name not in live_names
            ):
                # The prior Worker predates this migration-created seat.  JSON
                # null is Wrangler's atomic bulk-delete representation, so a
                # rollback restores the observed absence instead of inventing
                # a credential that never existed.
                values[login_profile.secret_name] = None
                continue
            raise
        if stored.state != "final":
            raise ProvisioningRefusal(
                "canonical old-staging rollback credential is not final")
        if login_profile.secret_name not in live_names:
            raise ProvisioningRefusal(
                "canonical old-staging credential has no matching Worker secret")
        values[login_profile.secret_name] = stored.value
    expected_names = {name for name, value in values.items() if value is not None}
    if live_names != expected_names:
        raise ProvisioningRefusal(
            "old-staging Worker database secret names differ from rollback material")
    return values


def repair_prior_rollback_credentials(
    old: Any, plans: Mapping[str, Sequence[str]], *,
    run: Run = subprocess.run, environ: Mapping[str, str] | None = None,
    connect: Connect = psycopg.connect,
) -> dict[str, str]:
    """Complete the canonical rollback project without touching the Worker.

    Forward migrations can add a new migration-owned login seat after the
    original staging credentials were minted.  A later candidate cutover must
    not delete that live Worker binding merely because the untouched rollback
    project predates it, and it must not rotate a credential whose local file
    was lost.  This door closes that gap narrowly: only a migration-created,
    still-passwordless seat may be adopted; ordinary missing credentials and
    already-passworded seats continue to refuse.
    """
    try:
        owner_secret = replacement.derive_dsn(
            old, replacement.OWNER_ROLE, run=run,
            environ=environ if environ is not None else os.environ)
    except replacement.ReplacementRefusal as exc:
        raise ProvisioningRefusal(
            "prior staging owner DSN derivation refused; output suppressed") from exc
    local = _local_scope(old)
    owner_dsn = ScopedDsn(
        local, replacement.OWNER_ROLE, old.endpoint_host, 5432, "neondb",
        owner_secret.value,
    )
    config_root = credential.profile("reader").paths.final.parent
    lock_path = config_root / ".staging-prior-rollback-role.lock"
    outcomes: dict[str, str] = {}
    with credential.exclusive_lock(lock_path):
        owner = connect(owner_dsn.value)
        try:
            expected_creator = require_direct_owner_identity(owner.cursor())
            owner.commit()
            for profile in PROFILES:
                file_profile = credential.profile(profile.label)
                try:
                    stored = credential.load_existing(
                        file_profile.paths, key=file_profile.key,
                        role_name=file_profile.role_name,
                        expected_endpoint=owner_dsn.endpoint,
                        expected_port=owner_dsn.port,
                        expected_database=owner_dsn.database,
                    )
                except credential.CredentialRefusal as exc:
                    if "is absent" not in str(exc):
                        raise
                    if not profile.created_by_migration:
                        raise ProvisioningRefusal(
                            f"canonical rollback credential for {profile.label} is absent")
                    cur = owner.cursor()
                    cur.execute("select exists(select 1 from pg_roles where rolname=%s)",
                                (profile.login_role,))
                    exists = cur.fetchone() == (True,)
                    passwordless = role_is_passwordless(cur, profile.login_role) if exists else None
                    action = decide_profile_action(
                        role_exists_now=exists, credential_state="absent",
                        role_created_by_migration=True,
                        role_passwordless=passwordless,
                    )
                    if action != "adopt":
                        raise ProvisioningRefusal(
                            f"canonical rollback seat {profile.login_role} is not adoptable")
                    stored = credential.prepare_pending(
                        file_profile.paths, key=file_profile.key,
                        role_name=file_profile.role_name,
                        owner_uri=owner_dsn.value,
                        expected_endpoint=owner_dsn.endpoint,
                        expected_port=owner_dsn.port,
                        expected_database=owner_dsn.database,
                    )
                else:
                    cur = owner.cursor()
                    cur.execute("select exists(select 1 from pg_roles where rolname=%s)",
                                (profile.login_role,))
                    exists = cur.fetchone() == (True,)
                    passwordless = (
                        role_is_passwordless(cur, profile.login_role)
                        if exists and profile.created_by_migration else None
                    )
                    action = decide_profile_action(
                        role_exists_now=exists, credential_state=stored.state,
                        role_created_by_migration=profile.created_by_migration,
                        role_passwordless=passwordless,
                    )
                if action in {"resume", "reuse"}:
                    validate_profile_login(
                        stored.value, profile, plans[profile.label],
                        expected_creator=expected_creator, connect=connect,
                    )
                    outcome = "resumed" if action == "resume" else "reused"
                elif action == "adopt":
                    apply_login_profile(
                        owner, profile, plans[profile.label], stored.password,
                        expected_creator=expected_creator, adopt=True,
                    )
                    validate_profile_login(
                        stored.value, profile, plans[profile.label],
                        expected_creator=expected_creator, connect=connect,
                    )
                    outcome = "adopted"
                else:
                    raise ProvisioningRefusal(
                        f"canonical rollback credential repair refused action {action}")
                if stored.state == "pending":
                    credential.promote_pending(
                        file_profile.paths, key=file_profile.key,
                        expected_value=stored.value,
                    )
                outcomes[profile.label] = outcome
        finally:
            owner.close()
    return outcomes


def validate_provider_scope(
    projects: Sequence[dict[str, Any]], branches: Sequence[dict[str, Any]],
    endpoints: Sequence[dict[str, Any]],
) -> ProviderScope:
    matches = [row for row in projects if row.get("name") == STAGING_PROJECT_NAME]
    if len(matches) != 1:
        raise ProvisioningRefusal(
            f"expected one {STAGING_PROJECT_NAME} project, found {len(matches)}"
        )
    project_id = str(matches[0].get("id") or "")
    if not project_id or project_id == PRODUCTION_PROJECT_ID:
        raise ProvisioningRefusal("staging resolved to an empty or Production project id")
    branch_matches = [row for row in branches if row.get("name") == STAGING_BRANCH_NAME]
    if len(branch_matches) != 1 or branch_matches[0].get("default") is not True:
        raise ProvisioningRefusal("staging must resolve to exactly one default main branch")
    branch_id = str(branch_matches[0].get("id") or "")
    branch_project = str(branch_matches[0].get("project_id") or "")
    if not branch_id or branch_project != project_id:
        raise ProvisioningRefusal(
            "staging main branch has no immutable id or belongs to another project"
        )
    endpoint_matches = [
        row for row in endpoints
        if str(row.get("branch_id") or branch_id) == branch_id
        and row.get("type") in {"read_write", "read-write", "rw"}
    ]
    if len(endpoint_matches) != 1:
        raise ProvisioningRefusal("staging main must have exactly one read-write endpoint")
    endpoint_id = str(endpoint_matches[0].get("id") or "")
    endpoint_host = str(endpoint_matches[0].get("host") or "").lower().rstrip(".")
    if (
        not endpoint_id.startswith("ep-")
        or not endpoint_host.startswith(endpoint_id + ".")
        or not endpoint_host.endswith(".neon.tech")
    ):
        raise ProvisioningRefusal("staging read-write endpoint identity or host is invalid")
    return ProviderScope(project_id, branch_id, endpoint_id, endpoint_host, 5432, "neondb")


def resolve_provider_scope(
    *, neonctl: str, run: Run = subprocess.run, environ: Mapping[str, str]
) -> ProviderScope:
    env = provider_environment(environ)
    project_payload = _provider_json(
        [neonctl, "projects", "list", "--org-id", db_tap.NEON_ORG, "--output", "json"],
        run=run, env=env,
    )
    projects = _rows(project_payload, "projects")
    matches = [row for row in projects if row.get("name") == STAGING_PROJECT_NAME]
    if len(matches) != 1:
        return validate_provider_scope(projects, [], [])
    project_id = str(matches[0].get("id") or "")
    if not project_id or project_id == PRODUCTION_PROJECT_ID:
        return validate_provider_scope(projects, [], [])
    branch_payload = _provider_json(
        [neonctl, "branches", "list", "--project-id", project_id, "--output", "json"],
        run=run, env=env,
    )
    branches = _rows(branch_payload, "branches")
    branch_matches = [row for row in branches if row.get("name") == STAGING_BRANCH_NAME]
    if len(branch_matches) != 1 or not str(branch_matches[0].get("id") or ""):
        return validate_provider_scope(projects, branches, [])
    branch_id = str(branch_matches[0]["id"])
    endpoint_payload = _provider_json(
        [neonctl, "api", f"/projects/{project_id}/branches/{branch_id}/endpoints",
         "--output", "json"],
        run=run, env=env,
    )
    return validate_provider_scope(
        projects, branches, _rows(endpoint_payload, "endpoints")
    )


def verify_provider_scope(
    scope: ProviderScope, *, neonctl: str, run: Run = subprocess.run,
    environ: Mapping[str, str],
) -> None:
    """Reject a rename/rebuild race without replacing the resolved IDs."""
    env = provider_environment(environ)
    project_payload = _provider_json(
        [neonctl, "projects", "list", "--org-id", db_tap.NEON_ORG, "--output", "json"],
        run=run, env=env,
    )
    projects = _rows(project_payload, "projects")
    exact_projects = [
        row for row in projects
        if row.get("name") == STAGING_PROJECT_NAME and str(row.get("id") or "") == scope.project_id
    ]
    if len(exact_projects) != 1 or len(
        [row for row in projects if row.get("name") == STAGING_PROJECT_NAME]
    ) != 1:
        raise ProvisioningRefusal("staging project changed after immutable scope resolution")
    branch_payload = _provider_json(
        [neonctl, "branches", "list", "--project-id", scope.project_id, "--output", "json"],
        run=run, env=env,
    )
    branches = _rows(branch_payload, "branches")
    exact_branches = [
        row for row in branches
        if row.get("name") == STAGING_BRANCH_NAME
        and row.get("default") is True
        and str(row.get("id") or "") == scope.branch_id
        and str(row.get("project_id") or "") == scope.project_id
    ]
    if len(exact_branches) != 1 or len(
        [row for row in branches if row.get("name") == STAGING_BRANCH_NAME]
    ) != 1:
        raise ProvisioningRefusal("staging main branch changed after immutable scope resolution")
    endpoint_payload = _provider_json(
        [neonctl, "api",
         f"/projects/{scope.project_id}/branches/{scope.branch_id}/endpoints",
         "--output", "json"], run=run, env=env,
    )
    current = validate_provider_scope(
        projects, branches, _rows(endpoint_payload, "endpoints")
    )
    if current != scope:
        raise ProvisioningRefusal("staging endpoint changed after immutable scope resolution")


def validate_provider_dsn_query(query_text: str) -> None:
    try:
        rows = parse_qsl(query_text, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise ProvisioningRefusal("provider DSN query is invalid; value suppressed") from exc
    query = dict(rows)
    if (
        len(rows) != len(query)
        or query != {"sslmode": "require", "channel_binding": "require"}
    ):
        raise ProvisioningRefusal("provider DSN query is outside the exact safe contract")


def _dsn_parts(dsn: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(dsn)
    except ValueError as exc:
        raise ProvisioningRefusal("provider returned an invalid DSN; value suppressed") from exc
    username = unquote(parsed.username or "")
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise ProvisioningRefusal("provider returned an invalid DSN port; value suppressed") from exc
    database = unquote(parsed.path.lstrip("/"))
    validate_provider_dsn_query(parsed.query)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not username or not host or not database or parsed.fragment
    ):
        raise ProvisioningRefusal("provider returned an incomplete DSN; value suppressed")
    return username, host, port, database


def provider_dsn(
    scope: ProviderScope, role_name: str, *, neonctl: str,
    run: Run = subprocess.run, environ: Mapping[str, str],
) -> ScopedDsn:
    """Derive one secret DSN from exact immutable project and branch IDs."""
    if (
        not scope.project_id
        or scope.project_id == PRODUCTION_PROJECT_ID
        or not scope.branch_id
        or not scope.endpoint_id
        or not scope.endpoint_host
        or scope.port != 5432
        or scope.database != "neondb"
        or role_name != "neondb_owner"
    ):
        raise ProvisioningRefusal("only the isolated-staging owner DSN may come from the provider")
    result = _provider_run(
        [neonctl, "connection-string", scope.branch_id,
         "--project-id", scope.project_id, "--role-name", role_name,
         "--database-name", scope.database, "--endpoint-type", "read_write"],
        run=run, env=provider_environment(environ),
    )
    value = result.stdout.strip()
    if not value or "\n" in value or "\r" in value:
        raise ProvisioningRefusal("provider DSN response has the wrong shape; output suppressed")
    username, endpoint, port, database = _dsn_parts(value)
    if (
        username != role_name or endpoint != scope.endpoint_host
        or port != scope.port or database != scope.database
    ):
        raise ProvisioningRefusal("provider DSN differs from the pinned endpoint target; value suppressed")
    return ScopedDsn(scope, role_name, endpoint, port, database, value)


def validate_connection_scope(owner: ScopedDsn, writer: ScopedDsn) -> None:
    if owner.role_name != "neondb_owner" or writer.role_name != APP_ROLE:
        raise ProvisioningRefusal("staging connection roles are not owner/app_writer")
    if owner.scope != writer.scope:
        raise ProvisioningRefusal("owner/app_writer DSNs were not derived from one immutable scope")
    if owner.scope.project_id == PRODUCTION_PROJECT_ID or not owner.scope.branch_id:
        raise ProvisioningRefusal("connection scope is not isolated staging")
    if (owner.endpoint, owner.port, owner.database) != (
        writer.endpoint, writer.port, writer.database
    ):
        raise ProvisioningRefusal(
            "staging owner/app_writer endpoint, port, or database changed between DSN calls"
        )


def collect_seed_state(cur: Any) -> SeedState:
    seed_contract = load_module(
        "staging_app_writer_seed_contract_runtime",
        REPO / "pipelines/staging_retrieval_doctrine_seed.py",
    )
    cur.execute("select status,count(*) from retrieval_proposal group by status order by status")
    proposal_status = tuple((str(status), int(count)) for status, count in cur.fetchall())
    target_count = 0
    for target in seed_contract.TARGETS:
        cur.execute(
            """select (select count(*) from doctrine_document where slug=%s)
                    + (select count(*) from doctrine_slug_alias where alias_slug=%s)
                    + (select count(*) from doctrine_section s
                         join doctrine_document d on d.id=s.document_id
                        where d.slug=%s and s.section_key=%s)""",
            (target.slug, target.slug, target.slug, target.section_key),
        )
        target_count += int(cur.fetchone()[0])
    cur.execute("select count(*) from doctrine_migration_batch")
    batch_count = int(cur.fetchone()[0])
    return SeedState(proposal_status, target_count, batch_count)


def read_seed_state(dsn: str, *, connect: Connect = psycopg.connect) -> SeedState:
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("begin transaction read only")
        state = collect_seed_state(cur)
        conn.rollback()
        return state
    finally:
        conn.close()


def collect_role_acl_facts(
    cur: Any, role: str
) -> tuple[tuple[str, str, str, bool], ...]:
    cur.execute(ROLE_ACL_FACTS_SQL, (role,))
    return tuple(sorted((str(kind), str(identity), str(privilege), bool(grantable))
                        for kind, identity, privilege, grantable in cur.fetchall()))


def collect_memberships(cur: Any, role: str) -> tuple[tuple[str, bool, bool, bool], ...]:
    cur.execute(
        """select granted.rolname,m.admin_option,m.inherit_option,m.set_option
             from pg_auth_members m
             join pg_roles granted on granted.oid=m.roleid
             join pg_roles member on member.oid=m.member
            where member.rolname=%s
            order by granted.rolname""",
        (role,),
    )
    return tuple((str(name), bool(admin), bool(inherit), bool(can_set))
                 for name, admin, inherit, can_set in cur.fetchall())


def collect_reachable_roles(cur: Any, role: str) -> tuple[str, ...]:
    """Return every role reachable through any recursive membership edge."""
    cur.execute(
        """with recursive closure(roleid) as (
             select m.roleid
               from pg_auth_members m join pg_roles member on member.oid=m.member
              where member.rolname=%s
             union
             select m.roleid from pg_auth_members m join closure c on m.member=c.roleid
           )
           select r.rolname from closure c join pg_roles r on r.oid=c.roleid
           order by r.rolname""",
        (role,),
    )
    return tuple(str(row[0]) for row in cur.fetchall())


def collect_owned_objects(cur: Any, role: str) -> tuple[tuple[str, str, str], ...]:
    """Use PostgreSQL's shared dependency ledger for cluster-wide ownership."""
    cur.execute(
        """select d.dbid::text,d.classid::regclass::text,d.objid::text
             from pg_shdepend d join pg_roles r on r.oid=d.refobjid
            where d.refclassid='pg_authid'::regclass and d.deptype='o'
              and r.rolname=%s
            order by 1,2,3""",
        (role,),
    )
    return tuple((str(database), str(catalog), str(object_id))
                 for database, catalog, object_id in cur.fetchall())


def collect_role_authority(cur: Any, role: str) -> RoleAuthority:
    cur.execute(
        """select rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,
                  rolreplication,rolbypassrls,rolconfig
             from pg_roles where rolname=%s""",
        (role,),
    )
    row = cur.fetchone()
    if row is None:
        raise ProvisioningRefusal(f"required role {role} disappeared")
    can_login, inherits, *rest = row
    *attribute_flags, config = rest
    attribute_names = (
        "superuser", "createdb", "createrole", "replication", "bypassrls"
    )
    powerful = tuple(name for name, enabled in zip(attribute_names, attribute_flags)
                     if bool(enabled))
    role_config = tuple(sorted(str(item) for item in (config or ())))
    return RoleAuthority(
        bool(can_login), bool(inherits), powerful, role_config,
        collect_memberships(cur, role), collect_reachable_roles(cur, role),
        collect_role_acl_facts(cur, role), collect_owned_objects(cur, role),
    )


def collect_creator_edges(
    cur: Any, role: str
) -> tuple[tuple[str, bool, bool, bool, int], ...]:
    cur.execute(
        """select member.rolname,m.admin_option,m.inherit_option,m.set_option,grantor.oid::bigint
             from pg_auth_members m
             join pg_roles granted on granted.oid=m.roleid
             join pg_roles member on member.oid=m.member
             join pg_roles grantor on grantor.oid=m.grantor
            where granted.rolname=%s order by member.rolname""",
        (role,),
    )
    return tuple((str(name), bool(admin), bool(inherit), bool(can_set), int(grantor_oid))
                 for name, admin, inherit, can_set, grantor_oid in cur.fetchall())


def role_exists(cur: Any, role: str) -> bool:
    cur.execute("select exists(select 1 from pg_roles where rolname=%s)", (role,))
    return cur.fetchone() == (True,)


def repair_snapshot_creator_edge(
    cur: Any, profile: LoginProfile, *, expected_creator: str,
) -> bool:
    """Remove only the redundant membership emitted by an older snapshot.

    PostgreSQL 17 creates the creator's ADMIN/SET-FALSE edge automatically.
    The old snapshot membership renderer then replayed the same LOGIN role with
    a plain GRANT, adding a second SET-TRUE edge granted by the creator.  A
    migration-created seat may repair exactly that two-edge shape inside the
    same transaction as adoption; every other shape still refuses.
    """
    if not profile.created_by_migration:
        return False
    expected = (
        (expected_creator, True, False, False, BOOTSTRAP_SUPERUSER_OID),
    )
    observed = collect_creator_edges(cur, profile.login_role)
    if observed == expected:
        return False
    cur.execute("select oid::bigint from pg_roles where rolname=%s", (expected_creator,))
    row = cur.fetchone()
    if row is None or len(row) != 1:
        raise ProvisioningRefusal("expected creator has no single role OID")
    creator_oid = int(row[0])
    redundant = (expected_creator, False, True, True, creator_oid)
    if len(observed) != 2 or set(observed) != {expected[0], redundant}:
        raise ProvisioningRefusal(
            f"{profile.login_role} creator ADMIN edge is not safely repairable"
        )
    cur.execute(sql.SQL("revoke {} from {} granted by {}").format(
        sql.Identifier(profile.login_role),
        sql.Identifier(expected_creator),
        sql.Identifier(expected_creator),
    ))
    if collect_creator_edges(cur, profile.login_role) != expected:
        raise ProvisioningRefusal(
            f"{profile.login_role} redundant snapshot creator edge did not clear"
        )
    return True


def collect_profile_closure(cur: Any, profile: LoginProfile) -> ProfileClosure:
    return ProfileClosure(
        collect_role_authority(cur, profile.login_role),
        collect_role_authority(cur, profile.bundle_role) if profile.bundle_role else None,
        collect_creator_edges(cur, profile.login_role),
    )


def validate_profile_closure(
    closure: ProfileClosure, profile: LoginProfile, canonical_grants: Sequence[str],
    *, exact: bool, expected_creator: str,
) -> None:
    login = closure.login
    bundle = closure.bundle
    if not login.can_login or not login.inherits_privileges or login.powerful_attributes:
        raise ProvisioningRefusal(f"{profile.login_role} is not a plain inheriting LOGIN role")
    if bundle is not None:
        if bundle.can_login or not bundle.inherits_privileges or bundle.powerful_attributes:
            raise ProvisioningRefusal(f"{profile.bundle_role} is not a plain NOLOGIN privilege bundle")
        if bundle.owned_objects:
            raise ProvisioningRefusal("staging login/bundle roles must not own objects")
        if login.direct_acl_facts:
            raise ProvisioningRefusal(f"{profile.login_role} has forbidden direct ACLs")
        if bundle.memberships or bundle.reachable_roles or bundle.role_config:
            raise ProvisioningRefusal(f"{profile.bundle_role} inherits or configures extra authority")
    if login.owned_objects:
        raise ProvisioningRefusal("staging login/bundle roles must not own objects")
    if closure.creator_edges != (
        (expected_creator, True, False, False, BOOTSTRAP_SUPERUSER_OID),
    ):
        raise ProvisioningRefusal(
            f"{profile.login_role} creator ADMIN edge is not exactly bound to "
            f"{expected_creator} and the bootstrap grantor"
        )
    allowed_config = tuple(sorted((
        "idle_in_transaction_session_timeout=120s", "statement_timeout=60s",
    )))
    # A DIRECT-GRANT PROFILE REACHES NOTHING, which is stricter than the bundle
    # pair rather than looser: the exact membership check below becomes "no
    # membership at all", and the canonical ACLs are validated on the login role
    # itself because that is where the migration granted them.
    expected_memberships = () if profile.bundle_role is None \
        else ((profile.bundle_role, False, True, True),)
    expected_reachable = () if profile.bundle_role is None else (profile.bundle_role,)
    if exact:
        if login.role_config != allowed_config:
            raise ProvisioningRefusal(f"{profile.login_role} timeouts/config are not exact")
        if login.memberships != expected_memberships:
            raise ProvisioningRefusal(f"{profile.login_role} bundle membership is not exact")
        if login.reachable_roles != expected_reachable:
            raise ProvisioningRefusal(f"{profile.login_role} reaches an unexpected role")
    else:
        if login.role_config and login.role_config != allowed_config:
            raise ProvisioningRefusal(f"reused {profile.login_role} has unexpected configuration")
        if any(name != profile.bundle_role for name, *_ in login.memberships):
            raise ProvisioningRefusal(f"reused {profile.login_role} has an extra membership")
        if any(name != profile.bundle_role for name in login.reachable_roles):
            raise ProvisioningRefusal(f"reused {profile.login_role} reaches an extra role")
    expected_acl = set(snapshot_grants.acl_facts(canonical_grants))
    actual_acl = set((bundle or login).direct_acl_facts)
    if actual_acl - expected_acl:
        raise ProvisioningRefusal(f"{profile.grant_role} has excess or grantable authority")
    if exact and expected_acl - actual_acl:
        raise ProvisioningRefusal(f"{profile.grant_role} is missing canonical authority")


def _profile_membership_sql(profile: LoginProfile) -> tuple[str, ...]:
    if profile.bundle_role is None:
        return ()
    return (
        f"grant {profile.bundle_role} to {profile.login_role} with admin false",
        f"grant {profile.bundle_role} to {profile.login_role} with inherit true",
        f"grant {profile.bundle_role} to {profile.login_role} with set true",
    )


def role_is_passwordless(cur: Any, role: str) -> bool:
    """Ask the DATABASE whether this role has ever been credentialed.

    ``pg_authid`` is the only place the answer lives: ``pg_roles`` reports every
    role's ``rolpassword`` as a fixed mask, so it cannot tell a passwordless role
    from a credentialed one. A connection that cannot read ``pg_authid`` gets no
    answer here and no benefit of the doubt -- the caller refuses.

    WHO CAN READ IT, measured rather than assumed: on Neon the owner connection
    authenticates as ``neondb_owner``, which reaches ``neon_superuser``, which
    reaches ``pg_read_all_data`` -- the predefined role that confers SELECT on
    every table, ``pg_authid`` included. That reachability is the same list
    tools/cleanup-staging-app-writer.py pins as EXPECTED_PROVIDER_REACHABLE_ROLES,
    and ops/staging-database-login-provision-db-gate.py proves BOTH halves
    against a real PostgreSQL: with the grant the adoption succeeds, without it
    the adoption refuses instead of assuming.
    """
    cur.execute("select rolpassword is null from pg_authid where rolname=%s", (role,))
    rows = cur.fetchall()
    if len(rows) != 1:
        raise ProvisioningRefusal(f"{role} has no single pg_authid row on this database")
    return rows[0][0] is True


def require_passwordless_seat(cur: Any, role: str) -> None:
    """The guard on adoption, and it reads the database rather than a local file.

    THE FAILURE THIS CLOSES (2026-09-13). Adoption used to be decided by role
    existence plus the ABSENCE OF A LOCAL CREDENTIAL FILE, and that file lives
    under the running machine's home directory. A deleted file, a fresh clone, or
    a run from a second machine is therefore indistinguishable from first
    provisioning -- and the answer to first provisioning is `alter role ...
    password`, which on a seat that is already credentialed silently ROTATES it
    and cuts off whatever is still connected as it.

    A migration-created seat is adopted from exactly one honest state: the role
    is present and no password has ever been set on it, which is the state
    migration 0502 leaves behind. Anything else is a lost local file, not a
    passwordless role, and the fix for a lost file is a deliberate rotation with
    its own approval -- never a side effect of a provisioning run.
    """
    try:
        passwordless = role_is_passwordless(cur, role)
    except ProvisioningRefusal:
        raise
    except psycopg.Error as exc:
        raise ProvisioningRefusal(
            f"could not read from pg_authid whether {role} is still passwordless; "
            "this connection cannot see it, so adoption refuses"
        ) from exc
    if not passwordless:
        raise ProvisioningRefusal(
            f"{role} already holds a password; adopting it would rotate a credential that "
            "is still in use. The credential file is missing on this machine, not on the "
            "database -- rotate deliberately or restore the file"
        )


def apply_login_profile(
    conn: Any, profile: LoginProfile, grants: Sequence[str], password: str,
    *, expected_creator: str, commit: bool = True, adopt: bool = False,
) -> bool:
    """Create/converge one login profile in one advisory-locked transaction.

    ``adopt`` is the migration-created seat's path: the role is already there
    with no password, so this sets one rather than creating the role. It is
    refused for any other profile; it re-reads pg_authid UNDER THE ADVISORY LOCK
    and refuses a role that already holds a password; and the same exact-closure
    validation runs at the end of both paths, so an adopted role is held to the
    identical bar.

    ``commit=False`` is how the adopt path stays reversible: the caller publishes
    the Worker secret and reads it back while this transaction is still open, and
    a publication failure rolls the password change away rather than leaving a
    rotated role behind a Worker that never received the new DSN.
    """
    cur = conn.cursor()
    created = False
    if adopt and not profile.created_by_migration:
        raise ProvisioningRefusal(
            f"{profile.login_role} is not a migration-created seat and may not be adopted")
    try:
        cur.execute("select pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        exists = role_exists(cur, profile.login_role)
        if exists and adopt:
            repair_snapshot_creator_edge(
                cur, profile, expected_creator=expected_creator,
            )
            authority = collect_role_authority(cur, profile.login_role)
            if (
                not authority.can_login or not authority.inherits_privileges
                or authority.powerful_attributes or authority.owned_objects
            ):
                raise ProvisioningRefusal(
                    f"{profile.login_role} is not a plain inheriting LOGIN role that owns nothing")
            # UNDER THE LOCK, immediately before the only statement that can
            # rotate a live credential. The caller's decision read happened
            # before this transaction existed; this one cannot be skipped by any
            # path that reaches the ALTER.
            require_passwordless_seat(cur, profile.login_role)
            cur.execute(sql.SQL("alter role {} with password {}").format(
                sql.Identifier(profile.login_role), sql.Literal(password)))
        elif exists:
            validate_profile_closure(
                collect_profile_closure(cur, profile), profile, grants,
                exact=True, expected_creator=expected_creator,
            )
        elif adopt:
            raise ProvisioningRefusal(
                f"{profile.login_role} does not exist; its migration has not applied here")
        else:
            if profile.bundle_role is not None:
                bundle = collect_role_authority(cur, profile.bundle_role)
                if (
                    bundle.can_login or not bundle.inherits_privileges
                    or bundle.powerful_attributes or bundle.owned_objects
                    or bundle.memberships or bundle.reachable_roles or bundle.role_config
                ):
                    raise ProvisioningRefusal(f"{profile.bundle_role} is not a closed bundle role")
                expected_acl = set(snapshot_grants.acl_facts(grants))
                if set(bundle.direct_acl_facts) - expected_acl:
                    raise ProvisioningRefusal(f"{profile.bundle_role} has excess authority")
            cur.execute("set local createrole_self_grant = ''")
            cur.execute("select current_setting('createrole_self_grant')")
            if cur.fetchone() != ("",):
                raise ProvisioningRefusal("createrole_self_grant did not fail closed")
            cur.execute(sql.SQL(
                "create role {} login inherit nosuperuser nocreatedb nocreaterole "
                "noreplication nobypassrls password {}"
            ).format(sql.Identifier(profile.login_role), sql.Literal(password)))
            # PostgreSQL 17 automatically grants the newly-created role back to
            # its CREATEROLE creator with ADMIN TRUE / INHERIT FALSE / SET FALSE.
            # The exact edge (including its grantor) is proved below by the
            # same fail-closed closure validation used for reused roles.
            created = True
        if not adopt:
            # THE ADOPT PATH VERIFIES THE MIGRATION'S GRANTS AND DOES NOT RE-ISSUE
            # THEM. For a role this tool creates, the canonical plan is what
            # brings the role into existence with its authority. For a
            # migration-created seat the numbered file already made every grant,
            # and re-running them here would be a credentialing tool quietly
            # re-granting authority it does not own -- which moves the GRANTOR on
            # the sealed function-ACL projection and, worse, would let a seat
            # whose grants had drifted be silently repaired by a password change
            # instead of refusing. The exact-closure validation below is
            # unchanged and still runs on both paths, so an adopted seat missing
            # any canonical grant fails here rather than being fixed here.
            for statement in grants:
                cur.execute(statement)
            for statement in _profile_membership_sql(profile):
                cur.execute(statement)
        cur.execute(f"alter role {profile.login_role} set statement_timeout = '60s'")
        cur.execute(
            f"alter role {profile.login_role} set idle_in_transaction_session_timeout = '120s'"
        )
        validate_profile_closure(
            collect_profile_closure(cur, profile), profile, grants,
            exact=True, expected_creator=expected_creator,
        )
        if commit:
            conn.commit()
        return created
    except Exception as exc:
        conn.rollback()
        if password and password in str(exc):
            raise ProvisioningRefusal(
                "database role provisioning failed; credential suppressed"
            ) from exc
        raise


def validate_profile_login(
    dsn: str, profile: LoginProfile, grants: Sequence[str], *, expected_creator: str,
    connect: Connect = psycopg.connect,
) -> None:
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("begin transaction read only")
        cur.execute("select session_user,current_user")
        if cur.fetchone() != (profile.login_role, profile.login_role):
            raise ProvisioningRefusal(f"postflight did not authenticate as {profile.login_role}")
        closure = collect_profile_closure(cur, profile)
        validate_profile_closure(
            closure, profile, grants, exact=True, expected_creator=expected_creator,
        )
        cur.execute(
            "select extract(epoch from current_setting('statement_timeout')::interval)::integer,"
            "extract(epoch from current_setting('idle_in_transaction_session_timeout')::interval)::integer"
        )
        if cur.fetchone() != (60, 120):
            raise ProvisioningRefusal(f"{profile.login_role} role timeouts are not 60s/120s")
        if profile.label == "writer":
            missing: list[str] = []
            for relation, privilege in REQUIRED_IMPORTER_PRIVILEGES:
                cur.execute("select has_table_privilege(current_user,%s,%s)", (relation, privilege))
                if cur.fetchone() != (True,):
                    missing.append(f"{relation}.{privilege}")
            if missing:
                raise ProvisioningRefusal("app_writer is missing importer privileges")
        conn.rollback()
    finally:
        conn.close()


def bulk_worker_database_secrets(
    values: Mapping[str, str | None], *, wrangler: str = str(WRANGLER),
    run: Run = subprocess.run, environ: Mapping[str, str] | None = None,
) -> None:
    if set(values) != set(WORKER_DATABASE_SECRET_NAMES) \
            or any(value is not None and (not isinstance(value, str) or not value)
                   for value in values.values()):
        raise ProvisioningRefusal("Worker database secret bulk payload is not exact")
    payload = json.dumps({name: values[name] for name in WORKER_DATABASE_SECRET_NAMES},
                         sort_keys=True, separators=(",", ":"))
    try:
        result = run(
            [wrangler, "secret", "bulk", "--env", "staging",
             "--config", str(WRANGLER_CONFIG)],
            input=payload, capture_output=True, text=True, timeout=60,
            env=worker_environment(environ if environ is not None else os.environ),
        )
    except Exception as exc:
        raise ProvisioningRefusal(
            "Worker database secret bulk outcome is uncertain; output suppressed") from exc
    if result.returncode != 0:
        raise ProvisioningRefusal(
            f"Worker database secret bulk failed (rc={result.returncode}); output suppressed"
        )


def read_worker_database_secret_names(
    *, wrangler: str = str(WRANGLER),
    run: Run = subprocess.run, environ: Mapping[str, str] | None = None,
) -> set[str]:
    try:
        result = run(
            [wrangler, "secret", "list", "--env", "staging",
             "--config", str(WRANGLER_CONFIG), "--format", "json"],
            capture_output=True, text=True, timeout=60,
            env=worker_environment(environ if environ is not None else os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisioningRefusal("Worker secret readback did not complete; output suppressed") from exc
    if result.returncode != 0:
        raise ProvisioningRefusal("Worker secret readback failed; output suppressed")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProvisioningRefusal("Worker secret readback was not JSON; output suppressed") from exc
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ProvisioningRefusal("Worker secret name readback has the wrong shape")
    names = [str(row.get("name") or "") for row in payload]
    database_names = sorted(name for name in names if name.startswith("DATABASE_URL"))
    if len(names) != len(set(names)):
        raise ProvisioningRefusal("Worker secret name readback contains duplicates")
    return set(database_names)


def verify_worker_database_secret_bindings(
    *, expected_names: set[str] | frozenset[str] = frozenset(WORKER_DATABASE_SECRET_NAMES),
    wrangler: str = str(WRANGLER), run: Run = subprocess.run,
    environ: Mapping[str, str] | None = None,
) -> None:
    if read_worker_database_secret_names(
        wrangler=wrangler, run=run, environ=environ,
    ) != set(expected_names):
        raise ProvisioningRefusal("Worker database secret name readback is not exact")


def publish_worker_cutover(
    candidate_values: Mapping[str, str], rollback_values: Mapping[str, str | None], *,
    preserve: Callable[[], None], bulk: Callable[[Mapping[str, str | None]], None],
    verify: Callable[[], None], rollback_verify: Callable[[], None] | None = None,
    postflight: Callable[[], None] = lambda: None,
) -> None:
    """Publish one atomic pair; restore the old pair if postflight refuses."""
    if set(candidate_values) != set(WORKER_DATABASE_SECRET_NAMES) \
            or set(rollback_values) != set(WORKER_DATABASE_SECRET_NAMES):
        raise ProvisioningRefusal("Worker cutover and rollback secret sets are not exact")
    preserve()
    verify_rollback = rollback_verify or verify
    try:
        bulk(candidate_values)
        verify()
        preserve()
        postflight()
    except Exception as exc:
        try:
            bulk(rollback_values)
            verify_rollback()
            preserve()
        except Exception as rollback_exc:
            raise ProvisioningRefusal(
                "Worker credential cutover refused and rollback outcome is uncertain; output suppressed"
            ) from rollback_exc
        raise ProvisioningRefusal(
            "Worker credential cutover refused; prior bindings restored; output suppressed"
        ) from exc


def settle_adopted_seats(
    conn: Any, deferred: Sequence[AdoptedSeat], *,
    publish: Callable[[], None], prove: Callable[[AdoptedSeat], None],
    compensate: Callable[[], None],
) -> None:
    """Publish the Worker secrets FIRST, then commit the adopted seats' passwords.

    The order is the whole point, which is why this is a named function rather
    than three statements inline. A migration-created seat's `alter role ...
    password` is prepared in an OPEN transaction; if publication or its readback
    refuses, this raises before the commit and the caller's unwind rolls that
    password change away, leaving the seat exactly as its migration left it --
    passwordless, and reachable by nothing. Committing first and publishing
    second is the shape that leaves a ROTATED role behind a Worker still holding
    the DSN that no longer authenticates, which is the state nothing in this tool
    can repair from the outside.

    THE COMMIT ITSELF CAN FAIL, and until 2026-09-13 nothing here answered for
    that. Publication has already succeeded at that point, so the Worker is
    holding the CANDIDATE DSN while the database has rolled the matching password
    away: the seat is passwordless again and the Worker's value authenticates as
    nothing. Ordering cannot close this one -- the window is the commit -- so it
    is closed by COMPENSATION: `compensate` restores the previously serving
    secret set through the same atomic bulk-plus-readback door the publication
    used, and only then is the failure reported. A compensation that cannot read
    its own restoration back is an uncertain state and says so by name rather
    than being retried.
    """
    publish()
    try:
        conn.commit()
    except Exception as exc:
        try:
            compensate()
        except Exception as compensate_exc:
            raise ProvisioningRefusal(
                "adopted seat commit refused and the Worker secret restoration outcome "
                "is uncertain; output suppressed"
            ) from compensate_exc
        raise ProvisioningRefusal(
            "adopted seat commit refused; prior Worker bindings restored; output suppressed"
        ) from exc
    for entry in deferred:
        prove(entry)


def rollback_worker_to_prior(
    rollback_values: Mapping[str, str | None], *, preserve: Callable[[], None],
    bulk: Callable[[Mapping[str, str | None]], None], verify: Callable[[], None],
) -> None:
    """Atomically restore the untouched old-staging pair through the same door."""
    if set(rollback_values) != set(WORKER_DATABASE_SECRET_NAMES):
        raise ProvisioningRefusal("Worker rollback secret set is not exact")
    preserve()
    bulk(rollback_values)
    verify()
    preserve()


def require_direct_owner_identity(cur: Any) -> str:
    cur.execute(
        "select session_user,current_user,r.rolsuper,r.rolcreaterole "
        "from pg_roles r where r.rolname=current_user"
    )
    row = cur.fetchone()
    if row != ("neondb_owner", "neondb_owner", False, True):
        raise ProvisioningRefusal(
            "provisioning requires direct non-superuser neondb_owner with CREATEROLE"
        )
    return "neondb_owner"


def decide_profile_action(
    *, role_exists_now: bool, credential_state: str,
    role_created_by_migration: bool = False, role_passwordless: bool | None = None,
) -> str:
    """Name the one safe action for an observed role/credential/password state.

    THE ADOPT ROW IS NARROW, AND SINCE 2026-09-13 IT IS DECIDED BY THE DATABASE.
    For an ordinary profile this tool creates the role and mints its credential
    in the same operation, so role-present/credential-absent means a credential
    was LOST and the only safe answer is to refuse. The Gate Zero producer seat
    is different by design: migration 0502 creates `carr_gate_zero_producer` with
    NO password, so role-present/credential-absent is its NORMAL starting state
    and refusing it left the declared DATABASE_URL_GATE_ZERO_WRITER with no way
    to exist.

    What separates those two cases is NOT the local file -- that file lives under
    one machine's home directory and its absence proves nothing -- it is whether
    the role still has no password. So `role_passwordless` is the observed
    `pg_authid` answer, and adoption is available only when it is True:

    - passwordless and credential absent  -> adopt: first provisioning.
    - passwordless and credential pending -> adopt: a run crashed before the
      password change committed, so re-applying the pending password is what
      makes the file and the role agree.
    - NOT passwordless and credential pending -> resume, the ordinary row: the
      password is already set, so the pending value is proved by logging in
      with it rather than re-applied.
    - NOT passwordless and credential absent -> no row at all, which refuses.
      That is the lost-local-file case, and refusing it is the point.
    """
    matrix = {
        (False, "absent"): "prepare_create",
        (False, "pending"): "create",
        (True, "pending"): "resume",
        (True, "final"): "reuse",
    }
    if role_created_by_migration and role_passwordless is True:
        matrix[(True, "absent")] = "adopt"
        matrix[(True, "pending")] = "adopt"
    action = matrix.get((role_exists_now, credential_state))
    if action is None:
        if role_created_by_migration and role_exists_now and credential_state == "absent":
            raise ProvisioningRefusal(
                "the migration-created seat already holds a password and has no credential "
                "file here; that is a lost local file, not a seat waiting to be provisioned"
            )
        raise ProvisioningRefusal(
            f"unsafe role/credential state: role_exists={role_exists_now}, credential={credential_state}"
        )
    return action


def run_profile_sequence(
    profiles: Sequence[LoginProfile],
    converge: Callable[[LoginProfile], tuple[str, str]],
    publish: Callable[[LoginProfile, str], None],
) -> dict[str, str]:
    """Reader first; each completed profile is recoverable if the next boundary fails."""
    outcomes: dict[str, str] = {}
    for profile in profiles:
        value, outcome = converge(profile)
        publish(profile, value)
        outcomes[profile.label] = outcome
    return outcomes


def redact_error(exc: BaseException) -> str:
    text = str(exc)
    text = re.sub(r"postgres(?:ql)?://\S+", "[DSN REDACTED]", text)
    text = re.sub(r"https?://\S+", "[URL REDACTED]", text)
    return text[:300]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-operation-id", required=True,
                        help="full UUIDv4 of the already-receipted replacement candidate")
    parser.add_argument("--receipt-id", required=True,
                        help="full immutable replacement receipt UUIDv4")
    parser.add_argument("--sha", required=True,
                        help="full merged lowercase source SHA attested by the receipt")
    parser.add_argument("--apply", action="store_true",
                        help="converge candidate roles and atomically cut over both Worker secrets")
    parser.add_argument("--rollback-to-prior-staging", action="store_true",
                        help="atomically restore both untouched prior-staging Worker secrets")
    parser.add_argument("--repair-prior-rollback-credentials", action="store_true",
                        help="adopt only missing passwordless migration seats on the prior "
                             "staging rollback project; never changes Worker secrets")
    args = parser.parse_args(argv)
    if args.rollback_to_prior_staging and args.repair_prior_rollback_credentials:
        parser.error("rollback and prior credential repair are mutually exclusive")
    if args.rollback_to_prior_staging and not args.apply:
        parser.error("--rollback-to-prior-staging requires --apply")
    if args.repair_prior_rollback_credentials and not args.apply:
        parser.error("--repair-prior-rollback-credentials requires --apply")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        reject_unsafe_environment(os.environ)
        target = replacement_target(
            args.candidate_operation_id, args.receipt_id, args.sha)
        plans = {
            profile.label: snapshot_grants.load_current_grants_to_role(
                SCHEMA, MIGRATIONS, profile.grant_role
            ) for profile in PROFILES
        }
        binding = resolve_replacement_binding(target, environ=os.environ)
        def preserve_provider_scopes() -> None:
            try:
                replacement.prove_provider_preservation(
                    target.candidate_operation_id, binding.production, binding.old,
                    binding.candidate, run=subprocess.run, environ=os.environ)
            except replacement.ReplacementRefusal as exc:
                raise ProvisioningRefusal(
                    "replacement provider preservation readback refused; output suppressed"
                ) from exc

        if args.repair_prior_rollback_credentials:
            preserve_provider_scopes()
            repair_outcomes = repair_prior_rollback_credentials(binding.old, plans)
            preserve_provider_scopes()
            print(json.dumps({
                "environment": "staging",
                "state": "prior_rollback_credentials_repaired",
                "candidate_operation_id": str(target.candidate_operation_id),
                "receipt_id": str(target.receipt_id), "git_sha": target.expected_sha,
                "prior_staging_project_id": binding.old.project_id,
                "role_outcomes": repair_outcomes, "worker_secret_update": "none",
            }, sort_keys=True))
            return 0

        if args.rollback_to_prior_staging:
            rollback_values = load_rollback_worker_values(binding.old)
            rollback_names = {name for name, value in rollback_values.items()
                              if value is not None}
            with credential.exclusive_lock(worker_cutover_lock_path()):
                rollback_worker_to_prior(
                    rollback_values, preserve=preserve_provider_scopes,
                    bulk=lambda values: bulk_worker_database_secrets(values),
                    verify=lambda: verify_worker_database_secret_bindings(
                        expected_names=rollback_names),
                )
            print(json.dumps({
                "environment": "staging", "state": "rolled_back_to_prior_staging",
                "candidate_operation_id": str(target.candidate_operation_id),
                "receipt_id": str(target.receipt_id), "git_sha": target.expected_sha,
                "prior_staging_project_id": binding.old.project_id,
                "worker_secret_update": "atomic_bulk_pair", "candidate_roles_mutated": False,
            }, sort_keys=True))
            return 0

        scope = _local_scope(binding.candidate)
        owner_dsn = binding.owner
        before = read_seed_state(owner_dsn.value)
        if not args.apply:
            print(json.dumps({
                "environment": "staging", "project": binding.candidate.project_name,
                "branch": STAGING_BRANCH_NAME, "state": "dry_run",
                "mutated": False,
                "candidate_operation_id": str(target.candidate_operation_id),
                "receipt_id": str(target.receipt_id), "git_sha": target.expected_sha,
                "candidate_project_id": binding.candidate.project_id,
                "prior_staging_project_id": binding.old.project_id,
                "production_project_id": binding.production.project_id,
                "canonical_grants": {label: len(plan) for label, plan in plans.items()},
                "proposal_status": dict(before.proposal_status),
                "target_count": before.target_count, "batch_count": before.batch_count,
                "reader_least_privilege_required": True,
                "worker_secret_update": "atomic_bulk_pair",
                "rollback_source": "untouched canonical old-staging credential files",
                "next_phase": "rerun with --apply after local checks are green",
            }, sort_keys=True))
            return 0

        preserve_provider_scopes()
        rollback_values = load_rollback_worker_values(binding.old)
        rollback_names = {name for name, value in rollback_values.items()
                          if value is not None}
        config_root = replacement_credential_root(target.candidate_operation_id)
        lock_path = config_root / ".staging-role-operation.lock"
        outcomes: dict[str, str] = {}
        candidate_values: dict[str, str] = {}
        # THE ADOPT PATH'S OPEN TRANSACTION. A migration-created seat's password
        # change is prepared here and COMMITTED ONLY AFTER the Worker secret is
        # published and read back, so a publication that fails rolls the change
        # away and leaves the seat exactly as its migration left it. A commit
        # that fails AFTER publication is compensated instead: the prior Worker
        # bindings are restored through the same atomic door.
        deferred_seats: list[AdoptedSeat] = []
        with credential.exclusive_lock(lock_path):
            owner = psycopg.connect(owner_dsn.value)
            try:
                cur = owner.cursor()
                expected_creator = require_direct_owner_identity(cur)
                owner.commit()
                owner.autocommit = True
                cur.execute("select pg_advisory_lock(%s)", (LOCK_KEY,))
                owner.autocommit = False
                def converge(login_profile: LoginProfile) -> tuple[str, str]:
                    file_profile = credential.profile(
                        login_profile.label, config_root=config_root)
                    cur.execute("select exists(select 1 from pg_roles where rolname=%s)",
                                (login_profile.login_role,))
                    exists = cur.fetchone() == (True,)
                    # The DATABASE decides whether a migration-created seat is
                    # still uncredentialed; the local file cannot answer it.
                    passwordless = (
                        role_is_passwordless(cur, login_profile.login_role)
                        if exists and login_profile.created_by_migration else None
                    )
                    try:
                        stored = credential.load_existing(
                            file_profile.paths, key=file_profile.key,
                            role_name=file_profile.role_name,
                            expected_endpoint=owner_dsn.endpoint,
                            expected_port=owner_dsn.port,
                            expected_database=owner_dsn.database,
                        )
                    except credential.CredentialRefusal as exc:
                        if "is absent" not in str(exc):
                            raise
                        action = decide_profile_action(
                            role_exists_now=exists, credential_state="absent",
                            role_created_by_migration=login_profile.created_by_migration,
                            role_passwordless=passwordless,
                        )
                        stored = credential.prepare_pending(
                            file_profile.paths, key=file_profile.key,
                            role_name=file_profile.role_name,
                            owner_uri=owner_dsn.value,
                            expected_endpoint=owner_dsn.endpoint,
                            expected_port=owner_dsn.port,
                            expected_database=owner_dsn.database,
                        )
                    else:
                        action = decide_profile_action(
                            role_exists_now=exists, credential_state=stored.state,
                            role_created_by_migration=login_profile.created_by_migration,
                            role_passwordless=passwordless,
                        )
                    if action in {"resume", "reuse"}:
                        validate_profile_login(
                            stored.value, login_profile, plans[login_profile.label],
                            expected_creator=expected_creator,
                        )
                        outcome = "resumed" if action == "resume" else "reused"
                    elif action == "adopt":
                        # ADOPT is the migration-created seat's row: the role is
                        # already there without a password, so the owner sets one
                        # rather than creating a role that exists -- and the
                        # change is NOT committed here. The seat is proved by
                        # logging in as it, and its credential file is promoted,
                        # only after the Worker secret carrying that same DSN is
                        # published and read back. Until then this transaction is
                        # the undo, so a failed publication cannot leave a rotated
                        # seat behind a Worker that never got the new value.
                        apply_login_profile(
                            owner, login_profile, plans[login_profile.label], stored.password,
                            expected_creator=expected_creator, adopt=True, commit=False,
                        )
                        deferred_seats.append(
                            AdoptedSeat(login_profile, file_profile, stored))
                        return stored.value, "adopting"
                    else:
                        apply_login_profile(
                            owner, login_profile, plans[login_profile.label], stored.password,
                            expected_creator=expected_creator,
                        )
                        validate_profile_login(
                            stored.value, login_profile, plans[login_profile.label],
                            expected_creator=expected_creator,
                        )
                        outcome = "created"
                    if stored.state == "pending":
                        credential.promote_pending(
                            file_profile.paths, key=file_profile.key,
                            expected_value=stored.value,
                        )
                    return stored.value, outcome
                for login_profile in PROFILES:
                    value, outcome = converge(login_profile)
                    candidate_values[login_profile.secret_name] = value
                    outcomes[login_profile.label] = outcome

                final_state: dict[str, SeedState] = {}
                def verify_final_candidate_state() -> None:
                    observed = read_seed_state(owner_dsn.value)
                    if observed != before:
                        raise ProvisioningRefusal(
                            "provisioning changed proposals, doctrine targets, or batches")
                    final_state["after"] = observed

                def publish_cutover() -> None:
                    with credential.exclusive_lock(worker_cutover_lock_path()):
                        publish_worker_cutover(
                            candidate_values, rollback_values,
                            preserve=preserve_provider_scopes,
                            bulk=lambda values: bulk_worker_database_secrets(values),
                            verify=lambda: verify_worker_database_secret_bindings(),
                            rollback_verify=lambda: verify_worker_database_secret_bindings(
                                expected_names=rollback_names),
                            postflight=verify_final_candidate_state,
                        )

                def prove_adopted_seat(seat: AdoptedSeat) -> None:
                    validate_profile_login(
                        seat.stored.value, seat.profile, plans[seat.profile.label],
                        expected_creator=expected_creator,
                    )
                    if seat.stored.state == "pending":
                        credential.promote_pending(
                            seat.credential_file.paths, key=seat.credential_file.key,
                            expected_value=seat.stored.value,
                        )
                    outcomes[seat.profile.label] = "adopted"

                def restore_prior_cutover() -> None:
                    # The SAME door the publication used, under the SAME lock:
                    # a compensation that took a different path would not be
                    # proof that the Worker is back on the bindings it had.
                    with credential.exclusive_lock(worker_cutover_lock_path()):
                        rollback_worker_to_prior(
                            rollback_values,
                            preserve=preserve_provider_scopes,
                            bulk=lambda values: bulk_worker_database_secrets(values),
                            verify=lambda: verify_worker_database_secret_bindings(
                                expected_names=rollback_names),
                        )

                # Publication happens while the adopt transaction is STILL OPEN;
                # any refusal reaches the `finally` unwind, which rolls it back.
                # A refusal of the COMMIT is past that point, so it compensates.
                settle_adopted_seats(
                    owner, deferred_seats,
                    publish=publish_cutover, prove=prove_adopted_seat,
                    compensate=restore_prior_cutover)
            finally:
                try:
                    owner.rollback()
                    owner.autocommit = True
                    owner.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))
                except (psycopg.Error, ValueError):
                    pass
                owner.close()
        after = final_state.get("after")
        if after is None:
            raise ProvisioningRefusal("final candidate state readback is absent")
        print(json.dumps({
            "environment": "staging", "project": binding.candidate.project_name,
            "branch": STAGING_BRANCH_NAME, "state": "provisioned",
            "candidate_operation_id": str(target.candidate_operation_id),
            "receipt_id": str(target.receipt_id), "git_sha": target.expected_sha,
            "role_outcomes": outcomes,
            "canonical_grants": {label: len(plan) for label, plan in plans.items()},
            "identities": [READER_ROLE, APP_ROLE], "statement_timeout_seconds": 60,
            "idle_timeout_seconds": 120,
            "proposal_status": dict(after.proposal_status),
            "target_count": after.target_count, "batch_count": after.batch_count,
        }, sort_keys=True))
        return 0
    except SystemExit:
        print(
            "staging-app-writer-provision: REFUSED — provider dependency exited; output suppressed",
            file=sys.stderr,
        )
        return 2
    except (
        OSError, ValueError, ProvisioningRefusal, credential.CredentialRefusal, psycopg.Error,
        replacement.ReplacementRefusal, subprocess.TimeoutExpired,
    ) as exc:
        print("staging-app-writer-provision: REFUSED — " + redact_error(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
