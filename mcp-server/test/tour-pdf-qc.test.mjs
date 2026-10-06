import assert from "node:assert/strict";
import test from "node:test";
import { inspectTourPdfProof } from "../src/deliverable-qc.js";

const digest = character => `sha256:${character.repeat(64)}`;
const refs = ["property:public:AlphaHealthCenter01", "property:public:ZetaMedicalPlaza0001"];
const markers = ["property-1-AlphaHealthCenter01", "property-2-ZetaMedicalPlaza0001"];
const expected = {
  page_count: 2, property_refs: refs, markers,
  projection_digest: digest("a"), template_digest: digest("b"), renderer_digest: digest("c"), qc_ruleset_digest: digest("d"),
  font_digests: [digest("e")], asset_digests: [digest("f"), digest("0")],
};
const observed = {
  page_count: 2,
  pages: [
    { page_number: 1, property_ref: refs[0], property_marker: markers[0], clipped_box_count: 0 },
    { page_number: 2, property_ref: refs[1], property_marker: markers[1], clipped_box_count: 0 },
  ],
  projection_digest: expected.projection_digest, template_digest: expected.template_digest,
  renderer_digest: expected.renderer_digest, qc_ruleset_digest: expected.qc_ruleset_digest,
  artifact_digest: digest("1"), r2_readback_digest: digest("1"),
  fonts: [{ family: "CARR Sans", digest: digest("e"), embedded: true }],
  asset_digests: [...expected.asset_digests],
  link_checks: [{ url: "https://reports.doctorcre.com/share", status: 200 }],
};

test("PDF proof verifies exactly one ordered, unclipped property page and immutable render inputs", () => {
  const result = inspectTourPdfProof({ expected, observed });
  assert.equal(result.blocked, false);
  assert.equal(result.disposition, "review_required");
  assert.equal(result.canApprove, false); assert.equal(result.canPublish, false); assert.equal(result.canSelfDismiss, false);
  assert.deepEqual(result.findings, []);
});

test("PDF proof blocks page, identity, order, clip, digest, font, asset, readback, and link failures", () => {
  const result = inspectTourPdfProof({ expected, observed: {
    ...observed, page_count: 3,
    pages: [
      { page_number: 2, property_ref: refs[1], property_marker: markers[1], clipped_box_count: 1 },
      { page_number: 1, property_ref: refs[1], property_marker: markers[1], clipped_box_count: 0 },
    ],
    template_digest: digest("9"), r2_readback_digest: digest("8"),
    fonts: [{ family: "Fallback", digest: digest("7"), embedded: false }],
    asset_digests: [digest("6")], link_checks: [{ url: "http://unsafe.example", status: 500 }],
  } });
  assert.equal(result.blocked, true);
  const rules = new Set(result.findings.map(item => item.ruleId));
  for (const rule of ["QC-PDF-PAGE-001", "QC-PDF-IDENTITY-001", "QC-PDF-MARKER-001", "QC-PDF-ORDER-001", "QC-PDF-CLIP-001", "QC-PDF-DIGEST-001", "QC-PDF-READBACK-001", "QC-PDF-FONT-001", "QC-PDF-ASSET-001", "QC-PDF-LINK-001"]) assert.equal(rules.has(rule), true, rule);
});

test("PDF proof cannot convert a clean result into approval or publication authority", () => {
  const result = inspectTourPdfProof({ expected, observed });
  assert.equal(Object.hasOwn(result, "approve"), false);
  assert.equal(Object.hasOwn(result, "publish"), false);
  assert.equal(Object.hasOwn(result, "dismiss"), false);
});
