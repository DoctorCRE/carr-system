#!/usr/bin/env python3
"""Fail-closed static preflight for Control Plane external provisioning.

This validates declarations and their checked-in binding surfaces only.  It
never reads environment values, contacts a provider, or provisions a database
role/device principal.  Those are explicit human-controlled external steps.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "ops" / "config" / "control-plane-provisioning.v1.json"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"config is unreadable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("config must be an object")
    return value


def text(path: str) -> str:
    try:
        return (REPO / path).read_text(encoding="utf-8")
    except OSError:
        return ""


def mapping(value: Any, path: str, errors: list[str], keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        errors.append(f"{path} must contain exactly: {', '.join(sorted(keys))}")
        return {}
    return value


def equals(value: Any, expected: str, path: str, errors: list[str]) -> None:
    if value != expected:
        errors.append(f"{path} must be {expected}")


def validate(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(config) != {"version", "authority", "device_evidence", "providers", "routine_jobs", "routine_backup", "deterministic_canaries"}:
        errors.append("config must contain exactly version, authority, device_evidence, providers, routine_jobs, routine_backup, deterministic_canaries")
        return errors
    if config.get("version") != 1:
        errors.append("version must be 1")

    authority = mapping(config.get("authority"), "authority", errors,
                        {"environment_variables", "login_roles", "privilege_bundle_role", "provisioning",
                         "required_for_system_rollout", "optional_nonblocking"})
    authority_env = mapping(authority.get("environment_variables"), "authority.environment_variables", errors,
                            {"joe", "dell", "single_seat_fallback"})
    for actor, env_name in {"joe": "CARR_DB_AUTHORITY_JOE_URL", "dell": "CARR_DB_AUTHORITY_DELL_URL",
                            "single_seat_fallback": "CARR_DB_AUTHORITY_URL"}.items():
        equals(authority_env.get(actor), env_name, f"authority.environment_variables.{actor}", errors)
    authority_roles = mapping(authority.get("login_roles"), "authority.login_roles", errors, {"joe", "dell"})
    equals(authority_roles.get("joe"), "carr_authority_joe", "authority.login_roles.joe", errors)
    equals(authority_roles.get("dell"), "carr_authority_dell", "authority.login_roles.dell", errors)
    equals(authority.get("privilege_bundle_role"), "carr_authority", "authority.privilege_bundle_role", errors)
    equals(authority.get("provisioning"), "external_human_approval", "authority.provisioning", errors)
    if authority.get("required_for_system_rollout") != ["joe"]:
        errors.append("authority.required_for_system_rollout must require Joe alone")
    if authority.get("optional_nonblocking") != ["dell"]:
        errors.append("authority.optional_nonblocking must preserve Dell without making him required")

    device = mapping(config.get("device_evidence"), "device_evidence", errors,
                     {"privilege_bundle_role", "principal_registry", "receipt_tables", "provisioning"})
    equals(device.get("privilege_bundle_role"), "carr_device_evidence", "device_evidence.privilege_bundle_role", errors)
    registry = mapping(device.get("principal_registry"), "device_evidence.principal_registry", errors,
                       {"table", "login_role_column", "device_id_column", "active_column"})
    for key, expected in {"table": "ops.device_evidence_principal", "login_role_column": "login_role",
                          "device_id_column": "device_id", "active_column": "active"}.items():
        equals(registry.get(key), expected, f"device_evidence.principal_registry.{key}", errors)
    if device.get("receipt_tables") != ["ops.device_evidence_receipt", "ops.npi_device_evidence_receipt",
                                         "ops.legacy_schedule_observation_receipt"]:
        errors.append("device_evidence.receipt_tables must declare all immutable receipt tables in order")
    equals(device.get("provisioning"), "external_human_approval", "device_evidence.provisioning", errors)

    providers = mapping(config.get("providers"), "providers", errors, {"file", "file_selector_env", "routes"})
    equals(providers.get("file"), "~/.config/carr/control-plane.env", "providers.file", errors)
    equals(providers.get("file_selector_env"), "CARR_CONTROL_PLANE_PROVIDER_ENV", "providers.file_selector_env", errors)
    routes = mapping(providers.get("routes"), "providers.routes", errors, {"primary", "secondary"})
    for route in ("primary", "secondary"):
        route_config = mapping(routes.get(route), f"providers.routes.{route}", errors, {"url_env", "token_env"})
        upper = route.upper()
        equals(route_config.get("url_env"), f"CARR_AI_ROUTE_{upper}_URL", f"providers.routes.{route}.url_env", errors)
        equals(route_config.get("token_env"), f"CARR_AI_ROUTE_{upper}_TOKEN", f"providers.routes.{route}.token_env", errors)

    jobs = mapping(config.get("routine_jobs"), "routine_jobs", errors,
                   {"scope", "credential_env", "login_role", "forbidden_environment_variables"})
    equals(jobs.get("scope"), "ledger_cli_and_tick_adapter_only", "routine_jobs.scope", errors)
    equals(jobs.get("credential_env"), "CARR_DB_JOBS_URL", "routine_jobs.credential_env", errors)
    equals(jobs.get("login_role"), "carr_jobs", "routine_jobs.login_role", errors)
    if jobs.get("forbidden_environment_variables") != ["DATABASE_URL", "CARR_DB_WRITER_URL", "CARR_DB_OWNER_URL", "CARR_DB_EXPORTER_URL"]:
        errors.append("routine_jobs.forbidden_environment_variables must declare every broad database credential")

    backup = mapping(config.get("routine_backup"), "routine_backup", errors,
                     {"scope", "credential_env", "login_role", "consumers", "provisioning"})
    equals(backup.get("scope"), "backup_dump_and_portability_mirror_only", "routine_backup.scope", errors)
    equals(backup.get("credential_env"), "CARR_DB_BACKUP_URL", "routine_backup.credential_env", errors)
    equals(backup.get("login_role"), "carr_backup", "routine_backup.login_role", errors)
    if backup.get("consumers") != ["bin/backup-dump.sh", "pipelines/doctrine_mirror.py"]:
        errors.append("routine_backup.consumers must name only backup dump and portability mirror")
    equals(backup.get("provisioning"), "external_human_approval", "routine_backup.provisioning", errors)

    canaries = mapping(config.get("deterministic_canaries"), "deterministic_canaries", errors,
                       {"file", "required_names", "scope"})
    equals(canaries.get("file"), "~/.config/carr/{calendar,notes}-canary.env", "deterministic_canaries.file", errors)
    if canaries.get("required_names") != ["CARR_CANARY_INGEST_URL", "CARR_CANARY_DESTINATION_ID"]:
        errors.append("deterministic_canaries.required_names must declare only the isolated calendar/Notes names")
    equals(canaries.get("scope"), "isolated calendar and Notes canary destinations only; no live URL or local state root", "deterministic_canaries.scope", errors)

    mcp = text("mcp-server/src/mcp.js")
    authority_runtime_probe = text("ops/control-plane-authority-runtime-preflight.py")
    migration_authority = text("migrations/0161_control_plane_authority_boundary.sql")
    migration_device = text("migrations/0163_control_plane_device_evidence.sql")
    migration_npi = text("migrations/0167_control_plane_npi_device_evidence.sql")
    migration_scheduler = text("migrations/0180_claude_scheduler_observation_receipt.sql")
    migration_launchd = text("migrations/0182_launchd_scheduler_observation_receipt.sql")
    tick = text("bin/control-plane-tick.sh")
    ledger = text("tools/control-plane.py")
    nightly = text("bin/nightly.sh")
    backup_dump = text("bin/backup-dump.sh")
    backup_role = text("migrations/0119_backup_role.sql")
    calendar = text("bin/pull-gmail-calendar.py")
    notes = text("bin/notes-sweep-post.sh")
    if "CARR_DB_AUTHORITY_${actor.slug.toUpperCase()}_URL" not in mcp or "CARR_DB_AUTHORITY_URL" not in mcp:
        errors.append("authority declarations are not bound by mcp-server/src/mcp.js")
    required_authority_probe_tokens = (
        "CARR_DB_AUTHORITY_JOE_URL", "CARR_DB_AUTHORITY_DELL_URL",
        "begin transaction read only", "ops.authority_actor_slug()",
        "FORBIDDEN_MEMBERSHIPS", "phase_exit_authorized",
    )
    if not all(token in authority_runtime_probe for token in required_authority_probe_tokens):
        errors.append("authority runtime identity probe is not bound to both partner logins and the read-only least-privilege contract")
    if not all(role in migration_authority for role in ("carr_authority_joe", "carr_authority_dell", "carr_authority")):
        errors.append("authority login-role mapping is not bound by migration 0161")
    if not all(token in migration_device for token in ("carr_device_evidence", "ops.device_evidence_principal", "login_role=session_user")):
        errors.append("device evidence bundle/principal registry is not bound by migration 0163")
    if not all(token in migration_npi for token in ("ops.npi_device_evidence_receipt", "login_role=session_user")):
        errors.append("NPI device evidence receipt is not bound by migration 0167")
    if not all(token in migration_scheduler for token in
               ("ops.legacy_schedule_observation_receipt", "login_role=session_user",
                "ops.record_claude_scheduler_observation")):
        errors.append("Claude scheduler observation receipt is not bound by migration 0180")
    if not all(token in migration_launchd for token in
               ("ops.legacy_schedule_launchd_contract", "login_role=session_user",
                "ops.record_launchd_scheduler_observation")):
        errors.append("launchd scheduler observation receipt is not bound by migration 0182")
    required_provider_tokens = ("CARR_CONTROL_PLANE_PROVIDER_ENV", "CARR_AI_ROUTE_PRIMARY_URL",
                                "CARR_AI_ROUTE_PRIMARY_TOKEN", "CARR_AI_ROUTE_SECONDARY_URL",
                                "CARR_AI_ROUTE_SECONDARY_TOKEN", "env -i")
    if not all(token in tick for token in required_provider_tokens):
        errors.append("provider file separation or primary/secondary route boundary is not bound by tick adapter")
    if "CARR_DB_JOBS_URL" not in ledger or "_assert_jobs_identity" not in ledger or "must not name an owner or writer" not in ledger:
        errors.append("jobs-only routine database boundary is not bound by ledger CLI")
    if not all(token in backup_role.lower() for token in ("carr_backup", "login")):
        errors.append("backup login-role mapping is not bound by migration 0119")
    if "CARR_DB_BACKUP_URL" not in backup_dump or "carr_backup" not in backup_dump:
        errors.append("backup credential and login boundary are not bound by backup-dump")
    # SCOPE, which is this preflight's subject: the mirror receives the backup
    # capability and nothing wider. #391 briefly also required the shell guard
    # `if [ -n "$CARR_DB_BACKUP_URL" ]` here, which is a different question —
    # what happens when the capability is absent — and one the callee now
    # answers itself by exiting 78 (#387). Asserting it here made a provisioning
    # preflight fail over the shape of a caller's error handling.
    #
    # WIDENED FROM ONE NAME TO A SET, 2026-09-01, and the ceiling is unchanged.
    # carr_backup's password is deliberately a value no agent may hold (migration
    # 0119: "THE PASSWORD IS NOT IN THIS FILE AND NOBODY HAS EVER SEEN IT" — the
    # role exists for the GitHub-Actions pg_dump and its secret lives in Actions),
    # so the key is absent from ~/.config/carr/db.env, the mirror exited 78 every
    # night from 2026-08-16, and 78 reads as a benign SKIP. A scope that only one
    # unobtainable credential can satisfy does not scope the step, it deletes it.
    # The exporter capability is STRICTLY NARROWER, measured against production:
    # over public+ops, app_exporter_local can SELECT 236 relations to carr_backup's
    # 459, the relations the exporter can read and the backup role cannot is ZERO,
    # the reverse is 223, and the exporter holds no non-SELECT grant anywhere.
    mirror_capabilities = ("CARR_DB_BACKUP_URL", "CARR_DB_EXPORTER_URL")
    if (
        not any(f'env DATABASE_URL="${name}"' in nightly for name in mirror_capabilities)
        or "pipelines/doctrine_mirror.py" not in nightly
    ):
        errors.append("portability mirror step is not scoped to a named read-only capability")
    if not all(token in calendar and token in notes for token in ("CARR_CANARY_DESTINATION_ID", "CARR_CONTROL_PLANE_MODE")) or "CARR_CALENDAR_CANARY_ENV" not in calendar or "CARR_NOTES_CANARY_ENV" not in notes:
        errors.append("isolated deterministic canary declarations are not bound by both entrypoints")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="validate Control Plane external provisioning declarations")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        errors = validate(read_json(args.config))
    except ValueError as exc:
        print(f"FAIL control-plane provisioning preflight: {exc}")
        return 1
    if errors:
        print("FAIL control-plane provisioning preflight:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("PASS control-plane provisioning preflight: static ledger/tick and scoped backup declarations are complete and bound; external provisioning remains human-controlled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
