import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import fontkit from "@pdf-lib/fontkit";
import {
  beginText, clip, concatTransformationMatrix, degrees, endPath, endText, lineTo, moveTo,
  PDFDocument, PDFName, popGraphicsState, pushGraphicsState, rectangle, setFontAndSize,
  setLineWidth, setTextMatrix, showText, StandardFonts, stroke,
} from "pdf-lib";
import { inspectStoredTourPacketPdf, renderTourPacketPdf, TOUR_PDF_RENDERER_VERSION, TOUR_PDF_TEMPLATE_VERSION } from "../src/tour-pdf-renderer.js";

const fontRoot = new URL("../assets/", import.meta.url);
const fonts = {
  regular: new Uint8Array(await readFile(new URL("lato-regular.ttf", fontRoot))),
  bold: new Uint8Array(await readFile(new URL("lato-bold.ttf", fontRoot))),
};
const packet = {
  as_of: "2026-08-27T12:00:00Z",
  caveat: null,
  properties: [
    { property_ref: "property:public:ZetaMedicalPlaza0001", route_sequence: 20, route_label: "B", name: "Zeta Medical Plaza", address: "200 Zeta Way, Pensacola, FL", property_type: "Medical office", size: { value: 4200, unit: "SF" }, availability: "Available" },
    { property_ref: "property:public:AlphaHealthCenter01", route_sequence: 10, route_label: "A", name: "Alpha Health Center", address: "100 Alpha Drive, Pensacola, FL", suite: "Suite 120", property_type: "Medical office", asking_economics: { value: "24.00", currency: "USD", period: "NNN" }, availability: "Available", parking: "4.5/1,000 SF" },
  ],
};

test("PDF renderer is byte-deterministic with exactly one Letter page per property", async () => {
  const first = await renderTourPacketPdf(packet, fonts);
  const second = await renderTourPacketPdf({ ...packet, properties: [...packet.properties].reverse() }, fonts);
  assert.deepEqual(first.bytes, second.bytes);
  assert.equal(first.artifactDigest, second.artifactDigest);
  assert.equal(first.propertyCount, 2);
  assert.deepEqual(first.propertyRefs, ["property:public:AlphaHealthCenter01", "property:public:ZetaMedicalPlaza0001"]);
  assert.equal(first.fontDigests.length, 2);
  assert.ok(first.fontDigests.every(value => /^sha256:[0-9a-f]{64}$/.test(value)));
  assert.equal(first.rendererVersion, TOUR_PDF_RENDERER_VERSION);
  assert.equal(first.templateVersion, TOUR_PDF_TEMPLATE_VERSION);
  const parsed = await PDFDocument.load(first.bytes);
  assert.equal(parsed.getPageCount(), first.propertyCount);
  for (const page of parsed.getPages()) assert.deepEqual(page.getSize(), { width: 612, height: 792 });
});

test("PDF renderer fails closed without both pinned embedded fonts", async () => {
  await assert.rejects(renderTourPacketPdf(packet, { regular: fonts.regular }), /bold must be nonempty font bytes/);
  await assert.rejects(renderTourPacketPdf(packet, { bold: fonts.bold }), /regular must be nonempty font bytes/);
});

test("stored PDF inspection derives pages, markers, component digests, and embedded fonts from bytes", async () => {
  const proof = {
    projection_digest: `sha256:${"1".repeat(64)}`,
    template_digest: `sha256:${"2".repeat(64)}`,
    renderer_digest: `sha256:${"3".repeat(64)}`,
    qc_ruleset_digest: `sha256:${"4".repeat(64)}`,
  };
  const rendered = await renderTourPacketPdf(packet, fonts, proof);
  const observed = await inspectStoredTourPacketPdf(rendered.bytes);
  assert.equal(observed.page_count, 2);
  assert.deepEqual(observed.pages.map(page => page.property_ref), rendered.propertyRefs);
  assert.deepEqual(observed.pages.map(page => page.property_marker), rendered.markers);
  assert.ok(observed.pages.every(page => page.clipped_box_count === 0));
  for (const [key, value] of Object.entries(proof)) assert.equal(observed[key], value);
  assert.deepEqual(observed.fonts.map(font => font.digest), rendered.fontDigests);
  assert.ok(observed.fonts.every(font => font.embedded));
});

test("PDF renderer rejects authoritative content that cannot fit instead of truncating it", async () => {
  const overflow = structuredClone(packet);
  // Within the 120-character client value cap, yet too wide for the layout.
  overflow.properties[0].name = "W".repeat(120);
  await assert.rejects(renderTourPacketPdf(overflow, fonts), /tour_pdf_content_overflow/);
});

test("PDF renderer measures route-label bounds and stored inspection decodes text extents and transformations", async () => {
  // A stop marker is 1-3 letters or digits (V5-J303), so a long label is
  // refused before layout rather than measured.
  const overflow = structuredClone(packet);
  overflow.properties[0].route_label = "W".repeat(80);
  await assert.rejects(renderTourPacketPdf(overflow, fonts), /tour_packet_invalid_route_label/);

  const rendered = await renderTourPacketPdf(packet, fonts);
  const document = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  document.catalog.delete(PDFName.of("CARRLayoutBoundsProofVersion"));
  const unmeasured = await document.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  const observed = await inspectStoredTourPacketPdf(unmeasured);
  assert.ok(observed.pages.every(page => page.clipped_box_count === 1));

  const outOfBounds = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  outOfBounds.registerFontkit(fontkit);
  const font = await outOfBounds.embedFont(fonts.regular, { subset: true });
  outOfBounds.getPage(0).drawText("OUTSIDE", { x: 610, y: 50, size: 24, font });
  const tampered = await outOfBounds.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  const tamperedObservation = await inspectStoredTourPacketPdf(tampered);
  assert.equal(tamperedObservation.pages[0].clipped_box_count, 1);

  const transformed = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  transformed.registerFontkit(fontkit);
  const transformedFont = await transformed.embedFont(fonts.regular, { subset: true });
  transformed.getPage(0).drawText("ROTATED", { x: 600, y: 760, size: 24, rotate: degrees(45), font: transformedFont });
  const transformedBytes = await transformed.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  const transformedObservation = await inspectStoredTourPacketPdf(transformedBytes);
  assert.equal(transformedObservation.pages[0].clipped_box_count, 1);

  const consecutive = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  consecutive.registerFontkit(fontkit);
  const consecutiveFont = await consecutive.embedFont(fonts.regular, { subset: true });
  const consecutivePage = consecutive.getPage(0);
  const consecutiveFontName = consecutivePage.node.newFontDictionary(consecutiveFont.name, consecutiveFont.ref);
  const glyph = consecutiveFont.encodeText("W");
  consecutivePage.pushOperators(
    pushGraphicsState(), beginText(), setFontAndSize(consecutiveFontName, 24), setTextMatrix(1, 0, 0, 1, 500, 100),
    ...Array.from({ length: 20 }, () => showText(glyph)), endText(), popGraphicsState(),
  );
  const consecutiveBytes = await consecutive.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(consecutiveBytes)).pages[0].clipped_box_count, 1);
});

test("stored inspection fails closed on clipping paths and Form XObjects", async () => {
  const rendered = await renderTourPacketPdf(packet, fonts);
  const cropped = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  cropped.getPage(0).setCropBox(0, 0, 100, 100);
  const croppedBytes = await cropped.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(croppedBytes)).pages[0].clipped_box_count, 1);

  const clipped = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  const clippedPage = clipped.getPage(0);
  clippedPage.pushOperators(pushGraphicsState(), rectangle(0, 0, 20, 20), clip(), endPath());
  const clippedFont = await clipped.embedFont(StandardFonts.Helvetica);
  clippedPage.drawText("CLIPPED", { x: 30, y: 30, size: 12, font: clippedFont });
  clippedPage.pushOperators(popGraphicsState());
  const clippedBytes = await clipped.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(clippedBytes)).pages[0].clipped_box_count, 1);

  const stroked = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  stroked.getPage(0).drawLine({ start: { x: 0, y: 400 }, end: { x: 500, y: 400 }, thickness: 40 });
  const strokedBytes = await stroked.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(strokedBytes)).pages[0].clipped_box_count, 1);

  const shearedMiter = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  shearedMiter.getPage(0).pushOperators(
    pushGraphicsState(), concatTransformationMatrix(1, 0.1, 1, -0.1, 60, 300), setLineWidth(10),
    moveTo(77.714596, 62.932039), lineTo(0, 0), lineTo(62.932039, 77.714596), stroke(), popGraphicsState(),
  );
  const shearedMiterBytes = await shearedMiter.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(shearedMiterBytes)).pages[0].clipped_box_count, 1);

  const deviceHairline = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  deviceHairline.getPage(0).pushOperators(
    pushGraphicsState(), concatTransformationMatrix(0.01, 0, 0, 0.01, 0.1, 0.1), setLineWidth(0),
    moveTo(0, 0), lineTo(100, 0), stroke(), popGraphicsState(),
  );
  const deviceHairlineBytes = await deviceHairline.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(deviceHairlineBytes)).pages[0].clipped_box_count, 1);

  const donor = await PDFDocument.create();
  const donorPage = donor.addPage([20, 20]);
  const donorFont = await donor.embedFont(StandardFonts.Helvetica);
  donorPage.drawText("X", { x: 2, y: 2, size: 12, font: donorFont });
  const withXObject = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  const [embeddedPage] = await withXObject.embedPdf(await donor.save());
  withXObject.getPage(0).drawPage(embeddedPage, { x: 10, y: 10, width: 20, height: 20 });
  const xObjectBytes = await withXObject.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  assert.equal((await inspectStoredTourPacketPdf(xObjectBytes)).pages[0].clipped_box_count, 1);
});
