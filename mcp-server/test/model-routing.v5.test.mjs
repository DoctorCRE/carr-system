// V5-F04 — role descriptions, measured qualification and deterministic route
// selection, proved case by case.
//
// The suite is organised by the decision it proves. Every settled decision gets
// both halves: the positive case that must SELECT, and the negatives that must
// refuse — because a router that only ever refuses cannot be told apart from a
// broken one. The bypass cases get their own group: they are the shapes someone
// would reach for to get a cheaper, weaker, expired or unqualified occupant
// admitted, and each one has to have a named refusal rather than a silent pass.
//
// EVERY MODEL, VERSION, EFFORT, BACKEND, PRICE, RANK AND LATENCY BELOW IS A
// SYNTHETIC FIXTURE. Nothing here reports that a real provider, a real model
// version or a real Mac Studio was measured, evaluated or reached; no test makes
// a network call, and none could.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_CANONICAL_AUTHORITY,
  V5_LOCAL_CAPABILITIES,
  V5BoundaryError,
} from "../src/global-boundaries.v5.js";
import {
  V5_ROUTING_DECISIONS,
  V5_ROUTING_DECISION_IDS,
  V5_ROUTING_DECISION_SUBSET_DIGEST,
  V5_ROLE_KEYS,
  V5_RANKING_KEYS,
  V5_PUBLIC_PRODUCT_IDENTITY,
  V5_PROVENANCE_SCOPE,
  V5_ROUTING_RESULT_SCHEMA_VERSION,
  V5RoutingError,
  assertRoutingDecisionBinding,
  defineRole,
  assignOccupant,
  occupantKey,
  compileRoutingPolicy,
  buildJobEnvelope,
  bindEnvelopeToRoute,
  buildPromptPayload,
  proposeRoutePlan,
  createModelRoutingGate,
  v5ModelRoutingProjection,
  v5RoutingDecisionCanonicalBytes,
} from "../src/model-routing.v5.js";

// The four source-evidence digests exactly as the reviewed F04 source binding
// carries them, so drift between the module and the binding is a test failure
// rather than a later discovery.
const REVIEWED_DECISION_BINDING = Object.freeze({
  "Q031.D1": "5f10ccd4bbfec68187e0d41a7437a97afaccefb7e57fc54c7b241af49b710fae",
  "Q047.D1": "23d7a07d0334d3905b3f406a439309758579d363bad341f14fc7c386aa4c375b",
  "Q106.D1": "ef7521effb9256cb13a709265dacfefe9f86150b7b166e608437171952998de7",
  "Q130.D1": "f1fd79cfa15c15f473aec2d98ebd2d9ccaae22b5929694f8b5081cd6975273ff",
});

const NOW = "2026-09-09T12:00:00.000Z";
const MEASURED_AT = "2026-09-01T00:00:00.000Z";
const EXPIRES_AT = "2026-10-01T00:00:00.000Z";
const CONTEXT_DIGEST = digest({ fixture: "f04-context", n: 1 });

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5RoutingError, `expected a V5RoutingError, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}" (${error.message})`);
    return error;
  }
  return assert.fail(`expected a refusal with code "${code}"`);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

// --- synthetic fixtures ----------------------------------------------------

function route(route_key, task_class, backend_key, model_key, effort, residual_risk_class, cost) {
  return {
    route_key, task_class, backend_key, model_key, model_version: "2026-01", effort,
    residual_risk_class,
    cost_components: {
      base_cost_units: cost[0], expected_retry_cost_units: cost[1],
      expected_review_cost_units: cost[2], expected_fallback_cost_units: cost[3],
    },
  };
}

/** A fresh plain-object policy on every call, so a mutating test cannot leak. */
function basePolicy() {
  return {
    schema_version: "model-routing-policy.v1",
    policy_id: "policy:f04-fixture",
    policy_version: 3,
    ranking: {
      key_order: [
        "quality_rank", "residual_risk_rank", "privacy_rank",
        "latency_ms_p95", "expected_total_cost_units", "local_preference_rank",
      ],
    },
    risk_ranks: { "risk:low": 0, "risk:standard": 1, "risk:high": 2 },
    privacy_ranks: { "egress:none": 0, "egress:internal": 1, "egress:external": 2 },
    // ONE declared scale, weakest first, and both maps below name grades from
    // it. `grade:fixture-frontier` is declared and held by no occupant on
    // purpose: a floor that no available route reaches is a legitimate state
    // whose answer is `unavailable`, and the fixture has to be able to express
    // it. Every grade name here is synthetic; none of it is a claim about any
    // real model.
    quality_scale: {
      scale_id: "scale:f04-fixture-grades",
      scale_version: 2,
      grades: [
        "grade:fixture-draft", "grade:fixture-review",
        "grade:fixture-architecture", "grade:fixture-frontier",
      ],
    },
    occupant_grades: {
      "model:fixture-strong-a@2026-01/high": "grade:fixture-architecture",
      "model:fixture-mid-b@2026-01/standard": "grade:fixture-review",
      "model:fixture-weak-d@2026-01/low": "grade:fixture-draft",
    },
    strength_refs: {
      "strength:draft-grade": "grade:fixture-draft",
      "strength:review-grade": "grade:fixture-review",
      "strength:architecture-grade": "grade:fixture-architecture",
      "strength:frontier-grade": "grade:fixture-frontier",
    },
    maximum_qualification_age_ms: 2592000000,
    backends: [
      {
        backend_key: "backend:mac-studio-local", kind: "local", local_node: "mac-studio",
        egress_class: "egress:none",
        permitted_data_classes: ["lease_economics", "market_comp", "practice_business_profile"],
        user_facing: false, grants_authority: false,
      },
      {
        backend_key: "backend:hermes-platform", kind: "hosted_open_model_platform",
        egress_class: "egress:internal",
        permitted_data_classes: ["market_comp"],
        user_facing: false, grants_authority: false,
      },
      {
        backend_key: "backend:cloud-gateway", kind: "cloud",
        egress_class: "egress:external",
        permitted_data_classes: ["lease_economics", "market_comp", "practice_business_profile"],
        user_facing: false, grants_authority: false,
      },
    ],
    task_classes: [
      {
        task_class: "task:deal-review",
        permitted_backend_keys: [
          "backend:cloud-gateway", "backend:hermes-platform", "backend:mac-studio-local",
        ],
        permitted_data_classes: ["lease_economics", "market_comp"],
        required_quality_floor_refs: ["floor:reviewer-evidence-cited"],
        local_preference_enabled: true,
        local_capability: "local_model_inference",
        privacy_restriction: "none",
      },
      {
        task_class: "task:private-compute",
        permitted_backend_keys: ["backend:mac-studio-local"],
        permitted_data_classes: ["practice_business_profile"],
        required_quality_floor_refs: [],
        local_preference_enabled: true,
        local_capability: "private_compute_batch",
        privacy_restriction: "local_only",
      },
    ],
    routes: [
      // total cost 6
      route("route:local-strong", "task:deal-review", "backend:mac-studio-local",
        "model:fixture-strong-a", "high", "risk:standard", [4, 1, 1, 0]),
      // total cost 5 — cheaper than the local route, and qualified
      route("route:cloud-strong", "task:deal-review", "backend:cloud-gateway",
        "model:fixture-strong-a", "high", "risk:standard", [3, 1, 1, 0]),
      // total cost 4 — qualified, but its backend's data policy is narrower
      route("route:hermes-mid", "task:deal-review", "backend:hermes-platform",
        "model:fixture-mid-b", "standard", "risk:standard", [2, 1, 1, 0]),
      // total cost 2 — at required strength, never measured
      route("route:cloud-mid-cheap", "task:deal-review", "backend:cloud-gateway",
        "model:fixture-mid-b", "standard", "risk:standard", [1, 1, 0, 0]),
      // total cost 1 — the cheapest route in the policy, and under strength
      route("route:cloud-weak-cheap", "task:deal-review", "backend:cloud-gateway",
        "model:fixture-weak-d", "low", "risk:standard", [1, 0, 0, 0]),
      route("route:private-local", "task:private-compute", "backend:mac-studio-local",
        "model:fixture-strong-a", "high", "risk:low", [3, 0, 0, 0]),
    ],
  };
}

function compiled(mutate) {
  const document = basePolicy();
  if (mutate) mutate(document);
  return compileRoutingPolicy(document);
}

function reviewerRole(overrides = {}) {
  return defineRole({
    role_key: "reviewer",
    title: "Reviewer",
    mission: "Independently review delivered work against its accepted contract and its evidence.",
    skills: [
      "reads a delivered change against the accepted slice contract",
      "cites the exact evidence behind every finding",
    ],
    rules: [
      "never reviews work it authored",
      "records a finding rather than repairing the work silently",
    ],
    authority: {
      authority_class: "developer",
      capability_refs: ["capability:deal.read", "capability:review.record"],
    },
    evidence_requirements: ["an exact source digest for every finding"],
    quality_floor_refs: ["floor:reviewer-evidence-cited"],
    minimum_strength_ref: "strength:review-grade",
    task_classes: ["task:deal-review", "task:private-compute"],
    ...overrides,
  });
}

function dealReviewJob(overrides = {}) {
  return {
    job_id: "job:fixture-deal-review-1",
    task_class: "task:deal-review",
    data_classes: ["lease_economics", "market_comp"],
    required_tool_ids: ["tool:comp-lookup"],
    risk_class: "risk:standard",
    context_digest: CONTEXT_DIGEST,
    capability_refs: ["capability:deal.read"],
    receipt_binding_ref: "receipt:fixture-deal-review-1",
    ...overrides,
  };
}

function privateJob(overrides = {}) {
  return {
    job_id: "job:fixture-private-1",
    task_class: "task:private-compute",
    data_classes: ["practice_business_profile"],
    required_tool_ids: [],
    risk_class: "risk:low",
    context_digest: CONTEXT_DIGEST,
    capability_refs: [],
    receipt_binding_ref: "receipt:fixture-private-1",
    ...overrides,
  };
}

function qualification(overrides = {}) {
  const id = overrides.qualification_id ?? "qual:fixture-local";
  return {
    qualification_id: id,
    task_class: "task:deal-review",
    backend_key: "backend:mac-studio-local",
    model_key: "model:fixture-strong-a",
    model_version: "2026-01",
    effort: "high",
    qualified_tool_ids: ["tool:comp-lookup"],
    permitted_data_classes: ["lease_economics", "market_comp"],
    qualified_max_risk_class: "risk:standard",
    met_quality_floor_refs: ["floor:reviewer-evidence-cited"],
    measured_latency_ms_p95: 1200,
    measured_at: MEASURED_AT,
    expires_at: EXPIRES_AT,
    measurement_digest: digest({ fixture: "f04-measurement", qualification_id: id }),
    verifier_id: "verifier:fixture-evaluation-kernel",
    ...overrides,
  };
}

const localQual = () => qualification();
const cloudQual = () => qualification({
  qualification_id: "qual:fixture-cloud",
  backend_key: "backend:cloud-gateway",
  measured_latency_ms_p95: 2400,
});
/** A measured record for the cheapest, weakest, under-strength route. */
const weakQual = () => qualification({
  qualification_id: "qual:fixture-weak",
  backend_key: "backend:cloud-gateway",
  model_key: "model:fixture-weak-d",
  effort: "low",
  measured_latency_ms_p95: 300,
});
const privateQual = () => qualification({
  qualification_id: "qual:fixture-private",
  task_class: "task:private-compute",
  qualified_tool_ids: [],
  permitted_data_classes: ["practice_business_profile"],
  qualified_max_risk_class: "risk:low",
  measured_latency_ms_p95: 800,
});

/** The verifier identity the fixture records name, and the only one configured. */
const TRUSTED_VERIFIER_IDS = Object.freeze(["verifier:fixture-evaluation-kernel"]);

/** A gate whose only job is exposing `requestBindingDigest`. */
function probeGate() {
  return createModelRoutingGate({
    authenticateQualifications: () => ({}), trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
  });
}

/**
 * A gate over a synthetic verifier. The verifier stands in for the trusted
 * projection this repository does not yet have; it authenticates nothing real,
 * and the tests that matter are the ones proving the gate refuses when its
 * binding does not hold.
 */
function gateWith(qualifications, { bindingDigest, trusted = TRUSTED_VERIFIER_IDS } = {}) {
  const probe = probeGate();
  return createModelRoutingGate({
    trusted_verifier_ids: [...trusted],
    authenticateQualifications: request => ({
      request_binding_digest: bindingDigest ?? probe.requestBindingDigest(request),
      qualifications: clone(qualifications),
    }),
  });
}

function evaluate({
  qualifications = [localQual(), cloudQual()],
  job = dealReviewJob(),
  policy = compiled(),
  role = reviewerRole(),
  nodes = { "mac-studio": "available" },
  now = NOW,
} = {}) {
  return gateWith(qualifications).evaluate({
    policy, role, job, now, local_node_states: nodes,
  });
}

function reasonFor(result, routeKey) {
  const entry = result.considered.find(candidate => candidate.route_key === routeKey);
  assert.ok(entry, `route ${routeKey} was not considered`);
  return entry.reason_id;
}

// ---------------------------------------------------------------------------
// The settled decision binding.
// ---------------------------------------------------------------------------

test("the four settled decisions are bound by exact source-evidence digest", () => {
  assert.deepEqual([...V5_ROUTING_DECISION_IDS], ["Q031.D1", "Q047.D1", "Q106.D1", "Q130.D1"]);
  for (const [id, expected] of Object.entries(REVIEWED_DECISION_BINDING)) {
    assert.equal(V5_ROUTING_DECISIONS[id].source_evidence_digest, expected);
  }
  // The subset digest is computed from the table rather than pasted beside it.
  assert.equal(V5_ROUTING_DECISION_SUBSET_DIGEST, digest(JSON.parse(v5RoutingDecisionCanonicalBytes())));
  assert.equal(assertRoutingDecisionBinding({
    decisions: Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
      .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }])),
    decision_subset_digest: V5_ROUTING_DECISION_SUBSET_DIGEST,
  }), true);
});

test("a drifted decision subset refuses in both directions", () => {
  const good = Object.fromEntries(Object.entries(REVIEWED_DECISION_BINDING)
    .map(([id, source_evidence_digest]) => [id, { source_evidence_digest }]));
  const missing = { ...good };
  delete missing["Q047.D1"];
  refuses(() => assertRoutingDecisionBinding({ decisions: missing }), "decision_binding_drift");
  refuses(() => assertRoutingDecisionBinding({
    decisions: { ...good, "Q999.D1": { source_evidence_digest: "0".repeat(64) } },
  }), "decision_binding_drift");
  refuses(() => assertRoutingDecisionBinding({
    decisions: { ...good, "Q031.D1": { source_evidence_digest: "a".repeat(64) } },
  }), "decision_binding_drift");
  refuses(() => assertRoutingDecisionBinding({
    decisions: good, decision_subset_digest: `sha256:${"b".repeat(64)}`,
  }), "decision_binding_drift");
  refuses(() => assertRoutingDecisionBinding({
    decisions: good, settled: true,
  }), "unknown_field");
});

// ---------------------------------------------------------------------------
// Q106 — a role is durable, its occupant is not.
// ---------------------------------------------------------------------------

test("a role description seals its own content and is stable", () => {
  const role = reviewerRole();
  assert.equal(role.role_key, "reviewer");
  assert.equal(role.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(role.occupant_bound, false);
  assert.equal(role.occupants_replaceable, true);
  assert.equal(role.role_confers_no_authority_by_itself, true);
  assert.equal(reviewerRole().role_digest, role.role_digest);
  assert.ok(V5_ROLE_KEYS.includes(role.role_key));
  // Durable job description, in human-readable terms.
  assert.ok(role.skills.length > 0 && role.rules.length > 0);
  assert.ok(role.evidence_requirements.length > 0 && role.quality_floor_refs.length > 0);
});

test("replacing the occupant does not move the role", () => {
  const role = reviewerRole();
  const first = assignOccupant(role, {
    model_key: "model:fixture-strong-a", model_version: "2026-01",
    effort: "high", backend_key: "backend:cloud-gateway",
  });
  const second = assignOccupant(role, {
    model_key: "model:fixture-mid-b", model_version: "2026-01",
    effort: "standard", backend_key: "backend:hermes-platform",
  });
  assert.equal(first.role_digest, role.role_digest);
  assert.equal(second.role_digest, role.role_digest);
  assert.equal(first.role_digest_unchanged_by_occupant, true);
  assert.notEqual(first.occupant_key, second.occupant_key);
  assert.equal(first.occupant_grants_no_authority, true);
  assert.equal(second.occupant_grants_no_authority, true);
  assert.equal(second.canonical_authority, V5_CANONICAL_AUTHORITY);
  assert.equal(second.public_product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
  assert.equal(occupantKey({
    model_key: "model:fixture-mid-b", model_version: "2026-01",
    effort: "standard", backend_key: "backend:hermes-platform",
  }), "model:fixture-mid-b@2026-01/standard");
});

test("a role a replaceable model could never occupy refuses", () => {
  refuses(() => reviewerRole({
    authority: { authority_class: "system_authority", capability_refs: ["capability:deal.read"] },
  }), "role_authority_class_not_occupiable");
  refuses(() => reviewerRole({ role_key: "chief_of_staff" }), "unknown_role_key");
  refuses(() => defineRole({ ...clone(reviewerRole()), role_digest: "x" }), "unknown_field");
  refuses(() => reviewerRole({ quality_floor_refs: [] }), "invalid_shape");
  refuses(() => reviewerRole({ skills: [] }), "invalid_shape");
});

test("a role is deeply frozen", () => {
  const role = reviewerRole();
  assert.throws(() => { role.role_key = "operator"; }, TypeError);
  assert.throws(() => { role.skills.push("something"); }, TypeError);
  assert.throws(() => { role.authority.authority_class = "system_authority"; }, TypeError);
});

// ---------------------------------------------------------------------------
// The versioned policy: input, not invention.
// ---------------------------------------------------------------------------

test("a policy compiles to its own digest over the exact bytes supplied", () => {
  const policy = compiled();
  assert.equal(policy.policy_digest, digest(policy.source));
  assert.equal(policy.policy_version, 3);
  assert.deepEqual([...policy.ranking_key_order], [...V5_RANKING_KEYS]);
  assert.equal(policy.routes.find(r => r.route_key === "route:local-strong").expected_total_cost_units, 6);
  assert.equal(policy.routes.find(r => r.route_key === "route:cloud-weak-cheap").expected_total_cost_units, 1);
});

test("a policy cannot make a backend authoritative or user-facing", () => {
  refuses(() => compiled(p => { p.backends[1].user_facing = true; }), "backend_user_facing_refused");
  refuses(() => compiled(p => { p.backends[1].grants_authority = true; }), "backend_authority_mint_refused");
});

test("the ranking order must be an exact permutation of the registered keys", () => {
  refuses(() => compiled(p => { p.ranking.key_order = p.ranking.key_order.slice(1); }),
    "ranking_key_order_invalid");
  refuses(() => compiled(p => { p.ranking.key_order = [...V5_RANKING_KEYS.slice(1), "quality_rank", "quality_rank"]; }),
    "ranking_key_order_invalid");
  refuses(() => compiled(p => { p.ranking.key_order = [...V5_RANKING_KEYS.slice(1), "vibes"]; }),
    "ranking_key_order_invalid");
});

test("a policy that omits a number is refused rather than having one defaulted", () => {
  refuses(() => compiled(p => { delete p.occupant_grades["model:fixture-strong-a@2026-01/high"]; }),
    "policy_rank_missing");
  refuses(() => compiled(p => { delete p.risk_ranks["risk:standard"]; }), "policy_rank_missing");
  refuses(() => compiled(p => { delete p.routes[0].cost_components.expected_fallback_cost_units; }),
    "missing_field");
  refuses(() => compiled(p => { p.routes[0].cost_components.expected_retry_cost_units = -1; }),
    "invalid_shape");
});

test("a policy shares one data-class vocabulary with the global boundary", () => {
  refuses(() => compiled(p => { p.backends[0].permitted_data_classes.push("phi"); }),
    "prohibited_data_class_in_policy");
  refuses(() => compiled(p => { p.backends[0].permitted_data_classes.push("whatever_we_like"); }),
    "unknown_data_class");
});

test("a policy cannot contradict its own privacy restriction or repeat a route", () => {
  refuses(() => compiled(p => {
    p.task_classes[1].permitted_backend_keys = ["backend:cloud-gateway", "backend:mac-studio-local"];
  }), "privacy_restriction_contradicted");
  refuses(() => compiled(p => {
    p.routes.push(route("route:duplicate-identity", "task:deal-review", "backend:cloud-gateway",
      "model:fixture-strong-a", "high", "risk:standard", [1, 0, 0, 0]));
  }), "duplicate_entry");
  refuses(() => compiled(p => { p.weights = { cost: 0.5 }; }), "unknown_field");
  refuses(() => compiled(p => { p.task_classes[0].local_capability = "teleportation"; }),
    "unknown_local_capability");
});

// ---------------------------------------------------------------------------
// The one declared strength scale.
//
// The no-downgrade guarantee is a comparison, and a comparison needs a shared
// scale. These prove the scale is declared once, that both sides of the
// comparison resolve through it, and that every way of breaking that refuses.
// ---------------------------------------------------------------------------

test("occupant grades and minimum-strength references resolve through one declared scale", () => {
  const policy = compiled();
  assert.equal(policy.quality_scale.scale_id, "scale:f04-fixture-grades");
  assert.equal(policy.quality_scale.scale_version, 2);
  assert.deepEqual([...policy.quality_scale.grades], [
    "grade:fixture-draft", "grade:fixture-review",
    "grade:fixture-architecture", "grade:fixture-frontier",
  ]);
  // The only strength numbers in the slice are positions in that declared
  // order — nothing declares an independent number of its own.
  assert.equal(policy.quality_scale.ranks["grade:fixture-draft"], 0);
  assert.equal(policy.quality_scale.ranks["grade:fixture-frontier"], 3);
  assert.equal(policy.occupant_grades["model:fixture-strong-a@2026-01/high"], "grade:fixture-architecture");
  assert.equal(policy.strength_refs["strength:review-grade"], "grade:fixture-review");

  const result = evaluate();
  assert.equal(result.quality_scale_id, "scale:f04-fixture-grades");
  assert.equal(result.quality_scale_version, 2);
  assert.equal(result.required_strength_ref, "strength:review-grade");
  assert.equal(result.required_strength_grade, "grade:fixture-review");
  assert.equal(result.required_strength_rank, policy.quality_scale.ranks["grade:fixture-review"]);
  assert.equal(result.selected_quality_grade, "grade:fixture-architecture");
  assert.equal(result.selected_strength_rank, policy.quality_scale.ranks["grade:fixture-architecture"]);
});

test("a grade the declared scale does not list is refused on either side", () => {
  refuses(() => compiled(p => {
    p.occupant_grades["model:fixture-strong-a@2026-01/high"] = "grade:invented-by-the-caller";
  }), "unknown_quality_grade");
  refuses(() => compiled(p => { p.strength_refs["strength:review-grade"] = "grade:invented-by-the-caller"; }),
    "unknown_quality_grade");
  // And a numeric map cannot be smuggled back in beside the named one.
  refuses(() => compiled(p => { p.occupant_grades["model:fixture-strong-a@2026-01/high"] = 30; }),
    "unknown_quality_grade");
  refuses(() => compiled(p => { p.strength_refs["strength:review-grade"] = 20; }), "unknown_quality_grade");
  refuses(() => compiled(p => { p.quality_ranks = { "model:fixture-strong-a@2026-01/high": 30 }; }),
    "unknown_field");
});

test("a malformed or ambiguous scale is refused before anything is compared", () => {
  refuses(() => compiled(p => { p.quality_scale.grades = ["grade:fixture-review"]; }), "quality_scale_invalid");
  refuses(() => compiled(p => {
    p.quality_scale.grades = ["grade:fixture-draft", "grade:fixture-review", "grade:fixture-draft"];
  }), "quality_scale_invalid");
  refuses(() => compiled(p => { p.quality_scale.grades = { strongest: "grade:fixture-review" }; }),
    "quality_scale_invalid");
  refuses(() => compiled(p => { delete p.quality_scale.scale_version; }), "missing_field");
  refuses(() => compiled(p => { p.quality_scale.notes = "roughly benchmark order"; }), "unknown_field");
  refuses(() => compiled(p => { delete p.quality_scale; }), "missing_field");
});

test("a changed scale changes the policy and refuses predictably rather than quietly", () => {
  // Same grade names, a different declared order and a new version: the
  // reviewer floor now sits above every occupant the policy declares.
  const reordered = compiled(p => {
    p.quality_scale.scale_version = 3;
    p.quality_scale.grades = [
      "grade:fixture-frontier", "grade:fixture-architecture",
      "grade:fixture-draft", "grade:fixture-review",
    ];
  });
  assert.notEqual(reordered.policy_digest, compiled().policy_digest);
  const result = evaluate({ policy: reordered });
  assert.equal(result.quality_scale_version, 3);
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "no_qualified_route_at_required_strength");
  assert.equal(result.selected_route, null);
  assert.equal(result.downgraded_from_required_strength, false);
  assert.equal(reasonFor(result, "route:local-strong"), "below_required_strength");
});

test("a floor no available route reaches is unavailable, never a weaker occupant", () => {
  // `strength:frontier-grade` is declared, resolves on the scale, and no
  // occupant in the policy holds that grade. That is a legitimate policy, not a
  // broken one, and the answer is an honest unavailable.
  const result = evaluate({ job: dealReviewJob({ minimum_strength_ref: "strength:frontier-grade" }) });
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "no_qualified_route_at_required_strength");
  assert.equal(result.required_strength_grade, "grade:fixture-frontier");
  assert.equal(result.selected_route, null);
  assert.equal(result.selected_qualification_id, null);
  assert.equal(result.downgraded_from_required_strength, false);
  for (const entry of result.considered) assert.equal(entry.admitted, false);
  // Every route whose backend and data policy let it get as far as the strength
  // check is refused there — including the ones with current measurements.
  for (const routeKey of ["route:local-strong", "route:cloud-strong",
    "route:cloud-mid-cheap", "route:cloud-weak-cheap"]) {
    assert.equal(reasonFor(result, routeKey), "below_required_strength");
  }
  // An exact pin at the same unreachable floor refuses rather than substituting.
  const pinned = evaluate({
    job: dealReviewJob({
      minimum_strength_ref: "strength:frontier-grade",
      pinned_route: {
        model_key: "model:fixture-strong-a", model_version: "2026-01",
        effort: "high", backend_key: "backend:cloud-gateway",
      },
    }),
  });
  assert.equal(pinned.decision, "refuse");
  assert.equal(pinned.reason_id, "pinned_route_not_qualified");
  assert.equal(pinned.pin_refusal_reason_id, "below_required_strength");
});

test("a grade map key that is not an occupant identity is refused, and so is an unkeyable route", () => {
  // The occupant-key grammar is the one correction this module makes to the
  // rank-map contract, so it gets its own negative case in both directions.
  refuses(() => compiled(p => { p.occupant_grades["not-an-occupant-key"] = "grade:fixture-review"; }),
    "invalid_shape");
  refuses(() => compiled(p => { p.occupant_grades["model:x@2026-01/high/extra"] = "grade:fixture-review"; }),
    "invalid_shape");
  refuses(() => compiled(p => {
    p.occupant_grades["model:fixture/slashed@2026-01/high"] = "grade:fixture-review";
  }), "invalid_shape");
  // A route whose model key carries a separator therefore has no declarable
  // grade, and refuses by name instead of matching some other occupant's.
  refuses(() => compiled(p => {
    p.routes.push(route("route:slashed", "task:deal-review", "backend:cloud-gateway",
      "model:fixture/slashed", "high", "risk:standard", [1, 0, 0, 0]));
  }), "policy_rank_missing");
});

test("a task class or backend named after an Object.prototype member refuses by name", () => {
  // `constructor` and `prototype` are grammar-valid reference tokens, so the
  // policy maps are null-prototype and every membership test asks for an OWN
  // key. An undeclared one must be a named refusal, never an inherited hit and
  // never a raw TypeError further down.
  for (const token of ["constructor", "prototype"]) {
    refuses(() => evaluate({
      role: reviewerRole({ task_classes: [token, "task:deal-review"] }),
      job: dealReviewJob({ task_class: token }),
    }), "unknown_task_class");
    refuses(() => compiled(p => { p.task_classes[0].permitted_backend_keys = [token]; }),
      "unknown_backend_key");
    refuses(() => compiled(p => { p.routes[0].backend_key = token; }), "unknown_backend_key");
  }
  // Route backend references are checked against the declared own keys first.
  // `__proto__` is undeclared and cannot resolve through Object.prototype.
  refuses(() => compiled(p => { p.routes[0].backend_key = "__proto__"; }), "unknown_backend_key");
  // And a policy may legitimately declare one: it is an ordinary key.
  const named = compiled(p => {
    p.task_classes.push({
      task_class: "constructor",
      permitted_backend_keys: ["backend:cloud-gateway"],
      permitted_data_classes: ["market_comp"],
      required_quality_floor_refs: [],
      local_preference_enabled: false,
      privacy_restriction: "none",
    });
    p.routes.push(route("route:constructor-cloud", "constructor", "backend:cloud-gateway",
      "model:fixture-strong-a", "high", "risk:standard", [1, 0, 0, 0]));
  });
  assert.equal(named.task_classes["constructor"].task_class, "constructor");
  assert.equal(named.routes.some(r => r.task_class === "constructor"), true);
  refuses(() => compiled(p => {
    p.task_classes.push({ ...p.task_classes[0], task_class: "task:deal-review" });
  }), "duplicate_entry");
});

// ---------------------------------------------------------------------------
// Q031 — the qualified route.
// ---------------------------------------------------------------------------

test("a qualified local route is selected, authenticated, and never a downgrade", () => {
  const result = evaluate();
  assert.equal(result.schema_version, V5_ROUTING_RESULT_SCHEMA_VERSION);
  assert.equal(result.decision, "route");
  assert.equal(result.reason_id, "highest_ranked_qualified_route");
  assert.equal(result.selected_route.route_key, "route:local-strong");
  assert.equal(result.selected_qualification_id, "qual:fixture-local");
  assert.equal(result.qualification_authenticated, true);
  assert.equal(result.qualification_status, "authenticated_by_trusted_projection_verifier");
  assert.equal(result.admissible_for_dispatch, true);
  assert.equal(result.downgraded_from_required_strength, false);
  assert.equal(result.cost_can_qualify, false);
  assert.equal(result.backend_grants_authority, false);
  assert.equal(result.dispatch_implemented, false);
  assert.equal(result.public_product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
  assert.equal(result.canonical_authority, V5_CANONICAL_AUTHORITY);
  assert.ok(result.selected_strength_rank >= result.required_strength_rank);
  assert.equal(result.fallback, null);
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.effects.network_calls, 0);
  assert.equal(result.effects.provider_actions, 0);
});

test("a cheaper route never qualifies by being cheaper", () => {
  const result = evaluate();
  const policy = compiled();
  const cheapest = [...policy.routes]
    .filter(r => r.task_class === "task:deal-review")
    .sort((a, b) => a.expected_total_cost_units - b.expected_total_cost_units)[0];
  // The cheapest route in the whole policy is refused, and the selected one
  // costs six times as much.
  assert.equal(cheapest.route_key, "route:cloud-weak-cheap");
  assert.equal(reasonFor(result, "route:cloud-weak-cheap"), "below_required_strength");
  assert.equal(reasonFor(result, "route:cloud-mid-cheap"), "no_qualification_record");
  assert.equal(result.selected_route.expected_total_cost_units, 6);
  assert.ok(result.selected_route.expected_total_cost_units > cheapest.expected_total_cost_units);
  // And the total is the declared components summed, retries and review included.
  assert.deepEqual({ ...result.selected_route.cost_components }, {
    base_cost_units: 4, expected_retry_cost_units: 1,
    expected_review_cost_units: 1, expected_fallback_cost_units: 0,
  });
});

test("a backend whose data policy is narrower than the job is refused", () => {
  assert.equal(reasonFor(evaluate(), "route:hermes-mid"), "backend_data_policy_excludes_data_class");
});

test("the ranking order is the policy's, and changing it changes the answer", () => {
  const costFirst = compiled(p => {
    p.ranking.key_order = [
      "expected_total_cost_units", "quality_rank", "residual_risk_rank",
      "privacy_rank", "latency_ms_p95", "local_preference_rank",
    ];
  });
  const byCost = evaluate({ policy: costFirst });
  assert.equal(byCost.decision, "route");
  // Same admitted set, different declared priority, different selection — and
  // the cheaper one is still a QUALIFIED route, never an unqualified one.
  assert.equal(byCost.selected_route.route_key, "route:cloud-strong");
  assert.equal(byCost.selected_route.expected_total_cost_units, 5);
  assert.equal(evaluate().selected_route.route_key, "route:local-strong");
});

test("two identical evaluations produce byte-identical results", () => {
  assert.equal(canonicalJson(evaluate()), canonicalJson(evaluate()));
});

// ---------------------------------------------------------------------------
// Q031 — the negatives. Every one of these must refuse the route.
// ---------------------------------------------------------------------------

test("an expired, future or policy-stale qualification refuses its route", () => {
  const expired = evaluate({
    qualifications: [
      qualification({ expires_at: "2026-09-05T00:00:00.000Z" }),
      cloudQual(),
    ],
  });
  assert.equal(reasonFor(expired, "route:local-strong"), "qualification_expired");
  assert.equal(expired.selected_route.route_key, "route:cloud-strong");

  const future = evaluate({
    qualifications: [qualification({
      measured_at: "2026-09-20T00:00:00.000Z", expires_at: "2026-10-20T00:00:00.000Z",
    }), cloudQual()],
  });
  assert.equal(reasonFor(future, "route:local-strong"), "qualification_not_yet_effective");

  const tightPolicy = compiled(p => { p.maximum_qualification_age_ms = 86400000; });
  const stale = evaluate({ policy: tightPolicy });
  assert.equal(reasonFor(stale, "route:local-strong"), "qualification_stale_for_policy");
  assert.equal(reasonFor(stale, "route:cloud-strong"), "qualification_stale_for_policy");
  assert.equal(stale.decision, "unavailable");
});

test("a qualification measured for a different task, model, version, effort or backend refuses", () => {
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ task_class: "task:private-compute" })] }),
      "route:local-strong"),
    "qualification_task_class_mismatch");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ model_version: "2025-06" })] }), "route:local-strong"),
    "qualification_model_version_mismatch");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ effort: "standard" })] }), "route:local-strong"),
    "qualification_effort_mismatch");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ backend_key: "backend:cloud-gateway" })] }),
      "route:local-strong"),
    "qualification_backend_mismatch");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ model_key: "model:fixture-mid-b" })] }),
      "route:local-strong"),
    "no_qualification_record");
});

test("two records for one exact route are an ambiguous projection, in either array order", () => {
  // Exactly what a RE-MEASUREMENT produces: two different ids measured for the
  // same task class, backend, model, version and effort. Whichever the verifier
  // returned first would otherwise decide the reported reason, and its measured
  // latency would decide the ranking.
  const older = qualification({
    qualification_id: "qual:fixture-local-older", expires_at: "2026-09-05T00:00:00.000Z",
  });
  const newer = qualification({ qualification_id: "qual:fixture-local-newer" });
  const first = refuses(() => evaluate({ qualifications: [older, newer, cloudQual()] }),
    "ambiguous_qualification_projection");
  const second = refuses(() => evaluate({ qualifications: [newer, older, cloudQual()] }),
    "ambiguous_qualification_projection");
  // The same refusal and the same detail from both orders: the answer is a
  // property of the SET of records, never of the order they arrived in.
  assert.deepEqual(first.detail, second.detail);
  assert.deepEqual(first.detail.qualification_ids,
    ["qual:fixture-local-newer", "qual:fixture-local-older"]);
  assert.equal(first.detail.route_tuple,
    "task:deal-review|backend:mac-studio-local|model:fixture-strong-a|2026-01|high");
  // The unauthenticated proposal path runs the same predicate, so nobody gets a
  // different answer by asking offline.
  refuses(() => proposeRoutePlan({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
    qualifications: [newer, older],
  }), "ambiguous_qualification_projection");
  // And the projection says whose job it is to resolve, rather than this module
  // inventing a latest-proof-wins rule or discarding the superseded record.
  assert.ok(v5ModelRoutingProjection().unimplemented_dependencies.some(entry =>
    entry.includes("exactly ONE current measurement per exact")));
});

test("a record differing in any one route field is not ambiguous and still routes", () => {
  const differences = {
    task_class: "task:private-compute",
    backend_key: "backend:cloud-gateway",
    model_key: "model:fixture-mid-b",
    model_version: "2025-06",
    effort: "standard",
  };
  for (const [field, value] of Object.entries(differences)) {
    const other = qualification({ qualification_id: `qual:fixture-differs-${field}`, [field]: value });
    const result = evaluate({ qualifications: [localQual(), other] });
    assert.equal(result.decision, "route", `a record differing only in ${field} must not refuse`);
    assert.equal(result.selected_route.route_key, "route:local-strong");
  }
});

test("a qualification that did not cover the tools, data, risk or floors refuses", () => {
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ qualified_tool_ids: ["tool:something-else"] })] }),
      "route:local-strong"),
    "tool_not_qualified");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ permitted_data_classes: ["market_comp"] })] }),
      "route:local-strong"),
    "data_class_not_qualified");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ qualified_max_risk_class: "risk:low" })] }),
      "route:local-strong"),
    "risk_class_not_qualified");
  assert.equal(
    reasonFor(evaluate({ qualifications: [qualification({ met_quality_floor_refs: [] })] }),
      "route:local-strong"),
    "quality_floor_not_met");
});

test("nothing current at all is an honest unavailable, not an empty success", () => {
  const noWeakRoute = compiled(p => {
    p.routes = p.routes.filter(r => r.route_key !== "route:cloud-weak-cheap");
  });
  const result = evaluate({ policy: noWeakRoute, qualifications: [] });
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "no_qualified_route_available");
  assert.equal(result.selected_route, null);
  assert.equal(result.selected_qualification_id, null);
  assert.equal(result.privacy_breach_avoided, false);
  assert.equal(result.downgraded_from_required_strength, false);
});

test("the global privacy boundary answers before any route is considered", () => {
  const phi = evaluate({ job: dealReviewJob({ data_classes: ["phi"] }) });
  assert.equal(phi.decision, "refuse");
  assert.equal(phi.reason_id, "data_class_prohibited_by_global_boundary");
  assert.deepEqual(phi.considered, []);

  const aggregate = evaluate({
    job: dealReviewJob({ data_classes: ["aggregate_patient_location_heatmap"] }),
  });
  assert.equal(aggregate.decision, "refuse");
  assert.equal(aggregate.reason_id, "data_class_needs_independent_privacy_route");
  assert.ok(aggregate.required_evidence.startsWith("step:"));

  const outsideTask = evaluate({ job: dealReviewJob({ data_classes: ["public_registry_record"] }) });
  assert.equal(outsideTask.decision, "refuse");
  assert.equal(outsideTask.reason_id, "data_class_outside_task_class");
});

test("a job outside its role, or reaching past it, refuses", () => {
  const outsideRole = evaluate({
    role: reviewerRole({ task_classes: ["task:private-compute"] }),
  });
  assert.equal(outsideRole.decision, "refuse");
  assert.equal(outsideRole.reason_id, "task_class_outside_role");

  const overreach = evaluate({
    job: dealReviewJob({ capability_refs: ["capability:deal.read", "capability:release.publish"] }),
  });
  assert.equal(overreach.decision, "refuse");
  assert.equal(overreach.reason_id, "job_exceeds_role_capabilities");
  assert.deepEqual(overreach.missing_capability_refs, ["capability:release.publish"]);
});

// ---------------------------------------------------------------------------
// Q031 — required strength and the exact pin.
// ---------------------------------------------------------------------------

test("a weaker model is left unused rather than quietly substituted", () => {
  const weakOnly = evaluate({
    qualifications: [qualification({
      qualification_id: "qual:fixture-weak",
      backend_key: "backend:cloud-gateway",
      model_key: "model:fixture-weak-d",
      effort: "low",
    })],
  });
  assert.equal(weakOnly.decision, "unavailable");
  assert.equal(weakOnly.reason_id, "no_qualified_route_at_required_strength");
  assert.equal(weakOnly.selected_route, null);
  assert.equal(weakOnly.downgraded_from_required_strength, false);
  // The weak route is refused on strength BEFORE its qualification is read, so
  // no amount of measurement can promote it past the role's floor.
  assert.equal(reasonFor(weakOnly, "route:cloud-weak-cheap"), "below_required_strength");
});

test("a job may raise the role's strength floor and may never lower it", () => {
  const scale = compiled().quality_scale;
  const raised = evaluate({
    job: dealReviewJob({ minimum_strength_ref: "strength:architecture-grade" }),
  });
  assert.equal(raised.decision, "route");
  assert.equal(raised.required_strength_grade, "grade:fixture-architecture");
  assert.equal(raised.required_strength_rank, scale.ranks["grade:fixture-architecture"]);

  const lowered = evaluate({ job: dealReviewJob({ minimum_strength_ref: "strength:draft-grade" }) });
  assert.equal(lowered.decision, "refuse");
  assert.equal(lowered.reason_id, "job_lowers_role_minimum_strength");
  // The role's floor, not the job's: the request never lowers it.
  assert.equal(lowered.required_strength_grade, "grade:fixture-review");
  assert.equal(lowered.required_strength_rank, scale.ranks["grade:fixture-review"]);

  // A minimum-strength reference the policy never declared is a named refusal,
  // not a defaulted floor.
  refuses(() => evaluate({ job: dealReviewJob({ minimum_strength_ref: "strength:undeclared" }) }),
    "policy_rank_missing");
});

test("an exact pin selects exactly that route, or refuses without falling back", () => {
  const pinnedCloud = evaluate({
    job: dealReviewJob({
      pinned_route: {
        model_key: "model:fixture-strong-a", model_version: "2026-01",
        effort: "high", backend_key: "backend:cloud-gateway",
      },
    }),
  });
  // The local route outranks it and is still not chosen: a pin is a pin.
  assert.equal(pinnedCloud.decision, "route");
  assert.equal(pinnedCloud.reason_id, "pinned_route_qualified");
  assert.equal(pinnedCloud.selected_route.route_key, "route:cloud-strong");

  const pinnedLostLocal = evaluate({
    nodes: { "mac-studio": "unavailable" },
    job: dealReviewJob({
      pinned_route: {
        model_key: "model:fixture-strong-a", model_version: "2026-01",
        effort: "high", backend_key: "backend:mac-studio-local",
      },
    }),
  });
  assert.equal(pinnedLostLocal.decision, "refuse");
  assert.equal(pinnedLostLocal.reason_id, "pinned_route_not_qualified");
  assert.equal(pinnedLostLocal.pin_refusal_reason_id, "local_route_not_executable");
  assert.equal(pinnedLostLocal.selected_route, null);
  assert.equal(pinnedLostLocal.fallback, null);

  const pinnedWeak = evaluate({
    job: dealReviewJob({
      pinned_route: {
        model_key: "model:fixture-weak-d", model_version: "2026-01",
        effort: "low", backend_key: "backend:cloud-gateway",
      },
    }),
  });
  assert.equal(pinnedWeak.reason_id, "pinned_route_not_qualified");
  assert.equal(pinnedWeak.pin_refusal_reason_id, "below_required_strength");

  const pinnedUnknown = evaluate({
    job: dealReviewJob({
      pinned_route: {
        model_key: "model:fixture-nonexistent", model_version: "2026-01",
        effort: "high", backend_key: "backend:cloud-gateway",
      },
    }),
  });
  assert.equal(pinnedUnknown.decision, "refuse");
  assert.equal(pinnedUnknown.reason_id, "pinned_route_not_in_policy");
});

// ---------------------------------------------------------------------------
// Q047 — the preferred, replaceable local node.
// ---------------------------------------------------------------------------

test("losing the local node falls back explicitly to a qualified permitted route", () => {
  for (const state of ["unavailable", "degraded"]) {
    const result = evaluate({ nodes: { "mac-studio": state } });
    assert.equal(result.decision, "route");
    assert.equal(result.reason_id, "qualified_permitted_fallback_selected");
    assert.equal(result.selected_route.route_key, "route:cloud-strong");
    assert.equal(result.fallback.from_route_key, "route:local-strong");
    assert.equal(result.fallback.from_reason_id, "local_route_not_executable");
    assert.equal(result.fallback.to_route_key, "route:cloud-strong");
    assert.equal(result.fallback.explicit, true);
    assert.equal(result.fallback.privacy_restriction_respected, true);
    // The fallback target is itself measured; a fallback is not an exemption.
    assert.equal(result.selected_qualification_id, "qual:fixture-cloud");
  }
});

test("an unreported local node state fails closed rather than being assumed up", () => {
  const result = evaluate({ nodes: {} });
  assert.equal(reasonFor(result, "route:local-strong"), "local_node_state_unknown");
  assert.equal(result.fallback.from_reason_id, "local_node_state_unknown");
});

test("a privacy-restricted class reports unavailable rather than leaving the node", () => {
  const available = evaluate({
    job: privateJob(), qualifications: [privateQual()], nodes: { "mac-studio": "available" },
  });
  assert.equal(available.decision, "route");
  assert.equal(available.selected_route.route_key, "route:private-local");
  assert.equal(available.selected_route.local_node, "mac-studio");

  const lost = evaluate({
    job: privateJob(), qualifications: [privateQual()], nodes: { "mac-studio": "unavailable" },
  });
  assert.equal(lost.decision, "unavailable");
  assert.equal(lost.reason_id, "local_only_privacy_restriction_no_permitted_fallback");
  assert.equal(lost.privacy_breach_avoided, true);
  assert.equal(lost.selected_route, null);
  assert.equal(lost.fallback, null);
  assert.equal(lost.privacy_restriction, "local_only");
});

test("local preference decides a genuine tie, and decides nothing else", () => {
  // Q047 asks for the node to be actively PREFERRED, not merely tolerated. In
  // the main fixture the local route already wins on privacy, so the preference
  // key never decides anything; this policy ties the two routes on all five
  // preceding keys so the preference is the only thing left.
  const tiePolicy = mutate => compiled(p => {
    // A declared statement that an internal-only platform and the local node
    // rank the same on egress. It is the policy's to make, not this module's.
    p.privacy_ranks["egress:internal"] = 0;
    p.routes.push(route("route:hermes-strong", "task:deal-review", "backend:hermes-platform",
      "model:fixture-strong-a", "high", "risk:standard", [4, 1, 1, 0]));
    if (mutate) mutate(p);
  });
  const job = dealReviewJob({
    job_id: "job:fixture-tie-1", data_classes: ["market_comp"],
    receipt_binding_ref: "receipt:fixture-tie-1",
  });
  const qualifications = [
    qualification({ qualification_id: "qual:tie-local", permitted_data_classes: ["market_comp"] }),
    qualification({
      qualification_id: "qual:tie-hermes", backend_key: "backend:hermes-platform",
      permitted_data_classes: ["market_comp"], measured_latency_ms_p95: 1200,
    }),
    cloudQual(),
  ];

  const preferred = evaluate({ policy: tiePolicy(), job, qualifications });
  assert.equal(preferred.decision, "route");
  assert.equal(preferred.selected_route.route_key, "route:local-strong");
  assert.equal(reasonFor(preferred, "route:hermes-strong"), "route_qualified");
  const local = preferred.considered.find(entry => entry.route_key === "route:local-strong");
  const hermes = preferred.considered.find(entry => entry.route_key === "route:hermes-strong");
  for (const key of ["quality_rank", "residual_risk_rank", "privacy_rank",
    "latency_ms_p95", "expected_total_cost_units"]) {
    assert.equal(local.ranking[key], hermes.ranking[key], `${key} must tie for this case to mean anything`);
  }
  assert.equal(local.ranking.local_preference_rank, 0);
  assert.equal(hermes.ranking.local_preference_rank, 1);

  // Turn the declared preference off and the identical tie resolves the other
  // way, on the final route_key tiebreak. So the preference decided it.
  const notPreferred = evaluate({
    policy: tiePolicy(p => { p.task_classes[0].local_preference_enabled = false; }),
    job, qualifications,
  });
  assert.equal(notPreferred.selected_route.route_key, "route:hermes-strong");
  assert.equal(notPreferred.fallback, null);
});

test("the local node carries no unique authority in any outcome", () => {
  const onNode = evaluate({ job: privateJob(), qualifications: [privateQual()] });
  assert.equal(onNode.backend_grants_authority, false);
  assert.equal(onNode.canonical_authority, V5_CANONICAL_AUTHORITY);
  assert.equal(onNode.public_product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
});

// ---------------------------------------------------------------------------
// Q130 / Q106 — one envelope across local, Hermes and cloud.
// ---------------------------------------------------------------------------

test("the same job keeps one binding digest whichever backend answers", () => {
  const marketOnlyJob = dealReviewJob({
    job_id: "job:fixture-market-only",
    data_classes: ["market_comp"],
    receipt_binding_ref: "receipt:fixture-market-only",
  });
  const role = reviewerRole();
  const envelope = buildJobEnvelope({ role, job: marketOnlyJob });
  const quals = [
    qualification({ qualification_id: "q:local", permitted_data_classes: ["market_comp"] }),
    qualification({
      qualification_id: "q:cloud", backend_key: "backend:cloud-gateway",
      permitted_data_classes: ["market_comp"], measured_latency_ms_p95: 2400,
    }),
    qualification({
      qualification_id: "q:hermes", backend_key: "backend:hermes-platform",
      model_key: "model:fixture-mid-b", effort: "standard",
      permitted_data_classes: ["market_comp"], measured_latency_ms_p95: 1500,
    }),
  ];
  const onLocal = evaluate({ job: marketOnlyJob, qualifications: quals });
  const onCloud = evaluate({
    job: marketOnlyJob, qualifications: quals, nodes: { "mac-studio": "unavailable" },
  });
  const hermesOnly = compiled(p => {
    p.routes = p.routes.filter(r => r.route_key === "route:hermes-mid" || r.route_key === "route:local-strong");
  });
  const onHermes = evaluate({
    job: marketOnlyJob, qualifications: quals, policy: hermesOnly,
    nodes: { "mac-studio": "unavailable" },
  });

  assert.equal(onLocal.selected_route.backend_kind, "local");
  assert.equal(onCloud.selected_route.backend_kind, "cloud");
  assert.equal(onHermes.selected_route.backend_kind, "hosted_open_model_platform");

  const bound = [onLocal, onCloud, onHermes]
    .map(routing_result => bindEnvelopeToRoute({ envelope, routing_result }));
  for (const boundEnvelope of bound) {
    assert.equal(boundEnvelope.binding_digest, envelope.binding_digest);
    assert.equal(boundEnvelope.job.job_id, marketOnlyJob.job_id);
    assert.equal(boundEnvelope.context_digest, CONTEXT_DIGEST);
    assert.equal(boundEnvelope.receipt_binding_ref, "receipt:fixture-market-only");
    assert.deepEqual([...boundEnvelope.capability_refs], ["capability:deal.read"]);
    assert.equal(boundEnvelope.public_product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
  }
  assert.equal(new Set(bound.map(b => b.route.backend_key)).size, 3);
  // Hermes is a backend and nothing more.
  assert.equal(onHermes.backend_grants_authority, false);
  assert.equal(onHermes.canonical_authority, V5_CANONICAL_AUTHORITY);
  assert.equal(onHermes.public_product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
});

test("an envelope refuses a job its role does not carry", () => {
  refuses(() => buildJobEnvelope({
    role: reviewerRole({ task_classes: ["task:private-compute"] }), job: dealReviewJob(),
  }), "task_class_outside_role");
  refuses(() => buildJobEnvelope({
    role: reviewerRole(),
    job: dealReviewJob({ capability_refs: ["capability:release.publish"] }),
  }), "job_exceeds_role_capabilities");
  refuses(() => buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob({ escalate: true }) }),
    "unknown_field");
});

// ---------------------------------------------------------------------------
// The trust seam: a proposal is not a qualification.
// ---------------------------------------------------------------------------

test("an unauthenticated proposal ranks identically and says it is not a route", () => {
  const proposal = proposeRoutePlan({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
    qualifications: [localQual(), cloudQual()],
  });
  const authenticated = evaluate();
  assert.equal(proposal.decision, "route");
  assert.equal(proposal.selected_route.route_key, authenticated.selected_route.route_key);
  assert.equal(proposal.qualification_authenticated, false);
  assert.equal(proposal.qualification_status, "schema_valid_proposal_only");
  assert.equal(proposal.admissible_for_dispatch, false);
  // And it cannot be carried into the adapter boundary.
  refuses(() => bindEnvelopeToRoute({
    envelope: buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() }),
    routing_result: proposal,
  }), "unauthenticated_route_refused");
});

test("the gate cannot be reached without an installed verifier", () => {
  refuses(() => createModelRoutingGate(), "authenticated_verifier_required");
  refuses(() => createModelRoutingGate({ authenticateQualifications: "trust me" }),
    "authenticated_verifier_required");
});

test("the gate must be configured with the verifier identities it accepts", () => {
  refuses(() => createModelRoutingGate({ authenticateQualifications: () => ({}) }),
    "trusted_verifier_ids_required");
  refuses(() => createModelRoutingGate({ authenticateQualifications: () => ({}), trusted_verifier_ids: [] }),
    "trusted_verifier_ids_required");
  refuses(() => createModelRoutingGate({
    authenticateQualifications: () => ({}), trusted_verifier_ids: "any verifier",
  }), "trusted_verifier_ids_required");
  assert.deepEqual([...probeGate().trusted_verifier_ids], [...TRUSTED_VERIFIER_IDS]);
});

test("a record attributed to an unconfigured verifier is undeclared authority and refuses", () => {
  refuses(() => evaluate({
    qualifications: [qualification({ verifier_id: "verifier:someone-elses-kernel" }), cloudQual()],
  }), "untrusted_verifier_id");
  // Including when the gate is configured for a different verifier entirely.
  refuses(() => gateWith([localQual()], { trusted: ["verifier:some-other-kernel"] }).evaluate({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
  }), "untrusted_verifier_id");
});

test("the verifier is synchronous, and a promise is refused rather than awaited", () => {
  const probe = probeGate();
  refuses(() => createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: async request => ({
      request_binding_digest: probe.requestBindingDigest(request),
      qualifications: [localQual()],
    }),
  }).evaluate({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
  }), "asynchronous_verifier_refused");
});

test("the verifier and the selection read the same frozen request bytes", () => {
  // A verifier that mutates the request it was handed cannot make the gate
  // select over different bytes than it bound: the gate froze a copy first, and
  // the caller's own object is left alone.
  const probe = probeGate();
  const job = dealReviewJob();
  let seen = null;
  const result = createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: request => {
      seen = request;
      assert.throws(() => { request.job.task_class = "task:private-compute"; }, TypeError);
      assert.throws(() => { request.job.data_classes.push("practice_business_profile"); }, TypeError);
      assert.throws(() => { request.local_node_states["mac-studio"] = "unavailable"; }, TypeError);
      return {
        request_binding_digest: probe.requestBindingDigest(request),
        qualifications: [localQual(), cloudQual()],
      };
    },
  }).evaluate({
    policy: compiled(), role: reviewerRole(), job, now: NOW,
    local_node_states: { "mac-studio": "available" },
  });
  assert.equal(result.decision, "route");
  assert.equal(result.selected_route.route_key, "route:local-strong");
  assert.notEqual(seen.job, job);
  assert.equal(Object.isFrozen(seen.job), true);
  assert.equal(Object.isFrozen(job), false);
  assert.equal(job.task_class, "task:deal-review");
});

test("the gate re-derives and freezes the policy and the role before the verifier sees them", () => {
  // Mutable, structurally valid clones. Both are admitted by RE-DERIVATION
  // rather than by object identity, so an ordinary caller may legitimately pass
  // one — which is exactly why the gate cannot pass them through by reference.
  const probe = probeGate();
  const callerPolicy = clone(compiled());
  const callerRole = clone(reviewerRole());
  const callerJob = dealReviewJob();
  const callerNodes = { "mac-studio": "available" };
  let seen = null;
  const result = createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: request => {
      seen = request;
      assert.throws(() => { request.role.minimum_strength_ref = "strength:draft-grade"; }, TypeError);
      assert.throws(() => { request.role.task_classes.push("task:private-compute"); }, TypeError);
      assert.throws(() => { request.policy.source.routes[0].cost_components.base_cost_units = 0; }, TypeError);
      assert.throws(() => { request.policy.strength_refs["strength:review-grade"] = "grade:fixture-draft"; },
        TypeError);
      return {
        request_binding_digest: probe.requestBindingDigest(request),
        qualifications: [localQual(), cloudQual()],
      };
    },
  }).evaluate({
    policy: callerPolicy, role: callerRole, job: callerJob, now: NOW, local_node_states: callerNodes,
  });
  assert.equal(result.decision, "route");
  assert.equal(result.selected_route.route_key, "route:local-strong");
  // All four inputs are this module's own frozen products, and none of them is
  // the caller's object.
  for (const key of ["policy", "role", "job", "local_node_states"]) {
    assert.equal(Object.isFrozen(seen[key]), true, `request.${key} was not frozen`);
  }
  assert.notEqual(seen.policy, callerPolicy);
  assert.notEqual(seen.role, callerRole);
  assert.notEqual(seen.job, callerJob);
  // ...and the caller's own objects are left exactly as they were handed over.
  assert.equal(Object.isFrozen(callerPolicy), false);
  assert.equal(Object.isFrozen(callerRole), false);
  assert.equal(Object.isFrozen(callerJob), false);
  assert.equal(callerRole.minimum_strength_ref, "strength:review-grade");
  assert.equal(callerPolicy.source.routes[0].cost_components.base_cost_units, 4);

  // And an absent key is still a `missing_field` naming that key, rather than
  // an `invalid_shape` from whichever re-derivation read it first.
  for (const key of ["policy", "role", "job", "now"]) {
    const partial = {
      policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
      local_node_states: { "mac-studio": "available" },
    };
    delete partial[key];
    const error = refuses(() => gateWith([localQual()]).evaluate(partial), "missing_field");
    assert.equal(error.detail.path, `request.${key}`);
  }
});

test("a role the verifier re-seals after binding cannot move the strength the gate selected under", () => {
  // The one privilege records alone could never reach: a record does not set a
  // floor, but a role does. The verifier here takes the binding digest, then
  // overwrites the caller's role object with a DIFFERENT, correctly re-sealed
  // role before the selection re-derives it.
  const swapMidVerification = replacement => {
    const probe = probeGate();
    const callerRole = clone(reviewerRole());
    const result = createModelRoutingGate({
      trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
      authenticateQualifications: request => {
        const request_binding_digest = probe.requestBindingDigest(request);
        Object.assign(callerRole, clone(replacement));
        return {
          request_binding_digest,
          qualifications: [localQual(), cloudQual(), weakQual()],
        };
      },
    }).evaluate({
      policy: compiled(), role: callerRole, job: dealReviewJob(), now: NOW,
      local_node_states: { "mac-studio": "available" },
    });
    return { result, callerRole };
  };

  // Downgrade: the swapped role asks for draft grade, and the cheapest, weakest
  // route in the policy has a measured record waiting for exactly that floor.
  const downgrade = swapMidVerification(reviewerRole({ minimum_strength_ref: "strength:draft-grade" }));
  assert.equal(downgrade.callerRole.minimum_strength_ref, "strength:draft-grade");
  assert.equal(downgrade.result.required_strength_ref, "strength:review-grade");
  assert.equal(downgrade.result.required_strength_grade, "grade:fixture-review");
  assert.equal(reasonFor(downgrade.result, "route:cloud-weak-cheap"), "below_required_strength");
  assert.equal(downgrade.result.selected_route.route_key, "route:local-strong");
  assert.ok(downgrade.result.selected_strength_rank >= downgrade.result.required_strength_rank);
  assert.equal(downgrade.result.downgraded_from_required_strength, false);

  // And the same in the direction this fixture makes visible at the decision
  // level: a swapped-in unreachable floor would have turned a routed answer
  // into an unavailable one, and does not.
  const raise = swapMidVerification(reviewerRole({ minimum_strength_ref: "strength:frontier-grade" }));
  assert.equal(raise.callerRole.minimum_strength_ref, "strength:frontier-grade");
  assert.equal(raise.result.decision, "route");
  assert.equal(raise.result.required_strength_ref, "strength:review-grade");
  assert.equal(raise.result.selected_route.route_key, "route:local-strong");
});

test("a caller may not hand the gate its own qualifications", () => {
  refuses(() => gateWith([localQual()]).evaluate({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" }, qualifications: [localQual()],
  }), "caller_supplied_qualifications_refused");
});

test("an authentication bound to different bytes is refused, not replayed", () => {
  const wrongDigest = gateWith([localQual()], { bindingDigest: digest({ some: "other request" }) });
  refuses(() => wrongDigest.evaluate({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
  }), "verification_binding_mismatch");

  // The same verification carried onto a SECOND job: the binding no longer
  // covers the request, so the replay refuses rather than routing job two on
  // job one's evidence.
  const probe = probeGate();
  const firstRequest = {
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
  };
  const replayed = createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: () => ({
      request_binding_digest: probe.requestBindingDigest(firstRequest),
      qualifications: [localQual(), cloudQual()],
    }),
  });
  assert.equal(replayed.evaluate(firstRequest).decision, "route");
  refuses(() => replayed.evaluate({
    ...firstRequest, job: dealReviewJob({ job_id: "job:fixture-deal-review-2" }),
  }), "verification_binding_mismatch");
});

test("a self-certified qualification is refused by name", () => {
  for (const field of ["qualified", "self_reported_quality", "model_says_qualified", "confidence"]) {
    refuses(() => evaluate({ qualifications: [qualification({ [field]: true })] }),
      "self_certified_qualification_refused");
  }
  refuses(() => evaluate({ qualifications: [qualification({ notes: "looks fine" })] }), "unknown_field");
});

test("a verifier that returns an open or malformed shape is refused", () => {
  const probe = probeGate();
  const request = {
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
  };
  refuses(() => createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: r => ({
      request_binding_digest: probe.requestBindingDigest(r), qualifications: [], verified_human: true,
    }),
  }).evaluate(request), "unknown_field");
  refuses(() => createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: r => ({
      request_binding_digest: probe.requestBindingDigest(r), qualifications: "all of them",
    }),
  }).evaluate(request), "invalid_shape");
});

// ---------------------------------------------------------------------------
// The prompt boundary.
// ---------------------------------------------------------------------------

test("a prompt payload carries content and never a secret or an authority", () => {
  const policy = compiled();
  const envelope = buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  const boundEnvelope = bindEnvelopeToRoute({ envelope, routing_result: evaluate() });
  const payload = buildPromptPayload({
    policy, bound_envelope: boundEnvelope,
    payload: {
      parts: [
        { part_id: "part:comps", data_class: "market_comp", text: "three comparable leases" },
        { part_id: "part:economics", data_class: "lease_economics", text: "base rent and escalations" },
      ],
    },
  });
  assert.equal(payload.envelope_binding_digest, envelope.binding_digest);
  assert.equal(payload.carries_secrets, false);
  assert.equal(payload.carries_authority, false);
  assert.equal(payload.capability_refs_included, false);
  assert.ok(!Object.keys(payload).includes("capability_refs"));
  assert.ok(!Object.keys(payload).includes("receipt_binding_ref"));

  for (const field of ["api_key", "capability_refs", "authority_grant", "session_token"]) {
    refuses(() => buildPromptPayload({
      policy, bound_envelope: boundEnvelope,
      payload: { parts: [{ part_id: "part:a", data_class: "market_comp", text: "x" }], [field]: "value" },
    }), "secret_or_authority_in_prompt_refused");
  }
  refuses(() => buildPromptPayload({
    policy, bound_envelope: boundEnvelope,
    payload: { parts: [{ part_id: "part:a", data_class: "practice_business_profile", text: "x" }] },
  }), "prompt_data_class_outside_job");
});

test("a prompt is built under the exact policy the route was decided under", () => {
  const envelope = buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  const boundEnvelope = bindEnvelopeToRoute({ envelope, routing_result: evaluate() });
  // The same envelope, read against a policy whose local backend has since been
  // narrowed. `compileRoutingPolicy` is public, so without this check a caller
  // could hand in a policy of their own — one whose backends permit everything —
  // and walk past the payload's data-class admission entirely.
  const tightened = compiled(p => { p.backends[0].permitted_data_classes = ["market_comp"]; });
  refuses(() => buildPromptPayload({
    policy: tightened, bound_envelope: boundEnvelope,
    payload: { parts: [{ part_id: "part:economics", data_class: "lease_economics", text: "base rent" }] },
  }), "policy_binding_mismatch");
  // A widened policy is refused by the same check, and for the same reason: the
  // routing question has to be asked again, not answered twice.
  const widened = compiled(p => {
    p.backends[1].permitted_data_classes = ["lease_economics", "market_comp", "practice_business_profile"];
  });
  refuses(() => buildPromptPayload({
    policy: widened, bound_envelope: boundEnvelope,
    payload: { parts: [{ part_id: "part:comps", data_class: "market_comp", text: "comps" }] },
  }), "policy_binding_mismatch");
  // The same bytes recompiled are the same policy, so the honest case works.
  assert.equal(buildPromptPayload({
    policy: compiled(), bound_envelope: boundEnvelope,
    payload: { parts: [{ part_id: "part:comps", data_class: "market_comp", text: "comps" }] },
  }).policy_digest, compiled().policy_digest);
  // And routing refuses the narrowing one step earlier, at admission.
  assert.equal(reasonFor(evaluate({ policy: tightened }), "route:local-strong"),
    "backend_data_policy_excludes_data_class");
});

// ---------------------------------------------------------------------------
// Provenance: the decision → envelope → prompt chain.
//
// Every preimage in this module is public and `digest` is exported, so these
// cases are the ones that matter: an object that hashes perfectly and was not
// produced here must still be refused.
// ---------------------------------------------------------------------------

test("a decision this gate did not produce cannot bind an envelope, however well it hashes", () => {
  const envelope = () => buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  const genuine = evaluate();
  assert.equal(genuine.provenance, "gate_verified_in_process");
  assert.ok(bindEnvelopeToRoute({ envelope: envelope(), routing_result: genuine }));

  // 1. A byte-for-byte copy of a genuine decision.
  refuses(() => bindEnvelopeToRoute({ envelope: envelope(), routing_result: clone(genuine) }),
    "routing_decision_not_locally_produced");
  // 2. A spread of a genuine decision with the occupant swapped for the
  //    cheapest, weakest route in the policy — the whole point of the check.
  const weakRoute = compiled().routes.find(r => r.route_key === "route:cloud-weak-cheap");
  refuses(() => bindEnvelopeToRoute({
    envelope: envelope(),
    routing_result: { ...genuine, selected_route: { ...weakRoute } },
  }), "routing_decision_not_locally_produced");
  // 3. A hand-built object carrying exactly the fields the binding reads.
  refuses(() => bindEnvelopeToRoute({
    envelope: envelope(),
    routing_result: {
      schema_version: V5_ROUTING_RESULT_SCHEMA_VERSION,
      qualification_authenticated: true,
      decision: "route",
      selected_route: { ...weakRoute },
      job_id: genuine.job_id,
      job_digest: genuine.job_digest,
      role_digest: genuine.role_digest,
      policy_digest: genuine.policy_digest,
    },
  }), "routing_decision_not_locally_produced");
  // 4. An honest unauthenticated proposal still refuses on its own status,
  //    which is the refusal that names what is actually wrong with it.
  const proposal = proposeRoutePlan({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" },
    qualifications: [localQual(), cloudQual()],
  });
  assert.equal(proposal.provenance, "public_offline_proposal_unauthenticated");
  refuses(() => bindEnvelopeToRoute({ envelope: envelope(), routing_result: proposal }),
    "unauthenticated_route_refused");
});

test("an envelope this module did not build cannot be bound", () => {
  const envelope = buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  const result = evaluate();
  refuses(() => bindEnvelopeToRoute({ envelope: clone(envelope), routing_result: result }),
    "job_envelope_not_locally_produced");
  refuses(() => bindEnvelopeToRoute({ envelope: { ...envelope }, routing_result: result }),
    "job_envelope_not_locally_produced");
  // An envelope is bound once: a bound envelope is a different object and is
  // not a candidate for a second binding.
  const bound = bindEnvelopeToRoute({ envelope, routing_result: result });
  refuses(() => bindEnvelopeToRoute({ envelope: bound, routing_result: result }),
    "job_envelope_not_locally_produced");
});

test("a decision is bound to the whole checked job, not to a job id", () => {
  const envelope = () => buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  // The same job bytes, built twice, bind: the digest is over content.
  assert.ok(bindEnvelopeToRoute({ envelope: envelope(), routing_result: evaluate() }));

  // Same job_id and same role, different admission inputs. Each of these
  // decisions routes successfully for its own job and must not be attachable to
  // the envelope for another.
  const variants = [
    dealReviewJob({ required_tool_ids: [] }),
    dealReviewJob({ data_classes: ["market_comp"] }),
    dealReviewJob({ risk_class: "risk:low" }),
    dealReviewJob({ minimum_strength_ref: "strength:architecture-grade" }),
    dealReviewJob({ intended_use: "a different stated purpose" }),
    dealReviewJob({ context_digest: digest({ fixture: "f04-context", n: 2 }) }),
    dealReviewJob({
      pinned_route: {
        model_key: "model:fixture-strong-a", model_version: "2026-01",
        effort: "high", backend_key: "backend:cloud-gateway",
      },
    }),
  ];
  for (const job of variants) {
    const decision = evaluate({ job });
    assert.equal(decision.decision, "route");
    assert.equal(decision.job_id, "job:fixture-deal-review-1");
    const error = refuses(() => bindEnvelopeToRoute({ envelope: envelope(), routing_result: decision }),
      "routing_result_binding_mismatch");
    assert.notEqual(error.detail.expected, error.detail.actual);
    // ...and it does bind to the envelope for its own job.
    assert.ok(bindEnvelopeToRoute({
      envelope: buildJobEnvelope({ role: reviewerRole(), job }), routing_result: decision,
    }));
  }
});

test("a bound envelope carries the evidence, the verifier and the policy the decision rested on", () => {
  const envelope = buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  const result = evaluate();
  const bound = bindEnvelopeToRoute({ envelope, routing_result: result });
  assert.equal(result.selected_qualification_id, "qual:fixture-local");
  assert.equal(result.selected_measurement_digest, localQual().measurement_digest);
  assert.equal(result.selected_verifier_id, "verifier:fixture-evaluation-kernel");
  assert.equal(bound.selected_measurement_digest, result.selected_measurement_digest);
  assert.equal(bound.selected_verifier_id, result.selected_verifier_id);
  assert.equal(bound.policy_digest, compiled().policy_digest);
  assert.equal(bound.job_digest, envelope.job_digest);
  assert.equal(bound.quality_scale_id, "scale:f04-fixture-grades");
  assert.equal(bound.quality_scale_version, 2);
  // An unavailable decision carries the same fields as nulls rather than
  // omitting them.
  const unavailable = evaluate({ qualifications: [] });
  assert.equal(unavailable.decision, "unavailable");
  assert.equal(unavailable.selected_measurement_digest, null);
  assert.equal(unavailable.selected_verifier_id, null);
  assert.equal(unavailable.selected_quality_grade, null);
});

test("a prompt is built only for a bound envelope this module produced", () => {
  const policy = compiled();
  const envelope = buildJobEnvelope({ role: reviewerRole(), job: dealReviewJob() });
  const bound = bindEnvelopeToRoute({ envelope, routing_result: evaluate() });
  const payload = { parts: [{ part_id: "part:comps", data_class: "market_comp", text: "comps" }] };
  assert.ok(buildPromptPayload({ policy, bound_envelope: bound, payload }));
  refuses(() => buildPromptPayload({ policy, bound_envelope: clone(bound), payload }),
    "bound_envelope_not_locally_produced");
  // The spread that re-hashes to the identical binding digest, because the
  // route deliberately lives outside that preimage.
  const swapped = { ...bound, route: { ...bound.route, backend_key: "backend:cloud-gateway" } };
  assert.equal(swapped.binding_digest, bound.binding_digest);
  refuses(() => buildPromptPayload({ policy, bound_envelope: swapped, payload }),
    "bound_envelope_not_locally_produced");
  // And a plain envelope that was never bound at all.
  refuses(() => buildPromptPayload({ policy, bound_envelope: envelope, payload }),
    "bound_envelope_not_locally_produced");
});

// ---------------------------------------------------------------------------
// Shape, immutability and the honest projection.
// ---------------------------------------------------------------------------

test("unknown fields and unreadable instants are contract violations", () => {
  refuses(() => evaluate({ job: dealReviewJob({ urgency: "high" }) }), "unknown_field");
  refuses(() => gateWith([localQual()]).evaluate({
    policy: compiled(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
    local_node_states: { "mac-studio": "available" }, hint: "prefer local",
  }), "unknown_field");
  refuses(() => evaluate({ now: "2026-02-31T00:00:00.000Z" }), "invalid_timestamp");
  refuses(() => evaluate({ now: "2026-09-09" }), "invalid_timestamp");
  refuses(() => evaluate({ nodes: { "mac-studio": "probably-fine" } }), "unknown_local_node_state");
  refuses(() => evaluate({ nodes: { "some-laptop": "available" } }), "unknown_local_node");
  refuses(() => evaluate({ qualifications: [qualification({ measurement_digest: "abc" })] }),
    "invalid_digest");
  // A policy object that was not compiled here cannot be substituted for one.
  refuses(() => gateWith([localQual()]).evaluate({
    policy: basePolicy(), role: reviewerRole(), job: dealReviewJob(), now: NOW,
  }), "policy_not_compiled");
});

test("an edited role or policy no longer hashes to its own seal", () => {
  const role = clone(reviewerRole());
  role.minimum_strength_ref = "strength:draft-grade";
  refuses(() => evaluate({ role }), "role_digest_mismatch");

  const policy = clone(compiled());
  policy.source.routes[0].cost_components.base_cost_units = 0;
  refuses(() => gateWith([localQual()]).evaluate({
    policy, role: reviewerRole(), job: dealReviewJob(), now: NOW,
  }), "policy_digest_mismatch");
});

test("every result is frozen", () => {
  const result = evaluate();
  assert.throws(() => { result.decision = "unavailable"; }, TypeError);
  assert.throws(() => { result.selected_route.model_key = "model:something-else"; }, TypeError);
  assert.throws(() => { result.considered.push({}); }, TypeError);
  assert.throws(() => { result.effects.network_calls = 1; }, TypeError);
});

test("an unregistered data class is refused by the global boundary itself", () => {
  assert.throws(() => evaluate({ job: dealReviewJob({ data_classes: ["invented_class"] }) }),
    error => error instanceof V5BoundaryError && error.code === "unknown_data_class");
});

test("the projection names what is implemented and what is missing", () => {
  const projection = v5ModelRoutingProjection();
  assert.deepEqual([...projection.decision_ids], ["Q031.D1", "Q047.D1", "Q106.D1", "Q130.D1"]);
  assert.equal(projection.cost_can_qualify_a_route, false);
  assert.equal(projection.silent_downgrade_possible_in_process, false);
  assert.equal(projection.strength_scale_is_policy_declared_and_shared, true);
  assert.equal(projection.backend_can_mint_authority, false);
  assert.equal(projection.backend_can_be_user_facing, false);
  assert.equal(projection.policy_values_invented_here, false);
  assert.equal(projection.public_product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
  // The provenance claim is scoped where it is made, not only in a comment.
  assert.equal(projection.provenance_scope, V5_PROVENANCE_SCOPE);
  assert.equal(projection.provenance_scope, "process_local_object_identity_only");
  assert.equal(projection.provenance_survives_serialization, false);
  // The gaps are stated, not simulated.
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("route-qualification.v1")));
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("V5-F06/V5-F07")));
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("migration")));
  assert.ok(projection.unimplemented_dependencies.some(entry =>
    entry.includes("signed capability token")));
  assert.throws(() => { projection.cost_can_qualify_a_route = true; }, TypeError);
});

test("the Q047 local capabilities S01 does not register are named, not widened", () => {
  const projection = v5ModelRoutingProjection();
  // Q047's settled requirement names rendering, OCR, embeddings and research.
  // S01 owns the local-capability registry and does not carry them; F04 says so
  // rather than adding them to someone else's vocabulary.
  assert.deepEqual([...projection.q047_capabilities_not_registered_in_s01],
    ["document_rendering", "ocr", "embeddings", "batch_research"]);
  for (const capability of projection.q047_capabilities_not_registered_in_s01) {
    assert.equal(Object.prototype.hasOwnProperty.call(V5_LOCAL_CAPABILITIES, capability), false);
    // A task class naming one refuses today; it is a gap, not a silent success.
    refuses(() => compiled(p => { p.task_classes[0].local_capability = capability; }),
      "unknown_local_capability");
  }
  assert.ok(projection.unimplemented_dependencies.some(entry =>
    entry.includes("S01 local-capability registry")));
});
