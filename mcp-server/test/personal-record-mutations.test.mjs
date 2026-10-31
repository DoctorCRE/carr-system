// Tests authorization for personal record mutations.
import test from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool, ToolError, TOOLS } from "../src/tools.js";
import { agentActorForToken } from "../src/identity.js";
import { derivedIdempotencyKey } from "../src/derived-key.ts";

// All identities and records are synthetic. Exercise registered dispatch and
// the real envelope; the adapter records domain writes and conflict reads.
const joe = { id: "10000000-0000-4000-8000-000000000001", slug: "joe", human: true };
const dell = { id: "10000000-0000-4000-8000-000000000002", slug: "dell", human: true };
const machineId = "10000000-0000-4000-8000-000000000003";
const sectionId = "20000000-0000-4000-8000-000000000001";
const loopId = "30000000-0000-4000-8000-000000000001";
const documentId = "40000000-0000-4000-8000-000000000001";
const receiptId = "50000000-0000-4000-8000-000000000001";
const doctrineVerbs = ["write-doctrine-section", "change-doctrine-sections",
  "set-doctrine-refs", "retire-doctrine-section", "claim-doctrine-sections"];
const loopVerbs = ["update-loop", "close-loop", "amend-closed-loop"];

function argsFor(verb, base_version = 2, byNumber = false) {
  const section = { section_key: "test", body_text: "Updated synthetic text", base_version };
  const args = { idempotency_key: derivedIdempotencyKey("test", `ownership-${verb}`), document: "test-doc",
    section_id: sectionId, section_ids: [sectionId], ...section, items: [section],
    ...(byNumber ? { number: "123", kind: "open_loop" } : { loop_id: loopId }),
    title: "Updated synthetic title", outcome: "The synthetic work was completed.",
    reason: "Correct the synthetic record", purpose: "Edit the synthetic section" };
  return Object.fromEntries(Object.entries(args).filter(([key]) => key in TOOLS[verb].inputSchema.properties));
}

function database(verb, { owner = joe.id, shared = false, claimHolder = null,
  changedOwner = null, foreignSection = null } = {}) {
  const queries = [], writes = [], events = [];
  const doc = { id: documentId, slug: "test-doc", content_class: "reference",
    visibility: shared ? "shared" : "personal", owner_actor_id: shared ? null : owner };
  const section = { ...doc, id: sectionId, document_id: documentId,
    section_key: "test", current_version: "2", current_revision_id: receiptId };
  const loop = { id: loopId, number: "123", kind: "open_loop", section: "hot",
    status: verb === "amend-closed-loop" ? "done" : "open", marker: "none",
    due_on: null, close_outcome: "Prior synthetic outcome", version: 2,
    tier: shared ? "shared" : "personal", personal_to: shared ? null : owner };
  return { queries, writes, events, async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    queries.push(sql);
    if (sql.startsWith("select pg_advisory_xact_lock")) return {rows:[]};
    if (sql.includes("ops.future_work_link")) return {rows:[{ref:'WR-999628'}]};
    if (sql.includes("d.slug='team-memory'")) return {rows:[]};
    if (sql.startsWith("select request_hash")) return { rows: [] };
    if (sql.startsWith("select id from actor where slug"))
      return { rows: [joe, dell].filter(a => a.slug === params[0]).map(a => ({ id: a.id })) };
    if (sql.includes("from doctrine_document") && !sql.includes("max_age_days")) return { rows: [doc] };
    if (sql.includes("max_age_days")) return { rows: [{ max_age_days: null }] };
    if (sql.includes("from doctrine_section")) return { rows: [{ ...section,
      ...(foreignSection === params[0] ? { owner_actor_id: dell.id } : {}) }] };
    if (sql.includes("from doctrine_revision")) return { rows: [{ version: "2" }] };
    if (sql.includes("from doctrine_gate_check")) return { rows: [] };
    if (sql.includes("from doctrine_claim")) return { rows: claimHolder
      ? [{ holder_actor_id: claimHolder, expires_at: "2099-01-01" }] : [] };
    if (sql.includes("from loop_item li join loop_block lb")) return { rows: [{ ...loop,
      ...(changedOwner && /for update/.test(sql) ? { tier: "personal", personal_to: changedOwner } : {}) }] };
    if (sql.startsWith("select version from loop_item")) return { rows: [{ version: 2 }] };
    if (sql.startsWith("select created_at from loop_item")) return { rows: [{ created_at: "2026-01-01" }] };
    if (sql.includes("from event e join actor")) return { rows: [] };
    if (sql.startsWith("select id, rel_path, block_key from loop_block")) return { rows: [] };
    if (sql.startsWith("insert into event")) { events.push(params); return { rows: [] }; }
    if (sql.startsWith("insert into tool_call")) return { rows: [] };
    if (/^(insert|update|delete)/.test(sql)) {
      writes.push({ sql, params });
      if (sql.startsWith("update doctrine_meta")) return { rows: [{ generation: "3" }] };
      if (sql.startsWith("delete from doctrine_claim")) return { rows: [{ section_id: sectionId }] };
      return { rows: [{ id: receiptId }] };
    }
    throw new Error(`unhandled synthetic query: ${sql}`);
  } };
}

async function denied(verb, actor, options, baseVersion = 2, byNumber = false) {
  const db = database(verb, options);
  await assert.rejects(() => executeRegisteredTool(db, actor, verb, {
    ...argsFor(verb, baseVersion, byNumber),
  }), e => e instanceof ToolError && e.payload.error ===
    (doctrineVerbs.includes(verb) ? "personal_doc_not_owner" : "personal_loop_not_owner"));
  assert.equal(db.writes.length, 0, "deny before domain writes, including gate/claim/amendment writes");
  assert.equal(db.events.length, 0, "denied calls emit no domain events");
  assert.equal(db.queries.some(sql => /from doctrine_revision|select version from loop_item|from event e join actor|from doctrine_claim/.test(sql)),
    false, "authorize before version/conflict or claim-holder reads");
}

for (const verb of [...doctrineVerbs, ...loopVerbs]) {
  for (const [owner, intruder] of [[joe, dell], [dell, joe]]) {
    for (const version of (loopVerbs.includes(verb) ? [2, 1, null] : [2, 1])) {
      test(`${verb}: ${intruder.slug} cannot mutate ${owner.slug}'s personal record (base ${version})`,
        () => denied(verb, intruder, { owner: owner.id, claimHolder: intruder.id }, version));
    }
  }
  for (const [label, actor, options] of [
    ["owner Joe", joe, { owner: joe.id }],
    ["owner Dell", dell, { owner: dell.id }],
    ["shared cross-partner", dell, { shared: true }],
    ["legacy machine owner", { id: machineId, slug: "codex", human: false }, { owner: machineId }],
  ]) {
    test(`${verb}: ${label} remains authorized`, async () => {
      const db = database(verb, options);
      assert.equal((await executeRegisteredTool(db, actor, verb, argsFor(verb))).ok, true);
      assert.ok(db.writes.length > 0);
    });
  }
  for (const sponsor of [joe, dell]) {
    const other = sponsor === joe ? dell : joe;
    const native = { id: machineId, slug: "codex", human: false,
      native_agent_verified: true, sponsoring_human_slug: sponsor.slug, via: "oauth-google" };
    const local = { ...agentActorForToken("Bearer synthetic-local", JSON.stringify({
      [`${sponsor.slug}-local`]: "synthetic-local" }), "local-token"), id: machineId };
    for (const [label, actor] of [["native", native], ["local", local]]) {
      test(`${verb}: verified ${sponsor.slug}-sponsored ${label} may mutate sponsor's record`, async () => {
        const db = database(verb, { owner: sponsor.id });
        assert.equal((await executeRegisteredTool(db, actor, verb, argsFor(verb))).ok, true);
      });
      test(`${verb}: ${sponsor.slug}-sponsored ${label} cannot mutate ${other.slug}'s record`,
        () => denied(verb, actor, { owner: other.id }));
    }
  }
  for (const actor of [
    { id: machineId, slug: "codex", human: false },
    { id: machineId, slug: "codex", human: false, sponsoring_human_slug: "joe", native_agent_verified: false },
    { id: machineId, slug: "jev", human: false, sponsoring_human_slug: "joe", review: true },
  ]) {
    test(`${verb}: ${actor.slug} without verified partner authority cannot borrow Joe's ownership`,
      () => denied(verb, actor, { owner: joe.id }));
  }
}

for (const verb of loopVerbs) {
  test(`${verb}: number resolution denies another partner's personal loop`,
    () => denied(verb, dell, { owner: joe.id }, 1, true));
  test(`${verb}: ownership is checked on the locked target`,
    () => denied(verb, dell, { shared: true, changedOwner: joe.id }));
  test(`${verb}: authorized number resolution locks its target`, async () => {
    const db = database(verb);
    assert.equal((await executeRegisteredTool(db, joe, verb, argsFor(verb, 2, true))).ok, true);
    assert.ok(db.queries.some(sql => sql.includes("where li.number = $1") && /for update of li/.test(sql)));
  });
}

test("claim-doctrine-sections preserves foreign-holder denial on an authorized shared section", async () => {
  const db = database("claim-doctrine-sections", { shared: true, claimHolder: dell.id });
  const result = await executeRegisteredTool(db, joe, "claim-doctrine-sections", argsFor("claim-doctrine-sections"));
  assert.equal(result.claims.length, 0);
  assert.equal(result.denied.length, 1);
  assert.equal(db.writes.length, 0);
});

for (const verb of [...doctrineVerbs, ...loopVerbs]) {
  test(`${verb}: caller-supplied sponsor is refused before database access`, async () => {
    const db = database(verb);
    await assert.rejects(() => executeRegisteredTool(db, dell, verb, {
      ...argsFor(verb), sponsoring_human_slug: "joe",
    }), e => e instanceof ToolError && e.payload.error === "caller_authority_field_forbidden");
    assert.equal(db.queries.length, 0);
  });
  test(`${verb}: a personal record without an owner fails closed`,
    () => denied(verb, joe, { owner: null }));
}

test("claim-doctrine-sections authorizes every section before changing any claim", async () => {
  const foreignSection = "20000000-0000-4000-8000-000000000002";
  const db = database("claim-doctrine-sections", { foreignSection });
  await assert.rejects(() => executeRegisteredTool(db, joe, "claim-doctrine-sections", {
    ...argsFor("claim-doctrine-sections"), section_ids: [sectionId, foreignSection],
  }), e => e instanceof ToolError && e.payload.error === "personal_doc_not_owner");
  assert.equal(db.writes.length, 0);
  assert.equal(db.queries.some(sql => sql.includes("from doctrine_claim")), false);
});
