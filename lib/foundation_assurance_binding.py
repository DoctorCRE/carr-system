"""Read-only receipt and provider scope binding for foundation assurance evidence."""

from __future__ import annotations
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
import psycopg


REPO = pathlib.Path(__file__).resolve().parents[1]


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


credential = load_module(
    "staging_database_credential", REPO / "tools/staging_database_credential.py"
)


replacement = load_module(
    "staging_project_replacement_for_worker_cutover",
    REPO / "lib/foundation_assurance_scopes.py",
)


REPLACEMENT_VERIFIER_KEY = "CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL"
REPLACEMENT_VERIFIER_ROLE = "carr_program5_forward_fix_verifier"
REPLACEMENT_RECEIPT_KEYS = frozenset({
    "contract_id", "receipt_id", "evidence_ref", "receipt_sha256", "git_sha",
    "source_tree_oid", "source_tree_sha256", "source_tree_entry_count",
    "artifact_sha256", "config_sha256", "dependency_sha256",
    "prior_staging_project_id", "replacement_project_id", "replacement_branch_id",
    "replacement_endpoint_id", "live_migration_ledger", "live_migration_count",
    "live_migration_highest", "live_migration_ledger_sha256", "synthetic_data_count",
    "production_overlap_count", "observed_at",
})


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


Run = Callable[..., subprocess.CompletedProcess]
Connect = Callable[[str], Any]


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
