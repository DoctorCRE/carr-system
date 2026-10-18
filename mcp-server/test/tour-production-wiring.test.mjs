import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { TOOLS } from "../src/tools.js";
import { projectTourDetail, projectTourLibrary, runTourPdfRender } from "../src/tour-runtime.js";

const root = path.resolve(import.meta.dirname, "../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");

test("production tool registry contains every Tour delivery verb", () => {
  for (const name of [
    "register-tour-property", "search-tour-properties", "append-tour-selection-cart-version", "read-tour-selection-cart",
    "prepare-tour-route-version", "record-tour-map-promotion-receipt", "issue-tour-share-grant", "rotate-tour-share-grant",
    "revoke-tour-share-grant", "read-tour-sharing-library", "request-tour-pdf-render",
    "read-tour-pdf-render", "record-tour-pdf-render-result", "record-tour-pdf-human-review",
  ]) assert.ok(TOOLS[name], name);
});

test("Worker production router mounts authenticated Tours and public reports", () => {
  const index = read("mcp-server/src/index.js");
  const dealroom = read("mcp-server/src/dealroom-web.js");
  assert.match(index, /createTourInternalWebHandler\(createTourRuntimeAdapters\(\)\)/);
  assert.match(index, /createReportsWebHandler\(createReportsRuntimeAdapters\(\)\)/);
  assert.match(index, /isReportsHostRequest\(request, env\)[\s\S]*reportsHandler\.fetch/);
  assert.ok(index.indexOf("isReportsHostRequest(request, env)") < index.indexOf('url.pathname === "/mcp"'), "reports host must close before machine/OAuth routing");
  assert.match(index, /tourHandler:\s*tourInternalHandler/);
  assert.match(dealroom, /isTourInternalRequest\(request\)[\s\S]*tourHandler\.fetch/);
  assert.match(dealroom, /"\/api\/tours\/"/);
  assert.match(dealroom, /"\/tours\/"/);
});

test("production Tour runtime preserves tool envelopes and digest-only public access", () => {
  const runtime = read("mcp-server/src/tour-runtime.js");
  assert.match(runtime, /authorization_class:\s*actor\?\.authorization_class \|\| authorizationClassForActor\(actor\)/);
  assert.match(runtime, /callTool\(toolEnvironment\(env, ctx\), runtimeActor, verb, args\)/);
  assert.doesNotMatch(runtime, /\{ \.\.\.env\b/, "a spread drops Deal Room's prototype-held bindings");
  assert.match(runtime, /tourSharingBrowserAccess/);
  assert.match(runtime, /sharing\.exchange/);
  assert.match(runtime, /sharing\.readPacket/);
  assert.match(runtime, /sharing\.readMap/);
  assert.match(runtime, /read_tour_projection_creation_metadata/);
  assert.match(runtime, /derivedIdempotencyUuid\("tour-pdf-render-result", context\.input\.idempotency_key\)/);
  assert.doesNotMatch(runtime, /record-tour-pdf-render-result[\s\S]{0,300}crypto\.randomUUID\(\)/);
  assert.doesNotMatch(runtime, /from ops\.tour_public_projection/i);
  assert.doesNotMatch(runtime, /raw_token|plaintext_token|authorization\s*:/i);
});

test("trusted PDF orchestration records a terminal failure after a queued job", async () => {
  const digest = character => `sha256:${character.repeat(64)}`;
  const jobId = "20000000-0000-4000-8000-000000000001";
  const calls = [];
  const result = await runTourPdfRender({
    env: {}, actor: { slug: "codex" },
    input: { projection_id: "10000000-0000-4000-8000-000000000001", idempotency_key: "40000000-0000-4000-8000-000000000001" },
  }, {
    tenantFn: () => "carr-internal",
    internalReadFn: async () => ({ packet: { properties: [{}] }, projection_digest: digest("1") }),
    prepareTourPdfArtifactFn: async () => ({
      packetDigest: digest("2"), templateDigest: digest("3"), rendererDigest: digest("4"),
      qcRulesetVersion: "1.0.0", qcRulesetDigest: digest("5"), markersDigest: digest("6"),
      rendered: { templateVersion: "1.0.0", rendererVersion: "1.0.0", propertyCount: 1, fontDigests: [digest("7")], artifactDigest: digest("8") },
    }),
    storeAndVerifyTourPdfFn: async () => { throw new Error("private storage detail"); },
    invokeFn: async (_context, verb, args) => {
      calls.push({ verb, args });
      if (verb === "request-tour-pdf-render") return { ok: true, data: { render_job_id: jobId } };
      return { ok: true, data: { status: args.status } };
    },
  });
  assert.deepEqual(result, { ok: false, status: 500, data: { render_job_id: jobId, status: "failed", phase: "store" } });
  assert.deepEqual(calls.map(call => call.verb), ["request-tour-pdf-render", "record-tour-pdf-render-result"]);
  const failure = calls[1].args;
  assert.equal(failure.status, "failed");
  assert.equal(failure.artifact_ref, null); assert.equal(failure.storage_ref, null);
  assert.match(failure.qc_run_digest, /^sha256:[0-9a-f]{64}$/);
  assert.doesNotMatch(JSON.stringify(failure), /private storage detail/);
});

test("production Tour runtime presents the exact browser view without promoting drafts", () => {
  assert.deepEqual(projectTourLibrary({ tours: [{ id: "tour", tour_name: "Bay County", tour_status: "draft" }] }), {
    tours: [{ id: "tour", tour_name: "Bay County", tour_status: "draft", name: "Bay County", status: "draft" }],
  });
  const detail = projectTourDetail({
    id: "tour", tour_name: "Bay County", tour_status: "draft", route_version: 1,
    routes: [
      { id: "draft-route", route_version: 2, accepted: false, acceptance_digest: `sha256:${"a".repeat(64)}`, stops: [{ id: "stop", route_sequence: 1, route_label: "A", property_name: "Alpha Clinic", property_address: "100 Main St" }] },
      { id: "accepted-route", route_version: 1, accepted: true, stops: [] },
    ],
    projections: [{ id: "draft-projection", status: "draft" }, { id: "approved-projection", status: "approved" }],
    shares: [
      { share_grant_id: "revoked-share", projection_id: "approved-projection", status: "revoked" },
      { share_grant_id: "active-share", projection_id: "approved-projection", status: "active" },
    ],
  });
  assert.equal(detail.name, "Bay County");
  assert.equal(detail.route_version_id, "draft-route");
  assert.equal(detail.route_acceptance_digest, `sha256:${"a".repeat(64)}`);
  assert.equal(detail.route_version_state, "draft");
  assert.equal(detail.accepted_route_version, 1);
  assert.equal(detail.stops[0].label, "A · Alpha Clinic");
  assert.equal(detail.stops[0].address, "100 Main St");
  assert.equal(detail.projection_id, "approved-projection", "draft projections never become share authority");
  assert.equal(detail.share_grant_id, "active-share");
});

test("expired sharing projection retains the immutable grant ID for rotation", () => {
  const detail = projectTourDetail({
    tour_name: "Escambia", tour_status: "draft", routes: [],
    projections: [{ id: "approved-projection", status: "approved" }],
    shares: [{ share_grant_id: "expired-share", projection_id: "approved-projection", status: "expired" }],
  });
  assert.equal(detail.share_grant_id, "expired-share");
  assert.equal(detail.share_status, "expired");
});

test("prior active links remain manageable when a newer projection is approved", () => {
  const detail = projectTourDetail({
    routes: [],
    projections: [{ id: "projection-v2", status: "approved" }, { id: "projection-v1", status: "approved" }],
    shares: [{ share_grant_id: "active-v1", projection_id: "projection-v1", status: "active", expires_at: "2026-09-01T00:00:00Z", permission_scopes: ["view_packet"] }],
  });
  assert.equal(detail.share_grant_id, null, "a prior grant cannot be rotated onto the current projection");
  assert.deepEqual(detail.share_grants, [{
    share_grant_id: "active-v1", projection_id: "projection-v1", status: "active",
    expires_at: "2026-09-01T00:00:00Z", grant_version: undefined, permission_scopes: ["view_packet"],
  }]);
});

test("a PDF from an older projection is never presented as the current packet", () => {
  const detail = projectTourDetail({
    routes: [], projections: [{ id: "projection-v2", status: "approved" }], shares: [],
    pdf_render: { render_job_id: "pdf-v1", projection_id: "projection-v1", status: "available", qc_run_digest: `sha256:${"a".repeat(64)}` },
  });
  assert.equal(detail.pdf_render_job_id, null);
  assert.equal(detail.pdf_status, "missing");
});
