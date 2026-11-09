import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import test, { after, before } from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";
import { ToolError } from "../src/tool-error.ts";

type Actor = { id: string; slug: string; human: boolean; [field: string]: unknown };

// The legacy JavaScript registry is a runtime boundary until its own strict migration.
const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
function object(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === "object" && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
const legacy = object(registry);
const invoke = legacy.executeRegisteredTool;
assert.equal(typeof invoke, "function");
async function executeRegisteredTool(client: pg.Client, identity: Actor, verb: string, args: object) {
  assert.ok(typeof invoke === "function");
  return object(await invoke(client, identity, verb, args));
}
const TOOLS = object(legacy.TOOLS);

import { publicError } from "../src/public-errors.ts";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.mjs";

let fixture: Awaited<ReturnType<typeof acquireDisposablePostgres>>, db: pg.Client, actor: Actor;
before(async () => {
  fixture = await acquireDisposablePostgres({
    prefix: "review-version-",
    snapshot: true,
    pgCtl: undefined,
    sourceTemplate: undefined,
  });
  db = new pg.Client(fixture.connection);
  await db.connect();
  actor = { ...(await db.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
});
after(async () => {
  try {
    await db?.end();
  } finally {
    await fixture?.close();
  }
});

async function seed() {
  const session = randomUUID(),
    candidate = randomUUID();
  await db.query(
    `insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,
    session_token_hash,expires_at,state,state_at,post_call) values($1::uuid,$1::text,'fixture',$2,'meeting',
    now(),now(),$1::text,now()+interval '1 day','done',now(),true)`,
    [session, actor.id],
  );
  await db.query(
    `insert into capture_candidate(id,session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
    values($1::uuid,$2,$1::text,'fixture',0,'next_step','{"action":"Original"}','Synthetic evidence',0.95)`,
    [candidate, session],
  );
  return { session, candidate };
}
async function version(session: string): Promise<string> {
  return (await db.query("select ops.resolve_action_card($1,'post_call_review',$2) target", [actor.id, session]))
    .rows[0].target.source_version;
}
async function review(args: object, client = db, identity = actor) {
  await client.query("begin");
  try {
    await client.query("set local role carr_writer");
    const result = await executeRegisteredTool(client, identity, "review-call-outcome", args);
    await client.query("commit");
    return result;
  } catch (error) {
    await client.query("rollback");
    throw error;
  }
}
async function unchanged(session: string, key: string) {
  assert.equal(
    (await db.query("select outcome_reviewed_at from capture_session where id=$1", [session])).rows[0]
      .outcome_reviewed_at,
    null,
  );
  assert.equal((await db.query("select count(*)::int n from tool_call where idempotency_key=$1", [key])).rows[0].n, 0);
  assert.equal((await db.query("select count(*)::int n from event where idempotency_key=$1", [key])).rows[0].n, 0);
}
const stale = (error: unknown) => {
  assert.ok(error instanceof ToolError);
  assert.equal(error.payload.error, "source_version_changed");
  assert.deepEqual(publicError(error, { env: { CORRELATION_ID: "20000000-0000-4000-8000-000000000001" } }), {
    error: "source_version_changed",
    correlation_id: "20000000-0000-4000-8000-000000000001",
    hint: "This call changed. Reopen the card and review the current outcome.",
  });
  return true;
};

test("changed candidate after card read refuses without session, event or receipt writes", async () => {
  const { session, candidate } = await seed(),
    key = randomUUID();
  const expected_source_version = await version(session);
  await db.query(`update capture_candidate set payload='{"action":"Unseen edit"}' where id=$1`, [candidate]);
  await assert.rejects(review({ session_id: session, expected_source_version, idempotency_key: key }), stale);
  await unchanged(session, key);
  assert.equal(
    (
      await review({
        session_id: session,
        expected_source_version: await version(session),
        idempotency_key: randomUUID(),
      })
    ).reviewed,
    true,
  );
});

for (const recorder of ["quill-joe-mac", "quill-joe-studio"]) {
  test(`${recorder} sessions expose the canonical version through the partner resolver`, async () => {
    const { session, candidate } = await seed();
    await db.query("update capture_session set actor_id=(select id from actor where slug=$2) where id=$1", [
      session,
      recorder,
    ]);
    const expected_source_version = await version(session);
    assert.match(expected_source_version, /^[a-f0-9]{32}$/);
    const calls = (await executeRegisteredTool(db, actor, "read-call-outcomes", {})).calls;
    assert.equal(
      object((Array.isArray(calls) ? calls : []).find((call) => object(call).session_id === session)).source_version,
      expected_source_version,
    );
    for (const slug of ["dell", "joe-local"]) {
      assert.equal(
        (
          await db.query("select ops.resolve_action_card(id,'post_call_review',$1) target from actor where slug=$2", [
            session,
            slug,
          ])
        ).rows[0].target,
        null,
      );
    }
    await db.query(`update capture_candidate set payload='{"action":"Device edit"}' where id=$1`, [candidate]);
    const key = randomUUID();
    await assert.rejects(review({ session_id: session, expected_source_version, idempotency_key: key }), stale);
    await unchanged(session, key);
    assert.equal(
      (
        await review({
          session_id: session,
          expected_source_version: await version(session),
          idempotency_key: randomUUID(),
        })
      ).reviewed,
      true,
    );
  });
}

test("session-only derivation installation is repeatable and limited to the writer role", async () => {
  const migration = await readFile(
    new URL("../../migrations/1168_post_call_capture_recorder_versions.sql", import.meta.url),
    "utf8",
  );
  const { session } = await seed();
  const original = await version(session);
  for (let pass = 0; pass < 2; pass++) {
    await db.query(migration);
    assert.equal(await version(session), original);
  }
  const permissions = (
    await db.query(`select
    has_function_privilege('carr_writer','ops.post_call_review_target(uuid)','execute') writer,
    has_function_privilege('carr_reader','ops.post_call_review_target(uuid)','execute') reader`)
  ).rows[0];
  assert.deepEqual(permissions, { writer: true, reader: false });
  assert.equal(
    (
      await db.query(`select count(*)::int n from jsonb_array_elements(ops.scac_current_grant_contract()->'secdef_execute') entry
    where entry->>'ingress_key'='db-function-acl:ops.post_call_review_target(p_id uuid):carr_writer:execute'`)
    ).rows[0].n,
    1,
  );
});

for (const recorder of ["quill-joe-mac", "joe-local"]) {
  test(`${recorder} revocation preserves historical read version and guarded review`, async () => {
    const { session } = await seed();
    const recorderId = (await db.query("select id from actor where slug=$1", [recorder])).rows[0].id;
    await db.query("update capture_session set actor_id=$2 where id=$1", [session, recorderId]);
    const contract = JSON.parse(
      await readFile(new URL("../contracts/read-call-outcomes.v1.json", import.meta.url), "utf8"),
    );
    async function readVersion() {
      const response = await executeRegisteredTool(db, actor, "read-call-outcomes", {});
      assert.equal(new Validator(contract).validate(response).valid, true);
      assert.ok(Array.isArray(response.calls));
      const value = object(response.calls.find((call) => object(call).session_id === session)).source_version;
      assert.ok(typeof value === "string");
      return value;
    }
    try {
      const seen = await readVersion();
      await db.query("update actor set active=false where id=$1", [recorderId]);
      assert.equal(await readVersion(), seen);
      assert.equal(
        (await db.query("select ops.resolve_action_card($1,'post_call_review',$2) target", [recorderId, session]))
          .rows[0].target,
        null,
      );
      if (recorder === "quill-joe-mac") assert.equal(await version(session), seen);
      assert.equal(
        (await review({ session_id: session, expected_source_version: seen, idempotency_key: randomUUID() })).reviewed,
        true,
      );
      const next = await seed();
      await db.query("update capture_session set actor_id=$2 where id=$1", [next.session, recorderId]);
      const response = await executeRegisteredTool(db, actor, "read-call-outcomes", {});
      assert.ok(Array.isArray(response.calls));
      const old = object(response.calls.find((call) => object(call).session_id === next.session)).source_version;
      await db.query(`update capture_candidate set payload='{"action":"Unseen after revocation"}' where id=$1`, [
        next.candidate,
      ]);
      const key = randomUUID();
      await assert.rejects(
        review({ session_id: next.session, expected_source_version: old, idempotency_key: key }),
        stale,
      );
      await unchanged(next.session, key);
    } finally {
      await db.query("update actor set active=true where id=$1", [recorderId]);
    }
  });
}

test("matching review stores one receipt and replay returns original receipt after source changes", async () => {
  const { session, candidate } = await seed();
  const args = { session_id: session, expected_source_version: await version(session), idempotency_key: randomUUID() };
  const first = await review(args);
  assert.equal(first.reviewed, true);
  await db.query(`update capture_candidate set payload='{"action":"Later edit"}' where id=$1`, [candidate]);
  assert.deepEqual(await review(args), { replayed: true, ...first });
  assert.equal(
    (await db.query("select count(*)::int n from tool_call where idempotency_key=$1", [args.idempotency_key])).rows[0]
      .n,
    1,
  );
  assert.equal(
    (await db.query("select count(*)::int n from event where idempotency_key=$1", [args.idempotency_key])).rows[0].n,
    1,
  );
});

test("proof observations come from the registered writer and SQL readback", async () => {
  const { session, candidate } = await seed();
  const old = await version(session),
    staleKey = randomUUID(),
    matchKey = randomUUID();
  await db.query(`update capture_candidate set payload='{"action":"Proof unseen edit"}' where id=$1`, [candidate]);
  const current = await version(session);
  let refusal: ReturnType<typeof publicError> | undefined;
  try {
    await review({ session_id: session, expected_source_version: old, idempotency_key: staleKey });
  } catch (error) {
    refusal = publicError(error, { env: { CORRELATION_ID: randomUUID() } });
  }
  await unchanged(session, staleKey);
  const before = (await db.query("select outcome_reviewed_at from capture_session where id=$1", [session])).rows[0];
  const args = { session_id: session, expected_source_version: current, idempotency_key: matchKey };
  const receipt = await review(args),
    replay = await review(args);
  const counts = (
    await db.query(
      `select
    (select count(*)::int from event where idempotency_key=$1) events,
    (select count(*)::int from tool_call where idempotency_key=$1) receipts,
    (select count(*)::int from event where idempotency_key=$2) stale_events,
    (select count(*)::int from tool_call where idempotency_key=$2) stale_receipts,
    (select outcome_reviewed_at is not null from capture_session where id=$3) reviewed`,
      [matchKey, staleKey, session],
    )
  ).rows[0];
  const observations = {
    session_id: session,
    old_version: old,
    current_version: current,
    refusal,
    before,
    receipt,
    replay,
    counts,
  };
  const contract = JSON.parse(
    await readFile(new URL("../contracts/review-call-outcome.v1.json", import.meta.url), "utf8"),
  );
  assert.equal(new Validator(contract.refusalSchema).validate(refusal).valid, true);
  for (const response of [receipt, replay])
    assert.equal(new Validator(contract.responseSchema).validate(response).valid, true);
  if (process.env.REVIEW_PROOF_OBSERVATIONS)
    await writeFile(process.env.REVIEW_PROOF_OBSERVATIONS, JSON.stringify(observations));
});

for (const [phase, instant] of [
  ["before", "2026-12-09T05:59:59.999Z"],
  ["at", "2026-12-09T06:00:00.000Z"],
  ["after", "2026-12-09T06:00:00.001Z"],
]) {
  test(`legacy omission ${phase} sunset has bounded acceptance for human and sponsored actors`, async (t) => {
    t.mock.method(Date, "now", () => Date.parse(instant ?? ""));
    const log = t.mock.method(console, "info", () => {});
    for (const identity of [
      actor,
      {
        ...(await db.query("select id,slug from actor where slug='joe-local'")).rows[0],
        human: false,
        native_agent_verified: true,
        sponsoring_human_slug: "joe",
        via: "local-token",
        client_id: "local-verb",
      },
    ]) {
      const { session } = await seed(),
        key = randomUUID();
      const args = { session_id: session, idempotency_key: key };
      if (phase === "before") {
        assert.equal((await review(args, db, identity)).reviewed, true);
        const counts = (
          await db.query(
            `select
          (select count(*)::int from event where idempotency_key=$1) events,
          (select count(*)::int from tool_call where idempotency_key=$1) receipts`,
            [key],
          )
        ).rows[0];
        assert.deepEqual(counts, { events: 1, receipts: 1 });
        const entry = JSON.parse(String(log.mock.calls.at(-1)?.arguments[0]));
        assert.equal(entry.event, "review_call_outcome_legacy_omission");
        assert.equal(entry.actor_kind, identity.human ? "verified_partner" : "sponsored_agent");
      } else {
        await assert.rejects(review(args, db, identity), (error: unknown) => {
          assert.ok(error instanceof ToolError);
          assert.equal(error.payload.error, "missing_required");
          assert.deepEqual(error.payload.missing, ["expected_source_version"]);
          return true;
        });
        await unchanged(session, key);
      }
      const { session: staleSession, candidate } = await seed();
      const seen = await version(staleSession),
        staleKey = randomUUID();
      await db.query(`update capture_candidate set payload='{"action":"Unseen edit"}' where id=$1`, [candidate]);
      await assert.rejects(
        review({ session_id: staleSession, expected_source_version: seen, idempotency_key: staleKey }, db, identity),
        stale,
      );
      await unchanged(staleSession, staleKey);
    }
    assert.equal(log.mock.callCount(), phase === "before" ? 2 : 0);
  });
}

for (const table of ["capture_candidate", "capture_session"]) {
  test(`concurrent ${table} edit committed during review is compared before write`, async () => {
    const { session, candidate } = await seed();
    const args = {
      session_id: session,
      expected_source_version: await version(session),
      idempotency_key: randomUUID(),
    };
    const editor = new pg.Client(fixture.connection),
      writer = new pg.Client(fixture.connection);
    await editor.connect();
    await writer.connect();
    let pending: ReturnType<typeof review> | undefined;
    try {
      await editor.query("begin");
      if (table === "capture_candidate")
        await editor.query(`update capture_candidate set payload='{"action":"Concurrent edit"}' where id=$1`, [
          candidate,
        ]);
      else
        await editor.query("update capture_session set started_at=started_at+interval '1 second' where id=$1", [
          session,
        ]);
      pending = review(args, writer);
      const outcome = pending.then(
        (value) => ({ error: undefined, value }),
        (error: unknown) => ({ error }),
      );
      await waitForLock(editor, writer);
      await editor.query("commit");
      const result = await outcome;
      assert.ok(result.error, "stale review must refuse");
      stale(result.error);
      await unchanged(session, args.idempotency_key);
    } finally {
      await editor.query("rollback");
      await pending?.catch(() => {});
      await editor.end();
      await writer.end();
    }
  });
}

async function seedPostCallCandidate() {
  const { session } = await seed();
  const party = (
    await db.query(
      "insert into party(kind,name,created_by,updated_by) values('person','Synthetic second collection',$1,$1) returning id",
      [actor.id],
    )
  ).rows[0].id;
  await db.query(
    "insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing",
  );
  const client = (
    await db.query(
      "insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id",
      [party, actor.id],
    )
  ).rows[0].id;
  const deal = (
    await db.query(
      "insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic second collection',$1,'lease','site_selection','joe',$2,$2) returning id",
      [client, actor.id],
    )
  ).rows[0].id;
  const candidate = (
    await db.query(
      `insert into capture_post_call_candidate(session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,evidence_quote,confidence)
    values($1,$2,'fixture',0,'assigned_action',$3,'joe','Original second','Synthetic evidence',1) returning id`,
      [session, randomUUID(), deal],
    )
  ).rows[0].id;
  return { session, candidate };
}

test("post-call candidate edit committed during review wait refuses stale review", async () => {
  const { session, candidate } = await seedPostCallCandidate();
  const args = { session_id: session, expected_source_version: await version(session), idempotency_key: randomUUID() };
  const editor = new pg.Client(fixture.connection),
    writer = new pg.Client(fixture.connection);
  await editor.connect();
  await writer.connect();
  let pending: Promise<unknown> | undefined;
  try {
    await editor.query("begin");
    await editor.query("update capture_post_call_candidate set action_description='Unseen second' where id=$1", [
      candidate,
    ]);
    pending = assert.rejects(review(args, writer), stale);
    await waitForLock(editor, writer);
    await editor.query("commit");
    await pending;
    await unchanged(session, args.idempotency_key);
  } finally {
    await editor.query("rollback");
    await pending;
    await editor.end();
    await writer.end();
  }
});

test("post-call candidate deletion waits until review commits", async () => {
  const { session, candidate } = await seedPostCallCandidate();
  const args = { session_id: session, expected_source_version: await version(session), idempotency_key: randomUUID() };
  const writer = new pg.Client(fixture.connection),
    editor = new pg.Client(fixture.connection);
  await writer.connect();
  await editor.connect();
  let pending: Promise<unknown> | undefined;
  try {
    await writer.query("begin");
    await writer.query("set local role carr_writer");
    assert.equal((await executeRegisteredTool(writer, actor, "review-call-outcome", args)).reviewed, true);
    pending = editor.query("delete from capture_post_call_candidate where id=$1", [candidate]);
    await waitForLock(writer, editor);
    await writer.query("commit");
    await pending;
    assert.equal(
      (await db.query("select count(*)::int n from capture_post_call_candidate where id=$1", [candidate])).rows[0].n,
      0,
    );
  } finally {
    await writer.query("rollback");
    await pending;
    await editor.end();
    await writer.end();
  }
});

async function waitForLock(holder: pg.Client, waiter: pg.Client) {
  assert.ok("processID" in holder && typeof holder.processID === "number");
  assert.ok("processID" in waiter && typeof waiter.processID === "number");
  const deadline = performance.now() + 5000;
  while (performance.now() < deadline) {
    if (
      (await db.query("select $1::int=any(pg_blocking_pids($2)) blocked", [holder.processID, waiter.processID])).rows[0]
        .blocked
    )
      return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail("expected PostgreSQL lock wait");
}

test("candidate edits, inserts and same-key retries wait until review commits", async () => {
  const { session, candidate } = await seed();
  const args = { session_id: session, expected_source_version: await version(session), idempotency_key: randomUUID() };
  const connections = await Promise.all(
    Array.from({ length: 4 }, async () => {
      const client = new pg.Client(fixture.connection);
      await client.connect();
      return client;
    }),
  );
  const [writer, editor, inserter, retry] = connections;
  assert.ok(writer && editor && inserter && retry);
  let edits: Promise<unknown> | undefined,
    inserts: Promise<unknown> | undefined,
    replay: ReturnType<typeof review> | undefined;
  try {
    await writer.query("begin");
    await writer.query("set local role carr_writer");
    const first = await executeRegisteredTool(writer, actor, "review-call-outcome", args);
    assert.equal(first.reviewed, true);
    edits = editor.query(`update capture_candidate set payload='{"action":"After review"}' where id=$1`, [candidate]);
    inserts = inserter.query(
      `insert into capture_candidate(session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
      values($1,$2,'fixture',1,'next_step','{}','New evidence',0.95)`,
      [session, randomUUID()],
    );
    replay = review(args, retry);
    await Promise.all([waitForLock(writer, editor), waitForLock(writer, inserter), waitForLock(writer, retry)]);
    await writer.query("commit");
    await Promise.all([edits, inserts]);
    assert.deepEqual(await replay, { replayed: true, ...first });
    assert.equal(
      (await db.query("select payload from capture_candidate where id=$1", [candidate])).rows[0].payload.action,
      "After review",
    );
    assert.equal(
      (await db.query("select count(*)::int n from capture_candidate where session_id=$1", [session])).rows[0].n,
      2,
    );
    assert.equal(
      (await db.query("select count(*)::int n from event where idempotency_key=$1", [args.idempotency_key])).rows[0].n,
      1,
    );
  } finally {
    await writer.query("rollback");
    await Promise.allSettled([edits, inserts, replay]);
    await Promise.all(connections.map((client) => client.end()));
  }
});

test("versioned write contract matches registered schema and public projection is closed", async () => {
  const contract = JSON.parse(
    await readFile(new URL("../contracts/review-call-outcome.v1.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(contract.inputSchema, object(TOOLS["review-call-outcome"]).inputSchema);
  assert.equal(object(TOOLS["review-call-outcome"]).humanOnly, true);
  assert.notEqual(object(TOOLS["review-call-outcome"]).humanCallerOnly, true);
  const { session } = await seed();
  const receipt = await review({
    session_id: session,
    expected_source_version: await version(session),
    idempotency_key: randomUUID(),
  });
  assert.equal(new Validator(contract.responseSchema).validate(receipt).valid, true);
  const projected = publicError(
    new ToolError({ error: "source_version_changed", source_version: "private", hint: "private", session_id: session }),
    { env: { CORRELATION_ID: "20000000-0000-4000-8000-000000000001" } },
  );
  assert.deepEqual(Object.keys(projected).sort(), ["correlation_id", "error", "hint"]);
  assert.equal(new Validator(contract.refusalSchema).validate(projected).valid, true);
});

test("registered read contract exposes the exact version accepted by review and changes after concurrent editing", async () => {
  const contract = JSON.parse(
    await readFile(new URL("../contracts/read-call-outcomes.v1.json", import.meta.url), "utf8"),
  );
  assert.deepEqual(object(TOOLS["read-call-outcomes"]).outputSchema, contract);
  const { session, candidate } = await seed();
  async function readVersion() {
    const response = await executeRegisteredTool(db, actor, "read-call-outcomes", {});
    assert.equal(new Validator(contract).validate(response).valid, true);
    assert.ok(Array.isArray(response.calls));
    const value = object(response.calls.find((call) => object(call).session_id === session)).source_version;
    assert.equal(typeof value, "string");
    return value;
  }
  const seen = await readVersion();
  assert.equal(seen, await version(session));
  const editor = new pg.Client(fixture.connection);
  await editor.connect();
  try {
    await editor.query(`update capture_candidate set payload='{"action":"Concurrent read edit"}' where id=$1`, [
      candidate,
    ]);
  } finally {
    await editor.end();
  }
  const current = await readVersion();
  assert.notEqual(current, seen);
  assert.equal(current, await version(session));
  await assert.rejects(
    review({ session_id: session, expected_source_version: seen, idempotency_key: randomUUID() }),
    stale,
  );
  assert.equal(
    (await review({ session_id: session, expected_source_version: current, idempotency_key: randomUUID() })).reviewed,
    true,
  );
});
