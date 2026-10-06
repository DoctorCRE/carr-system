import fontkit from "@pdf-lib/fontkit";
import { decodePDFRawStream, PDFDocument, PDFName, PDFString, rgb } from "pdf-lib";
import { formatApprovedMetric, renderTourPacket } from "./tour-packet-render.js";

export const TOUR_PDF_RENDERER_VERSION = "1.1.0";
export const TOUR_PDF_TEMPLATE_VERSION = "1.1.0";

const PAGE = [612, 792];
const NAVY = rgb(0, 47 / 255, 108 / 255);
const ORANGE = rgb(245 / 255, 127 / 255, 41 / 255);
const INK = rgb(23 / 255, 32 / 255, 51 / 255);
const MUTED = rgb(82 / 255, 96 / 255, 116 / 255);
const LINE = rgb(216 / 255, 224 / 255, 233 / 255);
const PAPER = rgb(247 / 255, 249 / 255, 252 / 255);

function bytes(value, field) {
  if (value instanceof Uint8Array && value.byteLength > 0) return value;
  if (value instanceof ArrayBuffer && value.byteLength > 0) return new Uint8Array(value);
  throw new TypeError(`${field} must be nonempty font bytes`);
}

function splitWord(word, font, size, width) {
  const chunks = [];
  let chunk = "";
  for (const character of word) {
    const next = chunk + character;
    if (chunk && font.widthOfTextAtSize(next, size) > width) { chunks.push(chunk); chunk = character; }
    else chunk = next;
  }
  if (chunk) chunks.push(chunk);
  return chunks;
}

function wrap(value, font, size, width, maximumLines) {
  const words = String(value || "").trim().split(/\s+/).filter(Boolean).flatMap(word =>
    font.widthOfTextAtSize(word, size) <= width ? [word] : splitWord(word, font, size, width));
  const lines = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (line && font.widthOfTextAtSize(next, size) > width) { lines.push(line); line = word; }
    else line = next;
  }
  if (line) lines.push(line);
  if (lines.length <= maximumLines) return lines;
  throw new RangeError(`tour_pdf_content_overflow:${maximumLines}:${width}`);
}

function drawLines(page, lines, options) {
  lines.forEach((line, index) => page.drawText(line, { ...options, y: options.y - index * options.lineHeight }));
}

function measuredSingleLine(value, font, size, maximumWidth) {
  const lines = wrap(value, font, size, maximumWidth, 1);
  const width = font.widthOfTextAtSize(lines[0], size);
  if (!Number.isFinite(width) || width < 0 || width > maximumWidth)
    throw new RangeError(`tour_pdf_content_overflow:1:${maximumWidth}`);
  return { text: lines[0], width };
}

function factValue(property, field) {
  const value = property[field];
  if (!value) return "Not provided";
  if (field === "size" || field === "asking_economics") return formatApprovedMetric(value);
  return String(value);
}

function formatAsOf(value) {
  const date = new Date(value);
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  return `${months[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()} at ${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")} UTC`;
}

function drawFact(page, regular, bold, label, value, y) {
  page.drawLine({ start: { x: 42, y: y + 8 }, end: { x: 570, y: y + 8 }, thickness: .7, color: LINE });
  page.drawText(label.toUpperCase(), { x: 42, y: y - 7, size: 8.5, font: bold, color: NAVY });
  drawLines(page, wrap(value, regular, 10.5, 340, 2), { x: 210, y: y - 7, size: 10.5, lineHeight: 14, font: regular, color: INK });
}

async function sha256(value) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", value));
  return `sha256:${[...digest].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

/** Generate a fixed Letter packet with exactly one polished page per property. */
export async function renderTourPacketPdf(input, fonts, proof = {}) {
  const packet = renderTourPacket(input);
  const regularBytes = bytes(fonts?.regular, "regular");
  const boldBytes = bytes(fonts?.bold, "bold");
  const document = await PDFDocument.create({ updateMetadata: false });
  document.registerFontkit(fontkit);
  const regular = await document.embedFont(regularBytes, { subset: true });
  const bold = await document.embedFont(boldBytes, { subset: true });
  const fixedDate = new Date(packet.facts.as_of);
  document.setTitle("CARR Property Tour Packet");
  document.setAuthor("CARR Healthcare Real Estate");
  document.setSubject("Facts-only property tour packet");
  document.setCreator(`CARR Tour Operations ${TOUR_PDF_RENDERER_VERSION}`);
  document.setProducer(`CARR deterministic PDF renderer ${TOUR_PDF_RENDERER_VERSION}`);
  document.setCreationDate(fixedDate);
  document.setModificationDate(fixedDate);
  const regularDigest = await sha256(regularBytes);
  const boldDigest = await sha256(boldBytes);
  const catalogProof = {
    CARRProjectionDigest: proof.projection_digest,
    CARRTemplateDigest: proof.template_digest,
    CARRRendererDigest: proof.renderer_digest,
    CARRQcRulesetDigest: proof.qc_ruleset_digest,
    CARRFontDigests: JSON.stringify([regularDigest, boldDigest]),
    CARRLayoutBoundsProofVersion: "decoded-content-geometry.v2",
  };
  for (const [key, value] of Object.entries(catalogProof)) {
    if (typeof value === "string" && value) document.catalog.set(PDFName.of(key), PDFString.of(value));
  }

  packet.facts.properties.forEach((property, index) => {
    const page = document.addPage(PAGE);
    page.drawText("CARR", { x: 42, y: 741, size: 19, font: bold, color: NAVY });
    const stop = property.route_label || `Stop ${property.route_sequence}`;
    const measuredStop = measuredSingleLine(stop.toUpperCase(), bold, 9, 240);
    page.drawText(measuredStop.text, { x: 570 - measuredStop.width, y: 746, size: 9, font: bold, color: NAVY });
    page.drawRectangle({ x: 42, y: 727, width: 528, height: 3, color: ORANGE });
    drawLines(page, wrap(property.name, bold, 24, 528, 2), { x: 42, y: 687, size: 24, lineHeight: 28, font: bold, color: NAVY });
    drawLines(page, wrap(`${property.address}${property.suite ? ` - ${property.suite}` : ""}`, regular, 11.5, 528, 2), { x: 42, y: 622, size: 11.5, lineHeight: 15, font: regular, color: MUTED });

    const facts = [
      ["Property type", factValue(property, "property_type")],
      ["Size", factValue(property, "size")],
      ["Asking economics", factValue(property, "asking_economics")],
      ["Availability", factValue(property, "availability")],
      ["Parking", factValue(property, "parking")],
    ];
    facts.forEach(([label, value], factIndex) => drawFact(page, regular, bold, label, value, 570 - factIndex * 58));
    page.drawLine({ start: { x: 42, y: 288 }, end: { x: 570, y: 288 }, thickness: .7, color: LINE });

    page.drawRectangle({ x: 42, y: 119, width: 528, height: 130, color: PAPER, borderColor: ORANGE, borderWidth: 1.5 });
    page.drawText("FACTS-ONLY CLIENT NOTE", { x: 58, y: 224, size: 8.5, font: bold, color: NAVY });
    drawLines(page, wrap(`As of ${formatAsOf(property.as_of)}`, regular, 9.5, 494, 1), { x: 58, y: 204, size: 9.5, lineHeight: 13, font: regular, color: INK });
    // No caveat line: renderTourPacket (1.3.0) admits only the client field
    // allowlist, so a property never carries one (V5-J303).

    const marker = packet.markers[index];
    const packetRefLine = measuredSingleLine(`Packet ref: ${property.property_ref}`, regular, 6.5, 528);
    const markerLine = measuredSingleLine(`Property marker: ${marker}`, regular, 6.5, 450);
    page.node.set(PDFName.of("CARRPropertyRef"), PDFString.of(property.property_ref));
    page.node.set(PDFName.of("CARRPropertyMarker"), PDFString.of(marker));
    page.drawText(packetRefLine.text, { x: 42, y: 76, size: 6.5, font: regular, color: MUTED });
    page.drawText(markerLine.text, { x: 42, y: 63, size: 6.5, font: regular, color: MUTED });
    const pageLabel = `${index + 1} / ${packet.propertyCount}`;
    page.drawText(pageLabel, { x: 570 - regular.widthOfTextAtSize(pageLabel, 7), y: 63, size: 7, font: regular, color: MUTED });
  });

  const pdfBytes = await document.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  return Object.freeze({
    bytes: pdfBytes,
    artifactDigest: await sha256(pdfBytes),
    fontDigests: Object.freeze([regularDigest, boldDigest]),
    rendererVersion: TOUR_PDF_RENDERER_VERSION,
    templateVersion: TOUR_PDF_TEMPLATE_VERSION,
    propertyCount: packet.propertyCount,
    propertyRefs: packet.propertyRefs,
    markers: packet.markers,
  });
}

function decoded(object) {
  return object && typeof object.decodeText === "function" ? object.decodeText() : null;
}

function pageContent(document, page) {
  const contents = page.node.Contents();
  const references = contents && typeof contents.asArray === "function" ? contents.asArray() : contents ? [contents] : [];
  const decoder = new TextDecoder();
  return references.map(reference => {
    const stream = document.context.lookup(reference);
    try { return decoder.decode(decodePDFRawStream(stream).decode()); }
    catch { return ""; }
  }).join("\n");
}

function numeric(object) {
  try { return object && typeof object.asNumber === "function" ? object.asNumber() : null; }
  catch { return null; }
}

function objects(object) {
  try { return object && typeof object.asArray === "function" ? object.asArray() : null; }
  catch { return null; }
}

function lookedUp(document, object) {
  try { return object ? document.context.lookup(object) : null; }
  catch { return null; }
}

function pageFontMetrics(document, page) {
  const resources = lookedUp(document, page.node.Resources());
  const fonts = resources && lookedUp(document, resources.get(PDFName.of("Font")));
  if (!fonts || typeof fonts.entries !== "function") return null;
  const result = new Map();
  for (const [resourceName, reference] of fonts.entries()) {
    const font = lookedUp(document, reference);
    const descendants = font && objects(font.get(PDFName.of("DescendantFonts")));
    if (decoded(font?.get(PDFName.of("Subtype"))) !== "Type0" ||
        decoded(font?.get(PDFName.of("Encoding"))) !== "Identity-H" || descendants?.length !== 1) return null;
    const descendant = lookedUp(document, descendants[0]);
    const descriptor = descendant && lookedUp(document, descendant.get(PDFName.of("FontDescriptor")));
    const box = descriptor && objects(descriptor.get(PDFName.of("FontBBox")))?.map(numeric);
    const widthsArray = descendant && objects(descendant.get(PDFName.of("W")));
    if (!box || box.length !== 4 || box.some(value => !Number.isFinite(value)) || !widthsArray) return null;
    const widths = new Map();
    for (let index = 0; index < widthsArray.length;) {
      const start = numeric(widthsArray[index++]);
      if (!Number.isInteger(start) || index >= widthsArray.length) return null;
      const nextWidths = objects(widthsArray[index]);
      if (nextWidths) {
        index += 1;
        for (let offset = 0; offset < nextWidths.length; offset += 1) {
          const glyphWidth = numeric(nextWidths[offset]);
          if (!Number.isFinite(glyphWidth)) return null;
          widths.set(start + offset, glyphWidth);
        }
      } else {
        const end = numeric(widthsArray[index++]);
        const glyphWidth = numeric(widthsArray[index++]);
        if (!Number.isInteger(end) || end < start || !Number.isFinite(glyphWidth)) return null;
        for (let code = start; code <= end; code += 1) widths.set(code, glyphWidth);
      }
    }
    const defaultWidth = numeric(descendant.get(PDFName.of("DW"))) ?? 1000;
    if (!Number.isFinite(defaultWidth)) return null;
    result.set(decoded(resourceName), { box, defaultWidth, widths });
  }
  return result.size ? result : null;
}

function multiplyMatrix(left, right) {
  return [
    left[0] * right[0] + left[2] * right[1], left[1] * right[0] + left[3] * right[1],
    left[0] * right[2] + left[2] * right[3], left[1] * right[2] + left[3] * right[3],
    left[0] * right[4] + left[2] * right[5] + left[4], left[1] * right[4] + left[3] * right[5] + left[5],
  ];
}

function transformPoint(matrix, x, y) {
  return [matrix[0] * x + matrix[2] * y + matrix[4], matrix[1] * x + matrix[3] * y + matrix[5]];
}

function contentTokens(content) {
  const expression = /%[^\r\n]*|<[0-9A-Fa-f\s]*>|\/(?:#[0-9A-Fa-f]{2}|[^\s<>\[\](){}%])+|\[[^\]]*\]|[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)|[A-Za-z*'"]+/g;
  const tokens = [];
  let end = 0;
  for (const match of content.matchAll(expression)) {
    if (content.slice(end, match.index).trim()) return null;
    end = match.index + match[0].length;
    if (!match[0].startsWith("%")) tokens.push(match[0]);
  }
  return content.slice(end).trim() ? null : tokens;
}

function decodedGeometryWithinPage(document, page, content, width, height) {
  const tokens = contentTokens(content);
  const fonts = pageFontMetrics(document, page);
  if (!tokens || !fonts) return false;
  const inside = point => point.every(Number.isFinite) && point[0] >= 0 && point[1] >= 0 && point[0] <= width && point[1] <= height;
  const identity = () => [1, 0, 0, 1, 0, 0];
  let state = { ctm: identity(), lineWidth: 1 };
  const graphics = [];
  let path = [];
  let operands = [];
  let inText = false;
  let font = null;
  let fontSize = null;
  let leading = 0;
  let textMatrix = identity();
  let textLineMatrix = identity();
  let geometryCount = 0;
  const numbers = (count) => operands.length === count && operands.every(Number.isFinite) ? operands : null;
  const pointInside = (x, y) => inside(transformPoint(state.ctm, x, y));
  const transformed = (x, y) => transformPoint(state.ctm, x, y);
  const maximumLinearScale = () => {
    const [a, b, c, d] = state.ctm;
    const sumSquares = (a * a) + (b * b) + (c * c) + (d * d);
    const determinant = (a * d) - (b * c);
    const discriminant = Math.max(0, (sumSquares * sumSquares) - (4 * determinant * determinant));
    return Math.sqrt((sumSquares + Math.sqrt(discriminant)) / 2);
  };
  const strokeWithinPage = () => {
    if (!path.length || !Number.isFinite(state.lineWidth) || state.lineWidth <= 0) return false;
    const scale = maximumLinearScale();
    const margin = Math.max(state.lineWidth, 1) * scale * 5;
    return Number.isFinite(margin) && path.every(([x, y]) => x >= margin && y >= margin && x <= width - margin && y <= height - margin);
  };
  const allCornersInside = (matrix, box) => [
    [box[0], box[1]], [box[0], box[3]], [box[2], box[1]], [box[2], box[3]],
  ].every(([x, y]) => inside(transformPoint(matrix, x, y)));
  try {
    for (const token of tokens) {
      if (/^[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(token)) { operands.push(Number(token)); continue; }
      if (token.startsWith("/") || token.startsWith("<") || token.startsWith("[")) { operands.push(token); continue; }
      if (token === "q") {
        if (operands.length || path.length) return false;
        graphics.push({ ctm: [...state.ctm], lineWidth: state.lineWidth });
      } else if (token === "Q") {
        if (operands.length || !graphics.length || path.length) return false;
        state = graphics.pop();
      } else if (token === "cm") {
        const values = numbers(6); if (!values || path.length) return false;
        state.ctm = multiplyMatrix(state.ctm, values);
      } else if (["rg", "RG"].includes(token)) {
        if (!numbers(3)) return false;
      } else if (["g", "G"].includes(token)) {
        if (!numbers(1)) return false;
      } else if (token === "w") {
        const values = numbers(1); if (!values || values[0] < 0) return false;
        state.lineWidth = values[0];
      } else if (token === "d") {
        if (operands.length !== 2 || typeof operands[0] !== "string" || !operands[0].startsWith("[") || !Number.isFinite(operands[1])) return false;
      } else if (["m", "l"].includes(token)) {
        const values = numbers(2); if (!values || !pointInside(values[0], values[1])) return false;
        path.push(transformed(values[0], values[1]));
        geometryCount += 1;
      } else if (token === "re") {
        const values = numbers(4); if (!values) return false;
        const [x, y, boxWidth, boxHeight] = values;
        const corners = [[x, y], [x + boxWidth, y], [x, y + boxHeight], [x + boxWidth, y + boxHeight]];
        if (!corners.every(([px, py]) => pointInside(px, py))) return false;
        path.push(...corners.map(([px, py]) => transformed(px, py)));
        geometryCount += 1;
      } else if (token === "h") {
        if (operands.length || !path.length) return false;
      } else if (["S", "s", "B", "B*", "b", "b*"].includes(token)) {
        if (operands.length || !strokeWithinPage()) return false;
        path = [];
      } else if (["f", "F", "f*", "n"].includes(token)) {
        if (operands.length) return false;
        path = [];
      } else if (token === "BT") {
        if (operands.length || inText) return false;
        inText = true; font = null; fontSize = null; leading = 0; textMatrix = identity(); textLineMatrix = identity();
      } else if (token === "ET") {
        if (operands.length || !inText) return false;
        inText = false;
      } else if (token === "Tf") {
        if (!inText || operands.length !== 2 || typeof operands[0] !== "string" || !operands[0].startsWith("/") || !Number.isFinite(operands[1]) || operands[1] <= 0) return false;
        font = fonts.get(operands[0].slice(1)); fontSize = operands[1];
        if (!font) return false;
      } else if (token === "TL") {
        const values = numbers(1); if (!inText || !values) return false;
        leading = values[0];
      } else if (token === "Tm") {
        const values = numbers(6); if (!inText || !values) return false;
        textMatrix = [...values]; textLineMatrix = [...values];
      } else if (token === "T*") {
        if (!inText || operands.length || !Number.isFinite(leading)) return false;
        textLineMatrix = multiplyMatrix(textLineMatrix, [1, 0, 0, 1, 0, -leading]);
        textMatrix = [...textLineMatrix];
      } else if (token === "Tj") {
        if (!inText || operands.length !== 1 || typeof operands[0] !== "string" || !operands[0].startsWith("<") || !font || !Number.isFinite(fontSize)) return false;
        const hex = operands[0].slice(1, -1).replace(/\s/g, "");
        if (!hex || hex.length % 4) return false;
        const matrix = multiplyMatrix(state.ctm, textMatrix);
        const scale = fontSize / 1000;
        let cursor = 0;
        for (let offset = 0; offset < hex.length; offset += 4) {
          const code = Number.parseInt(hex.slice(offset, offset + 4), 16);
          const glyphWidth = font.widths.get(code) ?? font.defaultWidth;
          const glyphBox = [cursor + font.box[0] * scale, font.box[1] * scale, cursor + font.box[2] * scale, font.box[3] * scale];
          if (!allCornersInside(matrix, glyphBox)) return false;
          cursor += glyphWidth * scale;
        }
        textMatrix = multiplyMatrix(textMatrix, [1, 0, 0, 1, cursor, 0]);
        geometryCount += 1;
      } else {
        return false;
      }
      operands = [];
    }
  } catch { return false; }
  return !inText && !graphics.length && !path.length && !operands.length && geometryCount > 0;
}

/** Parse stored PDF bytes so QC observations do not trust the render model. */
export async function inspectStoredTourPacketPdf(readback) {
  const document = await PDFDocument.load(readback, { updateMetadata: false });
  const catalog = key => decoded(document.catalog.get(PDFName.of(key)));
  const layoutProofCurrent = catalog("CARRLayoutBoundsProofVersion") === "decoded-content-geometry.v2";
  const pages = document.getPages().map((page, index) => {
    const size = page.getSize();
    const propertyRef = decoded(page.node.get(PDFName.of("CARRPropertyRef")));
    const propertyMarker = decoded(page.node.get(PDFName.of("CARRPropertyMarker")));
    const hasContent = Boolean(page.node.get(PDFName.of("Contents")));
    const crop = page.getCropBox();
    const pageFrameCurrent = page.getRotation().angle === 0 && crop.x === 0 && crop.y === 0 &&
      crop.width === size.width && crop.height === size.height;
    const geometryWithinPage = decodedGeometryWithinPage(document, page, pageContent(document, page), size.width, size.height);
    return {
      page_number: index + 1, property_ref: propertyRef, property_marker: propertyMarker,
      clipped_box_count: size.width === 612 && size.height === 792 && pageFrameCurrent && hasContent && propertyRef && propertyMarker && layoutProofCurrent && geometryWithinPage ? 0 : 1,
    };
  });
  let fontDigests = [];
  try { fontDigests = JSON.parse(catalog("CARRFontDigests") || "[]"); } catch { fontDigests = []; }
  const serialized = new TextDecoder().decode(readback);
  const embeddedFontPrograms = (serialized.match(/\/FontFile(?:2|3)\b/g) || []).length;
  return {
    page_count: document.getPageCount(), pages,
    projection_digest: catalog("CARRProjectionDigest"), template_digest: catalog("CARRTemplateDigest"),
    renderer_digest: catalog("CARRRendererDigest"), qc_ruleset_digest: catalog("CARRQcRulesetDigest"),
    fonts: Array.isArray(fontDigests) ? fontDigests.map(value => ({ digest: value, embedded: embeddedFontPrograms >= fontDigests.length })) : [],
    asset_digests: [], link_checks: [],
  };
}
