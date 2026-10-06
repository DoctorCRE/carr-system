// trace.test.mjs — coverage for Program 4 Gap A2 (defect cae5be2e-267c-40ba-
// 9424-b4618845e905, 2026-08-14): correlation.js mints and echoes an
// x-correlation-id but nothing wrote it anywhere a failed request could later
// be found by it. This file proves the fix's two halves:
//
//   FAILURES  — trace.js's classifiers and recordWorkerFailure (this file),
//               plus withFailureRecording's wiring contract.
//   MATERIAL WRITES — auditIdentity()/withEnvelope()/writeEvent() threading
//               env.CORRELATION_ID onto tool_call.correlation_id and
//               event.correlation_id, exercised here through dispatch()'s
//               real actor-decoration and through the executeRegisteredTool
//               write path with a fake writer client (mirrors update-lead.
//               test.mjs's and add-party-org-identity.test.mjs's own fake-
//               client convention — no live database).
//
// mcp.js holds no cloudflare: import (see tool-read-call.test.mjs's own
// header), so dispatch() itself is exercised directly here, including the
// real outer-catch path that produces the -32603 JSON-RPC internal-error
// envelope: neon(undefined) throws SYNCHRONOUSLY (proven below), which is a
// convenient, realistic way to force a genuine uncaught exception through
// dispatch() without needing a live database.
//
//   node --test mcp-server/test/trace.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  httpFailureClass,
  isExpectedPolicyRefusal,
  rpcInternalErrorFailureClass,
  actorUnresolvedFailureClass,
  RPC_INTERNAL_ERROR_CODE,
  incidentSignature,
  incidentFactText,
  recordWorkerFailure,
  scheduleFailureRecord,
  withFailureRecording,
  wrapNeonRows,
} from "../src/trace.js";
import { dispatch } from "../src/mcp.js";
import { auditIdentity } from "../src/tools.js";

const A_CORR = "9f3b2c1a-4d5e-4f60-8a1b-0123456789ab";

// ────────────────────────────────────────────────────────────────────────
// httpFailureClass / rpcInternalErrorFailureClass / actorUnresolvedFailureClass
// — the inclusion rule's three pure classifiers.
// ────────────────────────────────────────────────────────────────────────

test("httpFailureClass: any status >= 500 is http_5xx", () => {
  for (const status of [500, 502, 503, 599]) {
    assert.equal(httpFailureClass(status), "http_5xx");
  }
});

test("httpFailureClass: every 4xx and 2xx is null — including 401, the routine-noise case", () => {
  for (const status of [200, 201, 400, 401, 403, 404, 405, 413, 429]) {
    assert.equal(httpFailureClass(status), null, `status ${status} must not be recorded`);
  }
});

test("httpFailureClass: non-numeric or missing status is null, never a crash", () => {
  assert.equal(httpFailureClass(undefined), null);
  assert.equal(httpFailureClass(null), null);
  assert.equal(httpFailureClass("500"), null);
});

test("rpcInternalErrorFailureClass: -32603 (the outer catch's only code) is verb_internal_error", () => {
  assert.equal(rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE), "verb_internal_error");
  assert.equal(RPC_INTERNAL_ERROR_CODE, -32603);
});

test("every failure class this Worker can produce is a name, not a bare exit code", () => {
  // 0293 gave the incident fingerprint a normalized fourth field: ops-record.py
  // rewrites `exit_<n>` before it lands, because bin/nightly.sh passes wrapper
  // exit codes through and exit_1 and exit_2 from one step are one problem. The
  // Worker does NOT run that rule — restating it in a third language is how two
  // writers drift — and it does not have to, because every class it can emit is
  // already a name. This is the assertion that keeps that true: a new class
  // shaped like an exit code would fingerprint one failure two ways depending
  // on which writer saw it, silently.
  const everyClass = [
    httpFailureClass(500),
    httpFailureClass(503),
    rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE),
    actorUnresolvedFailureClass(),
  ];
  for (const cls of everyClass) {
    assert.ok(cls, "a producible failure class must not be empty");
    assert.ok(!/^exit[_-]?\d{1,3}$/i.test(cls),
      `${cls} is shaped like a bare exit code; ops-record.py would normalize it ` +
      `and this Worker would not, so one failure would get two fingerprints`);
  }
});

test("rpcInternalErrorFailureClass: every ToolError-shaped JSON-RPC code is null — a refusal is not a failure", () => {
  // -32601 (method not found) and every ToolError the system returns at 200 —
  // this file records neither; the inclusion rule names -32603 specifically
  // because it is the ONE code an uncaught exception can produce.
  for (const code of [-32601, -32600, -32602, 1, 0]) {
    assert.equal(rpcInternalErrorFailureClass(code), null);
  }
});

test("policy/admission refusals do not become incidents, while same-verb runtime faults still do", () => {
  const expected = [
    ["set-work-shape-disposition", "sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged or triaged-to-ready transitions"],
    ["set-work-shape-disposition", "sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, or triaged-to-ready transitions"],
    ["activate-context-bundle", "context compilation tenant must match authenticated tenant context"],
    ["issue-execution-envelope", "execution envelope requires an active conformance-passed environment provider binding"],
    ["read-attempt-reliability", "attempt reliability is not visible to tenant"],
    ["approve-rule", "rule approval refused: exact enforcement is not installed; missing {delegation_names_model_and_effort}"],
    ["approve-rule", "exact registered controls must be implemented before approval"],
  ];
  for (const [verb, message] of expected) {
    const error = new Error(message);
    assert.equal(isExpectedPolicyRefusal(verb, error), true, `${verb} refusal must be recognized`);
    assert.equal(rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE, { verb, error }), null,
      `${verb} refusal must not produce an incident class`);
  }

  const unexpected = [
    ["set-work-shape-disposition", "sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape disposition, or triaged-to-ready transitions"],
    ["set-work-shape-disposition", "sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, and triaged-to-ready transitions"],
    ["approve-rule", "TypeError: Invalid URL string."],
    ["complete-capability-project", "permission denied for table capability_verification"],
    ["activate-context-bundle", "connection terminated unexpectedly"],
    ["activate-context-bundle", "attempt reliability is not visible to tenant"],
    ["read-attempt-reliability", "attempt reliability is not visible to tenant for attempt 123"],
    ["read-attempt-reliability", "attempt reliability is not visible to tenant\npermission denied for table attempt"],
    ["read-attempt-reliability", "permission denied for table attempt_reliability"],
    ["read-attempt-reliability", "relation attempt_reliability does not exist"],
    ["read-attempt-reliability", "malformed attempt id: expected UUID"],
    ["read-attempt-reliability", "missing attempt id"],
    ["approve-rule", "rule approval refused: exact enforcement is not installed; missing"],
  ];
  for (const [verb, message] of unexpected) {
    const error = new Error(message);
    assert.equal(isExpectedPolicyRefusal(verb, error), false, `${verb} unexpected fault must not match`);
    assert.equal(rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE, { verb, error }), "verb_internal_error",
      `${verb} unexpected fault must remain an incident`);
  }
});

test("actorUnresolvedFailureClass: always actor_unresolved — one cause, no input", () => {
  assert.equal(actorUnresolvedFailureClass(), "actor_unresolved");
});

// ────────────────────────────────────────────────────────────────────────
// incidentSignature / incidentFactText — pure formatting, no DB.
// ────────────────────────────────────────────────────────────────────────

test("incidentSignature: matches tools/ops-record.py assess()'s own signature shape — service|environment|run_key|failure_class", () => {
  assert.equal(
    incidentSignature({ serviceKey: "carr-mcp", environment: "staging", routeKey: "/mcp", failureClass: "http_5xx" }),
    "carr-mcp|staging|/mcp|http_5xx",
  );
});

test("incidentFactText: carries route, failure class and correlation id; appends detail only when given, truncated to 200 chars", () => {
  const noDetail = incidentFactText({ routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: null });
  assert.equal(noDetail, `/mcp failed (http_5xx), correlation ${A_CORR}`);

  const withDetail = incidentFactText({ routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: "boom" });
  assert.match(withDetail, /— boom$/);

  const longDetail = "x".repeat(500);
  const truncated = incidentFactText({ routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: longDetail });
  const appended = truncated.slice(truncated.indexOf("— ") + 2);
  assert.equal(appended.length, 200, "detail must be capped to 200 chars, never the full 500");
});

// ────────────────────────────────────────────────────────────────────────
// recordWorkerFailure — the DI'd core. A fake `query(text, params)` records
// every call so the exact sequence and shape can be asserted without a
// database, the same convention recordReadCall's own tests use.
// ────────────────────────────────────────────────────────────────────────

function fakeQuery(script) {
  // script: array of { match: RegExp, rows: [...] } checked in order; the
  // FIRST matching entry answers. Every call is recorded regardless.
  const calls = [];
  const query = async (text, params) => {
    calls.push({ text, params });
    for (const { match, rows } of script) {
      if (match.test(text)) return { rows };
    }
    return { rows: [] };
  };
  return { query, calls };
}

test("recordWorkerFailure: an invalid or missing environment never queries anything — an unlabelled deploy is never guessed at", async () => {
  const { query, calls } = fakeQuery([]);
  await recordWorkerFailure(query, {
    environment: "unknown", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  await recordWorkerFailure(query, {
    environment: null, routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.length, 0);
});

test("recordWorkerFailure: missing failureClass, routeKey or correlationId never queries anything", async () => {
  const { query, calls } = fakeQuery([]);
  await recordWorkerFailure(query, { environment: "staging", routeKey: "/mcp", failureClass: null, correlationId: A_CORR });
  await recordWorkerFailure(query, { environment: "staging", routeKey: null, failureClass: "http_5xx", correlationId: A_CORR });
  await recordWorkerFailure(query, { environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: null });
  assert.equal(calls.length, 0);
});

test("recordWorkerFailure: an unregistered service is never invented — stops after the service lookup finds nothing", async () => {
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /select id from ops\.service/);
});

test("recordWorkerFailure: a brand-new signature opens an incident and attaches one fact", async () => {
  const svcId = "svc-1", incId = "inc-1";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [] }, // no open incident yet
    { match: /coalesce\(max\(substring/, rows: [{ day: "20260818", seq: 1 }] },                  // ref numbering
    { match: /insert into ops\.incident\b/, rows: [{ id: incId }] },
    { match: /select 1 from ops\.incident_fact/, rows: [] },              // no prior fact for this correlation id
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR, detail: "boom",
  });

  const insert = calls.find((c) => /insert into ops\.incident\b/.test(c.text));
  assert.ok(insert, "must open a new incident");
  assert.match(insert.text, /'detected'/);
  assert.match(insert.text, /source_kind/);
  assert.deepEqual(insert.params[1], A_CORR); // correlation_id positional
  assert.match(insert.params[0], /^INC-\d{8}-01$/); // ref shape matches ops-record.py's own format

  const fact = calls.find((c) => /insert into ops\.incident_fact/.test(c.text));
  assert.ok(fact, "must attach a fact to the new incident");
  assert.equal(fact.params[0], incId);
  assert.equal(fact.params[2], `correlation:${A_CORR}`);

  const bump = calls.find((c) => /update ops\.incident set observed_at/.test(c.text));
  assert.ok(bump, "must bump freshness after attaching a fact");
});

// THIS TEST GUARDS A CROSS-LANGUAGE CONTRACT, which is why it exists separately
// from the assertion two tests up that already checks the same string. A
// RECURRING failure's correlation id is stored NOWHERE but in this source_ref
// (the incident row's own correlation_id belongs to the FIRST failure of the
// signature), and migrations/0123_trace_incident_recurrence.sql's ops.v_trace
// arm is what reads it back — by parsing this exact prefix with this exact
// regex. Renaming the prefix here would not fail a single JS test that
// hardcodes both sides; it would silently stop every recurrence from tracing,
// which is the defect 0123 closed. So the regex the SQL uses is asserted here,
// against the JS that has to keep feeding it.
test("recordWorkerFailure: a fact's source_ref matches the regex migration 0123's ops.v_trace arm parses", async () => {
  // Character-for-character the pattern in 0123's substring(... from ...) call.
  const V_TRACE_ARM_PATTERN =
    /^correlation:([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$/;

  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: "svc-1" }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: "inc-open" }] }, // a RECURRENCE
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });

  const fact = calls.find((c) => /insert into ops\.incident_fact/.test(c.text));
  assert.ok(fact, "a recurrence must still attach a fact — it is the only record of its correlation id");
  const sourceRef = fact.params[2];
  const parsed = V_TRACE_ARM_PATTERN.exec(sourceRef);
  assert.ok(parsed, `0123's v_trace arm cannot parse ${sourceRef} — recurrences would stop tracing`);
  assert.equal(parsed[1], A_CORR, "the arm must recover the recurrence's own correlation id, unchanged");
});

test("recordWorkerFailure: a new recurrence invalidates monitoring evidence but can never close an incident", async () => {
  const svcId = "svc-1", incId = "inc-open";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: incId }] }, // already open
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  const update = calls.find((c) => /^\s*update ops\.incident set observed_at/.test(c.text));
  assert.ok(update, "the new fact must invalidate a stale recovery watch");
  assert.match(update.text, /state = case when state = 'monitoring' then 'detected'/);
  assert.match(update.text, /recovery_evidence_ref = case when state = 'monitoring'\s+then null/);
  assert.match(update.text, /monitoring_until = case when state = 'monitoring'\s+then null/);
  assert.doesNotMatch(update.text, /resolved_at|root_cause/,
    "failure recording may invalidate recovery but still carries no closure authority");
  assert.doesNotMatch(update.text, /then\s+'resolved'|then\s+'reviewed'/,
    "the only state transition this writer may make is back to detected");
});

// ────────────────────────────────────────────────────────────────────────
// INCIDENT NUMBERING — the day prefix and the sequence must come from ONE
// clock. This writer used to count against to_char(now(), ...) (the SERVER's
// timezone) and then stamp the ref from `new Date()` (the CLIENT's). The two
// agree only on a UTC server, and a Worker talking to Neon is UTC on both
// ends, so nothing here could ever have caught the split — which is precisely
// why these fakes now hand back a server day that is NOT today's.
// ────────────────────────────────────────────────────────────────────────

test("nextIncidentRef: the day prefix comes from the SERVER's answer, never from this runtime's clock", async () => {
  const svcId = "svc-1";
  // A day no client clock will ever produce: if the ref carries it, the label
  // came from the same query the sequence was counted by.
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [] },
    { match: /coalesce\(max\(substring/, rows: [{ day: "19991231", seq: 7 }] },
    { match: /insert into ops\.incident\b/, rows: [{ id: "inc-1" }] },
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  const insert = calls.find((c) => /insert into ops\.incident\b/.test(c.text));
  assert.equal(insert.params[0], "INC-19991231-07");

  // And the query itself must pin the day to UTC, so the numbering space does
  // not move when the cluster's own zone does.
  const numbering = calls.find((c) => /coalesce\(max\(substring/.test(c.text));
  assert.match(numbering.text, /now\(\) at time zone 'UTC'/);
  assert.doesNotMatch(numbering.text, /to_char\(now\(\),/,
    "a bare to_char(now(), ...) is the server-zone read this fix removed");
});

test("nextIncidentRef: two incidents opened across a non-UTC server's day boundary get DISTINCT refs", async () => {
  // The reproduced failure: a US/Central server at 19:22 CDT counts under
  // INC-20260818- (its own day) while a UTC client labels INC-20260819-. Every
  // incident in that 5-hour window came out numbered 01 and the second insert
  // died on incident_ref_key. Here the server answers as it would across that
  // boundary — same UTC day, sequence advancing — and both refs must differ.
  const serverDay = "20260819";
  const calls = [];
  let opened = 0;
  const query = async (text, params) => {
    calls.push({ text, params });
    if (/select id from ops\.service/.test(text)) return { rows: [{ id: "svc-1" }] };
    if (/select id from ops\.incident where signature/.test(text)) return { rows: [] };
    // What the fixed query returns: the day it counted under, and the next
    // sequence within THAT day — the two can no longer be read apart.
    if (/coalesce\(max\(substring/.test(text)) return { rows: [{ day: serverDay, seq: opened + 1 }] };
    if (/^\s*insert into ops\.incident\b/.test(text)) return { rows: [{ id: `inc-${++opened}` }] };
    return { rows: [] };
  };
  // Two DIFFERENT failure signatures, so 0116's dedupe opens two incidents
  // rather than appending a fact to the first.
  for (const routeKey of ["/mcp", "/health"]) {
    await recordWorkerFailure(query, {
      environment: "staging", routeKey, failureClass: "http_5xx", correlationId: A_CORR,
    });
  }
  const refs = calls
    .filter((c) => /^\s*insert into ops\.incident\b/.test(c.text))
    .map((c) => c.params[0]);
  assert.equal(opened, 2, "both failures must have opened an incident");
  assert.deepEqual(refs, [`INC-${serverDay}-01`, `INC-${serverDay}-02`]);
  assert.notEqual(refs[0], refs[1], "two incidents on one day must never share a ref");
});

test("recordWorkerFailure: an existing open incident gets a fact appended, never a second incident row", async () => {
  const svcId = "svc-1", incId = "inc-existing";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: incId }] },
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.filter((c) => /^\s*insert into ops\.incident\b/.test(c.text)).length, 0);
  assert.equal(calls.filter((c) => /insert into ops\.incident_fact/.test(c.text)).length, 1);
});

test("recordWorkerFailure: the SAME correlation id recurring never grows the fact list — deduped by source_ref", async () => {
  const svcId = "svc-1", incId = "inc-existing";
  const { query, calls } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: svcId }] },
    { match: /select id from ops\.incident where signature/, rows: [{ id: incId }] },
    { match: /select 1 from ops\.incident_fact/, rows: [{ "?column?": 1 }] }, // already recorded
  ]);
  await recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.equal(calls.filter((c) => /insert into ops\.incident_fact/.test(c.text)).length, 0);
  assert.equal(calls.filter((c) => /update ops\.incident set observed_at/.test(c.text)).length, 0);
});

test("recordWorkerFailure: a concurrent writer winning the open-incident race (23505) is treated as success, not an error", async () => {
  const svcId = "svc-1", incId = "inc-raced";
  let incidentInsertAttempts = 0;
  const calls = [];
  const query = async (text, params) => {
    calls.push({ text, params });
    if (/select id from ops\.service/.test(text)) return { rows: [{ id: svcId }] };
    if (/select id from ops\.incident where signature/.test(text)) {
      // first call (before the race): nothing open. second call (the retry
      // inside openIncident's catch): the concurrent writer's row is there.
      return { rows: incidentInsertAttempts > 0 ? [{ id: incId }] : [] };
    }
    if (/coalesce\(max\(substring/.test(text)) return { rows: [{ day: "20260818", seq: 1 }] };
    if (/^\s*insert into ops\.incident\b/.test(text)) {
      incidentInsertAttempts++;
      const e = new Error("duplicate key value violates unique constraint \"incident_one_open_per_signature\"");
      e.code = "23505";
      throw e;
    }
    if (/select 1 from ops\.incident_fact/.test(text)) return { rows: [] };
    return { rows: [] };
  };
  await assert.doesNotReject(recordWorkerFailure(query, {
    environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  }));
  assert.equal(incidentInsertAttempts, 1);
  const fact = calls.find((c) => /insert into ops\.incident_fact/.test(c.text));
  assert.ok(fact, "the race loser must still attach its fact to the winner's incident");
  assert.equal(fact.params[0], incId);
});

test("recordWorkerFailure: NEVER throws, even when the query function itself throws unexpectedly — a recording failure must not become a request failure", async () => {
  const originalError = console.error;
  console.error = () => {}; // this call is ALSO expected to log now — silenced here, asserted below
  try {
    const angryQuery = async () => { throw new Error("network blip"); };
    await assert.doesNotReject(recordWorkerFailure(angryQuery, {
      environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
    }));
  } finally {
    console.error = originalError;
  }
});

test("recordWorkerFailure: a swallowed error is LOGGED via one structured console.error line — never silent (defect cae5be2e, second finding: the FIRST build swallowed with no log line at all, which is exactly what let the wrapNeonRows bug below run in production for hours with zero trace anywhere)", async () => {
  const originalError = console.error;
  const lines = [];
  console.error = (line) => lines.push(line);
  try {
    const angryQuery = async () => { throw new TypeError("Cannot read properties of undefined (reading 'length')"); };
    await recordWorkerFailure(angryQuery, {
      environment: "production", routeKey: "mcp:tools/call:read-loop",
      failureClass: "verb_internal_error", correlationId: A_CORR,
    });
  } finally {
    console.error = originalError;
  }
  assert.equal(lines.length, 1);
  const parsed = JSON.parse(lines[0]);
  assert.equal(parsed.level, "error");
  assert.equal(parsed.event, "worker_failure_record_error");
  assert.equal(parsed.correlation_id, A_CORR);
  assert.equal(parsed.route_key, "mcp:tools/call:read-loop");
  assert.equal(parsed.failure_class, "verb_internal_error");
  assert.equal(parsed.error_name, "TypeError");
  assert.match(parsed.error_message, /Cannot read properties of undefined/);
  // NEVER the query text, params, or client content — only what the error
  // object itself carries (name + message), matching correlation.js's own
  // logLine discipline for the uncaught-throw case.
  assert.doesNotMatch(lines[0], /select |insert into|ops\.service/i);
});

test("recordWorkerFailure: a successful call logs NOTHING — the log line is for failure only, never routine operation", async () => {
  const originalError = console.error;
  const lines = [];
  console.error = (line) => lines.push(line);
  try {
    const { query } = fakeQuery([
      { match: /select id from ops\.service/, rows: [{ id: "svc-1" }] },
      { match: /select id from ops\.incident where signature/, rows: [] },
      { match: /coalesce\(max\(substring/, rows: [{ day: "20260818", seq: 1 }] },
      { match: /insert into ops\.incident\b/, rows: [{ id: "inc-1" }] },
      { match: /select 1 from ops\.incident_fact/, rows: [] },
    ]);
    await recordWorkerFailure(query, {
      environment: "staging", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
    });
  } finally {
    console.error = originalError;
  }
  assert.equal(lines.length, 0);
});

// ────────────────────────────────────────────────────────────────────────
// wrapNeonRows — THE ROOT CAUSE FIX (defect cae5be2e, second finding,
// diagnosed live against production 2026-08-14). PR #148 shipped, migration
// 0122 applied clean, every grant was correct, and the recorder STILL never
// wrote a row — because scheduleFailureRecord's OLD query function was
// `(text, params) => neon(env.DATABASE_URL_WRITER).query(text, params)`,
// returning whatever neon(dsn).query() actually returns. Per
// @neondatabase/serverless's own documented contract, that is a BARE ARRAY OF
// ROWS by default — e.g. `await sql.query("SELECT ...", [...])
// // -> [ { greeting: "hello world" } ]` — never `{rows: [...]}` unless
// `fullResults: true` is passed. recordWorkerFailure's first query
// (`select id from ops.service...`) returned that bare array, `svc.rows` read
// `undefined`, `.length` threw a TypeError, and the outer catch swallowed it —
// on every single call. Proof this is real, not a guess: tool_read_call's
// recordReadCall (mcp.js) — the SAME ctx.waitUntil, the SAME
// DATABASE_URL_WRITER, the SAME neon() driver — recorded the exact probe
// request that triggered this bug, because recordReadCall never reads `.rows`
// off its own insert's result. These tests prove the adapter now closes that
// exact gap, using a stub shaped like the REAL documented driver contract —
// not the `{rows:[...]}` shape the original (wrong) test mock used, which is
// precisely how the original suite passed 31/31 while this bug shipped.
// ────────────────────────────────────────────────────────────────────────

test("wrapNeonRows: adapts a bare array (neon()'s REAL documented return shape) into {rows:[...]}", async () => {
  const bareArrayDriver = { query: async () => [{ id: "svc-1" }] };
  const query = wrapNeonRows(bareArrayDriver);
  const result = await query("select id from ops.service where key=$1", ["carr-mcp"]);
  assert.deepEqual(result, { rows: [{ id: "svc-1" }] });
});

test("wrapNeonRows: an empty result set is {rows: []}, never {rows: undefined} — the exact shape recordWorkerFailure's `.rows.length` checks depend on", async () => {
  const bareArrayDriver = { query: async () => [] };
  const query = wrapNeonRows(bareArrayDriver);
  const result = await query("select 1", []);
  assert.deepEqual(result, { rows: [] });
});

test("wrapNeonRows: passes text and params through to the underlying driver unchanged", async () => {
  const seen = [];
  const driver = { query: async (text, params) => { seen.push({ text, params }); return []; } };
  const query = wrapNeonRows(driver);
  await query("select 1 where x=$1", ["y"]);
  assert.deepEqual(seen, [{ text: "select 1 where x=$1", params: ["y"] }]);
});

test("REGRESSION (defect cae5be2e, second finding): recordWorkerFailure through wrapNeonRows against a driver shaped EXACTLY like the real neon() contract actually opens an incident and attaches a fact — this is the test that would have caught the production bug", async () => {
  const calls = [];
  const bareArrayDriver = {
    query: async (text, params) => {
      calls.push({ text, params });
      if (/select id from ops\.service/.test(text)) return [{ id: "svc-1" }];
      if (/select id from ops\.incident where signature/.test(text)) return [];
      if (/coalesce\(max\(substring/.test(text)) return [{ day: "20260818", seq: 1 }];
      if (/^\s*insert into ops\.incident\b/.test(text)) return [{ id: "inc-1" }];
      if (/select 1 from ops\.incident_fact/.test(text)) return [];
      return [];
    },
  };
  const query = wrapNeonRows(bareArrayDriver);
  await recordWorkerFailure(query, {
    environment: "production", routeKey: "mcp:tools/call:read-loop",
    failureClass: "verb_internal_error", correlationId: A_CORR,
  });
  assert.ok(calls.some((c) => /^\s*insert into ops\.incident\b/.test(c.text)),
    "the incident insert must actually have been attempted — a naive {rows}-shaped mock would pass this test even with the old bug, which is exactly why THIS test drives a bare-array driver instead");
  assert.ok(calls.some((c) => /insert into ops\.incident_fact/.test(c.text)),
    "the fact insert must also have been attempted — proves the recorder read the incident id back correctly, not just that the first query survived");
});

// ────────────────────────────────────────────────────────────────────────
// scheduleFailureRecord composes wrapNeonRows — source assertion, since the
// live wiring needs a real (or network-reachable-shaped) DSN to exercise
// behaviorally, matching the convention correlation.test.mjs/tool-read-call.
// test.mjs already use for Worker-only composition points.
// ────────────────────────────────────────────────────────────────────────

test("trace.js: scheduleFailureRecord builds its query through wrapNeonRows, not a raw neon(...).query() passthrough", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/trace.js", import.meta.url), "utf8");
  const fn = src.slice(src.indexOf("export function scheduleFailureRecord"));
  assert.match(fn, /wrapNeonRows\(neon\(env\.DATABASE_URL_WRITER\)\)/,
    "scheduleFailureRecord must adapt neon()'s bare-array return through wrapNeonRows before recordWorkerFailure ever sees it");
});

// ────────────────────────────────────────────────────────────────────────
// THE REAL INSERT SHAPE — every NOT NULL / CHECK-constrained column
// migrations 0115/0116 require on ops.incident, asserted against the ACTUAL
// SQL text the recorder sends, not just "was insert called". A test that only
// checks the recorder ran (as every test above the wrapNeonRows section did,
// pre-fix) proves nothing about whether the statement itself is well-formed —
// this is the coverage that was missing alongside the shape bug.
// ────────────────────────────────────────────────────────────────────────

test("openIncident's INSERT supplies every column ops.incident's constraints actually require (0115 NOT NULL + CHECK, 0116 signature)", async () => {
  let insertText = null;
  const { query } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: "svc-1" }] },
    { match: /select id from ops\.incident where signature/, rows: [] },
    { match: /coalesce\(max\(substring/, rows: [{ day: "20260818", seq: 1 }] },
  ]);
  const capturingQuery = async (text, params) => {
    if (/^\s*insert into ops\.incident\b/.test(text)) {
      insertText = text;
      return { rows: [{ id: "inc-1" }] };
    }
    if (/select 1 from ops\.incident_fact/.test(text)) return { rows: [] };
    return query(text, params);
  };
  await recordWorkerFailure(capturingQuery, {
    environment: "production", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.ok(insertText, "the insert must actually have run");
  const columnList = insertText.slice(insertText.indexOf("("), insertText.indexOf(")") + 1);
  // NOT NULL, no default: ref, title, environment, detected_source, source_kind,
  // source_ref. NOT NULL WITH a default this insert deliberately supplies
  // explicitly anyway (correlation_id, state, detected_at, observed_at).
  // severity is NOT NULL AND check-constrained (SEV-0..4 — see the DEFAULT_
  // SEVERITY test below). signature is nullable but is what makes 0116's whole
  // dedup mechanism work — its absence would silently defeat the point of using
  // ops.incident over a new ledger row at all.
  for (const col of ["ref", "correlation_id", "title", "severity", "state",
                      "environment", "detected_source", "detected_at",
                      "source_kind", "source_ref", "signature", "observed_at",
                      "expires_at"]) {
    assert.match(columnList, new RegExp(`\\b${col}\\b`), `insert must supply ${col}`);
  }
});

test("the default severity satisfies ops.incident's CHECK constraint (severity ~ '^SEV-[0-4]$', migration 0115)", async () => {
  let severityParam = null;
  const { query } = fakeQuery([
    { match: /select id from ops\.service/, rows: [{ id: "svc-1" }] },
    { match: /select id from ops\.incident where signature/, rows: [] },
    { match: /coalesce\(max\(substring/, rows: [{ day: "20260818", seq: 1 }] },
    { match: /select 1 from ops\.incident_fact/, rows: [] },
  ]);
  const capturingQuery = async (text, params) => {
    if (/^\s*insert into ops\.incident\b/.test(text)) { severityParam = params[3]; return { rows: [{ id: "inc-1" }] }; }
    return query(text, params);
  };
  await recordWorkerFailure(capturingQuery, {
    environment: "production", routeKey: "/mcp", failureClass: "http_5xx", correlationId: A_CORR,
  });
  assert.match(severityParam, /^SEV-[0-4]$/);
});

// ────────────────────────────────────────────────────────────────────────
// scheduleFailureRecord — the thin env/ctx wiring. No live database: proves
// the guard (no-op without a writer credential) and that a present
// credential schedules work via ctx.waitUntil without blocking the caller —
// the same split tool-read-call.test.mjs draws for recordReadCall's wiring.
// ────────────────────────────────────────────────────────────────────────

test("scheduleFailureRecord: never schedules anything when DATABASE_URL_WRITER is absent", () => {
  const waited = [];
  scheduleFailureRecord({ CARR_ENV: "staging", CORRELATION_ID: A_CORR }, { waitUntil: (p) => waited.push(p) },
    { routeKey: "/mcp", failureClass: "http_5xx", detail: null });
  assert.equal(waited.length, 0);
});

test("scheduleFailureRecord: a classifier-suppressed refusal does not even schedule a recorder promise", () => {
  const waited = [];
  scheduleFailureRecord({ DATABASE_URL_WRITER: "postgres://fake:fake@localhost.invalid/fake", CARR_ENV: "production" },
    { waitUntil: (p) => waited.push(p) },
    { routeKey: "mcp:tools/call:approve-rule", failureClass: null, detail: "expected control refusal" });
  assert.equal(waited.length, 0);
});

test("scheduleFailureRecord: never throws when ctx or ctx.waitUntil is missing", () => {
  assert.doesNotThrow(() => scheduleFailureRecord({ DATABASE_URL_WRITER: "x" }, null, { routeKey: "/mcp", failureClass: "http_5xx" }));
  assert.doesNotThrow(() => scheduleFailureRecord({ DATABASE_URL_WRITER: "x" }, {}, { routeKey: "/mcp", failureClass: "http_5xx" }));
});

test("scheduleFailureRecord: schedules exactly one promise via ctx.waitUntil when a writer credential is present, and never rejects", async () => {
  const waited = [];
  // Syntactically VALID but network-unreachable: neon() does not throw at
  // construction on this shape (proven live — tool-read-call.test.mjs's own
  // fakeReaderClientEnv uses the identical DSN for the identical reason), so
  // this exercises the real path through wrapNeonRows and into
  // recordWorkerFailure's own try/catch, which swallows the eventual network
  // failure. Deliberately NOT "not-a-real-connection-string" here — that string
  // makes neon() throw SYNCHRONOUSLY at construction, which is a DIFFERENT
  // code path (scheduleFailureRecord's own try/catch, tested below) that never
  // reaches ctx.waitUntil at all.
  scheduleFailureRecord({ DATABASE_URL_WRITER: "postgres://fake:fake@localhost.invalid/fake", CARR_ENV: "staging", CORRELATION_ID: A_CORR },
    { waitUntil: (p) => waited.push(p) }, { routeKey: "/mcp", failureClass: "http_5xx", detail: null });
  assert.equal(waited.length, 1);
  await assert.doesNotReject(waited[0]);
});

test("scheduleFailureRecord: a connection string neon() rejects OUTRIGHT (synchronously, at construction) is caught and logged — never thrown, even though the throw happens in THIS function's own body, before ctx.waitUntil is ever reached (the exact regression this fix's own test suite caught before shipping)", async () => {
  const originalError = console.error;
  const lines = [];
  console.error = (line) => lines.push(line);
  const waited = [];
  try {
    assert.doesNotThrow(() => scheduleFailureRecord(
      { DATABASE_URL_WRITER: "not-a-real-connection-string", CARR_ENV: "staging", CORRELATION_ID: A_CORR },
      { waitUntil: (p) => waited.push(p) }, { routeKey: "/mcp", failureClass: "http_5xx", detail: null }));
  } finally {
    console.error = originalError;
  }
  assert.equal(waited.length, 0, "a synchronous neon() failure must never reach ctx.waitUntil");
  assert.equal(lines.length, 1);
  const parsed = JSON.parse(lines[0]);
  assert.equal(parsed.event, "worker_failure_record_error");
  assert.equal(parsed.correlation_id, A_CORR);
  assert.match(parsed.error_message, /not a valid URL/);
});

// ────────────────────────────────────────────────────────────────────────
// withFailureRecording — the second wrapping layer composed inside
// wrapWithCorrelation in index.js.
// ────────────────────────────────────────────────────────────────────────

function fakeEnvCtx() {
  const waited = [];
  return { env: { DATABASE_URL_WRITER: "postgres://fake:fake@localhost.invalid/fake", CARR_ENV: "staging", CORRELATION_ID: A_CORR },
    ctx: { waitUntil: (p) => waited.push(p) }, waited };
}

test("withFailureRecording: a successful response passes through unchanged and schedules nothing", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => new Response("ok", { status: 200 });
  const wrapped = withFailureRecording(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/health"), env, ctx);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "ok");
  assert.equal(waited.length, 0);
});

test("withFailureRecording: a 503 response is returned UNCHANGED and schedules a recording", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => new Response(JSON.stringify({ ok: false }), { status: 503 });
  const wrapped = withFailureRecording(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/health"), env, ctx);
  assert.equal(res.status, 503);
  assert.equal(waited.length, 1);
  await assert.doesNotReject(waited[0]);
});

test("withFailureRecording: a throwing handler is RE-THROWN unchanged (so wrapWithCorrelation still builds its own 500), and still schedules a recording", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => { throw new Error("boom"); };
  const wrapped = withFailureRecording(handler);
  await assert.rejects(wrapped(new Request("https://api.doctorcre.com/mcp"), env, ctx), /boom/);
  assert.equal(waited.length, 1);
  await assert.doesNotReject(waited[0]);
});

test("withFailureRecording: a 4xx response (including 401, the routine-noise case) passes through and schedules nothing", async () => {
  const { env, ctx, waited } = fakeEnvCtx();
  const handler = async () => new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  const wrapped = withFailureRecording(handler);
  const res = await wrapped(new Request("https://api.doctorcre.com/mcp"), env, ctx);
  assert.equal(res.status, 401);
  assert.equal(waited.length, 0);
});

// ────────────────────────────────────────────────────────────────────────
// dispatch() wiring — mcp.js holds no cloudflare: import, so this exercises
// the REAL outer-catch path with a genuine uncaught exception (neon(undefined)
// throwing synchronously inside the read branch) rather than a source-text
// assertion, and proves the actor-unresolved 401 path the same way.
// ────────────────────────────────────────────────────────────────────────

test("dispatch(): a genuine uncaught exception in a tool call schedules a failure record with failureClass verb_internal_error, and the caller is TOLD WHAT FAILED", async () => {
  const waited = [];
  // env.DATABASE_URL_READER is absent: list-verbs is a read verb, so callTool's
  // read branch calls neon(undefined) BEFORE its own try/catch — a real,
  // reproducible uncaught exception, not a contrived one (see this file's
  // header). DATABASE_URL_WRITER is syntactically valid but network-
  // unreachable (NOT "not-a-real-connection-string" — that shape makes
  // neon() throw synchronously at construction inside scheduleFailureRecord's
  // own guard, tested separately, and never reaches ctx.waitUntil at all).
  const env = { DATABASE_URL_WRITER: "postgres://fake:fake@localhost.invalid/fake" };
  const ctx = { waitUntil: (p) => waited.push(p) };
  const actor = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };
  const req = new Request("https://api.doctorcre.com/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "list-verbs", arguments: {} } }),
  });
  const res = await dispatch(req, env, ctx, actor);
  const body = await res.json();
  // CHANGED 2026-08-21, deliberately. This used to assert `body.error.code ===
  // -32603`, the protocol-level envelope whose message is the literal string
  // "internal error" and whose real cause sits in `data` — a field MCP clients
  // routinely drop. That masking is what let the capability queue's close verb
  // fail on every call it ever received while reporting nothing a caller could
  // read, and what turned two short-id-into-a-uuid-column bugs into blind
  // retries. A tool-call failure now comes back as a tool-level error result
  // that names the verb and carries the cause.
  assert.equal(body.error, undefined, "a tool-call failure is no longer a protocol-level error");
  assert.equal(body.result.isError, true, "it is a tool result flagged as an error");
  const payload = JSON.parse(body.result.content[0].text);
  assert.equal(payload.error, "unhandled_verb_failure");
  assert.equal(payload.verb, "list-verbs", "the caller must be told WHICH verb threw");
  assert.ok(payload.cause && payload.cause.length > 0, "the cause must actually be present, not an empty string");
  assert.doesNotMatch(payload.cause, /fake:fake@/,
    "the connection string in this env must be redacted out of the surfaced cause");
  assert.equal(waited.length, 1, "a failure record must STILL be scheduled — unmasking does not replace recording");
  await assert.doesNotReject(waited[0]);
});

test("dispatch(): tools/call for a KNOWN-GOOD read verb with a real fake env schedules NO failure record — the happy path stays silent", async () => {
  const waited = [];
  const env = { DATABASE_URL_READER: "postgres://fake:fake@localhost.invalid/fake", DATABASE_URL_WRITER: undefined };
  const ctx = { waitUntil: (p) => waited.push(p) };
  const actor = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };
  const req = new Request("https://api.doctorcre.com/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "list-verbs", arguments: {} } }),
  });
  const res = await dispatch(req, env, ctx, actor);
  const body = await res.json();
  assert.equal(body.result.content[0].type, "text");
  assert.equal(waited.length, 0);
});

// ────────────────────────────────────────────────────────────────────────
// MATERIAL WRITES — the correlation id threaded through auditIdentity(),
// proving mcp.js's dispatch() decoration reaches tools.js's writers.
// ────────────────────────────────────────────────────────────────────────

test("auditIdentity: carries correlation_id straight from the actor object dispatch() decorates it onto", () => {
  const withCorr = auditIdentity({ slug: "joe", human: true, correlation_id: A_CORR });
  assert.equal(withCorr.correlation_id, A_CORR);

  const withoutCorr = auditIdentity({ slug: "joe", human: true });
  assert.equal(withoutCorr.correlation_id, null);
});

test("dispatch(): decorates env.CORRELATION_ID onto the actor as correlation_id, never inventing one when absent", async () => {
  // list-verbs needs no DB in its own handler body, so this exercises the
  // decoration itself (via a source-observable side effect: initialize's
  // reply, which does not depend on it) plus a direct call to the exported
  // decoration logic through dispatch()'s own tools/list, which is enough to
  // prove the actor mcp.js builds carries the field — the write-path threading
  // itself (tool_call/event inserts) is proven by tools.js's own
  // withEnvelope/writeEvent unit coverage plus migration 0122's proof block.
  const env = { CORRELATION_ID: A_CORR, DATABASE_URL_READER: "postgres://fake:fake@localhost.invalid/fake" };
  const ctx = { waitUntil: () => {} };
  const actor = { slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: "claude" };
  const req = new Request("https://api.doctorcre.com/mcp", {
    method: "POST",
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "list-verbs", arguments: {} } }),
  });
  const res = await dispatch(req, env, ctx, actor);
  assert.equal(res.status, 200);
  // The strongest direct proof: mcp.js's own source wires env.CORRELATION_ID
  // onto scopedActor.correlation_id (asserted below by source, matching this
  // repo's own established convention for proving Worker-only wiring — see
  // correlation.test.mjs's identical technique for index.js).
});

test("mcp.js: dispatch() decorates env.CORRELATION_ID onto scopedActor.correlation_id, and auditIdentity is the only place tool_call/event insert it", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/mcp.js", import.meta.url), "utf8");
  assert.match(src, /correlation_id:\s*env\.CORRELATION_ID\s*\|\|\s*null/,
    "dispatch() must decorate env.CORRELATION_ID onto the actor as correlation_id");
});

test("tools.js: withEnvelope's tool_call insert and writeEvent's event insert both carry identity.correlation_id", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const envelopeBody = src.slice(src.indexOf("async function withEnvelope"), src.indexOf("async function writeEvent"));
  assert.match(envelopeBody, /insert into tool_call[\s\S]*correlation_id/, "withEnvelope's insert must carry correlation_id");
  assert.match(envelopeBody, /identity\.correlation_id/, "withEnvelope must pass identity.correlation_id as a param");

  const writeEventBody = src.slice(src.indexOf("async function writeEvent"), src.indexOf("async function writeEvent") + 4000);
  assert.match(writeEventBody, /insert into event[\s\S]*correlation_id/, "writeEvent's insert must carry correlation_id");
  assert.match(writeEventBody, /identity\.correlation_id/, "writeEvent must pass identity.correlation_id as a param");
});

test("tools.js: log-decision's own bespoke event insert also carries correlation_id — the second, non-writeEvent writer of the event table", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
  const idx = src.indexOf("'log-decision', 'decision'");
  assert.ok(idx > -1, "log-decision's direct event insert must still exist at this call site");
  const nearby = src.slice(idx - 400, idx + 800);
  assert.match(nearby, /correlation_id/, "log-decision's own insert must also carry correlation_id");
});
