// V5-F07 phase 1 — the four-axis compatibility comparator, proved case by case.
//
// Every deployment observation in this suite comes from the REAL buildRelease()
// running against a fake `sql`, not from a hand-written payload, so a change to
// /release that breaks the comparison breaks this suite rather than production.
//
// The positive case comes first on purpose: a rule that only ever refuses cannot
// be told apart from a broken one. It is built from an honest trusted fixture —
// a policy-epoch status in policy-epoch.js's own shape, explicitly labelled as an
// operator-supplied trusted report — because /release cannot answer that axis and
// nothing here invents an answer for it.
//
//   node --test mcp-server/test/command-version-compatibility.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { buildRelease } from "../src/release.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { PolicyEpochRefusal } from "../src/policy-epoch.js";
import {
  SCAC_MUTATION_REGISTRY_DIGEST,
  SCAC_MUTATION_REGISTRY_VERSION,
} from "../src/mutation-registry.js";
import {
  V5_COMMAND_VERSION_SCHEMA_VERSION,
  V5_COMMAND_VERSION_POLICY_VERSION,
  V5_VERSION_AXES,
  V5_TRUSTED_POLICY_EPOCH_SOURCES,
  V5_POLICY_OBSERVATION_SEAM,
  V5_UNKNOWN_ENVIRONMENT_SENTINEL,
  normalizeClientVersionDeclaration,
  deploymentVersionReportFromRelease,
  evaluateCommandVersionCompatibility,
  v5CommandVersionPolicyPreimage,
  v5CommandVersionPolicyDigest,
  v5CommandVersionPolicyCanonicalBytes,
} from "../src/command-version-compatibility.v5.js";

const SRC_PATH = fileURLToPath(new URL("../src/command-version-compatibility.v5.js", import.meta.url));

const FIXED_NOW = () => new Date("2026-09-10T00:00:00.000Z");
const GIT_SHA = "a".repeat(40);
const OTHER_GIT_SHA = "b".repeat(40);
const LEDGER_SHA256 = "sha256:" + "7".repeat(64);
const OTHER_LEDGER_SHA256 = "sha256:" + "6".repeat(64);
const MIGRATION = "0114_scac_successor.sql";
const WORKER_VERSION_ID = "cf-version-abc";
const POLICY_REGISTRY_VERSION = "scac-policy-registry.v3";
const POLICY_REGISTRY_DIGEST = "sha256:" + "9".repeat(64);
const POLICY_ENTRY_DIGEST = "sha256:" + "8".repeat(64);

/** A stand-in for neon()'s tagged-template `sql`, routed by query substring. */
function fakeSql({ ledger = { applied_count: 120, highest_applied_migration: MIGRATION, ledger_sha256: LEDGER_SHA256 },
  generation = 359 } = {}) {
  return async (strings) => {
    const query = strings.join(" ");
    if (query.includes("v_schema_ledger")) {
      if (ledger instanceof Error) throw ledger;
      return [ledger];
    }
    if (query.includes("doctrine_meta")) return [{ generation }];
    throw new Error("unmocked query: " + query);
  };
}

async function release({ env = {}, sql = fakeSql(), verbCount = 105 } = {}) {
  return buildRelease({
    env: {
      GIT_SHA,
      CARR_ENV: "production",
      CF_VERSION_METADATA: { id: WORKER_VERSION_ID, tag: "r7", timestamp: "2026-09-09T12:00:00.000Z" },
      ...env,
    },
    sql,
    verbCount,
    now: FIXED_NOW,
  });
}

/** A policy-epoch status in policy-epoch.js's exact shape. */
function policyStatus(overrides = {}) {
  return {
    epoch_state: "current",
    compatibility_state: "compatible",
    current_epoch: 7,
    request_epoch: 7,
    reason_id: null,
    current_entry_digest: POLICY_ENTRY_DIGEST,
    registry_version: POLICY_REGISTRY_VERSION,
    registry_digest: POLICY_REGISTRY_DIGEST,
    compatibility_authority: "fact_only_not_enforcement",
    ...overrides,
  };
}

// The honest fixture binds every dimension the release payload reports — an
// epoch report that names neither environment nor worker version has not shown
// it describes THIS deployment — and names the tenant /release cannot report.
function trustedPolicyObservation(overrides = {}) {
  return {
    source: V5_TRUSTED_POLICY_EPOCH_SOURCES[0],
    status: policyStatus(),
    tenant: "carr-internal",
    environment: "production",
    deployment_ref: WORKER_VERSION_ID,
    ...overrides,
  };
}

/** The declaration of a client built from exactly the deployment above. */
function matchingClient(overrides = {}) {
  const { axes: axisOverrides = {}, ...rest } = overrides;
  return normalizeClientVersionDeclaration({
    tenant: "carr-internal",
    environment: "production",
    deployment_ref: WORKER_VERSION_ID,
    axes: {
      code: { digest: GIT_SHA },
      schema: { label: MIGRATION, digest: LEDGER_SHA256 },
      command_contract: {
        label: SCAC_MUTATION_REGISTRY_VERSION,
        digest: SCAC_MUTATION_REGISTRY_DIGEST,
      },
      policy: { label: POLICY_REGISTRY_VERSION, digest: POLICY_REGISTRY_DIGEST },
      ...axisOverrides,
    },
    ...rest,
  });
}

async function compare({ client = matchingClient(), releaseOptions = {}, observation = trustedPolicyObservation(),
  mutateRelease = payload => payload } = {}) {
  const payload = mutateRelease(await release(releaseOptions));
  const deployment = deploymentVersionReportFromRelease(payload, { policy_observation: observation });
  return { payload, deployment, result: evaluateCommandVersionCompatibility({ client, deployment }) };
}

function boundaryError(code) {
  return error => error instanceof V5BoundaryError && error.code === code;
}

// --- the positive: exactly four known axes, and nothing wider ---------------

test("all four axes known and matched allows, and admits nothing", async () => {
  const { result, payload } = await compare();

  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "all_four_axes_known_and_matched");
  assert.deepEqual(result.blocking_axes, []);
  assert.deepEqual(result.axes_known_and_matched, [...V5_VERSION_AXES]);
  assert.deepEqual(result.axes_required, ["code", "schema", "command_contract", "policy"]);
  for (const axis of V5_VERSION_AXES) assert.equal(result.axis_states[axis].state, "match", axis);
  assert.equal(result.scope.state, "bound");
  assert.equal(result.scope.deployment_environment, "production");

  // The command-contract axis is the real shipped registry identity, read out of
  // the real payload rather than a literal written into this test.
  assert.equal(payload.command_contract.registry_digest, SCAC_MUTATION_REGISTRY_DIGEST);
  assert.equal(result.axis_states.command_contract.deployment.digest, SCAC_MUTATION_REGISTRY_DIGEST);
  assert.equal(result.axis_states.policy.deployment.epoch_state, "current");

  // An allow is a version fact. It is not authentication and admits no caller.
  assert.equal(result.authenticated, false);
  assert.equal(result.authorizes_command_dispatch, false);
  assert.equal(result.runtime_admission_granted, false);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
  assert.equal(result.schema_version, V5_COMMAND_VERSION_SCHEMA_VERSION);
  assert.equal(result.policy_version, V5_COMMAND_VERSION_POLICY_VERSION);
});

// --- the four axes, each unobservable on its own ---------------------------

test("a current release with no trusted policy observation refuses: policy is unobservable", async () => {
  const { result, payload } = await compare({ observation: null });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deployment_axis_unobservable");
  assert.deepEqual(result.blocking_axes, ["policy"]);
  assert.equal(result.axis_states.policy.state, "deployment_unobservable");
  // doctrine_generation is present and healthy in this payload, and it is still
  // not a policy epoch. That is the whole point of the refusal.
  assert.equal(payload.doctrine_generation.value, 359);
  assert.match(result.axis_states.policy.deployment.reason, /doctrine_generation is a doctrine row counter/);
  assert.match(result.axis_states.policy.deployment.reason, new RegExp(V5_POLICY_OBSERVATION_SEAM));
  assert.equal(result.axis_states.code.state, "match");
});

test("an older release with no command_contract field refuses, visibly, on that axis", async () => {
  const { result } = await compare({
    mutateRelease: payload => {
      const { command_contract: _dropped, ...older } = payload;
      return older;
    },
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deployment_axis_unobservable");
  assert.deepEqual(result.blocking_axes, ["command_contract"]);
  assert.match(result.axis_states.command_contract.deployment.reason,
    /predates command-contract reporting/);
  // The client declared it; the deployment simply cannot answer.
  assert.equal(result.axis_states.command_contract.client.declared, true);
});

test("an unstamped git_sha refuses on code, carrying the release payload's own reason", async () => {
  const { payload, result } = await compare({ releaseOptions: { env: { GIT_SHA: undefined } } });

  assert.equal(result.decision, "refuse");
  assert.deepEqual(result.blocking_axes, ["code"]);
  assert.equal(result.axis_states.code.state, "deployment_unobservable");
  assert.equal(result.axis_states.code.deployment.reason, payload.git_sha.reason);
  assert.match(result.axis_states.code.deployment.reason, /not stamped/);
});

test("an unreachable schema ledger refuses on schema, carrying the database reason", async () => {
  const { result } = await compare({
    releaseOptions: { sql: fakeSql({ ledger: new Error("connection terminated unexpectedly") }) },
  });

  assert.equal(result.decision, "refuse");
  assert.deepEqual(result.blocking_axes, ["schema"]);
  assert.match(result.axis_states.schema.deployment.reason, /database unreachable/);
});

test("each undeclared client axis refuses, naming that axis", async () => {
  for (const axis of V5_VERSION_AXES) {
    const { result } = await compare({ client: matchingClient({ axes: { [axis]: null } }) });
    assert.equal(result.decision, "refuse", axis);
    assert.equal(result.reason_id, "client_axis_undeclared", axis);
    assert.deepEqual(result.blocking_axes, [axis]);
    assert.equal(result.axis_states[axis].state, "client_undeclared");
    assert.match(result.axis_states[axis].client.reason, new RegExp(axis));
  }
});

// --- the four mismatches. A label is never a substitute for a digest --------

test("the same registry version with a different digest refuses", async () => {
  const { result } = await compare({
    client: matchingClient({
      axes: {
        command_contract: {
          label: SCAC_MUTATION_REGISTRY_VERSION, // identical label
          digest: "0".repeat(64), // rebuilt registry
        },
      },
    }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "command_contract_registry_mismatch");
  assert.deepEqual(result.axis_states.command_contract.mismatched_fields, ["digest"]);
});

test("the same migration number with a different ledger hash refuses", async () => {
  const { result } = await compare({
    client: matchingClient({ axes: { schema: { label: MIGRATION, digest: OTHER_LEDGER_SHA256 } } }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "schema_ledger_mismatch");
  assert.deepEqual(result.axis_states.schema.mismatched_fields, ["digest"]);
});

test("a different code revision refuses", async () => {
  const { result } = await compare({
    client: matchingClient({ axes: { code: { digest: OTHER_GIT_SHA } } }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "code_version_mismatch");
  assert.deepEqual(result.blocking_axes, ["code"]);
});

test("a different policy registry digest refuses even with a current epoch", async () => {
  const { result } = await compare({
    client: matchingClient({
      axes: { policy: { label: POLICY_REGISTRY_VERSION, digest: "sha256:" + "1".repeat(64) } },
    }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "policy_epoch_mismatch");
  assert.equal(result.axis_states.policy.deployment.epoch_state, "current");
});

test("a rolled-back or stale policy epoch refuses, whatever the digests say", async () => {
  for (const epochState of ["rolled_back", "stale", "future"]) {
    const { result } = await compare({
      observation: trustedPolicyObservation({
        status: policyStatus({
          epoch_state: epochState,
          compatibility_state: "incompatible",
          reason_id: "scac.refusal.epoch_incompatible",
          current_epoch: 6,
          request_epoch: 7,
        }),
      }),
    });

    assert.equal(result.decision, "refuse", epochState);
    assert.equal(result.reason_id, "policy_epoch_incompatible", epochState);
    assert.deepEqual(result.blocking_axes, ["policy"]);
    assert.equal(result.axis_states.policy.deployment.epoch_state, epochState);
  }
});

// --- untrusted, unknown and cross-environment evidence ----------------------

test("an untrusted policy source observes nothing; the axis stays unknown and blocks", async () => {
  const { result } = await compare({
    observation: { source: "release_doctrine_generation", status: policyStatus() },
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deployment_axis_unobservable");
  assert.match(result.axis_states.policy.deployment.reason, /is not a trusted epoch source/);
});

test("a malformed policy status surfaces PolicyEpochRefusal unchanged, not a local reinterpretation", async () => {
  const payload = await release();
  assert.throws(
    () => deploymentVersionReportFromRelease(payload, {
      policy_observation: trustedPolicyObservation({ status: policyStatus({ compatibility_state: "maybe" }) }),
    }),
    error => error instanceof PolicyEpochRefusal && error.reason === "epoch_status_malformed",
  );
});

test("a staging report is never answered as production", async () => {
  const { result } = await compare({
    releaseOptions: { env: { CARR_ENV: "staging" } },
    // Evidence honestly bound to the staging deployment it describes.
    observation: trustedPolicyObservation({ environment: "staging" }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "evidence_scope_mismatch");
  assert.match(result.scope.reason, /environment "production" is not the observed deployment's environment "staging"/);
  // The digests all match; the evidence is simply about a different deployment.
  assert.deepEqual(result.blocking_axes, []);
});

test("an unlabelled deployment refuses before any digest is read", async () => {
  const { result } = await compare({ releaseOptions: { env: { CARR_ENV: undefined } } });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deployment_environment_unobservable");
  assert.match(result.scope.reason, /never assumed to be production/);
  assert.equal(result.scope.deployment_environment, null);
});

test("a policy observation about another environment observes nothing about this one", async () => {
  const { result } = await compare({
    observation: trustedPolicyObservation({ environment: "staging" }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deployment_axis_unobservable");
  assert.match(result.axis_states.policy.deployment.reason,
    /observation environment "staging" is not this deployment's environment "production"/);
});

test("a client asking about a different worker version refuses", async () => {
  const { result } = await compare({ client: matchingClient({ deployment_ref: "cf-version-old" }) });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "evidence_scope_mismatch");
  assert.match(result.scope.reason, /deployment_ref/);
});

// --- the closed shape: nothing can narrow, defer or outvote the test --------

test("a caller cannot name which axes count, or argue distance and ancestry", async () => {
  const client = matchingClient();
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });

  for (const extra of [
    { enforced_axes: ["code", "schema"] },
    { axes_deferred: ["policy"] },
    { skew: { commit_distance: 0 } },
    { commit_distance: 370 },
    { declared_ancestry: "deployed_is_ancestor" },
  ]) {
    assert.throws(
      () => evaluateCommandVersionCompatibility({ client, deployment, ...extra }),
      boundaryError("unknown_field"),
      Object.keys(extra)[0],
    );
  }
});

test("unknown axes and unknown axis fields cannot be read", () => {
  assert.throws(
    () => normalizeClientVersionDeclaration({
      tenant: "carr-internal", environment: "production",
      axes: { code: { digest: GIT_SHA }, verb_count: { digest: GIT_SHA } },
    }),
    boundaryError("unknown_version_axis"),
  );
  assert.throws(
    () => normalizeClientVersionDeclaration({
      tenant: "carr-internal", environment: "production",
      axes: { code: { digest: GIT_SHA, compatible_anyway: true } },
    }),
    boundaryError("unknown_field"),
  );
  assert.throws(
    () => normalizeClientVersionDeclaration({
      tenant: "carr-internal", environment: "production",
      axes: { code: { digest: "not-a-digest" } },
    }),
    boundaryError("invalid_digest"),
  );
  assert.throws(
    () => normalizeClientVersionDeclaration({ environment: "production", axes: {} }),
    boundaryError("missing_field"),
  );
});

test("hand-built reports are refused; only this module's normalizers produce inputs", async () => {
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });
  const forged = {
    report_kind: "client_declaration",
    schema_version: "some-other-contract.v1",
    tenant: "carr-internal", environment: "production", deployment_ref: WORKER_VERSION_ID,
    axes: Object.fromEntries(V5_VERSION_AXES.map(axis => [axis, { declared: true, label: null, digest: GIT_SHA, reason: null }])),
  };
  assert.throws(
    () => evaluateCommandVersionCompatibility({ client: forged, deployment }),
    boundaryError("unnormalized_report"),
  );
  assert.throws(
    () => evaluateCommandVersionCompatibility({ client: matchingClient(), deployment: { ...deployment, report_kind: "client_declaration" } }),
    boundaryError("unnormalized_report"),
  );
  // Copying the marker does not skip the digest validation the normalizer does.
  assert.throws(
    () => evaluateCommandVersionCompatibility({
      client: { ...forged, schema_version: V5_COMMAND_VERSION_SCHEMA_VERSION,
        axes: { ...forged.axes, command_contract: { declared: true, label: null, digest: "trust-me", reason: null } } },
      deployment,
    }),
    boundaryError("unnormalized_report"),
  );
});

// --- policy evidence scope: kept, bound, and compared ----------------------

test("policy evidence for another TENANT refuses, though /release reports no tenant", async () => {
  const { deployment, result } = await compare({
    observation: trustedPolicyObservation({ tenant: "different-tenant" }),
  });

  // The payload cannot contradict this: it has no tenant field at all.
  assert.equal(deployment.tenant, null);
  assert.equal(deployment.axes.policy.state, "observed");
  // So the evidence's own tenant is kept and compared against the client's.
  assert.equal(deployment.axes.policy.evidence_scope.tenant, "different-tenant");
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "evidence_scope_mismatch");
  assert.match(result.scope.reason,
    /client tenant "carr-internal" is not the policy evidence's tenant "different-tenant"/);
  assert.equal(result.scope.policy_evidence.tenant, "different-tenant");
  assert.equal(result.scope.deployment_tenant, null, "a missing tenant stays unknown, never invented");
});

test("policy evidence that does not bind a dimension this deployment reports observes nothing", async () => {
  for (const [field, value] of [["deployment_ref", WORKER_VERSION_ID], ["environment", "production"]]) {
    const { result } = await compare({
      observation: trustedPolicyObservation({ [field]: null }),
    });
    assert.equal(result.decision, "refuse", field);
    assert.equal(result.reason_id, "deployment_axis_unobservable", field);
    assert.deepEqual(result.blocking_axes, ["policy"], field);
    assert.match(result.axis_states.policy.deployment.reason,
      new RegExp(`does not bind this deployment's ${field} "${value}"`));
  }
});

test("policy evidence naming another worker version observes nothing", async () => {
  const { result } = await compare({
    observation: trustedPolicyObservation({ deployment_ref: "cf-version-old" }),
  });

  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "deployment_axis_unobservable");
  assert.match(result.axis_states.policy.deployment.reason,
    /observation deployment_ref "cf-version-old" is not this deployment's deployment_ref "cf-version-abc"/);
});

test("policy evidence naming NO tenant observes nothing — the payload has no tenant to fall back on", async () => {
  for (const observation of [
    trustedPolicyObservation({ tenant: null }),
    // The same absence, written as an omission rather than a null.
    {
      source: V5_TRUSTED_POLICY_EPOCH_SOURCES[0], status: policyStatus(),
      environment: "production", deployment_ref: WORKER_VERSION_ID,
    },
  ]) {
    const { deployment, result } = await compare({ observation });

    assert.equal(result.decision, "refuse");
    assert.equal(result.reason_id, "deployment_axis_unobservable");
    assert.deepEqual(result.blocking_axes, ["policy"]);
    assert.match(result.axis_states.policy.deployment.reason, /names no tenant/);
    // Unbound is not the same as agreeing with whatever the client declared.
    assert.equal(deployment.tenant, null, "the release tenant stays visibly unknown");
    assert.notEqual(result.decision, "allow");
  }
});

// --- normalized reports are revalidated, not recognized by their marker -----

test("an honest JSON round-trip of a normalized report still compares the same", async () => {
  const client = matchingClient();
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });
  const copied = JSON.parse(JSON.stringify(deployment));

  assert.equal(evaluateCommandVersionCompatibility({ client, deployment: copied }).decision, "allow");
});

test("a tampered epoch_state cannot ride an observed policy axis into an allow", async () => {
  const client = matchingClient();
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });
  const tampered = JSON.parse(JSON.stringify(deployment));
  tampered.axes.policy.epoch_state = "rolled_back"; // state stays "observed"

  assert.throws(
    () => evaluateCommandVersionCompatibility({ client, deployment: tampered }),
    boundaryError("unnormalized_report"),
  );
});

test("policy evidence naming the unlabelled sentinel refuses legibly, and is not unreadable", async () => {
  // The refusal has to survive the trip back through the evaluator: this module
  // produced the report, so rejecting it as unreadable would hide its own answer.
  for (const [label, source] of [
    ["trusted", V5_TRUSTED_POLICY_EPOCH_SOURCES[0]],
    ["untrusted", "release_doctrine_generation"],
  ]) {
    const { deployment, result } = await compare({
      observation: {
        source, status: policyStatus(), tenant: "carr-internal",
        environment: V5_UNKNOWN_ENVIRONMENT_SENTINEL, deployment_ref: WORKER_VERSION_ID,
      },
    });

    // The deployment itself is labelled; only the evidence is not.
    assert.equal(deployment.environment, "production", label);
    assert.equal(deployment.axes.policy.state, "unobservable", label);
    assert.equal(deployment.axes.policy.evidence_scope.environment,
      V5_UNKNOWN_ENVIRONMENT_SENTINEL, label);
    assert.equal(result.decision, "refuse", label);
    assert.notEqual(result.decision, "allow", label);
    assert.equal(result.reason_id, "deployment_axis_unobservable", label);
    assert.deepEqual(result.blocking_axes, ["policy"], label);
    assert.equal(result.scope.state, "bound", label);
  }
});

test("a normalized report cannot report an epoch whose evidence names no tenant", async () => {
  const client = matchingClient();
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });
  const tampered = JSON.parse(JSON.stringify(deployment));
  tampered.axes.policy.evidence_scope.tenant = null; // state stays "observed"

  assert.throws(
    () => evaluateCommandVersionCompatibility({ client, deployment: tampered }),
    boundaryError("unnormalized_report"),
  );
});

test(`"${V5_UNKNOWN_ENVIRONMENT_SENTINEL}" never counts as a known environment, on any surface`, async () => {
  // A client cannot declare the absence of an environment as an environment.
  assert.throws(
    () => normalizeClientVersionDeclaration({
      tenant: "carr-internal", environment: V5_UNKNOWN_ENVIRONMENT_SENTINEL,
      axes: { code: { digest: GIT_SHA } },
    }),
    boundaryError("unknown_environment_sentinel"),
  );

  // The adapter normalizes the payload's sentinel to a visibly unknown environment.
  const unlabelled = deploymentVersionReportFromRelease(
    await release({ env: { CARR_ENV: undefined } }), { policy_observation: trustedPolicyObservation() });
  assert.equal(unlabelled.environment, null);
  assert.match(unlabelled.environment_reason, /never assumed to be production/);

  // And a report handed straight to the evaluator cannot smuggle it back in —
  // including two sides that would otherwise "agree" on being unknown.
  const client = matchingClient();
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });
  for (const tamper of [
    report => { report.environment = V5_UNKNOWN_ENVIRONMENT_SENTINEL; },
    report => {
      report.environment = V5_UNKNOWN_ENVIRONMENT_SENTINEL;
      report.environment_reason = "CARR_ENV not set on this Worker";
    },
    report => { report.axes.policy.evidence_scope.environment = V5_UNKNOWN_ENVIRONMENT_SENTINEL; },
  ]) {
    const report = JSON.parse(JSON.stringify(deployment));
    tamper(report);
    assert.throws(
      () => evaluateCommandVersionCompatibility({ client, deployment: report }),
      boundaryError("unnormalized_report"),
    );
  }
  const clientCopy = JSON.parse(JSON.stringify(client));
  clientCopy.environment = V5_UNKNOWN_ENVIRONMENT_SENTINEL;
  assert.throws(
    () => evaluateCommandVersionCompatibility({ client: clientCopy, deployment }),
    boundaryError("unnormalized_report"),
  );
});

test("every tampered shape refuses rather than turning into compatibility", async () => {
  const client = matchingClient();
  const deployment = deploymentVersionReportFromRelease(await release(),
    { policy_observation: trustedPolicyObservation() });
  const copy = () => JSON.parse(JSON.stringify(deployment));

  const tamperings = {
    "unknown report key": report => { report.compatible_anyway = true; },
    "unknown axis-entry key": report => { report.axes.code.override = true; },
    "missing axis-entry key": report => { delete report.axes.schema.reason; },
    "missing axis": report => { delete report.axes.command_contract; },
    "unknown axis": report => { report.axes.verb_count = { ...report.axes.code }; },
    "observed axis with no digest": report => { report.axes.code.digest = null; },
    "unobservable axis carrying a version": report => {
      report.axes.schema.state = "unobservable";
      report.axes.schema.reason = "made up";
    },
    "epoch state on a non-policy axis": report => { report.axes.code.epoch_state = "current"; },
    "incompatible state on a non-policy axis": report => {
      report.axes.code.state = "policy_epoch_incompatible";
    },
    "policy evidence removed": report => { report.axes.policy.evidence_scope = null; },
    "policy evidence unbound from the deployment": report => {
      report.axes.policy.evidence_scope.environment = "staging";
    },
    "policy evidence with an unknown key": report => { report.axes.policy.evidence_scope.trusted = true; },
    "environment reason erased while the environment is unknown": report => {
      report.environment = null;
      report.environment_reason = null;
    },
    "a rewritten source marker": report => { report.source = "operator_says_so"; },
  };
  for (const [name, tamper] of Object.entries(tamperings)) {
    const report = copy();
    tamper(report);
    assert.throws(
      () => evaluateCommandVersionCompatibility({ client, deployment: report }),
      boundaryError("unnormalized_report"),
      name,
    );
  }

  // The same discipline on the client side: an undeclared axis cannot smuggle a
  // version back in, and a declared one cannot skip its digest.
  const clientCopy = JSON.parse(JSON.stringify(matchingClient({ axes: { policy: null } })));
  clientCopy.axes.policy.digest = POLICY_REGISTRY_DIGEST; // still declared:false
  assert.throws(
    () => evaluateCommandVersionCompatibility({ client: clientCopy, deployment }),
    boundaryError("unnormalized_report"),
  );
});

test("results are frozen and the inputs are not touched", async () => {
  const client = matchingClient();
  const payload = await release();
  const deployment = deploymentVersionReportFromRelease(payload,
    { policy_observation: trustedPolicyObservation() });
  const result = evaluateCommandVersionCompatibility({ client, deployment });

  assert.ok(Object.isFrozen(result));
  assert.ok(Object.isFrozen(result.axis_states.code));
  assert.ok(Object.isFrozen(client.axes.code));
  assert.throws(() => { result.decision = "allow"; }, TypeError);
  assert.throws(() => { result.blocking_axes.push("code"); }, TypeError);
  // Reading the payload does not rewrite it.
  assert.deepEqual(payload.command_contract.registry_version, SCAC_MUTATION_REGISTRY_VERSION);
});

test("the module reads no clock, network, filesystem, database or environment", () => {
  const source = readFileSync(SRC_PATH, "utf8");
  for (const pattern of [
    /\bnode:fs\b/, /\bnode:net\b/, /\bnode:http\b/, /\bnode:https\b/, /\bnode:child_process\b/,
    /\bfetch\s*\(/, /\bprocess\.env\b/, /\bnew Date\b/, /\bDate\.now\b/,
    /\bsetTimeout\b/, /\bsetInterval\b/, /\brequire\s*\(/, /\bimport\s*\(/, /\bglobalThis\b/,
  ]) {
    assert.ok(!pattern.test(source), `module source must not contain ${pattern}`);
  }
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)].map(match => match[1]).sort();
  assert.deepEqual(imports, ["./artifact-trust.js", "./global-boundaries.v5.js", "./policy-epoch.js"],
    "the comparator reuses the existing kernels and keeps no second registry");
});

test("the policy preimage is closed to Q058's four axes and hashes deterministically", () => {
  const preimage = v5CommandVersionPolicyPreimage();
  assert.deepEqual(preimage.axes, ["code", "schema", "command_contract", "policy"]);
  assert.equal(preimage.all_axes_required, true);
  assert.equal(preimage.caller_may_select_axes, false);
  assert.equal(preimage.matching, "exact");
  assert.equal(preimage.accepts_commit_distance, false);
  assert.equal(preimage.accepts_declared_ancestry, false);
  assert.equal(preimage.authenticates, false);

  // Re-hashed here by hand rather than compared against a copy of the module's
  // own answer, so the digest is checked instead of merely echoed.
  const expected = "sha256:" + createHash("sha256")
    .update(v5CommandVersionPolicyCanonicalBytes()).digest("hex");
  assert.equal(v5CommandVersionPolicyDigest(), expected);
  assert.equal(v5CommandVersionPolicyDigest(), v5CommandVersionPolicyDigest());
});
