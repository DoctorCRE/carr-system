import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import pg from "pg";
import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";

const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
assert.ok(registry !== null && typeof registry === "object" && "executeRegisteredTool" in registry);
const invoke = registry.executeRegisteredTool;
async function executeRegisteredTool(c: pg.Client, actor: object, verb: string, args: object) {
  assert.equal(typeof invoke, "function");
  assert.ok(typeof invoke === "function");
  const result: unknown = await invoke(c, actor, verb, args);
  assert.ok(result !== null && typeof result === "object" && "activity_id" in result);
  return result;
}

test("future mail stores one exact Message-ID per mailbox and keeps contact links", async (t) => {
  const fixture = await acquireDisposablePostgres({ prefix: "email-message-", snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async () => {
    await c.end();
    await fixture.close();
  });
  const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  await c.query("update actor set email='owner@example.test' where id=$1", [actor.id]);
  const party = (
    await c.query(
      "insert into party(kind,name,created_by,updated_by) values('person','Message fixture',$1,$1) returning id",
      [actor.id],
    )
  ).rows[0].id;
  const ref = party;
  const capture = async (message_id: string | null, item_id: string = randomUUID(), target: string = ref) => {
    await c.query("begin");
    try {
      const result = await executeRegisteredTool(c, actor, "log-activity", {
        idempotency_key: randomUUID(),
        ref: target,
        kind: "email_in",
        summary: "Synthetic email",
        message_id,
        email_identity: { mailbox_owner: "joe", mailbox_account: "owner@example.test", item_id },
      });
      await c.query("commit");
      return result;
    } catch (e) {
      await c.query("rollback");
      throw e;
    }
  };
  const first = await capture(" \t<MiXeD@Example.test>\r\n");
  const retry = await capture("<MiXeD@Example.test>");
  assert.equal(retry.activity_id, first.activity_id);
  assert.deepEqual((await c.query("select message_id,mailbox_account from email")).rows, [
    { message_id: "<MiXeD@Example.test>", mailbox_account: "owner@example.test" },
  ]);
  assert.equal((await c.query("select count(*)::int n from activity where party_id=$1", [party])).rows[0].n, 1);
  await assert.rejects(
    c.query("insert into email(mailbox_account,message_id) values('owner@example.test','<MiXeD@Example.test>')"),
    (e: unknown) => e instanceof Error && "code" in e && e.code === "23505",
  );
  await c.query("insert into email(mailbox_account,message_id) values('other@example.test','<MiXeD@Example.test>')");
  for (const missing of [
    null,
    "",
    "bad",
    "<a b@example.test>",
    "<<a@example.test>>",
    "<a@example.test> junk",
    "bare@example.test",
  ]) {
    const result = await capture(missing);
    assert.equal(
      (
        await c.query("select e.message_id from activity a join email e on e.id=a.email_id where a.id=$1", [
          result.activity_id,
        ])
      ).rows[0].message_id,
      null,
    );
  }
  const otherParty = (
    await c.query(
      "insert into party(kind,name,created_by,updated_by) values('person','Second fixture',$1,$1) returning id",
      [actor.id],
    )
  ).rows[0].id;
  const linked = await capture("<MiXeD@Example.test>", randomUUID(), otherParty);
  assert.notEqual(linked.activity_id, first.activity_id);
  const links = (
    await c.query("select email_id from activity where id=any($1::uuid[])", [[first.activity_id, linked.activity_id]])
  ).rows;
  assert.equal(links.length, 2);
  assert.equal(links[0].email_id, links[1].email_id);
  const missingItem = randomUUID();
  const before = (await c.query("select count(*)::int n from email")).rows[0].n;
  await capture(null, missingItem);
  await capture(null, missingItem);
  assert.equal((await c.query("select count(*)::int n from email")).rows[0].n, before + 1);
  await capture("<mixed@Example.test>");
  assert.equal((await c.query("select count(*)::int n from email where message_id is not null")).rows[0].n, 3);
  assert.equal(
    (await c.query("select count(*)::int n from pg_indexes where indexname='email_mailbox_message_id'")).rows[0].n,
    1,
  );
});
