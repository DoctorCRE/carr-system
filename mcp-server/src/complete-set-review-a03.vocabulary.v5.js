// DoctorCRE v5 slice V5-A03 — the closed vocabularies of independent
// complete-set review and bounded adjudication.
//
// These sit in their own file for one reason: the public unavailable surface
// and the test-only classifiers both name them, and a vocabulary defined twice
// is two vocabularies that agree until the day they do not. Putting them here
// also keeps the import graph acyclic — this file imports nothing, the public
// module imports this, and the test-only classifier entry imports both. The
// public module does NOT import the classifiers, and nothing in mcp-server/src
// may: that is proved by a parser-backed scan, not by this comment.
//
// EVERY LIST HERE IS DERIVED FROM SETTLED DECISION TEXT, not from taste, and
// the deriving sentence is quoted beside it. Each one is in the A03 policy
// preimage, so changing a list moves the policy digest and a pinned consumer is
// refused rather than quietly reading a policy that now decides differently.
//
// Nothing in this file is an authorization. A vocabulary says which words the
// module can read; it never says yes.
//
// AND NO VALUE IN THIS FILE IS A PRIVILEGED OR OUTCOME WORD, under any name.
// Neither the exported values nor the export names contain `ok`, `allow`,
// `pass`, `satisfied`, `complete`, `admitted`, `favorable`, `unfavorable`,
// `adverse`, `isolating`, `quarantine` or `fail` — as a whole string or as a
// substring — because "it is only a vocabulary" is a distinction a consumer
// reading the bytes cannot make. Where a settled sentence uses one of those
// words, the word stays in a COMMENT, quoted, where it belongs. The suite
// proves this per word, over every export of both src modules, with no
// exemptions.
//
// THE ADJUDICATION OUTCOME VOCABULARY IS NOT HERE AT ALL, and that is the third
// correction of PR 987. It lived here first as the bare words "fail", "pass" and
// "quarantine", then as codes named `adverse`, `favorable` and `isolating` that
// published the same three dispositions under new spelling — renaming is not
// opacity, and an object key a consumer can read is a payload. A disposition is
// the one thing in this slice only an authority may say, so the closed set of
// three codes is module-private to the public module, no src module exports it,
// and only its canonical digest is public, for a future receipt store to bind
// to. The test side reaches the codes through the classifier test helper, which
// production cannot import.

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    Object.values(value).forEach(deepFreeze);
    return Object.freeze(value);
  }
  return value;
}

export const V5_A03_SCHEMA_VERSION = "doctorcre-v5-exhaustive-set-review.v1";
export const V5_A03_POLICY_VERSION = 1;

/**
 * The eleven review dimensions, C-sorted.
 *
 * Q154.D1, verbatim: "Run separate exhaustive architecture, sequencing,
 * repository, migration, context, security, business, product, cost,
 * resilience, and operations reviews". The catalog's included_scope repeats the
 * same eleven in the same order. The list is CLOSED: a twelfth dimension is a
 * contract violation, not a policy question, because a reviewer set nobody
 * accepted is not the complete set Q154 settled.
 */
export const V5_REVIEW_DIMENSIONS = deepFreeze([
  "architecture", "business", "context", "cost", "migration", "operations",
  "product", "repository", "resilience", "security", "sequencing",
]);

/**
 * The six duties Q107.D1 separates, plus the adjudicator Q042.D1 adds.
 *
 * Q107.D1, verbatim: "Separate architect, builder, reviewer, integration,
 * deployment, and program-control duties for the same change; no role may
 * certify or weaken its own work." Q042.D1 step 4 adds the "stronger
 * adjudicator". `reviewer` is here for completeness of the vocabulary; the
 * reviewer duty is held per-dimension by the routing assignments, not by one
 * named identity, which is what "separate exhaustive reviews" means.
 */
export const V5_REVIEW_ROLES = deepFreeze([
  "adjudicator", "architect", "builder", "deployment_controller",
  "integration_controller", "program_controller", "reviewer",
]);

/** Every role except `reviewer`: the duties a routing request names outright. */
export const V5_NON_REVIEWER_ROLES = deepFreeze(
  V5_REVIEW_ROLES.filter(role => role !== "reviewer"));

/**
 * The opposing pairs — the ones one identity may never hold at once FOR THE
 * SAME CHANGE. Q107.D1's recommendation is explicit that this is narrower than
 * "all six differ": "One model may occupy different roles at different times,
 * but never opposing roles for the same change."
 *
 * Each pair is derived from a named clause, and no pair is here without one:
 *
 *   architect / reviewer              "Architect may design but does not
 *                                      certify its own design" and "Reviewer
 *                                      cannot weaken acceptance criteria" —
 *                                      the reviewer certifies the design and
 *                                      is bound by criteria the architect set.
 *   builder / reviewer                "Builder cannot review its own
 *                                      implementation."
 *   builder / integration_controller  "Integration Controller verifies
 *                                      freshness and merge eligibility" — of
 *                                      the builder's change.
 *   builder / deployment_controller   "Deployment Controller promotes only the
 *                                      exact verified artifact" — a builder
 *                                      promoting its own build is the
 *                                      substitution that clause forbids.
 *   builder / program_controller      "Program Controller advances the graph
 *                                      but does not fabricate evidence" — the
 *                                      producer of the evidence may not be the
 *                                      one who decides it is enough.
 *   program_controller / reviewer     Same clause, reviewer side.
 *   deployment_controller / reviewer  The A03 checkable_done clause, verbatim:
 *                                      "maker/reviewer/releaser/session
 *                                      identities differ".
 *   adjudicator / builder             Q042.D1 step 4: the adjudicator settles a
 *   adjudicator / reviewer             disagreement BETWEEN these two, so it
 *                                      cannot be either of them.
 *
 * Pairs deliberately ABSENT, so their absence is a decision and not an
 * oversight: architect/builder (designing and building the same change is the
 * ordinary case and no settled clause separates them),
 * integration_controller/deployment_controller, and any pair involving only
 * controllers. Nothing in the settled text opposes those, and inventing a
 * separation nobody accepted would make honest routings refuse.
 */
export const V5_OPPOSING_ROLE_PAIRS = deepFreeze([
  ["adjudicator", "builder"],
  ["adjudicator", "reviewer"],
  ["architect", "reviewer"],
  ["builder", "deployment_controller"],
  ["builder", "integration_controller"],
  ["builder", "program_controller"],
  ["builder", "reviewer"],
  ["deployment_controller", "reviewer"],
  ["program_controller", "reviewer"],
]);

/** The one adjudicator role Q042.D1 names: a STRONGER judge, not another peer. */
export const V5_ADJUDICATOR_ROLE = "stronger_adjudicator";

/**
 * Q042.D1, verbatim: "allow at most two full rounds, then stronger adjudication
 * and pass, fail, or quarantine without endless spirals."
 */
export const V5_MAX_REVIEW_ROUNDS = 2;

/** What a review round's ordinal obliges next. */
export const V5_ROUND_TRANSITIONS = deepFreeze([
  "independent_review_round", "stronger_adjudication",
]);

/**
 * Q042.D1's closing sentence, verbatim: "The system must detect repeated
 * findings, circular reversions, reviewer instability, and 'fixes' that merely
 * weaken tests." Four named drifts, four detectors, C-sorted.
 */
export const V5_ROUND_REGRESSION_CLASSES = deepFreeze([
  "circular_reversion", "repeated_finding", "reviewer_instability", "test_weakening",
]);

/**
 * A reviewer's answer for one dimension in one round.
 *
 * The second answer used to be spelled `passed`, which carried the privileged
 * word into every payload that recited this vocabulary. It is spelled as the
 * negation of the first answer instead: a reviewer requiring no changes is the
 * only thing this vocabulary was ever entitled to say, and saying it that way
 * leaves no word here a consumer could mistake for a clearance.
 */
export const V5_REVIEW_STATES = deepFreeze(["changes_required", "no_changes_required"]);

/** Whether a reviewing session inherited the maker's context or started clean. */
export const V5_CONTEXT_BINDINGS = deepFreeze(["fresh", "inherited_from_maker"]);

/** Whether a dimension's finding set arrived at all. */
export const V5_SUBMISSION_STATES = deepFreeze(["absent", "submitted"]);

/** The one receipt kind this slice knows how to read the shape of. */
export const V5_ADJUDICATION_RECEIPT_KIND = "adjudication";

/**
 * WHICH ROLE A MODEL MAY OCCUPY. The catalog's model_judgment_boundary:
 * "Models perform qualified independent review; deterministic provenance and
 * round limits prevent self-approval or drift." So a model may produce review
 * CONTENT, and nothing else in this slice: routing, separation, the round bound
 * and adjudication admission are deterministic and take no model output.
 */
export const V5_MODEL_PERMITTED_ROLES = deepFreeze(["reviewer"]);

/** The four decisions deterministic code owns outright in this slice. */
export const V5_DETERMINISTIC_ONLY_DECISIONS = deepFreeze([
  "adjudication_admission", "finding_set_exhaustiveness", "review_round_bound", "review_routing",
]);

// ---------------------------------------------------------------------------
// The clause orders. Each list is the order the TEST-ONLY classifier for that
// decision runs its clauses in, and the FIRST clause that blocks is the
// classifier's answer. The public surface runs NONE of them: it reads no
// request, so it reports the whole order as defined and none of it as
// evaluated. The store/registry/ledger clause is LAST in every list because a
// classifier reading a self-inconsistent description should name that rather
// than the missing seam.
// ---------------------------------------------------------------------------

export const V5_ROUTING_CHECKS = deepFreeze([
  "dimension_coverage", "reviewer_role_separation", "duty_role_separation", "fresh_context",
  "reviewer_identity_registry",
]);

export const V5_FINDING_SET_CHECKS = deepFreeze([
  "dimension_submission", "delivered_set_scope", "enumeration_before_repair",
  "exhaustive_finding_set_registry",
]);

export const V5_ROUND_BOUND_CHECKS = deepFreeze([
  "round_reopened_after_adjudication", "round_limit", "round_drift", "prior_round_batch_regression",
  "review_round_ledger",
]);

export const V5_ADJUDICATION_CHECKS = deepFreeze([
  "adjudicator_role", "adjudicator_separation", "rounds_before_adjudication", "disputed_set_empty",
  "bounded_adjudication_receipt_store",
]);

// ---------------------------------------------------------------------------
// THE SEAMS. Each is a NAME, not a port. No evaluator in this slice accepts a
// holder as an argument and none exports a way to bind one, so a caller cannot
// become the authority for its own case by passing an object that looks like
// the store.
// ---------------------------------------------------------------------------

export const V5_REVIEWER_IDENTITY_REGISTRY_SEAM = "seam:independent-reviewer-identity-registry";
export const V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM = "seam:exhaustive-finding-set-registry";
export const V5_REVIEW_ROUND_LEDGER_SEAM = "seam:review-round-ledger";
export const V5_ADJUDICATION_RECEIPT_STORE_SEAM = "seam:bounded-adjudication-receipt-store";

/** Every seam this slice is owed, C-sorted, and which clause reads each. */
export const V5_A03_SEAMS = deepFreeze({
  bounded_adjudication_receipt_store: V5_ADJUDICATION_RECEIPT_STORE_SEAM,
  exhaustive_finding_set_registry: V5_EXHAUSTIVE_FINDING_SET_REGISTRY_SEAM,
  review_round_ledger: V5_REVIEW_ROUND_LEDGER_SEAM,
  reviewer_identity_registry: V5_REVIEWER_IDENTITY_REGISTRY_SEAM,
});
