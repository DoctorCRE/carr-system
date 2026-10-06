import latoRegular from "../assets/lato-regular.ttf";
import latoBold from "../assets/lato-bold.ttf";
import { inspectTourPdfProof, DELIVERABLE_QC_RULESET_VERSION } from "./deliverable-qc.js";
import { inspectStoredTourPacketPdf, renderTourPacketPdf, TOUR_PDF_RENDERER_VERSION, TOUR_PDF_TEMPLATE_VERSION } from "./tour-pdf-renderer.js";

const encoder = new TextEncoder();

async function digest(value) {
  const bytes = typeof value === "string" ? encoder.encode(value) : value instanceof Uint8Array ? value : new Uint8Array(value);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `sha256:${[...hash].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

function fontBytes(value) {
  return value instanceof Uint8Array ? value : new Uint8Array(value);
}

export async function prepareTourPdfArtifact(renderInput) {
  const packet = renderInput?.packet;
  const packetDigest = await digest(JSON.stringify(packet));
  const templateDigest = await digest(`tour-pdf-template:${TOUR_PDF_TEMPLATE_VERSION}`);
  const rendererDigest = await digest(`tour-pdf-renderer:${TOUR_PDF_RENDERER_VERSION}`);
  const qcRulesetDigest = await digest(`deliverable-qc:${DELIVERABLE_QC_RULESET_VERSION}`);
  const rendered = await renderTourPacketPdf(packet, { regular: fontBytes(latoRegular), bold: fontBytes(latoBold) }, {
    projection_digest: renderInput.projection_digest, template_digest: templateDigest,
    renderer_digest: rendererDigest, qc_ruleset_digest: qcRulesetDigest,
  });
  const markersDigest = await digest(JSON.stringify(rendered.markers));
  const expected = {
    page_count: rendered.propertyCount, property_refs: rendered.propertyRefs, markers: rendered.markers,
    projection_digest: renderInput.projection_digest, template_digest: templateDigest,
    renderer_digest: rendererDigest, qc_ruleset_digest: qcRulesetDigest,
    font_digests: rendered.fontDigests, asset_digests: [],
  };
  return { rendered, packetDigest, templateDigest, rendererDigest, qcRulesetVersion: DELIVERABLE_QC_RULESET_VERSION, qcRulesetDigest, markersDigest, expected };
}

// Tags a thrown error with WHICH phase of storage/verification produced it,
// without overwriting a phase a deeper call already set. runTourPdfRender's
// catch reads `.phase` to log something more useful than a bare error class.
function phased(error, phase) {
  if (error && typeof error === "object" && typeof error.phase !== "string") error.phase = phase;
  return error;
}

export async function storeAndVerifyTourPdf(env, tenant, renderJobId, prepared) {
  if (!env?.carr_documents?.put || !env?.carr_documents?.get) throw phased(new Error("tour_pdf_storage_unavailable"), "store");
  const safeTenant = String(tenant).replace(/[^A-Za-z0-9._-]/g, "_");
  const storageRef = `tour-pdf/${safeTenant}/${renderJobId}/${prepared.rendered.artifactDigest.slice(7)}.pdf`;
  try {
    await env.carr_documents.put(storageRef, prepared.rendered.bytes, {
      httpMetadata: { contentType: "application/pdf", contentDisposition: `attachment; filename="CARR-tour-${renderJobId}.pdf"` },
      customMetadata: { artifactDigest: prepared.rendered.artifactDigest, rendererVersion: TOUR_PDF_RENDERER_VERSION, templateVersion: TOUR_PDF_TEMPLATE_VERSION },
    });
  } catch (error) { throw phased(error, "store"); }
  let stored;
  try {
    stored = await env.carr_documents.get(storageRef);
  } catch (error) { throw phased(error, "store"); }
  if (!stored) throw phased(new Error("tour_pdf_storage_readback_missing"), "store");
  let readback, readbackDigest;
  try {
    readback = new Uint8Array(await stored.arrayBuffer());
    readbackDigest = await digest(readback);
  } catch (error) { throw phased(error, "verify"); }
  if (readbackDigest !== prepared.rendered.artifactDigest) throw phased(new Error("tour_pdf_storage_readback_mismatch"), "verify");
  let qc, qcRunDigest;
  try {
    const observed = { ...(await inspectStoredTourPacketPdf(readback)), artifact_digest: readbackDigest, r2_readback_digest: readbackDigest };
    qc = inspectTourPdfProof({ expected: prepared.expected, observed });
    qcRunDigest = await digest(JSON.stringify({ ruleset: DELIVERABLE_QC_RULESET_VERSION, expected: prepared.expected, observed, findings: qc.findings }));
  } catch (error) { throw phased(error, "verify"); }
  return { storageRef, contentLength: readback.byteLength, qc, qcRunDigest };
}
