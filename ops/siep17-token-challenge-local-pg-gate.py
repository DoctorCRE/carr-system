#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only adversarial acceptance for SIEP-17 token control facts."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"siep17-token-challenge-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def sha(value: bytes | str) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def refusal(cur, statement: str, params: tuple[object, ...], fragment: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(statement, params)
    except Exception as exc:  # noqa: BLE001 - the refusal is the assertion
        cur.execute("rollback to savepoint expected_refusal")
        cur.execute("release savepoint expected_refusal")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(f"expected refusal containing {fragment!r}, got {exc}") from exc
        return
    cur.execute("rollback to savepoint expected_refusal")
    cur.execute("release savepoint expected_refusal")
    raise RuntimeError(f"expected refusal containing {fragment!r}")


def set_session(cur, role: str) -> None:
    cur.execute(f"set session authorization {role}")


def reset_session(cur) -> None:
    cur.execute("reset session authorization")


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            zero = "sha256:" + "0" * 64
            epoch_digest = sha("siep17-epoch")
            registry_digest = cur.execute(
                "select registry_digest from ops.scac_mutation_registry_version "
                "where registry_version='scac-mutation-registry.v2'"
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
                    schema_highest_migration,schema_ledger_digest,source_digest,
                    source_session_user,source_relation)
                   values (1,%s,null,null,'carr-system-integrity-elimination-v1','carr-internal',
                    'scac-core','scac-mutation-registry.v2',%s,0,%s,%s,1,
                    '0465_siep17_token_challenge_authority.sql',%s,%s,current_user,
                    'public.schema_migrations')""",
                (epoch_digest, registry_digest, zero, zero, zero, zero),
            )
            device_ref = "joe-studio-siep17"
            public_key = bytes.fromhex("81" * 32)
            key_digest = sha(public_key)
            facts_digest = sha("siep17-device-facts")
            cur.execute(
                """insert into ops.scac_device_enrollment
                   (device_ref,sponsor,profile_key,device_key_digest,device_public_key,
                    policy_epoch,policy_epoch_digest,facts_digest)
                   values (%s,'joe','studio-executor',%s,%s,1,%s,%s)""",
                (device_ref, key_digest, public_key, epoch_digest, facts_digest),
            )
            initial = cur.execute("select ops.scac_token_control_snapshot()").fetchone()[0]
            if initial.get("kill_switch_state") != "active" or \
               initial.get("control_integrity_state") != "uninitialized_fail_closed":
                raise RuntimeError(f"uninitialized control did not fail closed: {initial!r}")

            release_reason = sha("reviewed source-test release")
            release_key = uuid.uuid4()
            set_session(cur, "carr_authority_dell")
            refusal(
                cur, "select ops.scac_transition_token_kill_switch('release',%s,%s)",
                (release_reason, release_key), "Joe-only",
            )
            reset_session(cur)
            set_session(cur, "carr_authority_joe")
            released = cur.execute(
                "select ops.scac_transition_token_kill_switch('release',%s,%s)",
                (release_reason, release_key),
            ).fetchone()[0]
            released_retry = cur.execute(
                "select ops.scac_transition_token_kill_switch('release',%s,%s)",
                (release_reason, release_key),
            ).fetchone()[0]
            reset_session(cur)
            if released != released_retry or released.get("kill_switch_state") != "inactive":
                raise RuntimeError(f"Joe release was not exact and idempotent: {released!r}")

            contract = cur.execute(
                """select ingress_key,contract from ops.scac_mutation_registry_entry
                   where registry_version='scac-mutation-registry.v2'
                     and ingress_kind='mcp_tool' and effect_class<>'read_only'
                     and coalesce((contract->>'classification_authorizing')::boolean,true)=false
                   order by ingress_key limit 1"""
            ).fetchone()
            if not contract:
                raise RuntimeError("test mutation ingress is unavailable")
            ingress = contract[0]
            principal_digest = sha("joe-authenticated-session")
            manifest_digest = sha("closed-operation-manifest")
            idempotency_digest = sha("exact-request-idempotency")
            issue_key = uuid.uuid4()
            set_session(cur, "carr_jobs")
            challenge = cur.execute(
                "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,300,%s)",
                (principal_digest, device_ref, ingress, manifest_digest, idempotency_digest, issue_key),
            ).fetchone()[0]
            challenge_retry = cur.execute(
                "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,300,%s)",
                (principal_digest, device_ref, ingress, manifest_digest, idempotency_digest, issue_key),
            ).fetchone()[0]
            if challenge != challenge_retry:
                raise RuntimeError("exact challenge issuance retry returned different material")
            expected_keys = {
                "schema_version", "challenge_id", "device_ref", "device_key_digest",
                "facts_digest", "policy_epoch", "policy_epoch_digest",
                "operation_manifest_digest", "nonce", "issued_at", "expires_at",
            }
            if set(challenge) != expected_keys or challenge["operation_manifest_digest"] != manifest_digest:
                raise RuntimeError(f"challenge projection leaked or omitted fields: {challenge!r}")
            payload = {
                "domain": "CARR-SCAC-POP-V1",
                "schema_version": challenge["schema_version"],
                "challenge_id": challenge["challenge_id"],
                "device_ref": challenge["device_ref"],
                "device_key_digest": challenge["device_key_digest"],
                "facts_digest": challenge["facts_digest"],
                "policy_epoch": challenge["policy_epoch"],
                "policy_epoch_digest": challenge["policy_epoch_digest"],
                "operation_manifest_digest": challenge["operation_manifest_digest"],
                "nonce": challenge["nonce"],
                "issued_at": challenge["issued_at"],
                "expires_at": challenge["expires_at"],
            }
            expected_challenge_digest = sha(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            reset_session(cur)
            stored_challenge_digest = cur.execute(
                "select challenge_digest from ops.scac_pop_challenge where challenge_id=%s",
                (challenge["challenge_id"],),
            ).fetchone()[0]
            set_session(cur, "carr_jobs")
            if expected_challenge_digest != stored_challenge_digest:
                raise RuntimeError(
                    "SQL/JS challenge canonical digest parity drifted: "
                    f"{expected_challenge_digest} != {stored_challenge_digest}"
                )
            refusal(
                cur, "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,300,%s)",
                (principal_digest, device_ref, ingress, sha("changed-manifest"),
                 idempotency_digest, issue_key), "idempotency binding mismatch",
            )
            revoked_issue_key = uuid.uuid4()
            revoked_challenge = cur.execute(
                "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,300,%s)",
                (principal_digest, device_ref, ingress, sha("revoked-challenge-manifest"),
                 sha("revoked-challenge-idempotency"), revoked_issue_key),
            ).fetchone()[0]
            reset_session(cur)
            revoked_challenge_digest = cur.execute(
                "select challenge_digest from ops.scac_pop_challenge where challenge_id=%s",
                (revoked_challenge["challenge_id"],),
            ).fetchone()[0]
            set_session(cur, "carr_authority_joe")
            challenge_revocation_key = uuid.uuid4()
            cur.execute(
                "select ops.scac_revoke_token_subject('challenge',%s,%s,%s)",
                (revoked_challenge_digest, sha("revoke-challenge"), challenge_revocation_key),
            )
            refusal(
                cur, "select ops.scac_revoke_token_subject('challenge',%s,%s,%s)",
                (revoked_challenge_digest, sha("revoke-challenge"), uuid.uuid4()),
                "different idempotency key",
            )
            reset_session(cur)
            set_session(cur, "carr_jobs")
            refusal(
                cur, "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,300,%s)",
                (principal_digest, device_ref, ingress, sha("revoked-challenge-manifest"),
                 sha("revoked-challenge-idempotency"), revoked_issue_key),
                "prior challenge is no longer issuable",
            )
            proof_verification_digest = sha("siep16-cryptographic-verification-receipt")
            consume_key = uuid.uuid4()
            consumed = cur.execute(
                "select ops.scac_consume_verified_pop_challenge(%s,%s,%s,%s,%s)",
                (challenge["challenge_id"], stored_challenge_digest, challenge["nonce"],
                 proof_verification_digest, consume_key),
            ).fetchone()[0]
            consumed_retry = cur.execute(
                "select ops.scac_consume_verified_pop_challenge(%s,%s,%s,%s,%s)",
                (challenge["challenge_id"], stored_challenge_digest, challenge["nonce"],
                 proof_verification_digest, consume_key),
            ).fetchone()[0]
            if consumed != consumed_retry or consumed.get("admission_state") != "ineligible_pending_siep18" \
               or consumed.get("routing_eligible") is not False \
               or consumed.get("privileges_active") is not False:
                raise RuntimeError(f"challenge consumption crossed SIEP-18 boundary: {consumed!r}")
            refusal(
                cur, "select ops.scac_consume_verified_pop_challenge(%s,%s,%s,%s,%s)",
                (challenge["challenge_id"], stored_challenge_digest, challenge["nonce"],
                 sha("different-proof"), uuid.uuid4()), "replayed",
            )
            expiring = cur.execute(
                "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,1,%s)",
                (principal_digest, device_ref, ingress, sha("expiring-manifest"),
                 sha("expiring-idempotency"), uuid.uuid4()),
            ).fetchone()[0]
            reset_session(cur)
            expiring_digest = cur.execute(
                "select challenge_digest from ops.scac_pop_challenge where challenge_id=%s",
                (expiring["challenge_id"],),
            ).fetchone()[0]
            set_session(cur, "carr_jobs")
            cur.execute("select pg_sleep(1.05)")
            refusal(
                cur, "select ops.scac_consume_verified_pop_challenge(%s,%s,%s,%s,%s)",
                (expiring["challenge_id"], expiring_digest, expiring["nonce"],
                 sha("expired-proof"), uuid.uuid4()), "expired",
            )
            reset_session(cur)

            # Record a digest-only externally verified token. It remains structurally
            # valid but cannot authorize anything, even before revocation.
            token_ref = sha("siep17-token-ref")
            receipt_key = uuid.uuid4()
            set_session(cur, "carr_jobs")
            token_status = cur.execute(
                """select ops.scac_record_capability_token_receipt(
                   %s,%s,%s,%s,%s,%s,%s,clock_timestamp(),clock_timestamp()+interval '30 seconds',%s)""",
                (stored_challenge_digest, token_ref, sha("signed-payload"), sha("signature"),
                 sha("issuer-key"), sha("root-event"), sha("external-verification"), receipt_key),
            ).fetchone()[0]
            reset_session(cur)
            if token_status.get("token_state") != "valid" or \
               token_status.get("admission_state") != "ineligible_pending_siep18" or \
               token_status.get("issuer_trust_state") != "unverified_pending_siep18_transaction_bridge":
                raise RuntimeError(f"token receipt status overstated authority: {token_status!r}")
            receipt_times = cur.execute(
                "select issued_at,expires_at from ops.scac_capability_token_receipt where token_ref_digest=%s",
                (token_ref,),
            ).fetchone()
            set_session(cur, "carr_jobs")
            token_retry = cur.execute(
                """select ops.scac_record_capability_token_receipt(
                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (stored_challenge_digest, token_ref, sha("signed-payload"), sha("signature"),
                 sha("issuer-key"), sha("root-event"), sha("external-verification"),
                 receipt_times[0], receipt_times[1], receipt_key),
            ).fetchone()[0]
            if token_retry != token_status:
                raise RuntimeError("exact token receipt retry was not stable")
            refusal(
                cur,
                """select ops.scac_record_capability_token_receipt(
                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (stored_challenge_digest, token_ref, sha("signed-payload"), sha("signature"),
                 sha("changed-issuer-key"), sha("root-event"), sha("external-verification"),
                 receipt_times[0], receipt_times[1], receipt_key),
                "idempotency binding mismatch",
            )
            reset_session(cur)

            # Permanent token revocation dominates otherwise valid token cryptography.
            set_session(cur, "carr_authority_joe")
            revoked = cur.execute(
                "select ops.scac_revoke_token_subject('token',%s,%s,%s)",
                (token_ref, sha("revoke-token"), uuid.uuid4()),
            ).fetchone()[0]
            reset_session(cur)
            set_session(cur, "carr_jobs")
            after_revocation = cur.execute(
                "select ops.scac_capability_token_status(%s)", (token_ref,)
            ).fetchone()[0]
            reset_session(cur)
            if revoked.get("revocation_state") != "revoked" or \
               after_revocation.get("token_state") != "revoked" or \
               after_revocation.get("reason_id") != "scac.refusal.revoked":
                raise RuntimeError("token revocation did not dominate valid cryptography")

            # Global kill-switch dominates revocation and blocks issuance. Joe can
            # release again only by appending a new event.
            set_session(cur, "carr_authority_joe")
            engaged = cur.execute(
                "select ops.scac_transition_token_kill_switch('engage',%s,%s)",
                (sha("engage-control"), uuid.uuid4()),
            ).fetchone()[0]
            reset_session(cur)
            if engaged.get("kill_switch_state") != "active":
                raise RuntimeError("Joe kill-switch engage did not take effect")
            set_session(cur, "carr_jobs")
            refusal(
                cur, "select ops.scac_issue_pop_challenge(%s,%s,null,%s,%s,%s,30,%s)",
                (principal_digest, device_ref, ingress, manifest_digest,
                 sha("second-request"), uuid.uuid4()), "scac.refusal.kill_switch",
            )
            killed_status = cur.execute(
                "select ops.scac_capability_token_status(%s)", (token_ref,)
            ).fetchone()[0]
            reset_session(cur)
            if killed_status.get("reason_id") != "scac.refusal.kill_switch":
                raise RuntimeError("kill-switch did not have highest refusal precedence")

            refusal(cur, "update ops.scac_token_kill_switch_event set action='release'", (), "append-only")
            refusal(cur, "delete from ops.scac_token_revocation_event", (), "append-only")
            refusal(cur, "truncate ops.scac_pop_challenge cascade", (), "cannot be truncated")

            grant_settable_runtime_roles(
                cur, "carr_reader", "carr_writer", "carr_jobs", "carr_authority"
            )
            for role in ("carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
                cur.execute("savepoint raw_scope")
                set_local_role(cur, role)
                try:
                    cur.execute("select nonce_bytes from ops.scac_pop_challenge limit 1")
                except Exception as exc:  # noqa: BLE001
                    cur.execute("rollback to savepoint raw_scope")
                    cur.execute("release savepoint raw_scope")
                    if "permission denied" not in str(exc).lower():
                        raise
                else:
                    raise RuntimeError(f"{role} could read raw SIEP-17 challenge material")

            print(
                "siep17-token-challenge-local-pg-gate: PASS — DB-clock challenge issuance, "
                "single-use consumption, Joe-only control, permanent revocation, secret-safe "
                "readback, and the SIEP-18/Production fence are exact"
            )
            return 0
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
