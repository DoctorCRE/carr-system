// action-class-successor-registry-postgres.test.mjs — V5-D01 on REAL
// PostgreSQL. The fake-client suite proves the handlers' own logic in
// isolation; this file proves the things only a real database can prove:
// migration 0708's status CHECK constraint really admits only 'inactive',
// the immutability triggers really refuse UPDATE/DELETE/TRUNCATE, the gate
// function really denies every action_class unconditionally (registered or
// not), and a gate denial for one action_class has no effect on any other
// query in the same session -- "missing future gate denies action but not
// unrelated work".
//
// Skips in the unit class; the migration class supplies DATABASE_URL and sets
// CARR_ACTION_CLASS_SUCCESSOR_DB_REQUIRED=1, which turns a silent skip into a
// failure (same convention as amend-closed-loop-postgres.test.mjs).

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { TOOLS, ToolError } from "../src/tools.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_ACTION_CLASS_SUCCESSOR_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

async function mintActor(admin, slug) {
  const row = (await admin.query(
    `insert into public.actor(slug, kind, display_name, active) values ($1,'automation',$1,true)
       on conflict (slug) do update set active = true returning id, slug`,
    [slug])).rows[0];
  return { id: row.id, slug, human: false, via: "test", client_id: "action-class-successor-registry-postgres.test" };
}

function actionClass(prefix) {
  return `${prefix}_${randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

test("DB: register-action-class-successor writes a real row with status inactive", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-register-${randomUUID().slice(0, 8)}`);
    const cls = actionClass("email_unattended_send");
    const result = await TOOLS["register-action-class-successor"].handler(admin, actor, {
      idempotency_key: randomUUID(), action_class: cls,
      title: "Unattended email sends", goal: "Future automated partner correspondence dispatch.",
      owner: "joe", activation_predicate: { requires: ["partner sign-off"] } });
    assert.equal(result.ok, true);
    assert.equal(result.status, "inactive");
    const row = (await admin.query(
      "select status, actor_id from action_class_successor where action_class=$1", [cls])).rows[0];
    assert.equal(row.status, "inactive");
    assert.equal(row.actor_id, actor.id);
  } finally { await admin.end(); }
});

test("DB: the status CHECK constraint refuses any value but 'inactive'", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-check-${randomUUID().slice(0, 8)}`);
    const cls = actionClass("salesforce_unattended_write");
    await assert.rejects(
      admin.query(
        `insert into action_class_successor
           (action_class, title, goal, owner, activation_predicate, status, actor_id)
         values ($1,'t','g','o','{}'::jsonb,'active',$2)`,
        [cls, actor.id]),
      /action_class_successor_status_inactive_only/,
      "registration cannot mint an effect capability -- the database itself refuses any status but 'inactive'");
  } finally { await admin.end(); }
});

test("DB: rows are append-only -- UPDATE, DELETE and TRUNCATE are all refused", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-immutable-${randomUUID().slice(0, 8)}`);
    const cls = actionClass("salesforce_unattended_write");
    await TOOLS["register-action-class-successor"].handler(admin, actor, {
      idempotency_key: randomUUID(), action_class: cls, title: "t", goal: "g", owner: "joe",
      activation_predicate: {} });
    await assert.rejects(
      admin.query("update action_class_successor set title='edited' where action_class=$1", [cls]),
      /append-only/);
    await assert.rejects(
      admin.query("delete from action_class_successor where action_class=$1", [cls]),
      /append-only/);
    await assert.rejects(admin.query("truncate action_class_successor"), /append-only/);
  } finally { await admin.end(); }
});

test("DB: the gate denies an unregistered action_class, and again a registered-but-inactive one", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-gate-${randomUUID().slice(0, 8)}`);
    const unregistered = actionClass("email_unattended_send");
    const denyBefore = await TOOLS["read-action-class-gate"].handler(admin, actor, { action_class: unregistered });
    assert.equal(denyBefore.allowed, false);
    assert.equal(denyBefore.registered, false);

    await TOOLS["register-action-class-successor"].handler(admin, actor, {
      idempotency_key: randomUUID(), action_class: unregistered, title: "t", goal: "g", owner: "joe",
      activation_predicate: {} });
    const denyAfter = await TOOLS["read-action-class-gate"].handler(admin, actor, { action_class: unregistered });
    assert.equal(denyAfter.allowed, false, "registration cannot mint an effect capability -- registering never flips the gate");
    assert.equal(denyAfter.registered, true);
  } finally { await admin.end(); }
});

test("DB: a gate denial for one action_class has no effect on another action_class or on unrelated queries", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-scope-${randomUUID().slice(0, 8)}`);
    const classA = actionClass("salesforce_unattended_write");
    const classB = actionClass("email_unattended_send");
    await TOOLS["register-action-class-successor"].handler(admin, actor, {
      idempotency_key: randomUUID(), action_class: classA, title: "t", goal: "g", owner: "joe",
      activation_predicate: {} });

    const gateA = await TOOLS["read-action-class-gate"].handler(admin, actor, { action_class: classA });
    assert.equal(gateA.allowed, false);

    // Unrelated work: classB was never touched by classA's denial, and an
    // entirely unrelated table read in the SAME session still succeeds --
    // the deny is scoped to the one action_class argument, not a lock, not a
    // global flag, not a refusal of the session.
    const gateB = await TOOLS["read-action-class-gate"].handler(admin, actor, { action_class: classB });
    assert.equal(gateB.allowed, false);
    assert.equal(gateB.registered, false, "classB was never registered; classA's registration does not leak to it");

    const unrelated = await admin.query("select 1 as ok");
    assert.equal(unrelated.rows[0].ok, 1, "an unrelated query in the same session is unaffected by the gate denial");
  } finally { await admin.end(); }
});

// THE MUTANTS RUN AGAINST 0708'S OWN OBJECTS, never a look-alike table. Each
// one opens a transaction, removes exactly one real guard from the real
// public.action_class_successor (DROP CONSTRAINT / DISABLE TRIGGER are
// transactional in PostgreSQL), shows the forbidden write now lands, and rolls
// back. The afterwards-check proves the rollback restored the guard, so the
// proofs above are the ones that read the load-bearing object.
async function inMutantTransaction(admin, mutate, body) {
  await admin.query("begin");
  try {
    await admin.query(mutate);
    await body();
  } finally {
    await admin.query("rollback");
  }
}

test("DB MUTANT: dropping 0708's real status CHECK lets an 'active' row in -- the constraint is load-bearing", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-mutant-check-${randomUUID().slice(0, 8)}`);
    const insertActive = (cls) => admin.query(
      `insert into action_class_successor
         (action_class, title, goal, owner, activation_predicate, status, actor_id)
       values ($1,'t','g','o','{}'::jsonb,'active',$2) returning status`,
      [cls, actor.id]);

    await inMutantTransaction(admin,
      "alter table public.action_class_successor drop constraint action_class_successor_status_inactive_only",
      async () => {
        const row = (await insertActive(actionClass("mutant_check"))).rows[0];
        assert.equal(row.status, "active",
          "MUTANT: with the real CHECK removed from the real table, an 'active' row is accepted");
      });

    const restored = (await admin.query(
      `select 1 from pg_constraint
        where conrelid = 'public.action_class_successor'::regclass
          and conname = 'action_class_successor_status_inactive_only'`)).rows;
    assert.equal(restored.length, 1, "the rollback must restore 0708's CHECK constraint");
    await assert.rejects(insertActive(actionClass("mutant_check_after")),
      /action_class_successor_status_inactive_only/,
      "with the constraint back, the same insert is refused again");
  } finally { await admin.end(); }
});

test("DB MUTANT: disabling 0708's real immutability trigger lets an UPDATE through -- the trigger is load-bearing", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  try {
    const actor = await mintActor(admin, `d01-mutant-trigger-${randomUUID().slice(0, 8)}`);
    const cls = actionClass("mutant_trigger");
    await TOOLS["register-action-class-successor"].handler(admin, actor, {
      idempotency_key: randomUUID(), action_class: cls, title: "original", goal: "g", owner: "joe",
      activation_predicate: {} });
    const edit = () => admin.query(
      "update action_class_successor set title='edited' where action_class=$1 returning title", [cls]);

    await inMutantTransaction(admin,
      "alter table public.action_class_successor disable trigger action_class_successor_immutable",
      async () => {
        const row = (await edit()).rows[0];
        assert.equal(row?.title, "edited",
          "MUTANT: with the real append-only trigger disabled, UPDATE rewrites the row");
      });

    await assert.rejects(edit(), /append-only/,
      "with the trigger re-enabled by the rollback, the same UPDATE is refused again");
    const row = (await admin.query(
      "select title from action_class_successor where action_class=$1", [cls])).rows[0];
    assert.equal(row.title, "original", "the mutant's edit did not survive the rollback");
  } finally { await admin.end(); }
});
