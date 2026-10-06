import assert from "node:assert/strict";
import test from "node:test";
import { inspectDeliverable, GENERIC_DELIVERABLE_QC_ADAPTER } from "../src/deliverable-qc.js";
import { renderTourPacket } from "../src/tour-packet-render.js";

const refs = {
  alpha: "property:public:AlphaHealthCenter01",
  zeta: "property:public:ZetaMedicalPlaza0001",
};
const facts = {
  as_of: "2026-08-27T12:00:00Z",
  caveat: null,
  properties: [
    { property_ref: refs.zeta, route_sequence: 20, route_label: "B", name: "Zeta Medical Plaza", address: "200 Zeta Way", availability: "Available" },
    { property_ref: refs.alpha, route_sequence: 10, route_label: "A", name: "Alpha Health Center", address: "100 Alpha Drive", availability: "Available" },
  ],
};

test("Tour QC verifies per-page public identity/fact parity and remains review-only when clear", () => {
  const rendered = renderTourPacket(facts);
  const result = inspectDeliverable({ artifactType: "tour-packet", html: rendered.html, facts: rendered.facts, expected: { markers: rendered.markers, pageCount: rendered.propertyCount } });
  assert.equal(result.blocked, false);
  assert.equal(result.disposition, "review_required");
  assert.equal(result.canApprove, false);
  assert.equal(result.canPublish, false);
  assert.equal(result.canSelfDismiss, false);
  assert.equal(result.findings.length, 0);
});

test("Tour QC seeded negatives block leakage, unsafe URLs, duplicate public refs, and swapped page facts", () => {
  const rendered = renderTourPacket(facts);
  // No caveat to strip: a client Tour packet carries none (V5-J303), so
  // QC-FACT-002 (supplied caveat missing) has nothing to fire on here.
  const bad = rendered.html
    .replace(refs.zeta, refs.alpha)
    .replace("100 Alpha Drive", "temporary-address")
    .replace("200 Zeta Way", "100 Alpha Drive")
    .replace("temporary-address", "200 Zeta Way")
    .replace(rendered.markers[1], rendered.markers[0])
    .replace("</body>", "<a href=\"javascript:alert(1)\">provider evidence contact@example.test</a><section data-deliverable-page='cover'></section></body>");
  const result = inspectDeliverable({ artifactType: "tour-packet", html: bad, facts: rendered.facts, expected: { markers: rendered.markers, pageCount: rendered.propertyCount } });
  assert.equal(result.blocked, true);
  const rules = new Set(result.findings.map(item => item.ruleId));
  for (const rule of ["QC-LEAKAGE-001", "QC-URL-001", "QC-PAGE-002", "QC-PAGE-003", "QC-PARITY-002", "QC-IDENTITY-001"]) assert.equal(rules.has(rule), true, rule);
});

test("generic non-Tour adapter applies the same versioned QC without Tour coupling", () => {
  const briefFacts = { as_of: "2026-08-27T12:00:00Z", caveat: "Draft facts remain subject to confirmation.", items: [{ title: "Leasing brief" }] };
  const html = `<!doctype html><html lang="en"><head><title>CARR Client Brief</title></head><body><article data-deliverable-page="brief" data-item-marker="item-001"><header data-brand="CARR">CARR</header><main><h1>Leasing brief</h1><p>${briefFacts.as_of}</p><p>${briefFacts.caveat}</p></main></article></body></html>`;
  const result = inspectDeliverable({ artifactType: "client-brief", adapter: GENERIC_DELIVERABLE_QC_ADAPTER, html, facts: briefFacts });
  assert.equal(result.blocked, false);
  assert.equal(result.artifactType, "client-brief");
});

test("Tour QC blocks optional displayed-fact and brand-style drift", () => {
  const rendered = renderTourPacket(facts);
  const bad = rendered.html.replace("Available", "Leased").replaceAll("#F57F29", "#000000");
  const result = inspectDeliverable({ artifactType: "tour-packet", html: bad, facts: rendered.facts, expected: { markers: rendered.markers, pageCount: rendered.propertyCount } });
  const rules = new Set(result.findings.map(item => item.ruleId));
  assert.equal(rules.has("QC-PARITY-003"), true);
  assert.equal(rules.has("QC-BRAND-002"), true);
});
