import { readRegistryArtifact } from '../../ops/registry-history.mjs';
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import test from "node:test";
import { tourSharingBrowserAccess, tourSharingTools } from "../src/tour-sharing.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const root = path.resolve(import.meta.dirname, "../..");
const projection = "10000000-0000-4000-8000-000000000001";
const session = `sha256:${"a".repeat(64)}`;
const projectionRef = `projection:public:${"b".repeat(32)}`;
const propertyRef = `property:public:${"c".repeat(32)}`;
const key = "20000000-0000-4000-8000-000000000001";
const actor = { id: "broker", organization_tenant_id: "tenant-one" };

function fixture() {
  const calls = [];
  const client = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes("write_tour_share_shortlist")) return { rows: [{ feedback: { saved: true, private: "secret" } }] };
    if (sql.includes("write_tour_share_comment")) return { rows: [{ feedback: { saved: true, private: "secret" } }] };
    if (sql.includes("read_tour_share_feedback")) return { rows: [{ feedback: { projection_ref: projectionRef, permission_scopes: ["shortlist", "comment", "edit_cheat_sheet"], items: [{ property_ref: propertyRef, shortlisted: true, comments: [{ comment_ref: `comment:public:${"d".repeat(32)}`, comment: "Good frontage", created_at: "2026-09-29T12:00:00Z", broker_notes: "private" }] }] } }] };
    if (sql.includes("read_tour_feedback")) return { rows: [{ feedback: { projection_id: projection, items: [{ property_ref: propertyRef, shortlisted: true, comments: [{ comment_ref: `comment:public:${"d".repeat(32)}`, comment: "Good frontage", created_at: "2026-09-29T12:00:00Z" }], broker_notes: "private" }] } }] };
    throw new Error(sql);
  } };
  return { calls, client, browser: tourSharingBrowserAccess({ ToolError }), tools: tourSharingTools({ ToolError, withEnvelope: async (_c, _a, _v, _x, fn) => fn(), writeEvent: async () => {} }) };
}

test("public feedback writes are digest-only, projection-bound and response-allowlisted", async () => {
  const h = fixture();
  const shortlist = await h.browser.shortlist(h.client, { session_digest: session, projection_ref: projectionRef, property_ref: propertyRef, shortlisted: true, idempotency_key: key });
  const comment = await h.browser.comment(h.client, { session_digest: session, projection_ref: projectionRef, property_ref: propertyRef, comment: "Good frontage", idempotency_key: key });
  assert.deepEqual(shortlist, { ok: true, feedback: { saved: true } });
  assert.deepEqual(comment, { ok: true, feedback: { saved: true } });
  assert.doesNotMatch(JSON.stringify([shortlist, comment]), /private|secret|projection_id|share_grant_id/);
  assert.deepEqual(h.calls[0].params, [session, projectionRef, propertyRef, true, key]);
  assert.deepEqual(h.calls[1].params, [session, projectionRef, propertyRef, "Good frontage", key]);
  await assert.rejects(h.browser.comment(h.client, { session_digest: session, projection_ref: projectionRef, property_ref: projection, comment: "x", idempotency_key: key }), /tour_input_invalid/);
  await assert.rejects(h.browser.shortlist(h.client, { session_digest: session, projection_ref: projectionRef, property_ref: propertyRef, shortlisted: true, idempotency_key: key, actor_id: "forged" }), /caller_authority_field_forbidden/);
  assert.equal(h.calls.length, 2);
});

test("client read excludes broker material and broker read is actor/tenant bound", async () => {
  const h = fixture();
  const client = await h.browser.readFeedback(h.client, { session_digest: session });
  assert.deepEqual(client.feedback.permission_scopes, ["shortlist", "comment"]);
  assert.deepEqual(client.feedback.items, [{ property_ref: propertyRef }]);
  assert.doesNotMatch(JSON.stringify(client.feedback), /"comments"|"shortlisted"|broker_notes|Good frontage/);
  const broker = await h.tools["read-tour-feedback"].handler(h.client, actor, { projection_id: projection, cursor: null, limit: 20 });
  assert.equal(broker.feedback.items[0].broker_notes, undefined);
  assert.deepEqual(h.calls.at(-1).params, ["carr-internal", projection, "broker", null, 20]);
});

test("broker read preserves no shortlist answer, explicit no, and yes", async () => {
  for (const shortlisted of [null, false, true]) {
    const client = { async query() { return { rows: [{ feedback: {
      projection_id: projection,
      items: [{ property_ref: propertyRef, route_label: "A", shortlisted, comments: [] }],
    } }] }; } };
    const tools = tourSharingTools({ ToolError, withEnvelope: async (_c, _a, _v, _x, fn) => fn(), writeEvent: async () => {} });
    const result = await tools["read-tour-feedback"].handler(client, actor, { projection_id: projection, cursor: null, limit: 20 });
    assert.equal(result.feedback.items[0].shortlisted, shortlisted);
  }
});

test("broker feedback distinguishes removed projection from unavailable read", async () => {
  const tools = tourSharingTools({ ToolError, withEnvelope: async (_c, _a, _v, _x, fn) => fn(), writeEvent: async () => {} });
  const args = { projection_id: projection, cursor: null, limit: 20 };
  const removed = { async query() { return { rows: [{ feedback: null }] }; } };
  await assert.rejects(tools["read-tour-feedback"].handler(removed, actor, args),
    error => error instanceof ToolError && error.payload.error === "tour_feedback_not_found");
  const unavailable = { async query() { throw new Error("feedback read unavailable"); } };
  await assert.rejects(tools["read-tour-feedback"].handler(unavailable, actor, args), /feedback read unavailable/);
});

test("migration binds feedback to sealed current projection, member property, active grant and idempotent request", () => {
  const migration = readRegistryArtifact(path.join(root, "migrations/0749_tour_client_feedback.sql"), "utf8");
  for (const name of ["write_tour_share_shortlist", "write_tour_share_comment", "read_tour_share_feedback", "read_tour_feedback"])
    assert.match(migration, new RegExp(`create (?:or replace )?function ops\\.${name}\\(`, "i"));
  assert.match(migration, /tour_share_session_grant\(p_session_digest,'shortlist'\)/);
  assert.match(migration, /tour_share_session_grant\(p_session_digest,'comment'\)/);
  assert.match(migration, /tour_public_projection_client_safe/);
  assert.match(migration, /tour_property_membership/);
  assert.match(migration, /rotated_from_grant_id|tour_share_grant successor/);
  assert.match(migration, /idempotency_key/);
  assert.match(migration, /unique \(organization_tenant_id,share_grant_id,idempotency_key\)/);
  assert.doesNotMatch(migration, /update ops\.tour_property|update ops\.tour_field_assertion/i);
});
