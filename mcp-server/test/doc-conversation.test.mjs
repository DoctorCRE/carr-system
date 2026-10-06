// WR-000112 — the Doc conversation acceptance proofs.
//
// The verb-surface cases are pure. The store cases need a real PostgreSQL and
// skip in the unit class; the migration class supplies DATABASE_URL and sets
// CARR_DOC_CONVERSATION_DB_REQUIRED=1, which turns a silent skip into a failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { docConversationTools, docConversationProjection, docConversationListProjection } from "../src/doc-conversation.js";
import { TOOLS } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_DOC_CONVERSATION_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

/**
 * A replay cache with the ONE property this proof leans on: the same
 * idempotency key returns the recorded answer and does not run the body twice.
 * Every assertion that matters counts stored ROWS, never this cache.
 */
function harness() {
  const envelopes = new Map();
  const events = [];
  return {
    events,
    withEnvelope: async (c, actor, verb, args, fn) => {
      const key = `${verb}:${args.idempotency_key}`;
      if (envelopes.has(key)) return envelopes.get(key);
      const value = await fn();
      envelopes.set(key, value);
      return value;
    },
    writeEvent: async (c, actor, verb, subjectType, subjectId, fields) => {
      events.push({ verb, subjectType, subjectId, fields });
    },
  };
}

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes conversation rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg, slug) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  if (slug) {
    await client.query("select set_config('carr.acting_actor_slug',$1,false)", [slug]);
    await client.query("select set_config('carr.verified_human_actor_slug',$1,false)", [slug]);
  }
  return client;
}

/** Two automation actors, minted here so the proof owns its own fixtures. */
async function actors(client) {
  const rows = [];
  for (const slug of ["wr112-author", "wr112-guest"]) {
    const r = await client.query(
      `insert into public.actor(slug, kind, display_name, active) values ($1,'automation',$1,true)
         on conflict (slug) do update set active = true returning id, slug`, [slug]);
    rows.push(r.rows[0]);
  }
  return { author: rows[0], guest: rows[1] };
}

async function newConversation(client, ownerId, title = "WR-000112 proof") {
  const r = await client.query(
    "insert into ops.doc_conversation(title, created_by_actor) values ($1,$2) returning id, version",
    [title, ownerId]);
  return r.rows[0];
}

// ---------------------------------------------------------------------------
// The verb surface.
// ---------------------------------------------------------------------------

test("DOC-VERB-REACHABLE: both verbs are registered, and neither widens an unattended profile", () => {
  assert.ok(TOOLS["add-doc-conversation-turn"], "add-doc-conversation-turn is not registered");
  assert.ok(TOOLS["read-doc-conversation"], "read-doc-conversation is not registered");
  assert.equal(TOOLS["add-doc-conversation-turn"].write, true);
  assert.equal(TOOLS["add-doc-conversation-turn"].authorityOnly, true,
    "the append function is granted to carr_authority, so the verb must declare authorityOnly");
  assert.equal(TOOLS["read-doc-conversation"].write, undefined);
  assert.equal(TOOLS["read-doc-conversation"].writerConnection, true,
    "ops.doc_conversation_facts is granted to carr_writer, and only the writer path installs the actor context");
  // Negatively asserted: widening an unattended profile is a separate ruling.
  for (const profile of ["capture", "away"]) {
    for (const name of ["add-doc-conversation-turn", "read-doc-conversation"]) {
      assert.ok(!PROFILES[profile].has(name),
        `${name} must not be in the ${profile} profile`);
    }
  }
});

test("DOC-ATTRIBUTION-SERVER-SIDE: the schema refuses a caller-named origin", () => {
  const schema = TOOLS["add-doc-conversation-turn"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  for (const forbidden of ["origin_actor", "origin_channel", "sequence", "at"]) {
    assert.ok(!Object.hasOwn(schema.properties, forbidden),
      `${forbidden} is nameable by a caller`);
  }
  assert.deepEqual(Object.keys(schema.properties).sort(),
    ["body", "conversation_id", "idempotency_key", "msg_id", "role"]);
});

test("the read shaper refuses anything that is not the definer function's own shape", () => {
  assert.throws(() => docConversationProjection(null, ToolError), /doc_conversation_not_found/);
  assert.throws(() => docConversationProjection({ ok: false }, ToolError), /doc_conversation_not_found/);
});

// ---------------------------------------------------------------------------
// The store.
// ---------------------------------------------------------------------------

test("DOC-TURN-READBACK: three turns read back in sequence order, with honest paging", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const conversation = await newConversation(client, author.id);
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

  const bodies = ["first turn", "second turn", "third turn"];
  for (const body of bodies) {
    await verbs["add-doc-conversation-turn"].handler(c, { id: author.id, slug: "joe" }, {
      idempotency_key: randomUUID(), conversation_id: conversation.id, role: "human", body });
  }

  const read = await verbs["read-doc-conversation"].handler(c, { id: author.id }, {
    conversation_id: conversation.id });
  assert.deepEqual(read.turns.map(turn => turn.body), bodies, "bodies are verbatim and in order");
  assert.deepEqual(read.turns.map(turn => turn.sequence), [0, 1, 2]);
  assert.equal(read.latest_sequence, 2);
  assert.equal(read.more, false);

  const tail = await verbs["read-doc-conversation"].handler(c, { id: author.id }, {
    conversation_id: conversation.id, after_sequence: 2 });
  assert.deepEqual(tail.turns.map(turn => turn.body), ["third turn"]);
  assert.equal(tail.more, false);
  const page = await verbs["read-doc-conversation"].handler(c, { id: author.id }, {
    conversation_id: conversation.id, limit: 2 });
  assert.equal(page.turns.length, 2);
  assert.equal(page.more, true, "more is honest when a page was cut short");
});

test("DOC-IDEMPOTENT: three counted facts, and the count is the assertion", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const conversation = await newConversation(client, author.id);
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });
  const actor = { id: author.id, slug: "joe" };
  const count = async () => Number((await client.query(
    "select count(*) from ops.doc_conversation_turn where conversation_id=$1",
    [conversation.id])).rows[0].count);

  // (a) the SAME request twice under ONE idempotency key.
  const key = randomUUID();
  const msgId = randomUUID();
  const args = { idempotency_key: key, conversation_id: conversation.id, role: "human",
    body: "one and only", msg_id: msgId };
  await verbs["add-doc-conversation-turn"].handler(c, actor, args);
  await verbs["add-doc-conversation-turn"].handler(c, actor, args);
  assert.equal(await count(), 1, "one key, one stored turn");

  // (b) the SAME msg_id under a FRESH key -- a genuinely separate transport, so
  //     the envelope cannot be what absorbs it.
  const again = await verbs["add-doc-conversation-turn"].handler(c, actor,
    { ...args, idempotency_key: randomUUID() });
  assert.equal(again.deduplicated, true);
  assert.equal(await count(), 1, "a fresh key over the same msg_id still stores one turn");
  const latest = await verbs["read-doc-conversation"].handler(c, { id: author.id },
    { conversation_id: conversation.id });
  assert.equal(latest.latest_sequence, 0, "latest_sequence did not move");

  // (c) that msg_id with a DIFFERENT body is a REUSE, not a duplicate.
  await assert.rejects(
    verbs["add-doc-conversation-turn"].handler(c, actor,
      { ...args, idempotency_key: randomUUID(), body: "a different body" }),
    error => error.error === "doc_conversation_msg_id_reuse");
  assert.equal(await count(), 1);
});

test("DOC-ATTRIBUTION-SERVER-SIDE: origin_actor is the session's derived actor, not any argument",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "joe");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author } = await actors(client);
    const conversation = await newConversation(client, author.id);
    const { withEnvelope, writeEvent } = harness();
    const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

    // The definer function called DIRECTLY, with no actor argument to give it.
    const direct = (await client.query(
      "select ops.append_doc_conversation_turn($1::uuid,'system','direct call',$2::uuid,$3::uuid) as appended",
      [conversation.id, randomUUID(), randomUUID()])).rows[0].appended;
    assert.equal(direct.ok, true);
    assert.equal(direct.origin_actor, "joe",
      "origin_actor is derived from the session, and there is no parameter for it");
    assert.equal(direct.origin_channel, "mcp");

    // And through the verb, whose schema cannot even name one.
    await verbs["add-doc-conversation-turn"].handler(c, { id: author.id, slug: "joe" }, {
      idempotency_key: randomUUID(), conversation_id: conversation.id,
      role: "assistant", body: "through the verb" });
    const rows = await client.query(
      "select distinct origin_actor, origin_channel from ops.doc_conversation_turn where conversation_id=$1",
      [conversation.id]);
    assert.deepEqual(rows.rows, [{ origin_actor: "joe", origin_channel: "mcp" }]);
  });

test("DOC-PRIVATE-INVISIBLE: a non-member gets not_found AND the count does not include it",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "joe");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author, guest } = await actors(client);
    const conversation = await newConversation(client, author.id, "private to the author");
    const { withEnvelope, writeEvent } = harness();
    const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

    const ownerView = await verbs["read-doc-conversation"].handler(c, { id: author.id },
      { conversation_id: conversation.id });
    assert.equal(ownerView.identity.visibility, "private");
    const ownerCount = ownerView.visible_conversation_count;

    await assert.rejects(
      verbs["read-doc-conversation"].handler(c, { id: guest.id }, { conversation_id: conversation.id }),
      error => error.error === "doc_conversation_not_found");

    // The COUNT, not only the list: an unauthorized caller must not learn a
    // private conversation exists from a number.
    const guestFacts = (await client.query(
      "select ops.doc_conversation_facts($1::uuid,$2::text,0,200) as facts",
      [conversation.id, guest.id])).rows[0].facts;
    assert.equal(guestFacts.ok, false);
    assert.equal(guestFacts.reason_id, "doc_conversation_not_found");
    assert.equal(guestFacts.visible_conversation_count, undefined,
      "a refusal returns no count at all");
    assert.ok(ownerCount >= 1);
  });

test("DOC-RENAME-RETAINS: the title moves and every turn is byte-identical", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const conversation = await newConversation(client, author.id, "before the rename");
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });
  for (const body of ["turn one", "turn two"]) {
    await verbs["add-doc-conversation-turn"].handler(c, { id: author.id, slug: "joe" },
      { idempotency_key: randomUUID(), conversation_id: conversation.id, role: "human", body });
  }
  const before = (await client.query(
    "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
    [conversation.id])).rows;

  await client.query(
    `insert into ops.doc_conversation_title_revision(conversation_id, title, by_actor)
       select id, title, created_by_actor from ops.doc_conversation where id = $1`, [conversation.id]);
  await client.query(
    "update ops.doc_conversation set title = $2, version = version + 1, updated_at = now() where id = $1",
    [conversation.id, "after the rename"]);

  const after = await verbs["read-doc-conversation"].handler(c, { id: author.id },
    { conversation_id: conversation.id });
  assert.equal(after.identity.title, "after the rename");
  assert.equal(after.identity.version, conversation.version + 1);
  const revisions = await client.query(
    "select title from ops.doc_conversation_title_revision where conversation_id=$1", [conversation.id]);
  assert.deepEqual(revisions.rows.map(row => row.title), ["before the rename"]);
  const now = (await client.query(
    "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
    [conversation.id])).rows;
  assert.deepEqual(now, before, "every turn's sequence, msg_id and body is byte-identical");
});

test("DOC-PRIVATE-INVISIBLE: a granted actor sees it, and a REVOKED grant takes it away", async t => {
  const pg = await skipUnlessDatabase(t);
  if (!pg) return;
  const client = await connect(pg, "joe");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author, guest } = await actors(client);
  const conversation = await newConversation(client, author.id, "shared then revoked");
  const { withEnvelope, writeEvent } = harness();
  const verbs = docConversationTools({ withEnvelope, writeEvent, ToolError });

  await client.query(
    `insert into ops.doc_conversation_grant(conversation_id, grantee_actor, granted_by_actor)
       values ($1,$2,$3)`, [conversation.id, guest.id, author.id]);
  const granted = await verbs["read-doc-conversation"].handler(c, { id: guest.id },
    { conversation_id: conversation.id });
  assert.equal(granted.identity.id, conversation.id);
  assert.equal(granted.effective_grants.length, 1);

  await client.query(
    "update ops.doc_conversation_grant set revoked_at = now() where conversation_id=$1 and grantee_actor=$2",
    [conversation.id, guest.id]);
  await assert.rejects(
    verbs["read-doc-conversation"].handler(c, { id: guest.id }, { conversation_id: conversation.id }),
    error => error.error === "doc_conversation_not_found");
});

// ---------------------------------------------------------------------------
// WR-000114 — the three write doors.
// ---------------------------------------------------------------------------

const WRITE_DOORS = ["create-doc-conversation", "share-doc-conversation",
  "rename-doc-conversation"];

test("DOC-APP-CALLABLE: none of the three is authorityOnly, and each is a write on the writer connection",
  () => {
    for (const name of WRITE_DOORS) {
      assert.ok(TOOLS[name], `${name} is not registered`);
      assert.equal(TOOLS[name].write, true, `${name} must declare write`);
      assert.equal(TOOLS[name].writerConnection, true,
        `${name} reaches its function on carr_writer, which is the writer connection`);
      // mcp.js:616-618 refuses an authority-only call before any grant is
      // consulted, and the app holds no authority binding. Copying the
      // append's authorityOnly would fail HERE rather than at release time.
      assert.equal(TOOLS[name].authorityOnly, undefined,
        `${name} must not be authorityOnly: the app calls it as the signed-in partner`);
    }
    // Widening an unattended profile is a separate ruling.
    for (const profile of ["capture", "away"]) {
      for (const name of WRITE_DOORS) {
        assert.ok(!PROFILES[profile].has(name), `${name} must not be in the ${profile} profile`);
      }
    }
  });

test("DOC-ATTRIBUTION-SERVER-SIDE: no write-door schema can name an actor, a grantor or a moment",
  () => {
    for (const name of WRITE_DOORS) {
      const schema = TOOLS[name].inputSchema;
      assert.equal(schema.additionalProperties, false, `${name} accepts undeclared arguments`);
      for (const forbidden of ["created_by", "creator", "granted_by", "granted_by_actor",
        "grantee_actor", "actor", "acting_actor", "acting_actor_slug", "at", "granted_at",
        "created_at", "updated_at", "version"]) {
        assert.ok(!Object.hasOwn(schema.properties, forbidden),
          `${name} lets a caller name ${forbidden}`);
      }
    }
    // base_version is the caller's OWN read-back version, which is the one
    // version-shaped argument a compare-and-swap requires.
    assert.deepEqual(Object.keys(TOOLS["create-doc-conversation"].inputSchema.properties).sort(),
      ["idempotency_key", "title", "visibility"]);
    assert.deepEqual(Object.keys(TOOLS["share-doc-conversation"].inputSchema.properties).sort(),
      ["conversation_id", "granted", "grantee_slug", "idempotency_key"]);
    assert.deepEqual(Object.keys(TOOLS["rename-doc-conversation"].inputSchema.properties).sort(),
      ["archived", "base_version", "conversation_id", "idempotency_key", "pinned", "title"]);
  });

test("DOC-CREATE-READBACK: a created conversation reads back byte-identical, and a replayed key is one row",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "wr112-author");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author } = await actors(client);
    const verbs = docConversationTools({ ...harness(), ToolError });
    const actor = { id: author.id, slug: "wr112-author" };

    // Punctuation and a non-ASCII character, so a shaper that normalised or
    // trimmed would be caught by an EQUALITY assertion rather than a substring.
    const title = "Q4 review — Dr. Reyes' suite, 1,200 sq ft";
    const key = randomUUID();
    const created = await verbs["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title, visibility: "private" });
    assert.equal(created.ok, true);
    assert.equal(created.deduplicated, false);
    assert.equal(created.conversation_id, key, "the row id IS the idempotency key");

    const read = await verbs["read-doc-conversation"].handler(c, actor,
      { conversation_id: key });
    assert.equal(read.identity.title, title, "the title reads back byte-identical");
    assert.equal(read.identity.visibility, "private");
    assert.equal(read.identity.created_by, author.id,
      "the creator is the derived actor, not anything a caller could name");

    // THE ENVELOPE IS DELIBERATELY BYPASSED: a FRESH harness, so the store path
    // really runs a second time. An implementation that minted a fresh uuid per
    // call and leaned on the envelope for idempotency stores a SECOND row here.
    const fresh = docConversationTools({ ...harness(), ToolError });
    const replay = await fresh["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title, visibility: "private" });
    assert.equal(replay.deduplicated, true, "the replay reached the store and deduplicated there");
    assert.equal(replay.conversation_id, key, "the replay returned the same identifier");
    const rows = Number((await client.query(
      "select count(*) from ops.doc_conversation where id = $1", [key])).rows[0].count);
    assert.equal(rows, 1, "one replayed key, one STORED row");
  });

test("DOC-SHARE-TOGGLE: a grant appears in the grantee's read AND count, a revoke removes it from both, and a non-creator cannot do either",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "wr112-author");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author, guest } = await actors(client);
    const verbs = docConversationTools({ ...harness(), ToolError });
    const actor = { id: author.id, slug: "wr112-author" };
    const key = randomUUID();
    await verbs["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title: "shared then withdrawn" });

    // The grantee's OWN conversation, so the guest always has one to count
    // against -- the count assertion must not depend on the shared row alone.
    const guestOwn = randomUUID();
    await client.query(
      "insert into ops.doc_conversation(id, title, created_by_actor) values ($1,$2,$3)",
      [guestOwn, "the guest's own", guest.id]);
    const guestCount = async () => (await verbs["read-doc-conversation"].handler(
      c, { id: guest.id }, { conversation_id: guestOwn })).visible_conversation_count;
    const before = await guestCount();

    await verbs["share-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        grantee_slug: "wr112-guest", granted: true });
    const granted = await verbs["read-doc-conversation"].handler(c, { id: guest.id },
      { conversation_id: key });
    assert.equal(granted.identity.id, key, "the grantee's read returns it");
    assert.equal(await guestCount(), before + 1,
      "the COUNT rose by exactly one -- a count computed outside the access-list join would not");

    await verbs["share-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        grantee_slug: "wr112-guest", granted: false });
    await assert.rejects(
      verbs["read-doc-conversation"].handler(c, { id: guest.id }, { conversation_id: key }),
      error => error.error === "doc_conversation_not_found");
    assert.equal(await guestCount(), before, "and the count dropped back");

    // THE ROW SURVIVES THE REVOKE, stamped rather than deleted, with its
    // grantor and its moment intact. Deletion could not assert this at all.
    const stamped = (await client.query(
      `select granted_by_actor, granted_at, revoked_at from ops.doc_conversation_grant
        where conversation_id = $1 and grantee_actor = $2`, [key, guest.id])).rows;
    assert.equal(stamped.length, 1, "the revoke stamped the row rather than deleting it");
    assert.equal(stamped[0].granted_by_actor, author.id, "the grantor survives for the audit");
    assert.ok(stamped[0].revoked_at instanceof Date, "and the withdrawal is stamped");

    // A NON-CREATOR is refused, for the grant and for the withdrawal alike.
    const asGuest = await connect(pg, "wr112-guest");
    t.after(() => asGuest.end().catch(() => {}));
    const g = { query: (text, values = []) => asGuest.query(text, values) };
    await verbs["share-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        grantee_slug: "wr112-guest", granted: true });
    for (const granting of [true, false]) {
      await assert.rejects(
        verbs["share-doc-conversation"].handler(g, { id: guest.id, slug: "wr112-guest" },
          { idempotency_key: randomUUID(), conversation_id: key,
            grantee_slug: "wr112-guest", granted: granting }),
        error => error.error === "doc_conversation_creator_only",
        `a non-creator ${granting ? "grant" : "revoke"} must be refused by name`);
    }

    // A GRANT WIDENS NOTHING BUT THIS CONVERSATION, asserted NEGATIVELY against
    // the functions' own statements: there is no join from the grant table to
    // any source record. public.actor is the one non-conversation relation
    // either function names, and it is a slug lookup, not a record.
    const bodies = (await client.query(
      `select pg_get_functiondef(p.oid) body from pg_proc p join pg_namespace n
         on n.oid = p.pronamespace where n.nspname='ops'
        and p.proname in ('share_doc_conversation','rename_doc_conversation')`)).rows;
    assert.equal(bodies.length, 2);
    for (const { body } of bodies) {
      // The CREATE header names the function itself; the BODY is what reaches
      // relations, so the header line is dropped before the scan.
      const statements = body.slice(body.indexOf("$function$"));
      for (const match of statements.matchAll(/\bops\.([a-z_]+)/g)) {
        assert.ok(["doc_conversation", "doc_conversation_grant", "doc_conversation_turn",
          "doc_conversation_title_revision", "portfolio_writer_actor_id"].includes(match[1]),
          `a write door reaches ops.${match[1]}, outside the Doc conversation store`);
      }
      for (const match of statements.matchAll(/\bpublic\.([a-z_]+)/g)) {
        assert.equal(match[1], "actor",
          `a write door reaches public.${match[1]}; only the actor slug lookup is allowed`);
      }
    }
  });

test("DOC-RENAME-RETAINS: the title moves, the prior title is retained, every turn is byte-identical, and a stale version is refused",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, "wr112-author");
    t.after(() => client.end().catch(() => {}));
    const c = { query: (text, values = []) => client.query(text, values) };
    const { author } = await actors(client);
    const verbs = docConversationTools({ ...harness(), ToolError });
    const actor = { id: author.id, slug: "wr112-author" };
    const key = randomUUID();
    const created = await verbs["create-doc-conversation"].handler(c, actor,
      { idempotency_key: key, title: "before the rename" });

    for (const body of ["turn one", "turn two"]) {
      await client.query(
        `insert into ops.doc_conversation_turn(conversation_id, sequence, role, body, msg_id, origin_actor)
         select $1, coalesce((select max(sequence)+1 from ops.doc_conversation_turn where conversation_id=$1),0),
                'human', $2, $3, 'wr112-author'`, [key, body, randomUUID()]);
    }
    const before = (await client.query(
      "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
      [key])).rows;

    const renamed = await verbs["rename-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key,
        base_version: created.version, title: "after the rename" });
    assert.equal(renamed.title, "after the rename");
    assert.equal(renamed.version, created.version + 1, "the version rose by exactly one");

    const revisions = (await client.query(
      "select title from ops.doc_conversation_title_revision where conversation_id=$1", [key])).rows;
    assert.deepEqual(revisions.map(row => row.title), ["before the rename"],
      "exactly one revision, carrying the PRIOR title");
    const after = (await client.query(
      "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
      [key])).rows;
    assert.deepEqual(after, before, "every turn's order, identifier and body is byte-identical");

    // THE NOW-STALE base version. The refusal must append NOTHING: the revision
    // table is immutable, so an orphan could never be cleaned up.
    await assert.rejects(
      verbs["rename-doc-conversation"].handler(c, actor,
        { idempotency_key: randomUUID(), conversation_id: key,
          base_version: created.version, title: "a third title" }),
      error => error.error === "version_conflict");
    const stillOne = (await client.query(
      "select count(*) from ops.doc_conversation_title_revision where conversation_id=$1",
      [key])).rows[0].count;
    assert.equal(Number(stillOne), 1,
      "a REFUSED rename appended no revision -- the step-2 version guard holds");
  });

// ---------------------------------------------------------------------------
// WR-000114 review B1: pin, unpin, archive and unarchive, each under its OWN
// named case. The rename case above no longer carries them even by implication
// -- a flag that moved a field it does not own has to go red HERE, in the case
// that names the act, and nowhere else.
//
// Each case asserts four things: the intended flag moved, the version rose by
// exactly one, the OTHER flag and the title and every turn are byte-identical,
// and a stale base version is refused without moving anything.
// ---------------------------------------------------------------------------

/** A conversation owned by wr112-author, carrying two turns, ready to flag. */
async function flagFixture(t, pg, title = "the flag fixture") {
  const client = await connect(pg, "wr112-author");
  t.after(() => client.end().catch(() => {}));
  const c = { query: (text, values = []) => client.query(text, values) };
  const { author } = await actors(client);
  const verbs = docConversationTools({ ...harness(), ToolError });
  const actor = { id: author.id, slug: "wr112-author" };
  const key = randomUUID();
  const created = await verbs["create-doc-conversation"].handler(c, actor,
    { idempotency_key: key, title });
  for (const body of ["turn one", "turn two"]) {
    await client.query(
      `insert into ops.doc_conversation_turn(conversation_id, sequence, role, body, msg_id, origin_actor)
       select $1, coalesce((select max(sequence)+1 from ops.doc_conversation_turn where conversation_id=$1),0),
              'human', $2, $3, 'wr112-author'`, [key, body, randomUUID()]);
  }
  return { client, c, verbs, actor, key, version: created.version, title };
}

/** The stamps and the title, read straight out of the row. */
async function flagRow(client, key) {
  return (await client.query(
    "select title, version, pinned_at, archived_at from ops.doc_conversation where id=$1",
    [key])).rows[0];
}

async function turnsOf(client, key) {
  return (await client.query(
    "select sequence, msg_id, body from ops.doc_conversation_turn where conversation_id=$1 order by sequence",
    [key])).rows;
}

/** Set one flag as a setup step, returning the new version. */
async function setFlag(fixture, version, args) {
  const result = await fixture.verbs["rename-doc-conversation"].handler(fixture.c, fixture.actor,
    { idempotency_key: randomUUID(), conversation_id: fixture.key, base_version: version, ...args });
  assert.equal(result.ok ?? true, true);
  return result.version;
}

/**
 * The shared body of all four cases. `act` is the flag write under test, and
 * `expect` is what must be true of the row afterwards; everything NOT named in
 * `act` is compared against the row as it stood before the act.
 */
async function provesOneFlag(t, pg, name, { setup = [], act, expect }) {
  const fixture = await flagFixture(t, pg, `${name} fixture`);
  const { client, c, verbs, actor, key } = fixture;
  let version = fixture.version;
  for (const step of setup) version = await setFlag(fixture, version, step);

  const before = await flagRow(client, key);
  const turnsBefore = await turnsOf(client, key);
  assert.equal(before.version, version);

  const moved = await verbs["rename-doc-conversation"].handler(c, actor,
    { idempotency_key: randomUUID(), conversation_id: key, base_version: version, ...act });
  const after = await flagRow(client, key);

  // 1. The intended flag moved, in the direction asked for.
  assert.equal(moved.pinned, expect.pinned, `${name}: the pinned flag is wrong`);
  assert.equal(moved.archived, expect.archived, `${name}: the archived flag is wrong`);
  assert.equal(after.pinned_at !== null, expect.pinned, `${name}: pinned_at disagrees with the answer`);
  assert.equal(after.archived_at !== null, expect.archived, `${name}: archived_at disagrees with the answer`);
  assert.equal(expect.moves === "pinned_at"
    ? after.pinned_at?.getTime() !== before.pinned_at?.getTime()
    : after.archived_at?.getTime() !== before.archived_at?.getTime(),
    true, `${name}: ${expect.moves} did not move at all`);

  // 2. The version rose by exactly one.
  assert.equal(moved.version, before.version + 1, `${name}: the version did not rise by exactly one`);
  assert.equal(after.version, before.version + 1);

  // 3. The OTHER flag, the title and every turn are byte-identical.
  const other = expect.moves === "pinned_at" ? "archived_at" : "pinned_at";
  assert.deepEqual(after[other], before[other],
    `${name}: ${other} moved, and this act does not own it`);
  assert.equal(after.title, before.title, `${name}: the title moved`);
  assert.equal(moved.title, before.title);
  assert.deepEqual(await turnsOf(client, key), turnsBefore, `${name}: a turn moved`);
  assert.equal(Number((await client.query(
    "select count(*) from ops.doc_conversation_title_revision where conversation_id=$1",
    [key])).rows[0].count), 0, `${name}: a flag write appended a title revision`);

  // 4. A stale base version is refused, and moves nothing.
  await assert.rejects(
    verbs["rename-doc-conversation"].handler(c, actor,
      { idempotency_key: randomUUID(), conversation_id: key, base_version: before.version, ...act }),
    error => error.error === "version_conflict",
    `${name}: a stale base version was not refused`);
  assert.deepEqual(await flagRow(client, key), after,
    `${name}: a REFUSED flag write moved the row`);
}

test("DOC-PIN-ONLY: a pin sets pinned_at, raises the version by one, and moves neither the archive flag nor the title",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await provesOneFlag(t, pg, "DOC-PIN-ONLY",
      { act: { pinned: true }, expect: { pinned: true, archived: false, moves: "pinned_at" } });
  });

test("DOC-UNPIN-ONLY: an unpin clears pinned_at and leaves an ARCHIVED conversation archived",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    // Archived first, so an unpin that reached archived_at instead of its own
    // field could not hide behind a null.
    await provesOneFlag(t, pg, "DOC-UNPIN-ONLY",
      { setup: [{ pinned: true }, { archived: true }],
        act: { pinned: false }, expect: { pinned: false, archived: true, moves: "pinned_at" } });
  });

test("DOC-ARCHIVE-ONLY: an archive sets archived_at, raises the version by one, and moves neither the pin flag nor the title",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await provesOneFlag(t, pg, "DOC-ARCHIVE-ONLY",
      { act: { archived: true }, expect: { pinned: false, archived: true, moves: "archived_at" } });
  });

test("DOC-UNARCHIVE-ONLY: an unarchive clears archived_at and leaves a PINNED conversation pinned",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    await provesOneFlag(t, pg, "DOC-UNARCHIVE-ONLY",
      { setup: [{ pinned: true }, { archived: true }],
        act: { archived: false }, expect: { pinned: true, archived: false, moves: "archived_at" } });
  });

// ---------------------------------------------------------------------------
// WR-000115 — the LIST door.
//
// Every database case below mints its OWN actor pair, so the set a list
// returns is exactly what that case created and an exact-set assertion is
// meaningful. Sharing wr112-author with the cases above would make every
// deepEqual on a whole set a hostage to test ordering.
// ---------------------------------------------------------------------------

// ONE RUN-UNIQUE TOKEN. Each case owns its whole visible set, and a second run
// against the SAME cluster owns a different one -- so an exact-set assertion is
// a statement about this run rather than about how many times the suite has
// been run against this database.
const RUN = randomUUID().slice(0, 8);
const listSlug = (tag, role) => `wr115-${RUN}-${tag}-${role}`;

/** One actor pair per case, so each case owns its whole visible set. */
async function listActors(client, tag) {
  const rows = [];
  for (const role of ["author", "guest"]) {
    const slug = listSlug(tag, role);
    const r = await client.query(
      `insert into public.actor(slug, kind, display_name, active) values ($1,'automation',$1,true)
         on conflict (slug) do update set active = true returning id, slug`, [slug]);
    rows.push(r.rows[0]);
  }
  return { author: rows[0], guest: rows[1] };
}

/** A verb-callable connection bound to one acting-actor context. */
async function listSeat(pg, t, slug) {
  const client = await connect(pg, slug);
  t.after(() => client.end().catch(() => {}));
  return {
    client,
    c: { query: (text, values = []) => client.query(text, values) },
    verbs: docConversationTools({ ...harness(), ToolError }),
  };
}

const listIds = listed => listed.conversations.map(row => row.id);
const sorted = ids => [...ids].sort();

test("LIST-OWN-AND-GRANTED: the list is exactly the created set plus the unrevoked-granted set, under two actor contexts",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const seat = await listSeat(pg, t, listSlug("own", "author"));
    const { author, guest } = await listActors(seat.client, "own");
    const guestSeat = await listSeat(pg, t, listSlug("own", "guest"));

    const create = async (s, actor, title) => (await s.verbs["create-doc-conversation"].handler(
      s.c, actor, { idempotency_key: randomUUID(), title, visibility: "private" }));
    const a = await create(seat, { id: author.id, slug: author.slug }, "WR-000115 A");
    const b = await create(seat, { id: author.id, slug: author.slug }, "WR-000115 B");
    const cc = await create(guestSeat, { id: guest.id, slug: guest.slug }, "WR-000115 C");

    await seat.verbs["share-doc-conversation"].handler(seat.c, { id: author.id },
      { idempotency_key: randomUUID(), conversation_id: b.conversation_id,
        grantee_slug: guest.slug, granted: true });

    // The WHOLE set under each context, by deepEqual on a sorted array. A
    // "contains B" assertion would pass for an implementation that returned
    // everything the connection can read; this one fails, because the author's
    // list would then carry C.
    const authorList = await seat.verbs["list-doc-conversations"].handler(seat.c, {}, {});
    assert.deepEqual(sorted(listIds(authorList)), sorted([a.conversation_id, b.conversation_id]));
    assert.equal(authorList.visible_conversation_count, 2);

    const guestList = await guestSeat.verbs["list-doc-conversations"].handler(guestSeat.c, {}, {});
    assert.deepEqual(sorted(listIds(guestList)), sorted([b.conversation_id, cc.conversation_id]));
    assert.equal(guestList.visible_conversation_count, 2);
  });

test("LIST-PRIVATE-INVISIBLE: a private conversation is absent from the other partner's list, appears on grant, and disappears on revoke, while the creator's list never changes",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const seat = await listSeat(pg, t, listSlug("priv", "author"));
    const { author, guest } = await listActors(seat.client, "priv");
    const guestSeat = await listSeat(pg, t, listSlug("priv", "guest"));

    const p = await seat.verbs["create-doc-conversation"].handler(seat.c, { id: author.id },
      { idempotency_key: randomUUID(), title: "WR-000115 private", visibility: "private" });
    // The guest owns one of its own, so its baseline count is not zero and a
    // count assertion cannot pass by accident.
    await guestSeat.verbs["create-doc-conversation"].handler(guestSeat.c, { id: guest.id },
      { idempotency_key: randomUUID(), title: "WR-000115 guest own", visibility: "private" });

    const authorSees = async () => {
      const listed = await seat.verbs["list-doc-conversations"].handler(seat.c, {}, {});
      const row = listed.conversations.find(entry => entry.id === p.conversation_id);
      assert.ok(row, "the creator's own list lost the conversation");
      assert.equal(row.version, p.version, "the creator's view of the version moved");
      return listed;
    };
    const guestSees = async () => {
      const listed = await guestSeat.verbs["list-doc-conversations"].handler(guestSeat.c, {}, {});
      return { listed, present: listIds(listed).includes(p.conversation_id) };
    };

    // 1. Before any grant.
    await authorSees();
    let seen = await guestSees();
    assert.equal(seen.present, false, "a private conversation reached the other partner's list");
    assert.equal(seen.listed.visible_conversation_count, 1);

    // 2. After the grant.
    await seat.verbs["share-doc-conversation"].handler(seat.c, { id: author.id },
      { idempotency_key: randomUUID(), conversation_id: p.conversation_id,
        grantee_slug: guest.slug, granted: true });
    await authorSees();
    seen = await guestSees();
    assert.equal(seen.present, true, "a granted conversation did not reach the grantee's list");
    assert.equal(seen.listed.visible_conversation_count, 2);

    const stamped = await seat.client.query(
      `select granted_by_actor, granted_at, revoked_at from ops.doc_conversation_grant
        where conversation_id = $1 and grantee_actor = $2`, [p.conversation_id, guest.id]);
    assert.equal(stamped.rows.length, 1);
    const grantRow = stamped.rows[0];
    assert.equal(grantRow.revoked_at, null);

    // 3. After the revoke: absent again, and the row is STAMPED, never deleted.
    await seat.verbs["share-doc-conversation"].handler(seat.c, { id: author.id },
      { idempotency_key: randomUUID(), conversation_id: p.conversation_id,
        grantee_slug: guest.slug, granted: false });
    await authorSees();
    seen = await guestSees();
    assert.equal(seen.present, false, "a REVOKED grant still shows the conversation to the grantee");
    assert.equal(seen.listed.visible_conversation_count, 1);

    const after = await seat.client.query(
      `select granted_by_actor, granted_at, revoked_at from ops.doc_conversation_grant
        where conversation_id = $1 and grantee_actor = $2`, [p.conversation_id, guest.id]);
    assert.equal(after.rows.length, 1, "the revoke deleted the grant row instead of stamping it");
    assert.equal(after.rows[0].granted_by_actor, grantRow.granted_by_actor);
    assert.deepEqual(after.rows[0].granted_at, grantRow.granted_at);
    assert.notEqual(after.rows[0].revoked_at, null);

    // 4. The creator's own list is unchanged at every one of the four moments.
    await authorSees();
  });

test("LIST-ORDER-AND-PAGE: pinned first then most recent, every conversation exactly once across pages, an honest more, a clamped limit, archived excluded by default",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const seat = await listSeat(pg, t, listSlug("page", "author"));
    const { author } = await listActors(seat.client, "page");
    const actor = { id: author.id };

    const seeded = [];
    for (let index = 0; index < 7; index += 1) {
      seeded.push(await seat.verbs["create-doc-conversation"].handler(seat.c, actor,
        { idempotency_key: randomUUID(), title: `WR-000115 page ${index}`, visibility: "private" }));
    }
    const versions = new Map(seeded.map(row => [row.conversation_id, row.version]));
    const flag = async (row, fields) => {
      const result = await seat.verbs["rename-doc-conversation"].handler(seat.c, actor,
        { idempotency_key: randomUUID(), conversation_id: row.conversation_id,
          base_version: versions.get(row.conversation_id), ...fields });
      versions.set(row.conversation_id, result.version);
      return result;
    };
    await flag(seeded[0], { pinned: true });
    await flag(seeded[1], { pinned: true });
    await flag(seeded[6], { archived: true });
    // TOUCH AN UNPINNED ROW LAST, so the two pinned rows are NOT also the two
    // most recently updated. Pinning bumps updated_at, so without this the data
    // cannot tell a pinned-first order from a plain updated_at order at all and
    // the position assertion below would hold for either.
    await flag(seeded[2], { title: "WR-000115 page 2 touched last" });
    const pinned = [seeded[0].conversation_id, seeded[1].conversation_id];
    const archived = seeded[6].conversation_id;
    const expected = seeded.slice(0, 6).map(row => row.conversation_id);

    const walk = async (between = null) => {
      const ids = [];
      const mores = [];
      let cursor = null;
      let page = 0;
      for (;;) {
        const args = { limit: 2 };
        if (cursor !== null) args.cursor = cursor;
        const listed = await seat.verbs["list-doc-conversations"].handler(seat.c, {}, args);
        ids.push(...listIds(listed));
        mores.push(listed.more);
        // (d) next_cursor is null exactly when more is false.
        assert.equal(listed.next_cursor === null, listed.more === false,
          "next_cursor and more disagree");
        if (!listed.more) break;
        cursor = listed.next_cursor;
        page += 1;
        if (between && page === 1) await between();
        assert.ok(page < 20, "the cursor walk did not terminate");
      }
      return { ids, mores };
    };

    const first = await walk();
    // (a) no duplicates and no omissions.
    assert.deepEqual(sorted(first.ids), sorted(expected));
    assert.equal(new Set(first.ids).size, first.ids.length, "a conversation was returned twice");
    // (b) both pinned ids occupy positions 0 and 1 of the CONCATENATION.
    assert.deepEqual(sorted(first.ids.slice(0, 2)), sorted(pinned),
      "pinned-first held only within a page");
    // (c) more is true on every page but the last, and false on the last.
    assert.deepEqual(first.mores, [...first.mores.slice(0, -1).map(() => true), false]);
    assert.equal(first.mores.at(-1), false);
    assert.ok(first.mores.slice(0, -1).every(value => value === true));
    // The archived conversation is absent by default.
    assert.ok(!first.ids.includes(archived), "an archived conversation appeared in the default page");
  });

test("LIST-ORDER-AND-PAGE/MID-WALK-PIN: a conversation pinned between page one and page two is still returned exactly once",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const seat = await listSeat(pg, t, listSlug("pin", "author"));
    const { author } = await listActors(seat.client, "pin");
    const actor = { id: author.id };

    const seeded = [];
    for (let index = 0; index < 7; index += 1) {
      seeded.push(await seat.verbs["create-doc-conversation"].handler(seat.c, actor,
        { idempotency_key: randomUUID(), title: `WR-000115 midwalk ${index}`, visibility: "private" }));
    }
    const expected = seeded.map(row => row.conversation_id);

    const ids = [];
    let cursor = null;
    let page = 0;
    for (;;) {
      const args = { limit: 2 };
      if (cursor !== null) args.cursor = cursor;
      const listed = await seat.verbs["list-doc-conversations"].handler(seat.c, {}, args);
      ids.push(...listIds(listed));
      if (!listed.more) break;
      cursor = listed.next_cursor;
      page += 1;
      if (page === 1) {
        // PIN A ROW PAGE ONE ALREADY RETURNED. Pinning moves it to the front of
        // the whole order, which is a position the reader has already passed. A
        // cursor carrying the WHOLE sort key knows that and leaves the rest of
        // the walk untouched; one that carries only (updated_at, id) cannot say
        // whether the reader is still inside the pinned block, so the pages
        // after this point stop being a partition of the visible set.
        //
        // Pinning a row the walk has NOT yet reached is deliberately not the
        // case here: no keyset cursor of any width can return a row that has
        // moved behind the reader, so such a walk would prove nothing about the
        // cursor's shape.
        const victim = seeded.find(row => row.conversation_id === ids[0]);
        assert.ok(victim, "the mid-walk pin must target a row page one returned");
        await seat.verbs["rename-doc-conversation"].handler(seat.c, actor,
          { idempotency_key: randomUUID(), conversation_id: victim.conversation_id,
            base_version: victim.version, pinned: true });
      }
      assert.ok(page < 20, "the cursor walk did not terminate");
    }
    assert.equal(new Set(ids).size, ids.length, "the mid-walk pin returned a conversation twice");
    assert.deepEqual(sorted(ids), sorted(expected), "the mid-walk pin dropped a conversation");
  });

test("LIST-ORDER-AND-PAGE/LIMIT-AND-ARCHIVED: an oversized limit is clamped to 100 and the archived toggle is honoured",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const seat = await listSeat(pg, t, listSlug("clamp", "author"));
    const { author } = await listActors(seat.client, "clamp");
    const actor = { id: author.id };

    const live = await seat.verbs["create-doc-conversation"].handler(seat.c, actor,
      { idempotency_key: randomUUID(), title: "WR-000115 live", visibility: "private" });
    const gone = await seat.verbs["create-doc-conversation"].handler(seat.c, actor,
      { idempotency_key: randomUUID(), title: "WR-000115 archived", visibility: "private" });
    await seat.verbs["rename-doc-conversation"].handler(seat.c, actor,
      { idempotency_key: randomUUID(), conversation_id: gone.conversation_id,
        base_version: gone.version, archived: true });

    // Clamped, never refused: the function answers, with at most 100 rows.
    const oversized = await seat.verbs["list-doc-conversations"].handler(seat.c, {}, { limit: 1000 });
    assert.ok(oversized.conversations.length <= 100,
      "an oversized limit was honoured instead of clamped");
    // The schema is the other half of the clamp: a zero limit never reaches the
    // function at all, because the transport refuses it.
    assert.equal(TOOLS["list-doc-conversations"].inputSchema.properties.limit.minimum, 1);
    assert.equal(TOOLS["list-doc-conversations"].inputSchema.properties.limit.maximum, 100);

    const byDefault = await seat.verbs["list-doc-conversations"].handler(seat.c, {}, {});
    assert.deepEqual(sorted(listIds(byDefault)), sorted([live.conversation_id]));
    const withArchived = await seat.verbs["list-doc-conversations"].handler(seat.c, {},
      { include_archived: true });
    assert.deepEqual(sorted(listIds(withArchived)),
      sorted([live.conversation_id, gone.conversation_id]));
    // visible_conversation_count is the WHOLE visible set, archived included
    // and paging ignored -- 0520:210-214 unchanged.
    assert.equal(byDefault.visible_conversation_count, 2);
  });

test("LIST-ATTRIBUTION-SERVER-SIDE: the schema names no actor, and the set follows the acting context and no argument",
  () => {
    const schema = TOOLS["list-doc-conversations"].inputSchema;
    assert.equal(schema.additionalProperties, false);
    // An EXACT sorted key list, not a loop over forbidden names: a newly
    // invented actor-shaped field fails here too.
    assert.deepEqual(Object.keys(schema.properties).sort(),
      ["cursor", "include_archived", "limit"]);
    assert.equal(Object.hasOwn(schema, "required"), false,
      "every field is optional: the first page of the signed-in partner's list is {}");
    // The handler passes no actor value at all, unlike read-doc-conversation
    // beside it, which passes a.id.
    const handler = TOOLS["list-doc-conversations"].handler.toString();
    assert.ok(!/\ba\.id\b/.test(handler), "the list handler passes an actor from the caller");
    assert.ok(!/\bactor\b/.test(handler), "the list handler names an actor");
  });

test("LIST-ATTRIBUTION-SERVER-SIDE/SIGNATURE: ops.list_doc_conversations takes exactly three arguments and none of them is an actor",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const client = await connect(pg, null);
    t.after(() => client.end().catch(() => {}));
    const r = await client.query(
      `select pg_get_function_identity_arguments(p.oid) as args
         from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'ops' and p.proname = 'list_doc_conversations'`);
    assert.equal(r.rows.length, 1, "ops.list_doc_conversations is not installed exactly once");
    // The shipped read keeps p_actor_id in slot two; this signature does not,
    // and the assertion fails immediately if anyone copies that slot forward.
    assert.equal(r.rows[0].args,
      "p_cursor text, p_limit integer, p_include_archived boolean");
    assert.equal(r.rows[0].args.split(",").length, 3, "exactly three arguments");
    assert.ok(!/uuid/.test(r.rows[0].args), "an argument is a uuid, which is what an actor id is");
    assert.ok(!/actor/i.test(r.rows[0].args), "an argument names an actor");
  });

test("LIST-APP-CALLABLE: the verb is not authorityOnly and reaches its function on the writer bundle",
  () => {
    assert.ok(TOOLS["list-doc-conversations"], "list-doc-conversations is not registered");
    assert.equal(TOOLS["list-doc-conversations"].write, undefined,
      "a list writes nothing: mcp.js opens `begin read only` only without a write flag");
    assert.equal(TOOLS["list-doc-conversations"].writerConnection, true,
      "ops.list_doc_conversations is granted to carr_writer, and only the writer path installs the actor context");
    assert.equal(TOOLS["list-doc-conversations"].authorityOnly, undefined,
      "list-doc-conversations must not be authorityOnly: the app calls it as the signed-in partner");
    for (const profile of ["capture", "away"]) {
      assert.ok(!PROFILES[profile].has("list-doc-conversations"),
        `list-doc-conversations must not be in the ${profile} profile`);
    }
  });

test("the list shaper refuses anything that is not the definer function's own shape", () => {
  assert.throws(() => docConversationListProjection(null, ToolError), /doc_conversation_not_found/);
  assert.throws(() => docConversationListProjection({ ok: false }, ToolError),
    /doc_conversation_not_found/);
  assert.throws(() => docConversationListProjection(
    { ok: false, reason_id: "doc_conversation_cursor_invalid" }, ToolError),
  /doc_conversation_cursor_invalid/);
  assert.throws(() => docConversationListProjection({ ok: true }, ToolError),
    /doc_conversation_not_found/);
});
