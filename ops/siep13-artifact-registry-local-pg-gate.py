#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only acceptance for SIEP-13 artifact/signature/transparency facts."""

from __future__ import annotations

import os
import sys

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"siep13-artifact-registry-local-pg-gate: FAIL — {message}", file=sys.stderr)
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
                    0,%s,%s,1,'0457_siep13_forward_mutation_registry.sql',%s,%s,current_user,'public.schema_migrations')""",
                (epoch_digest, v2_digest, zero, zero, zero, zero),
            )
            artifact = "sha256:" + "2" * 64
            source = "sha256:" + "3" * 64
            provenance = "sha256:" + "4" * 64
            manifest = cur.execute(
                """select ops.scac_artifact_manifest_digest(
                     %s,'source_bundle','application/gzip',14,'git:siep13-fixture',%s,%s,%s,1,%s)""",
                (artifact, source, zero, provenance, epoch_digest),
            ).fetchone()[0]
            cur.execute(
                """insert into ops.scac_artifact_manifest
                   (artifact_digest,manifest_digest,artifact_kind,media_type,byte_length,source_ref,
                    source_digest,sbom_digest,provenance_digest,policy_epoch,policy_epoch_digest)
                   values (%s,%s,'source_bundle','application/gzip',14,'git:siep13-fixture',%s,null,%s,1,%s)""",
                (artifact, manifest, source, provenance, epoch_digest),
            )
            public_key = bytes.fromhex("11" * 32)
            signature_bytes = bytes.fromhex("22" * 64)
            signature_digest = "sha256:" + __import__("hashlib").sha256(signature_bytes).hexdigest()
            key_digest = "sha256:" + __import__("hashlib").sha256(public_key).hexdigest()
            cur.execute(
                """insert into ops.scac_artifact_signature
                   (signature_digest,manifest_digest,algorithm,signer_key_digest,public_key_bytes,
                    signature_bytes,signed_payload_digest,signature_scope)
                   values (%s,%s,'ed25519',%s,%s,%s,%s,'scac-artifact-manifest.v1')""",
                (signature_digest, manifest, key_digest, public_key, signature_bytes, manifest),
            )
            statement = "sha256:" + "5" * 64
            entry1 = cur.execute(
                "select ops.scac_artifact_transparency_entry_digest(1,null,%s,%s,%s)",
                (manifest, signature_digest, statement),
            ).fetchone()[0]
            cur.execute(
                """insert into ops.scac_artifact_transparency_entry
                   (entry_no,entry_digest,previous_entry_digest,manifest_digest,signature_digest,
                    statement_digest,entry_kind)
                   values (1,%s,null,%s,%s,%s,'artifact_inclusion')""",
                (entry1, manifest, signature_digest, statement),
            )
            entry2 = cur.execute(
                "select ops.scac_artifact_transparency_entry_digest(2,%s,%s,%s,%s)",
                (entry1, manifest, signature_digest, "sha256:" + "6" * 64),
            ).fetchone()[0]
            cur.execute(
                """insert into ops.scac_artifact_transparency_entry
                   (entry_no,entry_digest,previous_entry_digest,manifest_digest,signature_digest,
                    statement_digest,entry_kind)
                   values (2,%s,%s,%s,%s,%s,'artifact_inclusion')""",
                (entry2, entry1, manifest, signature_digest, "sha256:" + "6" * 64),
            )
            state = cur.execute("select ops.scac_artifact_integrity_state(%s)", (artifact,)).fetchone()[0]
            if state.get("artifact_trust_state") != "untrusted_pending_siep14" or state.get("reason_id") != "scac.refusal.root_untrusted" or state.get("root_trust_operational") is not False or state.get("production_enforcement_active") is not False:
                raise RuntimeError(f"artifact facts were promoted beyond SIEP-13: {state!r}")
            unknown = cur.execute("select ops.scac_artifact_integrity_state(%s)", (zero,)).fetchone()[0]
            if unknown.get("root_trust_operational") is not False or unknown.get("production_enforcement_active") is not False:
                raise RuntimeError(f"unknown artifact response omitted fail-closed flags: {unknown!r}")

            refusal(cur, "update ops.scac_artifact_manifest set source_ref='changed'", "append-only")
            refusal(cur, "delete from ops.scac_artifact_signature", "append-only")
            refusal(cur, "truncate ops.scac_artifact_transparency_entry cascade", "cannot be truncated")
            refusal(cur, "truncate ops.scac_artifact_signature cascade", "cannot be truncated")
            refusal(cur, "truncate ops.scac_artifact_manifest cascade", "cannot be truncated")
            refusal(cur, f"insert into ops.scac_artifact_transparency_entry(entry_no,entry_digest,previous_entry_digest,manifest_digest,signature_digest,statement_digest,entry_kind) values (3,'{zero}','{entry1}','{manifest}','{signature_digest}','{zero}','artifact_inclusion')", "gap or fork")
            refusal(cur, f"insert into ops.scac_artifact_manifest(artifact_digest,manifest_digest,artifact_kind,media_type,byte_length,source_ref,source_digest,provenance_digest,policy_epoch,policy_epoch_digest,root_trust_operational) values ('{zero}','{zero}','binary','application/octet-stream',1,'x','{zero}','{zero}',1,'{epoch_digest}',true)", "manifest digest mismatch")

            grant_settable_runtime_roles(cur, "carr_reader", "carr_writer", "carr_jobs", "carr_authority")
            for role in ("carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                cur.execute("savepoint role_refusal")
                set_local_role(cur, role)
                try:
                    cur.execute("select artifact_digest from ops.scac_artifact_manifest limit 1")
                except Exception as exc:  # noqa: BLE001
                    cur.execute("rollback to savepoint role_refusal")
                    cur.execute("release savepoint role_refusal")
                    if "permission denied" not in str(exc).lower():
                        raise
                else:
                    raise RuntimeError(f"{role} could read the owner-only artifact registry")
            print("siep13-artifact-registry-local-pg-gate: PASS — immutable chain, fail-closed trust, and owner-only ACLs verified")
        return 0
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
