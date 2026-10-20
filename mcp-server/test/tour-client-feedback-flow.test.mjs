import test from "node:test";
import assert from "node:assert/strict";
import { createTourInternalWebHandler } from "../src/tour-internal-web.js";

const PROJECTION_ID = "10000000-0000-4000-8000-000000000001";

test("broker door: feedback scopes need packet access and unknown scopes stay refused", async () => {
  const env = { APP_HOST: "app.doctorcre.com" };
  const session = { key: "opaque", csrfToken: "csrf" };
  const actor = { id: "broker" };
  const seen = [];
  const surface = createTourInternalWebHandler({ issueShareGrantFn: async ({ input }) => { seen.push(input.permission_scopes); return { ok: true, data: { share_grant_id: "66666666-6666-4666-8666-666666666666" } }; } });
  const body = scopes => ({ projection_id: PROJECTION_ID, token_digest: `sha256:${"a".repeat(64)}`, permission_scopes: scopes, expires_at: "2026-10-08T12:00:00.000Z", receipt_digest: `sha256:${"b".repeat(64)}`, idempotency_key: "40000000-0000-4000-8000-000000000001" });
  const post = scopes => surface.fetch(new Request("https://app.doctorcre.com/api/tours/share/issue", { method: "POST", headers: { origin: "https://app.doctorcre.com", "sec-fetch-site": "same-origin", "content-type": "application/json", "x-carr-csrf": "csrf" }, body: JSON.stringify(body(scopes)) }), env, {}, actor, session);
  for (const ok of [["view_packet", "shortlist", "comment"], ["view_packet", "view_map", "shortlist", "comment"], ["view_packet", "comment"], ["view_packet"], ["view_map"]])
    assert.equal((await post(ok)).status, 200, ok.join());
  for (const bad of [["shortlist"], ["comment"], ["view_map", "shortlist"], ["view_packet", "edit_notes"], ["view_packet", "shortlist", "shortlist"], []])
    assert.equal((await post(bad)).status, 400, bad.join());
  assert.equal(seen.length, 5);
});


test("broker feedback route is GET only, authenticated, one projection id, and coarse on failure", async () => {
  const env = { APP_HOST: "app.doctorcre.com" };
  const session = { key: "opaque", csrfToken: "csrf" };
  const surface = createTourInternalWebHandler({ readFeedbackFn: async () => { throw new Error("connect ECONNRESET db.internal.example:5432"); } });
  const url = suffix => new Request(`https://app.doctorcre.com/api/tours/feedback${suffix}`);
  assert.equal((await surface.fetch(url(`?projection_id=${PROJECTION_ID}`), env, {}, undefined, undefined)).status, 401);
  assert.equal((await surface.fetch(url(""), env, {}, { id: "b" }, session)).status, 400);
  assert.equal((await surface.fetch(url(`?projection_id=${PROJECTION_ID}&tenant_id=x`), env, {}, { id: "b" }, session)).status, 400);
  assert.equal((await surface.fetch(new Request("https://app.doctorcre.com/api/tours/feedback", { method: "POST" }), env, {}, { id: "b" }, session)).status, 405);
  const failed = await surface.fetch(url(`?projection_id=${PROJECTION_ID}`), env, {}, { id: "b" }, session);
  assert.equal(failed.status, 503);
  assert.doesNotMatch(await failed.text(), /ECONNRESET|db\.internal/);
});
