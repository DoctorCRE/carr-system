#!/usr/bin/env node
// Focused PostgreSQL concurrency proof. Uses a fresh Docker PostgreSQL container
// and synthetic rows only; it never reads DATABASE_URL.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(resolve(root, "mcp-server/package.json"));
const { Client } = require("pg");
const container = `wr-supersession-proof-${process.pid}`;
const password = "synthetic-only-retirement-proof";
const graphLock = "ops.work_request_supersession_graph";

function docker(args) {
  const r = spawnSync("docker", args, { encoding: "utf8", timeout: 120_000 });
  if (r.status !== 0) throw new Error(`docker ${args[0]} failed: ${r.stderr.trim()}`);
  return r.stdout.trim();
}

const fixture = `
create schema ops;
create role carr_writer;
create table public.actor(id uuid primary key, slug text unique not null);
insert into public.actor values ('10000000-0000-0000-0000-000000000001','joe');
create table ops.work_request (
  id uuid primary key, ref text unique not null, title text not null,
  organization_tenant_id text not null, state text not null, version integer not null,
  capture_idempotency_key uuid, doctrine_section_id uuid, doctrine_revision_id uuid,
  sourced_capture_sequence integer, origin_ref text, program_key text, program_ordinal integer,
  requester_actor text, owner_actor text, triage_classification text, triaged_by_actor_id uuid,
  triaged_at timestamptz, shape_disposition text, shape_fixed_surface_ref text,
  shape_rationale text, shape_decided_by_actor_id uuid, shape_decided_at timestamptz,
  exit_reason text, closed_at timestamptz, superseded_by uuid references ops.work_request(id),
  updated_at timestamptz default now(),
  constraint work_request_sourced_capture_shape check (true)
);
create table ops.work_request_withdrawal_receipt (
  id uuid primary key default gen_random_uuid(), work_request_id uuid not null,
  idempotency_key uuid not null unique, base_version integer not null,
  result_version integer not null, final_state text not null, exit_reason text not null,
  superseded_by uuid, withdrawn_by_actor_id uuid not null, withdrawn_at timestamptz not null default now()
);
create table ops.work_request_triage_receipt (
  work_request_id uuid, base_version integer, result_version integer, classification text,
  triaged_by_actor_id uuid, triaged_at timestamptz
);
create table ops.sourced_work_request_shape_disposition_receipt (
  work_request_id uuid, base_version integer, result_version integer, disposition text,
  fixed_surface_ref text, rationale text, decided_by_actor_id uuid, decided_at timestamptz
);
create table ops.sourced_work_request_plan_acceptance_receipt (
  id uuid, work_request_id uuid, base_version integer, result_version integer, plan_hash text,
  plan_id uuid, shape_fixed_surface_ref text, shape_rationale text,
  accepted_by_actor_id uuid, accepted_at timestamptz
);
create table ops.sourced_work_request_plan (
  id uuid, plan_hash text, plan_ref text, runbook_ref text, runbook_content_hash text
);
create table ops.sourced_work_request_plan_shape_binding_receipt (
  id uuid, plan_acceptance_receipt_id uuid, work_request_id uuid, disposition text,
  fixed_surface_ref text, rationale text, decided_by_actor_id uuid, decided_at timestamptz
);
create function ops.sourced_work_request_is_immutable() returns trigger
language plpgsql as $$ begin return new; end $$;
create trigger sourced_work_request_is_immutable before insert or update on ops.work_request
for each row execute function ops.sourced_work_request_is_immutable();
create function ops.supersede_sourced_work_request(text,integer,text,text,text,uuid)
returns table(ref text,state text,version integer,exit_reason text,closed_at timestamptz,superseded_by_ref text)
language sql as $$ select null::text,null::text,null::integer,null::text,null::timestamptz,null::text where false $$;
create function ops.decline_sourced_work_request(
  p_work_request text,p_base_version integer,p_exit_reason text,p_actor_slug text,p_idempotency_key uuid)
returns table(ref text,state text,version integer,exit_reason text,closed_at timestamptz)
language plpgsql as $fn$
declare v_id uuid; v_actor uuid; v_now timestamptz:=now();
begin
  select id into v_actor from public.actor where slug=p_actor_slug;
  select w.id into v_id from ops.work_request w where w.ref=p_work_request
    and w.capture_idempotency_key is not null and w.state='captured' and w.version=p_base_version for update;
  if v_id is null then raise exception 'decline source CAS failed'; end if;
  insert into ops.work_request_withdrawal_receipt
    (work_request_id,idempotency_key,base_version,result_version,final_state,exit_reason,
     superseded_by,withdrawn_by_actor_id,withdrawn_at)
  values (v_id,p_idempotency_key,p_base_version,p_base_version+1,'declined',p_exit_reason,null,v_actor,v_now);
  update ops.work_request w set state='declined',exit_reason=p_exit_reason,closed_at=v_now,
    version=w.version+1,updated_at=v_now where w.id=v_id;
  return query select w.ref,w.state,w.version,w.exit_reason,w.closed_at
    from ops.work_request w where w.id=v_id;
end;
$fn$;
insert into ops.work_request
  (id,ref,title,organization_tenant_id,state,version,capture_idempotency_key,
   doctrine_section_id,doctrine_revision_id,sourced_capture_sequence,origin_ref,requester_actor,owner_actor)
select ('50000000-0000-0000-0000-' || lpad(n::text,12,'0'))::uuid,
  'WR-SYN-' || lpad(n::text,6,'0'),'synthetic row','carr-internal','captured',1,
  gen_random_uuid(),'20000000-0000-0000-0000-000000000001',
  '30000000-0000-0000-0000-000000000001',n,'doctrine:synthetic#proof','synthetic','synthetic'
from generate_series(1,40) n;
`;

function client(port, application_name = "wr-proof") {
  return new Client({ host: "127.0.0.1", port, user: "postgres", password,
    database: "postgres", application_name, connectionTimeoutMillis: 2_000 });
}

function ref(n) { return `WR-SYN-${String(n).padStart(6, "0")}`; }
function uuid(n) { return `50000000-0000-0000-0000-${String(n).padStart(12, "0")}`; }

async function blocked(monitor, names) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const r = await monitor.query(
      "select count(*)::int n from pg_stat_activity where application_name=any($1) and wait_event_type='Lock'",
      [names]);
    if (r.rows[0].n === names.length) return;
    await new Promise(resolve => setTimeout(resolve, 40));
  }
  const state = await monitor.query(
    "select application_name,state,wait_event_type,wait_event,left(query,150) query from pg_stat_activity where application_name=any($1)",
    [names]);
  throw new Error("contenders did not reach concurrent PostgreSQL lock waits: " + JSON.stringify(state.rows));
}

async function contender(port, app, source, successor, decline = false) {
  const c = client(port, app);
  await c.connect();
  try {
    const r = decline
      ? await c.query("select * from ops.decline_sourced_work_request($1,1,'synthetic decline','joe',$2::uuid)", [source, randomUUID()])
      : await c.query("select * from ops.supersede_sourced_work_request($1,1,'synthetic edge',$2,'joe',$3::uuid)", [source, successor, randomUUID()]);
    return { ok: true, state: r.rows[0].state, prior_state: r.rows[0].previous_state };
  } catch (e) {
    return { ok: false, code: e.code, error: e.message };
  } finally { await c.end(); }
}

async function race(port, attempts, lockSql, lockValues = []) {
  const holder = client(port, "wr-proof-holder");
  await holder.connect();
  await holder.query("begin");
  await holder.query(lockSql, lockValues);
  const names = attempts.map((_, i) => `wr-proof-${process.pid}-${i}`);
  const results = attempts.map(() => null);
  const pending = attempts.map(([source, successor, decline], i) =>
    contender(port, names[i], source, successor, decline).then(result => (results[i] = result)));
  try {
    const monitor = client(port, "wr-proof-monitor");
    await monitor.connect();
    try { await blocked(monitor, names); } catch (e) { e.message += "; completed=" + JSON.stringify(results); throw e; }
    finally { await monitor.end(); }
    await holder.query("commit");
    return await Promise.all(pending);
  } catch (e) {
    await holder.query("rollback").catch(() => {});
    await Promise.allSettled(pending);
    throw e;
  } finally { await holder.end(); }
}

async function edge(db, from, to) {
  await db.query("select * from ops.supersede_sourced_work_request($1,1,'synthetic edge',$2,'joe',$3::uuid)",
    [ref(from), ref(to), randomUUID()]);
}

async function verifyAcyclic(db) {
  const r = await db.query(`
    with recursive walk(start_id,id,next_id,path,is_cycle) as (
      select w.id,w.id,w.superseded_by,array[w.id],false from ops.work_request w where w.superseded_by is not null
      union all
      select walk.start_id,next.id,next.superseded_by,walk.path || next.id,next.id=any(walk.path)
        from walk join ops.work_request next on next.id=walk.next_id
       where walk.next_id is not null and not walk.is_cycle
    ) select count(*)::int n from walk where is_cycle
  `);
  assert.equal(r.rows[0].n, 0, "persisted successor graph must remain acyclic");
}

async function main() {
  docker(["info", "--format", "{{.ServerVersion}}"]);
  docker(["run", "--detach", "--rm", "--name", container, "-e", "POSTGRES_PASSWORD=" + password,
    "-p", "127.0.0.1::5432", "postgres:18"]);
  try {
    const binding = docker(["port", container, "5432/tcp"]).split("\n")[0];
    const port = Number(binding.slice(binding.lastIndexOf(":") + 1));
    let db;
    for (let i = 0; i < 60; i++) {
      try { db = client(port, "wr-proof-setup"); await db.connect(); break; }
      catch { if (db) await db.end().catch(() => {}); db = undefined; await new Promise(r => setTimeout(r, 250)); }
    }
    assert.ok(db, "ephemeral PostgreSQL did not become ready");
    try {
      await db.query(fixture);
      const migration = readFileSync(resolve(root, "migrations/0881_allow_triaged_work_request_supersession.sql"), "utf8");
      await db.query(migration);

      const gate = "select pg_advisory_xact_lock(hashtextextended($1,0))";
      const opposing = await race(port, [[ref(1), ref(2)], [ref(2), ref(1)]], gate, [graphLock]);
      assert.equal(opposing.filter(x => x.ok).length, 1, JSON.stringify(opposing));
      assert.equal(opposing.filter(x => !x.ok).length, 1, JSON.stringify(opposing));
      await verifyAcyclic(db);

      await edge(db, 10, 11);
      await edge(db, 12, 13);
      // Endpoint pairs (11,12) and (13,10) are disjoint; row-pair locks alone
      // cannot serialize this attempt to close a four-node cycle.
      const longer = await race(port, [[ref(11), ref(12)], [ref(13), ref(10)]], gate, [graphLock]);
      assert.equal(longer.filter(x => x.ok).length, 0, JSON.stringify(longer));
      assert.equal(longer.filter(x => !x.ok).length, 2, JSON.stringify(longer));
      assert.ok(longer.every(x => /successor.*withdrawn/i.test(x.error)), JSON.stringify(longer));
      await verifyAcyclic(db);

      // Model a legacy pointer chain that the prior direct two-row test cannot
      // see. Only synthetic rows are involved; bypass the trigger while installing
      // this pre-existing fixture graph, then test the real migration function.
      await db.query("begin");
      await db.query("set local session_replication_role=replica");
      for (const [from, to] of [[30, 31], [31, 32], [32, 33]]) {
        await db.query("update ops.work_request source set superseded_by=target.id from ops.work_request target where source.ref=$1 and target.ref=$2",
          [ref(from), ref(to)]);
      }
      await db.query("commit");
      await assert.rejects(() => edge(db, 33, 30), /supersession cycle/i);
      await verifyAcyclic(db);

      // Legacy rows may carry cross-tenant successor links. The attempted
      // same-tenant edge 34→35 would close 34→35→36→34; traversal must cross
      // tenant 36 and report the cycle before the normal dead-successor refusal.
      await db.query("begin");
      await db.query("set local session_replication_role=replica");
      // The current row-shape constraint pins sourced rows to carr-internal.
      // Drop it only in this disposable fixture to model legacy cross-tenant
      // rows that predate that constraint; the migration/function stay intact.
      await db.query("alter table ops.work_request drop constraint work_request_sourced_capture_shape");
      await db.query(`update ops.work_request source
         set organization_tenant_id='synthetic-other-tenant',state='superseded',
             exit_reason='legacy synthetic edge',closed_at=now(),superseded_by=target.id
        from ops.work_request target where source.ref=$1 and target.ref=$2`, [ref(36), ref(34)]);
      await db.query(`update ops.work_request source
         set state='superseded',exit_reason='legacy synthetic edge',closed_at=now(),superseded_by=target.id
        from ops.work_request target where source.ref=$1 and target.ref=$2`, [ref(35), ref(36)]);
      await db.query("commit");
      await assert.rejects(() => edge(db, 34, 35), /supersession cycle/i);
      await verifyAcyclic(db);

      const decline = await race(port, [[ref(20), ref(21)], [ref(21), null, true]],
        "select id from ops.work_request where id=$1::uuid for update", [uuid(21)]);
      assert.equal(decline.filter(x => x.ok).length, 1, JSON.stringify(decline));
      assert.equal(decline.filter(x => !x.ok).length, 1, JSON.stringify(decline));
      await verifyAcyclic(db);
      console.log(JSON.stringify({ database: "disposable PostgreSQL 18; synthetic data only",
        opposing_supersession: opposing, disjoint_pair_long_cycle: longer,
        legacy_chain_closure: "recursive check refused a cycle through three existing links",
        legacy_cross_tenant_chain: "recursive check crossed the tenant boundary and refused 34→35→36→34",
        supersede_vs_successor_decline: decline, persisted_graph: "acyclic after each race" }, null, 2));
    } finally { await db.end(); }
  } finally { docker(["rm", "--force", container]); }
}

main().catch(e => { console.error(e.stack || String(e)); process.exitCode = 1; });
