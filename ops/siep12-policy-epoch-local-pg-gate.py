#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only DB acceptance for SIEP-12's monotonic policy epoch."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from pathlib import Path

from rule_projection_fixture import seed_reviewed_rule_projection as seed_rule_projection
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
    return {1: 'sha256:7cc2feacec82bf7cce2af9af309dc4ae9426922003471703af010f6728957190', 2: 'sha256:6dc9f8353712e0f9ee9dcbc96d05b802631e420cfa698944c2b6401a11c6a9ff', 3: 'sha256:ace611250aaf2ffd7b96ca3195d20e9dc2697bbea555eadc7c661407e838cae7', 4: 'sha256:32b9edaf4e718cfa55f87cc6b650f97480a09f00fbde35c10f1e5e23bc6eaa4c', 5: 'sha256:314a4b108eaded1a00b7f604af93d44838b615fab79820d1a2c9d9d4b08fa6ca', 6: 'sha256:afd27c13b68423dcaaeafc68c0c1ddc018452ef8d9e7cad5bb32cac4327d80d7', 7: 'sha256:405a1bd7aa421b16ebd3d3fe7422a2525826dba563dffeb2dad5a6b90d12859b', 8: 'sha256:56edcf25393a1c7b47985b9e2a272a8e7fcf0930709147ae823ca9b15290212e', 9: 'sha256:398ddf5f86a46110518f7498af63782b65e047b9f624b798bf3c798058a79b4d', 10: 'sha256:9109437968d4ab76090980ff9ec370da1ccfbb65e0683f485e0239d3d5158a62'}[version]


def seed_reviewed_rule_projection(cur) -> None:
    seed_rule_projection(cur, slug="siep12-epoch-fixture", title="SIEP-12 epoch fixture",
                         rule_label="SIEP-12 reviewed projection fixture", uuid_tail="000000000001")


# --- Scenarios --------------------------------------------------------------
#
# WR-000258: every status call and every deferred-constraint flush below
# re-validates the whole sealed SCAC registry chain (116 successors, ~240k
# entries), roughly 14 seconds each on a CI runner, and that is where all of this
# gate's wall time goes. The assertions are independent once the reviewed rule
# projection is seeded, and each one restores the seeded baseline (savepoint
# rollback, deferred constraint mode) before the next, so they can be spread over
# disposable database copies without dropping or loosening any of them.
#
# The default (no CARR_GATE_SHARD) runs every scenario in one transaction against
# one seed, exactly as this gate always did. ops/ci-migration-gates.py sets
# CARR_GATE_SHARD=<index>/<count> and hands each shard its own copy of the
# isolated database; the shards together must run every declared scenario, which
# the runner verifies from the gate-scenarios-declared / gate-scenario-ran lines.


def scenario_registry_and_ledger(cur, ctx) -> None:
    expected_v1 = ctx["expected_v1"]
    expected_v2 = ctx["expected_v2"]
    migration_sha = ctx["migration_sha"]
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


def seed_baseline(cur, ctx) -> None:
    """Seed the reviewed rule projection and bootstrap epoch 1; leaves constraints deferred."""
    seed_reviewed_rule_projection(cur)
    current = cur.execute("select epoch,epoch_digest,schema_highest_migration from ops.scac_policy_epoch").fetchone()
    highest_schema = cur.execute("select max(filename collate \"C\") from public.schema_migrations").fetchone()[0]
    if current is None or current[0] != 1 or current[2] != highest_schema:
        raise RuntimeError(f"reviewed rule projection did not bootstrap exact epoch 1 at current schema: {current!r}")
    ctx["current"] = current


def scenario_accepted_token(cur, ctx) -> None:
    current = ctx["current"]
    accepted = cur.execute(
        "select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])
    ).fetchone()[0]
    if accepted["epoch_state"] != "current" or accepted["compatibility_state"] != "compatible" or accepted["reason_id"] is not None or accepted["compatibility_authority"] != "fact_only_not_enforcement":
        raise RuntimeError(f"exact current token was not compatible: {accepted!r}")


def scenario_doctrine_projection_drift(cur, ctx) -> None:
    current = ctx["current"]
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


def scenario_deferred_activation_refusals(cur, ctx) -> None:
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


def scenario_live_catalog_grant_drift(cur, ctx) -> None:
    current = ctx["current"]
    cur.execute("savepoint live_catalog_drift")
    cur.execute("grant insert on ops.scac_policy_epoch to carr_reader")
    drifted = cur.execute(
        "select ops.scac_policy_epoch_status(%s,%s)", (current[0], current[1])
    ).fetchone()[0]
    if drifted["epoch_state"] is not None or drifted["compatibility_state"] != "incompatible":
        raise RuntimeError(f"live DB grant drift did not fail closed: {drifted!r}")
    cur.execute("rollback to savepoint live_catalog_drift")
    cur.execute("release savepoint live_catalog_drift")


def scenario_role_authority_drift(cur, ctx) -> None:
    current = ctx["current"]
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


def scenario_epoch_token_refusals(cur, ctx) -> None:
    current = ctx["current"]
    for epoch, digest, reason in (
        (current[0] - 1, current[1], None),
        (current[0] + 1, current[1], "future"),
        (current[0], "sha256:" + "0" * 64, "rolled_back"),
    ):
        observed = cur.execute("select ops.scac_policy_epoch_status(%s,%s)", (epoch, digest)).fetchone()[0]
        if observed["epoch_state"] != reason or observed["compatibility_state"] != "incompatible":
            raise RuntimeError(f"epoch refusal mismatch for {reason}: {observed!r}")


def scenario_monotonic_change(cur, ctx) -> None:
    current = ctx["current"]
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


def scenario_runtime_role_privileges(cur, ctx) -> None:
    # Grants the runtime bundles SET to the session and never undoes them, so it
    # must be the last scenario of any session that runs it (it is last in
    # SEEDED_SCENARIOS and sessions run their scenarios in that order).
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


# (name, function, cost in seconds on a CI runner, from PR 1708's measured gate
# time). The cost only balances shards; it is never an assertion.
STATIC_SCENARIO = ("registry_and_ledger", scenario_registry_and_ledger, 30)
SEEDED_SCENARIOS = (
    ("accepted_token", scenario_accepted_token, 30),
    ("doctrine_projection_drift", scenario_doctrine_projection_drift, 60),
    ("deferred_activation_refusals", scenario_deferred_activation_refusals, 45),
    ("live_catalog_grant_drift", scenario_live_catalog_grant_drift, 30),
    ("role_authority_drift", scenario_role_authority_drift, 120),
    ("epoch_token_refusals", scenario_epoch_token_refusals, 90),
    ("monotonic_change", scenario_monotonic_change, 60),
    ("runtime_role_privileges", scenario_runtime_role_privileges, 1),
)
SEED_COST = 45
ALL_SCENARIO_NAMES = (STATIC_SCENARIO[0],) + tuple(name for name, _, _ in SEEDED_SCENARIOS)


def parse_shard(value: str | None) -> tuple[int, int]:
    if not value:
        return 0, 1
    index, _, count = value.partition("/")
    shard, shards = int(index), int(count)
    if shards < 1 or not 0 <= shard < shards:
        raise ValueError(f"invalid CARR_GATE_SHARD {value!r}")
    return shard, shards


def plan_shards(shards: int) -> list[list[str]]:
    """Deterministic longest-processing-time split; the static scenario stays in shard 0."""
    loads = [0] * shards
    seeded = [False] * shards
    plan: list[list[str]] = [[] for _ in range(shards)]
    plan[0].append(STATIC_SCENARIO[0])
    loads[0] += STATIC_SCENARIO[2]
    for name, _, cost in sorted(SEEDED_SCENARIOS, key=lambda item: (-item[2], item[0])):
        target = min(range(shards), key=lambda i: (loads[i] + (0 if seeded[i] else SEED_COST), i))
        if not seeded[target]:
            seeded[target] = True
            loads[target] += SEED_COST
        plan[target].append(name)
        loads[target] += cost
    return plan


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        shard, shards = parse_shard(os.environ.get("CARR_GATE_SHARD"))
        mine = set(plan_shards(shards)[shard]) if shards > 1 else set(ALL_SCENARIO_NAMES)
        print("gate-scenarios-declared: " + " ".join(ALL_SCENARIO_NAMES))
        ctx = {
            "expected_v1": generated_registry_digest(1),
            "expected_v2": generated_registry_digest(2),
        }
        migration = REPO / "migrations/0455_siep12_policy_epoch.sql"
        ctx["migration_sha"] = hashlib.sha256(migration.read_bytes()).hexdigest()
        started = time.monotonic()
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            if STATIC_SCENARIO[0] in mine:
                STATIC_SCENARIO[1](cur, ctx)
                print(f"gate-scenario-ran: {STATIC_SCENARIO[0]}")
            selected = [(name, fn) for name, fn, _ in SEEDED_SCENARIOS if name in mine]
            if selected:
                seed_baseline(cur, ctx)
                for name, fn in selected:
                    fn(cur, ctx)
                    print(f"gate-scenario-ran: {name}")
        elapsed = int(time.monotonic() - started)
        if shards == 1:
            print("siep12-policy-epoch-local-pg-gate passed: v1/v2 preserved; successor-aware epoch chain, stale/future/equivocation, monotonic source transition, ledger SHA, and least privilege verified")
        else:
            print(f"siep12-policy-epoch-local-pg-gate shard {shard}/{shards} passed in {elapsed}s: "
                  + ", ".join(sorted(mine, key=ALL_SCENARIO_NAMES.index)))
        return 0
    except Exception as exc:  # noqa: BLE001 - one-line CI failure
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
