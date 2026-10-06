// V5-F07 phase 1 — the four-axis command-version compatibility comparator.
//
// Q058.D1 settles that clients, workers and deployments report compatible CODE,
// SCHEMA, COMMAND-CONTRACT and POLICY versions, and that anything incompatible
// fails closed and stays visible. This file is that comparison and nothing else:
// a pure function from one typed client declaration plus one deployment
// observation to a frozen answer.
//
// ALL FOUR AXES ARE REQUIRED, ALWAYS. There is no enforced/deferred split and no
// caller-supplied axis list — `enforced_axes` is an unknown field, so a request
// that tries to name which axes count cannot be read at all. An axis that is
// undeclared, unobservable, mismatched, or observed in a non-current policy epoch
// blocks; only four KNOWN and MATCHING axes allow.
//
// WHAT IT COMPARES, AND WHY EXACTLY. Matching is exact on the digest, and on the
// label when both sides report one. A registry that keeps its version string
// while its digest changes is a different command contract; a migration number
// that stays put while the ledger hash moves is a different schema. Commit
// distance and caller-declared ancestry are not inputs — they are not "read and
// ignored", they are unreadable fields — so nothing can outvote a digest.
//
// WHERE POLICY COMES FROM. /release cannot answer the policy axis.
// `doctrine_generation` is a doctrine row counter, not a policy epoch, and
// reading it as one is exactly the "a signal that happens to correlate" mistake
// release.js was built to stop. So the deployment's policy axis is UNOBSERVABLE
// unless a trusted policy-epoch observation is supplied beside the payload, and
// its state is read through policy-epoch.js's own normalizer rather than a second
// implementation of those semantics. That observation must BIND every dimension
// the payload reports AND name its tenant — the one dimension /release cannot
// report, which is why the evidence has to supply it — and the scope it names
// travels with the axis, so an epoch belonging to another tenant is compared
// against the client rather than silently dropped.
//
// WHAT THIS IS NOT. Not authentication, not admission, not dispatch. Every
// result carries authenticated:false, authorizes_command_dispatch:false and
// runtime_admission_granted:false, because a public JSON payload is evidence a
// reader may look at and never a credential. An `allow` here says the four
// reported versions match — it does not admit a caller to anything.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { normalizePolicyEpochStatus } from "./policy-epoch.js";

export const V5_COMMAND_VERSION_SCHEMA_VERSION = "doctorcre-v5-command-version-compatibility.v1";
export const V5_COMMAND_VERSION_POLICY_VERSION = 1;

/** Q058's four words, closed, in the order the decision names them. */
export const V5_VERSION_AXES = Object.freeze(["code", "schema", "command_contract", "policy"]);

export const V5_AXIS_STATES = Object.freeze([
  "match", "mismatch", "client_undeclared", "deployment_unobservable", "policy_epoch_incompatible",
]);

/**
 * The only sources a policy-epoch observation may claim. A payload read off a
 * public endpoint is never one of them: `/release` is unauthenticated, so an
 * observation attributed to it is a claim about policy, not an observation of it.
 */
export const V5_TRUSTED_POLICY_EPOCH_SOURCES = Object.freeze([
  "operator_supplied_trusted_epoch_report",
]);

/** Where a deployment-side policy-epoch observation would come from. Not built here. */
export const V5_POLICY_OBSERVATION_SEAM = "step:v5-f07-gateway-policy-epoch-observation";

/**
 * release.js's word for a deployment that never declared what it is. It is a
 * statement of absence, so it never names an environment ANYWHERE here — not in
 * a payload, a client declaration, a policy evidence scope, or a hand-built
 * report. Two sides both saying "unknown" are not in the same environment.
 */
export const V5_UNKNOWN_ENVIRONMENT_SENTINEL = "unknown";

export const V5_COMMAND_VERSION_REASON_IDS = Object.freeze([
  "all_four_axes_known_and_matched",
  "client_axis_undeclared",
  "code_version_mismatch",
  "command_contract_registry_mismatch",
  "deployment_axis_unobservable",
  "deployment_environment_unobservable",
  "evidence_scope_mismatch",
  "policy_epoch_incompatible",
  "policy_epoch_mismatch",
  "schema_ledger_mismatch",
]);

const AXIS_MISMATCH_REASON = Object.freeze({
  code: "code_version_mismatch",
  schema: "schema_ledger_mismatch",
  command_contract: "command_contract_registry_mismatch",
  policy: "policy_epoch_mismatch",
});

// A 40-hex revision, a 64-hex registry digest, and either with the `sha256:`
// prefix the schema ledger and the policy registry use. Spellings are NOT
// normalized into one another: comparison is byte equality, so a client must
// declare a digest exactly as its deployment reports it.
const DIGEST_TOKEN = /^(?:sha256:)?(?:[0-9a-f]{40}|[0-9a-f]{64})$/;

const CLIENT_KEYS = Object.freeze(["axes", "deployment_ref", "environment", "tenant"]);
const CLIENT_AXIS_KEYS = Object.freeze(["digest", "label"]);
const POLICY_OBSERVATION_KEYS = Object.freeze([
  "deployment_ref", "environment", "source", "status", "tenant",
]);

// The exact shape of each normalized report, re-checked at the evaluator door.
const CLIENT_REPORT_KEYS = Object.freeze([
  "axes", "deployment_ref", "environment", "report_kind", "schema_version", "tenant",
]);
const CLIENT_AXIS_ENTRY_KEYS = Object.freeze(["declared", "digest", "label", "reason"]);
const DEPLOYMENT_REPORT_KEYS = Object.freeze([
  "axes", "deployment_ref", "environment", "environment_reason", "report_kind", "schema_version",
  "source", "tenant",
]);
const DEPLOYMENT_AXIS_ENTRY_KEYS = Object.freeze(["digest", "epoch_state", "label", "reason", "state"]);
const POLICY_AXIS_ENTRY_KEYS = Object.freeze([...DEPLOYMENT_AXIS_ENTRY_KEYS, "evidence_scope"]);
const EVIDENCE_SCOPE_KEYS = Object.freeze(["deployment_ref", "environment", "tenant"]);
const NON_CURRENT_EPOCH_STATES = Object.freeze(["stale", "future", "rolled_back"]);

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

/** An open schema is an unenforced one; an unread field could be a smuggled control. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertNonEmptyString(value, path) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  return value;
}

function assertDigestToken(value, path) {
  if (typeof value !== "string" || !DIGEST_TOKEN.test(value)) {
    fail("invalid_digest", `${path} must be a lower-case hex revision or sha256 digest`, { path });
  }
  return value;
}

function optionalString(value, path) {
  return value === undefined || value === null ? null : assertNonEmptyString(value, path);
}

// ---------------------------------------------------------------------------
// The client declaration.
//
// A client says which four versions it was built against, and for which tenant
// and environment it is asking. Environment is required because "am I
// compatible?" is a question about one deployment: an answer computed from a
// staging readback is not an answer about production.
// ---------------------------------------------------------------------------

function undeclaredAxis(axis) {
  return { declared: false, label: null, digest: null, reason: `client declared no ${axis} version` };
}

export function normalizeClientVersionDeclaration(declaration) {
  assertObject(declaration, "declaration");
  assertClosedKeys(declaration, CLIENT_KEYS, "declaration");
  assertRequiredKeys(declaration, ["axes", "environment", "tenant"], "declaration");
  if (declaration.environment === V5_UNKNOWN_ENVIRONMENT_SENTINEL) {
    fail("unknown_environment_sentinel",
      `declaration.environment must name an environment; "${V5_UNKNOWN_ENVIRONMENT_SENTINEL}" is the word for`
      + " a deployment that never declared one, and two of them are not the same environment",
      { path: "declaration.environment" });
  }
  const axesInput = assertObject(declaration.axes, "declaration.axes");
  for (const key of Object.keys(axesInput)) {
    if (!V5_VERSION_AXES.includes(key)) {
      fail("unknown_version_axis", `"${key}" is not one of the four registered version axes`,
        { axis: key, registered: [...V5_VERSION_AXES] });
    }
  }
  const axes = {};
  for (const axis of V5_VERSION_AXES) {
    const entry = axesInput[axis];
    // An absent or explicitly null axis is a policy answer, not a contract
    // violation: "I cannot say" is a thing a client is allowed to report, and
    // it refuses. A malformed one is unreadable and throws.
    if (entry === undefined || entry === null) { axes[axis] = undeclaredAxis(axis); continue; }
    assertObject(entry, `declaration.axes.${axis}`);
    assertClosedKeys(entry, CLIENT_AXIS_KEYS, `declaration.axes.${axis}`);
    assertRequiredKeys(entry, ["digest"], `declaration.axes.${axis}`);
    axes[axis] = {
      declared: true,
      label: optionalString(entry.label, `declaration.axes.${axis}.label`),
      digest: assertDigestToken(entry.digest, `declaration.axes.${axis}.digest`),
      reason: null,
    };
  }
  return deepFreeze({
    report_kind: "client_declaration",
    schema_version: V5_COMMAND_VERSION_SCHEMA_VERSION,
    tenant: assertNonEmptyString(declaration.tenant, "declaration.tenant"),
    environment: assertNonEmptyString(declaration.environment, "declaration.environment"),
    deployment_ref: optionalString(declaration.deployment_ref, "declaration.deployment_ref"),
    axes,
  });
}

// ---------------------------------------------------------------------------
// The deployment observation, adapted from the real /release payload.
//
// The payload is READ FIELD BY FIELD and never key-closed: /release grows fields
// over time and every existing consumer reads it the same way, so an older or
// newer payload must not make this adapter throw. What an older payload cannot
// answer becomes an unobservable axis carrying the payload's own reason string —
// visible, and blocking.
// ---------------------------------------------------------------------------

function observedAxis(label, axisDigest, extra = {}) {
  return { state: "observed", label: label ?? null, digest: axisDigest, reason: null, epoch_state: null, ...extra };
}

function unobservableAxis(reason) {
  return { state: "unobservable", label: null, digest: null, reason, epoch_state: null };
}

/** The policy axis alone carries the scope of the evidence it was built from. */
function policyUnobservable(reason, evidenceScope) {
  return { ...unobservableAxis(reason), evidence_scope: evidenceScope };
}

function readDigestField(value) {
  return typeof value === "string" && DIGEST_TOKEN.test(value) ? value : null;
}

function codeAxisFromRelease(release) {
  const value = release.git_sha && typeof release.git_sha === "object" ? release.git_sha.value : null;
  const observed = readDigestField(value);
  if (observed) return observedAxis(null, observed);
  const reason = release.git_sha && typeof release.git_sha.reason === "string" && release.git_sha.reason
    ? release.git_sha.reason
    : "release payload reports no readable code revision";
  return unobservableAxis(reason);
}

function schemaAxisFromRelease(release) {
  const schema = release.schema && typeof release.schema === "object" ? release.schema : null;
  const observed = schema ? readDigestField(schema.ledger_sha256) : null;
  if (observed) {
    return observedAxis(
      typeof schema.highest_applied_migration === "string" ? schema.highest_applied_migration : null,
      observed,
    );
  }
  const reason = schema && typeof schema.reason === "string" && schema.reason
    ? schema.reason
    : "release payload reports no schema ledger digest";
  return unobservableAxis(reason);
}

function commandContractAxisFromRelease(release) {
  const contract = release.command_contract && typeof release.command_contract === "object"
    ? release.command_contract
    : null;
  if (!contract) {
    return unobservableAxis(
      "release payload carries no command_contract identity; this deployment predates command-contract reporting");
  }
  const observed = readDigestField(contract.registry_digest);
  if (!observed) return unobservableAxis("release payload reports no readable command-contract registry digest");
  return observedAxis(
    typeof contract.registry_version === "string" ? contract.registry_version : null, observed);
}

/**
 * The policy axis, which /release cannot answer on its own.
 *
 * ITS EVIDENCE SCOPE IS KEPT, NOT SPENT AND DISCARDED. Whatever tenant,
 * environment and worker version the observation names travels on the axis as
 * `evidence_scope`, because the release payload cannot check every dimension:
 * /release carries no tenant, so an epoch report for a DIFFERENT tenant conflicts
 * with nothing here and would otherwise disappear. The comparison checks it
 * against the client's own expectations.
 *
 * AND IT MUST BIND WHAT THIS DEPLOYMENT DOES REPORT, PLUS THE TENANT IT DOES NOT.
 * An epoch report that names no environment or worker version, when the payload
 * names both, has not established that it describes this deployment. And one that
 * names no tenant is bound to no tenant at all — there is no payload field to
 * fall back on — so it cannot be evidence about the tenant being asked about.
 * Either way it observes nothing.
 *
 * Contents are read by policy-epoch.js's own normalizer, so a malformed status
 * surfaces PolicyEpochRefusal unchanged rather than being reinterpreted here.
 */
function policyAxisFromObservation(observation, scope) {
  if (observation === undefined || observation === null) {
    return policyUnobservable(
      "no trusted policy-epoch observation was supplied; doctrine_generation is a doctrine row "
      + `counter and does not establish policy epoch state (${V5_POLICY_OBSERVATION_SEAM})`, null);
  }
  assertObject(observation, "options.policy_observation");
  assertClosedKeys(observation, POLICY_OBSERVATION_KEYS, "options.policy_observation");
  assertRequiredKeys(observation, ["source", "status"], "options.policy_observation");
  const evidenceScope = {
    tenant: optionalString(observation.tenant, "options.policy_observation.tenant"),
    environment: optionalString(observation.environment, "options.policy_observation.environment"),
    deployment_ref: optionalString(observation.deployment_ref, "options.policy_observation.deployment_ref"),
  };
  if (!V5_TRUSTED_POLICY_EPOCH_SOURCES.includes(observation.source)) {
    return policyUnobservable(
      `policy-epoch observation source ${JSON.stringify(observation.source)} is not a trusted epoch source`,
      evidenceScope);
  }
  if (evidenceScope.environment === V5_UNKNOWN_ENVIRONMENT_SENTINEL) {
    return policyUnobservable(
      `policy-epoch observation environment is "${V5_UNKNOWN_ENVIRONMENT_SENTINEL}", which names no environment`,
      evidenceScope);
  }
  for (const field of EVIDENCE_SCOPE_KEYS) {
    const declared = evidenceScope[field];
    if (declared !== null && scope[field] !== null && declared !== scope[field]) {
      return policyUnobservable(
        `policy-epoch observation ${field} "${declared}" is not this deployment's ${field} "${scope[field]}"`,
        evidenceScope);
    }
    // The tenant is required even though the payload can never report one — that
    // is exactly why. An epoch report naming no tenant is not bound to any, so it
    // cannot be the tenant the client is asking about; it observes nothing.
    if (declared === null && field === "tenant") {
      return policyUnobservable(
        "policy-epoch observation names no tenant, and the release payload cannot report one, "
        + "so nothing binds this epoch to a tenant", evidenceScope);
    }
    if (declared === null && scope[field] !== null) {
      return policyUnobservable(
        `policy-epoch observation does not bind this deployment's ${field} "${scope[field]}"`, evidenceScope);
    }
  }
  const status = normalizePolicyEpochStatus(observation.status);
  if (status.epoch_state !== "current" || status.compatibility_state !== "compatible") {
    return {
      state: "policy_epoch_incompatible",
      label: status.registry_version,
      digest: status.registry_digest,
      reason: `deployment policy epoch is ${status.epoch_state}/${status.compatibility_state}`,
      epoch_state: status.epoch_state,
      evidence_scope: evidenceScope,
    };
  }
  if (!readDigestField(status.registry_digest)) {
    return policyUnobservable("policy-epoch status carries no policy registry digest", evidenceScope);
  }
  return {
    ...observedAxis(status.registry_version, status.registry_digest, { epoch_state: status.epoch_state }),
    evidence_scope: evidenceScope,
  };
}

export function deploymentVersionReportFromRelease(release, options = {}) {
  assertObject(release, "release");
  assertObject(options, "options");
  assertClosedKeys(options, ["policy_observation"], "options");

  // "unknown" is release.js's own word for an unlabelled deployment, and it is
  // preserved as such: an environment nobody declared is not an environment.
  const environmentValue = release.env && typeof release.env === "object" ? release.env.value : null;
  const environment = typeof environmentValue === "string" && environmentValue.length > 0
    && environmentValue !== V5_UNKNOWN_ENVIRONMENT_SENTINEL ? environmentValue : null;
  const environmentReason = environment
    ? null
    : (release.env && typeof release.env.reason === "string" && release.env.reason
      ? release.env.reason
      : "release payload reports no environment label");
  const workerVersionId = release.worker_version && typeof release.worker_version === "object"
    ? release.worker_version.id : null;
  const scope = {
    // /release carries no tenant field, so tenant is unreported rather than
    // assumed; it is compared only when both sides state one.
    tenant: null,
    environment,
    deployment_ref: typeof workerVersionId === "string" && workerVersionId ? workerVersionId : null,
  };

  return deepFreeze({
    report_kind: "deployment_observation",
    schema_version: V5_COMMAND_VERSION_SCHEMA_VERSION,
    source: "release_payload",
    tenant: scope.tenant,
    environment: scope.environment,
    environment_reason: environmentReason,
    deployment_ref: scope.deployment_ref,
    axes: {
      code: codeAxisFromRelease(release),
      schema: schemaAxisFromRelease(release),
      command_contract: commandContractAxisFromRelease(release),
      policy: policyAxisFromObservation(options.policy_observation, scope),
    },
  });
}

// ---------------------------------------------------------------------------
// The comparison.
// ---------------------------------------------------------------------------

// A REPORT IS REVALIDATED IN FULL, NOT RECOGNIZED BY ITS MARKER. The marker says
// which normalizer a report claims to come from; it proves nothing, because
// anything can be written by hand or round-tripped through JSON and edited. So
// the whole normalized schema is re-checked here — closed keys at every level,
// and the semantic consistency each state implies. An observed policy axis whose
// epoch_state is not `current` is not a compatibility question: the normalizer
// only reaches `observed` through policy-epoch.js's own refusal, so such a report
// is incoherent and cannot be read at all.

function badReport(path, message) {
  fail("unnormalized_report", message, { path });
}

function reportString(value, path, { nullable = false } = {}) {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || value.length === 0) badReport(path, `${path} must be a non-empty string`);
  return value;
}

function reportDigest(value, path) {
  if (typeof value !== "string" || !DIGEST_TOKEN.test(value)) {
    badReport(path, `${path} must be a readable digest`);
  }
  return value;
}

/** The unlabelled sentinel is never a known environment, however it arrives. */
function assertNotUnknownEnvironment(value, path) {
  if (value === V5_UNKNOWN_ENVIRONMENT_SENTINEL) {
    badReport(path, `${path} is "${V5_UNKNOWN_ENVIRONMENT_SENTINEL}", which is the absence of an environment`
      + " and must be normalized to null with a reason, never counted as a known one");
  }
  return value;
}

/** Exactly these keys, no more and no fewer, at every level of a report. */
function assertReportShape(value, keys, path) {
  if (!isPlainObject(value)) badReport(path, `${path} must be a plain object`);
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) badReport(`${path}.${key}`, `unknown field "${key}" at ${path}`);
  }
  for (const key of keys) {
    if (!(key in value)) badReport(`${path}.${key}`, `${path}.${key} is missing from the normalized report`);
  }
  return value;
}

function assertNormalizedClientDeclaration(value, path) {
  assertReportShape(value, CLIENT_REPORT_KEYS, path);
  if (value.report_kind !== "client_declaration" ||
      value.schema_version !== V5_COMMAND_VERSION_SCHEMA_VERSION) {
    badReport(path, `${path} must be produced by normalizeClientVersionDeclaration`);
  }
  reportString(value.tenant, `${path}.tenant`);
  reportString(value.environment, `${path}.environment`);
  assertNotUnknownEnvironment(value.environment, `${path}.environment`);
  reportString(value.deployment_ref, `${path}.deployment_ref`, { nullable: true });
  assertReportShape(value.axes, V5_VERSION_AXES, `${path}.axes`);
  for (const axis of V5_VERSION_AXES) {
    const at = `${path}.axes.${axis}`;
    const entry = assertReportShape(value.axes[axis], CLIENT_AXIS_ENTRY_KEYS, at);
    if (typeof entry.declared !== "boolean") badReport(`${at}.declared`, `${at}.declared must be a boolean`);
    if (entry.declared) {
      reportDigest(entry.digest, `${at}.digest`);
      reportString(entry.label, `${at}.label`, { nullable: true });
      if (entry.reason !== null) badReport(`${at}.reason`, `${at} is declared and carries no reason`);
    } else {
      if (entry.digest !== null || entry.label !== null) {
        badReport(at, `${at} is undeclared and cannot carry a version`);
      }
      reportString(entry.reason, `${at}.reason`);
    }
  }
  return value;
}

function assertNormalizedDeploymentObservation(value, path) {
  assertReportShape(value, DEPLOYMENT_REPORT_KEYS, path);
  if (value.report_kind !== "deployment_observation" ||
      value.schema_version !== V5_COMMAND_VERSION_SCHEMA_VERSION ||
      value.source !== "release_payload") {
    badReport(path, `${path} must be produced by deploymentVersionReportFromRelease`);
  }
  reportString(value.tenant, `${path}.tenant`, { nullable: true });
  reportString(value.environment, `${path}.environment`, { nullable: true });
  assertNotUnknownEnvironment(value.environment, `${path}.environment`);
  reportString(value.deployment_ref, `${path}.deployment_ref`, { nullable: true });
  // An environment is either known, or unknown WITH the payload's reason. A
  // missing or emptied reason must not read as a known environment.
  if (value.environment === null) reportString(value.environment_reason, `${path}.environment_reason`);
  else if (value.environment_reason !== null) {
    badReport(`${path}.environment_reason`, `${path}.environment_reason must be null for a known environment`);
  }
  assertReportShape(value.axes, V5_VERSION_AXES, `${path}.axes`);
  for (const axis of V5_VERSION_AXES) {
    const at = `${path}.axes.${axis}`;
    const isPolicy = axis === "policy";
    const entry = assertReportShape(value.axes[axis],
      isPolicy ? POLICY_AXIS_ENTRY_KEYS : DEPLOYMENT_AXIS_ENTRY_KEYS, at);
    if (entry.state === "observed") {
      reportDigest(entry.digest, `${at}.digest`);
      reportString(entry.label, `${at}.label`, { nullable: true });
      if (entry.reason !== null) badReport(`${at}.reason`, `${at} was observed and carries no reason`);
      // The reproduction: an observed policy axis is only reachable through a
      // current, compatible epoch, so any other epoch_state is incoherent.
      if (isPolicy ? entry.epoch_state !== "current" : entry.epoch_state !== null) {
        badReport(`${at}.epoch_state`,
          `${at} was observed and cannot carry epoch_state ${JSON.stringify(entry.epoch_state ?? null)}`);
      }
    } else if (entry.state === "unobservable") {
      if (entry.digest !== null || entry.label !== null) {
        badReport(at, `${at} is unobservable and cannot carry a version`);
      }
      if (entry.epoch_state !== null) badReport(`${at}.epoch_state`, `${at} is unobservable`);
      reportString(entry.reason, `${at}.reason`);
    } else if (entry.state === "policy_epoch_incompatible") {
      if (!isPolicy) badReport(`${at}.state`, `${at} is not the policy axis`);
      if (entry.epoch_state !== null && !NON_CURRENT_EPOCH_STATES.includes(entry.epoch_state)) {
        badReport(`${at}.epoch_state`, `${at} must name a non-current epoch state`);
      }
      if (entry.digest !== null) reportDigest(entry.digest, `${at}.digest`);
      reportString(entry.reason, `${at}.reason`);
    } else {
      badReport(`${at}.state`, `${at}.state is not a normalized deployment axis state`);
    }
  }
  assertPolicyEvidenceScope(value, path);
  return value;
}

/**
 * The policy axis's evidence scope: present whenever the axis says anything
 * about an epoch, closed, and bound to every dimension this deployment reports.
 */
function assertPolicyEvidenceScope(report, path) {
  const policy = report.axes.policy;
  const at = `${path}.axes.policy.evidence_scope`;
  if (policy.evidence_scope === null) {
    if (policy.state !== "unobservable") {
      badReport(at, `${at} is required once the policy axis reports an epoch`);
    }
    return;
  }
  const scope = assertReportShape(policy.evidence_scope, EVIDENCE_SCOPE_KEYS, at);
  for (const field of EVIDENCE_SCOPE_KEYS) {
    reportString(scope[field], `${at}.${field}`, { nullable: true });
  }
  // An axis that observed NOTHING keeps the scope it was handed, sentinel and
  // all: that scope is why it refused, and the refusal has to stay readable.
  // Rejecting the report here would turn this module's own honest answer into an
  // unreadable input.
  if (policy.state === "unobservable") return;
  assertNotUnknownEnvironment(scope.environment, `${at}.environment`);
  // The tenant the payload cannot report: evidence that reports an epoch must
  // name one, or nothing binds that epoch to the tenant being asked about.
  reportString(scope.tenant, `${at}.tenant`);
  for (const field of EVIDENCE_SCOPE_KEYS) {
    if (report[field] !== null && scope[field] !== report[field]) {
      badReport(`${at}.${field}`, `${at}.${field} does not bind this deployment's ${field}`);
    }
  }
}

/**
 * One axis, in a fixed order so two readers reach the same reason from the same
 * pair: an undeclared client axis first (the caller can fix that one), then the
 * deployment's own states, then the exact comparison. Every non-match blocks;
 * the order only decides which reason the summary names, and the whole axis map
 * is returned either way.
 */
function evaluateAxis(axis, client, deployment) {
  const base = {
    axis,
    client: { declared: client.declared, label: client.label, digest: client.digest, reason: client.reason },
    deployment: {
      state: deployment.state, label: deployment.label, digest: deployment.digest,
      reason: deployment.reason, epoch_state: deployment.epoch_state,
    },
    mismatched_fields: [],
  };
  if (!client.declared) {
    return { ...base, state: "client_undeclared", reason_id: "client_axis_undeclared", reason: client.reason };
  }
  if (deployment.state === "policy_epoch_incompatible") {
    return {
      ...base, state: "policy_epoch_incompatible", reason_id: "policy_epoch_incompatible",
      reason: deployment.reason,
    };
  }
  if (deployment.state !== "observed") {
    return {
      ...base, state: "deployment_unobservable", reason_id: "deployment_axis_unobservable",
      reason: deployment.reason,
    };
  }
  const mismatched = [];
  if (client.digest !== deployment.digest) mismatched.push("digest");
  if (client.label !== null && deployment.label !== null && client.label !== deployment.label) {
    mismatched.push("label");
  }
  if (mismatched.length > 0) {
    return {
      ...base, state: "mismatch", reason_id: AXIS_MISMATCH_REASON[axis], mismatched_fields: mismatched,
      reason: `client and deployment ${axis} ${mismatched.join(" and ")} differ`,
    };
  }
  return { ...base, state: "match", reason_id: null, reason: null };
}

/**
 * Compare one client declaration against one deployment observation.
 *
 *   1. The request must be readable and closed. `enforced_axes`, `skew` and
 *      `commit_distance` are unknown fields, so no caller can narrow the test or
 *      argue a mismatch away with distance or ancestry.
 *   2. The two reports must describe the same deployment. An unlabelled
 *      deployment, or a client asking about a different environment, blocks
 *      before any digest is compared — matching digests read off the wrong
 *      environment are not evidence about this one.
 *   3. All four axes must be known on both sides and equal.
 */
export function evaluateCommandVersionCompatibility(request) {
  assertObject(request, "request");
  assertClosedKeys(request, ["client", "deployment"], "request");
  assertRequiredKeys(request, ["client", "deployment"], "request");
  const client = assertNormalizedClientDeclaration(request.client, "request.client");
  const deployment = assertNormalizedDeploymentObservation(request.deployment, "request.deployment");

  let scopeState = "bound";
  let scopeReasonId = null;
  let scopeReason = null;
  const policyEvidence = deployment.axes.policy.evidence_scope;
  // Evidence the axis could not stand on is already set aside as unobservable
  // and blocks on its own; only evidence an epoch is actually reported from is
  // compared to the client, so the refusal names the real reason.
  const standingPolicyEvidence = deployment.axes.policy.state === "unobservable" ? null : policyEvidence;
  const clientScope = {
    tenant: client.tenant, environment: client.environment, deployment_ref: client.deployment_ref,
  };
  if (deployment.environment === null) {
    scopeState = "deployment_environment_unobservable";
    scopeReasonId = "deployment_environment_unobservable";
    scopeReason = deployment.environment_reason;
  } else {
    // Both the payload's own scope and the scope of the policy evidence are
    // checked against the client. The second is not redundant: /release reports
    // no tenant, so an epoch report for another tenant contradicts nothing in
    // the payload and would otherwise pass unseen.
    for (const [source, field, theirs] of [
      ...EVIDENCE_SCOPE_KEYS.map(field => ["the observed deployment's", field, deployment[field]]),
      ...(standingPolicyEvidence
        ? EVIDENCE_SCOPE_KEYS.map(field => ["the policy evidence's", field, standingPolicyEvidence[field]])
        : []),
    ]) {
      const mine = clientScope[field];
      if (mine !== null && theirs !== null && mine !== theirs) {
        scopeState = "evidence_scope_mismatch";
        scopeReasonId = "evidence_scope_mismatch";
        scopeReason = `client ${field} "${mine}" is not ${source} ${field} "${theirs}"`;
        break;
      }
    }
  }

  const axisStates = {};
  const blockingAxes = [];
  for (const axis of V5_VERSION_AXES) {
    const state = evaluateAxis(axis, client.axes[axis], deployment.axes[axis]);
    axisStates[axis] = state;
    if (state.state !== "match") blockingAxes.push(axis);
  }

  const scopeBlocks = scopeState !== "bound";
  const compatible = !scopeBlocks && blockingAxes.length === 0;
  return deepFreeze({
    schema_version: V5_COMMAND_VERSION_SCHEMA_VERSION,
    policy_version: V5_COMMAND_VERSION_POLICY_VERSION,
    decision: compatible ? "allow" : "refuse",
    reason_id: compatible
      ? "all_four_axes_known_and_matched"
      : (scopeBlocks ? scopeReasonId : axisStates[blockingAxes[0]].reason_id),
    axes_required: [...V5_VERSION_AXES],
    axes_known_and_matched: V5_VERSION_AXES.filter(axis => axisStates[axis].state === "match"),
    blocking_axes: blockingAxes,
    axis_states: axisStates,
    scope: {
      state: scopeState,
      reason: scopeReason,
      client_tenant: client.tenant,
      client_environment: client.environment,
      client_deployment_ref: client.deployment_ref,
      deployment_tenant: deployment.tenant,
      deployment_environment: deployment.environment,
      deployment_ref: deployment.deployment_ref,
      // Carried, not consumed: the scope of the policy evidence stays readable
      // in the answer, including the tenant the payload itself cannot report.
      policy_evidence: policyEvidence ? { ...policyEvidence } : null,
    },
    // What this answer is not. A reader of a public payload learns whether four
    // reported versions agree; it learns nothing about who is calling and admits
    // nobody to anything.
    authenticated: false,
    authorizes_command_dispatch: false,
    runtime_admission_granted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest. Identity for these rules
// and nothing else — computing it accepts nothing and proves no deployment.
// ---------------------------------------------------------------------------

export function v5CommandVersionPolicyPreimage() {
  return {
    schema_version: V5_COMMAND_VERSION_SCHEMA_VERSION,
    policy_version: V5_COMMAND_VERSION_POLICY_VERSION,
    axes: [...V5_VERSION_AXES],
    axis_states: [...V5_AXIS_STATES].sort(),
    reason_ids: [...V5_COMMAND_VERSION_REASON_IDS],
    trusted_policy_epoch_sources: [...V5_TRUSTED_POLICY_EPOCH_SOURCES],
    policy_observation_seam: V5_POLICY_OBSERVATION_SEAM,
    all_axes_required: true,
    caller_may_select_axes: false,
    policy_evidence_must_bind_reported_scope: true,
    policy_evidence_must_name_tenant: true,
    unknown_environment_sentinel_counts_as_known: false,
    reports_revalidated_at_comparison: true,
    matching: "exact",
    accepts_commit_distance: false,
    accepts_declared_ancestry: false,
    authenticates: false,
    authorizes_command_dispatch: false,
  };
}

/** The deterministic `sha256:` digest of the closed comparator policy. */
export function v5CommandVersionPolicyDigest() {
  return digest(v5CommandVersionPolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5CommandVersionPolicyCanonicalBytes() {
  return canonicalJson(v5CommandVersionPolicyPreimage());
}
