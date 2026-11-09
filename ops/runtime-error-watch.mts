// Reconcile runtime errors into incident records.

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const ENVIRONMENTS = new Set(["local", "rehearsal", "staging", "production"]);
const FAILURE_CLASSES = new Set(["http_5xx", "verb_internal_error", "actor_unresolved"]);
const INCIDENT_REF = /^INC-[0-9]{8}-[0-9]+$/;
const UUID = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

function key(ref: string, operation: string) {
  const hex = createHash("sha256").update(`carr-runtime-error/v1|${ref}|${operation}`).digest("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-8${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

type RecordCall = (verb: string, args: Record<string, unknown>) => Promise<unknown>;

function object(value: unknown, error: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error(error);
  return Object.fromEntries(Object.entries(value));
}

async function callRecord(verb: string, args: Record<string, unknown>) {
  const { stdout } = await promisify(execFile)(
    fileURLToPath(new URL("../run.sh", import.meta.url)),
    ["call", verb, JSON.stringify(args)],
    { timeout: 60000, maxBuffer: 8 * 1024 * 1024 },
  );
  const reply = object(JSON.parse(stdout), "record_call_refused");
  if (reply.ok === false || reply.error) throw new Error("record_call_refused");
  return reply;
}

export async function reconcileRuntimeErrors(call: RecordCall = callRecord) {
  const rows: Record<string, unknown>[] = [],
    cursors = new Set<string | null | undefined>();
  let cursor: string | null | undefined, expected: number | undefined;
  do {
    const page = object(
      await call("unfinished-work", { kinds: "incident", text: "carr-mcp", limit: 500, ...(cursor ? { cursor } : {}) }),
      "runtime_incident_census_incomplete",
    );
    const coverage = Array.isArray(page.coverage)
      ? page.coverage
          .map((row: unknown) => object(row, "runtime_incident_census_incomplete"))
          .filter((row) => row.kind === "incident")
      : [];
    const census = coverage[0];
    if (
      page.ok !== true ||
      coverage.length !== 1 ||
      !census ||
      census.state !== "complete" ||
      typeof census.count_total !== "number" ||
      !Number.isSafeInteger(census.count_total) ||
      !Array.isArray(page.items)
    )
      throw new Error("runtime_incident_census_incomplete");
    expected ??= census.count_total;
    if (expected !== census.count_total) throw new Error("runtime_incident_census_changed");
    rows.push(...page.items.map((row: unknown) => object(row, "runtime_incident_ref_invalid")));
    if (page.next_cursor != null && typeof page.next_cursor !== "string")
      throw new Error("runtime_incident_census_incomplete");
    cursor = page.next_cursor;
    if (cursor && cursors.has(cursor)) throw new Error("runtime_incident_cursor_repeated");
    cursors.add(cursor);
  } while (cursor);
  if (rows.length !== expected || new Set(rows.map((row) => row.id)).size !== expected)
    throw new Error("runtime_incident_census_short");

  let linked = 0;
  for (const row of rows) {
    if (row.kind !== "incident" || typeof row.id !== "string" || !INCIDENT_REF.test(row.id))
      throw new Error("runtime_incident_ref_invalid");
    const card = object(await call("get-incident", { ref: row.id, fact_limit: 1 }), "runtime_incident_card_incomplete");
    const incident = object(card.incident, "runtime_incident_card_incomplete");
    if (!Array.isArray(card.links)) throw new Error("runtime_incident_card_incomplete");
    if (incident.fingerprint != null && typeof incident.fingerprint !== "string")
      throw new Error("runtime_incident_card_incomplete");
    const fingerprint = typeof incident.fingerprint === "string" ? incident.fingerprint : "";
    const failureClass = fingerprint.split("|").at(-1);
    if (
      incident.source_ref !== "mcp-server/src/trace.js" ||
      incident.detected_source !== "carr-mcp-worker" ||
      typeof incident.environment !== "string" ||
      !ENVIRONMENTS.has(incident.environment) ||
      !failureClass ||
      !FAILURE_CLASSES.has(failureClass) ||
      !fingerprint.startsWith(`carr-mcp|${incident.environment}|`) ||
      incident.state === "resolved" ||
      incident.state === "reviewed" ||
      card.links.some((link: unknown) => object(link, "runtime_incident_card_incomplete").kind === "work_request")
    )
      continue;
    if (incident.ref !== row.id) throw new Error("runtime_incident_identity_mismatch");
    const signal = { ref: incident.ref, environment: incident.environment, failure_class: failureClass };
    const finding = object(
      await call("record-finding", {
        idempotency_key: key(signal.ref, "finding"),
        subject: "repo:DoctorCRE/carr-system",
        kind: "runtime_error",
        internal: true,
        epistemic_status: "observed",
        source: `incident:${signal.ref}`,
        value: { schema: "carr.runtime-error.v1", ...signal },
      }),
      "runtime_finding_receipt_invalid",
    );
    if (typeof finding.flag_id !== "string" || !UUID.test(finding.flag_id))
      throw new Error("runtime_finding_receipt_invalid");
    const request = object(
      await call("report-problem", {
        idempotency_key: key(signal.ref, "work-request"),
        situation: "DoctorCRE Worker runtime failure requires investigation, repair and regression verification.",
        title: `Worker runtime failure ${signal.ref} (${signal.environment})`,
        desired_outcome:
          `Address runtime finding ${finding.flag_id} from incident ${signal.ref} for DoctorCRE delivery. ` +
          "Record a verified fix or an evidence-backed disposition in this Work Request. Read the incident for diagnosis.",
        acceptance_criteria: [
          {
            id: "AC-CAUSE",
            text: "Identify the runtime failure cause and record a fix or an evidence-backed disposition.",
          },
          { id: "AC-PROOF", text: "Verify the affected behavior and a regression test using redacted evidence." },
        ],
      }),
      "runtime_work_request_receipt_invalid",
    );
    if (typeof request.human_ref !== "string" || !/^WR-[0-9]{1,12}$/.test(request.human_ref))
      throw new Error("runtime_work_request_receipt_invalid");
    const link = object(
      await call("link-incident-work-request", {
        idempotency_key: key(signal.ref, "link"),
        incident_ref: signal.ref,
        work_request: request.human_ref,
      }),
      "runtime_link_receipt_invalid",
    );
    if (link.ok !== true || link.incident_ref !== signal.ref || link.work_request !== request.human_ref)
      throw new Error("runtime_link_receipt_invalid");
    linked += 1;
    if (linked === 10) break;
  }
  return { linked };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    console.log(JSON.stringify(await reconcileRuntimeErrors()));
  } catch {
    console.error("runtime-error-watch: reconciliation failed; durable incidents remain pending");
    process.exitCode = 1;
  }
}
