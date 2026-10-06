import assert from "node:assert/strict";
import test from "node:test";
import { formatApprovedMetric, renderTourPacket, TourPacketRenderError } from "../src/tour-packet-render.js";

const refs = {
  alpha: "property:public:AlphaHealthCenter01",
  zeta: "property:public:ZetaMedicalPlaza0001",
};
const packet = Object.freeze({
  as_of: "2026-08-27T12:00:00Z",
  caveat: null,
  properties: [
    { property_ref: refs.zeta, route_sequence: 20, route_label: "B", name: "Zeta Medical Plaza", address: "200 Zeta Way, Pensacola, FL", property_type: "Medical office", size: { value: 4200, unit: "SF" }, availability: "Available" },
    { property_ref: refs.alpha, route_sequence: 10, route_label: "A", name: "Alpha Health Center", address: "100 Alpha Drive, Pensacola, FL", suite: "Suite 120", property_type: "Medical office", asking_economics: { value: "24.00", currency: "USD", period: "NNN" }, availability: "Available", parking: "4.5/1,000 SF" },
  ],
});

test("Tour packet rendering is deterministic in immutable route order with public identity parity", () => {
  const first = renderTourPacket(packet);
  const second = renderTourPacket({ ...packet, properties: [...packet.properties].reverse() });
  assert.equal(first.html, second.html);
  assert.equal(first.propertyCount, 2);
  assert.deepEqual(first.propertyRefs, [refs.alpha, refs.zeta]);
  assert.equal((first.html.match(/data-deliverable-page="property"/g) || []).length, 2);
  assert.equal((first.html.match(/data-property-ref=/g) || []).length, 2);
  assert.ok(first.html.indexOf("Alpha Health Center") < first.html.indexOf("Zeta Medical Plaza"));
  assert.match(first.html, /data-route-sequence="10"/);
  assert.match(first.html, /data-template-version="1\.1\.0"/);
  assert.match(first.html, /#002F6C/);
  assert.match(first.html, /#F57F29/);
  assert.equal((first.html.match(/<main\b/g) || []).length, 1);
  assert.match(first.html, /break-after: page/);
  assert.match(first.html, /page-break-after: always/);
  assert.match(first.html, /height: 9\.9in/);
  assert.doesNotMatch(first.html, /<section[^>]*(?:overview|cover)/i);
});

test("Tour packet preserves allowlisted structured metrics with deterministic formatting", () => {
  const result = renderTourPacket(packet);
  assert.deepEqual(result.facts.properties[0].asking_economics, { value: "24.00", currency: "USD", period: "NNN" });
  assert.deepEqual(result.facts.properties[1].size, { value: 4200, unit: "SF" });
  assert.equal(formatApprovedMetric({ min: 20, max: 25, currency: "USD", unit: "SF", period: "NNN", label: "Rate" }), "Rate: USD 20–25 SF / NNN");
  assert.match(result.html, /USD 24\.00 \/ NNN/);
  assert.match(result.html, /4200 SF/);
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], size: { value: 4200, provider: "private" } }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_field");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], size: { value: { nested: "no" } } }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_text");
});

test("Tour packet refuses any caveat: a per-property caveat key or a non-null packet caveat (V5-J303)", () => {
  for (const caveat of [null, "Broker-only caveat about the roof"])
    assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], caveat }] }),
      error => error instanceof TourPacketRenderError && error.code === "tour_packet_unknown_field");
  assert.throws(() => renderTourPacket({ ...packet, caveat: "Facts are provided for tour planning." }),
    error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_field");
});

test("Tour packet prints no caveat line at all when none is supplied anywhere", () => {
  // Regression for the removed hard-coded "Facts only; verify current
  // availability and economics." boilerplate (migrations/0586): a packet
  // with no top-level caveat and no per-property caveat must render with no
  // caveat text anywhere, not fall back to any default line.
  const noCaveat = { ...packet, caveat: undefined };
  const result = renderTourPacket(noCaveat);
  assert.equal(result.facts.caveat, null);
  for (const property of result.facts.properties) assert.equal("caveat" in property, false);
  assert.doesNotMatch(result.html, /Facts only; verify current availability and economics\./);
  assert.doesNotMatch(result.html, />null</);
  assert.doesNotMatch(result.html, /undefined/);
});

test("Tour packet refuses unsafe facts, duplicate public identity/route order, and overflow", () => {
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], provider: "Private vendor" }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_field");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], availability: "Call agent@example.test" }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_contact");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], name: "A".repeat(361) }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_overflow");
  assert.throws(() => renderTourPacket({ ...packet, as_of: "2026-02-30T12:00:00Z" }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_as_of");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], name: "Unsafe\u0000name" }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_text");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], size: { label: "Size" } }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_metric");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], route_sequence: 10 }, packet.properties[1]] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_duplicate_route_sequence");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], property_ref: refs.alpha }, packet.properties[1]] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_duplicate_property_ref");
});

test("HTML escaping preserves facts as text and cannot become markup", () => {
  const result = renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], name: "Clinic <North> & East" }] });
  assert.match(result.html, /Clinic &lt;North&gt; &amp; East/);
  assert.doesNotMatch(result.html, /<North>/);
});

test("Tour packet accepts the database's timestamptz JSON shape and normalizes it to UTC", () => {
  // ops.read_tour_packet_for_render emits timestamptz through jsonb, which is
  // "+00:00", not "Z". Rejecting it failed every production tour PDF render.
  const plain = renderTourPacket({ ...packet, as_of: "2026-09-24T04:40:00+00:00" });
  assert.equal(plain.facts.as_of, "2026-09-24T04:40:00.000Z");
  const micro = renderTourPacket({ ...packet, as_of: "2026-09-24T04:35:41.571502+00:00" });
  assert.equal(micro.facts.as_of, "2026-09-24T04:35:41.571Z");
  const central = renderTourPacket({ ...packet, as_of: "2026-09-23T23:40:00-05:00" });
  assert.equal(central.facts.as_of, "2026-09-24T04:40:00.000Z");
  assert.equal(renderTourPacket(packet).facts.as_of, "2026-08-27T12:00:00Z");
  for (const bad of ["2026-02-30T12:00:00+00:00", "2026-09-24T04:40:00+24:00", "2026-09-24T04:40:00+0000", "2026-09-24 04:40:00+00:00", "2026-09-24T04:40:00"]) {
    assert.throws(() => renderTourPacket({ ...packet, as_of: bad }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_as_of", bad);
  }
});
