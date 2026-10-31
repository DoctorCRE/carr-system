// WR-000617 slice 7 -- a finished Doc request notifies the partner who asked.
//
// Pure: a small in-memory stand-in answers the four statements the verb issues
// (turn lookup, advisory lock, event lookup, ops.mint_notification). The mint
// stand-in keeps the real door's rule -- one row per (recipient, dedupe_key) --
// so the cases count stored notifications, never a replay cache.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { docConversationTools } from "../src/doc-conversation.js";
import { TOOLS } from "../src/tools.js";

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

const CONVERSATION = "5b1c0a52-0000-4000-8000-000000000001";
const JOE_ASK = "5b1c0a52-0000-4000-8000-0000000000a1";
const DELL_ASK = "5b1c0a52-0000-4000-8000-0000000000a2";
const ANSWER = "5b1c0a52-0000-4000-8000-0000000000b1";
const EARLIER_ANSWER = "5b1c0a52-0000-4000-8000-0000000000b0";

function store() {
  const turns = [
    { msg_id: EARLIER_ANSWER, sequence: 0, role: "assistant", origin_actor: "claude" },
    { msg_id: JOE_ASK, sequence: 1, role: "human", origin_actor: "joe" },
    { msg_id: DELL_ASK, sequence: 2, role: "human", origin_actor: "dell" },
    { msg_id: ANSWER, sequence: 3, role: "assistant", origin_actor: "claude" },
  ];
  const events = [];
  const notifications = [];
  const c = {
    async query(text, values = []) {
      if (text.includes("from ops.doc_conversation_turn")) {
        const row = values[0] === CONVERSATION ? turns.find(t => t.msg_id === values[1]) : undefined;
        return { rows: row ? [row] : [] };
      }
      if (text.includes("pg_advisory_xact_lock")) return { rows: [] };
      if (text.includes("from event where")) {
        const hit = events.find(e => e.new.request_msg_id === values[1]);
        return { rows: hit ? [{ id: hit.id }] : [] };
      }
      if (text.includes("ops.mint_notification")) {
        const [source, eventRef, subjectType, subjectRef, reason, severity, deepLink, dedupeKey, slug, bypass, hold] = values;
        const prior = notifications.find(n => n.recipient === slug && n.dedupe_key === dedupeKey);
        if (prior) return { rows: [{ result: { ok: true, minted: false, deduplicated: true, notification_id: prior.id } }] };
        const row = { id: randomUUID(), recipient: slug, source, eventRef, subjectType, subjectRef,
          reason, severity, deep_link: deepLink, dedupe_key: dedupeKey, bypass, hold };
        notifications.push(row);
        return { rows: [{ result: { ok: true, minted: true, notification_id: row.id } }] };
      }
      throw new Error(`unexpected statement: ${text}`);
    },
  };
  const envelopes = new Map();
  const tools = docConversationTools({
    ToolError,
    withEnvelope: async (_c, _actor, verb, args, fn) => {
      const key = `${verb}:${args.idempotency_key}`;
      if (envelopes.has(key)) return envelopes.get(key);
      const value = await fn();
      envelopes.set(key, value);
      return value;
    },
    writeEvent: async (_c, _actor, verb, subjectType, subjectId, fields) => {
      events.push({ id: randomUUID(), verb, subjectType, subjectId, ...fields });
    },
  });
  const complete = args => tools["complete-doc-request"].handler(c, { id: "a" },
    { idempotency_key: randomUUID(), conversation_id: CONVERSATION, ...args });
  return { notifications, events, complete };
}

test("complete-doc-request is an authority-only write on the registry", () => {
  assert.equal(TOOLS["complete-doc-request"].write, true);
  assert.equal(TOOLS["complete-doc-request"].authorityOnly, true);
  assert.ok(!("recipient" in TOOLS["complete-doc-request"].inputSchema.properties));
  assert.equal(TOOLS["complete-doc-request"].inputSchema.additionalProperties, false);
});

test("an answered request notifies the asker once and links to the answer", async () => {
  const s = store();
  const r = await s.complete({ request_msg_id: JOE_ASK, outcome: "answered", answer_msg_id: ANSWER });
  assert.equal(s.notifications.length, 1);
  const n = s.notifications[0];
  assert.equal(n.recipient, "joe");
  assert.equal(n.severity, "completion");
  assert.equal(n.deep_link, `/doc/conversations/${CONVERSATION}/turns/${ANSWER}`);
  assert.match(n.deep_link, /^\/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$/);
  assert.equal(n.subjectRef, JOE_ASK);
  assert.equal(n.bypass, false);
  assert.equal(r.notification_id, n.id);
  assert.equal(r.deep_link, n.deep_link);
});

test("a failed request notifies the asker once", async () => {
  const s = store();
  await s.complete({ request_msg_id: JOE_ASK, outcome: "failed" });
  assert.equal(s.notifications.length, 1);
  assert.equal(s.notifications[0].recipient, "joe");
  assert.equal(s.notifications[0].severity, "failure");
  assert.equal(s.notifications[0].deep_link, `/doc/conversations/${CONVERSATION}/turns/${JOE_ASK}`);
});

test("a refused request notifies the asker once", async () => {
  const s = store();
  await s.complete({ request_msg_id: DELL_ASK, outcome: "refused", answer_msg_id: ANSWER });
  assert.equal(s.notifications.length, 1);
  assert.equal(s.notifications[0].recipient, "dell");
  assert.equal(s.notifications[0].severity, "failure");
});

test("a duplicate completion stays at one notification", async () => {
  const s = store();
  const first = await s.complete({ request_msg_id: JOE_ASK, outcome: "answered", answer_msg_id: ANSWER });
  const retry = await s.complete({ request_msg_id: JOE_ASK, outcome: "answered", answer_msg_id: ANSWER });
  const changed = await s.complete({ request_msg_id: JOE_ASK, outcome: "failed" });
  assert.equal(s.notifications.length, 1);
  assert.equal(first.deduplicated, false);
  assert.equal(retry.deduplicated, true);
  assert.equal(changed.deduplicated, true);
  assert.equal(retry.notification_id, first.notification_id);
  assert.equal(s.events.length, 1);
});

test("a request still in progress has no notification", async () => {
  const s = store();
  assert.equal(s.notifications.length, 0);
  await assert.rejects(() => s.complete({ request_msg_id: ANSWER, outcome: "answered", answer_msg_id: ANSWER }),
    { error: "doc_request_not_found" });
  assert.equal(s.notifications.length, 0);
});

test("the other partner gets nothing", async () => {
  const s = store();
  await s.complete({ request_msg_id: JOE_ASK, outcome: "answered", answer_msg_id: ANSWER });
  assert.deepEqual(s.notifications.map(n => n.recipient), ["joe"]);
  assert.equal(s.notifications.filter(n => n.recipient === "dell").length, 0);
});

test("an answer must follow the request and an answered request must name it", async () => {
  const s = store();
  await assert.rejects(() => s.complete({ request_msg_id: JOE_ASK, outcome: "answered" }),
    { error: "doc_request_answer_required" });
  await assert.rejects(() => s.complete({ request_msg_id: JOE_ASK, outcome: "answered", answer_msg_id: EARLIER_ANSWER }),
    { error: "doc_request_answer_invalid" });
  await assert.rejects(() => s.complete({ request_msg_id: JOE_ASK, outcome: "answered", answer_msg_id: DELL_ASK }),
    { error: "doc_request_answer_invalid" });
  await assert.rejects(() => s.complete({ request_msg_id: JOE_ASK, outcome: "finished" }),
    { error: "doc_request_outcome_invalid" });
  assert.equal(s.notifications.length, 0);
});
