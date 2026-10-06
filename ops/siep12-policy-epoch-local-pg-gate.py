#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only DB acceptance for SIEP-12's monotonic policy epoch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role
from scac_mutation_db_inventory import project, project_escalation, project_role_authority, summarize

REPO = Path(__file__).resolve().parents[1]


def fail(message: str) -> int:
    print(f"siep12-policy-epoch-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refusal(cur, query: str, params: tuple = (), fragment: str = "permission denied") -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(query, params)
    except Exception as exc:  # noqa: BLE001 - the exception is the asserted boundary
        cur.execute("rollback to savepoint expected_refusal")
        cur.execute("release savepoint expected_refusal")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_refusal")
    cur.execute("release savepoint expected_refusal")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


def deferred_refusal(cur, mutation: str, fragment: str) -> None:
    cur.execute("savepoint expected_deferred_refusal")
    try:
        cur.execute(mutation)
        cur.execute("set constraints all immediate")
    except Exception as exc:  # noqa: BLE001 - the deferred refusal is the assertion
        cur.execute("rollback to savepoint expected_deferred_refusal")
        cur.execute("release savepoint expected_deferred_refusal")
        cur.execute("set constraints all deferred")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(f"expected deferred refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_deferred_refusal")
    cur.execute("release savepoint expected_deferred_refusal")
    cur.execute("set constraints all deferred")
    raise RuntimeError(f"expected deferred refusal containing {fragment!r}")


def generated_registry_digest(version: int) -> str:
    from registry_chain import registry_chain
    return registry_chain()['versions'][version-1]['digest']


def uuid_for(short: str) -> str:
    return f"{short}-0000-4000-8000-000000000001"


def seed_reviewed_rule_projection(cur) -> None:
    raw = (REPO / "ops/config/rule-enforcement-map.json").read_bytes()
    reviewed = json.loads(raw)
    map_digest = hashlib.sha256(raw).hexdigest()
    scope_by_short = {
        short: scope
        for scope, short_ids in reviewed["active_rule_ids"].items()
        for short in short_ids
    }
    cur.execute(
        """insert into public.actor(slug,kind,display_name) values ('joe','human','Joe')
             on conflict(slug) do update set display_name=excluded.display_name
             returning id"""
    )
    joe = cur.fetchone()[0]
    document_id = cur.execute(
        """insert into public.doctrine_document(slug,title,content_class,created_by)
             values ('siep12-epoch-fixture','SIEP-12 epoch fixture','reference',%s)
             returning id""",
        (joe,),
    ).fetchone()[0]
    generation = cur.execute("select generation from public.doctrine_meta where id=1").fetchone()[0]
    cur.execute(
        """insert into public.doctrine_snapshot(document_id,generation,snapshot_json,content_hash)
             values (%s,%s,%s::jsonb,%s)""",
        (document_id, generation, json.dumps({"document": {"slug": "siep12-epoch-fixture"}, "sections": []}),
         hashlib.sha256(b"siep12-epoch-fixture").hexdigest()),
    )
    cur.execute("alter table public.rule disable trigger user")
    try:
        for short, scope in sorted(scope_by_short.items()):
            cur.execute(
                """insert into public.rule(id,statement,taught_by,status,activated_by,personal_to)
                     values (%s,%s,%s,'active',%s,%s)""",
                (uuid_for(short), f"SIEP-12 reviewed projection fixture {short}", joe, joe,
                 joe if scope == "joe" else None),
            )
    finally:
        cur.execute("alter table public.rule enable trigger user")
    for pack, contract in sorted(reviewed["rule_packs"].items()):
        cur.execute(
            """insert into ops.rule_pack(pack,title,description,triggers,source)
                 values (%s,%s,%s,%s,%s)""",
            (pack, contract["title"], contract["description"], contract["triggers"],
             "ops/config/rule-enforcement-map.json"),
        )
    for short, contract in sorted(reviewed["rule_load_layers"].items()):
        cur.execute(
            """insert into ops.rule_load_layer
                 (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                 values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (uuid_for(short), short, contract["load_layer"], contract.get("packs", []),
             scope_by_short[short], contract.get("why"),
             "ops/config/rule-enforcement-map.json", map_digest),
        )
    cur.execute("set constraints all immediate")
    cur.execute("set constraints all deferred")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        expected_v1 = generated_registry_digest(1)
        expected_v2 = generated_registry_digest(2)
        migration = REPO / "migrations/0455_siep12_policy_epoch.sql"
        migration_sha = hashlib.sha256(migration.read_bytes()).hexdigest()
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            v1 = cur.execute(
                "select registry_digest,entry_count from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1'"
            ).fetchone()
            if v1 != (expected_v1, 1387):
                raise RuntimeError(f"sealed v1 changed: {v1!r}")
            v2 = cur.execute(
                """select registry_digest,entry_count,source_entry_count,catalog_projection,
                          runtime_projection_authorizing,atomic_database_mediation_operational,
                          direct_database_grant_cutover,production_enforcement_active
                     from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v2'"""
            ).fetchone()
            if v2 is None or v2[0] != expected_v2 or any(v2[4:]):
                raise RuntimeError(f"v2 registry is missing, drifted, or authority-expanding: {v2!r}")
            current_projection = cur.execute(
                "select catalog_projection from ops.scac_mutation_registry_version order by regexp_replace(registry_version,'^.*[.]v','','')::integer desc limit 1"
            ).fetchone()[0]
            catalog = summarize(project(cur))
            role_authority = project_role_authority(cur)
            if {key: role_authority[key] for key in ("count", "digest")} != current_projection["role_authority"]:
                raise RuntimeError(f"live DB role authority differs from current sealed registry: {role_authority!r}")
            expected_catalog = {
                "categories": {
                    "secdef_execute": current_projection["secdef_execute"],
                    "relation_dml": current_projection["relation_dml"],
                    "column_dml": current_projection["column_dml"],
                    "job_definitions": {
                        "count": 26,
                        "digest": "sha256:152742893824c64275a99326335f2b8ca97cf592153c5cb280b353adfa15eb91",
                    },
                },
                "combined": catalog["combined"],
            }
            if catalog["categories"] != expected_catalog["categories"]:
                raise RuntimeError(f"live DB capability census differs from current sealed registry: {catalog!r}")
            lookup = cur.execute(
                "select ops.scac_mutation_registration_v2(%s,'mcp-tool:standing-context')", (expected_v2,)
            ).fetchone()[0]
            if not lookup["registered"] or lookup["registry_version"] != "scac-mutation-registry.v2":
                v2_integrity = cur.execute(
                    """select v.entry_count,count(e.*),v.entry_set_digest,
                              'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(e.entry_digest,',' order by e.ingress_key collate "C"),''),'UTF8'),'sha256'),'hex'),
                              coalesce(bool_or(e.entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(e.contract),'UTF8'),'sha256'),'hex')),false)
                         from ops.scac_mutation_registry_version v
                         left join ops.scac_mutation_registry_entry e on e.registry_version=v.registry_version
                        where v.registry_version='scac-mutation-registry.v2'
                        group by v.entry_count,v.entry_set_digest"""
                ).fetchone()
                raise RuntimeError(f"exact v2 lookup refused the sealed registry: {lookup!r}; integrity={v2_integrity!r}")
            downgrade = cur.execute(
                "select ops.scac_mutation_registration_v2(%s,'mcp-tool:standing-context')", (expected_v1,)
            ).fetchone()[0]
            if downgrade["registered"] or downgrade["reason"] != "digest_mismatch":
                raise RuntimeError("v2 lookup accepted a v1 downgrade")
            historical_signature = cur.execute(
                "select ops.scac_mutation_registration(%s,'mcp-tool:standing-context')", (expected_v1,)
            ).fetchone()[0]
            if not historical_signature["registered"] or historical_signature["registry_version"] != "scac-mutation-registry.v1":
                raise RuntimeError("owner-only historical v1 audit lookup is unavailable")

            ledger = cur.execute(
                "select sha256 from public.schema_migrations where filename='0455_siep12_policy_epoch.sql'"
            ).fetchone()
            if ledger != (migration_sha,):
                raise RuntimeError(f"0455 ledger SHA is not exact: {ledger!r}")
            current = cur.execute("select epoch,epoch_digest,schema_highest_migration from ops.scac_policy_epoch").fetchone()
            if current is not None:
                raise RuntimeError(f"empty reconstructed policy was incorrectly blessed: {current!r}")
            unavailable = cur.execute(
                "select ops.scac_policy_epoch_status(1,%s)", ("sha256:" + "0" * 64,)
            ).fetchone()[0]
            if unavailable["epoch_state"] is not None or unavailable["compatibility_state"] != "incompatible":
                raise RuntimeError(f"empty reconstructed policy did not fail closed: {unavailable!r}")
            seed_reviewed_rule_projection(cur)
            current = cur.execute("select epoch,epoch_digest,schema_highest_migration from ops.scac_policy_epoch").fetchone()
            highest_schema = cur.execute("select max(filename collate \"C\") from public.schema_migrations").fetchone()[0]
            if current is None or current[0] != 1 or current[2] != highest_schema:
                raise RuntimeError(f"reviewed rule projection did not bootstrap exact epoch 1 at current schema: {current!r}")
            accepted = cur.execute(
                "select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])
            ).fetchone()[0]
            if accepted["epoch_state"] != "current" or accepted["compatibility_state"] != "compatible" or accepted["reason_id"] is not None or accepted["compatibility_authority"] != "fact_only_not_enforcement":
                raise RuntimeError(f"exact current token was not compatible: {accepted!r}")
            cur.execute("savepoint doctrine_projection_drift")
            set_local_role(cur, "carr_writer")
            cur.execute(
                """update public.doctrine_snapshot
                      set snapshot_json=jsonb_set(snapshot_json,'{tampered}','true'::jsonb,true)
                    where snapshot_json->'document'->>'slug'='siep12-epoch-fixture'"""
            )
            doctrine_drift = cur.execute(
                "select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])
            ).fetchone()[0]
            cur.execute("reset role")
            if doctrine_drift["epoch_state"] is not None or doctrine_drift["compatibility_state"] != "incompatible":
                raise RuntimeError(f"direct writer doctrine mutation remained compatible: {doctrine_drift!r}")
            cur.execute("set constraints all immediate")
            if cur.execute("select max(epoch) from ops.scac_policy_epoch").fetchone()[0] != 2:
                raise RuntimeError("doctrine projection mutation did not append one epoch")
            cur.execute("rollback to savepoint doctrine_projection_drift")
            cur.execute("release savepoint doctrine_projection_drift")
            cur.execute("set constraints all deferred")
            deferred_refusal(
                cur,
                "delete from ops.rule_load_layer where rule_id=(select rule_id from ops.rule_load_layer order by rule_id limit 1)",
                "activation-safe",
            )
            deferred_refusal(
                cur,
                "delete from ops.rule_delivery_policy where singleton",
                "policy singleton",
            )
            deferred_refusal(
                cur,
                "update ops.rule_load_layer set scope='joe' where scope='shared' and rule_id=(select rule_id from ops.rule_load_layer where scope='shared' order by rule_id limit 1)",
                "activation-safe",
            )
            cur.execute("savepoint live_catalog_drift")
            cur.execute("grant insert on ops.scac_policy_epoch to carr_reader")
            drifted = cur.execute(
                "select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])
            ).fetchone()[0]
            if drifted["epoch_state"] is not None or drifted["compatibility_state"] != "incompatible":
                raise RuntimeError(f"live DB grant drift did not fail closed: {drifted!r}")
            cur.execute("rollback to savepoint live_catalog_drift")
            cur.execute("release savepoint live_catalog_drift")
            for mutation in (
                "grant carr_writer to carr_reader",
                "alter role carr_reader bypassrls",
                "grant insert on ops.scac_policy_epoch to carr_authority_joe",
                "grant execute on function ops.scac_policy_epoch_refresh() to carr_authority_joe",
            ):
                cur.execute("savepoint role_authority_drift")
                cur.execute(mutation)
                drifted = cur.execute(
                    "select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])
                ).fetchone()[0]
                if drifted["epoch_state"] is not None or drifted["compatibility_state"] != "incompatible":
                    raise RuntimeError(f"role-authority drift did not fail closed for {mutation}: {drifted!r}")
                cur.execute("rollback to savepoint role_authority_drift")
                cur.execute("release savepoint role_authority_drift")
            for epoch, digest, reason in (
                (current[0] - 1, current[1], None),
                (current[0] + 1, current[1], "future"),
                (current[0], "sha256:" + "0" * 64, "rolled_back"),
            ):
                observed = cur.execute("select ops.scac_policy_epoch_status(%s,%s)", (epoch, digest)).fetchone()[0]
                if observed["epoch_state"] != reason or observed["compatibility_state"] != "incompatible":
                    raise RuntimeError(f"epoch refusal mismatch for {reason}: {observed!r}")

            cur.execute("savepoint monotonic_change")
            prior_generation = cur.execute("select generation from public.doctrine_meta where id=1").fetchone()[0]
            cur.execute("update public.doctrine_meta set generation=generation+1,updated_at=clock_timestamp() where id=1")
            cur.execute("set constraints all immediate")
            advanced = cur.execute("select epoch,previous_epoch,doctrine_generation from ops.scac_policy_epoch order by epoch desc limit 1").fetchone()
            if advanced != (2, 1, prior_generation + 1):
                raise RuntimeError(f"canonical source change did not append exact N+1: {advanced!r}")
            stale = cur.execute("select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])).fetchone()[0]
            if stale["epoch_state"] != "stale" or stale["compatibility_state"] != "incompatible":
                raise RuntimeError(f"prior token did not become stale: {stale!r}")
            cur.execute("rollback to savepoint monotonic_change")
            cur.execute("set constraints all deferred")

            grant_settable_runtime_roles(cur, "carr_reader", "carr_writer", "carr_jobs", "carr_authority")
            for role in ("carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                set_local_role(cur, role)
                refusal(cur, "insert into ops.scac_policy_epoch(epoch) values (999)")
                refusal(cur, "update ops.scac_policy_epoch set epoch=999")
                refusal(cur, "delete from ops.scac_policy_epoch")
                refusal(cur, "truncate ops.scac_policy_epoch")
                cur.execute("reset role")
            rights = cur.execute(
                """select count(*) from information_schema.role_table_grants
                    where table_schema='ops' and table_name='scac_policy_epoch'
                      and grantee in ('carr_reader','carr_writer','carr_jobs','carr_authority')"""
            ).fetchone()[0]
            if rights:
                raise RuntimeError("runtime role gained raw policy epoch table privileges")
        print("siep12-policy-epoch-local-pg-gate passed: v1/v2 preserved; successor-aware epoch chain, stale/future/equivocation, monotonic source transition, ledger SHA, and least privilege verified")
        return 0
    except Exception as exc:  # noqa: BLE001 - one-line CI failure
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
