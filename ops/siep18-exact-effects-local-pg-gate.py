#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only behavioral acceptance for SIEP-18 exact effects/principal binding."""

from __future__ import annotations

import json
import os
import sys
import uuid

from gate_runtime_role import rollback_only_connection


def fail(message: str) -> int:
    print(f"siep18-exact-effects-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def expect_refusal(cur, sql: str, params: tuple[object, ...], needle: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(sql, params)
    except Exception as exc:  # noqa: BLE001 - exact server refusal is asserted below
        cur.execute("rollback to savepoint expected_refusal")
        cur.execute("release savepoint expected_refusal")
        if needle not in str(exc):
            raise RuntimeError(f"wrong refusal, wanted {needle!r}: {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_refusal")
    cur.execute("release savepoint expected_refusal")
    raise RuntimeError(f"operation unexpectedly succeeded; wanted refusal {needle!r}")


def registry_entry(cur, ingress_key: str) -> tuple[str, str, list[str]]:
    row = cur.execute(
        """select registry_version,entry_digest,contract->'delegates_to'
             from ops.scac_mutation_registry_entry
            where registry_version='scac-mutation-registry.v11' and ingress_key=%s""",
        (ingress_key,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"v11 fixture ingress absent: {ingress_key}")
    return row[0], row[1], row[2]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            if cur.execute("select count(*) from ops.scac_exact_effect_contract").fetchone()[0] != 0:
                raise RuntimeError("0469 unexpectedly seeded reviewed effect authority")
            if cur.execute("select count(*) from ops.scac_trusted_principal_binding").fetchone()[0] != 0:
                raise RuntimeError("0469 unexpectedly seeded trusted-principal authority")
            for role in ("carr_writer", "carr_jobs", "carr_authority"):
                for signature in (
                    "ops.scac_register_exact_effect_contract(text,text,text,jsonb,uuid)",
                    "ops.scac_exact_effect_union(text,text)",
                    "ops.scac_bind_trusted_principal(jsonb,uuid)",
                ):
                    if cur.execute(
                        "select has_function_privilege(%s,%s,'execute')", (role, signature)
                    ).fetchone()[0]:
                        raise RuntimeError(f"0469 leaked runtime EXECUTE: {role} {signature}")

            # Provider-managed authority login roles are intentionally absent
            # from reconstructed CI databases (0273 documents that boundary).
            # Create rollback-only fixture principals and attach only the
            # existing NOLOGIN authority bundle so the behavioral proof can
            # exercise the real session_user checks without changing the
            # committed or post-gate catalog.
            for role in ("carr_authority_joe", "carr_authority_dell"):
                if cur.execute("select 1 from pg_roles where rolname=%s", (role,)).fetchone() is None:
                    cur.execute(f"create role {role} noinherit")
                    cur.execute(f"grant carr_authority to {role}")
            if cur.execute("select 1 from pg_roles where rolname='carr_writer'").fetchone() is None:
                raise RuntimeError("disposable runtime role absent: carr_writer")
            leaf_version, leaf_digest, leaf_delegates = registry_entry(cur, "mcp-tool:log-activity")
            if leaf_delegates != []:
                raise RuntimeError(f"leaf fixture unexpectedly delegates: {leaf_delegates!r}")
            outer_version, outer_digest, outer_delegates = registry_entry(cur, "mcp-tool:stamp-touch")
            if outer_delegates != ["log-activity"]:
                raise RuntimeError(f"outer registry delegate changed: {outer_delegates!r}")
            cur.execute(
                "grant execute on function ops.scac_register_exact_effect_contract(text,text,text,jsonb,uuid) to carr_authority_joe"
            )
            cur.execute(
                "grant execute on function ops.scac_exact_effect_union(text,text) to carr_authority_joe"
            )
            cur.execute("set session authorization carr_authority_joe")
            leaf_contract = {
                "schema_version": "scac-exact-effect-contract.v1",
                "ingress_key": "mcp-tool:log-activity",
                "direct_effects": [
                    {
                        "kind": "execute",
                        "function_signature": "ops.a(A)",
                    },
                    {
                        "kind": "execute",
                        "function_signature": "ops.a(a)",
                    },
                    {
                        "kind": "execute",
                        "function_signature": "ops.record_event(uuid,text)",
                    },
                    {
                        "kind": "insert",
                        "relation": "public.activity",
                        "columns": ["actor_id", "kind", "summary"],
                    }
                ],
                "delegates_to": [],
                "sql_state": "static_reviewed",
                "integration_state": "reviewed_source_test",
            }
            leaf = cur.execute(
                "select ops.scac_register_exact_effect_contract(%s,%s,%s,%s::jsonb,%s)",
                (leaf_version, "mcp-tool:log-activity", leaf_digest,
                 json.dumps(leaf_contract), str(uuid.uuid4())),
            ).fetchone()[0]
            if leaf.get("contract_state") != "reviewed_source_test" or \
               leaf.get("production_enforcement_active") is not False:
                raise RuntimeError(f"leaf registration expanded authority: {leaf!r}")

            # DB registry delegates are operation names; the exact-effect
            # contract uses full ingress keys and the registration function
            # requires the reviewed correspondence explicitly.
            outer_contract = {
                "schema_version": "scac-exact-effect-contract.v1",
                "ingress_key": "mcp-tool:stamp-touch",
                "direct_effects": [],
                "delegates_to": ["mcp-tool:log-activity"],
                "sql_state": "static_reviewed",
                "integration_state": "reviewed_source_test",
            }
            outer = cur.execute(
                "select ops.scac_register_exact_effect_contract(%s,%s,%s,%s::jsonb,%s)",
                (outer_version, "mcp-tool:stamp-touch", outer_digest,
                 json.dumps(outer_contract), str(uuid.uuid4())),
            ).fetchone()[0]
            if outer.get("contract_state") != "reviewed_source_test":
                raise RuntimeError(f"delegating registration unavailable: {outer!r}")
            exact = cur.execute(
                "select ops.scac_exact_effect_union(%s,%s)",
                (outer_version, "mcp-tool:stamp-touch"),
            ).fetchone()[0]
            if exact.get("effects") != leaf_contract["direct_effects"] or \
               exact.get("production_enforcement_active") is not False:
                raise RuntimeError(f"exact finite union drifted: {exact!r}")
            expect_refusal(
                cur, "select ops.scac_exact_effect_union(%s,%s)",
                (leaf_version, "mcp-tool:unreviewed"), "exact recursive effect union unavailable",
            )
            cur.execute("reset session authorization")

            actor_id, actor_slug, actor_kind = cur.execute(
                "select id,slug,kind from public.actor where slug='joe' and active"
            ).fetchone()
            backend_pid = cur.execute("select pg_backend_pid()").fetchone()[0]
            principal_manifest = {
                "schema_version": "scac-trusted-principal.v1",
                "organization_tenant_id": "carr-internal",
                "actor_id": str(actor_id),
                "actor_slug": actor_slug,
                "actor_kind": actor_kind,
                "human": True,
                "via": "oauth-google",
                "client_id": "siep18-db-gate",
                "sponsoring_human_slug": "joe",
                "native_agent_verified": False,
                "authority_sponsor_slug": None,
                "authorization_class": "verified_partner",
                "session_principal": "carr_writer",
                "privilege_bundle": "carr_writer",
                "backend_pid": backend_pid,
            }
            principal_digest = cur.execute(
                "select ops.scac_reference_monitor_sha256(%s::jsonb)",
                (json.dumps(principal_manifest),),
            ).fetchone()[0]
            principal = {
                **principal_manifest,
                "principal_digest": principal_digest,
                "source": "server_authenticated_actor_plus_database_readback",
                "production_enforcement_active": False,
            }
            cur.execute(
                "grant execute on function ops.scac_bind_trusted_principal(jsonb,uuid) to carr_writer"
            )
            cur.execute("set session authorization carr_writer")
            bound = cur.execute(
                "select ops.scac_bind_trusted_principal(%s::jsonb,%s)",
                (json.dumps(principal), str(uuid.uuid4())),
            ).fetchone()[0]
            if bound.get("binding_state") != "current_source_test" or \
               bound.get("production_enforcement_active") is not False:
                raise RuntimeError(f"trusted principal binding expanded authority: {bound!r}")
            expect_refusal(
                cur, "select ops.scac_bind_trusted_principal(%s::jsonb,%s)",
                (json.dumps({**principal, "actor_slug": "dell"}), str(uuid.uuid4())),
                "trusted principal digest mismatch",
            )
            cur.execute("reset session authorization")

            def signed(server_manifest: dict[str, object]) -> dict[str, object]:
                digest = cur.execute(
                    "select ops.scac_reference_monitor_sha256(%s::jsonb)",
                    (json.dumps(server_manifest),),
                ).fetchone()[0]
                return {**server_manifest, "principal_digest": digest,
                        "source": "server_authenticated_actor_plus_database_readback",
                        "production_enforcement_active": False}

            automation_id, automation_kind = cur.execute(
                "select id,kind from public.actor where slug='codex' and active"
            ).fetchone()
            forged_automation = signed({**principal_manifest,
                "actor_id": str(automation_id), "actor_slug": "codex",
                "actor_kind": automation_kind, "human": True,
                "authorization_class": "verified_partner"})
            cur.execute("set session authorization carr_writer")
            expect_refusal(
                cur, "select ops.scac_bind_trusted_principal(%s::jsonb,%s)",
                (json.dumps(forged_automation), str(uuid.uuid4())),
                "actor kind, sponsor, or authority",
            )
            cur.execute("reset session authorization")

            cur.execute(
                "grant execute on function ops.scac_bind_trusted_principal(jsonb,uuid) to carr_authority_dell"
            )
            wrong_partner_manifest = {**principal_manifest,
                "session_principal": "carr_authority_dell",
                "privilege_bundle": "carr_authority",
                "sponsoring_human_slug": "dell", "authority_sponsor_slug": "dell"}
            wrong_partner = signed(wrong_partner_manifest)
            cur.execute("set session authorization carr_authority_dell")
            expect_refusal(
                cur, "select ops.scac_bind_trusted_principal(%s::jsonb,%s)",
                (json.dumps(wrong_partner), str(uuid.uuid4())),
                "actor kind, sponsor, or authority",
            )
            cur.execute("reset session authorization")

            null_authority_manifest = {**principal_manifest,
                "session_principal": "carr_authority_dell",
                "privilege_bundle": "carr_authority",
                "sponsoring_human_slug": None, "authority_sponsor_slug": None}
            null_authority = signed(null_authority_manifest)
            cur.execute("set session authorization carr_authority_dell")
            expect_refusal(
                cur, "select ops.scac_bind_trusted_principal(%s::jsonb,%s)",
                (json.dumps(null_authority), str(uuid.uuid4())),
                "actor kind, sponsor, or authority",
            )
            cur.execute("reset session authorization")
    except Exception as exc:  # noqa: BLE001 - gate reports the exact refusal
        return fail(str(exc))
    print(
        "siep18-exact-effects-local-pg-gate passed: no seeded/granted authority, "
        "mixed finite effect union, missing-contract refusal, and actor/session/authority binding"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
