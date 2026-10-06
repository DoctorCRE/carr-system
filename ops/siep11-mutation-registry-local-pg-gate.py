#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only DB acceptance for SIEP-11's immutable ingress registry."""

from __future__ import annotations

import os
import json
import sys
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role
from scac_mutation_db_inventory import project, summarize
from registry_chain import registry_chain

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.control_plane_scheduler_cutover import scheduler_launchd_rows  # noqa: E402


JOB_DEFINITION_CATALOG = {
    "count": 26,
    "digest": "sha256:152742893824c64275a99326335f2b8ca97cf592153c5cb280b353adfa15eb91",
}

def fail(message: str) -> int:
    print(f"siep11-mutation-registry-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def validate_incident_work_request_same_key_serialization(dsn: str) -> None:
    """The WR69 handler takes this lock before withEnvelope reads replay state.

    The JS unit test proves that call order. This disposable-Postgres check
    proves that two real transactions using the same key cannot cross that
    boundary together.
    """
    uppercase_input = "A0B0C0D0-E0F0-4A00-8B00-C00000000069"
    key = uppercase_input.lower()
    case_variant = uppercase_input.lower()  # the handler's UUID normalization
    second_started = threading.Event()

    def acquire_second() -> bool:
        with psycopg.connect(dsn, autocommit=False) as second:
            second_started.set()
            second.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s,0))", (case_variant,)
            )
            second.rollback()
        return True

    with psycopg.connect(dsn, autocommit=False) as first:
        first.execute("select pg_advisory_xact_lock(hashtextextended(%s,0))", (key,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(acquire_second)
            if not second_started.wait(timeout=2):
                raise RuntimeError("second same-key transaction did not start")
            if waiting.done():
                raise RuntimeError("same-key transaction crossed the pre-envelope lock")
            first.rollback()
            if waiting.result(timeout=5) is not True:
                raise RuntimeError("same-key transaction did not continue after lock release")


def refusal(cur, query: str, params: tuple, fragment: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(query, params)
    except Exception as exc:  # noqa: BLE001 - the refusal is the assertion
        cur.execute("rollback to savepoint expected_refusal")
        cur.execute("release savepoint expected_refusal")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_refusal")
    cur.execute("release savepoint expected_refusal")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


def sealed_service_launchd(registry_version: str) -> list[tuple[str, str, str]]:
    """Check live source closure and read the selected seal's frozen catalog.

    Decision 05e144eb exempts launchd source edits from registry resealing.
    Comparing an immutable database snapshot with today's catalog would demand
    a successor anyway. Keep exact DB parity against its independent fixture;
    workflowDefinitionInventory still refuses missing/duplicate live owners.
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", """
        import { frozenInventory, workflowDefinitionInventory }
          from './ops/scac-mutation-inventory.mjs';
        workflowDefinitionInventory();
        const mappings = frozenInventory(process.argv[1]).flatMap(row =>
          (row.physical_authority_refs || []).filter(ref =>
            ref.startsWith('ops.service_environment:')).map(ref => {
              const parts = ref.split(':');
              if (parts.length !== 3 || !parts[1] || !parts[2])
                throw new Error('malformed frozen service authority');
              return [parts[1], parts[2], row.source_locator];
            }));
        process.stdout.write(JSON.stringify(mappings));
        """, registry_version],
        cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
    )
    rows = json.loads(result.stdout)
    if not isinstance(rows, list) or not rows or any(
        not isinstance(row, list) or len(row) != 3
        or any(not isinstance(value, str) or not value for value in row)
        for row in rows
    ):
        raise ValueError("malformed sealed launchd service catalog")
    mappings = sorted(tuple(row) for row in rows)
    if len(mappings) != len(set(mappings)):
        raise ValueError("duplicate sealed launchd service mapping")
    return mappings


def validate_launchd_authority_refs(
    cur, registry_version: str, expected_launchd: list[tuple],
    expected_service_launchd: list[tuple[str, str, str]],
    expected_sealed_service_launchd: list[tuple[str, str, str]],
) -> None:
    authority_refs = cur.execute(
        """select e.ingress_key,e.contract->>'source_locator',ref.value
             from ops.scac_mutation_registry_entry e
             cross join lateral jsonb_array_elements_text(e.contract->'physical_authority_refs') ref(value)
            where e.ingress_kind='workflow_entrypoint'
              and e.registry_version=%s
              and e.contract ? 'physical_authority_refs'
            order by e.ingress_key,ref.value""",
        (registry_version,),
    ).fetchall()
    service_refs = [row for row in authority_refs if row[2].startswith("ops.service_environment:")]
    legacy_refs = [row for row in authority_refs if row[2].startswith("ops.legacy_schedule_launchd_contract:")]
    if len(service_refs) != len(expected_sealed_service_launchd) or \
       len(legacy_refs) != len(expected_launchd):
        raise RuntimeError(f"unexpected launchd physical authority reference counts {authority_refs!r}")
    actual_service_launchd = [tuple(row) for row in cur.execute(
        """select s.key,se.environment,se.deploy_mechanism,s.retired_at is not null
             from ops.service s join ops.service_environment se on se.service_id=s.id
            where se.deploy_mechanism like 'ops/launchd/%.plist'
            order by s.key,se.environment,se.deploy_mechanism"""
    ).fetchall()]
    expected_active_service_launchd = [(*row, False) for row in expected_service_launchd]
    if actual_service_launchd != expected_active_service_launchd:
        raise RuntimeError("launchd service environments do not exactly match the active checked-in catalog")
    expected_service_refs = sorted(
        (path, f"ops.service_environment:{service_key}:{environment}")
        for service_key, environment, path in expected_sealed_service_launchd
    )
    if sorted((source_locator, ref) for _, source_locator, ref in service_refs) != expected_service_refs:
        raise RuntimeError("launchd service authority refs do not exactly cover sealed service environments")
    actual_launchd = [tuple(row) for row in cur.execute(
        """select surface_id,workflow_key,workflow_version,locator,repo_plist_relpath,
                  installed_plist_name,program_arguments,plist_sha256,schedule_sha256,timezone
             from ops.legacy_schedule_launchd_contract order by surface_id"""
    ).fetchall()]
    if actual_launchd != expected_launchd:
        raise RuntimeError("launchd legacy contracts do not exactly match checked-in paths, labels, arguments, or digests")
    expected_legacy_refs = sorted(f"ops.legacy_schedule_launchd_contract:{row[0]}" for row in expected_launchd)
    if sorted(row[2] for row in legacy_refs) != expected_legacy_refs:
        raise RuntimeError("launchd legacy authority refs do not exactly cover the checked-in native contracts")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        validate_incident_work_request_same_key_serialization(dsn)
        registry = json.loads((REPO / "ops/config/control-plane-scheduler-cutover.v1.json").read_text(encoding="utf-8"))
        manifest = json.loads((REPO / "ops/config/control-plane-workflows.v1.json").read_text(encoding="utf-8"))
        services = json.loads((REPO / "ops/config/services.json").read_text(encoding="utf-8"))
        expected_service_launchd = sorted(
            (str(service["key"]), str(environment["environment"]), str(environment["deploy_mechanism"]))
            for service in services["services"]
            for environment in service.get("environments", [])
            if isinstance(environment.get("deploy_mechanism"), str)
            and environment["deploy_mechanism"].startswith("ops/launchd/")
            and environment["deploy_mechanism"].endswith(".plist")
        )
        expected_launchd = sorted(
            (row[2], row[0], row[1], row[3], row[4], row[5], json.loads(row[6]), row[7], row[8], row[9])
            for row in scheduler_launchd_rows(registry, manifest=manifest, repo=REPO)
        )
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            catalog = summarize(project(cur))
            successor = cur.execute(
                """select registry_version,catalog_projection from ops.scac_mutation_registry_version
                    where registry_version>'scac-mutation-registry.v1'
                    order by regexp_replace(registry_version,'^.*[.]v','','')::integer desc limit 1"""
            ).fetchone()
            sealed_projection = successor[1] if successor is not None else cur.execute(
                "select catalog_projection from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1'"
            ).fetchone()[0]
            expected_categories = {
                "secdef_execute": sealed_projection["secdef_execute"],
                "relation_dml": sealed_projection["relation_dml"],
                "column_dml": sealed_projection["column_dml"],
                "job_definitions": JOB_DEFINITION_CATALOG,
            }
            if catalog["categories"] != expected_categories:
                raise RuntimeError(f"fresh DB mutation catalog drifted: {catalog!r}")
            version = cur.execute(
                """select registry_digest,entry_count,mcp_default_deny_source_guarded,
                          db_metadata_authority,runtime_projection_authorizing,
                          non_mcp_default_deny_operational,atomic_database_mediation_operational,
                          direct_database_grant_cutover,production_enforcement_active
                     from ops.scac_mutation_registry_version
                    where registry_version='scac-mutation-registry.v1'"""
            ).fetchone()
            digest = version[0]
            runtime_version = successor[0] if successor is not None else "scac-mutation-registry.v1"
            expected_sealed_service_launchd = sealed_service_launchd(runtime_version)
            if runtime_version not in {row["version"] for row in registry_chain()["versions"][1:]}:
                raise RuntimeError(f"unsupported live successor {runtime_version!r}")
            # A successor may only ADD a seal. Whatever version is live, the one
            # immediately below it must still be present AND still validate its
            # own entry-set seal -- that is what makes v22 sealed HISTORY under
            # v26 rather than a row the successor quietly rewrote. The allowlist
            # above stays enumerated so an unreviewed frontier fails closed; this
            # predecessor check is derived from whichever member is live.
            runtime_ordinal = int(runtime_version.rsplit(".v", 1)[1])
            if runtime_ordinal > 1:
                predecessor_version = f"scac-mutation-registry.v{runtime_ordinal - 1}"
                if cur.execute(
                    "select count(*) from ops.scac_mutation_registry_version where registry_version=%s",
                    (predecessor_version,),
                ).fetchone()[0] != 1:
                    raise RuntimeError(
                        f"sealed predecessor {predecessor_version} is absent under live {runtime_version}"
                    )
                # Per-version seal-availability functions begin at v5, so only ask
                # the database to revalidate a predecessor that actually has one.
                if runtime_ordinal >= 6 and cur.execute(
                    f"select ops.scac_mutation_registry_v{runtime_ordinal - 1}_seal_available()"
                ).fetchone()[0] is not True:
                    raise RuntimeError(
                        f"sealed predecessor {predecessor_version} no longer validates its entry set"
                    )
            lookup_function = f"ops.scac_mutation_registration_{runtime_version.rsplit('.', 1)[1]}"
            runtime_digest = cur.execute(
                "select registry_digest from ops.scac_mutation_registry_version where registry_version=%s",
                (runtime_version,),
            ).fetchone()[0]
            historical = cur.execute(
                "select ops.scac_mutation_registration(%s,%s)", (digest, "mcp-tool:add-loop")
            ).fetchone()[0]
            if historical.get("registered") is not True or historical.get("registry_version") != "scac-mutation-registry.v1":
                raise RuntimeError("owner-only historical v1 audit lookup is unavailable")
            if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
                raise RuntimeError(f"malformed sealed registry digest {digest!r}")
            if version[1:] != (1387, True, True, False, False, False, False, False):
                raise RuntimeError(f"unexpected sealed registry version {version!r}")

            counts = dict(cur.execute(
                "select ingress_kind,count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v1' group by ingress_kind"
            ))
            if counts != {"mcp_tool": 220, "script_entrypoint": 485,
                          "worker_route": 6, "worker_sidewrite": 3,
                          "external_admin": 28, "break_glass": 2,
                          "job_definition": 26, "workflow_entrypoint": 30, "db_function_acl": 290,
                          "db_relation_acl": 285, "db_column_acl": 12}:
                raise RuntimeError(f"unexpected ingress census {counts!r}")
            if cur.execute(
                """select count(*) from ops.scac_mutation_registry_entry
                    where registry_version='scac-mutation-registry.v1'
                      and (contract->>'owner_package'<>'11'
                       or (contract->>'classification_authorizing')::boolean
                       or entry_digest !~ '^sha256:[0-9a-f]{64}$')"""
            ).fetchone()[0]:
                raise RuntimeError("registry contains authority-expanding or malformed rows")

            validate_launchd_authority_refs(
                cur, runtime_version, expected_launchd, expected_service_launchd,
                expected_sealed_service_launchd
            )

            cur.execute("savepoint wrong_service_path_probe")
            cur.execute(
                """update ops.service_environment se set deploy_mechanism='ops/launchd/wrong.plist'
                     from ops.service s where s.id=se.service_id and s.key='rules-refresh'
                       and se.deploy_mechanism='ops/launchd/com.carr.rules-refresh.plist'"""
            )
            try:
                validate_launchd_authority_refs(
                    cur, runtime_version, expected_launchd, expected_service_launchd,
                    expected_sealed_service_launchd
                )
            except RuntimeError:
                pass
            else:
                raise RuntimeError("wrong service launchd path did not fail exact authority parity")
            cur.execute("rollback to savepoint wrong_service_path_probe")
            cur.execute("release savepoint wrong_service_path_probe")

            cur.execute("savepoint wrong_service_environment_probe")
            cur.execute(
                """update ops.service_environment se set environment='local'
                     from ops.service s where s.id=se.service_id and s.key='rules-refresh'
                       and se.environment='production'"""
            )
            try:
                validate_launchd_authority_refs(
                    cur, runtime_version, expected_launchd, expected_service_launchd,
                    expected_sealed_service_launchd
                )
            except RuntimeError:
                pass
            else:
                raise RuntimeError("wrong service environment did not fail exact authority parity")
            cur.execute("rollback to savepoint wrong_service_environment_probe")
            cur.execute("release savepoint wrong_service_environment_probe")

            cur.execute("savepoint retired_service_probe")
            cur.execute("update ops.service set retired_at=now() where key='rules-refresh'")
            try:
                validate_launchd_authority_refs(
                    cur, runtime_version, expected_launchd, expected_service_launchd,
                    expected_sealed_service_launchd
                )
            except RuntimeError:
                pass
            else:
                raise RuntimeError("retired launchd service did not fail exact authority parity")
            cur.execute("rollback to savepoint retired_service_probe")
            cur.execute("release savepoint retired_service_probe")

            cur.execute("savepoint extra_service_environment_probe")
            cur.execute(
                """insert into ops.service(key,name,criticality,owner_actor,runtime)
                    values ('siep11-rogue','SIEP11 rogue fixture','low','joe','launchd')"""
            )
            cur.execute(
                """insert into ops.service_environment(service_id,environment,deploy_mechanism)
                    select id,'local','ops/launchd/com.carr.rules-refresh.plist'
                      from ops.service where key='siep11-rogue'"""
            )
            try:
                validate_launchd_authority_refs(
                    cur, runtime_version, expected_launchd, expected_service_launchd,
                    expected_sealed_service_launchd
                )
            except RuntimeError:
                pass
            else:
                raise RuntimeError("extra launchd service environment did not fail exact authority parity")
            cur.execute("rollback to savepoint extra_service_environment_probe")
            cur.execute("release savepoint extra_service_environment_probe")

            cur.execute("savepoint wrong_legacy_path_probe")
            cur.execute(
                """update ops.legacy_schedule_launchd_contract set repo_plist_relpath='ops/launchd/wrong.plist'
                    where surface_id='nightly-record-layer.launchd.v1'"""
            )
            try:
                validate_launchd_authority_refs(
                    cur, runtime_version, expected_launchd, expected_service_launchd,
                    expected_sealed_service_launchd
                )
            except RuntimeError:
                pass
            else:
                raise RuntimeError("same legacy surface with wrong path did not fail exact authority parity")
            cur.execute("rollback to savepoint wrong_legacy_path_probe")
            cur.execute("release savepoint wrong_legacy_path_probe")

            grant_settable_runtime_roles(cur, "carr_reader", "carr_writer", "carr_jobs", "carr_authority")
            for role in ("carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                set_local_role(cur, role)
                answer = cur.execute(
                    f"select {lookup_function}(%s,%s)",
                    (runtime_digest, "mcp-tool:add-loop"),
                ).fetchone()[0]
                if answer.get("registered") is not True or answer.get("atomic_database_mediation_operational") is not False:
                    raise RuntimeError(f"{role} did not receive bounded safe registry readback")
                unknown = cur.execute(
                    f"select {lookup_function}(%s,%s)",
                    (runtime_digest, "mcp-tool:not-reviewed"),
                ).fetchone()[0]
                mismatch = cur.execute(
                    f"select {lookup_function}(%s,%s)",
                    ("sha256:" + "0" * 64, "mcp-tool:add-loop"),
                ).fetchone()[0]
                if unknown != {"reason": "unknown_ingress", "registered": False,
                               "registry_digest": runtime_digest, "registry_version": runtime_version}:
                    raise RuntimeError(f"{role} unknown ingress did not fail closed: {unknown!r}")
                if mismatch.get("registered") is not False or mismatch.get("reason") != "digest_mismatch":
                    raise RuntimeError(f"{role} digest mismatch did not fail closed: {mismatch!r}")
                refusal(cur, "select count(*) from ops.scac_mutation_registry_entry", (), "permission denied")
                refusal(cur, "select ops.scac_mutation_registration(%s,%s)",
                        (digest, "mcp-tool:add-loop"), "permission denied")
                refusal(cur, "insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract) values ('scac-mutation-registry.v1','mcp-tool:forged','mcp_tool','record_mutation','forged','sha256:'||repeat('0',64),'{}')", (), "permission denied")
                cur.execute("reset role")

            refusal(cur,
                    "update ops.scac_mutation_registry_version set entry_count=entry_count+1",
                    (), "append-only")
            refusal(cur,
                    "delete from ops.scac_mutation_registry_entry where ingress_key='mcp-tool:add-loop'",
                    (), "append-only")

            cur.execute("savepoint registry_corruption_probe")
            cur.execute("alter table ops.scac_mutation_registry_entry disable trigger scac_mutation_registry_entry_sealed")
            forged_contract = {
                "ingress_key": "mcp-tool:forged",
                "ingress_kind": "mcp_tool",
                "effect_class": "administrative_mutation",
                "source_locator": "safe:forged",
                "owner_package": "11",
                "classification_authorizing": False,
            }
            cur.execute(
                """insert into ops.scac_mutation_registry_entry
                       (registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)
                     values ('scac-mutation-registry.v1','mcp-tool:forged','mcp_tool',
                             'administrative_mutation','safe:forged','sha256:'||repeat('0',64),%s::jsonb)""",
                (json.dumps(forged_contract, sort_keys=True, separators=(",", ":")),),
            )
            corrupt = cur.execute(
                "select ops.scac_mutation_registration(%s,%s)", (digest, "mcp-tool:add-loop")
            ).fetchone()[0]
            if corrupt.get("registered") is not False or corrupt.get("reason") != "registry_corrupt":
                raise RuntimeError(f"tampered historical v1 registry did not fail closed: {corrupt!r}")
            cur.execute("rollback to savepoint registry_corruption_probe")
            cur.execute("release savepoint registry_corruption_probe")

            cur.execute("savepoint registry_same_cardinality_probe")
            cur.execute("alter table ops.scac_mutation_registry_entry disable trigger scac_mutation_registry_entry_sealed")
            cur.execute(
                """update ops.scac_mutation_registry_entry
                      set contract=jsonb_set(contract,'{mutation_kind}','\"tampered\"'::jsonb)
                    where registry_version='scac-mutation-registry.v1'
                      and ingress_key='mcp-tool:add-loop'"""
            )
            same_cardinality = cur.execute(
                "select ops.scac_mutation_registration(%s,%s)", (digest, "mcp-tool:add-loop")
            ).fetchone()[0]
            if same_cardinality.get("registered") is not False or same_cardinality.get("reason") != "registry_corrupt":
                raise RuntimeError(f"same-cardinality historical v1 tamper did not fail closed: {same_cardinality!r}")
            cur.execute("rollback to savepoint registry_same_cardinality_probe")
            cur.execute("release savepoint registry_same_cardinality_probe")
    except Exception as exc:  # noqa: BLE001 - concise CI surface
        return fail(str(exc))
    print("siep11-mutation-registry-local-pg-gate passed: exact immutable application/catalog entries; 4 runtime roles have lookup-only access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
