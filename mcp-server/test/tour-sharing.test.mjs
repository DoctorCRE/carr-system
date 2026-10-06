import assert from "node:assert/strict";
import test from "node:test";
import {
  projectTourClientMap, projectTourClientPacket, projectTourPublicAsset,
  tourSharingBrowserAccess, tourSharingTools,
} from "../src/tour-sharing.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const actor = { id: "actor-00000000-0000-4000-8000-000000000001", slug: "codex" };
const ids = {
  projection: "10000000-0000-4000-8000-000000000001",
  grant: "20000000-0000-4000-8000-000000000001",
  successor: "30000000-0000-4000-8000-000000000001",
};
const idem = "60000000-0000-4000-8000-000000000001";
const tokenDigest = `sha256:${"a".repeat(64)}`;
const sessionDigest = `sha256:${"b".repeat(64)}`;
const receiptDigest = `sha256:${"c".repeat(64)}`;
const auditDigest = `sha256:${"d".repeat(64)}`;
const propertyRef = "property:public:abcdefghijklmnop";
const packetFixture = {
  tour_name: "Tour", summary: "Two stops", provider: "private", allow_comments: true,
  stops: [{ name: "Medical Plaza", address: "100 Clinic Way", suite: "Suite 200", property_ref: propertyRef,
    route_sequence: 1, route_label: "A", access_notes: "private", latest_reaction: "interested",
    size: { value: 1200, unit: "sf" },
    photos: [{ asset_ref: "asset:public:abcdefghijklmnop", alt: "Front", source: "provider" }] }],
};
const mapFixture = { as_of: "2026-08-27T00:00:00Z", points: [{ latitude: 30.1, longitude: -87.2,
  property_ref: propertyRef, route_sequence: 1, route_label: "A", verifier: "private" }] };

function harness() {
  const calls = [], events = [], envelopes = [], replays = new Map();
  const client = { async query(sql, params) {
    calls.push({ sql, params });
    if (sql.includes("issue_tour_share_grant")) return { rows: [{ share_grant_id: ids.grant, private: "row" }] };
    if (sql.includes("rotate_tour_share_grant")) return { rows: [{ share_grant_id: ids.successor }] };
    if (sql.includes("revoke_tour_share_grant")) return { rows: [{ share_grant_id: ids.grant }] };
    if (sql.includes("exchange_tour_share_token")) return { rows: [{ exchange: { expires_at: "2026-08-28T00:00:00Z", permission_scopes: ["view_packet", "view_map", "comment"] } }] };
    if (sql.includes("read_tour_share_packet")) return { rows: [{ packet: packetFixture }] };
    if (sql.includes("read_tour_share_map")) return { rows: [{ map: mapFixture }] };
    if (sql.includes("resolve_tour_public_asset")) return { rows: [{ asset: { media_type: "image/jpeg", content_length: 12, provider_url: "private" } }] };
    if (sql.includes("read_tour_sharing_library")) return { rows: [{ library: { grants: [{ share_grant_id: ids.grant, status: "active", permission_scopes: ["view_packet", "comment"], expires_at: "2026-08-28T00:00:00Z", provider: "private" }] } }] };
    if (sql.includes("read_tour_sharing_interactions")) return { rows: [{ interactions: { items: [] } }] };
    throw new Error(sql);
  } };
  const withEnvelope = async (c, a, verb, args, fn) => {
    assert.equal(c, client); assert.equal(a, actor);
    const key = `${verb}:${args.idempotency_key}`;
    if (replays.has(key)) return replays.get(key);
    const result = await fn(); envelopes.push({ verb, result }); replays.set(key, result); return result;
  };
  return { client, calls, events, envelopes,
    tools: tourSharingTools({ withEnvelope, writeEvent: async (...event) => events.push(event), ToolError }),
    browser: tourSharingBrowserAccess({ ToolError }) };
}

test("MCP factory excludes public session paths and keeps only authority/internal reads", () => {
  const tools = harness().tools;
  assert.deepEqual(Object.keys(tools).sort(), ["issue-tour-share-grant", "read-tour-feedback", "read-tour-sharing-library", "revoke-tour-share-grant", "rotate-tour-share-grant"]);
  for (const name of ["issue-tour-share-grant", "rotate-tour-share-grant", "revoke-tour-share-grant"])
    assert.equal(tools[name].authorityOnly, true);
  assert.equal(tools["read-tour-sharing-library"].writerConnection, true);
  assert.equal(tools["read-tour-sharing-library"].inputSchema.properties.cursor.pattern, "^[0-9]{1,9}$");
});

test("share authority lifecycle permits narrow feedback scopes and rejects unrelated writes", async () => {
  const h = harness();
  const grant = { idempotency_key: idem, projection_id: ids.projection, token_digest: tokenDigest,
    permission_scopes: ["view_packet", "view_map"], expires_at: "2026-08-28T00:00:00Z", receipt_digest: receiptDigest };
  const first = await h.tools["issue-tour-share-grant"].handler(h.client, actor, grant);
  const replay = await h.tools["issue-tour-share-grant"].handler(h.client, actor, grant);
  assert.deepEqual(first, { ok: true, share_grant_id: ids.grant, status: "active" });
  assert.deepEqual(replay, first);
  assert.equal(h.calls.filter(call => call.sql.includes("issue_tour_share_grant")).length, 1);
  assert.equal(h.events.length, 1);
  await assert.rejects(
    h.tools["issue-tour-share-grant"].handler(h.client, actor, { ...grant, idempotency_key: "60000000-0000-4000-8000-000000000002", permission_scopes: ["view_packet", "edit_cheat_sheet"] }),
    error => error instanceof ToolError && error.payload.error === "tour_share_scope_invalid",
  );
});

test("browser exchange and reads are digest-only and expose narrow feedback authority", async () => {
  const h = harness();
  assert.deepEqual(Object.keys(h.browser).sort(), ["comment", "exchange", "readFeedback", "readMap", "readPacket", "resolveAsset", "shortlist"]);
  const exchange = await h.browser.exchange(h.client, { token_digest: tokenDigest, session_digest: sessionDigest,
    session_expires_at: "2026-08-28T00:00:00Z", audit_digest: auditDigest });
  assert.deepEqual(exchange, { ok: true, expires_at: "2026-08-28T00:00:00Z", permission_scopes: ["view_packet", "view_map", "comment"] });
  const packet = await h.browser.readPacket(h.client, { session_digest: sessionDigest });
  const map = await h.browser.readMap(h.client, { session_digest: sessionDigest });
  const asset = await h.browser.resolveAsset(h.client, { session_digest: sessionDigest, asset_ref: "asset:public:abcdefghijklmnop" });
  assert.equal(packet.packet.allow_comments, undefined);
  assert.equal(packet.packet.tour_name, undefined);
  assert.equal(packet.packet.summary, undefined);
  assert.equal(packet.packet.stops[0].latest_reaction, undefined);
  assert.equal(map.map.points[0].verifier, undefined);
  assert.deepEqual(asset.asset, { asset_ref: "asset:public:abcdefghijklmnop", media_type: "image/jpeg", content_length: 12 });
});

test("public and internal projections strip secrets and unsupported scopes", async () => {
  const packet = projectTourClientPacket(packetFixture);
  assert.equal(packet.tour_name, undefined);
  assert.equal(packet.summary, undefined);
  assert.equal(packet.allow_comments, undefined);
  assert.equal(packet.stops[0].access_notes, undefined);
  assert.equal(packet.stops[0].suite, "Suite 200");
  assert.deepEqual(packet.stops[0].size, { value: 1200, unit: "sf" });
  // A metric carrying any key outside the client metric shape is refused (the
  // database value-safety rule refuses the same object), never trimmed, and
  // the refusal takes the whole packet, as the database and the PDF do.
  assert.equal(projectTourClientPacket({ ...packetFixture, stops: [{ ...packetFixture.stops[0], size: { value: 1200, unit: "sf", verifier: "no" } }] }), null);
  assert.equal(packet.stops[0].latest_reaction, undefined);
  assert.deepEqual(projectTourPublicAsset({ media_type: "image/jpeg", provider: "private" }, "asset:public:abcdefghijklmnop"),
    { asset_ref: "asset:public:abcdefghijklmnop", media_type: "image/jpeg" });
  const h = harness();
  const library = await h.tools["read-tour-sharing-library"].handler(h.client, actor, { projection_id: ids.projection, cursor: null, limit: 10 });
  assert.deepEqual(library.library.grants, [{ share_grant_id: ids.grant, status: "active", permission_scopes: ["view_packet", "comment"], expires_at: "2026-08-28T00:00:00Z" }]);
  assert.doesNotMatch(JSON.stringify(library), /provider|token_digest|session_digest|r2_key|rights|evidence/);
});

test("public projections require bounded opaque property identity and valid map coordinates", () => {
  assert.equal(projectTourClientPacket(packetFixture).stops.length, 1);
  // One bad stop or point refuses the whole share: the list and the map never
  // show a client a different set of stops.
  assert.equal(projectTourClientPacket({ ...packetFixture, stops: [{ ...packetFixture.stops[0], route_sequence: 0 }] }), null);
  assert.equal(projectTourClientMap({ ...mapFixture, points: [{ ...mapFixture.points[0], latitude: 91 }, mapFixture.points[0]] }), null);
  assert.equal(projectTourClientMap(mapFixture).points.length, 1);
});
