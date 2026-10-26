// WR-000530 — read-correspondence-thread is bound to the server-derived sponsor
// on the existing read-connection boundary.
//
// Audit (PR #2183): ops.correspondence_thread_readback filters receipts by the
// transaction's carr.sponsoring_human_slug, but the verb ran on the plain
// reader route, which installs no actor context. Every caller therefore read
// nothing: fail-closed, but the partner could never read their own mailbox.
//
// These cases run the REGISTERED verb on the route connectionRouteForTool
// picks for it, as the role that route logs in as, against real rows: Joe and
// Dell each hold a receipt for the SAME native thread in their OWN mailbox.
// Same-mailbox callers see exactly their partner's receipt; a caller from the
// other mailbox and an unsponsored machine see none (a refusal, not an escalation).
//
// The migration class supplies DATABASE_URL and CARR_CORRESPONDENCE_DB_REQUIRED=1.

import test from "node:test";
import assert from "node:assert/strict";

import { TOOLS, executeRegisteredTool } from "../src/tools.js";
import { connectionRouteForTool, setWriterActorContext } from "../src/mcp.js";

const VERB = "read-correspondence-thread";
const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_CORRESPONDENCE_DB_REQUIRED === "1";
const LOOPBACK = /^postgres(?:ql)?:\/\/[^@/]*@(?:127\.0\.0\.1|localhost)[:/]/;

const MAILBOX = {
  joe: "sha256:577047ee6425cc34f2e7a23bb904395c2bfa7b1aba3eae2e23f525812197e3f3",
  dell: "sha256:6632cb6fcdf5e605e667c31251acce51db90e088038855d48d7d523ff84f1834",
};
const THREAD = { source_system: "fixture-mail", native_id: "wr530-thread", native_id_epoch: 0 };

const sponsored = (slug, sponsor) => ({ slug, human: false, agent: true, via: "local-token",
  client_id: null, sponsoring_human_slug: sponsor, human_slug: sponsor, sponsor_required: false,
  native_agent_verified: true });
const IDENTITIES = [
  // [label, actor, partner whose mailbox it may read, or null]
  ["Joe, the human partner", { slug: "joe", human: true, via: "oauth-google" }, "joe"],
  ["Dell, the human partner", { slug: "dell", human: true, via: "oauth-google" }, "dell"],
  ["joe-local, verified-sponsored by Joe", sponsored("joe-local", "joe"), "joe"],
  ["dell-local, verified-sponsored by Dell", sponsored("dell-local", "dell"), "dell"],
  ["codex, an unsponsored machine", { slug: "codex", human: false, agent: true, via: "agent-token",
    client_id: null }, null],
];

test("the thread read declares the actor-scoped read-only writer route", () => {
  assert.equal(TOOLS[VERB].write, false);
  assert.equal(connectionRouteForTool(TOOLS[VERB]), "writer_read_only");
});

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.match(DSN, LOOPBACK, "REFUSED: this proof writes fixture receipts on a disposable loopback only");
  const pg = (await import("pg")).default;
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

/** One consent and one receipt per partner, for the same native thread, as the owner. */
async function seed(client) {
  for (const [partner, digest] of Object.entries(MAILBOX)) {
    await client.query(
      `select set_config('carr.acting_actor_slug',$1,true), set_config('carr.verified_human_actor_slug',$1,true),
              set_config('carr.sponsoring_human_slug',$1,true)`, [partner]);
    const consent = (await client.query(
      `select ops.correspondence_record_adapter_consent($1,'v5_f10_partner_mail_calendar_adapter',$2,
         array['read_mail_message_metadata'],'synthetic WR-000530 quote',gen_random_uuid()) as id`,
      [partner, digest])).rows[0].id;
    await client.query(
      `select ops.correspondence_record_read_receipt($1,$2,$3,$4,
         jsonb_build_object('relevance_state','related','correspondence_state','awaiting_reply'),gen_random_uuid())`,
      [consent, THREAD.source_system, THREAD.native_id, THREAD.native_id_epoch]);
  }
}

/** Open the registered verb's own route, as that route's role, for one actor. */
async function asRoute(client, actor) {
  await client.query(
    `select set_config('carr.acting_actor_slug','',true), set_config('carr.verified_human_actor_slug','',true),
            set_config('carr.sponsoring_human_slug','',true)`);
  const route = connectionRouteForTool(TOOLS[VERB]);
  if (route === "reader") {
    await client.query("set local role carr_reader");
  } else {
    assert.equal(route, "writer_read_only");
    await client.query("set local role carr_writer");
    await setWriterActorContext(client, actor);
  }
}

for (const [label, actor, mailbox] of IDENTITIES) {
  test(`real PostgreSQL: ${label} reads ${mailbox ? `only ${mailbox}'s mailbox` : "no mailbox"}`, async t => {
    const client = await database(t);
    if (!client) return;
    try {
      await client.query("begin");
      await seed(client);
      await client.query("savepoint routed");
      await asRoute(client, { ...actor });
      const answer = await executeRegisteredTool(client, { ...actor }, VERB, { ...THREAD });
      await client.query("rollback to savepoint routed");
      if (mailbox === null) {
        assert.equal(answer.decision, "unavailable", "an unsponsored machine was handed a mailbox");
        assert.deepEqual(answer.receipts, []);
        return;
      }
      assert.equal(answer.decision, "receipts_found", `${label} could not read its own mailbox`);
      assert.equal(answer.receipts.length, 1, "a receipt from the other mailbox escaped");
      assert.equal(answer.receipts[0].partner_slug, mailbox);
      assert.equal(answer.receipts[0].account_digest, MAILBOX[mailbox]);
    } finally {
      await client.query("rollback").catch(() => {});
      await client.end();
    }
  });
}
