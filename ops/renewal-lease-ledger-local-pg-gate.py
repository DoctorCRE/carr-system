#!/usr/bin/env python3
# ci: db-gate
# doctrine: renewals
"""Rollback-only acceptance for the authenticated CARR lease renewal provider."""
from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg

from gate_runtime_role import rollback_only_connection

FORBIDDEN_COLUMNS = {"id", "pool_id", "source_key", "source_row", "email", "phone", "address"}
REQUIRED_COLUMNS = {"display_name", "est_lease_event", "tier_status", "flag_status", "has_channel",
                    "decision_count", "source_observed_at", "freshness_state", "owner_slug"}


def one(cur: psycopg.Cursor[Any], sql: str, args: tuple[object, ...] = ()) -> tuple[Any, ...]:
    cur.execute(sql, args)
    value = cur.fetchone()
    if value is None:
        raise RuntimeError(f"renewal lease ledger gate expected one row: {sql}")
    return tuple(value)


def refused(cur: psycopg.Cursor[Any], sql: str, args: tuple[object, ...], text: str) -> None:
    cur.execute("savepoint expected_refusal")
    try:
        cur.execute(sql, args)
    except psycopg.Error as exc:
        if text not in str(exc):
            raise
        cur.execute("rollback to savepoint expected_refusal")
        return
    raise RuntimeError(f"renewal lease ledger gate accepted forbidden call: {text}")


def record(
    cur: psycopg.Cursor[Any], deal: uuid.UUID, base_version: int | None,
    *, expiration: str = "365 days", evidence_kind: str = "executed_lease",
) -> tuple[Any, ...]:
    return one(
        cur,
        """select lease_id,version,superseded_lease_id,deal_id,client_id
             from ops.record_executed_lease
               (%s,%s,current_date-30,current_date-20,(current_date+(%s)::interval)::date,
                12,%s,'lease-abstract:sha256:fixture','CARR-held executed lease abstract')""",
        (str(deal), base_version, expiration, evidence_kind),
    )


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        raise RuntimeError("renewal lease ledger gate requires disposable DATABASE_URL or CARR_LOCAL_PG_DSN")
    with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
        cur.execute("""do $$ begin
          if not exists(select 1 from pg_roles where rolname='carr_authority_joe') then
            create role carr_authority_joe login;
          end if;
          if not exists(select 1 from pg_roles where rolname='carr_authority_dell') then
            create role carr_authority_dell login;
          end if;
          grant carr_authority to carr_authority_joe,carr_authority_dell;
        end $$""")
        # Safe-view shape and read boundary (moved from the retired
        # renewal-decision-delivery-gate.py, whose pre-0279 branch was dead).
        columns = {row[0] for row in cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name='v_renewal_decision_queue'").fetchall()}
        if columns & FORBIDDEN_COLUMNS or not REQUIRED_COLUMNS <= columns:
            raise RuntimeError(f"safe renewal queue columns are wrong: {sorted(columns)}")
        if one(cur, "select count(*) from information_schema.columns where table_schema='public' "
                    "and table_name='v_renewal_decision_queue_status' and column_name='owner_slug'") != (1,):
            raise RuntimeError("renewal status view is not sponsor-scoped")
        for role, relation in (("carr_reader", "public.v_renewal_decision_queue"),
                               ("carr_reader", "public.v_renewal_decision_queue_status"),
                               ("carr_reader", "public.candidate_pool"), ("carr_reader", "public.v_export_pool"),
                               ("carr_reader", "public.lease"),
                               ("carr_reader", "ops.renewal_decision_source_run"),
                               ("carr_jobs", "ops.renewal_decision_source_run")):
            if one(cur, "select has_table_privilege(%s,%s,'select')", (role, relation))[0] \
                    != relation.startswith("public.v_renewal_decision"):
                raise RuntimeError(f"unexpected {role} select boundary on {relation}")
        joe = one(cur, "select id from actor where slug='joe'")[0]
        dell = one(cur, "select id from actor where slug='dell'")[0]
        party, client, deal = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        client_status = one(cur, "select slug from client_status order by sort,slug limit 1")[0]
        cur.execute(
            "insert into party(id,kind,name,city,state,email,created_by,updated_by) "
            "values(%s,'person','Lease Ledger Fixture','Pensacola','FL','fixture@example.test',%s,%s)",
            (party, joe, joe),
        )
        cur.execute(
            "insert into client(id,party_id,status,vertical,created_by,updated_by) "
            "values(%s,%s,%s,'medical',%s,%s)",
            (client, party, client_status, joe, joe),
        )
        cur.execute(
            "insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by) "
            "values(%s,%s,'Lease Ledger Fixture Deal','lease','closing',%s,%s)",
            (deal, client, joe, joe),
        )
        cur.execute(
            "insert into deal_participant(deal_id,actor_id,role,set_by) values(%s,%s,'lead',%s)",
            (deal, joe, joe),
        )

        cur.execute("set session authorization carr_writer")
        refused(
            cur,
            """insert into lease
                 (deal_id,client_id,owner_id,executed_on,expiration_on,evidence_kind,evidence_ref,
                  source,status,created_by)
               values(%s,%s,%s,current_date,current_date+365,'executed_lease','forged',
                      'forged direct writer row','current',%s)""",
            (deal, client, joe, joe),
            "permission denied",
        )
        refused(
            cur,
            "select * from ops.record_executed_lease(%s,null,current_date,current_date,current_date+365,12,'executed_lease','forged','forged')",
            (str(deal),),
            "permission denied",
        )
        cur.execute("reset session authorization")

        cur.execute("set session authorization carr_authority_joe")
        created = record(cur, deal, None)
        lease_id = created[0]
        if created[1:] != (1, None, deal, client):
            raise RuntimeError(f"Joe authority received wrong lease readback: {created}")
        refused(
            cur,
            """select * from ops.record_executed_lease
                 (%s,1,current_date,current_date,current_date+365,12,
                  'web_research','ref','source')""",
            (str(deal),),
            "evidence kind is not admitted",
        )
        cur.execute("reset session authorization")

        cur.execute("set session authorization carr_reader")
        joe_status = one(cur, "select t1_candidate_count,freshness_state from v_renewal_decision_queue_status where owner_slug='joe'")
        dell_status = one(cur, "select t1_candidate_count,freshness_state from v_renewal_decision_queue_status where owner_slug='dell'")
        if joe_status != (1, "ready") or dell_status != (0, "empty"):
            raise RuntimeError(f"lease provider sponsor states are wrong: joe={joe_status} dell={dell_status}")
        row = one(cur, "select display_name,tier_status,has_channel,owner_slug from v_renewal_decision_queue where owner_slug='joe'")
        if row != ("Lease Ledger Fixture", "t1", True, "joe"):
            raise RuntimeError(f"lease provider rendered the wrong safe row: {row}")
        refused(cur, "select * from lease", (), "permission denied")
        cur.execute("reset session authorization")

        # Ownership is dynamic. A current lead transfer immediately moves the
        # safe reader and the authority to Dell without rewriting lease facts.
        cur.execute("update deal_participant set to_at=now() where deal_id=%s and role='lead' and to_at is null", (deal,))
        cur.execute(
            "insert into deal_participant(deal_id,actor_id,role,set_by) values(%s,%s,'lead',%s)",
            (deal, dell, joe),
        )
        if one(cur, "select count(*) from v_renewal_decision_queue where owner_slug='joe'") != (0,):
            raise RuntimeError("Joe retained a renewal after the deal transferred")
        if one(cur, "select count(*) from v_renewal_decision_queue where owner_slug='dell'") != (1,):
            raise RuntimeError("Dell did not receive the transferred renewal")
        cur.execute("set session authorization carr_authority_joe")
        refused(cur, "select * from ops.record_executed_lease(%s,1,current_date,current_date,current_date+400,12,'lease_abstract','ref','source')", (str(deal),), "does not own")
        cur.execute("reset session authorization")
        cur.execute("set session authorization carr_authority_dell")
        replaced = record(cur, deal, 1, expiration="400 days", evidence_kind="lease_amendment")
        if replaced[2] != lease_id:
            raise RuntimeError("Dell replacement did not supersede the exact Joe lease")
        cur.execute("reset session authorization")
        if one(cur, "select status from lease where id=%s", (lease_id,)) != ("superseded",):
            raise RuntimeError("old lease was not retained as superseded history")

        cur.execute("set session authorization carr_writer")
        refused(cur, "update lease set expiration_on=current_date+1 where id=%s", (replaced[0],), "permission denied")
        cur.execute("reset session authorization")
    print("renewal lease ledger local acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
