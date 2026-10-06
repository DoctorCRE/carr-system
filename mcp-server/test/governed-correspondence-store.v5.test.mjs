// V5-J103 governed correspondence store: the verb layer, against a fake record
// layer. The database half is proved by governed-correspondence-store-postgres.sql
// on real PostgreSQL; this suite proves what the JavaScript adds — argument
// refusal before any query, partner derivation from the transaction rather than
// the caller, account digesting, fail-closed receipt revalidation, and that no
// verb this module registers can send or draft.

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import {
  V5_J103_STORE_VERBS,
  correspondenceAccountDigest,
  governedCorrespondenceStoreTools,
  revalidateReceipt,
} from "../src/governed-correspondence-store.v5.js";
import { V5_F10_WRITE_OPERATIONS } from "../src/partner-mail-calendar.v5.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const envelopes = [];
const events = [];
const withEnvelope = async (c, actor, verb, args, fn) => { envelopes.push({ verb, args }); return fn(); };
const writeEvent = async (c, actor, verb, type, id, fields) => { events.push({ verb, type, id, fields }); };
const TOOLS = governedCorrespondenceStoreTools({ withEnvelope, writeEvent, ToolError });

function fakeClient(responses) {
  const calls = [];
  return {
    calls,
    async query(sql, params) {
      calls.push({ sql, params });
      for (const [pattern, rows] of responses) {
        if (sql.includes(pattern)) return { rows: typeof rows === "function" ? rows(params) : rows };
      }
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

async function refusedWith(promise, code) {
  await assert.rejects(promise, (e) => e instanceof ToolError && e.payload.error === code, `expected ${code}`);
}

const actor = { id: "00000000-0000-4000-8000-00000000aaaa", slug: "joe", human: true };
const KEY = "00000000-0000-4000-8000-000000000001";
const CONSENT = "00000000-0000-4000-8000-0000000000c1";

test("the module registers exactly its four verbs, and none can send or draft", () => {
  assert.deepEqual(Object.keys(TOOLS).sort(), [...V5_J103_STORE_VERBS].sort());
  for (const name of Object.keys(TOOLS)) {
    assert.ok(!/send|dispatch|draft|deliver|transmit|outbound|forward|reply/.test(name), `${name} names a send path`);
    for (const op of V5_F10_WRITE_OPERATIONS) assert.ok(!name.includes(op.replaceAll("_", "-")));
  }
  assert.equal(TOOLS["correspondence-readiness"].write, false);
  assert.equal(TOOLS["read-correspondence-thread"].write, false);
  assert.equal(TOOLS["record-correspondence-adapter-consent"].humanOnly, true);
  assert.equal(TOOLS["revoke-correspondence-adapter-consent"].humanOnly, true);
});

test("no verb's input schema has a field a destination or send instruction could occupy", () => {
  for (const [name, tool] of Object.entries(TOOLS)) {
    assert.equal(tool.inputSchema.additionalProperties, false, `${name} schema is open`);
    for (const field of Object.keys(tool.inputSchema.properties)) {
      assert.ok(!/recipient|to_address|send|dispatch|schedule|deliver|(^|_)b?cc(_|$)|reply_to/.test(field), `${name}.${field}`);
    }
  }
});

test("consent refuses every provider write operation before touching the database", async () => {
  for (const op of V5_F10_WRITE_OPERATIONS) {
    const c = fakeClient([]);
    await refusedWith(TOOLS["record-correspondence-adapter-consent"].handler(c, actor, {
      idempotency_key: KEY, adapter_kind: "v5_f10_partner_mail_calendar_adapter",
      account: "joe.bookout@carr.us", read_operations: ["list_mail_messages", op], human_quote: "yes",
    }), "write_operation_refused");
    assert.equal(c.calls.length, 0, `${op} reached the database`);
  }
});

test("consent takes the partner from the transaction, never from the caller", async () => {
  await refusedWith(TOOLS["record-correspondence-adapter-consent"].handler(fakeClient([]), actor, {
    idempotency_key: KEY, adapter_kind: "v5_f10_partner_mail_calendar_adapter", account: "joe.bookout@carr.us",
    read_operations: ["list_mail_messages"], human_quote: "yes", partner_slug: "dell",
  }), "unregistered_field");
  const noVerified = fakeClient([["verified_human_actor_slug", [{ slug: null }]]]);
  await refusedWith(TOOLS["record-correspondence-adapter-consent"].handler(noVerified, actor, {
    idempotency_key: KEY, adapter_kind: "v5_f10_partner_mail_calendar_adapter", account: "joe.bookout@carr.us",
    read_operations: ["list_mail_messages"], human_quote: "yes",
  }), "verified_partner_required");
});

test("consent stores and returns only the account digest", async () => {
  events.length = 0;
  const c = fakeClient([
    ["verified_human_actor_slug", [{ slug: "joe" }]],
    ["correspondence_record_adapter_consent", [{ id: CONSENT }]],
  ]);
  const r = await TOOLS["record-correspondence-adapter-consent"].handler(c, actor, {
    idempotency_key: KEY, adapter_kind: "v5_f10_partner_mail_calendar_adapter", account: "  Joe.Bookout@CARR.us ",
    read_operations: ["read_mail_message_metadata", "list_mail_messages"], human_quote: "I consent to read-only access",
  });
  const expected = `sha256:${createHash("sha256").update("joe.bookout@carr.us").digest("hex")}`;
  assert.equal(r.account_digest, expected);
  assert.equal(r.partner_slug, "joe");
  assert.equal(r.reads_enabled, false);
  assert.equal(r.dispatchable, false);
  const write = c.calls.find(q => q.sql.includes("correspondence_record_adapter_consent"));
  assert.equal(write.params[0], "joe");
  assert.equal(write.params[2], expected);
  assert.deepEqual(write.params[3], ["list_mail_messages", "read_mail_message_metadata"]);
  const everything = JSON.stringify([r, c.calls, events]);
  assert.ok(!/@carr\.us/i.test(everything), "the raw account leaked");
});

test("an account that is not a mailbox address is refused", () => {
  assert.throws(() => correspondenceAccountDigest("not-an-address"), /mailbox address/);
  assert.throws(() => correspondenceAccountDigest("mailto:joe@example.invalid.test"), /mailbox address/);
});

test("revocation needs a verified partner and the database decides whose consent it is", async () => {
  const c = fakeClient([
    ["verified_human_actor_slug", [{ slug: "joe" }]],
    ["correspondence_revoke_adapter_consent", [{ id: "00000000-0000-4000-8000-0000000000d1" }]],
  ]);
  const r = await TOOLS["revoke-correspondence-adapter-consent"].handler(c, actor, {
    idempotency_key: KEY, consent_id: CONSENT, human_quote: "stop",
  });
  assert.equal(r.consent_in_force, false);
  await refusedWith(TOOLS["revoke-correspondence-adapter-consent"].handler(fakeClient([]), actor, {
    idempotency_key: KEY, consent_id: "not-a-uuid", human_quote: "stop",
  }), "invalid_uuid");
});

test("a thread read with no receipt is unavailable and names the owed seam", async () => {
  const c = fakeClient([["correspondence_thread_readback", [{ rows: [] }]]]);
  const r = await TOOLS["read-correspondence-thread"].handler(c, actor, { source_system: "fixture-mail", native_id: "t-1", native_id_epoch: 0 });
  assert.equal(r.decision, "unavailable");
  assert.match(r.owed_seam, /adapter-read-receipt|adapter_read_receipt|receipt/);
});

const storedRow = (over = {}) => ({
  read_receipt_id: "00000000-0000-4000-8000-0000000000e1", partner_slug: "joe",
  adapter_kind: "v5_f10_partner_mail_calendar_adapter", account_digest: `sha256:${"a".repeat(64)}`,
  native_identity: { source_system: "fixture-mail", native_id: "t-1", native_id_epoch: 0 },
  thread_metadata: { relevance_state: "related" }, metadata_digest: `sha256:${"b".repeat(64)}`,
  recomputed_digest: `sha256:${"b".repeat(64)}`, consent_in_force: true, recorded_at: "2026-09-25T12:00:00.000Z", ...over,
});

test("a thread read carries account and native provenance on every receipt", async () => {
  const c = fakeClient([["correspondence_thread_readback", [{ rows: [storedRow()] }]]]);
  const r = await TOOLS["read-correspondence-thread"].handler(c, actor, { source_system: "fixture-mail", native_id: "t-1", native_id_epoch: 0 });
  assert.equal(r.decision, "receipts_found");
  assert.equal(r.receipts[0].account_digest, `sha256:${"a".repeat(64)}`);
  assert.deepEqual({ ...r.receipts[0].native_identity }, { source_system: "fixture-mail", native_id: "t-1", native_id_epoch: 0 });
  assert.equal(r.dispatchable, false);
});

test("a receipt whose bytes no longer produce its digest refuses the whole read", async () => {
  const c = fakeClient([["correspondence_thread_readback", [{ rows: [storedRow(), storedRow({ recomputed_digest: `sha256:${"c".repeat(64)}` })] }]]]);
  await refusedWith(TOOLS["read-correspondence-thread"].handler(c, actor, { source_system: "fixture-mail", native_id: "t-1", native_id_epoch: 0 }), "receipt_digest_mismatch");
});

test("a receipt missing provenance or for another identity is refused", async () => {
  assert.throws(() => revalidateReceipt(storedRow({ account_digest: "joe" })), /account_digest/);
  const { native_identity, ...partial } = storedRow();
  assert.throws(() => revalidateReceipt(partial), /native_identity/);
  const c = fakeClient([["correspondence_thread_readback", [{ rows: [storedRow({ native_identity: { source_system: "fixture-mail", native_id: "t-2", native_id_epoch: 0 } })] }]]]);
  await refusedWith(TOOLS["read-correspondence-thread"].handler(c, actor, { source_system: "fixture-mail", native_id: "t-1", native_id_epoch: 0 }), "receipt_provenance_lost");
});

test("readiness reports consent, receipts and every owed step from the record layer", async () => {
  const c = fakeClient([["correspondence_readiness", [{ r: {
    server_instant: "2026-09-25T12:00:00.000Z", read_receipt_writer_granted_to_runtime: false,
    partners: [{ partner_slug: "dell", consents_in_force: 0, consents_revoked: 0, read_receipts: 0, drafts: 0 },
               { partner_slug: "joe", consents_in_force: 0, consents_revoked: 0, read_receipts: 0, drafts: 0 }],
  } }]]]);
  const r = await TOOLS["correspondence-readiness"].handler(c, actor, {});
  assert.equal(r.mailbox_reads_possible, false);
  assert.equal(r.activation.status, "consent_not_recorded");
  assert.ok(r.owed.some(s => s.step === "human:partner-local-mailbox-consent"));
  assert.ok(r.owed.some(s => s.step === "step:governed-correspondence-internal-update-independent-receipt"));
  assert.ok(r.never_consentable_operations.includes("send_mail_message"));
  assert.ok(!r.consentable_operations.includes("send_mail_message"));
  assert.equal(r.automatic_internal_update, false);
});

test("unknown arguments are refused before any query on every verb", async () => {
  for (const [name, tool] of Object.entries(TOOLS)) {
    const c = fakeClient([]);
    await refusedWith(tool.handler(c, actor, { idempotency_key: KEY, send_now: true }), "unregistered_field");
    assert.equal(c.calls.length, 0, `${name} queried before refusing`);
  }
});

// ---------------------------------------------------------------- review round 1 (#1266)

test("consent is a positive allowlist: each partner only for their own carr.us mailbox, and the database holds the same pairs", async () => {
  const { V5_J103_PARTNER_MAILBOXES, partnerOwnsMailbox } = await import("../src/governed-correspondence-store.v5.js");
  const { readFileSync, readdirSync } = await import("node:fs");
  assert.deepEqual(JSON.parse(JSON.stringify(V5_J103_PARTNER_MAILBOXES)),
    { joe: ["joe.bookout@carr.us"], dell: ["dell.mccraney@carr.us"] });
  const refusedFor = [
    // [partner, account]: the other partner's mailbox, both partners' Google
    // sign-in addresses, a delegated or shared mailbox, and an unknown one.
    ["joe", "dell.mccraney@carr.us"], ["joe", " DELL.McCraney@carr.us "], ["joe", "dell.mccraney.carr.us@gmail.com"],
    ["joe", "joe.bookout.carr.us@gmail.com"], ["joe", "info@carr.us"], ["joe", "someone@example.invalid.test"],
    ["dell", "joe.bookout@carr.us"], ["dell", "joe.bookout.carr.us@gmail.com"], ["dell", "dell.mccraney.carr.us@gmail.com"],
    ["dell", "info@carr.us"], ["dell", "someone@example.invalid.test"],
  ];
  for (const [partner, account] of refusedFor) {
    assert.equal(partnerOwnsMailbox(partner, account), false, `${partner} / ${account}`);
    const c = fakeClient([["verified_human_actor_slug", [{ slug: partner }]]]);
    await refusedWith(TOOLS["record-correspondence-adapter-consent"].handler(c, actor, {
      idempotency_key: KEY, adapter_kind: "v5_f10_partner_mail_calendar_adapter", account,
      read_operations: ["list_mail_messages"], human_quote: "yes",
    }), "account_not_partners_own");
    assert.ok(!c.calls.some(q => q.sql.includes("correspondence_record_adapter_consent")),
      `${partner} / ${account}: the refusal reached the writer`);
  }
  for (const [partner, account] of [["joe", "joe.bookout@carr.us"], ["dell", " Dell.McCraney@CARR.us "]]) {
    assert.equal(partnerOwnsMailbox(partner, account), true, `${partner} / ${account}`);
    const c = fakeClient([
      ["verified_human_actor_slug", [{ slug: partner }]],
      ["correspondence_record_adapter_consent", [{ id: CONSENT }]],
    ]);
    const r = await TOOLS["record-correspondence-adapter-consent"].handler(c, actor, {
      idempotency_key: KEY, adapter_kind: "v5_f10_partner_mail_calendar_adapter", account,
      read_operations: ["list_mail_messages"], human_quote: "yes",
    });
    assert.equal(r.partner_slug, partner);
  }
  assert.equal(partnerOwnsMailbox("automation", "joe.bookout@carr.us"), false);
  assert.equal(partnerOwnsMailbox("__proto__", "joe.bookout@carr.us"), false);
  // Digest parity: the record layer holds exactly the same (partner, digest) pairs.
  const migration = readdirSync(new URL("../../migrations/", import.meta.url))
    .find(f => /_governed_correspondence_store\.sql$/.test(f));
  const sql = readFileSync(new URL(`../../migrations/${migration}`, import.meta.url), "utf8");
  const body = sql.match(/create function ops\.correspondence_partner_owns_account\(p_partner_slug text, p_account_digest text\)[\s\S]*?\$\$([\s\S]*?)\$\$;/)[1];
  const sqlPairs = [...body.matchAll(/\('(\w+)', '(sha256:[0-9a-f]{64})'\)/g)].map(m => `${m[1]}=${m[2]}`).sort();
  const jsPairs = Object.entries(V5_J103_PARTNER_MAILBOXES)
    .flatMap(([slug, list]) => list.map(a => `${slug}=${correspondenceAccountDigest(a)}`)).sort();
  assert.deepEqual(sqlPairs, jsPairs);
  assert.match(body, /\(p_partner_slug, p_account_digest\) in \(/, "the record layer no longer checks the pair");
});

test("no owed step or verb description mentions Google, Gmail or OAuth; activation names only the local stores", async () => {
  const { V5_J103_STORE_OWED_STEPS } = await import("../src/governed-correspondence-store.v5.js");
  const text = JSON.stringify([Object.values(TOOLS).map(t => [t.description, t.inputSchema]), V5_J103_STORE_OWED_STEPS]);
  assert.ok(!/google|gmail|oauth/i.test(text), "Google, Gmail or OAuth is still mentioned");
  const human = V5_J103_STORE_OWED_STEPS.find(s => s.step.startsWith("human:"));
  for (const want of [/HxStore/, /Apple Mail/, /Apple Calendar through EventKit/, /joe\.bookout@carr\.us/, /dell\.mccraney@carr\.us/])
    assert.match(human.what, want);
  const consent = TOOLS["record-correspondence-adapter-consent"].description;
  for (const want of [/joe\.bookout@carr\.us/, /dell\.mccraney@carr\.us/, /delegated or shared mailbox/, /HxStore/, /EventKit/])
    assert.match(consent, want);
});
