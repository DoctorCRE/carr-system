// V5-S01 live door — the dispatch-seam verdict and the read projection.
//
// Organised by checkable_done clause:
//   1. the role, scope, continuity and platform matrices match the settled
//      decisions (pinned tables below, computed live by the door);
//   2. the offline-write, PHI, listing-side and unauthorized-admin negatives
//      refuse, each beside the positive that must pass;
//   3. losing the Mac Studio or Hermes reports degraded/unavailable and moves
//      no authority.
// Plus the door's own contract: shadow never blocks and never throws, enforce
// refuses by name, and no argument value ever reaches a verdict or a log line.

import test from "node:test";
import assert from "node:assert/strict";

import { ToolError } from "../src/tool-error.js";
import {
  V5_ACTIONS,
  V5_ACTION_KEYS,
  V5_LOCAL_CAPABILITIES,
  V5_OPTIONAL_LOCAL_NODES,
  V5_SETTLED_DECISIONS,
  v5BoundaryPolicyDigest,
} from "../src/global-boundaries.v5.js";
import {
  V5_BOUNDARY_DOOR_MODE,
  V5_BOUNDARY_DOOR_MODES,
  V5_CLOUD_DOOR_CONTEXT,
  V5_DOOR_SYSTEM_AUTHORITY_VERBS,
  V5_DOOR_MAX_SCANNED_NODES,
  V5BoundaryDoorRefusal,
  doorAuthoritySubject,
  doorObservationSnapshot,
  evaluateDispatchBoundaries,
  globalBoundariesDoorTools,
  normalizeFieldName,
  passBoundaryDoor,
  resetDoorObservationForTest,
  v5BoundaryDoorProjection,
  v5ContinuityMatrix,
  v5PlatformMatrix,
  v5RoleMatrix,
  v5ScopeMatrix,
} from "../src/global-boundaries-door.v5.js";

// ---------------------------------------------------------------- fixtures

const NOW = "2026-09-25T12:00:00Z";
const human = slug => Object.freeze({ slug, human: true, via: "oauth-google",
  sponsoring_human_slug: null, human_slug: null, sponsor_required: false });
const JOE = human("joe");
const DELL = human("dell");
const nativeAgent = (slug, sponsor) => Object.freeze({ slug, human: false, via: "oauth-google",
  client_id: "c1", sponsoring_human_slug: sponsor, human_slug: sponsor, sponsor_required: true,
  native_agent_verified: true });
const JOE_CLAUDE = nativeAgent("claude", "joe");
const DELL_CLAUDE = nativeAgent("claude", "dell");
const JOE_LOCAL = nativeAgent("joe-local", "joe");
// Sponsored, but the native-agent binding is not verified: no partner authority.
const UNVERIFIED_CLAUDE = Object.freeze({ ...JOE_CLAUDE, native_agent_verified: false });
const HERMES = Object.freeze({ slug: "hermes-pilot", human: false, hermes: true, via: "hermes-token",
  sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: false });
const PROBE = Object.freeze({ slug: "smoke-probe", human: false, probe: true, via: "probe-token" });
const REVIEWER = Object.freeze({ slug: "codex-reviewer", human: false, review: true, via: "review-token" });

const cloud = { ...V5_CLOUD_DOOR_CONTEXT, now: NOW };
const door = (over = {}) => evaluateDispatchBoundaries({
  verb: "log-activity", write: true, actor: JOE, args: {}, context: cloud, ...over,
});
const only = (verdict, boundary) => verdict.checks.filter(c => c.boundary === boundary);
const reasons = verdict => verdict.refusals.map(r => r.reason_id);

// ------------------------------------------------------------- mode flag

test("the door flag is a constant that defaults to shadow; enforce is the only other mode", () => {
  assert.equal(V5_BOUNDARY_DOOR_MODE, "shadow");
  assert.deepEqual([...V5_BOUNDARY_DOOR_MODES], ["shadow", "enforce"]);
  assert.throws(() => door({ mode: "canary" }), e => e.code === "unknown_door_mode");
  assert.equal(door().mode, "shadow");
  assert.equal(V5_CLOUD_DOOR_CONTEXT.connectivity, "online");
});

test("an ordinary partner write at the cloud door passes every boundary", () => {
  const verdict = door({ verb: "update-deal", args: { deal: "C-1", fields: { next_step: "call" } } });
  assert.equal(verdict.boundary_refused, false);
  assert.deepEqual(verdict.refusals, []);
  assert.equal(verdict.enforced, false);
  assert.equal(verdict.policy_digest, v5BoundaryPolicyDigest());
  assert.equal(verdict.permanent_privilege_granted, false);
  assert.equal(verdict.effects.creates_effect, false);
});

// ------------------------------------------------------ Q007 offline write

test("Q007: a mutation under degraded or offline connectivity refuses; online passes", () => {
  for (const connectivity of ["degraded", "offline"]) {
    const verdict = door({ context: { ...cloud, connectivity } });
    assert.equal(verdict.boundary_refused, true, connectivity);
    assert.deepEqual(reasons(verdict), ["offline_mutation_refused"]);
    assert.equal(only(verdict, "read_continuity")[0].availability,
      connectivity === "offline" ? "unavailable" : "degraded");
  }
  assert.equal(door().boundary_refused, false);
  assert.equal(only(door(), "read_continuity")[0].reason_id, "online_mutation_permitted");
});

test("Q007: an offline read reports unavailable rather than an empty success", () => {
  const verdict = door({ write: false, context: { ...cloud, connectivity: "offline" } });
  assert.deepEqual(reasons(verdict), ["read_unavailable_no_cache"]);
  assert.equal(only(verdict, "read_continuity")[0].availability, "unavailable");
  assert.equal(door({ write: false }).boundary_refused, false);
});

test("Q007: connectivity is the server's door context, never a caller argument", () => {
  // A caller naming connectivity in its arguments changes nothing.
  const verdict = door({ args: { connectivity: "offline", note: "offline" } });
  assert.equal(verdict.boundary_refused, false);
  assert.throws(() => door({ context: { ...cloud, connectivity: "flaky" } }),
    e => e.code === "unknown_connectivity_state");
  assert.throws(() => door({ context: { ...cloud, actor: "joe" } }), e => e.code === "unknown_field");
});

// ------------------------------------------------------------- Q033 PHI

test("Q033: a patient-level field name at any depth refuses and names the field, not the value", () => {
  const cases = [
    [{ patient_name: "Jane Roe" }, "patient_identifier", "patient_name"],
    [{ details: { rows: [{ mrn: "12345" }] } }, "patient_identifier", "details.rows[0].mrn"],
    [{ payload: { patientAddress: "1 Main" } }, "raw_patient_location", "payload.patientAddress"],
    [{ fields: { "patient-latitude": 30.1 } }, "raw_patient_location", "fields.patient-latitude"],
    [{ facts: { diagnosis_code: "E11" } }, "phi", "facts.diagnosis_code"],
    [{ facts: { medical_records: [] } }, "patient_record", "facts.medical_records"],
    [{ facts: { encounter_date: "2026-01-01" } }, "patient_visit_detail", "facts.encounter_date"],
  ];
  for (const [args, dataClass, field] of cases) {
    const verdict = door({ args });
    const privacy = only(verdict, "privacy");
    assert.equal(privacy.length, 1, JSON.stringify(args));
    assert.equal(privacy[0].decision, "refuse");
    assert.equal(privacy[0].reason_id, "phi_or_raw_patient_location_refused");
    assert.deepEqual([...privacy[0].data_classes], [dataClass]);
    assert.deepEqual([...privacy[0].fields], [field]);
    assert.ok(!JSON.stringify(verdict).includes("Jane Roe"));
  }
});

test("Q033: an aggregate patient input is held for its independent privacy route, not passed", () => {
  const verdict = door({ args: { layers: { patient_location_heatmap: {} } } });
  assert.deepEqual(reasons(verdict), ["aggregate_input_requires_independent_privacy_route"]);
});

test("Q033: values are never pattern-matched and bare 'diagnosis' is not patient data", () => {
  for (const args of [
    { summary: "patient volume is about 40 per day; patient_name unknown" },
    { finding: { diagnosis: "the cache key omitted the tenant" } },
    { practice: { patients_per_day: 40 } },
    { notes: ["mrn", "patient_address"] },
  ]) {
    const verdict = door({ args });
    assert.equal(only(verdict, "privacy").length, 0, JSON.stringify(args));
    assert.equal(verdict.boundary_refused, false);
  }
});

test("Q033: an argument too large to scan refuses as incomplete rather than passing unread", () => {
  const big = { rows: Array.from({ length: V5_DOOR_MAX_SCANNED_NODES + 5 }, () => 1) };
  const verdict = door({ args: big });
  assert.deepEqual(reasons(verdict), ["argument_scan_incomplete"]);
  let deep = {};
  const root = deep;
  for (let i = 0; i < 40; i += 1) { deep.next = {}; deep = deep.next; }
  assert.deepEqual(reasons(door({ args: root })), ["argument_scan_incomplete"]);
});

test("field-name normalization folds camelCase, hyphens and spaces", () => {
  assert.equal(normalizeFieldName("patientAddress"), "patient_address");
  assert.equal(normalizeFieldName("Patient-Name"), "patient_name");
  assert.equal(normalizeFieldName("patient name"), "patient_name");
  assert.equal(normalizeFieldName("MRN"), "mrn");
});

// ------------------------------------------------ Q073/Q092 listing side

test("Q073/Q092: asserting landlord or seller representation refuses; tenant and buyer pass", () => {
  for (const side of ["landlord", "seller"]) {
    const verdict = door({ verb: "new-deal", args: { name: "X", representation_side: side } });
    assert.deepEqual(reasons(verdict), ["listing_side_exposure_refused"], side);
  }
  for (const side of ["tenant", "buyer"]) {
    const verdict = door({ verb: "new-deal", args: { name: "X", representation_side: side } });
    assert.equal(verdict.boundary_refused, false, side);
    assert.equal(only(verdict, "representation_scope")[0].reason_id, "tenant_buyer_representation_in_scope");
  }
  const nested = door({ args: { draft: { representedSide: "seller" } } });
  assert.deepEqual(reasons(nested), ["listing_side_exposure_refused"]);
});

test("Q073/Q092: an unregistered side is a contract violation, refused rather than guessed", () => {
  for (const side of ["owner", "", 7, null]) {
    const verdict = door({ args: { representation_side: side } });
    assert.deepEqual(reasons(verdict), ["boundary_contract_violation:unknown_representation_side"], String(side));
  }
});

test("Q073/Q092: any truthy listing-side activation refuses; false or absent does not", () => {
  for (const value of [true, "yes", 1, { on: true }]) {
    const verdict = door({ args: { activate_listing_side: value } });
    assert.deepEqual(reasons(verdict), ["listing_side_activation_refused"], JSON.stringify(value));
  }
  assert.deepEqual(reasons(door({ args: { settings: { enableListingSide: true } } })),
    ["listing_side_activation_refused"]);
  for (const value of [false, null]) {
    assert.equal(door({ args: { activate_listing_side: value } }).boundary_refused, false);
  }
});

test("Q092: the brokerage side stays a legal structural value on record-counter and add-premises", () => {
  for (const side of ["tenant", "landlord", "buyer", "seller"]) {
    const verdict = door({ verb: "record-counter", args: { deal: "C-1", side } });
    assert.equal(verdict.boundary_refused, false, side);
    const scope = only(verdict, "representation_scope");
    assert.equal(scope.length, 1);
    assert.equal(scope[0].structural, true);
  }
  assert.equal(only(door({ verb: "record-counter", args: { side: "landlord" } }), "representation_scope")[0].reason_id,
    "brokerage_side_structural_value_only");
  const premises = door({ verb: "add-premises", args: { deal: "C-1",
    ownership: [{ kind: "listing_agent", party: "P-1", also_listing_side: true }, { kind: "owner", party: "P-2" }] } });
  assert.equal(premises.boundary_refused, false);
  const scope = only(premises, "representation_scope");
  assert.equal(scope.length, 1);
  assert.equal(scope[0].field, "ownership[0].also_listing_side");
  assert.equal(scope[0].reason_id, "brokerage_side_structural_value_only");
  // The same `side` field on any other verb is not structural and is not read.
  assert.equal(only(door({ verb: "log-activity", args: { side: "landlord" } }), "representation_scope").length, 0);
});

// --------------------------------------- Q003/Q020/Q141 unauthorized admin

test("Q141: the door's admin verbs map only to retained system-authority actions", () => {
  assert.deepEqual(Object.keys(V5_DOOR_SYSTEM_AUTHORITY_VERBS).sort(), [
    "accept-workflow", "approve-rule", "disable-legacy-schedule", "retire-rule",
    "set-current-model-role-revision",
  ]);
  for (const entry of Object.values(V5_DOOR_SYSTEM_AUTHORITY_VERBS)) {
    assert.equal(V5_ACTIONS[entry.action].authority_class, "system_authority");
    assert.ok(entry.existing_enforcement.length > 0);
  }
  // amend-rule is deliberately absent: a PROPOSED rule's amendment needs no authority.
  assert.equal(Object.hasOwn(V5_DOOR_SYSTEM_AUTHORITY_VERBS, "amend-rule"), false);
});

test("Q141: an admin verb refuses Dell, Dell's agents and every non-partner seat; Joe and his agents pass", () => {
  for (const verb of ["approve-rule", "retire-rule", "disable-legacy-schedule", "set-current-model-role-revision"]) {
    for (const actor of [JOE, JOE_CLAUDE, JOE_LOCAL]) {
      const verdict = door({ verb, actor });
      assert.equal(verdict.boundary_refused, false, `${verb} ${actor.slug}`);
      assert.equal(only(verdict, "actor_authority")[0].reason_id, "system_authority_retained");
    }
    for (const actor of [DELL, DELL_CLAUDE]) {
      assert.deepEqual(reasons(door({ verb, actor })), ["system_authority_reserved_to_joe"], `${verb} ${actor.slug}`);
    }
    for (const actor of [HERMES, PROBE, REVIEWER, UNVERIFIED_CLAUDE]) {
      assert.deepEqual(reasons(door({ verb, actor })), ["actor_not_verified_partner"], `${verb} ${actor.slug}`);
    }
  }
});

test("Q141: accept-workflow is admin only in canary mode", () => {
  assert.deepEqual(reasons(door({ verb: "accept-workflow", actor: DELL, args: { mode: "canary" } })),
    ["system_authority_reserved_to_joe"]);
  const shadowRun = door({ verb: "accept-workflow", actor: DELL, args: { mode: "shadow" } });
  assert.equal(only(shadowRun, "actor_authority").length, 0);
  assert.equal(shadowRun.boundary_refused, false);
  assert.equal(door({ verb: "accept-workflow", actor: JOE, args: { mode: "canary" } }).boundary_refused, false);
});

test("Q141: ordinary verbs are not authority-evaluated at the door; Dell's business work passes", () => {
  for (const verb of ["update-deal", "log-activity", "record-counter", "amend-rule", "teach"]) {
    const verdict = door({ verb, actor: DELL, args: {} });
    assert.equal(only(verdict, "actor_authority").length, 0, verb);
    assert.equal(verdict.boundary_refused, false, verb);
  }
});

test("the authority subject is server-derived: partner, sponsor, or nobody", () => {
  assert.deepEqual({ ...doorAuthoritySubject(JOE) },
    { subject: "joe", source: "verified_partner", authorization_class: "verified_partner" });
  assert.deepEqual({ ...doorAuthoritySubject(DELL_CLAUDE) },
    { subject: "dell", source: "server_derived_sponsor", authorization_class: "sponsored_agent" });
  for (const actor of [HERMES, PROBE, REVIEWER, UNVERIFIED_CLAUDE, undefined, null]) {
    assert.equal(doorAuthoritySubject(actor).subject, null);
  }
  // A caller cannot name the subject: arguments are never read for it.
  const verdict = door({ verb: "approve-rule", actor: DELL, args: { sponsor: "joe", actor: "joe" } });
  assert.deepEqual(reasons(verdict), ["system_authority_reserved_to_joe"]);
});

test("an admin verb with no `now` is a contract violation at the door, not a pass", () => {
  const verdict = door({ verb: "approve-rule", actor: JOE, context: { ...V5_CLOUD_DOOR_CONTEXT } });
  assert.deepEqual(reasons(verdict), ["boundary_contract_violation:missing_field"]);
});

// --------------------------------------------- Q020/Q030 local platform

test("Q020/Q030: losing the Mac Studio or Hermes degrades capability and never moves authority", () => {
  const platform = v5PlatformMatrix();
  for (const [key, unchanged] of Object.entries(platform.role_matrix_unchanged_by_node_state)) {
    assert.equal(unchanged, true, key);
  }
  assert.equal(Object.keys(platform.role_matrix_unchanged_by_node_state).length,
    V5_OPTIONAL_LOCAL_NODES.length * 3);
  for (const row of platform.rows) {
    assert.equal(row.canonical_authority, "carr_cloud_record_layer");
    assert.equal(row.authority_unchanged, true);
    if (row.node_state === "available") {
      assert.equal(row.availability, "available");
      continue;
    }
    // Never "available" once the node is gone, and never an unstated outcome.
    assert.ok(["degraded", "unavailable"].includes(row.availability), JSON.stringify(row));
    const fallback = V5_LOCAL_CAPABILITIES[row.capability].fallback;
    const expected = {
      cloud_fallback: ["allow", "approved_cloud_fallback", "cloud_fallback"],
      visible_queue: ["deferred", "visible_queue_pending_local_node", "visible_queue"],
      none: ["refuse", row.node_state === "unavailable"
        ? "local_capability_unavailable" : "local_capability_degraded_no_fallback", "none"],
    }[fallback];
    assert.deepEqual([row.decision, row.reason_id, row.execution], expected, JSON.stringify(row));
  }
});

test("Q030: no dispatch verdict consults a local node, and Hermes carries no authority", () => {
  const verdict = door({ verb: "approve-rule", actor: JOE });
  assert.equal(verdict.local_platform_consulted, false);
  assert.equal(doorAuthoritySubject(HERMES).subject, null);
  assert.deepEqual(reasons(door({ verb: "approve-rule", actor: HERMES })), ["actor_not_verified_partner"]);
});

// ------------------------------------------ settled-decision matrices

test("role matrix matches Q003/Q020/Q141: Joe retains, Dell's classes are deferred, business is shared", () => {
  const matrix = v5RoleMatrix();
  assert.deepEqual(matrix.map(r => r.action), [...V5_ACTION_KEYS]);
  for (const row of matrix) {
    const expected = {
      system_authority: [["allow", "system_authority_retained"], ["refuse", "system_authority_reserved_to_joe"]],
      developer: [["allow", "system_authority_retained"], ["refuse", "deferred_authority_requires_grant"]],
      release_admin: [["allow", "system_authority_retained"], ["refuse", "deferred_authority_requires_grant"]],
      ordinary_business: [["allow", "ordinary_business_within_controls"], ["allow", "ordinary_business_within_controls"]],
    }[row.authority_class];
    assert.deepEqual([[row.joe.decision, row.joe.reason_id], [row.dell.decision, row.dell.reason_id]], expected, row.action);
  }
});

test("scope matrix matches Q073/Q092", () => {
  const table = Object.fromEntries(v5ScopeMatrix().map(r => [`${r.representation_side}:${r.intent}`, r]));
  const expect = {
    "tenant:expose": ["allow", "tenant_buyer_representation_in_scope", true],
    "buyer:expose": ["allow", "tenant_buyer_representation_in_scope", true],
    "landlord:expose": ["refuse", "listing_side_exposure_refused", false],
    "seller:expose": ["refuse", "listing_side_exposure_refused", false],
    "landlord:structural_record": ["allow", "brokerage_side_structural_value_only", false],
    "seller:structural_record": ["allow", "brokerage_side_structural_value_only", false],
    "tenant:structural_record": ["allow", "tenant_buyer_representation_in_scope", true],
    "buyer:structural_record": ["allow", "tenant_buyer_representation_in_scope", true],
    "tenant:activate": ["refuse", "scope_activation_requires_amendment", false],
    "buyer:activate": ["refuse", "scope_activation_requires_amendment", false],
    "landlord:activate": ["refuse", "listing_side_activation_refused", false],
    "seller:activate": ["refuse", "listing_side_activation_refused", false],
  };
  assert.deepEqual(Object.keys(table).sort(), Object.keys(expect).sort());
  for (const [key, [decision, reason_id, exposed]] of Object.entries(expect)) {
    assert.deepEqual([table[key].decision, table[key].reason_id, table[key].exposed], [decision, reason_id, exposed], key);
  }
});

test("continuity matrix matches Q007: online-first, no offline mutation", () => {
  const table = Object.fromEntries(v5ContinuityMatrix().map(r => [`${r.operation_kind}:${r.connectivity}`, r]));
  assert.deepEqual([table["read:online"].decision, table["read:online"].availability], ["allow", "available"]);
  assert.deepEqual([table["mutation:online"].decision, table["mutation:online"].availability], ["allow", "available"]);
  for (const c of ["degraded", "offline"]) {
    assert.equal(table[`mutation:${c}`].reason_id, "offline_mutation_refused");
    assert.equal(table[`read:${c}`].decision, "refuse");
    assert.notEqual(table[`read:${c}`].availability, "available");
  }
});

// ------------------------------------------------- the door's contract

test("shadow never blocks: a would-refuse verdict is recorded and returned", () => {
  resetDoorObservationForTest();
  const lines = [];
  const verdict = passBoundaryDoor({ verb: "approve-rule", write: true, actor: DELL,
    args: { rule_id: "r1", reason: "secret-reason-text" }, now: NOW, log: l => lines.push(l) });
  assert.equal(verdict.boundary_refused, true);
  assert.equal(verdict.enforced, false);
  assert.equal(lines.length, 1);
  const line = JSON.parse(lines[0]);
  assert.equal(line.event, "v5_boundary_door");
  assert.deepEqual(line.refusals, [{ boundary: "actor_authority", reason_id: "system_authority_reserved_to_joe", count: 1 }]);
  assert.ok(!lines[0].includes("secret-reason-text"), "argument values never reach the log");
  const snapshot = doorObservationSnapshot();
  assert.equal(snapshot.scope, "this_worker_isolate_since_start");
  assert.equal(snapshot.evaluated, 1);
  assert.equal(snapshot.boundary_refused, 1);
  assert.equal(snapshot.enforced, 0);
  assert.equal(snapshot.by_reason["actor_authority:system_authority_reserved_to_joe"], 1);
  // A passing verdict is counted but not logged.
  passBoundaryDoor({ verb: "log-activity", write: true, actor: DELL, args: {}, now: NOW, log: l => lines.push(l) });
  assert.equal(lines.length, 1);
  assert.equal(doorObservationSnapshot().evaluated, 2);
});

test("shadow never throws: an unreadable door input is a door_error, and dispatch proceeds", () => {
  resetDoorObservationForTest();
  const lines = [];
  const result = passBoundaryDoor({ verb: "log-activity", write: "yes", actor: JOE, args: {}, now: NOW,
    log: l => lines.push(l) });
  assert.equal(result, null);
  assert.equal(doorObservationSnapshot().door_errors, 1);
  assert.equal(JSON.parse(lines[0]).event, "v5_boundary_door_error");
  // Even a throwing logger cannot fail a shadow dispatch.
  assert.doesNotThrow(() => passBoundaryDoor({ verb: "approve-rule", write: true, actor: DELL, args: {},
    now: NOW, log: () => { throw new Error("log sink down"); } }));
});

test("enforce refuses by name before any handler, and fails closed on an unreadable input", () => {
  resetDoorObservationForTest();
  assert.throws(() => passBoundaryDoor({ verb: "approve-rule", write: true, actor: DELL, args: {},
    now: NOW, mode: "enforce", log: () => {} }), error => {
    assert.ok(error instanceof V5BoundaryDoorRefusal);
    assert.equal(error.payload.error, "v5_boundary_refused");
    assert.deepEqual(error.payload.refusals, [{ boundary: "actor_authority", reason_id: "system_authority_reserved_to_joe", count: 1 }]);
    return true;
  });
  assert.equal(doorObservationSnapshot().enforced, 1);
  assert.throws(() => passBoundaryDoor({ verb: "log-activity", write: "yes", actor: JOE, args: {},
    now: NOW, mode: "enforce", log: () => {} }), e => e.code === "invalid_shape");
  const ok = passBoundaryDoor({ verb: "approve-rule", write: true, actor: JOE, args: {}, now: NOW,
    mode: "enforce", log: () => {} });
  assert.equal(ok.boundary_refused, false);
});

// ------------------------------------------------- the read projection

test("the read projection carries the policy, the eight decisions, every matrix and the door", () => {
  const projection = v5BoundaryDoorProjection();
  assert.equal(projection.projection.policy_digest, v5BoundaryPolicyDigest());
  assert.deepEqual(projection.decisions.map(d => d.decision_id), Object.keys(V5_SETTLED_DECISIONS).sort());
  for (const d of projection.decisions) {
    assert.equal(d.source_evidence_digest, V5_SETTLED_DECISIONS[d.decision_id].source_evidence_digest);
  }
  assert.equal(projection.door.mode, "shadow");
  assert.equal(projection.accepts_anything, false);
  assert.deepEqual(projection.matrices.role, v5RoleMatrix());
  assert.equal(projection.door.system_authority_verbs.length, 5);
});

test("read-global-boundaries is a read verb that refuses a stale expected digest by name", async () => {
  const tools = globalBoundariesDoorTools({ ToolError });
  const tool = tools["read-global-boundaries"];
  assert.notEqual(tool.write, true);
  assert.notEqual(tool.humanOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  const noDb = { query: () => { throw new Error("the read verb must not touch the database"); } };
  const result = await tool.handler(noDb, JOE, {});
  assert.equal(result.projection.policy_digest, v5BoundaryPolicyDigest());
  const same = await tool.handler(noDb, JOE, { expected_policy_digest: v5BoundaryPolicyDigest() });
  assert.equal(same.projection.policy_digest, v5BoundaryPolicyDigest());
  await assert.rejects(tool.handler(noDb, JOE, { expected_policy_digest: `sha256:${"0".repeat(64)}` }),
    e => e instanceof ToolError && e.payload.error === "stale_expected_digest");
  await assert.rejects(tool.handler(noDb, JOE, { expected_policy_digest: "nope" }),
    e => e instanceof ToolError && e.payload.error === "invalid_expected_digest");
});

// ----------------------------------- bounded output (#1268 review, round 1)
//
// The reviewer's reproduction: 9,000 rows of {representation_side:"landlord"}
// produced one 800 KB log line, because every matching field appended its own
// refusal and the whole list was logged, buffered and served back. Everything
// the door records must now be bounded whatever the caller sends.

const manyRows = (n, row) => ({ rows: Array.from({ length: n }, (_, i) => row(i)) });

test("a huge matching input yields merged refusals and a bounded verdict and log line", () => {
  resetDoorObservationForTest();
  const cases = [
    manyRows(9000, () => ({ representation_side: "landlord" })),
    manyRows(6000, () => ({ patient_name: "x", activate_listing_side: true })),
    manyRows(3000, i => ({ [`${"k".repeat(5000)}${i}`]: { patient_address: "1 Main" } })),
  ];
  for (const args of cases) {
    const lines = [];
    const verdict = passBoundaryDoor({ verb: "log-activity", write: true, actor: DELL, args, now: NOW,
      log: l => lines.push(l) });
    assert.equal(verdict.boundary_refused, true);
    const keys = verdict.refusals.map(r => `${r.boundary}:${r.reason_id}`);
    assert.equal(new Set(keys).size, keys.length, "refusals are merged by boundary:reason_id");
    assert.ok(verdict.refusals.every(r => Number.isInteger(r.count) && r.count >= 1));
    for (const check of verdict.checks) {
      if (!check.fields) continue;
      assert.ok(check.fields.length <= 5);
      assert.ok(check.fields.every(f => f.length <= 160));
    }
    assert.ok(JSON.stringify(verdict).length < 8000, `verdict is ${JSON.stringify(verdict).length} bytes`);
    assert.equal(lines.length, 1);
    assert.ok(lines[0].length < 2000, `log line is ${lines[0].length} bytes`);
  }
  const landlord = passBoundaryDoor({ verb: "log-activity", write: true, actor: DELL, now: NOW, log: () => {},
    args: manyRows(9000, () => ({ representation_side: "landlord" })) });
  assert.deepEqual(landlord.refusals.map(r => ({ ...r })),
    [{ boundary: "representation_scope", reason_id: "listing_side_exposure_refused", count: 9000 }]);
  const scope = landlord.checks.filter(c => c.boundary === "representation_scope");
  assert.equal(scope.length, 1);
  assert.equal(scope[0].count, 9000);
  assert.equal(scope[0].fields.length, 5);
  assert.equal(scope[0].fields_truncated, true);
  // The isolate buffer and the read projection stay bounded too.
  for (let i = 0; i < 40; i += 1) {
    passBoundaryDoor({ verb: "log-activity", write: true, actor: DELL, now: NOW, log: () => {},
      args: manyRows(3000, () => ({ representation_side: "seller", mrn: 2, activate_listing: 1 })) });
  }
  const snapshot = doorObservationSnapshot();
  assert.ok(snapshot.recent_refused.length <= 20);
  assert.ok(JSON.stringify(snapshot).length < 20000, `snapshot is ${JSON.stringify(snapshot).length} bytes`);
  assert.ok(JSON.stringify(v5BoundaryDoorProjection()).length < 60000);
});

test("the door never changes the caller's arguments", () => {
  const args = { deal: "C-1", rows: [{ representation_side: "landlord", patient_name: "x" }],
    activate_listing_side: true, side: "landlord", mode: "canary", ownership: [{ also_listing_side: true }] };
  const before = JSON.stringify(args);
  for (const verb of ["log-activity", "record-counter", "add-premises", "approve-rule", "accept-workflow"]) {
    passBoundaryDoor({ verb, write: true, actor: DELL, args, now: NOW, log: () => {} });
    evaluateDispatchBoundaries({ verb, write: true, actor: DELL, args, context: cloud });
  }
  assert.equal(JSON.stringify(args), before);
  assert.deepEqual(Object.keys(args), ["deal", "rows", "activate_listing_side", "side", "mode", "ownership"]);
});
