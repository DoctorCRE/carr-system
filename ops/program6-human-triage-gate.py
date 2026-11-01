#!/usr/bin/env python3
# ci: db-gate
"""Rollback-only gate for the Program 6 human captured-to-triaged transition."""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb
from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role


def fail(message: str) -> int:
    print(f"program6-human-triage-gate: FAIL — {message}", file=sys.stderr)
    return 1


def expect_refusal(cur, sql: str, params: tuple, label: str) -> None:
    cur.execute("savepoint program6_triage_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint program6_triage_refusal")
        return
    cur.execute("rollback to savepoint program6_triage_refusal")
    raise RuntimeError(f"{label} was accepted")


def doctrine_fixture(cur, actor_id: uuid.UUID, section_key: str = "source"):
    key = uuid.uuid4().hex
    document_id = cur.execute(
        """insert into doctrine_document (slug,title,content_class,visibility,created_by)
             values (%s,'Program 6 triage gate','reference','shared',%s) returning id""",
        (f"program6-triage-{key}", actor_id),
    ).fetchone()[0]
    section_id = cur.execute(
        """insert into doctrine_section (document_id,section_key,title,ordinal,status,current_version)
             values (%s,%s,'Program 6 triage source',10,'active',1) returning id""",
        (document_id, section_key),
    ).fetchone()[0]
    body = "Human triage records one bounded classification and performs no work."
    revision_id = cur.execute(
        """insert into doctrine_revision (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
             values (%s,1,%s,%s,%s,%s,'Program 6 triage fixture') returning id""",
        (section_id, actor_id, Jsonb({"text": body}), body, hashlib.sha256(body.encode()).hexdigest()),
    ).fetchone()[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (revision_id, section_id))
    return section_id, revision_id, f"doctrine:program6-triage-{key}#{section_key}"


def capture(cur, section_id, revision_id, origin_ref):
    return cur.execute(
        """select id,ref,state,version from ops.capture_sourced_work_request(%s,%s,%s,%s,%s,%s,%s)""",
        (origin_ref, "Human triage gate", "Record a bounded human review",
         Jsonb([{"id": "HUMAN-TRIAGE", "text": "A human classification is recorded"}]),
         section_id, revision_id, uuid.uuid4()),
    ).fetchone()


def triage(cur, ref, version, classification, key):
    return cur.execute(
        """select id,ref,state,version,classification,triaged_by_actor_slug,triaged_at,replayed
             from ops.triage_sourced_work_request(%s,%s,%s,%s)""",
        (ref, version, classification, key),
    ).fetchone()


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return fail("DATABASE_URL is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            joe = cur.execute("select id from actor where slug='joe' and active and kind='human'").fetchone()
            dell = cur.execute("select id from actor where slug='dell' and active and kind='human'").fetchone()
            if not joe or not dell:
                return fail("seeded active human actors joe and dell are required")
            joe_id = joe[0]
            cur.execute("""do $$ begin
              if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then create role carr_authority_joe login; end if;
              if not exists (select 1 from pg_roles where rolname='carr_authority_dell') then create role carr_authority_dell login; end if;
            end $$""")
            cur.execute("grant carr_authority to carr_authority_joe,carr_authority_dell")
            grant_settable_runtime_roles(
                cur, "carr_authority_joe", "carr_authority_dell", "carr_writer", "carr_reader", "carr_jobs"
            )

            section_id, revision_id, origin_ref = doctrine_fixture(cur, joe_id)
            # Team-memory keys are slash-scoped (WR-000668); report-problem must
            # capture from them instead of tripping the origin-ref shape check.
            scoped_source = doctrine_fixture(cur, joe_id, "lessons/slash-scoped-source")
            set_local_role(cur, "carr_writer")
            scoped = capture(cur, *scoped_source)
            if not scoped or scoped[2] != "captured":
                return fail(f"slash-scoped doctrine source did not capture: {scoped}")
            request_id, ref, state, version = capture(cur, section_id, revision_id, origin_ref)
            expect_refusal(cur, "select * from ops.triage_sourced_work_request(%s,%s,%s,%s)",
                           (ref, version, "operational", uuid.uuid4()), "generic writer triage")
            expect_refusal(cur,
                """update ops.work_request
                      set state='triaged',triage_classification='operational',triaged_by_actor_id=%s,
                          triaged_at=now(),version=version+1,updated_at=now()
                    where id=%s""",
                (dell[0], request_id), "generic writer exact-shaped sourced transition without receipt")
            cur.execute("reset role")
            receipt_privileges = cur.execute(
                """select has_table_privilege('carr_writer','ops.work_request_triage_receipt','INSERT'),
                          has_table_privilege('carr_authority','ops.work_request_triage_receipt','INSERT'),
                          has_table_privilege('carr_jobs','ops.work_request_triage_receipt','INSERT')"""
            ).fetchone()
            if receipt_privileges != (False, False, False):
                return fail(f"a non-owner role can forge a triage receipt: {receipt_privileges}")
            for role in ("carr_reader", "carr_jobs"):
                set_local_role(cur, role)
                expect_refusal(cur, "select * from ops.triage_sourced_work_request(%s,%s,%s,%s)",
                               (ref, version, "operational", uuid.uuid4()), f"{role} triage")
                cur.execute("reset role")

            key = uuid.uuid4()
            cur.execute("set session authorization carr_authority_dell")
            triaged = triage(cur, ref, version, "operational", key)
            cur.execute("reset session authorization")
            if not triaged or triaged[:6] != (request_id, ref, "triaged", version + 1, "operational", "dell") or triaged[6] is None or triaged[7] is not False:
                return fail(f"Dell authority did not persist the exact triage result: {triaged}")
            cur.execute("set session authorization carr_authority_dell")
            replay = triage(cur, ref, version, "operational", key)
            cur.execute("reset session authorization")
            if replay[:7] != triaged[:7] or replay[7] is not True:
                return fail("exact authority replay did not return the persisted receipt")
            set_local_role(cur, "carr_reader")
            dell_card = cur.execute("select * from ops.work_request_card(%s,%s)", (ref, "carr-internal")).fetchone()
            cur.execute("reset role")
            if not dell_card or dell_card[0] != ref or dell_card[2] != "triaged" or dell_card[11:14] != ("operational", "dell", triaged[6]):
                return fail(f"triaged Dell card lost review attribution or classification: {dell_card}")
            cur.execute("set session authorization carr_authority_joe")
            expect_refusal(cur, "select * from ops.triage_sourced_work_request(%s,%s,%s,%s)",
                           (ref, version, "operational", key), "other authority idempotency replay")
            cur.execute("reset session authorization")

            # Both admitted partners can review a shared sourced request, but
            # the persisted reviewer remains session-derived, never caller data.
            set_local_role(cur, "carr_writer")
            joe_request_id, joe_ref, joe_state, joe_version = capture(cur, section_id, revision_id, origin_ref)
            cur.execute("reset role")
            cur.execute("set session authorization carr_authority_joe")
            joe_triaged = triage(cur, joe_ref, joe_version, "needs_judgment", uuid.uuid4())
            cur.execute("reset session authorization")
            if not joe_triaged or joe_triaged[:6] != (joe_request_id, joe_ref, "triaged", joe_version + 1, "needs_judgment", "joe"):
                return fail(f"Joe authority did not receive an independently attributed shared triage: {joe_triaged}")
            set_local_role(cur, "carr_reader")
            joe_card = cur.execute("select * from ops.work_request_card(%s,%s)", (joe_ref, "carr-internal")).fetchone()
            cur.execute("reset role")
            if not joe_card or joe_card[11:14] != ("needs_judgment", "joe", joe_triaged[6]):
                return fail(f"triaged Joe card lost review attribution or classification: {joe_card}")
            cur.execute("set session authorization carr_authority_dell")
            expect_refusal(cur, "select * from ops.triage_sourced_work_request(%s,%s,%s,%s)",
                           (ref, version + 1, "operational", uuid.uuid4()), "post-triage advance")
            cur.execute("reset session authorization")
            cur.execute("savepoint program6_later_card")
            cur.execute("alter table ops.work_request drop constraint work_request_sourced_capture_shape")
            cur.execute("alter table ops.work_request disable trigger sourced_work_request_is_immutable")
            cur.execute("update ops.work_request set state='needs_joe' where id=%s", (request_id,))
            if cur.execute("select * from ops.work_request_card(%s,%s)", (ref, "carr-internal")).fetchone():
                return fail("later Work Request state remained visible through the sourced card")
            cur.execute("rollback to savepoint program6_later_card")
            expect_refusal(cur, "select * from ops.triage_sourced_work_request(%s,%s,%s,%s)",
                           (ref, version, "invented", uuid.uuid4()), "open classification")
            stored = cur.execute(
                "select state,program_key,program_ordinal,triage_classification,triaged_by_actor_id from ops.work_request where id=%s",
                (request_id,),
            ).fetchone()
            if stored != ("triaged", None, None, "operational", dell[0]):
                return fail(f"triage widened sourced Work Request state or attribution: {stored}")
        print("PASS: Program 6 triage is authority-bound, idempotent, and captured-to-triaged only")
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
