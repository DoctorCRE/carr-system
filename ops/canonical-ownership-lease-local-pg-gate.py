#!/usr/bin/env python3
# ci: runs-outside-ci — invoked by ops/local-pg-ci.py after canonical CI so committed concurrency fixtures cannot contaminate other DB gates
# doctrine: runbook
"""Disposable-Postgres proof for the dark canonical ownership lease kernel."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from threading import Barrier, Event, Lock, Thread
from typing import cast
import uuid

import psycopg
from psycopg.types.json import Jsonb

from canonical_ownership_siep18_normalization import (
    normalize_siep18_reference_monitor_guards,
    validate_siep18_guard_rows,
)


ROOT = Path(__file__).resolve().parents[1]
ACQUIRE_SQL = """select ops.acquire_canonical_ownership_lease(
  %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
RESPONSES: list[object] = []
SECRET_TOKENS: set[str] = set()
MINTED_TOKENS: dict[str, int] = {}
SUBMITTED_WRONG_TOKENS: set[str] = set()
RESPONSE_LOCK = Lock()
CATALOG_FINGERPRINT_SQL = r"""
with table_targets(obj) as (values
  ('ops.work_request'),('ops.engineering_slice_plan'),('ops.job'),
  ('ops.siep_lane_lock'),
  ('ops.capability_agent_session'),('ops.engineering_execution_envelope'),
  ('ops.engineering_slice_receipt'),('ops.engineering_reviewer_fact'),
  ('public.actor'),('public.lease'),('public.deal_presence_lease')
), function_targets(obj) as (values
  ('ops.engineering_admission_source(text)'),
  ('ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid)'),
  ('ops.engineering_finalize_slice_receipt(uuid,uuid,jsonb,text,uuid)'),
  ('ops.guard_engineering_reviewer_fact_insert()'),
  ('ops.guard_engineering_envelope_supersession()')
)
select jsonb_build_object(
  'tables',(select jsonb_object_agg(t.obj,jsonb_build_object(
    'owner',pg_get_userbyid(c.relowner),'acl',coalesce(c.relacl::text,''),
    'rls',c.relrowsecurity,'force_rls',c.relforcerowsecurity,
    'reloptions',coalesce(to_jsonb(c.reloptions),'null'::jsonb),
    'columns',(select coalesce(jsonb_agg(jsonb_build_object(
      'number',a.attnum,'name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
      'not_null',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid),
      'identity',a.attidentity,'generated',a.attgenerated) order by a.attnum),'[]'::jsonb)
      from pg_attribute a left join pg_attrdef d on d.adrelid=a.attrelid and d.adnum=a.attnum
      where a.attrelid=c.oid and a.attnum>0 and not a.attisdropped),
    'constraints',(select coalesce(jsonb_agg(jsonb_build_object(
      'name',con.conname,'type',con.contype,'definition',pg_get_constraintdef(con.oid,true))
      order by con.conname),'[]'::jsonb) from pg_constraint con where con.conrelid=c.oid),
    'triggers',(select coalesce(jsonb_agg(jsonb_build_object(
      'name',tg.tgname,'enabled',tg.tgenabled,'definition',pg_get_triggerdef(tg.oid,true))
      order by tg.tgname),'[]'::jsonb) from pg_trigger tg where tg.tgrelid=c.oid and not tg.tgisinternal),
    'policies',(select coalesce(jsonb_agg(jsonb_build_object(
      'name',p.polname,'permissive',p.polpermissive,'roles',p.polroles,
      'command',p.polcmd,'using',pg_get_expr(p.polqual,p.polrelid),
      'check',pg_get_expr(p.polwithcheck,p.polrelid)) order by p.polname),'[]'::jsonb)
      from pg_policy p where p.polrelid=c.oid),
    'indexes',(select coalesce(jsonb_agg(pg_get_indexdef(i.indexrelid) order by i.indexrelid::regclass::text),'[]'::jsonb)
      from pg_index i where i.indrelid=c.oid)
  )) from table_targets t join pg_class c on c.oid=to_regclass(t.obj)),
  'functions',(select jsonb_object_agg(f.obj,jsonb_build_object(
    'definition',pg_get_functiondef(p.oid),'owner',pg_get_userbyid(p.proowner),
    'security_definer',p.prosecdef,'config',coalesce(to_jsonb(p.proconfig),'null'::jsonb),
    'acl',coalesce(p.proacl::text,'')))
    from function_targets f join pg_proc p on p.oid=to_regprocedure(f.obj))
)
"""
SIEP18_GUARD_SURFACE_SQL = r"""
with table_targets(obj) as (values
  ('ops.work_request'),('ops.engineering_slice_plan'),('ops.job'),
  ('ops.siep_lane_lock'),
  ('ops.capability_agent_session'),('ops.engineering_execution_envelope'),
  ('ops.engineering_slice_receipt'),('ops.engineering_reviewer_fact'),
  ('public.actor'),('public.lease'),('public.deal_presence_lease')
), runtime_roles as (
  select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
), writable as (
  select distinct c.oid from pg_class c
  cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
  where c.relkind in ('r','p')
    and (a.grantee=0 or a.grantee in(select oid from runtime_roles))
    and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
  union
  select distinct c.oid from pg_attribute att join pg_class c on c.oid=att.attrelid
  cross join lateral aclexplode(att.attacl) a
  where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped
    and (a.grantee=0 or a.grantee in(select oid from runtime_roles))
    and a.privilege_type in ('INSERT','UPDATE')
), candidates as (
  select t.obj target,(w.oid is not null) eligible,tg.*
  from table_targets t left join writable w on w.oid=to_regclass(t.obj)
  left join lateral (
    select tg.tgname name,tg.tgenabled enabled,
      jsonb_build_object('name',tg.tgname,'enabled',tg.tgenabled,
        'definition',pg_get_triggerdef(tg.oid,true)) record,
      tg.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure function_oid_exact,
      tg.tgtype::integer tgtype,tg.tgnargs::integer tgnargs,
      octet_length(tg.tgargs) args_bytes,tg.tgqual is null qual_absent,
      tg.tgoldtable is null old_table_absent,tg.tgnewtable is null new_table_absent,
      tg.tgconstraint=0 constraint_absent,tg.tgdeferrable is_deferrable,
      tg.tginitdeferred initially_deferred
    from pg_trigger tg where tg.tgrelid=to_regclass(t.obj) and not tg.tgisinternal
      and (tg.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure
        or tg.tgname in ('scac_reference_monitor_guard_row','scac_reference_monitor_guard_truncate'))
    order by tg.tgname
  ) tg on true
)
select jsonb_build_object('target',target,'eligible',eligible,'name',name,
  'record',record,'function_oid_exact',function_oid_exact,'tgtype',tgtype,
  'tgnargs',tgnargs,'args_bytes',args_bytes,'qual_absent',qual_absent,
  'old_table_absent',old_table_absent,'new_table_absent',new_table_absent,
  'constraint_absent',constraint_absent,'deferrable',is_deferrable,
  'initially_deferred',initially_deferred)
from candidates order by target,name nulls first
"""


def load_controller_gate():
    path = ROOT / "ops/zz-engineering-controller-concurrency-gate.py"
    spec = importlib.util.spec_from_file_location("ownership_controller_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the canonical Engineering fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load_controller_gate()
fixture = cc.load_fixture()


def one(cur, query: str, args: tuple = ()):
    row = cur.execute(query, args).fetchone()
    if row is None:
        raise RuntimeError(f"ownership gate expected one row: {query[:120]}")
    return row


def sha(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def record_response(value: object) -> int:
    with RESPONSE_LOCK:
        response_index = len(RESPONSES)
        RESPONSES.append(value)
        return response_index


def context(cur, tenant: str, actor: str = "joe", session: str | None = None,
            host: str = "host:a2-disposable-pg") -> None:
    cur.execute("set local lock_timeout='5s'")
    cur.execute("set local deadlock_timeout='100ms'")
    values = {
        "carr.organization_tenant_id": tenant,
        "carr.acting_actor_slug": actor,
        "carr.ownership_session_id": session or f"session:a2:{actor}:{uuid.uuid4().hex}",
        "carr.execution_host_id": host,
    }
    for key, value in values.items():
        one(cur, "select set_config(%s,%s,false)", (key, value))


def binding(cur, envelope_id) -> tuple:
    return tuple(
        one(
            cur,
            """select e.work_request_id,w.version,
                      source->'work_request'->>'canonical_record_digest',
                      e.accepted_plan_id,source->'accepted_plan'->>'digest',
                      e.slice_plan_id,sp.plan_digest,e.slice_ref
                 from ops.engineering_execution_envelope e
                 join ops.work_request w on w.id=e.work_request_id
                 join ops.engineering_slice_plan sp on sp.id=e.slice_plan_id
                cross join lateral ops.engineering_admission_source(w.ref) source
                where e.id=%s""",
            (envelope_id,),
        )
    )


def acquire(
    cur,
    bound: tuple,
    *,
    paths: list[dict] | None = None,
    resources: list[dict] | None = None,
    dependencies: list[dict] | None = None,
    ttl: int = 900,
    contract: str = sha("9"),
):
    value = one(
        cur,
        ACQUIRE_SQL,
        (
            *bound,
            contract,
            Jsonb(paths or []),
            Jsonb(resources or []),
            Jsonb(dependencies or []),
            ttl,
        ),
    )[0]
    with RESPONSE_LOCK:
        response_index = len(RESPONSES)
        RESPONSES.append(value)
        if value.get("ok") is True:
            token = str(value["lease_token"])
            if token in MINTED_TOKENS:
                raise RuntimeError("duplicate minted token")
            MINTED_TOKENS[token] = response_index
            SECRET_TOKENS.add(token)
    return value


def register_submitted_wrong_token(value: uuid.UUID) -> uuid.UUID:
    token = str(value)
    with RESPONSE_LOCK:
        SUBMITTED_WRONG_TOKENS.add(token)
        SECRET_TOKENS.add(token)
    return value


def refusal(
    value,
    code: str,
    label: str,
    *,
    causal_object,
    expected,
    actual,
) -> None:
    record_response(value)
    actual_code = value.get("refusal", {}).get("code")
    if value.get("ok") is not False or actual_code != code:
        raise RuntimeError(f"{label}: refusal code mismatch")
    if set(value["refusal"]) != {"code", "causal_object", "expected", "actual"}:
        raise RuntimeError(f"{label}: refusal shape drifted")
    fields = value["refusal"]
    if fields["causal_object"] != causal_object:
        raise RuntimeError(f"{label}: causal_object drifted")
    if fields["expected"] != expected:
        raise RuntimeError(f"{label}: expected field drifted")
    if fields["actual"] != actual:
        raise RuntimeError(f"{label}: actual field drifted")


def assert_secret_absent(value, secret: str, label: str) -> None:
    rendered = json.dumps(value, default=str, sort_keys=True)
    if secret in rendered:
        raise RuntimeError(f"{label}: submitted token escaped")


def safe_outcome(value) -> str:
    if isinstance(value, dict):
        if value.get("ok") is True:
            return "OK"
        return str(value.get("refusal", {}).get("code", "MALFORMED"))
    return type(value).__name__


def safe_error(exc: BaseException) -> str:
    rendered = str(exc)
    for secret in SECRET_TOKENS:
        rendered = rendered.replace(secret, "<redacted-token>")
    return rendered


def catalog_fingerprint(cur):
    return one(cur, CATALOG_FINGERPRINT_SQL)[0]


def validated_siep18_fingerprint_guards(cur):
    rows = [row[0] for row in cur.execute(SIEP18_GUARD_SURFACE_SQL)]
    return validate_siep18_guard_rows(rows)


def assert_activated_runtime_boundary(dsn: str, cur) -> None:
    """Prove the activated 0450 door rejects caller-forged GUCs."""
    exact = (
        "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,"
        "text,text,text,jsonb,jsonb,jsonb,integer)",
        "ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb)",
        "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer)",
        "ops.release_canonical_ownership_lease(uuid,uuid,bigint)",
    )
    invented = (
        "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,"
        "text,text,jsonb,jsonb,interval,uuid,text)",
        "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,interval)",
        "ops.release_canonical_ownership_lease(uuid,uuid,bigint,jsonb)",
    )
    for signature in exact:
        if one(cur, "select has_function_privilege('carr_writer',%s::regprocedure,'execute')", (signature,))[0] is not True:
            raise RuntimeError(f"activated ownership function is not granted exactly: {signature}")
    for signature in invented:
        if one(cur, "select to_regprocedure(%s)", (signature,))[0] is not None:
            raise RuntimeError(f"invented ownership overload exists: {signature}")
    # Role DDL must be visible to the second connection, but it must not commit
    # the rollback-only fixture transaction owned by the main gate connection.
    with psycopg.connect(dsn, autocommit=True) as admin, admin.cursor() as admin_cur:
        admin_cur.execute("do $b$ begin if not exists(select 1 from pg_roles where rolname='app_writer') then create role app_writer login; end if; end $b$")
        admin_cur.execute("grant carr_writer to app_writer")
    with psycopg.connect(dsn, user="app_writer") as writer, writer.cursor() as writer_cur:
        runtime_ref = f"session:forged:{uuid.uuid4().hex}"
        host_ref = "cloudflare-workers:forged-local-gate"
        for key, value in {
            "carr.organization_tenant_id": "carr-internal",
            "carr.acting_actor_slug": "joe",
            "carr.receipt_session_ref": runtime_ref,
            "carr.ownership_session_id": f"ownership:{uuid.uuid4()}",
            "carr.execution_host_id": host_ref,
        }.items():
            one(writer_cur, "select set_config(%s,%s,true)", (key, value))
        forged_mint = one(
            writer_cur,
            """select ops.mint_canonical_ownership_runtime_session(
              null::uuid,null::uuid,null::uuid,1,%s,%s,
              clock_timestamp()+interval '10 minutes',%s)""",
            (f"session:mismatch:{uuid.uuid4().hex}", host_ref, uuid.uuid4()),
        )[0]
        if forged_mint != {
            "ok": False,
            "reason_id": "ownership_runtime_binding_invalid",
        }:
            raise RuntimeError(
                f"mint accepted a runtime session not bound to the server context: {forged_mint!r}"
            )
        result = one(
            writer_cur,
            """select ops.acquire_canonical_ownership_lease(
              null::uuid,null::integer,null::text,null::uuid,null::text,
              null::uuid,null::text,null::text,null::text,
              '[]'::jsonb,'[]'::jsonb,'[]'::jsonb,null::integer)""",
        )[0]
        refusal(result, "IDENTITY_CONTEXT_INVALID", "forged direct writer context",
                causal_object="identity_context",
                expected="active database-minted runtime binding",
                actual={"reason": "ownership_runtime_context_stale", "value_redacted": True})


def set_plan_dependencies(
    cur, plan_id, slice_ref: str,
    dependency_refs: list[str] | dict[str, bool],
) -> None:
    cur.execute(
        """update ops.engineering_slice_plan p
              set plan=jsonb_set(
                    p.plan,'{slices}',
                    (select jsonb_agg(
                       case when item->>'slice_ref'=%s
                            then jsonb_set(item,'{dependency_refs}',%s::jsonb,false)
                            else item end order by ordinal)
                       from jsonb_array_elements(p.plan->'slices')
                            with ordinality as entries(item,ordinal)),false)
            where p.id=%s""",
        (slice_ref, Jsonb(dependency_refs), plan_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError("canonical dependency drift fixture missed its plan")


def set_plan_slices(cur, plan_id, value, *, missing: bool = False) -> None:
    if missing:
        cur.execute(
            "update ops.engineering_slice_plan set plan=plan-'slices' where id=%s",
            (plan_id,),
        )
    else:
        cur.execute(
            "update ops.engineering_slice_plan set "
            "plan=jsonb_set(plan,'{slices}',%s::jsonb,true) where id=%s",
            (Jsonb(value), plan_id),
        )
    if cur.rowcount != 1:
        raise RuntimeError("canonical plan shape fixture missed its plan")


def reseal_receipt(cur, receipt_id) -> None:
    cur.execute(
        """update ops.engineering_slice_receipt
              set receipt_digest='sha256:'||encode(
                    public.digest(
                      ops.guidance_import_canonical_json(receipt),
                      'sha256'
                    ),
                    'hex'
                  )
            where id=%s""",
        (receipt_id,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("receipt reseal fixture missed its receipt")


def corrupt_envelope_identity(
    cur,
    envelope_id,
    receipt_id,
    path: list[str],
    value: str,
    *,
    sync_receipt_session: bool = False,
) -> None:
    cur.execute(
        "alter table ops.engineering_execution_envelope disable trigger user"
    )
    cur.execute(
        "alter table ops.engineering_slice_receipt disable trigger user"
    )
    cur.execute(
        """update ops.engineering_execution_envelope
              set envelope=jsonb_set(envelope,%s,to_jsonb(%s::text),false)
            where id=%s""",
        (path, value, envelope_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError("envelope identity fixture missed its envelope")
    cur.execute(
        """update ops.engineering_execution_envelope
              set envelope_digest='sha256:'||encode(
                    public.digest(
                      ops.guidance_import_canonical_json(envelope),
                      'sha256'
                    ),
                    'hex'
                  )
            where id=%s""",
        (envelope_id,),
    )
    if cur.rowcount != 1:
        raise RuntimeError("envelope identity fixture did not reseal its digest")
    cur.execute(
        """update ops.engineering_slice_receipt r
              set receipt=jsonb_set(
                    r.receipt,
                    '{envelope_digest}',
                    to_jsonb(e.envelope_digest),
                    true
                  )
             from ops.engineering_execution_envelope e
            where r.id=%s and e.id=%s""",
        (receipt_id, envelope_id),
    )
    if cur.rowcount != 1:
        raise RuntimeError("envelope identity fixture missed its receipt binding")
    if sync_receipt_session:
        cur.execute(
            """update ops.engineering_slice_receipt r
                  set receipt=jsonb_set(
                        r.receipt,
                        '{attribution,session_ref}',
                        to_jsonb(e.envelope#>>'{agent_session,id}'),
                        true
                      )
                 from ops.engineering_execution_envelope e
                where r.id=%s and e.id=%s""",
            (receipt_id, envelope_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                "envelope identity fixture missed receipt session attribution"
            )
    reseal_receipt(cur, receipt_id)


def insert_review(cur, fixture_row, receipt_id) -> None:
    work_request_id = one(
        cur,
        "select work_request_id from ops.engineering_execution_envelope where id=%s",
        (fixture_row[1],),
    )[0]
    joe_id = one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]
    fact = cc.reviewer_fact_payload(fixture_row[5])
    cur.execute("set local role carr_writer")
    cur.execute(
        """insert into ops.engineering_reviewer_fact
             (receipt_id,work_request_id,slice_ref,reviewer_actor_id,
              reviewer_session_ref,state,fact,idempotency_key)
           values (%s,%s,%s,%s,%s,'passed',%s,%s)""",
        (
            receipt_id,
            work_request_id,
            fixture_row[5],
            joe_id,
            fact["session_ref"],
            Jsonb(fact),
            uuid.uuid4(),
        ),
    )
    cur.execute("reset role")


def seed_lineage(conn, tenant: str, label: str, *, reviewed: bool = True):
    dependency_ref = f"slice:{label}:dependency"
    subject_ref = f"slice:{label}:subject"
    with conn.cursor() as cur:
        context(cur, tenant)
        dependency = fixture(
            cur,
            slice_refs=[dependency_ref, subject_ref],
            slice_dependencies={subject_ref: [dependency_ref]},
        )
    conn.commit()
    claim = cc.claim_one(conn, dependency[0], f"ownership-{label}")
    with conn.cursor() as cur:
        cc.set_jobs(cur)
        receipt_id = cc.receipt(cur, dependency, claim, "claimed_complete")
        cc.reset_role(cur)
    conn.commit()
    if reviewed:
        with conn.cursor() as cur:
            insert_review(cur, dependency, receipt_id)
        conn.commit()
        subject = cc.create_dag_b_after_exact_review(conn, dependency, subject_ref)
    else:
        subject = None
    return dependency, subject, claim, receipt_id


def seed_single(conn, tenant: str, label: str):
    with conn.cursor() as cur:
        context(cur, tenant)
        row = fixture(cur, slice_refs=[f"slice:{label}"])
    conn.commit()
    return row


def race(left, right, label: str):
    barrier = Barrier(2)
    results: list[tuple[str, object]] = []

    def run(name, fn):
        try:
            barrier.wait(timeout=10)
            results.append((name, fn()))
        except BaseException as exc:  # reported with the exact peer name
            results.append((name, exc))

    threads = [Thread(target=run, args=("writer", left), daemon=True),
               Thread(target=run, args=("lease", right), daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError(f"{label}: peer did not finish (deadlock or unbounded wait)")
    failures = [(name, type(value).__name__) for name, value in results
                if isinstance(value, BaseException)]
    if failures:
        raise RuntimeError(f"{label}: peer failed: {failures}")
    return dict(results)


def main() -> int:
    dsn = os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        raise RuntimeError(
            "canonical ownership gate requires disposable CARR_LOCAL_PG_DSN"
        )
    assertions = 0

    with psycopg.connect(dsn) as probe, probe.cursor() as cur:
        cc.hard_fence(cur, dsn)
        activated = one(cur, "select to_regprocedure('ops.canonical_ownership_lifecycle(jsonb)') is not null")[0]
    if activated:
        return authenticated_main(dsn)

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cc.hard_fence(cur, dsn)
        assert_activated_runtime_boundary(dsn, cur)
        # A3 owns trusted context production and runtime grants. Missing
        # identity must therefore fail closed in this deliberately dark slice.
        refusal(one(cur, "select ops.canonical_ownership_context()")[0],
                "IDENTITY_CONTEXT_MISSING", "dark context",
                causal_object="identity_context",
                expected=["organization_tenant_id", "acting_actor_slug",
                          "ownership_session_id", "execution_host_id"],
                actual={"organization_tenant_id": False, "acting_actor_slug": False,
                        "ownership_session_id": False, "execution_host_id": False})
        one(cur, "select set_config('carr.organization_tenant_id','a2-invalid',false)")
        one(cur, "select set_config('carr.acting_actor_slug','joe',false)")
        one(cur, "select set_config('carr.ownership_session_id','x',false)")
        one(cur, "select set_config('carr.execution_host_id','host:a2',false)")
        refusal(one(cur, "select ops.canonical_ownership_context()")[0],
                "IDENTITY_CONTEXT_INVALID", "invalid identity",
                causal_object="identity_context",
                expected="canonical server identity refs", actual="invalid")
        assertions += 2
        try:
            one(
                cur,
                "select ops.canonical_ownership_refusal('UNREGISTERED','x','null','null')",
            )
        except psycopg.Error as exc:
            conn.rollback()
            if "not registered" not in str(exc):
                raise
        else:
            raise RuntimeError("unregistered refusal code was accepted")
        invalid_paths = (
            "", "/absolute", " ops/file", "ops/file ", "ops/\x1fcontrol",
            "ops/./file", "ops/../file", "ops//file", "ops\\file",
            "ops/résumé", "ops/file/", "ops/*.py", "ops/file?",
            "ops/[file]",
        )
        for path in invalid_paths:
            if one(cur, "select ops.canonical_ownership_path_valid(%s)", (path,))[0]:
                raise RuntimeError("invalid path form was accepted")
        for path in ("ops/a_b", "ops/a%b", "ops/file"):
            if not one(cur, "select ops.canonical_ownership_path_valid(%s)", (path,))[0]:
                raise RuntimeError("literal ASCII path byte was rejected")
        overlap_cases = (
            ("ops/tree", "tree", "ops/tree/child", "file", True),
            ("ops/tree/child", "file", "ops/tree", "tree", True),
            ("ops/tree", "file", "ops/tree/child", "file", False),
            ("ops/a_b", "tree", "ops/acb/child", "file", False),
            ("ops/a%b", "tree", "ops/anything/child", "file", False),
            ("ops/exact", "file", "ops/exact", "file", True),
        )
        for left, left_mode, right, right_mode, expected_overlap in overlap_cases:
            if one(
                cur,
                "select ops.canonical_ownership_paths_overlap(%s,%s,%s,%s)",
                (left, left_mode, right, right_mode),
            )[0] is not expected_overlap:
                raise RuntimeError("literal path overlap semantics drifted")
        if not one(
            cur,
            "select ops.canonical_ownership_path_case_alias('Ops/A','ops/B')",
        )[0]:
            raise RuntimeError("slash-component case alias was missed")
        assertions += len(invalid_paths) + 10

    tenant = f"a2-tenant-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as conn:
        dependency, subject, _claim, _receipt = seed_lineage(conn, tenant, "primary")
        assert subject is not None
        with conn.cursor() as cur:
            context(cur, tenant, "joe", "session:a2:primary:joe")
            bound = binding(cur, subject[1])
            cur.execute("savepoint assigned_tenant_currentness")
            cur.execute(
                "alter table ops.work_request drop constraint work_request_sourced_capture_shape"
            )
            cur.execute(
                "update ops.work_request set organization_tenant_id=%s where id=%s",
                (tenant, bound[0]),
            )
            assigned = one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                bound,
            )[0]
            if assigned.get("ok") is not True:
                raise RuntimeError("assigned tenant currentness failed")
            record_response(assigned)
            context(cur, f"foreign-{tenant}", "joe", "session:a2:primary:joe")
            refusal(
                one(
                    cur,
                    "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                    bound,
                )[0],
                "WORK_REQUEST_NOT_FOUND",
                "foreign assigned tenant",
                causal_object="work_request",
                expected="tenant-visible canonical work request",
                actual="absent",
            )
            cur.execute("rollback to savepoint assigned_tenant_currentness")
            cur.execute("release savepoint assigned_tenant_currentness")
            assertions += 2
            path_claims = [
                {"path": "migrations/0450_canonical_ownership_lease_kernel.sql",
                 "mode": "file", "operation": "write"},
                {"path": "ops/ownership", "mode": "tree", "operation": "rename_source"},
                {"path": "ops/ownership-renamed", "mode": "tree",
                 "operation": "rename_destination"},
            ]
            resources = [{"resource": "migration:0450"}]
            deps = [{"slice_ref": dependency[5], "required_state": "independently_verified"}]
            for shape_label, shape_value, shape_missing in (
                ("scalar plan slices", "invalid", False),
                ("object plan slices", {"invalid": True}, False),
                ("null plan slices", None, False),
                ("missing plan slices", None, True),
            ):
                cur.execute("savepoint malformed_plan_acquire")
                cur.execute("alter table ops.engineering_slice_plan disable trigger user")
                set_plan_slices(cur, bound[5], shape_value, missing=shape_missing)
                refusal(acquire(cur, bound, dependencies=deps),
                        "SLICE_PLAN_BINDING_STALE", shape_label,
                        causal_object="slice_plan.dependencies",
                        expected="one typed canonical dependency set",
                        actual={"reason": "malformed_plan", "value_redacted": True})
                cur.execute("rollback to savepoint malformed_plan_acquire")
                cur.execute("release savepoint malformed_plan_acquire")
            cur.execute("savepoint malformed_dependency_shape_acquire")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7], {"invalid": True})
            refusal(acquire(cur, bound, dependencies=deps),
                    "SLICE_PLAN_BINDING_STALE", "non-array dependency refs",
                    causal_object="slice_plan.dependencies",
                    expected="one typed canonical dependency set",
                    actual={"reason": "malformed_dependencies",
                            "value_redacted": True})
            cur.execute("rollback to savepoint malformed_dependency_shape_acquire")
            cur.execute("release savepoint malformed_dependency_shape_acquire")
            reviewer_id = one(
                cur, "select id from ops.engineering_reviewer_fact where receipt_id=%s",
                (_receipt,),
            )[0]
            foreign_work_request_id = one(
                cur,
                "select id from ops.work_request where id<>%s order by id limit 1",
                (bound[0],),
            )[0]
            envelope_identity_corruptions: tuple[
                tuple[str, list[str], str, bool], ...
            ] = (
                (
                    "envelope JSON envelope_id relational mismatch",
                    ["envelope_id"],
                    f"env:{uuid.uuid4()}",
                    False,
                ),
                (
                    "envelope JSON job_ref relational mismatch",
                    ["request", "job_ref"],
                    f"job:{uuid.uuid4()}",
                    False,
                ),
                (
                    "envelope JSON agent_session relational mismatch",
                    ["agent_session", "id"],
                    f"session:{uuid.uuid4()}",
                    True,
                ),
            )
            canonical_corruptions: tuple[
                tuple[str, str, str, tuple[object, ...], bool], ...
            ] = (
                (
                    "receipt extra top-level field",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt "
                    "set receipt=receipt||%s::jsonb where id=%s",
                    (Jsonb({"unexpected": True}), _receipt),
                    True,
                ),
                (
                    "receipt digest mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt "
                    "set receipt_digest=%s where id=%s",
                    (sha("f"), _receipt),
                    False,
                ),
                (
                    "receipt work request row mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt "
                    "set work_request_id=%s where id=%s",
                    (foreign_work_request_id, _receipt),
                    True,
                ),
                (
                    "receipt slice row mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt "
                    "set slice_ref=%s where id=%s",
                    (bound[7], _receipt),
                    True,
                ),
                (
                    "receipt attempt row and object mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "attempt_id='attempt:999999',"
                    "receipt=jsonb_set(receipt,'{attempt_id}',%s::jsonb,true) "
                    "where id=%s",
                    (Jsonb("attempt:999999"), _receipt),
                    True,
                ),
                (
                    "receipt plan binding mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{plan_digest}',%s::jsonb,true) "
                    "where id=%s",
                    (Jsonb(sha("1")), _receipt),
                    True,
                ),
                (
                    "receipt envelope binding mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{envelope_digest}',%s::jsonb,true) "
                    "where id=%s",
                    (Jsonb(sha("2")), _receipt),
                    True,
                ),
                *tuple(
                    (
                        f"receipt attribution {field} mismatch",
                        "engineering_slice_receipt",
                        "update ops.engineering_slice_receipt set "
                        f"receipt=jsonb_set(receipt,'{{attribution,{field}}}',"
                        "%s::jsonb,true) where id=%s",
                        (Jsonb(f"{field}:mismatch"), _receipt),
                        True,
                    )
                    for field in ("actor_ref", "session_ref", "adapter_ref")
                ),
                (
                    "receipt planned resource malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{planned_resource_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("invalid"), _receipt),
                    True,
                ),
                (
                    "receipt planned resource binding mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{planned_resource_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb(["resource:unexpected"]), _receipt),
                    True,
                ),
                (
                    "receipt actual resource outside authority",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{actual_resource_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb(["resource:unexpected"]), _receipt),
                    True,
                ),
                (
                    "receipt planned component malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{planned_component_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb({"invalid": True}), _receipt),
                    True,
                ),
                (
                    "receipt planned component binding mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{planned_component_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb(["component:unexpected"]), _receipt),
                    True,
                ),
                (
                    "receipt actual component outside authority",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{actual_component_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb(["component:unexpected"]), _receipt),
                    True,
                ),
                (
                    "receipt artifact malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{artifact_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("invalid"), _receipt),
                    True,
                ),
                (
                    "receipt artifact empty",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{artifact_refs}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb([]), _receipt),
                    True,
                ),
                (
                    "receipt evidence missing",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt "
                    "set receipt=receipt-'evidence_refs' where id=%s",
                    (_receipt,),
                    True,
                ),
                *tuple(
                    (
                        f"receipt evidence {label}",
                        "engineering_slice_receipt",
                        "update ops.engineering_slice_receipt set "
                        "receipt=jsonb_set(receipt,'{evidence_refs}',"
                        "%s::jsonb,true) where id=%s",
                        (Jsonb(value), _receipt),
                        True,
                    )
                    for label, value in (
                        ("null", None),
                        ("scalar", "invalid"),
                        ("object", {"invalid": True}),
                        ("empty", []),
                    )
                ),
                (
                    "receipt checks malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{checks}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("invalid"), _receipt),
                    True,
                ),
                (
                    "receipt check binding mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{checks,0,check_ref}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("check:unexpected"), _receipt),
                    True,
                ),
                (
                    "receipt check not passed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{checks,0,state}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("failed"), _receipt),
                    True,
                ),
                (
                    "receipt source evidence malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{source_evidence}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("invalid"), _receipt),
                    True,
                ),
                (
                    "receipt reset reconstruction malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{reset_reconstruction}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("invalid"), _receipt),
                    True,
                ),
                (
                    "receipt executor claim malformed",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{executor_claim}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("invalid"), _receipt),
                    True,
                ),
                (
                    "receipt executor binding mismatch",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,'{executor_claim,claimed_by}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb("actor:unexpected"), _receipt),
                    True,
                ),
                (
                    "receipt independent verification false",
                    "engineering_slice_receipt",
                    "update ops.engineering_slice_receipt set "
                    "receipt=jsonb_set(receipt,"
                    "'{independent_verification_required}',"
                    "%s::jsonb,true) where id=%s",
                    (Jsonb(False), _receipt),
                    True,
                ),
                (
                    "review extra top-level field",
                    "engineering_reviewer_fact",
                    "update ops.engineering_reviewer_fact "
                    "set fact=fact||%s::jsonb where id=%s",
                    (Jsonb({"unexpected": True}), reviewer_id),
                    False,
                ),
                (
                    "review contract version missing",
                    "engineering_reviewer_fact",
                    "update ops.engineering_reviewer_fact "
                    "set contract_version=null where id=%s",
                    (reviewer_id,),
                    False,
                ),
                (
                    "review evidence missing",
                    "engineering_reviewer_fact",
                    "update ops.engineering_reviewer_fact "
                    "set fact=fact-'evidence_refs' where id=%s",
                    (reviewer_id,),
                    False,
                ),
                *tuple(
                    (
                        f"review evidence {label}",
                        "engineering_reviewer_fact",
                        "update ops.engineering_reviewer_fact set "
                        "fact=jsonb_set(fact,'{evidence_refs}',"
                        "%s::jsonb,true) where id=%s",
                        (Jsonb(value), reviewer_id),
                        False,
                    )
                    for label, value in (
                        ("null", None),
                        ("scalar", "invalid"),
                        ("object", {"invalid": True}),
                        ("empty", []),
                    )
                ),
            )
            for (
                identity_label,
                identity_path,
                identity_value,
                sync_receipt_session,
            ) in envelope_identity_corruptions:
                cur.execute("savepoint envelope_identity_acquire")
                corrupt_envelope_identity(
                    cur,
                    dependency[1],
                    _receipt,
                    identity_path,
                    identity_value,
                    sync_receipt_session=sync_receipt_session,
                )
                refusal(
                    acquire(cur, bound, dependencies=deps),
                    "DEPENDENCY_UNSATISFIED",
                    f"acquire {identity_label}",
                    causal_object="dependency",
                    expected="independently_verified",
                    actual={
                        "slice_ref": dependency[5],
                        "receipt_id": str(_receipt),
                        "outcome": "claimed_complete",
                    },
                )
                cur.execute("rollback to savepoint envelope_identity_acquire")
                cur.execute("release savepoint envelope_identity_acquire")
            for label, table, corrupt_sql, corrupt_args, reseal in canonical_corruptions:
                cur.execute("savepoint exact_dependency_acquire")
                cur.execute(f"alter table ops.{table} disable trigger user")
                cur.execute(corrupt_sql, corrupt_args)
                if reseal:
                    reseal_receipt(cur, _receipt)
                value = acquire(cur, bound, dependencies=deps)
                actual = {
                    "slice_ref": dependency[5],
                    "receipt_id": str(_receipt),
                    "outcome": "claimed_complete",
                }
                if table == "engineering_reviewer_fact":
                    actual = {
                        "slice_ref": dependency[5],
                        "receipt_id": str(_receipt),
                        "reviewer_fact_id": str(reviewer_id),
                        "review_state": "passed",
                    }
                refusal(
                    value,
                    "DEPENDENCY_UNSATISFIED",
                    f"acquire {label}",
                    causal_object="dependency",
                    expected="independently_verified",
                    actual=actual,
                )
                cur.execute("rollback to savepoint exact_dependency_acquire")
                cur.execute("release savepoint exact_dependency_acquire")
            for index, (shape_label, shape_value, shape_missing) in enumerate((
                ("missing receipt deviations", None, True),
                ("scalar receipt deviations", "invalid", False),
                ("object receipt deviations", {"invalid": True}, False),
                ("null receipt deviations", None, False),
            )):
                cur.execute("savepoint malformed_receipt_deviations")
                cur.execute("alter table ops.engineering_slice_receipt disable trigger user")
                if shape_missing:
                    cur.execute("update ops.engineering_slice_receipt "
                                "set receipt=receipt-'deviations' where id=%s", (_receipt,))
                else:
                    cur.execute("update ops.engineering_slice_receipt set "
                                "receipt=jsonb_set(receipt,'{deviations}',%s::jsonb,true) "
                                "where id=%s", (Jsonb(shape_value), _receipt))
                reseal_receipt(cur, _receipt)
                value = acquire(cur, bound, dependencies=deps) if index == 0 else one(
                    cur, "select ops.canonical_ownership_dependency_state(%s,%s,%s,%s)",
                    (bound[0], bound[5], dependency[5], "independently_verified"),
                )[0]
                refusal(value, "DEPENDENCY_UNSATISFIED", shape_label,
                        causal_object="dependency", expected="independently_verified",
                        actual={"slice_ref": dependency[5], "receipt_id": str(_receipt),
                                "outcome": "claimed_complete"})
                cur.execute("rollback to savepoint malformed_receipt_deviations")
                cur.execute("release savepoint malformed_receipt_deviations")
            for field in ("reviewed_deviation_refs", "resolved_deviation_refs"):
                for shape_label, shape_value, shape_missing in (
                    ("missing", None, True), ("scalar", "invalid", False),
                    ("object", {"invalid": True}, False), ("null", None, False),
                ):
                    cur.execute("savepoint malformed_review_deviations")
                    cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
                    if shape_missing:
                        cur.execute("update ops.engineering_reviewer_fact "
                                    f"set fact=fact-%s where id=%s", (field, reviewer_id))
                    else:
                        cur.execute("update ops.engineering_reviewer_fact set "
                                    "fact=jsonb_set(fact,%s,%s::jsonb,true) where id=%s",
                                    ([field], Jsonb(shape_value), reviewer_id))
                    refusal(one(
                        cur, "select ops.canonical_ownership_dependency_state(%s,%s,%s,%s)",
                        (bound[0], bound[5], dependency[5], "independently_verified"),
                    )[0], "DEPENDENCY_UNSATISFIED", f"{shape_label} {field}",
                            causal_object="dependency", expected="independently_verified",
                            actual={"slice_ref": dependency[5],
                                    "receipt_id": str(_receipt),
                                    "reviewer_fact_id": str(reviewer_id),
                                    "review_state": "passed"})
                    cur.execute("rollback to savepoint malformed_review_deviations")
                    cur.execute("release savepoint malformed_review_deviations")
            stale_lookup = list(bound)
            stale_lookup[1] += 1
            refusal(acquire(cur, tuple(stale_lookup), ttl=1), "INPUT_INVALID",
                    "top-level input precedes lookup", causal_object="lease.input",
                    expected="bounded exact A2 input", actual="invalid")
            precedence_duplicate = {
                "path": "ops/precedence.py", "mode": "file", "operation": "write"
            }
            alias_paths = [
                {"path": "Ops/A", "mode": "file", "operation": "write"},
                {"path": "ops/B", "mode": "file", "operation": "write"},
                precedence_duplicate, precedence_duplicate,
            ]
            invalid_resource = [{"resource": "résource:precedence"}]
            refusal(acquire(cur, bound, paths=[
                        {"path": "../escape", "mode": "file", "operation": "write"},
                        *alias_paths], resources=invalid_resource, dependencies=deps),
                    "PATH_INVALID", "path precedes case/resource/duplicate",
                    causal_object="path_claim", expected="exact A1a path claim",
                    actual={"ordinal": 1, "field": "path_claim", "reason": "invalid",
                            "value_redacted": True})
            refusal(acquire(cur, bound, paths=alias_paths, resources=invalid_resource,
                            dependencies=deps),
                    "PATH_CASE_ALIAS", "case precedes resource/duplicate",
                    causal_object="path_claims", expected="one canonical path case",
                    actual={"left_ordinal": 1, "right_ordinal": 2,
                            "reason": "case_alias", "value_redacted": True})
            refusal(acquire(cur, bound, paths=[precedence_duplicate,
                                              precedence_duplicate],
                            resources=invalid_resource, dependencies=deps),
                    "RESOURCE_INVALID", "resource precedes duplicate",
                    causal_object="resource_claim",
                    expected="exact ASCII resource identifier",
                    actual={"ordinal": 1, "field": "resource_claim", "reason": "invalid",
                            "value_redacted": True})
            refusal(one(
                cur,
                "select ops.canonical_ownership_dependency_state(%s,%s,%s,%s)",
                (bound[0], bound[5], "slice:a2:missing", "independently_verified"),
            )[0], "DEPENDENCY_MISSING", "missing canonical dependency evidence",
                causal_object="dependency",
                expected="exactly one unsuperseded envelope leaf",
                actual={"slice_ref": "slice:a2:missing", "leaf_count": 0})
            refusal(one(
                cur,
                "select ops.canonical_ownership_dependency_state(%s,%s,%s,%s)",
                (bound[0], bound[5], subject[5], "independently_verified"),
            )[0], "DEPENDENCY_UNSATISFIED", "unsatisfied canonical dependency evidence",
                causal_object="dependency", expected="independently_verified",
                actual={"slice_ref": subject[5], "receipt_id": None, "outcome": None})

            stale_work = list(bound)
            stale_work[1] += 1
            refusal(one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(stale_work),
            )[0], "WORK_REQUEST_BINDING_STALE", "stale work request",
                causal_object="work_request.binding",
                expected="submitted binding matches canonical source",
                actual={"reason": "binding_stale", "value_redacted": True})
            missing_plan = list(bound)
            missing_plan[5] = uuid.uuid4()
            refusal(one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(missing_plan),
            )[0], "SLICE_PLAN_NOT_FOUND", "missing slice plan",
                causal_object="slice_plan", expected="canonical slice plan",
                actual="absent")
            stale_plan = list(bound)
            stale_plan[6] = sha("f")
            refusal(one(
                cur,
                "select ops.canonical_ownership_currentness(%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(stale_plan),
            )[0], "SLICE_PLAN_BINDING_STALE", "stale slice plan",
                causal_object="slice_plan.binding",
                expected="submitted binding matches canonical slice plan",
                actual={"reason": "binding_stale", "value_redacted": True})

            refusal(acquire(cur, bound), "SLICE_PLAN_BINDING_STALE",
                    "omitted canonical dependency",
                    causal_object="slice_plan.dependencies",
                    expected="exact canonical dependency snapshot",
                    actual={"reason": "snapshot_mismatch", "submitted_count": 0,
                            "canonical_count": 1, "first_mismatch_ordinal": 1,
                            "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[*deps, {
                "slice_ref": "slice:a2:extra", "required_state": "independently_verified"
            }]), "SLICE_PLAN_BINDING_STALE", "extra canonical dependency",
                    causal_object="slice_plan.dependencies",
                    expected="exact canonical dependency snapshot",
                    actual={"reason": "snapshot_mismatch", "submitted_count": 2,
                            "canonical_count": 1, "first_mismatch_ordinal": 1,
                            "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[{
                "slice_ref": dependency[5], "required_state": "completed"
            }]), "SLICE_PLAN_BINDING_STALE", "downgraded canonical dependency",
                    causal_object="slice_plan.dependencies",
                    expected="exact canonical dependency snapshot",
                    actual={"reason": "snapshot_mismatch", "submitted_count": 1,
                            "canonical_count": 1, "first_mismatch_ordinal": 1,
                            "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[{
                "slice_ref": dependency[5]
            }]), "INPUT_INVALID", "malformed canonical dependency",
                    causal_object="dependency", expected="exact dependency object",
                    actual={"ordinal": 1, "field": "dependency", "reason": "invalid",
                            "value_redacted": True})
            refusal(acquire(cur, bound, dependencies=[deps[0], deps[0]]),
                    "INPUT_INVALID", "duplicate canonical dependency",
                    causal_object="dependencies", expected="unique slice_ref values",
                    actual={"duplicate_ordinal": 2})

            refusal(acquire(cur, bound, ttl=1), "INPUT_INVALID", "ttl precedence",
                    causal_object="lease.input", expected="bounded exact A2 input",
                    actual="invalid")
            refusal(
                acquire(cur, bound, paths=[{"path": "../escape", "mode": "file",
                                            "operation": "write"}]),
                "PATH_INVALID",
                "repo-relative path",
                causal_object="path_claim", expected="exact A1a path claim",
                actual={"ordinal": 1, "field": "path_claim", "reason": "invalid",
                        "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, paths=[
                    {"path": "Ops/A", "mode": "file", "operation": "write"},
                    {"path": "ops/B", "mode": "file", "operation": "write"},
                ]),
                "PATH_CASE_ALIAS",
                "case-fold alias",
                causal_object="path_claims", expected="one canonical path case",
                actual={"left_ordinal": 1, "right_ordinal": 2,
                        "reason": "case_alias", "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, resources=[{"resource": "résource:bad"}]),
                "RESOURCE_INVALID",
                "ASCII resource",
                causal_object="resource_claim",
                expected="exact ASCII resource identifier",
                actual={"ordinal": 1, "field": "resource_claim", "reason": "invalid",
                        "value_redacted": True},
            )
            duplicate = {"path": "ops/duplicate.py", "mode": "file", "operation": "write"}
            refusal(acquire(cur, bound, paths=[duplicate, duplicate]),
                    "DUPLICATE_CLAIM", "duplicate path", causal_object="claims",
                    expected="unique claims",
                    actual={"claim_kind": "path", "duplicate_ordinal": 2})
            lease = acquire(
                cur, bound, paths=path_claims, resources=resources, dependencies=deps
            )
            if lease.get("ok") is not True or set(lease) != {
                "ok", "lease_id", "lease_token", "fencing_generation", "expires_at"
            }:
                raise RuntimeError("acquire result shape drifted")
            lease_id = lease["lease_id"]
            lease_token = lease["lease_token"]
            generation = lease["fencing_generation"]
            wrong_token = register_submitted_wrong_token(uuid.uuid4())
            for label, value in (
                ("malformed path token", acquire(cur, bound, paths=[{
                    "path": f"ops/{lease_token}", "mode": "file", "operation": "write",
                    "submitted_token": str(lease_token)
                }], dependencies=deps)),
                ("malformed resource token", acquire(cur, bound, resources=[{
                    "resource": f"resource:{lease_token}",
                    "submitted_token": str(lease_token)
                }], dependencies=deps)),
                ("malformed dependency token", acquire(cur, bound, dependencies=[{
                    "slice_ref": dependency[5], "required_state": "independently_verified",
                    "submitted_token": str(lease_token)
                }])),
            ):
                refusal(value, "PATH_INVALID" if "path" in label else
                        "RESOURCE_INVALID" if "resource" in label else "INPUT_INVALID",
                        label,
                        causal_object="path_claim" if "path" in label else
                                      "resource_claim" if "resource" in label else
                                      "dependency",
                        expected="exact A1a path claim" if "path" in label else
                                 "exact ASCII resource identifier" if "resource" in label else
                                 "exact dependency object",
                        actual={"ordinal": 1,
                                "field": "path_claim" if "path" in label else
                                          "resource_claim" if "resource" in label else
                                          "dependency",
                                "reason": "invalid", "value_redacted": True})
                assert_secret_absent(value, str(lease_token), label)
            assertions += 14
        conn.commit()

        with conn.cursor() as cur:
            context(cur, tenant, "dell")
            collision_path = [{"path": "ops/ownership/child.py", "mode": "file",
                               "operation": "write"}]
            refusal(acquire(cur, tuple(stale_work), paths=collision_path,
                            dependencies=deps),
                    "WORK_REQUEST_BINDING_STALE", "work stale precedes collision",
                    causal_object="work_request.binding",
                    expected="submitted binding matches canonical source",
                    actual={"reason": "binding_stale", "value_redacted": True})
            refusal(acquire(cur, tuple(missing_plan), paths=collision_path,
                            dependencies=deps),
                    "SLICE_PLAN_NOT_FOUND", "slice missing precedes collision",
                    causal_object="slice_plan", expected="canonical slice plan",
                    actual="absent")
            refusal(acquire(cur, tuple(stale_plan), paths=collision_path,
                            dependencies=deps),
                    "SLICE_PLAN_BINDING_STALE", "slice stale precedes collision",
                    causal_object="slice_plan.binding",
                    expected="submitted binding matches canonical slice plan",
                    actual={"reason": "binding_stale", "value_redacted": True})

            cur.execute("savepoint dependency_missing_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            missing_dependency_ref = "slice:a2:missing-precedence"
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], missing_dependency_ref])
            refusal(acquire(cur, bound, paths=collision_path, dependencies=[
                        deps[0], {"slice_ref": missing_dependency_ref,
                                  "required_state": "independently_verified"}]),
                    "DEPENDENCY_MISSING", "dependency missing precedes collision",
                    causal_object="dependency",
                    expected="exactly one unsuperseded envelope leaf",
                    actual={"slice_ref": missing_dependency_ref, "leaf_count": 0})
            cur.execute("rollback to savepoint dependency_missing_precedence")
            cur.execute("release savepoint dependency_missing_precedence")

            cur.execute("savepoint dependency_unsatisfied_precedence")
            cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
            cur.execute("update ops.engineering_reviewer_fact set state='failed' "
                        "where receipt_id=%s", (_receipt,))
            refusal(acquire(cur, bound, paths=collision_path, dependencies=deps),
                    "DEPENDENCY_UNSATISFIED",
                    "dependency unsatisfied precedes collision",
                    causal_object="dependency", expected="independently_verified",
                    actual={"slice_ref": dependency[5], "receipt_id": str(_receipt),
                            "reviewer_fact_id": str(one(cur,
                                "select id from ops.engineering_reviewer_fact where receipt_id=%s",
                                (_receipt,))[0]),
                            "review_state": "failed"})
            cur.execute("rollback to savepoint dependency_unsatisfied_precedence")
            cur.execute("release savepoint dependency_unsatisfied_precedence")

            refusal(
                acquire(
                    cur,
                    bound,
                    paths=collision_path, dependencies=deps,
                ),
                "FOREIGN_LEASE_COLLISION",
                "tree ancestry collision",
                causal_object="lease.collision", expected="unclaimed scope",
                actual={"conflicting_lease_id": str(lease_id), "claim_kind": "path",
                        "submitted_ordinal": 1,
                        "claim_digest": hashlib.sha256(b"ops/ownership").hexdigest(),
                        "reason": "already_claimed", "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, resources=resources, dependencies=deps),
                "FOREIGN_LEASE_COLLISION",
                "resource collision",
                causal_object="lease.collision", expected="unclaimed scope",
                actual={"conflicting_lease_id": str(lease_id), "claim_kind": "resource",
                        "submitted_ordinal": 1,
                        "claim_digest": hashlib.sha256(b"migration:0450").hexdigest(),
                        "reason": "already_claimed", "value_redacted": True},
            )
            refusal(
                acquire(cur, bound, paths=[{
                    "path": "ops/ownership-renamed/child.py", "mode": "file",
                    "operation": "write"}], dependencies=deps),
                "FOREIGN_LEASE_COLLISION", "rename destination collision",
                causal_object="lease.collision", expected="unclaimed scope",
                actual={"conflicting_lease_id": str(lease_id), "claim_kind": "path",
                        "submitted_ordinal": 1,
                        "claim_digest": hashlib.sha256(
                            b"ops/ownership-renamed").hexdigest(),
                        "reason": "already_claimed", "value_redacted": True},
            )
            resource_prefix = acquire(
                cur, bound, resources=[{"resource": "migration:0450:child"}],
                dependencies=deps,
            )
            if resource_prefix.get("ok") is not True:
                raise RuntimeError("resource prefix was treated as a wildcard collision")
            holder = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (lease_id, lease_token, generation),
            )[0]
            refusal(holder, "LEASE_HOLDER_MISMATCH", "Joe and Dell identity",
                    causal_object="lease.holder",
                    expected="acquiring actor, session, and host",
                    actual={"actor_matches": False, "session_matches": False,
                            "host_matches": True})
            context(cur, tenant, "joe", "session:a2:primary:changed")
            refusal(
                one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation),
                )[0],
                "LEASE_HOLDER_MISMATCH",
                "changed holder session",
                causal_object="lease.holder",
                expected="acquiring actor, session, and host",
                actual={"actor_matches": True, "session_matches": False,
                        "host_matches": True},
            )
            context(cur, tenant, "joe", "session:a2:primary:joe",
                    "host:a2:changed")
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation))[0],
                "LEASE_HOLDER_MISMATCH", "changed execution host",
                causal_object="lease.holder",
                expected="acquiring actor, session, and host",
                actual={"actor_matches": True, "session_matches": True,
                        "host_matches": False},
            )
            assertions += 4

            context(cur, tenant, "joe", "session:a2:primary:joe")
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (uuid.uuid4(), wrong_token, generation))[0],
                "LEASE_NOT_FOUND", "missing lease", causal_object="lease",
                expected="tenant lease", actual="absent",
            )
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, register_submitted_wrong_token(uuid.uuid4()), generation))[0],
                "LEASE_TOKEN_STALE",
                "stale token",
                causal_object="lease.token", expected="current lease token",
                actual="redacted",
            )
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation + 1))[0],
                "FENCING_GENERATION_STALE",
                "stale fencing generation",
                causal_object="lease.fencing_generation", expected=generation,
                actual=generation + 1,
            )
            refusal(
                one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                    (
                        lease_id,
                        lease_token,
                        generation,
                        Jsonb([{"path": "ops/not-owned.py", "mode": "file",
                                "operation": "write"}]),
                        Jsonb([]),
                    ),
                )[0],
                "LEASE_CLAIMS_MISMATCH",
                "claim subset",
                causal_object="lease.claims", expected="claimed scope",
                actual={"claim_kind": "path", "submitted_ordinal": 1,
                        "reason": "unowned_claim", "value_redacted": True},
            )
            for label, paths, resource_claims in (
                ("valid path submitted token", [{
                    "path": f"ops/{wrong_token}", "mode": "file", "operation": "write"
                }], []),
                ("valid resource submitted token", [], [{
                    "resource": f"resource:{wrong_token}"
                }]),
            ):
                token_claim = one(
                    cur,
                    "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                    (lease_id, lease_token, generation, Jsonb(paths), Jsonb(resource_claims)),
                )[0]
                refusal(token_claim, "LEASE_CLAIMS_MISMATCH", label,
                        causal_object="lease.claims", expected="claimed scope",
                        actual={"claim_kind": "path" if paths else "resource",
                                "submitted_ordinal": 1, "reason": "unowned_claim",
                                "value_redacted": True})
                assert_secret_absent(token_claim, str(wrong_token), label)
            malformed_required = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                (lease_id, lease_token, generation, Jsonb([{
                    "path": f"ops/{lease_token}", "mode": "file", "operation": "write",
                    "submitted_token": str(lease_token)
                }]), Jsonb([])),
            )[0]
            refusal(malformed_required, "INPUT_INVALID", "required claim token",
                    causal_object="required_claims.path", expected="exact path claim",
                    actual={"ordinal": 1, "field": "path_claim", "reason": "invalid",
                            "value_redacted": True})
            assert_secret_absent(malformed_required, str(lease_token),
                                 "required claim token")
            for label, shape_value, shape_missing, sql, params in (
                ("check scalar plan", "invalid", False,
                 "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                 (lease_id, lease_token, generation)),
                ("renew object plan", {"invalid": True}, False,
                 "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                 (lease_id, lease_token, generation)),
                ("release null plan", None, False,
                 "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                 (lease_id, lease_token, generation)),
                ("check missing plan", None, True,
                 "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                 (lease_id, lease_token, generation)),
            ):
                cur.execute("savepoint malformed_plan_boundary")
                cur.execute("alter table ops.engineering_slice_plan disable trigger user")
                set_plan_slices(cur, bound[5], shape_value, missing=shape_missing)
                refusal(one(cur, sql, params)[0], "SLICE_PLAN_BINDING_STALE", label,
                        causal_object="slice_plan.dependencies",
                        expected="one typed canonical dependency set",
                        actual={"reason": "malformed_plan", "value_redacted": True})
                cur.execute("rollback to savepoint malformed_plan_boundary")
                cur.execute("release savepoint malformed_plan_boundary")
            for label, table, field, value, missing, sql in (
                ("check malformed receipt deviations", "receipt", "deviations",
                 "invalid", False,
                 "select ops.check_canonical_ownership_lease(%s,%s,%s)"),
                ("renew missing reviewed deviations", "review",
                 "reviewed_deviation_refs", None, True,
                 "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)"),
                ("release malformed resolved deviations", "review",
                 "resolved_deviation_refs", {"invalid": True}, False,
                 "select ops.release_canonical_ownership_lease(%s,%s,%s)"),
            ):
                cur.execute("savepoint malformed_deviation_boundary")
                if table == "receipt":
                    cur.execute("alter table ops.engineering_slice_receipt disable trigger user")
                    cur.execute("update ops.engineering_slice_receipt set "
                                "receipt=jsonb_set(receipt,%s,%s::jsonb,true) where id=%s",
                                ([field], Jsonb(value), _receipt))
                    reseal_receipt(cur, _receipt)
                    actual = {"slice_ref": dependency[5],
                              "receipt_id": str(_receipt),
                              "outcome": "claimed_complete"}
                else:
                    cur.execute("alter table ops.engineering_reviewer_fact disable trigger user")
                    if missing:
                        cur.execute("update ops.engineering_reviewer_fact "
                                    "set fact=fact-%s where id=%s", (field, reviewer_id))
                    else:
                        cur.execute("update ops.engineering_reviewer_fact set "
                                    "fact=jsonb_set(fact,%s,%s::jsonb,true) where id=%s",
                                    ([field], Jsonb(value), reviewer_id))
                    actual = {"slice_ref": dependency[5],
                              "receipt_id": str(_receipt),
                              "reviewer_fact_id": str(reviewer_id),
                              "review_state": "passed"}
                refusal(one(cur, sql, (lease_id, lease_token, generation))[0],
                        "DEPENDENCY_UNSATISFIED", label,
                        causal_object="dependency", expected="independently_verified",
                        actual=actual)
                cur.execute("rollback to savepoint malformed_deviation_boundary")
                cur.execute("release savepoint malformed_deviation_boundary")
            exact_boundary_calls: tuple[tuple[str, str, tuple[object, ...]], ...] = (
                (
                    "check",
                    "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation),
                ),
                (
                    "renew",
                    "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                    (lease_id, lease_token, generation),
                ),
                (
                    "release",
                    "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation),
                ),
            )
            for (
                identity_label,
                identity_path,
                identity_value,
                sync_receipt_session,
            ) in envelope_identity_corruptions:
                cur.execute("savepoint envelope_identity_boundary")
                corrupt_envelope_identity(
                    cur,
                    dependency[1],
                    _receipt,
                    identity_path,
                    identity_value,
                    sync_receipt_session=sync_receipt_session,
                )
                for operation, boundary_sql, boundary_args in exact_boundary_calls:
                    refusal(
                        one(cur, boundary_sql, boundary_args)[0],
                        "DEPENDENCY_UNSATISFIED",
                        f"{operation} {identity_label}",
                        causal_object="dependency",
                        expected="independently_verified",
                        actual={
                            "slice_ref": dependency[5],
                            "receipt_id": str(_receipt),
                            "outcome": "claimed_complete",
                        },
                    )
                cur.execute("rollback to savepoint envelope_identity_boundary")
                cur.execute("release savepoint envelope_identity_boundary")
            for label, table, corrupt_sql, corrupt_args, reseal in canonical_corruptions:
                cur.execute("savepoint exact_dependency_boundary")
                cur.execute(f"alter table ops.{table} disable trigger user")
                cur.execute(corrupt_sql, corrupt_args)
                if reseal:
                    reseal_receipt(cur, _receipt)
                actual = {
                    "slice_ref": dependency[5],
                    "receipt_id": str(_receipt),
                    "outcome": "claimed_complete",
                }
                if table == "engineering_reviewer_fact":
                    actual = {
                        "slice_ref": dependency[5],
                        "receipt_id": str(_receipt),
                        "reviewer_fact_id": str(reviewer_id),
                        "review_state": "passed",
                    }
                for operation, boundary_sql, boundary_args in exact_boundary_calls:
                    refusal(
                        one(cur, boundary_sql, boundary_args)[0],
                        "DEPENDENCY_UNSATISFIED",
                        f"{operation} {label}",
                        causal_object="dependency",
                        expected="independently_verified",
                        actual=actual,
                    )
                cur.execute("rollback to savepoint exact_dependency_boundary")
                cur.execute("release savepoint exact_dependency_boundary")
            cur.execute("savepoint post_acquire_dependency_drift")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            drift_ref = "slice:a2:post-acquire-drift"
            set_plan_dependencies(cur, bound[5], bound[7], [dependency[5], drift_ref])
            drift_cases: tuple[tuple[str, str, tuple[object, ...]], ...] = (
                ("check canonical drift precedes claims",
                 "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                 (lease_id, lease_token, generation,
                  Jsonb([{"path": "ops/unowned-after-drift.py", "mode": "file",
                          "operation": "write"}]), Jsonb([]))),
                ("renew canonical drift",
                 "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                 (lease_id, lease_token, generation)),
                ("release canonical drift",
                 "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                 (lease_id, lease_token, generation)),
            )
            for label, sql, drift_params in drift_cases:
                refusal(one(cur, sql, drift_params)[0],
                        "SLICE_PLAN_BINDING_STALE", label,
                        causal_object="slice_plan.dependencies",
                        expected="persisted dependencies match canonical plan",
                        actual={"reason": "canonical_drift", "value_redacted": True})
            cur.execute("rollback to savepoint post_acquire_dependency_drift")
            cur.execute("release savepoint post_acquire_dependency_drift")
            renewed = one(
                cur,
                "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                (lease_id, lease_token, generation),
            )[0]
            if renewed.get("ok") is not True:
                raise RuntimeError(f"renewal failed: {safe_outcome(renewed)}")
            record_response(renewed)
            released = one(
                cur,
                "select ops.release_canonical_ownership_lease(%s,%s,%s)",
                (lease_id, lease_token, generation),
            )[0]
            if released.get("state") != "released":
                raise RuntimeError(f"release failed: {safe_outcome(released)}")
            record_response(released)
            refusal(
                one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                    (lease_id, lease_token, generation))[0],
                "LEASE_RELEASED",
                "released lease",
                causal_object="lease.lifecycle", expected="active",
                actual="released",
            )
            context(cur, tenant, "dell", "session:a2:released:dell")
            refusal(one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                        (lease_id, wrong_token, generation + 1))[0],
                    "LEASE_HOLDER_MISMATCH", "holder precedes token/generation/lifecycle",
                    causal_object="lease.holder",
                    expected="acquiring actor, session, and host",
                    actual={"actor_matches": False, "session_matches": False,
                            "host_matches": True})
            context(cur, tenant, "joe", "session:a2:primary:joe")
            refusal(one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                        (lease_id, wrong_token, generation + 1))[0],
                    "LEASE_TOKEN_STALE", "token precedes generation/lifecycle",
                    causal_object="lease.token", expected="current lease token",
                    actual="redacted")
            refusal(one(cur, "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                        (lease_id, lease_token, generation + 1))[0],
                    "FENCING_GENERATION_STALE", "generation precedes lifecycle",
                    causal_object="lease.fencing_generation", expected=generation,
                    actual=generation + 1)
            cur.execute("savepoint released_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], "slice:a2:released-drift"])
            refusal(one(cur,
                        "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                        (lease_id, lease_token, generation,
                         Jsonb([{"path": "ops/released-unowned.py", "mode": "file",
                                 "operation": "write"}]), Jsonb([])))[0],
                    "LEASE_RELEASED", "released precedes downstream drift/claims",
                    causal_object="lease.lifecycle", expected="active",
                    actual="released")
            cur.execute("rollback to savepoint released_precedence")
            cur.execute("release savepoint released_precedence")
            assertions += 13
        conn.commit()

        # Expired scopes can be reacquired, but never revived. The new grant
        # receives a strictly larger fence and preserves expired/replaced facts.
        with conn.cursor() as cur:
            context(cur, tenant, "joe", "session:a2:expiry:joe")
            expiring = acquire(
                cur,
                bound,
                paths=[{"path": "ops/expired.py", "mode": "file", "operation": "write"}],
                dependencies=deps,
            )
            fixture_expiry = one(
                cur,
                """update ops.canonical_ownership_lease
                      set expires_at=acquired_at+interval '1 microsecond'
                    where id=%s returning acquired_at,expires_at""",
                (expiring["lease_id"],),
            )
            if fixture_expiry[1] <= fixture_expiry[0]:
                raise RuntimeError("expiry fixture violated acquisition ordering")
            cur.execute("savepoint wall_clock_expiry_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], "slice:a2:expired-drift"])
            refusal(one(cur,
                        "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                        (expiring["lease_id"], expiring["lease_token"],
                         expiring["fencing_generation"],
                         Jsonb([{"path": "ops/expired-unowned.py", "mode": "file",
                                 "operation": "write"}]), Jsonb([])))[0],
                    "LEASE_EXPIRED", "wall clock expiry precedes drift/claims",
                    causal_object="lease.expiry", expected="future expiry",
                    actual="elapsed")
            cur.execute("rollback to savepoint wall_clock_expiry_precedence")
            cur.execute("release savepoint wall_clock_expiry_precedence")
            cleanup = one(cur, "select ops.expire_canonical_ownership_leases()")[0]
            if cleanup.get("ok") is not True or cleanup.get("expired_count") != 1:
                raise RuntimeError("expiry cleanup did not transition exactly one lease")
            record_response(cleanup)
            cleanup_again = one(cur, "select ops.expire_canonical_ownership_leases()")[0]
            if cleanup_again.get("ok") is not True or cleanup_again.get("expired_count") != 0:
                raise RuntimeError("expiry cleanup was not idempotent")
            record_response(cleanup_again)
            expired_check = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (expiring["lease_id"], expiring["lease_token"],
                 expiring["fencing_generation"]),
            )[0]
            refusal(expired_check, "LEASE_EXPIRED", "cleanup expired check",
                    causal_object="lease.lifecycle",
                    expected="active and unexpired", actual="expired")
            expired_renew = one(
                cur,
                "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
                (expiring["lease_id"], expiring["lease_token"],
                 expiring["fencing_generation"]),
            )[0]
            refusal(expired_renew, "LEASE_EXPIRED", "cleanup expired renew",
                    causal_object="lease.lifecycle",
                    expected="active and unexpired", actual="expired")
            context(cur, tenant, "dell", "session:a2:expiry:dell")
            replacement = acquire(
                cur,
                bound,
                paths=[{"path": "ops/expired.py", "mode": "file", "operation": "write"}],
                dependencies=deps,
            )
            if replacement.get("ok") is not True or replacement["fencing_generation"] <= expiring["fencing_generation"]:
                raise RuntimeError("reacquisition did not mint a monotonic fence")
            state = one(
                cur,
                "select state,superseded_by_lease_id from ops.canonical_ownership_lease where id=%s",
                (expiring["lease_id"],),
            )
            if state[0] != "replaced" or str(state[1]) != str(replacement["lease_id"]):
                raise RuntimeError(f"expired predecessor was not replaced: {state}")
            events = one(
                cur,
                """select array_agg(event_kind order by id)
                     from ops.canonical_ownership_lease_event where lease_id=%s""",
                (expiring["lease_id"],),
            )[0]
            if events[-2:] != ["expired", "replaced"]:
                raise RuntimeError(f"expiry/replacement audit sequence drifted: {events}")
            context(cur, tenant, "joe", "session:a2:expiry:joe")
            replaced_check = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (expiring["lease_id"], expiring["lease_token"],
                 expiring["fencing_generation"]),
            )[0]
            refusal(replaced_check, "LEASE_REPLACED", "replaced predecessor",
                    causal_object="lease.lifecycle", expected="active",
                    actual="replaced")
            cur.execute("savepoint replaced_precedence")
            cur.execute("alter table ops.engineering_slice_plan disable trigger user")
            set_plan_dependencies(cur, bound[5], bound[7],
                                  [dependency[5], "slice:a2:replaced-drift"])
            refusal(one(cur,
                        "select ops.check_canonical_ownership_lease(%s,%s,%s,%s,%s)",
                        (expiring["lease_id"], expiring["lease_token"],
                         expiring["fencing_generation"],
                         Jsonb([{"path": "ops/replaced-unowned.py", "mode": "file",
                                 "operation": "write"}]), Jsonb([])))[0],
                    "LEASE_REPLACED", "replaced precedes downstream drift/claims",
                    causal_object="lease.lifecycle", expected="active",
                    actual="replaced")
            cur.execute("rollback to savepoint replaced_precedence")
            cur.execute("release savepoint replaced_precedence")
            retained = one(
                cur,
                """select count(*),
                          (select count(*) from ops.canonical_ownership_claim where lease_id=%s),
                          (select count(*) from ops.canonical_ownership_dependency where lease_id=%s),
                          min(fencing_generation),max(fencing_generation)
                     from ops.canonical_ownership_lease_event where lease_id=%s""",
                (expiring["lease_id"], expiring["lease_id"], expiring["lease_id"]),
            )
            if retained[0] != 3 or retained[1] < 1 or retained[2] != len(deps) or retained[3] != retained[4] or retained[3] != expiring["fencing_generation"]:
                raise RuntimeError("cleanup/replacement did not retain exact lineage and fence")
            assertions += 8
        conn.commit()

        # A separate tenant can hold byte-identical claims; it cannot observe
        # or collide with the first tenant's rows.
        other_tenant = f"a2-other-{uuid.uuid4().hex}"
        other_dep, other_subject, *_ = seed_lineage(conn, other_tenant, "other")
        assert other_subject is not None
        with conn.cursor() as cur:
            context(cur, other_tenant, "joe")
            other = acquire(
                cur,
                binding(cur, other_subject[1]),
                paths=path_claims,
                resources=resources,
                dependencies=[{"slice_ref": other_dep[5],
                               "required_state": "independently_verified"}],
            )
            if other.get("ok") is not True:
                raise RuntimeError(f"tenant isolation failed: {safe_outcome(other)}")
            if one(
                cur,
                """select count(*) from ops.canonical_ownership_lease
                    where id=%s and organization_tenant_id=current_setting(
                      'carr.organization_tenant_id')""",
                (replacement["lease_id"],),
            )[0] != 0:
                raise RuntimeError("cross-tenant lease became visible")
            assertions += 2
        conn.commit()

    # Atomic same-tenant acquisition: exactly one winner, one typed collision.
    concurrency_tenant = f"a2-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        race_dep, race_subject, *_ = seed_lineage(setup, concurrency_tenant, "atomic")
        assert race_subject is not None
        race_bound = binding(setup.cursor(), race_subject[1])
    race_claim = [
        {"path": "ops/atomic-owner-a.py", "mode": "file", "operation": "write"},
        {"path": "ops/atomic-owner-b.py", "mode": "file", "operation": "rename_destination"},
    ]
    race_resources = [{"resource": "ownership:atomic"}]

    def acquire_peer(actor):
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, concurrency_tenant, actor)
            value = acquire(
                cur,
                race_bound,
                paths=race_claim,
                resources=race_resources,
                dependencies=[{"slice_ref": race_dep[5],
                               "required_state": "independently_verified"}],
            )
            peer.commit()
            return value

    atomic = race(lambda: acquire_peer("joe"), lambda: acquire_peer("dell"),
                  "atomic acquisition")
    codes = sorted(
        "OK" if value.get("ok") else value["refusal"]["code"]
        for value in atomic.values()
    )
    if codes != ["FOREIGN_LEASE_COLLISION", "OK"]:
        raise RuntimeError(
            "atomic acquisition had wrong outcomes: "
            + ",".join(sorted(safe_outcome(value) for value in atomic.values()))
        )
    winner = next(value for value in atomic.values() if value.get("ok") is True)
    loser = next(value for value in atomic.values() if value.get("ok") is False)
    refusal(
        loser, "FOREIGN_LEASE_COLLISION", "atomic multi-claim loser",
        causal_object="lease.collision", expected="unclaimed scope",
        actual={"conflicting_lease_id": str(winner["lease_id"]),
                "claim_kind": "path", "submitted_ordinal": 1,
                "claim_digest": hashlib.sha256(b"ops/atomic-owner-a.py").hexdigest(),
                "reason": "already_claimed", "value_redacted": True},
    )
    with psycopg.connect(dsn) as proof, proof.cursor() as cur:
        atomic_counts = one(cur,
            """select count(*),
                      (select count(*) from ops.canonical_ownership_claim c
                        join ops.canonical_ownership_lease l on l.id=c.lease_id
                       where l.organization_tenant_id=%s),
                      (select count(*) from ops.canonical_ownership_dependency d
                        join ops.canonical_ownership_lease l on l.id=d.lease_id
                       where l.organization_tenant_id=%s)
                 from ops.canonical_ownership_lease
                where organization_tenant_id=%s""",
            (concurrency_tenant, concurrency_tenant, concurrency_tenant))
        if atomic_counts != (1, 3, 1):
            raise RuntimeError("atomic multi-claim loser left partial rows")
    assertions += 1

    # Real writers and A2 take the same session -> actor -> lineage ordering.
    receipt_tenant = f"a2-receipt-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        receipt_fixture = seed_single(setup, receipt_tenant, "receipt-race")
        receipt_claim = cc.claim_one(
            setup, receipt_fixture[0], "ownership-receipt-race"
        )
        receipt_bound = binding(setup.cursor(), receipt_fixture[1])

    def append_receipt():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            cc.set_jobs(cur)
            result = cc.receipt(cur, receipt_fixture, receipt_claim, "claimed_complete")
            cc.reset_role(cur)
            peer.commit()
            return result

    def acquire_during_receipt():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, receipt_tenant)
            value = acquire(
                cur,
                receipt_bound,
                paths=[{"path": "ops/receipt-race.py", "mode": "file",
                        "operation": "write"}],
                dependencies=[],
            )
            peer.commit()
            return value

    receipt_race = race(append_receipt, acquire_during_receipt, "receipt append")
    lease_outcome = receipt_race["lease"]
    if lease_outcome.get("ok") is not True:
        raise RuntimeError(
            "receipt race crossed a noncausal boundary: "
            + safe_outcome(lease_outcome)
        )
    assertions += 1

    review_tenant = f"a2-review-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        review_fixture = seed_single(setup, review_tenant, "review-race")
        review_claim = cc.claim_one(
            setup, review_fixture[0], "ownership-review-race"
        )
        with setup.cursor() as cur:
            cc.set_jobs(cur)
            review_receipt = cc.receipt(
                cur, review_fixture, review_claim, "claimed_complete"
            )
            cc.reset_role(cur)
        setup.commit()
        review_bound = binding(setup.cursor(), review_fixture[1])

    def append_review():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            insert_review(cur, review_fixture, review_receipt)
            peer.commit()
            return "reviewed"

    def acquire_during_review():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, review_tenant)
            value = acquire(
                cur,
                review_bound,
                paths=[{"path": "ops/review-race.py", "mode": "file",
                        "operation": "write"}],
                dependencies=[],
            )
            peer.commit()
            return value

    review_race = race(append_review, acquire_during_review, "review append")
    lease_outcome = review_race["lease"]
    if lease_outcome.get("ok") is not True:
        raise RuntimeError(
            "review race crossed a noncausal boundary: "
            + safe_outcome(lease_outcome)
        )
    assertions += 1

    successor_tenant = f"a2-successor-race-{uuid.uuid4().hex}"
    with psycopg.connect(dsn) as setup:
        successor_fixture = seed_single(setup, successor_tenant, "successor-race")
        successor_claim = cc.claim_one(
            setup,
            successor_fixture[0],
            "ownership-successor-race",
        )
        with setup.cursor() as cur:
            cc.set_jobs(cur)
            successor_receipt = cc.receipt(
                cur, successor_fixture, successor_claim, "claimed_complete"
            )
            cc.reset_role(cur)
        setup.commit()
        with setup.cursor() as cur:
            insert_review(cur, successor_fixture, successor_receipt)
        setup.commit()
        successor_bound = binding(setup.cursor(), successor_fixture[1])
        with setup.cursor() as cur:
            context(
                cur,
                successor_tenant,
                "joe",
                "session:a2:successor-race:joe",
            )
            successor_lease = acquire(
                cur,
                successor_bound,
                paths=[{"path": "ops/successor-race.py", "mode": "file",
                        "operation": "write"}],
                dependencies=[],
            )
        setup.commit()

    def append_successor():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            result = cc.insert_successor(cur, successor_fixture, cancel_prior=True)
            peer.commit()
            return result[1]

    def check_during_successor():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(
                cur,
                successor_tenant,
                "joe",
                "session:a2:successor-race:joe",
            )
            value = one(
                cur,
                "select ops.check_canonical_ownership_lease(%s,%s,%s)",
                (
                    successor_lease["lease_id"],
                    successor_lease["lease_token"],
                    successor_lease["fencing_generation"],
                ),
            )[0]
            peer.commit()
            return value

    successor_race = race(
        append_successor, check_during_successor, "successor insertion"
    )
    lease_outcome = successor_race["lease"]
    if not lease_outcome.get("ok") and lease_outcome["refusal"]["code"] != "SLICE_PLAN_BINDING_STALE":
        raise RuntimeError(
            "successor race crossed a noncausal boundary: "
            + safe_outcome(lease_outcome)
        )
    record_response(lease_outcome)
    assertions += 1

    lifecycle_session = "session:a2:lifecycle:joe"

    def lifecycle_lease(label: str):
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            value = acquire(
                cur, bound,
                paths=[{"path": f"ops/lifecycle-{label}.py", "mode": "file",
                        "operation": "write"}],
                dependencies=deps,
            )
            peer.commit()
            if value.get("ok") is not True:
                raise RuntimeError(f"{label}: lifecycle fixture acquisition failed")
            return value

    def lifecycle_call(lease_value, operation: str):
        sql = {
            "renew": "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
            "release": "select ops.release_canonical_ownership_lease(%s,%s,%s)",
        }[operation]
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            value = one(cur, sql, (
                lease_value["lease_id"], lease_value["lease_token"],
                lease_value["fencing_generation"],
            ))[0]
            peer.commit()
            record_response(value)
            return value

    # Transaction start is deliberately earlier than the tenant lock release.
    # Every persisted acquisition timestamp must still be the one sample taken
    # after that lock, never transaction-start now().
    timestamp_ready = Event()
    timestamp_results: list[tuple[dict[str, object], datetime] | BaseException] = []
    with psycopg.connect(dsn) as blocker, blocker.cursor() as blocker_cur:
        context(blocker_cur, tenant, "joe", lifecycle_session)
        blocker_cur.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"canonical-ownership:{tenant}",),
        )

        def acquire_after_lock_wait() -> None:
            try:
                with psycopg.connect(dsn) as peer, peer.cursor() as cur:
                    context(
                        cur,
                        tenant,
                        "dell",
                        "session:a2:post-lock-time:dell",
                    )
                    transaction_started = one(
                        cur, "select transaction_timestamp()"
                    )[0]
                    timestamp_ready.set()
                    value = acquire(
                        cur,
                        bound,
                        paths=[{
                            "path": "ops/post-lock-time.py",
                            "mode": "file",
                            "operation": "write",
                        }],
                        dependencies=deps,
                    )
                    peer.commit()
                    timestamp_results.append((value, transaction_started))
            except BaseException as exc:
                timestamp_results.append(exc)

        timestamp_thread = Thread(target=acquire_after_lock_wait, daemon=True)
        timestamp_thread.start()
        if not timestamp_ready.wait(10):
            raise RuntimeError("post-lock timestamp peer did not reach acquisition")
        blocker_cur.execute("select pg_sleep(0.25)")
        blocker.commit()
        timestamp_thread.join(15)
    if timestamp_thread.is_alive() or len(timestamp_results) != 1:
        raise RuntimeError("post-lock timestamp peer did not finish")
    timestamp_result = timestamp_results[0]
    if isinstance(timestamp_result, BaseException):
        raise RuntimeError(
            "post-lock timestamp peer failed: "
            + type(timestamp_result).__name__
        )
    waited_lease, transaction_started = timestamp_result
    if not isinstance(waited_lease, dict) or waited_lease.get("ok") is not True:
        raise RuntimeError("post-lock timestamp acquisition was refused")
    with psycopg.connect(dsn) as proof, proof.cursor() as cur:
        times = one(
            cur,
            """select l.acquired_at,l.created_at,l.updated_at,
                      e.occurred_at,e.created_at,
                      (select min(c.created_at)
                         from ops.canonical_ownership_claim c
                        where c.lease_id=l.id),
                      (select max(c.created_at)
                         from ops.canonical_ownership_claim c
                        where c.lease_id=l.id),
                      (select min(d.evaluated_at)
                         from ops.canonical_ownership_dependency d
                        where d.lease_id=l.id),
                      (select max(d.created_at)
                         from ops.canonical_ownership_dependency d
                        where d.lease_id=l.id)
                 from ops.canonical_ownership_lease l
                 join ops.canonical_ownership_lease_event e on e.lease_id=l.id
                where l.id=%s and e.event_kind='acquired'""",
            (waited_lease["lease_id"],),
        )
    if len(set(times)) != 1 or times[0] <= transaction_started:
        raise RuntimeError("acquisition did not persist one post-lock timestamp")
    assertions += 1

    def holder_deactivation_race(label: str, operation) -> None:
        ready = Event()
        outcomes: list[object] = []
        with psycopg.connect(dsn) as deactivator, deactivator.cursor() as actor_cur:
            actor_cur.execute(
                "update public.actor set active=false where slug='joe'"
            )

            def invoke_with_stale_context() -> None:
                try:
                    with psycopg.connect(dsn) as peer, peer.cursor() as cur:
                        context(cur, tenant, "joe", lifecycle_session)
                        sampled = one(
                            cur, "select ops.canonical_ownership_context()"
                        )[0]
                        if sampled.get("ok") is not True:
                            raise RuntimeError(
                                f"{label}: initial actor context was not active"
                            )
                        ready.set()
                        value = operation(cur)
                        peer.commit()
                        outcomes.append(value)
                except BaseException as exc:
                    outcomes.append(exc)

            thread = Thread(target=invoke_with_stale_context, daemon=True)
            thread.start()
            if not ready.wait(10):
                raise RuntimeError(f"{label}: stale context was not sampled")
            deactivator.commit()
            thread.join(15)
        with psycopg.connect(dsn) as restore, restore.cursor() as restore_cur:
            restore_cur.execute(
                "update public.actor set active=true where slug='joe'"
            )
            restore.commit()
        if thread.is_alive() or len(outcomes) != 1:
            raise RuntimeError(f"{label}: actor race did not finish")
        if isinstance(outcomes[0], BaseException):
            raise RuntimeError(
                f"{label}: actor race failed: {type(outcomes[0]).__name__}"
            )
        refusal(
            outcomes[0],
            "IDENTITY_CONTEXT_INVALID",
            label,
            causal_object="identity_context.actor",
            expected="active canonical actor",
            actual={"reason": "unknown_or_inactive", "value_redacted": True},
        )

    holder_deactivation_race(
        "acquire holder deactivated after context",
        lambda cur: acquire(
            cur,
            bound,
            paths=[{
                "path": "ops/actor-race-acquire.py",
                "mode": "file",
                "operation": "write",
            }],
            dependencies=deps,
        ),
    )
    for operation in ("check", "renew", "release"):
        actor_lease = lifecycle_lease(f"actor-race-{operation}")
        actor_sql = {
            "check": "select ops.check_canonical_ownership_lease(%s,%s,%s)",
            "renew": "select ops.renew_canonical_ownership_lease(%s,%s,%s,600)",
            "release": "select ops.release_canonical_ownership_lease(%s,%s,%s)",
        }[operation]
        holder_deactivation_race(
            f"{operation} holder deactivated after context",
            lambda cur, sql=actor_sql, lease=actor_lease: one(
                cur,
                sql,
                (
                    lease["lease_id"],
                    lease["lease_token"],
                    lease["fencing_generation"],
                ),
            )[0],
        )
    assertions += 4

    renew_release_lease = lifecycle_lease("renew-release")
    renew_release = race(
        lambda: lifecycle_call(renew_release_lease, "renew"),
        lambda: lifecycle_call(renew_release_lease, "release"),
        "renew versus release",
    )
    if renew_release["lease"].get("state") != "released":
        raise RuntimeError("renew versus release did not release exactly once")
    if renew_release["writer"].get("ok") is not True:
        refusal(renew_release["writer"], "LEASE_RELEASED",
                "release won before renew", causal_object="lease.lifecycle",
                expected="active", actual="released")
    with psycopg.connect(dsn) as proof, proof.cursor() as cur:
        event_counts = one(cur,
            """select count(*) filter(where event_kind='released'),
                      count(*) filter(where event_kind='renewed'),
                      min(fencing_generation),max(fencing_generation)
                 from ops.canonical_ownership_lease_event where lease_id=%s""",
            (renew_release_lease["lease_id"],))
        if event_counts[0] != 1 or event_counts[1] not in (0, 1) \
           or event_counts[2] != event_counts[3] \
           or event_counts[2] != renew_release_lease["fencing_generation"]:
            raise RuntimeError("renew versus release event/fence proof drifted")
    assertions += 1

    cleanup_lease = lifecycle_lease("renew-cleanup")
    with psycopg.connect(dsn) as fixture, fixture.cursor() as cur:
        context(cur, tenant, "joe", lifecycle_session)
        fixture_expiry = one(
            cur,
            "update ops.canonical_ownership_lease set "
            "expires_at=acquired_at+interval '1 microsecond' "
            "where id=%s returning acquired_at,expires_at",
            (cleanup_lease["lease_id"],),
        )
        if fixture_expiry[1] <= fixture_expiry[0]:
            raise RuntimeError("cleanup expiry fixture violated acquisition ordering")
        fixture.commit()

    def cleanup_call():
        with psycopg.connect(dsn) as peer, peer.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            value = one(cur, "select ops.expire_canonical_ownership_leases()")[0]
            peer.commit()
            record_response(value)
            return value

    renew_cleanup = race(cleanup_call,
                         lambda: lifecycle_call(cleanup_lease, "renew"),
                         "renew versus cleanup")
    if renew_cleanup["writer"].get("expired_count") != 1:
        raise RuntimeError("cleanup did not win exactly one expiry transition")
    cleanup_renew = renew_cleanup["lease"]
    if cleanup_renew.get("refusal", {}).get("causal_object") == "lease.expiry":
        refusal(cleanup_renew, "LEASE_EXPIRED", "wall expiry won before cleanup",
                causal_object="lease.expiry", expected="future expiry", actual="elapsed")
    else:
        refusal(cleanup_renew, "LEASE_EXPIRED", "cleanup won before renew",
                causal_object="lease.lifecycle", expected="active and unexpired",
                actual="expired")
    with psycopg.connect(dsn) as proof, proof.cursor() as cur:
        state_events = one(cur,
            """select l.state,count(*) filter(where e.event_kind='expired'),
                      min(e.fencing_generation),max(e.fencing_generation)
                 from ops.canonical_ownership_lease l
                 join ops.canonical_ownership_lease_event e on e.lease_id=l.id
                where l.id=%s group by l.state""",
            (cleanup_lease["lease_id"],))
        if state_events != ("expired", 1, cleanup_lease["fencing_generation"],
                            cleanup_lease["fencing_generation"]):
            raise RuntimeError("renew versus cleanup lifecycle proof drifted")
    assertions += 1

    def replacement_race(label: str, competitor: str) -> None:
        predecessor = lifecycle_lease(label)
        with psycopg.connect(dsn) as fixture, fixture.cursor() as cur:
            context(cur, tenant, "joe", lifecycle_session)
            fixture_expiry = one(
                cur,
                "update ops.canonical_ownership_lease set "
                "expires_at=acquired_at+interval '1 microsecond' "
                "where id=%s returning acquired_at,expires_at",
                (predecessor["lease_id"],),
            )
            if fixture_expiry[1] <= fixture_expiry[0]:
                raise RuntimeError(
                    "replacement expiry fixture violated acquisition ordering"
                )
            fixture.commit()

        def replace_call():
            with psycopg.connect(dsn) as peer, peer.cursor() as cur:
                context(cur, tenant, "dell", f"session:a2:{label}:dell")
                value = acquire(
                    cur, bound,
                    paths=[{"path": f"ops/lifecycle-{label}.py", "mode": "file",
                            "operation": "write"}], dependencies=deps,
                )
                peer.commit()
                return value

        competing_call = cleanup_call if competitor == "cleanup" else \
            lambda: lifecycle_call(predecessor, competitor)
        outcomes = race(replace_call, competing_call,
                        f"replacement versus {competitor}")
        successor = outcomes["writer"]
        if successor.get("ok") is not True:
            raise RuntimeError(f"replacement versus {competitor} did not replace")
        competing = outcomes["lease"]
        if competitor == "cleanup":
            if competing.get("ok") is not True or competing.get("expired_count") not in (0, 1):
                raise RuntimeError("replacement versus cleanup returned a noncausal result")
        elif competing.get("ok") is True:
            raise RuntimeError(f"expired predecessor was revived by {competitor}")
        else:
            cause = competing.get("refusal", {}).get("causal_object")
            if cause == "lease.expiry":
                refusal(competing, "LEASE_EXPIRED", f"{competitor} observed wall expiry",
                        causal_object="lease.expiry", expected="future expiry",
                        actual="elapsed")
            elif cause == "lease.lifecycle" and competing["refusal"]["actual"] == "expired":
                refusal(competing, "LEASE_EXPIRED", f"{competitor} observed cleanup",
                        causal_object="lease.lifecycle", expected="active and unexpired",
                        actual="expired")
            else:
                refusal(competing, "LEASE_REPLACED", f"{competitor} observed replacement",
                        causal_object="lease.lifecycle", expected="active",
                        actual="replaced")
        with psycopg.connect(dsn) as proof, proof.cursor() as cur:
            row = one(cur,
                """select l.state,l.superseded_by_lease_id,l.fencing_generation,
                          count(*) filter(where e.event_kind='expired'),
                          count(*) filter(where e.event_kind='replaced'),
                          min(e.fencing_generation),max(e.fencing_generation)
                     from ops.canonical_ownership_lease l
                     join ops.canonical_ownership_lease_event e on e.lease_id=l.id
                    where l.id=%s
                    group by l.state,l.superseded_by_lease_id,l.fencing_generation""",
                (predecessor["lease_id"],))
            successor_events = one(cur,
                "select fencing_generation,count(*) from ops.canonical_ownership_lease_event where lease_id=%s and event_kind='acquired' group by fencing_generation",
                (successor["lease_id"],))
            if row[0] != "replaced" or str(row[1]) != str(successor["lease_id"]) \
               or row[2] != predecessor["fencing_generation"] \
               or row[3:] != (1, 1, predecessor["fencing_generation"],
                               predecessor["fencing_generation"]) \
               or successor_events != (successor["fencing_generation"], 1) \
               or successor["fencing_generation"] <= predecessor["fencing_generation"]:
                raise RuntimeError(f"replacement versus {competitor} fence/events drifted")

    for competitor in ("renew", "release", "cleanup"):
        replacement_race(f"replace-{competitor}", competitor)
        assertions += 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        private_functions = [
            "ops.canonical_ownership_refusal(text,text,jsonb,jsonb)",
            "ops.canonical_ownership_path_valid(text)",
            "ops.canonical_ownership_resource_valid(text)",
            "ops.canonical_ownership_path_case_alias(text,text)",
            "ops.canonical_ownership_paths_overlap(text,text,text,text)",
            "ops.canonical_ownership_context()",
            "ops.canonical_ownership_lock_lineage(uuid,uuid,text[])",
            "ops.canonical_ownership_currentness(uuid,integer,text,uuid,text,uuid,text,text)",
            "ops.canonical_ownership_plan_dependencies(uuid,text)",
            "ops.canonical_ownership_dependency_state(uuid,uuid,text,text)",
            "ops.acquire_canonical_ownership_lease(uuid,integer,text,uuid,text,uuid,text,text,text,jsonb,jsonb,jsonb,integer)",
            "ops.canonical_ownership_validate_live(uuid,uuid,bigint,boolean)",
            "ops.check_canonical_ownership_lease(uuid,uuid,bigint,jsonb,jsonb)",
            "ops.renew_canonical_ownership_lease(uuid,uuid,bigint,integer)",
            "ops.release_canonical_ownership_lease(uuid,uuid,bigint)",
            "ops.expire_canonical_ownership_leases()",
            "ops.canonical_ownership_append_only()",
        ]
        for role in ("public", "carr_reader", "carr_writer", "carr_jobs", "carr_authority"):
            for function in private_functions:
                if one(
                    cur,
                    "select has_function_privilege(%s,%s::regprocedure,'EXECUTE')",
                    (role, function),
                )[0]:
                    raise RuntimeError(f"dark kernel grant leaked: {role} can execute {function}")
            for table in (
                "ops.canonical_ownership_lease",
                "ops.canonical_ownership_claim",
                "ops.canonical_ownership_dependency",
                "ops.canonical_ownership_lease_event",
            ):
                for privilege in (
                    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                    "REFERENCES", "TRIGGER",
                ):
                    if one(cur, "select has_table_privilege(%s,%s,%s)",
                           (role, table, privilege))[0]:
                        raise RuntimeError(
                            f"dark kernel table privilege leaked: {role}/{privilege}"
                        )
                if one(
                    cur,
                    """select exists(
                         select 1 from information_schema.columns
                          where table_schema=split_part(%s,'.',1)
                            and table_name=split_part(%s,'.',2)
                            and (has_column_privilege(%s,%s,column_name,'SELECT')
                              or has_column_privilege(%s,%s,column_name,'INSERT')
                              or has_column_privilege(%s,%s,column_name,'UPDATE')
                              or has_column_privilege(%s,%s,column_name,'REFERENCES')))""",
                    (table, table, role, table, role, table, role, table, role, table),
                )[0]:
                    raise RuntimeError(f"dark kernel column privilege leaked: {role}")
            for privilege in ("USAGE", "SELECT", "UPDATE"):
                if one(
                    cur,
                    "select has_sequence_privilege(%s,%s,%s)",
                    (role, "ops.canonical_ownership_fencing_generation", privilege),
                )[0]:
                    raise RuntimeError(
                        f"dark kernel sequence privilege leaked: {role}/{privilege}"
                    )

        tokens = one(
            cur,
            """select coalesce(array_agg(lease_token::text),'{}'::text[])
                 from ops.canonical_ownership_lease""",
        )[0]
        evidence = one(
            cur,
            """select coalesce(string_agg(event_kind||cause::text||session_ref||
                                         host_ref,E'\n'),'')
                 from ops.canonical_ownership_lease_event""",
        )[0]
        if set(tokens) != set(MINTED_TOKENS):
            raise RuntimeError("minted token registry diverged from private lease rows")
        if any(token in evidence for token in tokens):
            raise RuntimeError("raw lease token leaked into lifecycle evidence")
        if one(
            cur,
            """select count(*) from information_schema.columns
                where table_schema='ops'
                  and table_name in ('canonical_ownership_claim',
                                     'canonical_ownership_dependency',
                                     'canonical_ownership_lease_event')
                  and column_name='lease_token'""",
        )[0] != 0:
            raise RuntimeError("raw token escaped the private lease row")

        for table in ("canonical_ownership_claim", "canonical_ownership_dependency",
                      "canonical_ownership_lease_event"):
            cur.execute("savepoint append_only")
            try:
                cur.execute(f"delete from ops.{table}")
            except psycopg.Error as exc:
                cur.execute("rollback to savepoint append_only")
                cur.execute("release savepoint append_only")
                if "append-only" not in str(exc):
                    raise
            else:
                raise RuntimeError(f"{table} accepted destructive cleanup")
        assertions += 3

        timestamp_drift = one(
            cur,
            """select count(*) from ops.canonical_ownership_lease l
                where l.created_at is distinct from l.acquired_at
                   or l.updated_at is distinct from case l.state
                        when 'released' then l.released_at
                        when 'replaced' then l.replaced_at
                        when 'expired' then (
                          select max(e.occurred_at)
                            from ops.canonical_ownership_lease_event e
                           where e.lease_id=l.id and e.event_kind='expired')
                        else coalesce(l.renewed_at,l.acquired_at)
                      end
                   or (select count(*) from ops.canonical_ownership_lease_event e
                        where e.lease_id=l.id and e.event_kind='acquired')<>1
                   or not exists (
                        select 1 from ops.canonical_ownership_lease_event e
                         where e.lease_id=l.id and e.event_kind='acquired'
                           and e.occurred_at=l.acquired_at
                           and e.created_at=e.occurred_at)
                   or ((l.renewed_at is null) is distinct from
                       ((select count(*) from ops.canonical_ownership_lease_event e
                          where e.lease_id=l.id and e.event_kind='renewed')=0))
                   or (l.renewed_at is not null and l.renewed_at is distinct from
                       (select max(e.occurred_at)
                          from ops.canonical_ownership_lease_event e
                         where e.lease_id=l.id and e.event_kind='renewed'))
                   or (l.state='released' and (
                        l.released_at is null
                        or (select count(*) from ops.canonical_ownership_lease_event e
                             where e.lease_id=l.id and e.event_kind='released')<>1
                        or not exists (
                             select 1 from ops.canonical_ownership_lease_event e
                              where e.lease_id=l.id and e.event_kind='released'
                                and e.occurred_at=l.released_at)))
                   or (l.state<>'released' and (
                        l.released_at is not null
                        or exists (
                             select 1 from ops.canonical_ownership_lease_event e
                              where e.lease_id=l.id and e.event_kind='released')))
                   or (l.state='replaced' and (
                        l.replaced_at is null
                        or (select count(*) from ops.canonical_ownership_lease_event e
                             where e.lease_id=l.id and e.event_kind='replaced')<>1
                        or not exists (
                             select 1 from ops.canonical_ownership_lease_event e
                              where e.lease_id=l.id and e.event_kind='replaced'
                                and e.occurred_at=l.replaced_at)))
                   or (l.state<>'replaced' and (
                        l.replaced_at is not null
                        or exists (
                             select 1 from ops.canonical_ownership_lease_event e
                              where e.lease_id=l.id and e.event_kind='replaced')))
                   or (l.state='expired' and (
                        (select count(*) from ops.canonical_ownership_lease_event e
                          where e.lease_id=l.id and e.event_kind='expired')<>1
                        or not exists (
                             select 1 from ops.canonical_ownership_lease_event e
                              where e.lease_id=l.id and e.event_kind='expired'
                                and e.occurred_at=l.updated_at)))
                   or exists (
                        select 1 from ops.canonical_ownership_lease_event e
                         where e.lease_id=l.id
                           and (e.created_at is distinct from e.occurred_at
                                or e.occurred_at<l.acquired_at
                                or e.occurred_at>l.updated_at))""",
        )[0]
        if timestamp_drift != 0:
            raise RuntimeError(
                "lease and lifecycle audit timestamps lost their exact linkage"
            )
        assertions += 1

        baseline_text = os.environ.get("CARR_OWNERSHIP_PRE_0450_FINGERPRINT", "")
        if not baseline_text:
            raise RuntimeError("true pre-0450 catalog fingerprint is required")
        baseline = json.loads(baseline_text)
        current = normalize_siep18_reference_monitor_guards(
            catalog_fingerprint(cur), validated_siep18_fingerprint_guards(cur)
        )
        if current != baseline:
            raise RuntimeError("existing catalog/grant fingerprint drifted across 0450")
        assertions += 1

    response_evidence = json.dumps(RESPONSES, default=str, sort_keys=True)
    for token, response_index in MINTED_TOKENS.items():
        own_response = json.dumps(RESPONSES[response_index], default=str, sort_keys=True)
        other_responses = json.dumps(
            [value for index, value in enumerate(RESPONSES) if index != response_index],
            default=str, sort_keys=True,
        )
        if own_response.count(token) != 1 or token in other_responses \
           or response_evidence.count(token) != 1:
            raise RuntimeError("minted token escaped its one acquire success response")
    if any(token in response_evidence for token in SUBMITTED_WRONG_TOKENS):
        raise RuntimeError("submitted wrong token escaped into a response")
    print(f"canonical ownership lease local PG gate — {assertions} assertion groups passed")
    return 0


def authenticated_main(dsn: str) -> int:
    """Production SQL and real NOINHERIT login connections, never a ci bypass."""
    assertions = 0
    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cc.hard_fence(cur, dsn)
        cur.execute((ROOT / "mcp-server/test/authenticated-canonical-ownership-postgres.sql").read_text())
        admin.rollback()  # GUCs and role switches in the SQL proof are isolated.
        row = fixture(cur, slice_refs=[f"slice:authenticated:{uuid.uuid4().hex}"],
                      source_merge_paths=["mcp-server/src/authenticated-fixture.js"])
        admin.commit()
        claimed = cc.claim_one(admin, row[0], "ownership-authenticated-gate")
        seed = one(cur, """select e.work_request_id,e.accepted_plan_id,e.id,
          'session:'||e.agent_session_id::text,w.organization_tenant_id,
          least(j.leased_until,s.lease_expires_at,e.expires_at)-interval '5 seconds'
          from ops.engineering_execution_envelope e join ops.work_request w on w.id=e.work_request_id
          join ops.job j on j.id=e.job_id join ops.capability_agent_session s on s.id=e.agent_session_id
          where e.id=%s""", (row[1],))
    host = "cloudflare-workers:authenticated-pg-fixture"
    mint_key = uuid.uuid4()
    runtime = None

    def setup(cur, *, actor="codex", session=None, execution_host=host, token=None, ownership=None):
        cur.execute("set local role carr_ownership_issuer")
        for key, value in {
            "carr.organization_tenant_id": seed[4],
            "carr.acting_actor_slug": actor,
            "carr.receipt_session_ref": session or seed[3],
            "carr.execution_host_id": execution_host,
            "carr.engineering_job_lease_token": str(token or claimed[1]),
            "carr.ownership_session_id": ownership or runtime or "",
        }.items():
            one(cur, "select set_config(%s,%s,true)", (key, value))

    def mint(cur, key=mint_key, runtime_ref=None, execution_host=host):
        return one(cur, """select ops.mint_canonical_ownership_runtime_session(
          %s,%s,%s,%s,%s,%s,%s,%s)""",
          (*seed[:3], claimed[2], runtime_ref or seed[3], execution_host, seed[5], key))[0]

    def lifecycle(cur, request):
        return one(cur, "select ops.canonical_ownership_lifecycle(%s)", (Jsonb(request),))[0]

    def require(value, label):
        if value.get("ok") is not True:
            raise RuntimeError(f"authenticated ownership {label} refused: {value.get('reason_id') or value.get('refusal', {}).get('code')}")

    def deny(value, label):
        if value.get("ok") is not False:
            raise RuntimeError(f"authenticated ownership {label} unexpectedly succeeded")

    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        rejected_contexts: list[tuple[dict[str, object], str]] = [
            ({"actor": "joe"}, "wrong executor"),
            ({"token": uuid.uuid4()}, "wrong capability"),
            ({"session": "session:wrong-durable-session"}, "wrong session"),
            ({"execution_host": "cloudflare-workers:wrong-host"}, "wrong host"),
        ]
        for changes, label in rejected_contexts:
            setup(cur, **changes)
            deny(mint(cur, uuid.uuid4()), label)
            issuer.rollback()
            assertions += 1
        setup(cur)
        controller_sql = "select ops.authenticated_canonical_ownership_controller_binding(%s,%s,%s,%s)"
        controller = one(cur, controller_sql, (row[0], claimed[1], "ownership-authenticated-gate", "acquire"))[0]
        if not controller or controller["envelope_id"] != str(row[1]) or not controller.get("runtime_expires_at"):
            raise RuntimeError("issuer controller projection lost exact bounded authority")
        for capability, worker, operation in [(uuid.uuid4(), "ownership-authenticated-gate", "acquire"),
                (claimed[1], "wrong-worker", "acquire"), (claimed[1], "ownership-authenticated-gate", "dispatch")]:
            if one(cur, controller_sql, (row[0], capability, worker, operation))[0] is not None:
                raise RuntimeError("controller projection admitted wrong capability, worker, or operation")
        assertions += 4
        bound = mint(cur)
        require(bound, "mint")
        runtime = bound["ownership_session_ref"]
        require(mint(cur), "mint replay")
        issuer.commit()
        assertions += 2

        # A reused pooled connection carries neither local role nor authority.
        if one(cur, "select current_user,current_setting('carr.ownership_session_id',true)") != (
                "carr_ownership_issuer_g1", ""):
            raise RuntimeError("pooled connection retained transaction-local authority")
        issuer.rollback()
        setup(cur, ownership=runtime)
        require(one(cur, "select ops.canonical_ownership_trusted_context()")[0], "context")
        issuer.commit()
        assertions += 2

    acquire_request = {
        "operation": "acquire", "idempotency_key": str(uuid.uuid4()),
        "ttl_seconds": 60,
    }

    def concurrent_acquire():
        with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as conn, conn.cursor() as cur:
            setup(cur)
            value = lifecycle(cur, acquire_request)
            require(value, "concurrent acquire")
            return value

    raced = race(concurrent_acquire, concurrent_acquire, "authenticated same-key acquire")
    lease = raced["writer"]
    if lease["lease_id"] != raced["lease"]["lease_id"] or lease["lease_token"] != raced["lease"]["lease_token"]:
        raise RuntimeError("concurrent idempotency issued different leases")
    assertions += 1
    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        setup(cur)
        acquire_operation_id = uuid.UUID(cast(str, acquire_request["idempotency_key"]))
        readback = one(cur, "select ops.read_canonical_ownership_operation(%s)",
                       (acquire_operation_id,))[0]
        if readback["lease_id"] != lease["lease_id"] or readback["lease_token"] != lease["lease_token"]:
            raise RuntimeError("ambiguous acquire readback lost exact identity")
        conflict = dict(acquire_request, ttl_seconds=61)
        if lifecycle(cur, conflict).get("reason_id") != "ownership_operation_idempotency_conflict":
            raise RuntimeError("changed replay did not conflict")
        check = {"operation": "check", "idempotency_key": str(uuid.uuid4()),
                 "lease_id": lease["lease_id"], "lease_token": lease["lease_token"],
                 "fencing_generation": lease["fencing_generation"]}
        projected = one(cur, "select ops.canonical_ownership_claim_projection(%s)", (lease["lease_id"],))[0]
        if projected.get("path_claims") != [{"path": "mcp-server/src/authenticated-fixture.js", "mode": "file", "operation": "write"}]:
            raise RuntimeError("claims were not derived from the exact accepted scope")
        deny(lifecycle(cur, dict(acquire_request, idempotency_key=str(uuid.uuid4()), path_claims=[])), "caller-selected scope")
        require(lifecycle(cur, check), "check")
        deny(lifecycle(cur, dict(check, idempotency_key=str(uuid.uuid4()), lease_token=str(uuid.uuid4()))), "wrong lease token")
        renew = {"operation": "renew", "idempotency_key": str(uuid.uuid4()),
                "lease_id": lease["lease_id"], "lease_token": lease["lease_token"],
                "fencing_generation": lease["fencing_generation"], "ttl_seconds": 90}
        require(lifecycle(cur, renew), "renew")
        for request in [check, renew]:
            replay = one(cur, "select ops.read_canonical_ownership_operation(%s)",
                         (uuid.UUID(cast(str, request["idempotency_key"])),))[0]
            if replay.get("lease_token") != lease["lease_token"] or replay.get("lease_id") != lease["lease_id"]:
                raise RuntimeError("protected check/renew readback dropped lease capability")
        deny(one(cur, "select ops.read_canonical_ownership_operation(%s)", (uuid.uuid4(),))[0], "foreign operation readback")
        issuer.commit()
        assertions += 10
        rejected_replays: list[dict[str, object]] = [
            {"actor": "joe"}, {"token": uuid.uuid4()},
            {"session": "session:wrong-durable-session"},
            {"execution_host": "cloudflare-workers:wrong-host"},
            {"ownership": f"ownership:{uuid.uuid4()}"},
        ]
        for changes in rejected_replays:
            setup(cur, **changes)
            deny(one(cur, "select ops.canonical_ownership_trusted_context()")[0], "forged context")
            deny(one(cur, "select ops.read_canonical_ownership_operation(%s)",
                     (acquire_operation_id,))[0], "forged capability readback")
            issuer.rollback()
            assertions += 1

    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        original_deadline = one(cur, "select leased_until from ops.job where id=%s", (row[0],))[0]
        cur.execute("update ops.job set leased_until=clock_timestamp()+interval '60 seconds' where id=%s", (row[0],))
    try:
        with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
            setup(cur)
            if one(cur, controller_sql, (row[0], claimed[1], "ownership-authenticated-gate", "acquire"))[0] is not None:
                raise RuntimeError("acquire admitted insufficient dispatch runway")
            for operation in ["check", "renew", "release"]:
                if one(cur, controller_sql, (row[0], claimed[1], "ownership-authenticated-gate", operation))[0] is None:
                    raise RuntimeError("ongoing lifecycle incorrectly requires pre-dispatch runway")
            assertions += 4
    finally:
        with psycopg.connect(dsn) as admin, admin.cursor() as cur:
            cur.execute("update ops.job set leased_until=%s where id=%s", (original_deadline, row[0]))

    # Existing bindings survive rotation only for bounded drain operations.
    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cur.execute("update ops.canonical_ownership_issuer_generation set state='draining' where principal='carr_ownership_issuer_g1'")
        cur.execute("update ops.canonical_ownership_issuer_generation set state='active' where principal='carr_ownership_issuer_g2'")
    try:
        with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
            setup(cur)
            require(one(cur, "select ops.canonical_ownership_trusted_context()")[0], "draining context")
            require(mint(cur), "draining binding replay")
            deny(mint(cur, uuid.uuid4()), "draining mint")
            require(lifecycle(cur, dict(check, idempotency_key=str(uuid.uuid4()))), "draining check")
            deny(lifecycle(cur, dict(acquire_request, idempotency_key=str(uuid.uuid4()))), "new draining acquire")
            release = {"operation": "release", "idempotency_key": str(uuid.uuid4()),
                       "lease_id": lease["lease_id"], "lease_token": lease["lease_token"],
                       "fencing_generation": lease["fencing_generation"]}
            require(lifecycle(cur, release), "release")
            require(lifecycle(cur, release), "release replay")
            assertions += 7
    finally:
        with psycopg.connect(dsn) as admin, admin.cursor() as cur:
            cur.execute("update ops.canonical_ownership_issuer_generation set state='draining' where principal='carr_ownership_issuer_g2'")
            cur.execute("update ops.canonical_ownership_issuer_generation set state='active' where principal='carr_ownership_issuer_g1'")

    # Expired leases are replaced with a new generation, never resurrected.
    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        setup(cur)
        current = lifecycle(cur, dict(acquire_request, idempotency_key=str(uuid.uuid4())))
        require(current, "reacquire after release")
    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cur.execute("update ops.canonical_ownership_lease set acquired_at=clock_timestamp()-interval '2 minutes',expires_at=clock_timestamp()-interval '1 minute' where id=%s", (current["lease_id"],))
    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        setup(cur)
        current_check = dict(check, idempotency_key=str(uuid.uuid4()), lease_id=current["lease_id"],
                             lease_token=current["lease_token"], fencing_generation=current["fencing_generation"])
        deny(lifecycle(cur, current_check), "expired lease")
        replacement = lifecycle(cur, dict(acquire_request, idempotency_key=str(uuid.uuid4())))
        require(replacement, "expired lease replacement")
        deny(lifecycle(cur, dict(current_check, idempotency_key=str(uuid.uuid4()))), "replaced lease")
        assertions += 3

    # The actual scoped failure door must clean up before making the job terminal.
    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cc.set_jobs(cur)
        one(cur, "select ops.engineering_fail_claim(%s,%s,'fixture','authenticated terminal cleanup')", (row[0], claimed[1]))
        cc.reset_role(cur)
        state = one(cur, "select state from ops.canonical_ownership_lease where id=%s", (replacement["lease_id"],))[0]
        if state != "released":
            raise RuntimeError("terminal job left an active ownership lease")
    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        setup(cur)
        deny(one(cur, "select ops.canonical_ownership_trusted_context()")[0], "terminal runtime binding")
        assertions += 2
    assertions += authenticated_merge_proof(dsn)
    print(f"authenticated canonical ownership local PG gate — {assertions} assertion groups passed; no test-role production bypass")
    return 0


def authenticated_merge_proof(dsn: str) -> int:
    """Exercise post-terminal merge authority; history is fixture data, not a bypass."""
    head = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    pr = 126
    host = "cloudflare-workers:merge-pg-fixture"
    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        row = fixture(cur, slice_refs=[f"slice:merge:{uuid.uuid4().hex}"],
                      source_merge_paths=["mcp-server/src/merge-fixture.js"])
        admin.commit()
        claimed = cc.claim_one(admin, row[0], "ownership-merge-gate")
        seed = one(cur, """select e.work_request_id,e.accepted_plan_id,w.ref,w.organization_tenant_id,
          'session:'||e.agent_session_id::text,
          least(e.expires_at,s.lease_expires_at,j.leased_until)-interval '5 seconds'
          from ops.engineering_execution_envelope e join ops.work_request w on w.id=e.work_request_id
          join ops.job j on j.id=e.job_id join ops.capability_agent_session s on s.id=e.agent_session_id
          where e.id=%s""", (row[1],))
        decision_id = uuid.uuid4()
        event_id, decision_expiry = one(cur, """insert into public.event(
          occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause,human_quote,
          sponsoring_human_slug,authorization_class,organization_tenant_id)
          select clock_timestamp(),id,'log-decision','decision',%s,%s,'human_stated',%s,
                 'joe','human',%s from public.actor where slug='joe' and active
          returning id,occurred_at+interval '119 minutes'""",
          (decision_id, Jsonb({"title": f"Gate A2 source-merge authority: {seed[2]}", "quote_absent": False}),
           f"I approve Gate A2 for {seed[2]} PR #{pr} at {head} for 120 minutes", seed[3]))
        invalid_decisions = []
        expected_title = f"Gate A2 source-merge authority: {seed[2]}"
        expected_quote = f"I approve Gate A2 for {seed[2]} PR #{pr} at {head} for 120 minutes"
        for title, quote, absent, authorization, offset, label in [
            ("Routine authorized green PRs merge without asking Joe for ceremonial approval", expected_quote, False, "human", "0 seconds", "global policy decision"),
            (expected_title, expected_quote + ".", False, "human", "0 seconds", "inexact human quote"),
            (expected_title, expected_quote, True, "human", "0 seconds", "absent quote"),
            (expected_title, expected_quote, False, "automation", "0 seconds", "nonhuman authorization"),
            (expected_title, expected_quote, False, "human", "-121 minutes", "expired decision"),
        ]:
            bad_id = uuid.uuid4()
            one(cur, """insert into public.event(occurred_at,actor_id,verb,subject_type,subject_id,
              new_value,cause,human_quote,sponsoring_human_slug,authorization_class,organization_tenant_id)
              select clock_timestamp()+%s::interval,id,'log-decision','decision',%s,%s,'human_stated',%s,
                'joe',%s,%s from public.actor where slug='joe' and active returning id""",
              (offset, bad_id, Jsonb({"title": title, "quote_absent": absent}), quote, authorization, seed[3]))
            invalid_decisions.append((bad_id, label))

    def configure(cur, binding=None):
        cur.execute("set local role carr_ownership_issuer")
        values = {"carr.organization_tenant_id": seed[3], "carr.acting_actor_slug": "codex",
                  "carr.execution_host_id": host,
                  "carr.receipt_session_ref": binding["runtime_session_ref"] if binding else seed[4],
                  "carr.ownership_session_id": binding["ownership_session_ref"] if binding else "",
                  "carr.engineering_job_lease_token": binding["merge_capability_token"] if binding else str(claimed[1])}
        for key, value in values.items():
            one(cur, "select set_config(%s,%s,true)", (key, value))

    def require(value, label):
        if not value or value.get("ok") is not True:
            raise RuntimeError(f"merge fixture {label} refused: {(value or {}).get('reason_id') or (value or {}).get('error')}")

    def reject(value, label):
        if not value or value.get("ok") is not False:
            raise RuntimeError(f"merge fixture {label} unexpectedly authorized")

    merge_key = uuid.uuid4()
    mint_sql = "select ops.mint_canonical_ownership_merge_session(%s,%s,%s,%s,%s,%s,%s)"
    mint_args = (row[1], decision_id, head, pr, host, decision_expiry, merge_key)
    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        configure(cur)
        reject(one(cur, mint_sql, mint_args)[0], "missing claimed_complete")
        build = one(cur, """select ops.mint_canonical_ownership_runtime_session(
          %s,%s,%s,%s,%s,%s,%s,%s)""",
          (seed[0], seed[1], row[1], claimed[2], seed[4], host, seed[5], uuid.uuid4()))[0]
        require(build, "build mint")
        one(cur, "select set_config('carr.ownership_session_id',%s,true)", (build["ownership_session_ref"],))
        build_lease = one(cur, "select ops.canonical_ownership_lifecycle(%s)",
            (Jsonb({"operation": "acquire", "idempotency_key": str(uuid.uuid4()), "ttl_seconds": 60}),))[0]
        require(build_lease, "build acquire")

    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cc.set_jobs(cur)
        payload = cc.receipt_payload(cur, row, claimed, "claimed_complete")
        payload["source_evidence"]["source_sha"] = head
        receipt_id = one(cur, """select id from ops.engineering_finalize_slice_receipt(%s,%s,%s,%s,%s)""",
            (row[1], claimed[1], Jsonb(payload), cc.canonical_digest(payload), row[3]))[0]
        cc.reset_role(cur)
        if one(cur, "select state from ops.canonical_ownership_lease where id=%s", (build_lease["lease_id"],))[0] != "released":
            raise RuntimeError("successful terminal receipt did not release build lease")
        insert_review(cur, row, receipt_id)
        reviewer_id = one(cur, "select id from ops.engineering_reviewer_fact where receipt_id=%s", (receipt_id,))[0]
        # Seed immutable historical assurance rows through ordinary INSERT with
        # all production constraints/triggers enabled. This fixture tests the
        # projection of history, not the separately gated assurance authoring API.
        manifests = []
        for stage in ["post_commit", "review"]:
            manifests.append(one(cur, """insert into ops.assurance_execution_manifest(
              organization_tenant_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,
              lease_id,fencing_generation,repository_stage,repository_commit_sha,repository_tree_sha,
              compiler_id,compiler_version,input_digest,manifest_hash,applicable_rule_snapshot_digest,
              coordination_snapshot_digest,snapshot_valid_until,applicable_rules,coordination_snapshot,
              compiler_input,ownership_contract_digest,manifest,idempotency_key)
              select l.organization_tenant_id,l.work_request_id,l.accepted_plan_id,l.slice_plan_id,l.slice_ref,
                l.id,l.fencing_generation,%s,%s,%s,'carr-assurance-slice-compiler','fixture',%s,%s,%s,%s,
                clock_timestamp()+interval '1 hour','{}','{}','{}',l.contract_digest,'{}',%s
              from ops.canonical_ownership_lease l where l.id=%s returning id""",
              (stage, head, "1" * 40, sha("a"), "sha256:" + uuid.uuid4().hex * 2,
               sha("b"), sha("c"), uuid.uuid4(), build_lease["lease_id"]))[0])
        evidence_id = one(cur, """insert into ops.assurance_evidence_extension(
          receipt_id,manifest_id,evidence_digest,evidence,idempotency_key) values(%s,%s,%s,'{}',%s) returning id""",
          (receipt_id, manifests[0], "sha256:" + uuid.uuid4().hex * 2, uuid.uuid4()))[0]
        one(cur, """insert into ops.assurance_review_extension(
          reviewer_fact_id,review_manifest_id,evidence_id,review_digest,review,idempotency_key)
          values(%s,%s,%s,%s,'{}',%s) returning id""",
          (reviewer_id, manifests[1], evidence_id, "sha256:" + uuid.uuid4().hex * 2, uuid.uuid4()))
        historical = one(cur, "select jsonb_agg(to_jsonb(m) order by id) from ops.assurance_execution_manifest m where id=any(%s)", (manifests,))[0]

    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        configure(cur)
        one(cur, "select set_config('carr.ownership_session_id',%s,true)", (build["ownership_session_ref"],))
        reject(one(cur, "select ops.canonical_ownership_trusted_context()")[0], "terminal build reuse")
        changed_cases: list[tuple[dict[int, object], str]] = [
            ({1: uuid.uuid4()}, "wrong decision"),
            ({2: "f" * 40}, "wrong head"),
            ({3: pr + 1}, "wrong PR"),
            ({5: datetime(2000, 1, 1).isoformat() + "Z"}, "expired expiry"),
            ({5: datetime(2099, 1, 1).isoformat() + "Z"}, "expiry beyond decision"),
        ]
        for changed, label in changed_cases:
            args = list(mint_args)
            args[-1] = uuid.uuid4()
            for index, value in changed.items():
                args[index] = value
            reject(one(cur, mint_sql, tuple(args))[0], label)
        for bad_id, label in invalid_decisions:
            args = list(mint_args)
            args[1], args[-1] = bad_id, uuid.uuid4()
            reject(one(cur, mint_sql, tuple(args))[0], label)
        merge = one(cur, mint_sql, mint_args)[0]
        require(merge, "exact Gate A2 mint")
        replay = one(cur, mint_sql, mint_args)[0]
        if replay != merge or merge["phase"] != "merge" or merge["ownership_session_ref"] == build["ownership_session_ref"]:
            raise RuntimeError("merge did not mint a distinct idempotent phase binding")
        if merge["merge_capability_token"] in {str(claimed[1]), build_lease["lease_token"]}:
            raise RuntimeError("merge reused build authority")
        configure(cur, merge)
        merge_lease = one(cur, "select ops.canonical_ownership_lifecycle(%s)",
            (Jsonb({"operation": "acquire", "idempotency_key": str(uuid.uuid4()), "ttl_seconds": 60}),))[0]
        require(merge_lease, "merge acquire")
        if merge_lease["lease_id"] == build_lease["lease_id"]:
            raise RuntimeError("merge reused terminal build lease")

    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cur.execute("set local role carr_reader")
        projection = one(cur, "select ops.source_merge_authority_projection(%s,null,%s,%s)", (decision_id, head, pr))[0]
        require(projection, "projection consumes merge phase")
        claims = projection["authority"]["authorized_path_claims"]
        if not claims or {claim["lease_ref"] for claim in claims} != {f"canonical-ownership-lease:{merge_lease['lease_id']}"}:
            raise RuntimeError("source-merge projection consumed historical build lease")
        for bad_head, bad_pr in [("f" * 40, pr), (head, pr + 1)]:
            reject(one(cur, "select ops.source_merge_authority_projection(%s,null,%s,%s)", (decision_id, bad_head, bad_pr))[0], "wrong projection source")
        cur.execute("reset role")
        if one(cur, "select jsonb_agg(to_jsonb(m) order by id) from ops.assurance_execution_manifest m where id=any(%s)", (manifests,))[0] != historical:
            raise RuntimeError("merge projection rewrote historical build manifests")
    with psycopg.connect(dsn, user="carr_ownership_issuer_g1") as issuer, issuer.cursor() as cur:
        configure(cur, merge)
        require(one(cur, "select ops.canonical_ownership_lifecycle(%s)", (Jsonb({
            "operation": "release", "idempotency_key": str(uuid.uuid4()),
            "lease_id": merge_lease["lease_id"], "lease_token": merge_lease["lease_token"],
            "fencing_generation": merge_lease["fencing_generation"]}),))[0], "merge release")
    with psycopg.connect(dsn) as admin, admin.cursor() as cur:
        cur.execute("set local role carr_reader")
        reject(one(cur, "select ops.source_merge_authority_projection(%s,null,%s,%s)", (decision_id, head, pr))[0], "released merge projection")
    return 23


if __name__ == "__main__":
    if sys.argv[1:] == ["--fingerprint-only"]:
        fingerprint_dsn = os.environ.get("CARR_LOCAL_PG_DSN", "")
        with psycopg.connect(fingerprint_dsn) as fingerprint_conn:
            with fingerprint_conn.cursor() as fingerprint_cur:
                print(json.dumps(catalog_fingerprint(fingerprint_cur), sort_keys=True))
        raise SystemExit(0)
    captured_out = io.StringIO()
    captured_err = io.StringIO()
    failure: Exception | None = None
    result = 1
    with redirect_stdout(captured_out), redirect_stderr(captured_err):
        try:
            result = main()
        except Exception as exc:
            failure = exc
            print(
                "canonical-ownership-lease-local-pg-gate: FAIL — "
                f"{type(exc).__name__}: {safe_error(exc)}",
                file=sys.stderr,
            )
    rendered_output = captured_out.getvalue() + captured_err.getvalue()
    escaped = next((secret for secret in SECRET_TOKENS if secret in rendered_output), None)
    if escaped is not None:
        print(
            "canonical-ownership-lease-local-pg-gate: FAIL — submitted token "
            "escaped into captured stdout/stderr",
            file=sys.stderr,
        )
        raise SystemExit(1)
    sys.stdout.write(captured_out.getvalue())
    sys.stderr.write(captured_err.getvalue())
    raise SystemExit(1 if failure is not None else result)
