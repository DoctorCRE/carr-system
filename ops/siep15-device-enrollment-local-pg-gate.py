#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only acceptance for SIEP-15 signed device and benchmark facts."""

from __future__ import annotations

import hashlib
import json
import os
import sys

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"siep15-device-enrollment-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refusal(cur, statement: str, fragment: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(statement)
    except Exception as exc:  # noqa: BLE001 - the refusal is the assertion
        cur.execute("rollback to savepoint expected_refusal")
        cur.execute("release savepoint expected_refusal")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_refusal")
    cur.execute("release savepoint expected_refusal")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def reject_fact_shape(
    cur, facts: dict[str, object], epoch_digest: str, device_ref: str,
    key_byte: int, expected_fragment: str,
) -> None:
    facts_json = json.dumps(facts, separators=(",", ":"), sort_keys=True)
    public_key = bytes([key_byte]) * 32
    signature = bytes([key_byte + 1]) * 64
    cur.execute("savepoint invalid_fact_shape")
    try:
        facts_digest = cur.execute(
            "select ops.scac_device_fact_payload_digest(%s,'joe','studio-executor',%s::jsonb,1,%s)",
            (device_ref, facts_json, epoch_digest),
        ).fetchone()[0]
        cur.execute(
            """insert into ops.scac_device_enrollment
               (device_ref,sponsor,profile_key,device_key_digest,device_public_key,
                policy_epoch,policy_epoch_digest,facts_digest)
               values (%s,'joe','studio-executor',%s,%s,1,%s,%s)""",
            (device_ref, sha(public_key), public_key, epoch_digest, facts_digest),
        )
        cur.execute(
            """insert into ops.scac_device_fact_receipt
               (device_ref,schema_version,facts,observed_at,facts_digest,algorithm,
                signature_bytes,signature_digest,signed_payload_digest,verifier_contract)
               values (%s,'scac-device-facts.v1',%s::jsonb,%s,%s,'ed25519',%s,%s,%s,
                'mcp-server/src/device-enrollment.js')""",
            (device_ref, facts_json, facts["observed_at"], facts_digest, signature,
             sha(signature), facts_digest),
        )
    except Exception as exc:  # noqa: BLE001 - exact refusal is the assertion
        cur.execute("rollback to savepoint invalid_fact_shape")
        cur.execute("release savepoint invalid_fact_shape")
        if expected_fragment.lower() not in str(exc).lower():
            raise RuntimeError(
                f"invalid device facts refused for the wrong reason: {exc}"
            ) from exc
        return
    cur.execute("rollback to savepoint invalid_fact_shape")
    cur.execute("release savepoint invalid_fact_shape")
    raise RuntimeError(f"invalid device facts were accepted: {device_ref}")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            zero = "sha256:" + "0" * 64
            epoch_digest = "sha256:" + "1" * 64
            v2_digest = cur.execute(
                "select registry_digest from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v2'"
            ).fetchone()[0]
            cur.execute(
                """do $fixture$ begin
                     if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then
                       create role carr_authority_joe;
                     end if;
                     if not exists(select 1 from pg_roles where rolname='carr_authority_dell') then
                       create role carr_authority_dell;
                     end if;
                   end $fixture$"""
            )
            cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
            cur.execute(
                """insert into ops.scac_policy_epoch
                   (epoch,epoch_digest,previous_epoch,previous_epoch_digest,program_key,tenant_scope,
                    policy_domain,registry_version,registry_digest,doctrine_generation,
                    doctrine_projection_digest,rule_projection_digest,schema_applied_count,
                    schema_highest_migration,schema_ledger_digest,source_digest,source_session_user,source_relation)
                   values (1,%s,null,null,'carr-system-integrity-elimination-v1','carr-internal',
                    'scac-core','scac-mutation-registry.v2',%s,
                    0,%s,%s,1,'0460_siep15_device_enrollment.sql',%s,%s,current_user,'public.schema_migrations')""",
                (epoch_digest, v2_digest, zero, zero, zero, zero),
            )
            device_ref = "joe-studio-pending"
            public_key = bytes.fromhex("71" * 32)
            key_digest = sha(public_key)
            facts = {
                "exact_model_identifier": "Mac-model-discovered",
                "cpu_identifier": "arm64-cpu-discovered",
                "cpu_core_count": 16,
                "gpu_core_count": 40,
                "memory_bytes": 64 * 1024**3,
                "storage_bytes": 2 * 1024**4,
                "architecture": "arm64",
                "os_version": "discovered",
                "os_build": "discovered",
                "filevault_state": "enabled",
                "sip_state": "enabled",
                "virtualization_entitlement": True,
                "observed_at": "2026-08-26T00:00:00Z",
            }
            facts_json = json.dumps(facts, separators=(",", ":"), sort_keys=True)
            facts_digest = cur.execute(
                "select ops.scac_device_fact_payload_digest(%s,'joe','studio-executor',%s::jsonb,1,%s)",
                (device_ref, facts_json, epoch_digest),
            ).fetchone()[0]
            if facts_digest != "sha256:a44c6a2f5bf8a8a2ba99f149a9fc7b3215dc43802e7d189976d0f995f8b629da":
                raise RuntimeError(f"SQL/JS device-fact canonical digest parity drifted: {facts_digest}")
            decimal_facts = {
                **facts,
                "cpu_core_count": 16.0,
                "gpu_core_count": 40.0,
                "memory_bytes": float(64 * 1024**3),
                "storage_bytes": float(2 * 1024**4),
            }
            decimal_facts_digest = cur.execute(
                "select ops.scac_device_fact_payload_digest(%s,'joe','studio-executor',%s::jsonb,1,%s)",
                (device_ref, json.dumps(decimal_facts, separators=(",", ":"), sort_keys=True),
                 epoch_digest),
            ).fetchone()[0]
            if decimal_facts_digest != facts_digest:
                raise RuntimeError(
                    "SQL did not normalize integral decimal JSON facts to the JS canonical digest: "
                    f"{decimal_facts_digest}"
                )
            cur.execute(
                """insert into ops.scac_device_enrollment
                   (device_ref,sponsor,profile_key,device_key_digest,device_public_key,
                    policy_epoch,policy_epoch_digest,facts_digest)
                   values (%s,'joe','studio-executor',%s,%s,1,%s,%s)""",
                (device_ref, key_digest, public_key, epoch_digest, facts_digest),
            )
            fact_signature = bytes.fromhex("72" * 64)
            cur.execute(
                """insert into ops.scac_device_fact_receipt
                   (device_ref,schema_version,facts,observed_at,facts_digest,algorithm,
                    signature_bytes,signature_digest,signed_payload_digest,verifier_contract)
                   values (%s,'scac-device-facts.v1',%s::jsonb,%s,%s,'ed25519',%s,%s,%s,
                    'mcp-server/src/device-enrollment.js')""",
                (device_ref, facts_json, facts["observed_at"], facts_digest, fact_signature,
                 sha(fact_signature), facts_digest),
            )
            cur.execute("set session authorization carr_authority_joe")
            incomplete = cur.execute(
                "select ops.scac_device_enrollment_status(%s)", (device_ref,)
            ).fetchone()[0]
            cur.execute("reset session authorization")
            if incomplete.get("assurance_state") != "receipts_incomplete" or \
               incomplete.get("routing_eligible") is not False or \
               incomplete.get("privileges_active") is not False:
                raise RuntimeError(f"incomplete Studio was promoted: {incomplete!r}")

            kinds = (
                "thermal_sustained_cpu_gpu", "ssd", "vm_isolation",
                "mlx_inference_context_memory", "concurrent_jobs", "reboot_power_loss",
                "network_egress", "workload_quotas", "failover",
            )
            profile_digest = "sha256:60eb0ebfb46d7155cf71ec479b38bd24e5d0c31051923382fd91ec2faf524762"
            for index, kind in enumerate(kinds):
                metrics_digest = sha(f"metrics:{kind}".encode())
                observed_at = "2026-08-26T01:00:00Z"
                receipt_digest = cur.execute(
                    "select ops.scac_device_benchmark_payload_digest(%s,%s,%s,%s,%s,true,%s)",
                    (kind, device_ref, facts_digest, profile_digest, metrics_digest, observed_at),
                ).fetchone()[0]
                if kind == "ssd" and receipt_digest != "sha256:16c0df31f262be36b22628a0b7a0c12e8fdadaadfb9030e50ef9133485707ebf":
                    raise RuntimeError(f"SQL/JS benchmark canonical digest parity drifted: {receipt_digest}")
                signature = bytes([128 + index]) * 64
                cur.execute(
                    """insert into ops.scac_device_benchmark_receipt
                       (device_ref,benchmark_kind,schema_version,facts_digest,profile_digest,
                        metrics_digest,passed,observed_at,receipt_digest,algorithm,signature_bytes,
                        signature_digest,signed_payload_digest,verifier_contract)
                       values (%s,%s,'scac-device-benchmark.v1',%s,%s,%s,true,%s,%s,'ed25519',
                        %s,%s,%s,'mcp-server/src/device-enrollment.js')""",
                    (device_ref, kind, facts_digest, profile_digest, metrics_digest, observed_at,
                     receipt_digest, signature, sha(signature), receipt_digest),
                )
            cur.execute("set session authorization carr_authority_joe")
            complete = cur.execute(
                "select ops.scac_device_enrollment_status(%s)", (device_ref,)
            ).fetchone()[0]
            cur.execute("reset session authorization")
            if complete.get("assurance_state") != "structurally_complete_non_authorizing" or \
               complete.get("cryptographic_device_state") != "external_verification_required" or \
               complete.get("pop_state") != "pending_siep16" or \
               complete.get("optional_non_blocking") is not True or \
               complete.get("source_of_truth") is not False or \
               complete.get("critical_dependency") is not False or \
               complete.get("routing_eligible") is not False or \
               complete.get("privileges_active") is not False or \
               complete.get("production_enforcement_active") is not False:
                raise RuntimeError(f"complete receipts crossed the SIEP-15 boundary: {complete!r}")

            numeric_string_facts = {**facts, "cpu_core_count": "16"}
            reject_fact_shape(
                cur, numeric_string_facts, epoch_digest, "invalid-numeric-facts", 0x73,
                "cannot cast jsonb string to type numeric",
            )
            non_string_identifier_facts = {**facts, "exact_model_identifier": 1234}
            reject_fact_shape(
                cur, non_string_identifier_facts, epoch_digest, "invalid-identifier-facts", 0x75,
                "scac_device_fact_receipt_facts_check",
            )
            invalid_calendar_facts = {**facts, "observed_at": "2026-02-30T00:00:00Z"}
            reject_fact_shape(
                cur, invalid_calendar_facts, epoch_digest, "invalid-calendar-facts", 0x77,
                "date/time field value out of range",
            )

            refusal(cur, "update ops.scac_device_enrollment set lifecycle_state='changed'", "append-only")
            refusal(cur, "delete from ops.scac_device_fact_receipt", "append-only")
            refusal(cur, "truncate ops.scac_device_benchmark_receipt cascade", "cannot be truncated")
            refusal(cur, f"insert into ops.scac_device_benchmark_receipt(device_ref,benchmark_kind,schema_version,facts_digest,profile_digest,metrics_digest,passed,observed_at,receipt_digest,algorithm,signature_bytes,signature_digest,signed_payload_digest,verifier_contract) values ('{device_ref}','ssd','scac-device-benchmark.v1','{facts_digest}','{profile_digest}','{zero}',true,'2026-08-26T01:00:00Z','{zero}','ed25519',decode('00','hex')||decode(repeat('00',63),'hex'),'{zero}','{zero}','mcp-server/src/device-enrollment.js')", "does not bind")

            grant_settable_runtime_roles(cur, "carr_reader", "carr_writer", "carr_jobs", "carr_authority")
            for role in ("carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                cur.execute("savepoint status_scope")
                set_local_role(cur, role)
                try:
                    cur.execute("select ops.scac_device_enrollment_status(%s)", (device_ref,))
                except Exception as exc:  # noqa: BLE001
                    cur.execute("rollback to savepoint status_scope")
                    cur.execute("release savepoint status_scope")
                    expected = "authority refused" if role == "carr_authority" else "permission denied"
                    if expected not in str(exc).lower():
                        raise
                else:
                    raise RuntimeError(f"{role} could enumerate Joe Studio status")
                cur.execute("savepoint raw_scope")
                set_local_role(cur, role)
                try:
                    cur.execute("select device_public_key from ops.scac_device_enrollment limit 1")
                except Exception as exc:  # noqa: BLE001
                    cur.execute("rollback to savepoint raw_scope")
                    cur.execute("release savepoint raw_scope")
                    if "permission denied" not in str(exc).lower():
                        raise
                else:
                    raise RuntimeError(f"{role} could read raw device enrollment facts")

            cur.execute("savepoint dell_status_scope")
            cur.execute("set session authorization carr_authority_dell")
            try:
                cur.execute("select ops.scac_device_enrollment_status(%s)", (device_ref,))
            except Exception as exc:  # noqa: BLE001
                cur.execute("rollback to savepoint dell_status_scope")
                cur.execute("reset session authorization")
                cur.execute("release savepoint dell_status_scope")
                if "joe studio status authority refused" not in str(exc).lower():
                    raise
            else:
                cur.execute("reset session authorization")
                cur.execute("release savepoint dell_status_scope")
                raise RuntimeError("Dell authority could enumerate Joe Studio status")
            cur.execute("savepoint joe_status_scope")
            cur.execute("set session authorization carr_authority_joe")
            try:
                state = cur.execute(
                    "select ops.scac_device_enrollment_status(%s)", (device_ref,)
                ).fetchone()[0]
            except Exception:
                cur.execute("rollback to savepoint joe_status_scope")
                cur.execute("reset session authorization")
                cur.execute("release savepoint joe_status_scope")
                raise
            cur.execute("reset session authorization")
            cur.execute("release savepoint joe_status_scope")
            if state.get("routing_eligible") is not False or state.get("sponsor") != "joe":
                raise RuntimeError(f"Joe received an unsafe Studio projection: {state!r}")

            historical_versions = (
                "scac-mutation-registry.v1", "scac-mutation-registry.v2",
                "scac-mutation-registry.v3", "scac-mutation-registry.v4",
            )
            for version in historical_versions:
                if cur.execute(
                    "select ops.scac_mutation_registry_seal_valid(%s)", (version,)
                ).fetchone()[0] is not True:
                    raise RuntimeError(f"sealed historical registry did not validate: {version}")
            if cur.execute(
                "select ops.scac_mutation_registry_v4_seal_available()"
            ).fetchone()[0] is not True:
                raise RuntimeError("sealed v4 registry was unavailable after v5")
            if cur.execute(
                "select ops.scac_mutation_catalog_v4_live_at_seal()"
            ).fetchone()[0] is not False or cur.execute(
                "select ops.scac_mutation_catalog_v4_current()"
            ).fetchone()[0] is not False:
                raise RuntimeError("historical v4 live-catalog validator was weakened into a seal predicate")

            cur.execute("savepoint historical_registry_tamper")
            cur.execute(
                "alter table ops.scac_mutation_registry_entry "
                "disable trigger scac_mutation_registry_entry_sealed"
            )
            cur.execute(
                """update ops.scac_mutation_registry_entry
                      set contract=jsonb_set(contract,'{implementation_state}','\"tampered\"'::jsonb)
                    where registry_version='scac-mutation-registry.v4'
                      and ingress_key=(select min(ingress_key)
                        from ops.scac_mutation_registry_entry
                        where registry_version='scac-mutation-registry.v4')"""
            )
            if cur.execute(
                "select ops.scac_mutation_registry_seal_valid('scac-mutation-registry.v4')"
            ).fetchone()[0] is not False:
                raise RuntimeError("same-cardinality historical v4 tamper passed the sealed-history verifier")
            cur.execute("rollback to savepoint historical_registry_tamper")
            cur.execute("release savepoint historical_registry_tamper")
            print("siep15-device-enrollment-local-pg-gate: PASS — signed-fact structure, benchmark completeness, ACL, and nonauthorizing fences verified")
        return 0
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
