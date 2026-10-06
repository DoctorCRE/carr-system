/**
 * Deterministic, presentation-only rendering for an already-redacted Tour
 * facts projection. It deliberately has no database, network, publication,
 * routing, or map capability.
 */

import { CLIENT_TOUR_PACKET_COLUMNS, CLIENT_TOUR_STOP_ENVELOPE_KEYS } from "./tour-operations-contract.js";
import { CLIENT_TEXT_MAX_CHARS, isClientRouteLabel, isClientSafeText, normalizeClientText } from "./tour-client-value-safety.js";

// 1.3.0 (V5-J303): fields are CLIENT_TOUR_FIELD_KEYS only (no per-property
// caveat), every value passes the shared client value-safety rule, and the
// stop marker is 1-3 letters or digits.
export const TOUR_PACKET_RENDER_VERSION = "1.3.0";
export const TOUR_PACKET_TEMPLATE_VERSION = "1.1.0";
export const TOUR_PACKET_BRAND = "CARR";

// `caveat` stays a recognised top-level key only because the database packet
// carries it as an explicit null (0586); any non-null caveat is refused.
const TOP_LEVEL_FIELDS = new Set(["as_of", "caveat", "properties"]);
// The client allowlist and its envelope, plus the per-property as-of stamp
// the PDF prints. Nothing else -- a caveat included -- is renderable.
const PROPERTY_FIELDS = new Set([
  ...CLIENT_TOUR_STOP_ENVELOPE_KEYS, ...Object.values(CLIENT_TOUR_PACKET_COLUMNS), "as_of",
]);
const METRIC_FIELDS = new Set(["value", "unit", "min", "max", "currency", "period", "label"]);
const REQUIRED_PROPERTY_FIELDS = ["property_ref", "route_sequence", "route_label", "name", "address"];
const PROPERTY_REF = /^property:public:[A-Za-z0-9_-]{16,128}$/;
const FORBIDDEN_FIELD = /(?:^|_)(?:id|provider|rights?|evidence|verifier|contact|email|phone|access(?:_notes?)?|internal)(?:$|_)/i;
const EMAIL = /\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/;
const PHONE = /\b(?:\+?\d[\d .()-]{7,}\d)\b/;
const MAX_PROPERTIES = 50;
const MAX_FIELD_CHARS = 360;
const MAX_PROPERTY_CHARS = 1_440;
const MAX_ESTIMATED_LINES = 32;

export class TourPacketRenderError extends Error {
  constructor(code, details = {}) {
    super(code);
    this.name = "TourPacketRenderError";
    this.code = code;
    this.details = details;
  }
}

function reject(code, details) { throw new TourPacketRenderError(code, details); }

function plainText(value, path, maximum = MAX_FIELD_CHARS, contactScreen = true) {
  if (typeof value !== "string") reject("tour_packet_invalid_text", { path });
  if (/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(value)) reject("tour_packet_invalid_text", { path });
  const text = value.trim().replace(/\s+/g, " ");
  if (!text) reject("tour_packet_invalid_text", { path });
  if (text.length > maximum) reject("tour_packet_overflow", { path, maximum });
  if (contactScreen && (EMAIL.test(text) || PHONE.test(text))) reject("tour_packet_forbidden_contact", { path });
  return text;
}

/**
 * A client field value: plain text judged by the shared client value-safety
 * rule alone, so the PDF never refuses a value the list and map show
 * ("36602-1234", "Available 03-15-2027"). The renderer prints exactly the
 * text that rule judged -- normalizeClientText(value): NFKC, space runs
 * collapsed, ends trimmed -- so nothing the PDF prints has escaped the rule,
 * and a full-width value cannot print differently from the text the database
 * and the browser share judged.
 */
function clientText(value, path) {
  plainText(value, path, CLIENT_TEXT_MAX_CHARS, false);
  if (!isClientSafeText(value)) reject("tour_packet_forbidden_contact", { path });
  return normalizeClientText(value);
}

// Accepts the canonical "Z" form unchanged, and the "+HH:MM" form that
// Postgres timestamptz takes through jsonb (ops.read_tour_packet_for_render),
// normalized to UTC ISO. Calendar-impossible values still refuse.
const TIMESTAMP = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,6})?(?:Z|([+-])(\d{2}):(\d{2}))$/;
function timestamp(value, path) {
  // The anchored pattern admits digits and separators only, so the free-text
  // contact screen is not applied: microsecond digits read as a phone number.
  if (typeof value !== "string" || value.length > 40) reject("tour_packet_invalid_as_of", { path });
  const text = value;
  const match = TIMESTAMP.exec(text);
  if (!match) reject("tour_packet_invalid_as_of", { path });
  const [, local, fraction = "", sign, hours, minutes] = match;
  const wall = Date.parse(`${local}.000Z`);
  if (Number.isNaN(wall) || new Date(wall).toISOString().slice(0, 19) !== local) reject("tour_packet_invalid_as_of", { path });
  if (!sign) {
    if (fraction && fraction.length !== 4) reject("tour_packet_invalid_as_of", { path });
    return text;
  }
  if (Number(hours) > 23 || Number(minutes) > 59) reject("tour_packet_invalid_as_of", { path });
  const offset = (sign === "-" ? -1 : 1) * (Number(hours) * 60 + Number(minutes));
  const millis = Number((fraction.slice(1) + "000").slice(0, 3));
  return new Date(wall + millis - offset * 60000).toISOString();
}

function assertExactObject(value, allowed, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) reject("tour_packet_invalid_object", { path });
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_FIELD.test(key)) reject("tour_packet_forbidden_field", { path: `${path}.${key}` });
    if (!allowed.has(key)) reject("tour_packet_unknown_field", { path: `${path}.${key}` });
  }
}

function finiteNumber(value, path) {
  if (typeof value !== "number" || !Number.isFinite(value)) reject("tour_packet_invalid_metric", { path });
  return value;
}

function metricPart(value, path) {
  return typeof value === "number" ? finiteNumber(value, path) : clientText(value, path);
}

/** Preserve a structured approved metric after strict allowlist validation. */
function approvedMetric(value, path) {
  assertExactObject(value, METRIC_FIELDS, path);
  if (value.value === undefined && value.min === undefined && value.max === undefined)
    reject("tour_packet_invalid_metric", { path });
  const output = {};
  for (const field of ["value", "min", "max"]) if (value[field] !== undefined) output[field] = metricPart(value[field], `${path}.${field}`);
  for (const field of ["unit", "currency", "period", "label"]) if (value[field] !== undefined) output[field] = clientText(value[field], `${path}.${field}`);
  if (output.min !== undefined && output.max !== undefined && typeof output.min === "number" && typeof output.max === "number" && output.min > output.max)
    reject("tour_packet_invalid_metric", { path, reason: "min_gt_max" });
  return Object.freeze(output);
}

/** Deterministic human formatting of an already-approved structured metric. */
export function formatApprovedMetric(metric) {
  const range = metric.value !== undefined ? String(metric.value) :
    metric.min !== undefined && metric.max !== undefined ? `${metric.min}–${metric.max}` : String(metric.min ?? metric.max ?? "");
  const leading = metric.currency ? `${metric.currency} ${range}` : range;
  const withUnit = metric.unit ? `${leading} ${metric.unit}` : leading;
  const withPeriod = metric.period ? `${withUnit} / ${metric.period}` : withUnit;
  return metric.label ? `${metric.label}: ${withPeriod}` : withPeriod;
}

function canonicalProperty(property, index, packetAsOf) {
  assertExactObject(property, PROPERTY_FIELDS, `properties[${index}]`);
  const output = {};
  for (const field of REQUIRED_PROPERTY_FIELDS) {
    if (property[field] === undefined) reject("tour_packet_required_fact_missing", { path: `properties[${index}].${field}` });
  }
  output.property_ref = plainText(property.property_ref, `properties[${index}].property_ref`, 160);
  if (!PROPERTY_REF.test(output.property_ref)) reject("tour_packet_invalid_property_ref", { path: `properties[${index}].property_ref` });
  if (!Number.isInteger(property.route_sequence) || property.route_sequence < 1)
    reject("tour_packet_invalid_route_sequence", { path: `properties[${index}].route_sequence` });
  output.route_sequence = property.route_sequence;
  output.route_label = plainText(property.route_label, `properties[${index}].route_label`, 80);
  if (!isClientRouteLabel(output.route_label)) reject("tour_packet_invalid_route_label", { path: `properties[${index}].route_label` });
  output.name = clientText(property.name, `properties[${index}].name`);
  output.address = clientText(property.address, `properties[${index}].address`);
  for (const field of ["suite", "property_type", "availability", "parking"]) {
    if (property[field] !== undefined && property[field] !== null) output[field] = clientText(property[field], `properties[${index}].${field}`);
  }
  for (const field of ["size", "asking_economics"]) {
    if (property[field] !== undefined && property[field] !== null) output[field] = approvedMetric(property[field], `properties[${index}].${field}`);
  }
  output.as_of = property.as_of === undefined ? packetAsOf : timestamp(property.as_of, `properties[${index}].as_of`);

  const displayValues = Object.values(output).filter(value => value != null).map(value => typeof value === "object" ? formatApprovedMetric(value) : String(value));
  const totalChars = displayValues.reduce((total, item) => total + item.length, 0);
  const estimatedLines = 8 + displayValues.reduce((total, item) => total + Math.ceil(item.length / 68), 0);
  if (totalChars > MAX_PROPERTY_CHARS || estimatedLines > MAX_ESTIMATED_LINES)
    reject("tour_packet_overflow", { path: `properties[${index}]`, totalChars, estimatedLines, maxChars: MAX_PROPERTY_CHARS, maxLines: MAX_ESTIMATED_LINES });
  return output;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function row(label, value) { return value ? `<div class="fact"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>` : ""; }
function markerFor(property) { return `property-${property.route_sequence}-${property.property_ref.slice("property:public:".length)}`; }

function page(property, marker) {
  const title = escapeHtml(property.name);
  return `<section class="tour-property-page" data-deliverable-page="property" data-property-marker="${marker}" data-property-ref="${escapeHtml(property.property_ref)}" data-route-sequence="${property.route_sequence}" aria-labelledby="${marker}-title">\n<!-- property-marker:${marker} -->\n<header class="property-header" data-brand="CARR"><p class="brand">CARR</p><p class="page-index">${escapeHtml(property.route_label)}</p></header>\n<h1 id="${marker}-title">${title}</h1><p class="address">${escapeHtml(property.address)}${property.suite ? ` · ${escapeHtml(property.suite)}` : ""}</p><dl class="facts">${row("Property type", property.property_type)}${row("Size", property.size && formatApprovedMetric(property.size))}${row("Asking economics", property.asking_economics && formatApprovedMetric(property.asking_economics))}${row("Availability", property.availability)}${row("Parking", property.parking)}</dl><aside class="facts-caveat"><p><strong>As of:</strong> ${escapeHtml(property.as_of)}</p></aside>\n</section>`;
}

const STYLE = `<style>
@page { size: Letter; margin: 0.55in; }
* { box-sizing: border-box; }
html { font-family: Arial, Helvetica, sans-serif; color: #002F6C; }
body { margin: 0; }
.tour-property-page { min-height: 9.85in; break-after: page; page-break-after: always; display: flex; flex-direction: column; }
.tour-property-page:last-child { break-after: auto; page-break-after: auto; }
.property-header { border-bottom: 3px solid #F57F29; display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 0.12in; }
.brand { color: #002F6C; font-size: 18pt; font-weight: 700; letter-spacing: .08em; margin: 0; }
.page-index { color: #002F6C; font-size: 9pt; margin: 0; text-transform: uppercase; }
.packet-content { padding-top: 0.24in; }
h1 { font-size: 24pt; line-height: 1.12; margin: 0; overflow-wrap: anywhere; }
.address { font-size: 12pt; line-height: 1.35; margin: .10in 0 .28in; overflow-wrap: anywhere; }
.facts { border-top: 1px solid #b8c2ca; margin: 0; }
.fact { border-bottom: 1px solid #d4dbe0; display: grid; grid-template-columns: 1.75in 1fr; gap: .16in; padding: .11in 0; }
dt { color: #002F6C; font-size: 9pt; font-weight: 700; text-transform: uppercase; } dd { font-size: 11pt; line-height: 1.32; margin: 0; overflow-wrap: anywhere; }
.facts-caveat { background: #f3f5f6; border-left: 4px solid #F57F29; font-size: 9pt; line-height: 1.35; margin-top: .28in; padding: .12in .16in; overflow-wrap: anywhere; }
.facts-caveat p { margin: 0 0 .08in; }.facts-caveat p:last-child { margin-bottom: 0; }
@media screen { body { background: #e9edf0; padding: .3in; }.tour-property-page { background: #fff; margin: 0 auto .3in; max-width: 8.5in; padding: .55in; } }
@media print { body { background: #fff; }.packet-content { padding-top: 0; }.tour-property-page { height: 9.9in; min-height: 9.9in; max-height: 9.9in; overflow: hidden; padding: 0; } }
</style>`;

/** Returns canonical safe facts and HTML; it never delivers, approves, or publishes. */
export function renderTourPacket(input) {
  assertExactObject(input, TOP_LEVEL_FIELDS, "packet");
  const asOf = timestamp(input.as_of, "as_of");
  // No caveat reaches a client (V5-J303 ruling; no-caveats rule cbb267fb).
  // The database packet carries caveat as an explicit null; anything else is
  // refused rather than printed.
  if (input.caveat != null) reject("tour_packet_forbidden_field", { path: "packet.caveat" });
  const caveat = null;
  if (!Array.isArray(input.properties) || input.properties.length === 0 || input.properties.length > MAX_PROPERTIES)
    reject("tour_packet_property_count_invalid", { count: Array.isArray(input.properties) ? input.properties.length : null, maximum: MAX_PROPERTIES });
  const properties = input.properties.map((property, index) => canonicalProperty(property, index, asOf));
  const routeSequences = new Set(properties.map(property => property.route_sequence));
  const propertyRefs = new Set(properties.map(property => property.property_ref));
  if (routeSequences.size !== properties.length) reject("tour_packet_duplicate_route_sequence", {});
  if (propertyRefs.size !== properties.length) reject("tour_packet_duplicate_property_ref", {});
  properties.sort((left, right) => left.route_sequence - right.route_sequence);
  const markers = properties.map(markerFor);
  const html = `<!doctype html>\n<html lang="en">\n<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>CARR Property Tour Packet</title>${STYLE}</head>\n<body data-artifact-type="tour-packet" data-render-version="${TOUR_PACKET_RENDER_VERSION}" data-template-version="${TOUR_PACKET_TEMPLATE_VERSION}" data-property-count="${properties.length}">\n<main class="packet-content">\n${properties.map(property => page(property, markerFor(property))).join("\n")}\n</main>\n</body>\n</html>\n`;
  return Object.freeze({
    rendererVersion: TOUR_PACKET_RENDER_VERSION,
    templateVersion: TOUR_PACKET_TEMPLATE_VERSION,
    artifactType: "tour-packet",
    html,
    propertyCount: properties.length,
    markers: Object.freeze(markers),
    propertyRefs: Object.freeze(properties.map(property => property.property_ref)),
    facts: Object.freeze({ as_of: asOf, caveat, properties: Object.freeze(properties.map(property => Object.freeze({ ...property }))) }),
  });
}
