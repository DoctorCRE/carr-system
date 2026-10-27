#!/usr/bin/env python3
"""Opt-in, read-only proof of the externally provisioned authority logins.

The disposable database gate cannot impersonate ``carr_authority_joe`` or
``carr_authority_dell`` on managed PostgreSQL.  This probe closes that evidence
gap at the real connection boundary without invoking an authority function,
writing a fixture, printing a credential, or treating identity proof as a
human phase-exit decision.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl, unquote, urlsplit


AUTHORITY_ENVIRONMENTS = {
    "joe": ("CARR_DB_AUTHORITY_JOE_URL", "carr_authority_joe"),
    "dell": ("CARR_DB_AUTHORITY_DELL_URL", "carr_authority_dell"),
}
REQUIRED_AUTHORITY_ACTORS = ("joe",)
OPTIONAL_AUTHORITY_ACTORS = ("dell",)

# The authority principal is intentionally not a general read/write role.  It
# reaches finite SECURITY DEFINER functions and the two audit-envelope inserts
# required by the MCP dispatcher.
FORBIDDEN_MEMBERSHIPS = (
    "carr_writer",
    "carr_jobs",
    "carr_reader",
    "carr_exporter",
    "carr_backup",
    "carr_device_evidence",
)
ROLE_MEMBERSHIPS = ("carr_authority", *FORBIDDEN_MEMBERSHIPS)
ALLOWED_MUTATION_PRIVILEGES = {
    ("public.event", "INSERT"),
    ("public.tool_call", "INSERT"),
}
REQUIRED_AUTHORITY_FUNCTIONS = (
    "ops.approve_rule(uuid,text,text[],text,text)",
    "ops.authority_actor_slug()",
    "ops.record_workflow_acceptance(text,text,text,text)",
    "ops.record_guidance_decision(uuid,text,text,text)",
    "ops.activate_guidance_situation_mapping(uuid,uuid,text)",
    "ops.activate_guidance_registry(uuid,text,text,text)",
    "ops.decide_guidance_import_batch(uuid,text,text,text,text)",
    "ops.deactivate_guidance_registry(uuid,text,text,text)",
    "ops.triage_sourced_work_request(text,integer,text,uuid)",
    "ops.accept_sourced_work_request_plan(text,integer,text,uuid)",
    "ops.accept_sourced_work_request_outcome_feedback(text,integer,text,uuid)",
    "ops.redeem_program6_browser_action_challenge(text,text,text,text,uuid)",
)
BROAD_CREDENTIAL_KEYS = {
    "DATABASE_URL",
    "CARR_DB_WRITER_URL",
    "CARR_DB_OWNER_URL",
    "CARR_DB_JOBS_URL",
    "CARR_DB_READER_URL",
    "CARR_DB_EXPORTER_URL",
    "CARR_DB_BACKUP_URL",
    "CARR_DB_DEVICE_URL",
    "CARR_DB_AUTHORITY_URL",
}

IDENTITY_SQL = "select session_user,current_user,current_setting('transaction_read_only')"
ACTOR_SQL = "select ops.authority_actor_slug()"
ROLE_SQL = """
select rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls
  from pg_roles where rolname=current_user
"""
MEMBERSHIP_SQL = """
select role_name,pg_has_role(current_user,role_name,'member')
  from unnest(%s::text[]) role_name order by role_name
"""
FUNCTION_SQL = """
select signature::text,has_function_privilege(current_user,signature,'execute')
  from unnest(%s::regprocedure[]) signature order by signature::text
"""
MUTATION_SQL = """
select n.nspname||'.'||c.relname,privilege_name
  from pg_class c
  join pg_namespace n on n.oid=c.relnamespace
  cross join unnest(array['INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) privilege_name
 where n.nspname in ('public','ops') and c.relkind in ('r','p')
   and has_table_privilege(current_user,c.oid,privilege_name)
 order by 1,2
"""


class Cursor(Protocol):
    def execute(self, query: str, params: object | None = None) -> Any: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...
    def close(self) -> None: ...


Connect = Callable[[str], Connection]


def is_direct_authority_uri(value: str, expected_user: str) -> bool:
    """Admit only a complete literal URI; never inherit service/passfile state."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (TypeError, ValueError):
        return False
    try:
        query_items = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return False
    query = dict(query_items)
    canonical_neon_tls = (
        len(query_items) == len(query)
        and query.get("sslmode") == "require"
        and set(query) in ({"sslmode"}, {"sslmode", "channel_binding"})
        and ("channel_binding" not in query or query["channel_binding"] == "require")
    )
    return bool(
        parsed.scheme.lower() in {"postgres", "postgresql"}
        and unquote(parsed.username or "") == expected_user
        and parsed.password not in (None, "")
        and parsed.hostname
        and parsed.path not in ("", "/")
        and canonical_neon_tls
        and parsed.fragment == ""
        and (port is None or 1 <= port <= 65535)
    )


def broad_environment_key(environ: Mapping[str, str]) -> str | None:
    """Return the first ambiguous connection input without ever reading values."""
    for key in sorted(environ):
        if not environ.get(key):
            continue
        if key in BROAD_CREDENTIAL_KEYS or key.startswith("PG"):
            return key
    return None


def _base_report() -> dict[str, Any]:
    return {
        "scope": "authority-runtime-identity-only; not phase exit, workflow acceptance, or human approval",
        "read_only": True,
        "phase_exit_authorized": False,
        "required_authority_identities_verified": False,
        "optional_authority_identities_verified": {
            actor: False for actor in OPTIONAL_AUTHORITY_ACTORS
        },
        "authority_runtime_identities_verified": False,
        "principals": {
            actor: {
                "expected_login": expected,
                "credential_present": False,
                "verified": False,
            }
            for actor, (_, expected) in AUTHORITY_ENVIRONMENTS.items()
        },
    }


def probe_principal(actor: str, expected_login: str, dsn: str, connect: Connect) -> dict[str, Any]:
    """Verify one real connection using fixed, read-only catalog queries."""
    result: dict[str, Any] = {
        "actor": actor,
        "expected_login": expected_login,
        "credential_present": True,
        "credential_shape_valid": is_direct_authority_uri(dsn, expected_login),
        "identity_matches": False,
        "actor_mapping_matches": False,
        "transaction_read_only": False,
        "role_attributes_narrow": False,
        "authority_membership": False,
        "forbidden_memberships_absent": False,
        "required_functions_reachable": False,
        "direct_mutation_scope_exact": False,
        "verified": False,
    }
    if not result["credential_shape_valid"]:
        result["error"] = "credential_shape_refused"
        return result

    connection = connect(dsn)
    cursor = connection.cursor()
    try:
        # This must be the first SQL.  Psycopg starts a transaction on the first
        # statement, so declaring READ ONLY after an identity SELECT is too late.
        cursor.execute("begin transaction read only")
        cursor.execute(IDENTITY_SQL)
        identity = cursor.fetchone() or (None, None, None)
        result["identity_matches"] = identity[0] == expected_login and identity[1] == expected_login
        result["transaction_read_only"] = identity[2] == "on"

        # This is the one safe positive authority call: it is stable, returns
        # only the session-derived actor slug, and performs no canonical write.
        cursor.execute(ACTOR_SQL)
        actor_row = cursor.fetchone() or (None,)
        result["actor_mapping_matches"] = actor_row[0] == actor

        cursor.execute(ROLE_SQL)
        role = cursor.fetchone()
        if role is not None and len(role) == 8:
            (name, superuser, inherit, create_role, create_db, can_login,
             replication, bypass_rls) = role
            result["role_attributes_narrow"] = bool(
                name == expected_login and can_login is True and inherit is True
                and superuser is False and create_role is False and create_db is False
                and replication is False and bypass_rls is False
            )

        cursor.execute(MEMBERSHIP_SQL, (list(ROLE_MEMBERSHIPS),))
        memberships = {str(name): bool(value) for name, value in cursor.fetchall()}
        result["authority_membership"] = memberships.get("carr_authority") is True
        result["forbidden_memberships_absent"] = all(
            memberships.get(name, False) is False for name in FORBIDDEN_MEMBERSHIPS)

        cursor.execute(FUNCTION_SQL, (list(REQUIRED_AUTHORITY_FUNCTIONS),))
        functions = {str(name): bool(value) for name, value in cursor.fetchall()}
        missing = [name for name in REQUIRED_AUTHORITY_FUNCTIONS if functions.get(name) is not True]
        result["required_functions_reachable"] = not missing
        result["required_function_count"] = len(REQUIRED_AUTHORITY_FUNCTIONS)
        result["missing_function_count"] = len(missing)

        cursor.execute(MUTATION_SQL)
        mutation_privileges = {(str(table), str(privilege)) for table, privilege in cursor.fetchall()}
        result["direct_mutation_scope_exact"] = mutation_privileges == ALLOWED_MUTATION_PRIVILEGES
        result["direct_mutation_privilege_count"] = len(mutation_privileges)

        result["verified"] = all(result[key] is True for key in (
            "identity_matches",
            "actor_mapping_matches",
            "transaction_read_only",
            "role_attributes_narrow",
            "authority_membership",
            "forbidden_memberships_absent",
            "required_functions_reachable",
            "direct_mutation_scope_exact",
        ))
        return result
    finally:
        try:
            cursor.execute("rollback")
        finally:
            connection.close()


def collect_runtime(environ: Mapping[str, str], connect: Connect) -> dict[str, Any]:
    """Collect secret-safe evidence for both partner-specific authority DSNs."""
    report = _base_report()
    ambiguous = broad_environment_key(environ)
    if ambiguous is not None:
        report["error"] = "broad_environment_refused"
        report["refused_environment_key"] = ambiguous
        return report

    for actor, (env_name, expected_login) in AUTHORITY_ENVIRONMENTS.items():
        dsn = environ.get(env_name, "")
        report["principals"][actor]["credential_present"] = bool(dsn)
        if not dsn:
            continue
        if not is_direct_authority_uri(dsn, expected_login):
            report["principals"][actor]["credential_shape_valid"] = False
            report["principals"][actor]["error"] = "credential_shape_refused"
            continue
        try:
            report["principals"][actor] = probe_principal(actor, expected_login, dsn, connect)
        except Exception:
            report["principals"][actor] = {
                "actor": actor,
                "expected_login": expected_login,
                "credential_present": True,
                "credential_shape_valid": True,
                "verified": False,
                "error": "unavailable_or_not_authorized",
            }
    required_verified = all(
        report["principals"][actor].get("verified") is True
        for actor in REQUIRED_AUTHORITY_ACTORS)
    report["required_authority_identities_verified"] = required_verified
    report["optional_authority_identities_verified"] = {
        actor: report["principals"][actor].get("verified") is True
        for actor in OPTIONAL_AUTHORITY_ACTORS
    }
    # Compatibility field retains its literal plural meaning: every declared
    # partner identity verified. System rollout uses the separate REQUIRED
    # result, so an absent optional Dell credential never becomes a quorum gate.
    report["authority_runtime_identities_verified"] = all(
        report["principals"][actor].get("verified") is True
        for actor in AUTHORITY_ENVIRONMENTS)
    return report


def system_rollout_ready(report: Mapping[str, Any]) -> bool:
    """Joe's verified authority is sufficient; optional seats never form quorum."""
    return report.get("required_authority_identities_verified") is True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="read-only required-Joe and optional-Dell authority-runtime identity preflight")
    parser.add_argument("--runtime", action="store_true",
                        help="read partner-specific authority environment variables and query their databases read-only")
    args = parser.parse_args()
    if not args.runtime:
        print(json.dumps({
            "ok": False,
            "runtime_opt_in_required": True,
            "scope": "no environment values or database were read",
            "phase_exit_authorized": False,
        }, sort_keys=True))
        return 2
    try:
        import psycopg
        report = collect_runtime(os.environ, cast(Connect, psycopg.connect))
    except ImportError:
        report = _base_report()
        report["error"] = "preflight_unavailable"
        print(json.dumps(report, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if system_rollout_ready(report) else 2


if __name__ == "__main__":
    raise SystemExit(main())
