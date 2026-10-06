#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only acceptance for SIEP-14 root quorum and revocation facts."""

from __future__ import annotations

import hashlib
import os
import sys

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"siep14-root-trust-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def refusal(cur, statement: str, fragment: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(statement)
    except Exception as exc:  # noqa: BLE001
        cur.execute("rollback to savepoint expected_refusal")
        cur.execute("release savepoint expected_refusal")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_refusal")
    cur.execute("release savepoint expected_refusal")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


def sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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
                """insert into ops.scac_policy_epoch
                   (epoch,epoch_digest,previous_epoch,previous_epoch_digest,program_key,tenant_scope,
                    policy_domain,registry_version,registry_digest,doctrine_generation,
                    doctrine_projection_digest,rule_projection_digest,schema_applied_count,
                    schema_highest_migration,schema_ledger_digest,source_digest,source_session_user,source_relation)
                   values (1,%s,null,null,'carr-system-integrity-elimination-v1','carr-internal',
                    'scac-core','scac-mutation-registry.v2',%s,
                    0,%s,%s,1,'0459_siep14_forward_mutation_registry.sql',%s,%s,current_user,'public.schema_migrations')""",
                (epoch_digest, v2_digest, zero, zero, zero, zero),
            )
            root_bytes = bytes.fromhex("31" * 32)
            root_digest = sha(root_bytes)
            cur.execute(
                """insert into ops.scac_root_trust_key(key_digest,algorithm,public_key_bytes,key_purpose)
                   values (%s,'ed25519',%s,'artifact_manifest_signing')""",
                (root_digest, root_bytes),
            )
            signatures = [bytes.fromhex("41" * 64), bytes.fromhex("42" * 64)]
            approval_digests = sorted(sha(value) for value in signatures)
            # The reviewed membership is three custodians, while this ceremony
            # deliberately carries only the threshold subset: a real 2-of-3.
            custodian_keys = [bytes([81 + index]) * 32 for index in range(3)]
            custodian_set_digest = cur.execute(
                "select ops.scac_root_custodian_set_digest(%s)",
                (sorted(sha(value) for value in custodian_keys),),
            ).fetchone()[0]
            for custodian_key in custodian_keys:
                cur.execute(
                    """insert into ops.scac_root_custodian_set_member
                       (custodian_set_digest,custodian_key_digest) values (%s,%s)""",
                    (custodian_set_digest, sha(custodian_key)),
                )
            event_digest = cur.execute(
                """select ops.scac_root_trust_event_digest(
                     1,null,'establish',%s,null,2,%s,%s,null,1,%s)""",
                (root_digest, custodian_set_digest, approval_digests, epoch_digest),
            ).fetchone()[0]
            cur.execute(
                """insert into ops.scac_root_trust_event
                   (event_no,event_digest,previous_event_digest,action,subject_key_digest,
                    replacement_key_digest,threshold,custodian_set_digest,custodian_approval_digests,
                    recovery_receipt_digest,policy_epoch,policy_epoch_digest)
                   values (1,%s,null,'establish',%s,null,2,%s,%s,null,1,%s)""",
                (event_digest, root_digest, custodian_set_digest, approval_digests, epoch_digest),
            )
            incomplete = cur.execute("select ops.scac_root_trust_chain_state()").fetchone()[0]
            if incomplete.get("valid") is not False:
                raise RuntimeError(f"ceremony without attestations was accepted: {incomplete!r}")
            statement_digest = cur.execute(
                """select ops.scac_root_trust_event_statement_digest(
                     1,null,'establish',%s,null,2,%s,null,1,%s)""",
                (root_digest, custodian_set_digest, epoch_digest),
            ).fetchone()[0]
            for index, signature in enumerate(signatures):
                custodian_key = custodian_keys[index]
                cur.execute(
                    """insert into ops.scac_root_custodian_attestation
                       (event_digest,custodian_key_digest,algorithm,public_key_bytes,signature_bytes,
                        signature_digest,signed_payload_digest,verifier_contract)
                       values (%s,%s,'ed25519',%s,%s,%s,%s,'mcp-server/src/root-trust.js')""",
                    (event_digest, sha(custodian_key), custodian_key, signature,
                     sha(signature), statement_digest),
                )
            state = cur.execute("select ops.scac_root_trust_chain_state()").fetchone()[0]
            if state.get("valid") is not False or state.get("structurally_valid") is not True or \
               state.get("cryptographic_quorum_state") != "external_verification_required" or \
               state.get("active_key_digest") != root_digest or \
               state.get("root_trust_operational") is not False or \
               state.get("production_enforcement_active") is not False:
                raise RuntimeError(f"valid nonproduction root ceremony did not replay: {state!r}")

            refusal(cur, "update ops.scac_root_trust_key set key_purpose='changed'", "append-only")
            refusal(cur, "delete from ops.scac_root_trust_event", "append-only")
            refusal(cur, "delete from ops.scac_root_custodian_set_member", "append-only")
            refusal(cur, "truncate ops.scac_root_custodian_set_member", "cannot be truncated")
            refusal(cur, "truncate ops.scac_root_custodian_attestation cascade", "cannot be truncated")
            refusal(cur, f"insert into ops.scac_root_trust_event(event_no,event_digest,previous_event_digest,action,subject_key_digest,threshold,custodian_set_digest,custodian_approval_digests,policy_epoch,policy_epoch_digest) values (3,'{zero}','{event_digest}','revoke','{root_digest}',2,'{custodian_set_digest}',array['{approval_digests[0]}','{approval_digests[1]}'],1,'{epoch_digest}')", "gap or fork")

            grant_settable_runtime_roles(cur, "carr_reader", "carr_writer", "carr_jobs", "carr_authority")
            for role in ("carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                cur.execute("savepoint role_refusal")
                set_local_role(cur, role)
                try:
                    cur.execute("select key_digest from ops.scac_root_trust_key limit 1")
                except Exception as exc:  # noqa: BLE001
                    cur.execute("rollback to savepoint role_refusal")
                    cur.execute("release savepoint role_refusal")
                    if "permission denied" not in str(exc).lower():
                        raise
                else:
                    raise RuntimeError(f"{role} could read raw root material")
            print("siep14-root-trust-local-pg-gate: PASS — quorum, chain, ACL, and nonproduction fences verified")
        return 0
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
