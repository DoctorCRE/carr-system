import assert from "node:assert/strict";
import test from "node:test";
import { tourArtifactTools, trustedTourRendererResult } from "../src/tour-artifacts.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "codex" };
const humanActor = { id: "actor-00000000-0000-4000-8000-000000000002", slug: "joe", human: true };
const ids = { projection: "10000000-0000-4000-8000-000000000001", job: "20000000-0000-4000-8000-000000000001", result: "25000000-0000-4000-8000-000000000001", review: "30000000-0000-4000-8000-000000000001", idempotency: "40000000-0000-4000-8000-000000000001" };
const digest = character => `sha256:${character.repeat(64)}`;

function harness(expectedActor = actor) {
  const calls = [], events = [], envelopes = [];
  const client = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes("request_tour_pdf_render")) return { rows: [{ render_job_id: ids.job }] };
    if (sql.includes("record_tour_pdf_render_result")) return { rows: [{ render_result_id: ids.result }] };
    if (sql.includes("read_tour_pdf_render")) return { rows: [{ render: { render_job_id: ids.job, status: "review_ready", artifact_ref: "artifact:tour-pdf:abcdefghijklmnop", artifact_digest: digest("a"), projection_digest: digest("b"), page_count: 2, expected_property_count: 2, blocking_finding_count: 0, human_review_state: "pending", r2_key: "private", lease_digest: digest("c"), token_digest: digest("d") } }] };
    if (sql.includes("record_tour_pdf_human_review")) return { rows: [{ review_receipt_id: ids.review }] };
    throw new Error(sql);
  } };
  const withEnvelope = async (c, a, verb, args, fn) => { assert.equal(c, client); assert.equal(a, expectedActor); envelopes.push({ verb, args }); return fn(); };
  return { client, calls, events, envelopes, tools: tourArtifactTools({ withEnvelope, writeEvent: async (...args) => events.push(args), ToolError }) };
}

const request = {
  idempotency_key: ids.idempotency, projection_id: ids.projection,
  projection_digest: digest("a"), packet_digest: digest("b"), template_version: "1.0.0", template_digest: digest("c"),
  renderer_version: "1.1.0", renderer_digest: digest("d"), qc_ruleset_version: "1.1.0", qc_ruleset_digest: digest("e"),
  expected_property_count: 2, expected_markers_digest: digest("f"), expected_asset_digests: [digest("0")], expected_font_digests: [digest("1")],
};

test("render request binds every immutable input and cannot approve or publish", async () => {
  const h = harness();
  assert.deepEqual(Object.keys(h.tools).sort(), ["read-tour-pdf-render", "record-tour-pdf-human-review", "record-tour-pdf-render-result", "request-tour-pdf-render"]);
  assert.equal(h.tools["record-tour-pdf-render-result"].authorityOnly, true);
  assert.equal(h.tools["record-tour-pdf-human-review"].authorityOnly, true);
  assert.deepEqual(await h.tools["request-tour-pdf-render"].handler(h.client, actor, request), { ok: true, render_job_id: ids.job, status: "queued" });
  assert.deepEqual(h.calls[0].params.slice(0, 2), ["carr-internal", actor.id]);
  const { idempotency_key: _idempotencyKey, ...expectedRequest } = request;
  assert.deepEqual(JSON.parse(h.calls[0].params[2]), expectedRequest);
  assert.equal(h.events.length, 1); assert.doesNotMatch(JSON.stringify(h.events), /asset_digests|font_digests|packet_digest/);
  assert.equal(Object.hasOwn(h.tools, "publish-tour-pdf"), false); assert.equal(Object.hasOwn(h.tools, "approve-tour-pdf"), false);
});

test("renderer result records exact R2/QC evidence without granting approval", async () => {
  const h = harness();
  const result = {
    idempotency_key: ids.idempotency, render_job_id: ids.job, status: "review_ready",
    artifact_ref: "artifact:tour-pdf:abcdefghijklmnop", artifact_digest: digest("4"),
    storage_ref: `tour-pdf/carr-internal/${ids.job}/artifact.pdf`, content_length: 12345,
    page_count: 2, blocking_finding_count: 0, qc_run_digest: digest("5"),
  };
  assert.deepEqual(await h.tools["record-tour-pdf-render-result"].handler(h.client, actor, trustedTourRendererResult(result)), { ok: true, render_result_id: ids.result, render_job_id: ids.job, status: "review_ready" });
  assert.match(h.calls[0].sql, /record_tour_pdf_render_result/);
  assert.equal(h.events.length, 1);
  assert.doesNotMatch(JSON.stringify(h.events), /storage_ref/);
  assert.equal(Object.hasOwn(result, "decision"), false);
});

test("renderer result refuses caller-fabricated receipts and accepts terminal trusted failures", async () => {
  const h = harness();
  const success = {
    idempotency_key: ids.idempotency, render_job_id: ids.job, status: "review_ready",
    artifact_ref: "artifact:tour-pdf:abcdefghijklmnop", artifact_digest: digest("4"),
    storage_ref: `tour-pdf/carr-internal/${ids.job}/artifact.pdf`, content_length: 12345,
    page_count: 2, blocking_finding_count: 0, qc_run_digest: digest("5"),
  };
  await assert.rejects(h.tools["record-tour-pdf-render-result"].handler(h.client, actor, success), error => error?.payload?.error === "tour_trusted_renderer_required");
  await assert.rejects(h.tools["record-tour-pdf-render-result"].handler(h.client, actor, JSON.parse(JSON.stringify(trustedTourRendererResult(success)))), error => error?.payload?.error === "tour_trusted_renderer_required");
  const failed = trustedTourRendererResult({
    ...success, status: "failed", artifact_ref: null, artifact_digest: null,
    storage_ref: null, content_length: null, page_count: null, blocking_finding_count: 0,
  });
  assert.deepEqual(await h.tools["record-tour-pdf-render-result"].handler(h.client, actor, failed), { ok: true, render_result_id: ids.result, render_job_id: ids.job, status: "failed" });
  assert.deepEqual(h.calls.at(-1).params.slice(2, 9), ["failed", null, null, null, null, null, 0]);
});

test("render request refuses unpinned, duplicate, oversized, and caller-authority inputs", async () => {
  for (const input of [
    { ...request, template_version: "latest" },
    { ...request, expected_property_count: 51 },
    { ...request, expected_font_digests: [] },
    { ...request, expected_asset_digests: [digest("0"), digest("0")] },
    { ...request, reviewer: "chosen-by-caller" },
  ]) {
    const h = harness();
    await assert.rejects(h.tools["request-tour-pdf-render"].handler(h.client, actor, input), error => error instanceof ToolError);
    assert.equal(h.calls.length, 0);
  }
});

test("render reads are sanitized and human acceptance is a separate receipt, never publication", async () => {
  const h = harness(humanActor);
  const read = await h.tools["read-tour-pdf-render"].handler(h.client, humanActor, { render_job_id: ids.job });
  assert.equal(read.render.status, "review_ready"); assert.equal(read.render.artifact_ref, "artifact:tour-pdf:abcdefghijklmnop");
  assert.doesNotMatch(JSON.stringify(read), /r2_key|lease_digest|token_digest/);
  const review = { idempotency_key: ids.idempotency, render_job_id: ids.job, qc_run_digest: digest("2"), decision: "accept", reviewed_at: "2026-08-27T12:00:00Z", review_receipt_digest: digest("3"), reason: "QC proof reviewed" };
  assert.deepEqual(await h.tools["record-tour-pdf-human-review"].handler(h.client, humanActor, review), { ok: true, review_receipt_id: ids.review, decision: "accept" });
  assert.equal(h.events.length, 1); assert.doesNotMatch(JSON.stringify(h.events), /review_receipt_digest|qc_run_digest/);
  assert.equal(Object.hasOwn(review, "publish"), false);
});

test("PDF acceptance requires an authenticated verified human, not sponsored authority alone", async () => {
  const h = harness();
  const review = { idempotency_key: ids.idempotency, render_job_id: ids.job, qc_run_digest: digest("2"), decision: "accept", reviewed_at: "2026-08-27T12:00:00Z", review_receipt_digest: digest("3"), reason: "QC proof reviewed" };
  await assert.rejects(h.tools["record-tour-pdf-human-review"].handler(h.client, actor, review), error => error?.payload?.error === "tour_verified_human_required");
  assert.equal(h.calls.length, 0);
  assert.equal(h.tools["record-tour-pdf-human-review"].humanOnly, true);
});

test("human review refuses malformed decisions, timestamps, receipts, and authority selectors before SQL", async () => {
  const base = { idempotency_key: ids.idempotency, render_job_id: ids.job, qc_run_digest: digest("2"), decision: "reject", reviewed_at: "2026-08-27T12:00:00Z", review_receipt_digest: digest("3"), reason: "Layout mismatch" };
  for (const input of [{ ...base, decision: "publish" }, { ...base, reviewed_at: "bad" }, { ...base, review_receipt_digest: "bad" }, { ...base, actor_id: "caller" }]) {
    const h = harness(humanActor);
    await assert.rejects(h.tools["record-tour-pdf-human-review"].handler(h.client, humanActor, input), error => error instanceof ToolError);
    assert.equal(h.calls.length, 0);
  }
});
