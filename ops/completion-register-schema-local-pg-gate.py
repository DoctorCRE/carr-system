#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only fixture proof for the completion evidence core."""

from __future__ import annotations

import os
import hashlib
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import rollback_only_connection


DIMENSIONS = (
    "canonical_owner",
    "intended_consumer",
    "workflow_trigger",
    "retrieval_admission",
    "enforcement_closure",
    "operator_surface",
    "telemetry",
    "canonical_implementation",
    "activation",
    "live_readback",
    "rollback",
)
PRECEDENCE = (
    "conflicting",
    "canceled",
    "superseded",
    "unknown_stale",
    "blocked",
    "planned",
    "built_unmerged",
    "merged_unactivated",
    "active_unproven",
    "partially_built",
    "operational",
)


def sha(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def one(cur: psycopg.Cursor[Any], query: str, args: tuple[object, ...] = ()) -> tuple[Any, ...]:
    row = cur.execute(query, args).fetchone()
    if row is None:
        raise RuntimeError(f"completion register fixture expected one row: {query[:120]}")
    return tuple(row)


def refused(
    cur: psycopg.Cursor[Any],
    query: str,
    args: tuple[object, ...],
    expected: str,
    label: str,
) -> None:
    savepoint = "refuse_" + uuid.uuid4().hex[:12]
    cur.execute(f"savepoint {savepoint}")
    try:
        cur.execute(query, args)
    except psycopg.Error as exc:
        if expected not in str(exc):
            raise RuntimeError(f"{label} raised the wrong refusal: {exc}") from exc
        cur.execute(f"rollback to savepoint {savepoint}")
        cur.execute(f"release savepoint {savepoint}")
        return
    raise RuntimeError(f"{label} accepted forbidden input")


def policy(cur: psycopg.Cursor[Any], tenant: str) -> None:
    one(
        cur,
        """insert into ops.completion_policy
             (policy_key,policy_version,capability_class,required_dimensions,
              default_freshness,state_precedence,effective_at,policy_digest)
           values ('fixture-default',1,'default',%s,interval '1 day',%s,
                   now()-interval '1 day',null)
           returning organization_tenant_id,policy_digest""",
        (list(DIMENSIONS), list(PRECEDENCE)),
    )
    if one(cur, "select organization_tenant_id from ops.completion_policy where policy_key='fixture-default'") != (tenant,):
        raise RuntimeError("completion policy did not derive its tenant")


def subject(
    cur: psycopg.Cursor[Any], key: str, *, source: bool = True
) -> uuid.UUID:
    return one(
        cur,
        """insert into ops.completion_subject
             (stable_key,human_label,capability_class,canonical_source_kind,
              canonical_source_ref,created_provenance)
           values (%s,%s,'fixture',%s,%s,%s) returning id""",
        (
            key,
            key,
            "fixture" if source else None,
            f"fixture:{key}" if source else None,
            Jsonb({"source_ref": f"fixture:{key}"}),
        ),
    )[0]


def receipt(
    cur: psycopg.Cursor[Any], source_ref: str, *, succeeded: bool = True
) -> uuid.UUID:
    outcome = "succeeded" if succeeded else "failed"
    failure = None if succeeded else "fixture_failure"
    return one(
        cur,
        """insert into ops.completion_receipt
             (receipt_ref,collector_name,collector_version,source_kind,source_ref,
              source_cursor,rows_observed,rows_changed,outcome,failure_class,
              started_at,finished_at,expires_at,evidence_digest)
           values (%s,'fixture','v1','fixture',%s,%s,20,%s,%s,%s,
                   now()-interval '3 days 1 second',now()-interval '3 days',
                   now()+interval '1 day',%s) returning id""",
        (
            f"receipt:{uuid.uuid4()}",
            source_ref,
            Jsonb({"cursor_ref": source_ref}),
            20 if succeeded else 0,
            outcome,
            failure,
            sha("a"),
        ),
    )[0]


def observation(
    cur: psycopg.Cursor[Any], subject_id: uuid.UUID, receipt_id: uuid.UUID,
    source_ref: str, kind: str, *, value_seed: str = "b",
    coherent_revision: str = "fixture-revision-1", stale: bool = False,
    policy_stale: bool = False,
    authority_class: str = "authoritative",
) -> None:
    observed = "now()-interval '2 days'" if stale or policy_stale else "now()"
    expires = (
        "now()-interval '1 day'" if stale
        else "now()+interval '12 hours'" if policy_stale
        else "now()+interval '1 hour'"
    )
    query = f"""insert into ops.completion_observation
      (subject_id,receipt_id,source_kind,source_ref,source_revision,coherent_revision,
       observation_kind,authority_class,content_digest,value_digest,redacted_value,
       evidence_locator,observed_at,expires_at,collector_name,collector_version)
      values (%s,%s,'fixture',%s,'source-revision-1',%s,%s,%s,
              %s,%s,%s,%s,{observed},{expires},'fixture','v1')"""
    cur.execute(
        query,
        (
            subject_id,
            receipt_id,
            source_ref,
            coherent_revision,
            kind,
            authority_class,
            sha((value_seed + "c")[-1]),
            sha(value_seed),
            Jsonb({"assertion": "present", "kind": kind}),
            f"evidence:{source_ref}:{kind}",
        ),
    )


def complete_subject(
    cur: psycopg.Cursor[Any], key: str, *, source: bool = True,
    stale_dimension: str | None = None,
    policy_stale_dimension: str | None = None,
    authority_class: str = "authoritative",
) -> tuple[uuid.UUID, uuid.UUID]:
    subject_id = subject(cur, key, source=source)
    source_ref = f"fixture-source:{key}"
    receipt_id = receipt(cur, source_ref)
    for ordinal, dimension in enumerate(DIMENSIONS):
        observation(
            cur,
            subject_id,
            receipt_id,
            source_ref,
            dimension,
            value_seed=chr(ord("b") + ordinal),
            stale=dimension == stale_dimension,
            policy_stale=dimension == policy_stale_dimension,
            authority_class=authority_class,
        )
    return subject_id, receipt_id


def projected_state(cur: psycopg.Cursor[Any], subject_id: uuid.UUID) -> tuple[Any, ...]:
    return one(
        cur,
        """select lifecycle_state,precedence_rank,policy_version,coherent_revision,
                  next_required_dimension
             from ops.completion_projection where subject_id=%s""",
        (subject_id,),
    )


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        raise RuntimeError("completion register gate requires disposable DATABASE_URL or CARR_LOCAL_PG_DSN")

    assertions = 0
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        tenant = "completion-fixture"
        cur.execute("select set_config('carr.organization_tenant_id',%s,true)", (tenant,))
        policy(cur, tenant)
        assertions += 1

        derived_id = subject(cur, "tenant-derived")
        if one(cur, "select organization_tenant_id from ops.completion_subject where id=%s", (derived_id,)) != (tenant,):
            raise RuntimeError("completion subject did not derive its tenant")
        refused(
            cur,
            """insert into ops.completion_subject
                 (organization_tenant_id,stable_key,human_label,capability_class,
                  canonical_source_kind,canonical_source_ref,created_provenance)
               values ('other-tenant','wrong-tenant','Wrong','fixture','fixture',
                       'fixture:wrong','{}')""",
            (),
            "tenant is server-derived",
            "caller-selected tenant",
        )
        assertions += 1

        refused(
            cur,
            "update ops.completion_subject set human_label='rewritten' where id=%s",
            (derived_id,),
            "completion_subject is append-only",
            "subject rewrite",
        )
        refused(
            cur,
            "delete from ops.completion_subject where id=%s",
            (derived_id,),
            "completion_subject is append-only",
            "subject delete",
        )
        assertions += 1

        failed_receipt = receipt(cur, "fixture-source:failed", succeeded=False)
        refused(
            cur,
            """insert into ops.completion_observation
                 (subject_id,receipt_id,source_kind,source_ref,source_revision,
                  coherent_revision,observation_kind,authority_class,content_digest,
                  value_digest,redacted_value,evidence_locator,observed_at,expires_at,
                  collector_name,collector_version)
               values (%s,%s,'fixture','fixture-source:failed','r1','r1',
                       'canonical_owner','authoritative',%s,%s,'{}','evidence:failed',
                       now(),now()+interval '1 hour','fixture','v1')""",
            (derived_id, failed_receipt, sha("d"), sha("e")),
            "matching successful receipt",
            "failed receipt evidence",
        )
        assertions += 1

        expired_receipt = one(
            cur,
            """insert into ops.completion_receipt
                 (receipt_ref,collector_name,collector_version,source_kind,source_ref,
                  source_cursor,rows_observed,rows_changed,outcome,failure_class,
                  started_at,finished_at,expires_at,evidence_digest)
               values (%s,'fixture','v1','fixture','fixture-source:expired',
                       '{"cursor_ref":"expired"}',1,1,'succeeded',null,
                       now()-interval '3 hours',now()-interval '2 hours',
                       now()-interval '1 hour',%s) returning id""",
            (f"receipt:{uuid.uuid4()}", sha("expired-receipt")),
        )[0]
        refused(
            cur,
            """insert into ops.completion_observation
                 (subject_id,receipt_id,source_kind,source_ref,source_revision,
                  coherent_revision,observation_kind,authority_class,content_digest,
                  value_digest,redacted_value,evidence_locator,observed_at,expires_at,
                  collector_name,collector_version)
               values (%s,%s,'fixture','fixture-source:expired','r1','r1',
                       'canonical_owner','authoritative',%s,%s,'{}',
                       'evidence:expired-replay',now(),now()+interval '1 hour',
                       'fixture','v1')""",
            (derived_id, expired_receipt, sha("expired-content"), sha("expired-value")),
            "matching successful receipt",
            "expired receipt replay",
        )
        assertions += 1

        good_receipt = receipt(cur, "fixture-source:redaction")
        base_observation = """insert into ops.completion_observation
          (subject_id,receipt_id,source_kind,source_ref,source_revision,coherent_revision,
           observation_kind,authority_class,content_digest,value_digest,redacted_value,
           evidence_locator,observed_at,expires_at,collector_name,collector_version)
          values (%s,%s,'fixture','fixture-source:redaction',%s,'r1','canonical_owner',
                  'authoritative',%s,%s,%s,'evidence:redaction',now(),
                  now()+interval '1 hour','fixture','v1')"""
        refused(
            cur,
            base_observation,
            (derived_id, good_receipt, "nested-secret", sha("f"), sha("g"), Jsonb({"safe": {"api_token": "redacted?"}})),
            "completion_observation_redacted_value_check",
            "nested secret payload",
        )
        refused(
            cur,
            base_observation,
            (derived_id, good_receipt, "writer-operational", sha("h"), sha("i"), Jsonb({"claim": "operational"})),
            "completion_observation_redacted_value_check",
            "writer operational assertion",
        )
        refused(
            cur,
            base_observation,
            (derived_id, good_receipt, "unrestricted-url", sha("url"), sha("url-value"), Jsonb({"locator": "https://example.invalid/raw"})),
            "completion_observation_redacted_value_check",
            "unrestricted URL payload",
        )
        refused(
            cur,
            """insert into ops.completion_observation
                 (subject_id,receipt_id,source_kind,source_ref,source_revision,
                  coherent_revision,observation_kind,authority_class,content_digest,
                  value_digest,redacted_value,evidence_locator,observed_at,expires_at,
                  collector_name,collector_version)
               values (%s,%s,'fixture','fixture-source:redaction','missing-coherence',
                       null,'canonical_owner','authoritative',%s,%s,'{}',
                       'evidence:missing-coherence',now(),now()+interval '1 hour',
                       'fixture','v1')""",
            (derived_id, good_receipt, sha("missing-coherence"), sha("missing-coherence-value")),
            "coherent_revision",
            "revision-less observation",
        )
        assertions += 1

        source_backed = subject(cur, "source-backed-disposition")
        joe = one(cur, "select id from public.actor where slug='joe' and kind='human' and active")[0]
        refused(
            cur,
            """insert into ops.completion_disposition
                 (subject_id,disposition,decision_ref,rationale,decided_by_actor_id,decided_at)
               values (%s,'canceled','decision:fixture','fixture',%s,now())""",
            (source_backed, joe),
            "only for source-less subjects",
            "source-owned disposition",
        )
        assertions += 1

        operational, _ = complete_subject(cur, "operational")
        if projected_state(cur, operational)[:4] != ("operational", 11, 1, "fixture-revision-1"):
            raise RuntimeError(f"complete coherent evidence did not derive operational: {projected_state(cur, operational)}")
        assertions += 1

        supporting_only, _ = complete_subject(
            cur, "supporting-only", authority_class="supporting"
        )
        supporting_state = projected_state(cur, supporting_only)
        if supporting_state[0] != "partially_built" or supporting_state[4] != "canonical_owner":
            raise RuntimeError(
                f"supporting-only evidence satisfied authoritative completion: {supporting_state}"
            )
        assertions += 1

        stale, _ = complete_subject(
            cur, "stale", policy_stale_dimension="live_readback"
        )
        stale_state = projected_state(cur, stale)
        if stale_state[0] != "unknown_stale" or stale_state[4] != "live_readback":
            raise RuntimeError(
                f"evidence beyond policy freshness did not demote: {stale_state}"
            )
        assertions += 1

        replacement = subject(cur, "replacement", source=False)
        conflicting, _ = complete_subject(cur, "conflicting", source=False)
        second_ref = "fixture-source:conflicting-second"
        second_receipt = receipt(cur, second_ref)
        observation(
            cur,
            conflicting,
            second_receipt,
            second_ref,
            "canonical_owner",
            value_seed="z",
        )
        cur.execute(
            """insert into ops.completion_disposition
                 (subject_id,disposition,replacement_subject_id,decision_ref,rationale,
                  decided_by_actor_id,decided_at)
               values (%s,'superseded',%s,'decision:fixture-supersession',
                       'fixture supersession',%s,now())""",
            (conflicting, replacement, joe),
        )
        conflict_state = projected_state(cur, conflicting)
        if conflict_state[0] != "conflicting" or conflict_state[1] != 1:
            raise RuntimeError(f"conflict did not outrank disposition: {conflict_state}")
        assertions += 1

        built = subject(cur, "built-unmerged")
        built_ref = "fixture-source:built-unmerged"
        built_receipt = receipt(cur, built_ref)
        observation(cur, built, built_receipt, built_ref, "intent")
        observation(cur, built, built_receipt, built_ref, "implementation_artifact", value_seed="y")
        if projected_state(cur, built)[0] != "built_unmerged":
            raise RuntimeError(f"artifact-only subject did not derive built_unmerged: {projected_state(cur, built)}")
        assertions += 1

        other_subject = subject(cur, "relation-target")
        cur.execute(
            """insert into ops.completion_relation
                 (from_subject_id,to_subject_id,relation_kind,authority_class,
                  source_kind,source_ref,source_revision,relation_digest)
               values (%s,%s,'implements','exact_source','fixture',
                       'fixture:relation','r1',%s)""",
            (built, other_subject, sha("j")),
        )
        refused(
            cur,
            "update ops.completion_relation set relation_kind='duplicates' where from_subject_id=%s",
            (built,),
            "completion_relation is append-only",
            "relation rewrite",
        )
        assertions += 1

        refused(
            cur,
            "update ops.completion_policy set default_freshness=interval '2 days' where policy_key='fixture-default'",
            (),
            "completion_policy is append-only",
            "policy rewrite",
        )
        assertions += 1

        if one(cur, "select count(*) from ops.completion_projection")[0] < 4:
            raise RuntimeError("tenant projection omitted fixture subjects")
        cur.execute("select set_config('carr.organization_tenant_id','other-tenant',true)")
        if one(cur, "select count(*) from ops.completion_projection") != (0,):
            raise RuntimeError("completion projection leaked another tenant")
        cur.execute("select set_config('carr.organization_tenant_id',%s,true)", (tenant,))
        assertions += 1

        cur.execute("set session authorization carr_reader")
        if projected_state(cur, operational)[0] != "operational":
            raise RuntimeError("carr_reader could not consume the tenant projection")
        cur.execute("reset session authorization")
        assertions += 1

        cur.execute("set session authorization carr_writer")
        refused(
            cur,
            """insert into ops.completion_subject
                 (stable_key,human_label,capability_class,created_provenance)
               values ('writer-forgery','Writer forgery','fixture','{}')""",
            (),
            "permission denied",
            "direct writer assertion",
        )
        cur.execute("reset session authorization")
        assertions += 1

        if one(
            cur,
            """select count(*) from information_schema.columns
                 where table_schema='ops'
                   and table_name in ('completion_subject','completion_observation',
                                      'completion_relation','completion_disposition',
                                      'completion_policy','completion_receipt')
                   and column_name in ('status','state','lifecycle_state','operational')""",
        ) != (0,):
            raise RuntimeError("completion evidence tables expose writer-set operational state")
        assertions += 1

    print(
        "completion-register-schema-local-pg-gate: PASS — "
        f"{assertions} redacted assertions cover append-only, tenant, redaction, "
        "receipt, precedence, and stale demotion"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
