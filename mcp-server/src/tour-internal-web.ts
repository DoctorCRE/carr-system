// Authenticated internal Tour Operations browser adapter.  This is purposely
// an injectable leaf beneath Deal Room's established cookie/session + CSRF
// gate: it owns no identity, authority selection, database, routing, or map
// implementation.

import { routeTimestampMicroseconds, validRouteTimestamp } from "./tour-route-timestamp.ts";

type Actor = { id?: unknown; slug?: unknown };
type Session = { csrfToken?: unknown };
type Environment = {
  APP_HOST?: unknown;
  DEALROOM_HOST?: unknown;
  ASSETS?: { fetch(request: Request): Promise<Response> };
};
type Context = { env: Environment; ctx: unknown; actor: Actor | undefined; input: Record<string, unknown> | null };
type Seam = (context: Context) => Promise<unknown>;
type FailureRecord = {
  event: string;
  route: string | null;
  status: number;
  error_class: string | null;
  code: string | null;
  phase?: string;
};
type Dependencies = Partial<Record<(typeof SEAMS)[keyof typeof SEAMS] | "appendTourSelectionCartVersionFn", Seam>> & {
  reportFailureFn?: (record: FailureRecord) => unknown;
};

const MAX_BODY_BYTES = 32 * 1024;
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const SHARE_SCOPES = new Set(["view_packet", "view_map", "shortlist", "comment", "download"]);
const AUTHORITY_FIELDS = new Set([
  "actor",
  "actor_id",
  "tenant",
  "tenant_id",
  "organization_tenant_id",
  "authorization",
  "authorization_class",
  "identity",
  "reviewer",
  "sponsor",
  "human_slug",
]);

const STATIC = new Map([
  ["/tours", "/tours/index.html"],
  ["/tours/app.js", "/tours/app.js"],
  ["/tours/app.css", "/tours/app.css"],
  ["/tours/property-panel.js", "/tours/property-panel.js"],
  ["/tours/property-panel.css", "/tours/property-panel.css"],
]);
const METHODS = new Map([
  ["/api/tours/library", "GET"],
  ["/api/tours/detail", "GET"],
  ["/api/tours/cheat-sheet/history/v1", "GET"],
  ["/api/tours/property-evidence/v1", "GET"],
  ["/api/tours/properties/search", "POST"],
  ["/api/tours/selection-cart", "GET, POST"],
  ["/api/tours/create", "POST"],
  ["/api/tours/route-draft", "POST"],
  ["/api/tours/route-stop", "POST"],
  ["/api/tours/route-stop-transition", "POST"],
  ["/api/tours/route-version", "POST"],
  ["/api/tours/route-reorder", "POST"],
  ["/api/tours/route-accept", "POST"],
  ["/api/tours/cheat-sheet/autosave", "POST"],
  ["/api/tours/cheat-sheet/restore", "POST"],
  ["/api/tours/projection", "POST"],
  ["/api/tours/projection/candidates", "GET"],
  ["/api/tours/projection/seal", "POST"],
  ["/api/tours/share/issue", "POST"],
  ["/api/tours/share/rotate", "POST"],
  ["/api/tours/share/revoke", "POST"],
  ["/api/tours/feedback", "GET"],
  ["/api/tours/pdf/render", "POST"],
  ["/api/tours/pdf/status", "GET"],
  ["/api/tours/pdf/review", "POST"],
  ["/api/tours/pdf/preview", "GET"],
  ["/api/tours/pdf/download", "GET"],
]);

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && (typeof value === "object" || typeof value === "function");
}
function object(value: unknown): Record<string, unknown> {
  return record(value) ? value : {};
}
function member(set: Set<string>, value: unknown): boolean {
  return typeof value === "string" && set.has(value);
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
}

function methodNotAllowed(allow: string) {
  return json({ error: "method_not_allowed" }, 405, { allow });
}
function plain(value: unknown): value is Record<string, unknown> {
  return (
    Boolean(value) &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype
  );
}
function validId(value: unknown): value is string {
  return typeof value === "string" && ID.test(value);
}
function validDigest(value: unknown): value is string {
  return typeof value === "string" && DIGEST.test(value);
}
function hasAuthoritySelector(value: unknown) {
  return plain(value) && Object.keys(value).some((key) => AUTHORITY_FIELDS.has(key));
}

function sameOriginPost(request: Request, origin: string) {
  return request.headers.get("origin") === origin && request.headers.get("sec-fetch-site") === "same-origin";
}
function equal(left: unknown, right: unknown) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let mismatch = 0;
  for (let index = 0; index < left.length; index += 1) mismatch |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return mismatch === 0;
}
function originFor(request: Request, env: Environment) {
  const host = env?.APP_HOST || env?.DEALROOM_HOST;
  if (typeof host !== "string" || !/^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$/i.test(host)) return null;
  const origin = `https://${host}`;
  try {
    return new URL(request.url).origin === origin ? origin : null;
  } catch {
    return null;
  }
}
function actorSession(actor: Actor | undefined, session: Session | undefined) {
  // Deal Room verified actors are keyed by slug; integrations may supply a durable id.
  const actorKey = actor && (actor.id || actor.slug);
  return Boolean(
    typeof actorKey === "string" && actorKey && session && typeof session.csrfToken === "string" && session.csrfToken,
  );
}

async function body(request: Request) {
  if (!/^application\/json(?:\s*;|$)/i.test(request.headers.get("content-type") || ""))
    return { error: json({ error: "unsupported_media_type" }, 415) };
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && (length < 0 || length > MAX_BODY_BYTES))
    return { error: json({ error: "payload_too_large" }, 413) };
  const raw = await request.clone().text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES)
    return { error: json({ error: "payload_too_large" }, 413) };
  try {
    const value: unknown = JSON.parse(raw);
    return plain(value) ? { value } : { error: json({ error: "invalid_request" }, 400) };
  } catch {
    return { error: json({ error: "invalid_json" }, 400) };
  }
}

function exact<K extends string>(value: unknown, keys: K[]): value is Record<K, unknown> {
  if (!plain(value) || hasAuthoritySelector(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
function ids(value: unknown, min = 1, max = 100) {
  return (
    Array.isArray(value) &&
    value.length >= min &&
    value.length <= max &&
    new Set(value).size === value.length &&
    value.every(validId)
  );
}
const COUNTIES = new Set(["Escambia", "Santa Rosa", "Okaloosa", "Walton", "Bay"]);
const AVAILABILITY = new Set(["available", "coming_soon", "under_contract", "unknown"]);
const SORT = new Set(["updated_desc", "address_asc", "size_asc", "size_desc"]);
function distinctList(value: unknown, allowed: Set<string> | null, maximum: number, pattern?: RegExp) {
  return (
    Array.isArray(value) &&
    value.length <= maximum &&
    new Set(value).size === value.length &&
    value.every((item) => typeof item === "string" && (allowed ? allowed.has(item) : Boolean(pattern?.test(item))))
  );
}
function optionalSize(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 100_000_000);
}
function optionalBoolean(value: unknown) {
  return value === null || typeof value === "boolean";
}
function validSearch(value: unknown) {
  return (
    exact(value, [
      "query",
      "counties",
      "property_types",
      "min_square_feet",
      "max_square_feet",
      "availability",
      "entrance_verified",
      "public_projection_ready",
      "photos_available",
      "sort",
      "cursor",
      "limit",
    ]) &&
    (value.query === null ||
      (typeof value.query === "string" &&
        value.query.trim().length > 0 &&
        value.query.length <= 200 &&
        ![...value.query].some((character) => character.charCodeAt(0) <= 31 || character.charCodeAt(0) === 127))) &&
    distinctList(value.counties, COUNTIES, 5) &&
    distinctList(value.property_types, null, 12, /^[a-z0-9][a-z0-9_-]{0,63}$/) &&
    optionalSize(value.min_square_feet) &&
    optionalSize(value.max_square_feet) &&
    (value.min_square_feet === null ||
      value.max_square_feet === null ||
      value.min_square_feet <= value.max_square_feet) &&
    distinctList(value.availability, AVAILABILITY, 4) &&
    optionalBoolean(value.entrance_verified) &&
    optionalBoolean(value.public_projection_ready) &&
    optionalBoolean(value.photos_available) &&
    member(SORT, value.sort) &&
    (value.cursor === null || (typeof value.cursor === "string" && /^[0-9]{1,9}$/.test(value.cursor))) &&
    typeof value.limit === "number" &&
    Number.isInteger(value.limit) &&
    value.limit >= 1 &&
    value.limit <= 100
  );
}
function scopes(value: unknown) {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.length <= SHARE_SCOPES.size &&
    new Set(value).size === value.length &&
    value.every((scope) => SHARE_SCOPES.has(scope)) &&
    // Client writes only make sense on a packet the client can read; the database enforces the same rule.
    (!(value.includes("shortlist") || value.includes("comment")) || value.includes("view_packet"))
  );
}
function iso(value: unknown) {
  if (
    typeof value !== "string" ||
    value.length > 64 ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)
  )
    return false;
  const parsed = Date.parse(value);
  const expected = /\.\d{3}Z$/.test(value) ? value : value.replace(/Z$/, ".000Z");
  return Number.isFinite(parsed) && new Date(parsed).toISOString() === expected;
}
function validContent(value: unknown) {
  try {
    return (
      plain(value) &&
      Object.keys(value).length <= 64 &&
      new TextEncoder().encode(JSON.stringify(value)).byteLength <= 24 * 1024
    );
  } catch {
    return false;
  }
}

const SUBJECT_TYPES = new Set(["client", "work", "deal"]);
const STOP_STATES = new Set(["active", "held", "excluded"]);
const ACCESS_STATUS = new Set(["unknown", "candidate", "approved", "excluded"]);
const DISPOSITIONS = new Set(["unchanged", "reordered", "removed", "held", "excluded", "merged", "added"]);
const POINT_ROLES = new Set(["entrance", "driveway", "parking_access", "start", "end"]);
const PRECISIONS = new Set(["unknown", "approximate", "parcel", "building", "address", "entrance", "surveyed"]);
function text(value: unknown, max: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.trim().length <= max;
}
function boundedInt(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max;
}
// Same privacy-bearing reference policy as tour-domain.js point(); a reference the domain
// will always refuse is a 400 here, never a retryable outage.
const PRIVATE_REFERENCE = /(contact|phone|email|internal|client|@)/i;
// The verb re-validates every field; this only stops malformed bodies at the door.
function validPoint(value: unknown, role: string) {
  return (
    exact(value, ["latitude", "longitude", "position_role", "precision_class", "source_ref"]) &&
    value.position_role === role &&
    POINT_ROLES.has(role) &&
    typeof value.latitude === "number" &&
    Number.isFinite(value.latitude) &&
    value.latitude >= -90 &&
    value.latitude <= 90 &&
    typeof value.longitude === "number" &&
    Number.isFinite(value.longitude) &&
    value.longitude >= -180 &&
    value.longitude <= 180 &&
    member(PRECISIONS, value.precision_class) &&
    text(value.source_ref, 500) &&
    !PRIVATE_REFERENCE.test(value.source_ref.trim())
  );
}
function validCreate(v: unknown) {
  return (
    exact(v, [
      "idempotency_key",
      "tour_name",
      "subject_type",
      "subject_id",
      "canonical_dataset_version",
      "start_point",
      "end_point",
    ]) &&
    validId(v.idempotency_key) &&
    text(v.tour_name, 240) &&
    member(SUBJECT_TYPES, v.subject_type) &&
    typeof v.subject_id === "string" &&
    /^[A-Za-z0-9._:-]{1,200}$/.test(v.subject_id) &&
    (v.subject_type !== "deal" || validId(v.subject_id)) &&
    text(v.canonical_dataset_version, 240) &&
    validPoint(v.start_point, "start") &&
    validPoint(v.end_point, "end")
  );
}
function validRouteDraft(v: unknown) {
  return (
    exact(v, [
      "idempotency_key",
      "tour_id",
      "route_version",
      "base_route_version_id",
      "expected_route_version",
      "start_point",
      "end_point",
    ]) &&
    validId(v.idempotency_key) &&
    validId(v.tour_id) &&
    boundedInt(v.route_version, 1, 2_147_483_647) &&
    validId(v.base_route_version_id) &&
    boundedInt(v.expected_route_version, 0, 2_147_483_647) &&
    validPoint(v.start_point, "start") &&
    validPoint(v.end_point, "end")
  );
}
function validRouteStop(v: unknown) {
  if (
    !exact(v, [
      "idempotency_key",
      "route_version_id",
      "property_id",
      "route_sequence",
      "route_label",
      "stop_state",
      "appointment_start",
      "appointment_end",
      "locked_appointment",
      "dwell_minutes",
      "buffer_minutes",
      "access_coordinate_status",
      "assertion_set_digest",
    ])
  )
    return false;
  if (
    !validId(v.idempotency_key) ||
    !validId(v.route_version_id) ||
    !validId(v.property_id) ||
    !member(STOP_STATES, v.stop_state)
  )
    return false;
  const active = v.stop_state === "active";
  // The label is presentation only; the property ID is never derived from or compared to it.
  if (
    active
      ? !(
          boundedInt(v.route_sequence, 1, 2_147_483_647) &&
          typeof v.route_label === "string" &&
          /^[A-Za-z0-9._ -]{1,80}$/.test(v.route_label.trim())
        )
      : !(v.route_sequence === null && v.route_label === null)
  )
    return false;
  const hasStart = v.appointment_start !== null,
    hasEnd = v.appointment_end !== null;
  if (
    hasStart !== hasEnd ||
    (hasStart &&
      (!validRouteTimestamp(v.appointment_start) ||
        !validRouteTimestamp(v.appointment_end) ||
        routeTimestampMicroseconds(v.appointment_end) < routeTimestampMicroseconds(v.appointment_start)))
  )
    return false;
  return (
    typeof v.locked_appointment === "boolean" &&
    (!v.locked_appointment || hasStart) &&
    boundedInt(v.dwell_minutes, 0, 1440) &&
    boundedInt(v.buffer_minutes, 0, 1440) &&
    member(ACCESS_STATUS, v.access_coordinate_status) &&
    validDigest(v.assertion_set_digest)
  );
}
function validRouteStopTransition(v: unknown) {
  if (
    !exact(v, [
      "idempotency_key",
      "old_route_version_id",
      "new_route_version_id",
      "old_route_stop_id",
      "new_route_stop_id",
      "disposition",
    ]) ||
    !validId(v.idempotency_key) ||
    !validId(v.new_route_version_id) ||
    !member(DISPOSITIONS, v.disposition)
  )
    return false;
  const [ov, os, ns] = [v.old_route_version_id, v.old_route_stop_id, v.new_route_stop_id];
  if (![ov, os, ns].every((item) => item === null || validId(item))) return false;
  const d = v.disposition;
  if (d === "added") return ov === null && os === null && ns !== null;
  if (d === "removed") return ov !== null && os !== null && ns === null;
  if (d === "held" || d === "excluded") return ov !== null && os !== null;
  return ov !== null && os !== null && ns !== null;
}

const VALID: Record<string, (value: unknown) => boolean> = {
  "/api/tours/create": validCreate,
  "/api/tours/route-draft": validRouteDraft,
  "/api/tours/route-stop": validRouteStop,
  "/api/tours/route-stop-transition": validRouteStopTransition,
  "/api/tours/properties/search": validSearch,
  "/api/tours/selection-cart": (v) =>
    exact(v, [
      "tour_id",
      "base_selection_version_id",
      "expected_selection_version",
      "property_ids",
      "selection_digest",
      "idempotency_key",
    ]) &&
    validId(v.tour_id) &&
    (v.base_selection_version_id === null || validId(v.base_selection_version_id)) &&
    typeof v.expected_selection_version === "number" &&
    Number.isInteger(v.expected_selection_version) &&
    v.expected_selection_version >= 0 &&
    ids(v.property_ids, 0) &&
    validDigest(v.selection_digest) &&
    validId(v.idempotency_key),
  "/api/tours/route-version": (v) =>
    exact(v, ["tour_id", "expected_route_version", "stop_ids", "idempotency_key"]) &&
    validId(v.tour_id) &&
    typeof v.expected_route_version === "number" &&
    Number.isInteger(v.expected_route_version) &&
    v.expected_route_version >= 0 &&
    ids(v.stop_ids, 1) &&
    validId(v.idempotency_key),
  "/api/tours/route-reorder": (v) =>
    exact(v, ["tour_id", "route_version_id", "expected_route_version", "stop_ids", "idempotency_key"]) &&
    validId(v.tour_id) &&
    validId(v.route_version_id) &&
    typeof v.expected_route_version === "number" &&
    Number.isInteger(v.expected_route_version) &&
    v.expected_route_version >= 0 &&
    ids(v.stop_ids, 1) &&
    validId(v.idempotency_key),
  "/api/tours/route-accept": (v) =>
    exact(v, ["route_version_id", "expected_prior_route_version", "acceptance_digest", "idempotency_key"]) &&
    validId(v.route_version_id) &&
    typeof v.expected_prior_route_version === "number" &&
    Number.isInteger(v.expected_prior_route_version) &&
    v.expected_prior_route_version >= 0 &&
    validDigest(v.acceptance_digest) &&
    validId(v.idempotency_key),
  "/api/tours/cheat-sheet/autosave": (v) =>
    exact(v, ["tour_id", "content", "expected_revision_number", "idempotency_key"]) &&
    validId(v.tour_id) &&
    validContent(v.content) &&
    typeof v.expected_revision_number === "number" &&
    Number.isInteger(v.expected_revision_number) &&
    v.expected_revision_number >= 0 &&
    validId(v.idempotency_key),
  "/api/tours/cheat-sheet/restore": (v) =>
    exact(v, ["tour_id", "restore_revision_id", "expected_revision_number", "idempotency_key"]) &&
    validId(v.tour_id) &&
    validId(v.restore_revision_id) &&
    typeof v.expected_revision_number === "number" &&
    Number.isInteger(v.expected_revision_number) &&
    v.expected_revision_number >= 0 &&
    validId(v.idempotency_key),
  "/api/tours/projection": (v) =>
    exact(v, ["tour_id", "route_version_id", "as_of", "idempotency_key"]) &&
    validId(v.tour_id) &&
    validId(v.route_version_id) &&
    iso(v.as_of) &&
    validId(v.idempotency_key),
  "/api/tours/projection/seal": (v) =>
    exact(v, ["projection_id", "candidate_digest", "receipt_digest", "idempotency_key"]) &&
    validId(v.projection_id) &&
    validDigest(v.candidate_digest) &&
    validDigest(v.receipt_digest) &&
    validId(v.idempotency_key),
  "/api/tours/share/issue": (v) =>
    exact(v, [
      "projection_id",
      "token_digest",
      "permission_scopes",
      "expires_at",
      "receipt_digest",
      "idempotency_key",
    ]) &&
    validId(v.projection_id) &&
    validDigest(v.token_digest) &&
    scopes(v.permission_scopes) &&
    iso(v.expires_at) &&
    validDigest(v.receipt_digest) &&
    validId(v.idempotency_key),
  "/api/tours/share/rotate": (v) =>
    exact(v, [
      "share_grant_id",
      "projection_id",
      "token_digest",
      "permission_scopes",
      "expires_at",
      "receipt_digest",
      "idempotency_key",
    ]) &&
    validId(v.share_grant_id) &&
    validId(v.projection_id) &&
    validDigest(v.token_digest) &&
    scopes(v.permission_scopes) &&
    iso(v.expires_at) &&
    validDigest(v.receipt_digest) &&
    validId(v.idempotency_key),
  "/api/tours/share/revoke": (v) =>
    exact(v, ["share_grant_id", "reason", "revoked_at", "receipt_digest", "idempotency_key"]) &&
    validId(v.share_grant_id) &&
    typeof v.reason === "string" &&
    v.reason.trim().length > 0 &&
    v.reason.length <= 240 &&
    iso(v.revoked_at) &&
    validDigest(v.receipt_digest) &&
    validId(v.idempotency_key),
  "/api/tours/pdf/render": (v) =>
    exact(v, ["projection_id", "idempotency_key"]) && validId(v.projection_id) && validId(v.idempotency_key),
  "/api/tours/pdf/review": (v) =>
    exact(v, [
      "render_job_id",
      "qc_run_digest",
      "decision",
      "reviewed_at",
      "review_receipt_digest",
      "reason",
      "idempotency_key",
    ]) &&
    validId(v.render_job_id) &&
    validDigest(v.qc_run_digest) &&
    (v.decision === "accept" || v.decision === "reject") &&
    iso(v.reviewed_at) &&
    validDigest(v.review_receipt_digest) &&
    typeof v.reason === "string" &&
    v.reason.trim().length > 0 &&
    v.reason.length <= 500 &&
    validId(v.idempotency_key),
};

const SEAMS = {
  "/api/tours/properties/search": "searchTourPropertiesFn",
  "/api/tours/selection-cart": "readTourSelectionCartFn",
  "/api/tours/library": "listToursFn",
  "/api/tours/detail": "readTourFn",
  "/api/tours/cheat-sheet/history/v1": "readCheatSheetHistoryFn",
  "/api/tours/property-evidence/v1": "readPropertyEvidenceFn",
  "/api/tours/create": "createTourFn",
  "/api/tours/route-draft": "openRouteDraftFn",
  "/api/tours/route-stop": "appendRouteStopFn",
  "/api/tours/route-stop-transition": "appendRouteStopTransitionFn",
  "/api/tours/route-version": "createRouteVersionFn",
  "/api/tours/route-reorder": "reorderRouteStopsFn",
  "/api/tours/route-accept": "acceptRouteVersionFn",
  "/api/tours/cheat-sheet/autosave": "autosaveCheatSheetFn",
  "/api/tours/cheat-sheet/restore": "restoreCheatSheetFn",
  "/api/tours/projection": "createProjectionFn",
  "/api/tours/projection/candidates": "readProjectionCandidatesFn",
  "/api/tours/projection/seal": "sealProjectionFn",
  "/api/tours/share/issue": "issueShareGrantFn",
  "/api/tours/share/rotate": "rotateShareGrantFn",
  "/api/tours/share/revoke": "revokeShareGrantFn",
  "/api/tours/feedback": "readFeedbackFn",
  "/api/tours/pdf/render": "renderPdfFn",
  "/api/tours/pdf/status": "readPdfRenderFn",
  "/api/tours/pdf/review": "reviewPdfFn",
  "/api/tours/pdf/preview": "previewPdfFn",
  "/api/tours/pdf/download": "downloadPdfFn",
} as const;

// Server-side failure record. The browser still gets only the coarse status,
// but an operator can now see WHY: the route (from the fixed table above), the
// status, the caught error's class and its ToolError code. Both are admitted
// only when they match a bare identifier shape, and the error MESSAGE is never
// reported: a database driver's message can name hosts, users or databases,
// and a verb's can echo input. No actor, tenant, body, token or header goes
// out. This leaf still writes to no log itself; the production adapter
// supplies `reportFailureFn` and owns the sink.
//
// A route can also fail WITHOUT throwing: a seam returns its own {ok:false}
// (runTourPdfRender's catch persists a "failed" job receipt and returns
// {ok:false,status:500,...} rather than rethrow). `error` is undefined on that
// path, so error_class/code stay null -- there is no caught error object to
// read a class or code off of. That seam still knows WHICH internal phase
// failed (store, verify, record), and reports it on its own successful {ok:
// true,...}-shaped `data`; when present and shaped like a bare identifier,
// surface it here instead of leaving an operator with two nulls.
const ERROR_CLASS = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const ERROR_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const FAILURE_PHASE = new Set(["prepare", "store", "verify", "record"]);
export function tourFailureRecord(pathname: string, status: number, error: unknown, data: unknown) {
  const absent = error === undefined;
  const failure = object(error == null ? error : Object(error));
  const payload = object(failure.payload);
  const errorClass = absent
    ? null
    : ERROR_CLASS.test(typeof failure.name === "string" ? failure.name : "")
      ? String(failure.name)
      : error !== null && ERROR_CLASS.test(failure.constructor?.name || "")
        ? failure.constructor.name
        : "UnknownError";
  const code = typeof payload.error === "string" && ERROR_CODE.test(payload.error) ? payload.error : null;
  const record: FailureRecord = {
    event: "tour_internal_failure",
    route: METHODS.has(pathname) ? pathname : null,
    status,
    error_class: errorClass,
    code,
  };
  if (error === undefined && typeof object(data).phase === "string" && member(FAILURE_PHASE, object(data).phase))
    record.phase = String(object(data).phase);
  return record;
}
function safeFailure(result: unknown, pathname: string, error: unknown, report: Dependencies["reportFailureFn"]) {
  const outcome = object(result);
  const status =
    typeof outcome.status === "number" && [400, 403, 404, 409].includes(outcome.status) ? outcome.status : 503;
  if (typeof report === "function") {
    try {
      report(tourFailureRecord(pathname, status, error, outcome.data));
    } catch {}
  }
  if (status === 400) return json({ error: "invalid_request" }, 400);
  return json(
    {
      error:
        status === 403 ? "forbidden" : status === 404 ? "not_found" : status === 409 ? "conflict" : "tour_unavailable",
    },
    status,
  );
}
const CONFLICT_MESSAGES = new Set([
  "tour route preparation refuses stale state",
  "route version refuses concurrent or stale route state",
  "route acceptance refuses concurrent or stale route state",
  "route acceptance refuses changed draft contents",
  "cheat sheet revision refuses concurrent or stale version",
  "cheat sheet restore refuses unavailable or stale revision",
  "tour selection refuses stale version",
  "route stop cannot alter an accepted route version",
  "route transition cannot alter an accepted route version",
  // Precondition refusals: the editor's view of the route is out of date or incomplete, so reload.
  "route version base is invalid",
  "route acceptance requires at least one active stop",
  "route acceptance requires an explicit transition for every new route stop",
  "route acceptance requires an explicit disposition for every prior route stop",
]);
// Typed domain refusals raised before any write: retrying the same input can never succeed.
const VALIDATION_CODES = new Set([
  "tour_input_invalid",
  "tour_input_unknown_field",
  "tour_point_invalid",
  "tour_stop_state_invalid",
  "tour_appointment_invalid",
  "tour_transition_invalid",
  "tour_provider_tuple_invalid",
  "tour_routing_request_invalid",
  "caller_authority_field_forbidden",
]);
// Deterministic SQL refusals of a transition's shape: the same input can never succeed.
const VALIDATION_MESSAGES = new Set([
  "tour deal is unavailable",
  "reordered route transition requires a sequence change",
  "unchanged route transition requires the same sequence",
  "route transition property identity mismatch",
  "merged route transition requires explicit property identity lineage",
]);
function dependencyFailure(error: unknown) {
  const failure = object(error == null ? error : Object(error));
  const payload = object(failure.payload);
  const code = typeof payload.error === "string" ? payload.error : "";
  const message = typeof failure.message === "string" ? failure.message : "";
  if (VALIDATION_CODES.has(code) || VALIDATION_MESSAGES.has(message)) return { status: 400 };
  return {
    status:
      code === "tour_selection_cart_not_found"
        ? 404
        : code === "version_conflict" || code === "key_reuse" || CONFLICT_MESSAGES.has(message)
          ? 409
          : 503,
  };
}
async function staticAsset(env: Environment, request: Request, pathname: string) {
  if (!env?.ASSETS?.fetch) return json({ error: "not_found" }, 404);
  const url = new URL(request.url);
  url.pathname = pathname;
  url.search = "";
  const response = await env.ASSETS.fetch(new Request(url, { method: "GET" }));
  if (response.status === 404) return json({ error: "not_found" }, 404);
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store");
  if (pathname.endsWith(".html")) headers.set("content-type", "text/html; charset=utf-8");
  if (pathname.endsWith(".js")) headers.set("content-type", "application/javascript; charset=utf-8");
  if (pathname.endsWith(".css")) headers.set("content-type", "text/css; charset=utf-8");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function api(
  request: Request,
  env: Environment,
  ctx: unknown,
  actor: Actor | undefined,
  session: Session | undefined,
  dependencies: Dependencies,
  pathname: string,
) {
  const origin = originFor(request, env);
  if (!origin || !actorSession(actor, session) || !session) return json({ error: "unauthorized" }, 401);
  const seamName =
    pathname === "/api/tours/selection-cart" && request.method === "POST"
      ? "appendTourSelectionCartVersionFn"
      : Object.entries(SEAMS).find(([path]) => path === pathname)?.[1];
  const seam = seamName ? dependencies[seamName] : undefined;
  if (typeof seam !== "function") return json({ error: "not_found" }, 404);
  let input: Record<string, unknown> | null = null;
  if (request.method === "GET") {
    const url = new URL(request.url);
    if (
      pathname === "/api/tours/detail" ||
      pathname === "/api/tours/selection-cart" ||
      pathname === "/api/tours/cheat-sheet/history/v1"
    ) {
      if ([...url.searchParams.keys()].length !== 1 || !validId(url.searchParams.get("tour_id")))
        return json({ error: "invalid_request" }, 400);
      input = { tour_id: url.searchParams.get("tour_id") };
    } else if (pathname === "/api/tours/property-evidence/v1") {
      const keys = [...url.searchParams.keys()].sort();
      if (
        keys.join(",") !== "as_of,property_id" ||
        !validId(url.searchParams.get("property_id")) ||
        !iso(url.searchParams.get("as_of"))
      )
        return json({ error: "invalid_request" }, 400);
      input = { property_id: url.searchParams.get("property_id"), as_of: url.searchParams.get("as_of") };
    } else if (pathname === "/api/tours/projection/candidates" || pathname === "/api/tours/feedback") {
      if ([...url.searchParams.keys()].length !== 1 || !validId(url.searchParams.get("projection_id")))
        return json({ error: "invalid_request" }, 400);
      input = { projection_id: url.searchParams.get("projection_id") };
    } else if (
      pathname === "/api/tours/pdf/status" ||
      pathname === "/api/tours/pdf/preview" ||
      pathname === "/api/tours/pdf/download"
    ) {
      if ([...url.searchParams.keys()].length !== 1 || !validId(url.searchParams.get("render_job_id")))
        return json({ error: "invalid_request" }, 400);
      input = { render_job_id: url.searchParams.get("render_job_id") };
    } else if ([...url.searchParams.keys()].length) return json({ error: "invalid_request" }, 400);
  } else {
    if (!sameOriginPost(request, origin) || !equal(request.headers.get("x-carr-csrf"), session.csrfToken))
      return json({ error: "forbidden" }, 403);
    const parsed = await body(request);
    if (parsed.error) return parsed.error;
    if (!VALID[pathname]?.(parsed.value)) return json({ error: "invalid_request" }, 400);
    input = parsed.value;
  }
  try {
    const rawResult = await seam({ env, ctx, actor, input });
    const result = object(rawResult);
    if (
      (pathname === "/api/tours/pdf/preview" || pathname === "/api/tours/pdf/download") &&
      result?.ok &&
      result.response instanceof Response
    )
      return result.response;
    if (!result?.ok || !plain(result.data))
      return safeFailure(result, pathname, undefined, dependencies.reportFailureFn);
    return json({ data: result.data, csrf_token: request.method === "GET" ? session.csrfToken : undefined });
  } catch (error) {
    return safeFailure(dependencyFailure(error), pathname, error, dependencies.reportFailureFn);
  }
}

function withSecurityHeaders(response: Response) {
  const headers = new Headers(response.headers);
  headers.set("cache-control", "no-store");
  headers.set(
    "content-security-policy",
    "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; worker-src 'none'",
  );
  headers.set("strict-transport-security", "max-age=31536000; includeSubDomains");
  headers.set("permissions-policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()");
  headers.set("x-content-type-options", "nosniff");
  headers.set("x-frame-options", "DENY");
  headers.set("referrer-policy", "no-referrer");
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

/** Create the bounded Tour leaf. The caller supplies verified actor/session context. */
export function createTourInternalWebHandler(overrides: Dependencies = {}) {
  return {
    fetch: async (request: Request, env: Environment, _ctx: unknown, actor?: Actor, session?: Session) => {
      let url: URL;
      try {
        url = new URL(request.url);
      } catch {
        return withSecurityHeaders(json({ error: "not_found" }, 404));
      }
      const asset = STATIC.get(url.pathname);
      if (asset) {
        if (!originFor(request, env) || !actorSession(actor, session))
          return withSecurityHeaders(json({ error: "unauthorized" }, 401));
        return withSecurityHeaders(
          request.method === "GET" ? await staticAsset(env, request, asset) : methodNotAllowed("GET"),
        );
      }
      const method = METHODS.get(url.pathname);
      if (!method) return withSecurityHeaders(json({ error: "not_found" }, 404));
      if (!method.split(", ").includes(request.method)) return withSecurityHeaders(methodNotAllowed(method));
      return withSecurityHeaders(await api(request, env, _ctx, actor, session, overrides, url.pathname));
    },
  };
}

export function isTourInternalRequest(request: Request) {
  try {
    const path = new URL(request.url).pathname;
    return STATIC.has(path) || METHODS.has(path);
  } catch {
    return false;
  }
}
