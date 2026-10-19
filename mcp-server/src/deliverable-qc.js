/** Pure inspection. A clean result means only that no configured blocker was
 * found; it is intentionally not an approval, publication, or dismissal. */

export const DELIVERABLE_QC_VERSION = "1.1.0";
export const DELIVERABLE_QC_RULESET_VERSION = "1.2.0";

export class DeliverableQcError extends Error {
  constructor(code, details = {}) { super(code); this.name = "DeliverableQcError"; this.code = code; this.details = details; }
}

function finding(ruleId, category, message, details = {}) {
  return Object.freeze({ findingVersion: DELIVERABLE_QC_VERSION, ruleId, category, severity: "block", message, details: Object.freeze(details) });
}
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const PUBLIC_PROPERTY_REF = /^property:public:[A-Za-z0-9_-]{16,128}$/;

/**
 * Inspect machine-observed PDF/render proof. This verifies evidence supplied by
 * a renderer/probe; it does not render, approve, publish, or dismiss findings.
 */
export function inspectTourPdfProof(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new DeliverableQcError("deliverable_qc_invalid_input", { field: "input" });
  const expected = input.expected && typeof input.expected === "object" && !Array.isArray(input.expected) ? input.expected : {};
  const observed = input.observed && typeof input.observed === "object" && !Array.isArray(input.observed) ? input.observed : {};
  const findings = [];
  const expectedRefs = Array.isArray(expected.property_refs) ? expected.property_refs : [];
  const expectedMarkers = Array.isArray(expected.markers) ? expected.markers : [];
  const pages = Array.isArray(observed.pages) ? observed.pages : [];
  const expectedPageCount = expected.page_count;

  if (!Number.isInteger(expectedPageCount) || expectedPageCount < 1 || expectedPageCount !== expectedRefs.length || expectedPageCount !== expectedMarkers.length || observed.page_count !== expectedPageCount || pages.length !== expectedPageCount)
    findings.push(finding("QC-PDF-PAGE-001", "page_count", "PDF page count must exactly equal the expected property count.", { expected: expectedPageCount ?? null, observed: observed.page_count ?? null, probedPages: pages.length }));

  const observedRefs = pages.map(page => page?.property_ref);
  const observedMarkers = pages.map(page => page?.property_marker);
  const pageNumbers = pages.map(page => page?.page_number);
  if (expectedRefs.some(ref => !PUBLIC_PROPERTY_REF.test(ref)) || new Set(observedRefs).size !== observedRefs.length || observedRefs.some((ref, index) => ref !== expectedRefs[index]))
    findings.push(finding("QC-PDF-IDENTITY-001", "public_identity", "Each PDF page must carry exactly one expected public property reference in immutable route order.", { expected: expectedRefs, observed: observedRefs }));
  if (new Set(observedMarkers).size !== observedMarkers.length || observedMarkers.some((marker, index) => marker !== expectedMarkers[index]))
    findings.push(finding("QC-PDF-MARKER-001", "unique_marker", "PDF property markers are missing, duplicated, or out of order.", { expected: expectedMarkers, observed: observedMarkers }));
  if (pageNumbers.some((pageNumber, index) => pageNumber !== index + 1))
    findings.push(finding("QC-PDF-ORDER-001", "page_order", "PDF probe pages are not in contiguous one-based order.", { observed: pageNumbers }));
  if (pages.some(page => !Number.isInteger(page?.clipped_box_count) || page.clipped_box_count !== 0))
    findings.push(finding("QC-PDF-CLIP-001", "layout", "PDF contains clipped or unmeasured layout boxes."));

  for (const field of ["projection_digest", "template_digest", "renderer_digest", "qc_ruleset_digest"]) {
    if (!SHA256.test(expected[field] || "") || observed[field] !== expected[field])
      findings.push(finding("QC-PDF-DIGEST-001", "digest_parity", "PDF render component digest is absent or does not match the expected immutable input.", { field, expected: expected[field] || null, observed: observed[field] || null }));
  }
  if (!SHA256.test(observed.artifact_digest || "") || observed.r2_readback_digest !== observed.artifact_digest)
    findings.push(finding("QC-PDF-READBACK-001", "artifact_integrity", "R2 readback digest does not match the rendered PDF artifact digest."));

  const expectedFonts = Array.isArray(expected.font_digests) ? expected.font_digests : [];
  const fonts = Array.isArray(observed.fonts) ? observed.fonts : [];
  const observedFontDigests = fonts.map(font => font?.digest);
  if (!expectedFonts.length || fonts.length !== expectedFonts.length || fonts.some(font => font?.embedded !== true || !SHA256.test(font?.digest || "")) || observedFontDigests.some((value, index) => value !== expectedFonts[index]))
    findings.push(finding("QC-PDF-FONT-001", "font", "Every expected font must be embedded with an exact pinned digest.", { expected: expectedFonts, observed: observedFontDigests }));

  const expectedAssets = Array.isArray(expected.asset_digests) ? expected.asset_digests : [];
  const observedAssets = Array.isArray(observed.asset_digests) ? observed.asset_digests : [];
  if (expectedAssets.length !== observedAssets.length || expectedAssets.some((value, index) => !SHA256.test(value) || observedAssets[index] !== value))
    findings.push(finding("QC-PDF-ASSET-001", "asset_parity", "Rendered PDF assets do not exactly match the expected ordered digest set.", { expected: expectedAssets, observed: observedAssets }));

  const links = Array.isArray(observed.link_checks) ? observed.link_checks : [];
  if (links.some(link => !link || typeof link.url !== "string" || !link.url.startsWith("https://") || !Number.isInteger(link.status) || link.status < 200 || link.status >= 400))
    findings.push(finding("QC-PDF-LINK-001", "link", "PDF contains a non-HTTPS or failed link check."));

  return Object.freeze({
    qcVersion: DELIVERABLE_QC_VERSION,
    rulesetVersion: DELIVERABLE_QC_RULESET_VERSION,
    artifactType: "tour-pdf",
    blocked: findings.length > 0,
    disposition: "review_required",
    canApprove: false,
    canPublish: false,
    canSelfDismiss: false,
    findings: Object.freeze(findings),
  });
}
