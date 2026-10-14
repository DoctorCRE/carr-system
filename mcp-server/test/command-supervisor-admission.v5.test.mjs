
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_TRUSTED_POLICY_EPOCH_SOURCES,
  normalizeClientVersionDeclaration,
  deploymentVersionReportFromRelease,
  evaluateCommandVersionCompatibility,
} from "../src/command-version-compatibility.v5.js";
import {
  V5_COMMAND_SUPERVISOR_SCHEMA_VERSION,
  V5_COMMAND_SUPERVISOR_POLICY_VERSION,
  V5_SUPERVISOR_ADMISSION_CHECKS,
  V5_CHECKS_BEFORE_ANY_DIGEST_IS_READ,
  V5_ADMISSION_CHECK_STATES,
  V5_ROLLBACK_COUNTER_STATES,
  V5_SUPERVISOR_MODES,
  V5_CAPABILITY_STATES,
  V5_NONCE_STATES,
  V5_PATH_RESOLUTION_MODES,
  V5_COMMAND_SUPERVISOR_REASON_IDS,
  V5_ADMISSIBLE_PATH_RESOLUTION,
  V5_REQUIRED_LINK_COUNT,
  V5_DIRECT_CREDENTIAL_CLAUSE_SEAM,
  V5_DIRECT_CREDENTIAL_BLOCKING_DECISION_IDS,
  commandSupervisorRegistryEntryDigest,
  normalizeCommandSupervisorRegistry,
  normalizeSupervisorCapability,
  normalizeCommandExecutionObservation,
  evaluateCommandSupervisorAdmission,
  v5CommandSupervisorPolicyPreimage,
  v5CommandSupervisorPolicyDigest,
  v5CommandSupervisorPolicyCanonicalBytes,
} from "../src/command-supervisor-admission.v5.js";

const SRC_PATH = fileURLToPath(new URL("../src/command-supervisor-admission.v5.js", import.meta.url));

const COMMAND_ID = "deal-room.read";
const DECLARED_ROOT = "/opt/carr/supervisor/handlers";
const RESOLVED_PATH = "/opt/carr/supervisor/handlers/deal-room-read";
const REGISTERED_BYTES = "sha256:" + "1".repeat(64);
const SUBSTITUTED_BYTES = "sha256:" + "2".repeat(64);
const REGISTRY_DIGEST = "sha256:" + "3".repeat(64);
const HOLDER = "joe-local";

const GIT_SHA = "a".repeat(40);
const LEDGER_SHA256 = "sha256:" + "7".repeat(64);
const CONTRACT_DIGEST = "sha256:" + "5".repeat(64);
const POLICY_REGISTRY_DIGEST = "sha256:" + "9".repeat(64);
const POLICY_ENTRY_DIGEST = "sha256:" + "8".repeat(64);
const WORKER_VERSION_ID = "cf-version-abc";
const MIGRATION = "0453_frontier.sql";
const CONTRACT_VERSION = "scac-mutation-registry.v24";
const POLICY_REGISTRY_VERSION = "scac-policy-registry.v3";

function boundaryError(code) {
  return error => error instanceof V5BoundaryError && error.code === code;
}


function releasePayload(overrides = {}) {
  return {
    env: { value: "production" },
    git_sha: { value: GIT_SHA },
    schema: { ledger_sha256: LEDGER_SHA256, highest_applied_migration: MIGRATION },
    command_contract: { registry_digest: CONTRACT_DIGEST, registry_version: CONTRACT_VERSION },
    worker_version: { id: WORKER_VERSION_ID },
    ...overrides,
  };
}

function trustedPolicyObservation() {
  return {
    source: V5_TRUSTED_POLICY_EPOCH_SOURCES[0],
    tenant: ORGANIZATION_TENANT_ID,
    environment: "production",
    deployment_ref: WORKER_VERSION_ID,
    status: {
      epoch_state: "current",
      compatibility_state: "compatible",
      current_epoch: 7,
      request_epoch: 7,
      reason_id: null,
      current_entry_digest: POLICY_ENTRY_DIGEST,
      registry_version: POLICY_REGISTRY_VERSION,
      registry_digest: POLICY_REGISTRY_DIGEST,
      compatibility_authority: "fact_only_not_enforcement",
    },
  };
}

function versionAnswer({ clientCodeDigest = GIT_SHA } = {}) {
  const client = normalizeClientVersionDeclaration({
    tenant: ORGANIZATION_TENANT_ID,
    environment: "production",
    deployment_ref: WORKER_VERSION_ID,
    axes: {
      code: { digest: clientCodeDigest },
      schema: { label: MIGRATION, digest: LEDGER_SHA256 },
      command_contract: { label: CONTRACT_VERSION, digest: CONTRACT_DIGEST },
      policy: { label: POLICY_REGISTRY_VERSION, digest: POLICY_REGISTRY_DIGEST },
    },
  });
  const deployment = deploymentVersionReportFromRelease(releasePayload(),
    { policy_observation: trustedPolicyObservation() });
  return evaluateCommandVersionCompatibility({ client, deployment });
}


function registryEntry(overrides = {}) {
  const fields = {
    command_id: COMMAND_ID,
    executable_digest: REGISTERED_BYTES,
    executable_label: "deal-room-read",
    declared_root: DECLARED_ROOT,
    ...overrides,
  };
  const { command_id, ...rest } = fields;
  return { ...rest, sealed_entry_digest: commandSupervisorRegistryEntryDigest(fields) };
}

function registry({ entries, ...overrides } = {}) {
  return normalizeCommandSupervisorRegistry({
    registry_digest: REGISTRY_DIGEST,
    entries: entries ?? { [COMMAND_ID]: registryEntry() },
    ...overrides,
  });
}

function capability(overrides = {}) {
  return normalizeSupervisorCapability({
    capability_id: "supervisor-capability-0001",
    issued_to: HOLDER,
    mode: "normal",
    state: "active",
    ...overrides,
  });
}

function observation({ path = {}, nonce, rollback, ...overrides } = {}) {
  return normalizeCommandExecutionObservation({
    command_id: COMMAND_ID,
    executable_digest: REGISTERED_BYTES,
    executable_label: "deal-room-read",
    path: {
      resolution: "descriptor_relative",
      resolved_root: DECLARED_ROOT,
      resolved_path: RESOLVED_PATH,
      symlink_followed: false,
      link_count: 1,
      ...path,
    },
    nonce: nonce === undefined ? { nonce_id: "nonce-0001", state: "unconsumed" } : nonce,
    rollback: rollback === undefined
      ? { admitted: { counter_id: "supervisor-generation", value: 41 },
        proposed: { counter_id: "supervisor-generation", value: 42 } }
      : rollback,
    ...overrides,
  });
}

function actor(overrides = {}) {
  return { slug: HOLDER, human: false, sponsoring_human_slug: "joe", ...overrides };
}

function admit(overrides = {}) {
  return evaluateCommandSupervisorAdmission({
    actor: actor(),
    registry: registry(),
    capability: capability(),
    observation: observation(),
    version_compatibility: versionAnswer(),
    ...overrides,
  });
}


test("positive: a clean observation clears every registered negative and allows", () => {
  const result = admit();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "admitted_after_all_supervisor_negatives_cleared");
  assert.equal(result.blocking_check, null);
  assert.deepEqual(result.checks_satisfied, [...V5_SUPERVISOR_ADMISSION_CHECKS]);
  assert.deepEqual(result.checks_not_reached, []);
  assert.equal(result.command.registered, true);
  assert.equal(result.supervisor.actor_slug, HOLDER);
  assert.equal(result.supervisor.actor_authorization_class, "sponsored_agent");
});


test("clause 1: substituted executable bytes refuse, and a matching label does not rescue them", () => {
  const result = admit({
    observation: observation({ executable_digest: SUBSTITUTED_BYTES, executable_label: "deal-room-read" }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "executable_digest_substituted");
  assert.equal(result.blocking_check, "executable_substitution");
  const state = result.check_states.executable_substitution;
  assert.equal(state.state, "violated");
  assert.equal(state.label_matched, true);
  assert.equal(state.registered_digest, REGISTERED_BYTES);
  assert.equal(state.observed_digest, SUBSTITUTED_BYTES);

  const silent = admit({ observation: observation({ executable_digest: null }) });
  assert.equal(silent.decision, "refuse");
  assert.equal(silent.reason_id, "executable_digest_unobservable");
  assert.equal(silent.check_states.executable_substitution.state, "unobservable");
});

test("clause 1: a registry entry whose own sealed digest has moved refuses", () => {
  const moved = { ...registryEntry(), executable_digest: SUBSTITUTED_BYTES };
  const result = admit({
    registry: registry({ entries: { [COMMAND_ID]: moved } }),
    observation: observation({ executable_digest: SUBSTITUTED_BYTES }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  assert.equal(result.check_states.executable_substitution.state, "not_reached");
  assert.notEqual(result.check_states.registry_entry_integrity.recomputed_entry_digest,
    result.check_states.registry_entry_integrity.sealed_entry_digest);
});


const SEALED_ENTRY_BYTES = "{\"command_id\":\"deal-room.read\","
  + "\"declared_root\":\"/opt/carr/supervisor/handlers\","
  + "\"entry_kind\":\"command-supervisor-registry-entry.v1\","
  + "\"executable_digest\":\"sha256:" + "1".repeat(64) + "\","
  + "\"executable_label\":\"deal-room-read\","
  + "\"schema_version\":\"doctorcre-v5-command-supervisor-admission.v1\"}";

const SEALED_ENTRY_DIGEST = "sha256:99c64b2289a70d9eee522c81ace36fa3df4e40de77430996258893ef13bf4f8b";

function sealedFields(overrides = {}) {
  return {
    command_id: COMMAND_ID,
    executable_digest: REGISTERED_BYTES,
    executable_label: "deal-room-read",
    declared_root: DECLARED_ROOT,
    ...overrides,
  };
}

test("clause 1: the registry-entry seal is pinned to literal bytes, not echoed back from the function that computes it", () => {
  assert.equal("sha256:" + createHash("sha256").update(SEALED_ENTRY_BYTES).digest("hex"),
    SEALED_ENTRY_DIGEST);

  assert.equal(commandSupervisorRegistryEntryDigest(sealedFields()), SEALED_ENTRY_DIGEST);

  assert.deepEqual(Object.keys(JSON.parse(SEALED_ENTRY_BYTES)).sort(),
    ["command_id", "declared_root", "entry_kind", "executable_digest",
      "executable_label", "schema_version"]);
});

test("clause 1: every field the registry-entry seal covers moves the seal", () => {
  const changed = {
    command_id: "deal-room.write",
    executable_digest: SUBSTITUTED_BYTES,
    executable_label: "something-else",
    declared_root: "/opt/carr/supervisor/other",
  };
  for (const field of Object.keys(changed)) {
    assert.notEqual(commandSupervisorRegistryEntryDigest(sealedFields({ [field]: changed[field] })),
      SEALED_ENTRY_DIGEST, field);
  }
  assert.notEqual(commandSupervisorRegistryEntryDigest(sealedFields({ executable_label: null })),
    SEALED_ENTRY_DIGEST);
  assert.equal(commandSupervisorRegistryEntryDigest(sealedFields()), SEALED_ENTRY_DIGEST);
});

test("clause 1: a registry entry re-pointed at another root after sealing refuses", () => {
  const REPOINTED_ROOT = "/opt/carr/supervisor/other";
  const repointed = { ...registryEntry(), declared_root: REPOINTED_ROOT };
  const result = admit({
    registry: registry({ entries: { [COMMAND_ID]: repointed } }),
    observation: observation({
      path: { resolved_root: REPOINTED_ROOT, resolved_path: `${REPOINTED_ROOT}/deal-room-read` },
    }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  assert.equal(result.check_states.registry_entry_integrity.state, "violated");
  assert.equal(result.check_states.path_resolution.state, "not_reached");
  assert.notEqual(result.check_states.registry_entry_integrity.recomputed_entry_digest,
    result.check_states.registry_entry_integrity.sealed_entry_digest);
});

test("clause 1: a registry entry relabelled after sealing refuses", () => {
  const relabelled = { ...registryEntry(), executable_label: "something-else" };
  const result = admit({
    registry: registry({ entries: { [COMMAND_ID]: relabelled } }),
    observation: observation({ executable_label: "something-else" }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  assert.equal(result.check_states.registry_entry_integrity.state, "violated");
  assert.equal(result.check_states.executable_substitution.state, "not_reached");
});

test("clause 1: an entry sealed for one command and filed under another refuses", () => {
  const OTHER_COMMAND_ID = "deal-room.write";
  const result = admit({
    registry: registry({ entries: { [OTHER_COMMAND_ID]: registryEntry() } }),
    observation: observation({ command_id: OTHER_COMMAND_ID }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  assert.equal(result.check_states.command_registration.state, "satisfied");
  assert.equal(result.check_states.executable_substitution.state, "not_reached");
});


test("clause 2: a command not reached relative to a held directory descriptor refuses", () => {
  for (const resolution of ["path_lookup", "absolute_path"]) {
    const result = admit({ observation: observation({ path: { resolution } }) });
    assert.equal(result.decision, "refuse", resolution);
    assert.equal(result.reason_id, "path_not_descriptor_relative", resolution);
    assert.equal(result.check_states.path_resolution.resolution, resolution);
  }
  const unstated = admit({ observation: observation({ path: { resolution: null } }) });
  assert.equal(unstated.reason_id, "path_resolution_unstated");
  assert.equal(unstated.check_states.path_resolution.state, "unobservable");
});

test("clause 2: a path resolving outside its declared root refuses, prefix siblings included", () => {
  const outside = admit({
    observation: observation({
      path: { resolved_root: "/opt/carr/supervisor/other", resolved_path: "/opt/carr/supervisor/other/x" },
    }),
  });
  assert.equal(outside.decision, "refuse");
  assert.equal(outside.reason_id, "path_outside_declared_root");

  const prefixSibling = admit({
    observation: observation({ path: { resolved_path: DECLARED_ROOT + "X/deal-room-read" } }),
  });
  assert.equal(prefixSibling.decision, "refuse");
  assert.equal(prefixSibling.reason_id, "path_outside_declared_root");

  const unresolved = admit({
    observation: observation({ path: { resolved_path: DECLARED_ROOT + "/../etc/deal-room-read" } }),
  });
  assert.equal(unresolved.decision, "refuse");
  assert.equal(unresolved.reason_id, "path_outside_declared_root");
});


test("clause 3: a followed symbolic link refuses", () => {
  const result = admit({ observation: observation({ path: { symlink_followed: true } }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "symlink_followed");
  assert.equal(result.blocking_check, "symlink");
  assert.equal(result.check_states.symlink.state, "violated");
});

test("clause 3: an observation silent about link following is unobservable and blocks", () => {
  for (const path of [{ symlink_followed: null }, { symlink_followed: undefined }]) {
    const result = admit({ observation: observation({ path }) });
    assert.equal(result.decision, "refuse");
    assert.equal(result.reason_id, "symlink_state_unobservable");
    assert.equal(result.check_states.symlink.state, "unobservable");
    assert.notEqual(result.check_states.symlink.state, "satisfied");
    assert.notEqual(result.check_states.symlink.state, "not_reached");
  }
});


test("clause 4: a link count other than one, and an unstated link count, both refuse", () => {
  for (const link_count of [0, 2, 17]) {
    const result = admit({ observation: observation({ path: { link_count } }) });
    assert.equal(result.decision, "refuse", String(link_count));
    assert.equal(result.reason_id, "link_count_not_single", String(link_count));
    assert.equal(result.check_states.link_count.state, "violated");
    assert.equal(result.check_states.link_count.required_link_count, V5_REQUIRED_LINK_COUNT);
  }
  const unstated = admit({ observation: observation({ path: { link_count: null } }) });
  assert.equal(unstated.decision, "refuse");
  assert.equal(unstated.reason_id, "link_count_unstated");
  assert.equal(unstated.check_states.link_count.state, "unobservable");

  assert.equal(admit({ observation: observation({ path: { link_count: 1 } }) }).decision, "allow");
});


test("clause 5: a consumed nonce, an absent nonce and an unknown nonce state all refuse", () => {
  const consumed = admit({
    observation: observation({ nonce: { nonce_id: "nonce-0001", state: "consumed" } }),
  });
  assert.equal(consumed.decision, "refuse");
  assert.equal(consumed.reason_id, "nonce_already_consumed");
  assert.equal(consumed.blocking_check, "nonce_replay");

  const absent = admit({ observation: observation({ nonce: null }) });
  assert.equal(absent.decision, "refuse");
  assert.equal(absent.reason_id, "nonce_absent");

  const unknown = admit({
    observation: observation({ nonce: { nonce_id: "nonce-0001", state: "unknown" } }),
  });
  assert.equal(unknown.decision, "refuse");
  assert.equal(unknown.reason_id, "nonce_state_unobservable");
  assert.equal(unknown.check_states.nonce_replay.state, "unobservable");
});

test("clause 5: the module says plainly that it decides on the claim and holds no lock", () => {
  const result = admit();
  assert.equal(result.enforces_single_use_lock, false);
  assert.equal(result.decided_on_caller_supplied_consumption_claim, true);
  assert.equal(v5CommandSupervisorPolicyPreimage().enforces_single_use_lock, false);
  assert.equal(admit().decision, "allow");
  assert.equal(admit().decision, "allow");
});


test("clause 6: a counter that has not advanced, regressed, is unstated or is a different counter refuses", () => {
  const cases = [
    [{ admitted: { counter_id: "supervisor-generation", value: 41 },
      proposed: { counter_id: "supervisor-generation", value: 41 } },
    "rollback_counter_unchanged", "unchanged", "violated"],
    [{ admitted: { counter_id: "supervisor-generation", value: 41 },
      proposed: { counter_id: "supervisor-generation", value: 40 } },
    "rollback_counter_regressed", "regressed", "violated"],
    [{ admitted: { counter_id: "supervisor-generation", value: 41 },
      proposed: { counter_id: "some-other-counter", value: 9001 } },
    "rollback_counter_uncomparable", "uncomparable", "unobservable"],
    [{ admitted: { counter_id: "supervisor-generation", value: 41 }, proposed: null },
      "rollback_counter_unstated", "unstated", "unobservable"],
    [null, "rollback_counter_unstated", "unstated", "unobservable"],
  ];
  for (const [rollback, reason_id, counterState, checkState] of cases) {
    const result = admit({ observation: observation({ rollback }) });
    assert.equal(result.decision, "refuse", reason_id);
    assert.equal(result.reason_id, reason_id);
    assert.equal(result.blocking_check, "rollback_counter");
    assert.equal(result.check_states.rollback_counter.rollback_counter_state, counterState);
    assert.equal(result.check_states.rollback_counter.state, checkState);
  }
  assert.equal(admit({
    observation: observation({
      rollback: { admitted: { counter_id: "supervisor-generation", value: 41 },
        proposed: { counter_id: "supervisor-generation", value: 42 } },
    }),
  }).decision, "allow");
});

test("clause 6: the rollback vocabulary is a closed constant hashed into the preimage, not inline literals", () => {
  assert.deepEqual([...V5_ROLLBACK_COUNTER_STATES].sort(),
    ["advanced", "regressed", "unchanged", "uncomparable", "unstated"]);
  const preimage = v5CommandSupervisorPolicyPreimage();
  assert.deepEqual(preimage.rollback_counter_states, [...V5_ROLLBACK_COUNTER_STATES].sort());

  const bytes = v5CommandSupervisorPolicyCanonicalBytes();
  assert.ok(bytes.includes(
    `"rollback_counter_states":${canonicalJson([...V5_ROLLBACK_COUNTER_STATES].sort())}`));
  const widened = { ...preimage, rollback_counter_states: [...preimage.rollback_counter_states, "skipped"] };
  assert.notEqual(digest(widened), v5CommandSupervisorPolicyDigest());
  assert.equal(digest(preimage), v5CommandSupervisorPolicyDigest());
  for (const state of ["advanced", "regressed", "uncomparable", "unchanged", "unstated"]) {
    assert.ok(V5_ROLLBACK_COUNTER_STATES.includes(state));
  }
  assert.throws(() => normalizeCommandExecutionObservation({
    command_id: COMMAND_ID,
    path: { resolution: "descriptor_relative" },
    rollback: { admitted: null, proposed: null, state: "advanced" },
  }), boundaryError("unknown_field"));
  assert.equal(preimage.caller_may_name_rollback_state, false);
});


test("clause 7: in safe mode every command refuses with its own distinct reason id", () => {
  const result = admit({ capability: capability({ mode: "safe" }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "safe_mode_refuses_all_commands");
  assert.equal(result.blocking_check, "supervisor_mode");
  assert.notEqual(result.reason_id, "capability_revoked");
  assert.notEqual(result.reason_id, "command_not_registered");
  assert.notEqual(result.reason_id, "admitted_after_all_supervisor_negatives_cleared");

  const dirtyToo = admit({
    capability: capability({ mode: "safe" }),
    observation: observation({ executable_digest: SUBSTITUTED_BYTES, path: { symlink_followed: true } }),
  });
  assert.equal(dirtyToo.reason_id, "safe_mode_refuses_all_commands");
  assert.equal(dirtyToo.check_states.executable_substitution.state, "not_reached");
  assert.equal(dirtyToo.check_states.symlink.state, "not_reached");
});

test("clause 7: a revoked capability refuses before any digest is read", () => {
  for (const [state, reason_id] of [
    ["revoked", "capability_revoked"], ["expired", "capability_expired"], ["unissued", "capability_unissued"],
  ]) {
    const result = admit({
      capability: capability({ state }),
      observation: observation({ executable_digest: SUBSTITUTED_BYTES }),
    });
    assert.equal(result.decision, "refuse", state);
    assert.equal(result.reason_id, reason_id, state);
    assert.equal(result.blocking_check, "capability_state");
    assert.equal(result.check_states.registry_entry_integrity.state, "not_reached");
    assert.equal(result.check_states.executable_substitution.state, "not_reached");
    assert.equal(result.check_states.executable_substitution.blocked_by, "capability_state");
  }
  const digestReaders = ["registry_entry_integrity", "executable_substitution"];
  for (const door of V5_CHECKS_BEFORE_ANY_DIGEST_IS_READ) {
    for (const reader of digestReaders) {
      assert.ok(V5_SUPERVISOR_ADMISSION_CHECKS.indexOf(door) <
        V5_SUPERVISOR_ADMISSION_CHECKS.indexOf(reader), `${door} must precede ${reader}`);
    }
  }
  const otherHolder = admit({
    capability: capability({ issued_to: "dell-local" }),
    observation: observation({ executable_digest: SUBSTITUTED_BYTES }),
  });
  assert.equal(otherHolder.reason_id, "capability_holder_mismatch");
  assert.equal(otherHolder.check_states.executable_substitution.state, "not_reached");
});


test("clause 8: an unregistered command refuses at the door and no caller may name which checks apply", () => {
  const result = admit({ observation: observation({ command_id: "not-registered.verb" }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "command_not_registered");
  assert.equal(result.blocking_check, "command_registration");
  assert.equal(result.command.registered, false);
  for (const check of ["registry_entry_integrity", "executable_substitution", "path_resolution",
    "symlink", "link_count", "nonce_replay", "rollback_counter"]) {
    assert.equal(result.check_states[check].state, "not_reached", check);
    assert.equal(result.check_states[check].reason_id, null, check);
  }

  for (const smuggled of [
    { enforced_checks: ["symlink"] }, { skip_checks: ["nonce_replay"] }, { trusted: true },
    { enforced_axes: ["code"] },
  ]) {
    assert.throws(() => evaluateCommandSupervisorAdmission({
      actor: actor(), registry: registry(), capability: capability(),
      observation: observation(), version_compatibility: versionAnswer(), ...smuggled,
    }), boundaryError("unknown_field"), Object.keys(smuggled)[0]);
  }
  assert.equal(v5CommandSupervisorPolicyPreimage().caller_may_select_checks, false);
});


test("clause 9: every result carries the three non-grants and the no-effects marker", () => {
  const results = [
    admit(),
    admit({ capability: capability({ mode: "safe" }) }),
    admit({ capability: capability({ state: "revoked" }) }),
    admit({ observation: observation({ command_id: "not-registered.verb" }) }),
    admit({ observation: observation({ executable_digest: SUBSTITUTED_BYTES }) }),
    admit({ observation: observation({ path: { symlink_followed: true } }) }),
    admit({ observation: observation({ path: { link_count: 3 } }) }),
    admit({ observation: observation({ nonce: null }) }),
    admit({ observation: observation({ rollback: null }) }),
    admit({ version_compatibility: null }),
  ];
  assert.ok(results.some(result => result.decision === "allow"));
  assert.ok(results.some(result => result.decision === "refuse"));
  for (const result of results) {
    assert.equal(result.authenticated, false);
    assert.equal(result.authorizes_command_dispatch, false);
    assert.equal(result.runtime_admission_granted, false);
    assert.equal(result.launches_process, false);
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.equal(result.direct_credential_attempt_decided, false);
    assert.equal(result.direct_credential_clause_seam, V5_DIRECT_CREDENTIAL_CLAUSE_SEAM);
    assert.ok(V5_COMMAND_SUPERVISOR_REASON_IDS.includes(result.reason_id));
    for (const check of V5_SUPERVISOR_ADMISSION_CHECKS) {
      assert.ok(V5_ADMISSION_CHECK_STATES.includes(result.check_states[check].state), check);
    }
  }
});


test("clause 10: the policy preimage is closed and versioned, with both state vocabularies enumerated", () => {
  const preimage = v5CommandSupervisorPolicyPreimage();
  assert.equal(preimage.schema_version, V5_COMMAND_SUPERVISOR_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_COMMAND_SUPERVISOR_POLICY_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(preimage.checks_in_order, [...V5_SUPERVISOR_ADMISSION_CHECKS]);

  assert.deepEqual(preimage.check_states, [...V5_ADMISSION_CHECK_STATES].sort());
  assert.deepEqual(preimage.rollback_counter_states, [...V5_ROLLBACK_COUNTER_STATES].sort());
  assert.deepEqual(preimage.supervisor_modes, [...V5_SUPERVISOR_MODES].sort());
  assert.deepEqual(preimage.capability_states, [...V5_CAPABILITY_STATES].sort());
  assert.deepEqual(preimage.nonce_states, [...V5_NONCE_STATES].sort());
  assert.deepEqual(preimage.path_resolution_modes, [...V5_PATH_RESOLUTION_MODES].sort());
  assert.deepEqual(preimage.reason_ids, [...V5_COMMAND_SUPERVISOR_REASON_IDS].sort());
  assert.equal(preimage.admissible_path_resolution, V5_ADMISSIBLE_PATH_RESOLUTION);
  assert.equal(preimage.label_may_outvote_digest, false);
  assert.equal(preimage.unstated_observation_blocks, true);
  assert.equal(preimage.first_unsatisfied_check_decides, true);
  assert.equal(preimage.recomputes_version_compatibility, false);
  assert.equal(preimage.grants_runtime_admission, false);

  assert.deepEqual(Object.keys(preimage).sort(), [
    "admissible_path_resolution", "authenticates", "authorizes_command_dispatch",
    "caller_may_name_rollback_state", "caller_may_select_checks", "capability_states",
    "check_states", "checks_before_any_digest_is_read", "checks_in_order",
    "decides_direct_credential_attempts", "direct_credential_clause",
    "direct_credential_clause_blocking_decision_ids", "direct_credential_clause_seam",
    "enforces_single_use_lock", "first_unsatisfied_check_decides", "grants_runtime_admission",
    "label_may_outvote_digest", "launches_process", "nonce_states", "path_resolution_modes",
    "policy_version", "reason_ids", "recomputes_version_compatibility", "registry_entry_kind",
    "required_link_count", "rollback_counter_states", "schema_version", "supervisor_modes",
    "tenant", "unstated_observation_blocks", "version_compatibility_is_an_input",
  ]);

  for (const key of ["check_states", "rollback_counter_states", "supervisor_modes", "capability_states",
    "nonce_states", "path_resolution_modes", "reason_ids",
    "direct_credential_clause_blocking_decision_ids"]) {
    assert.deepEqual(preimage[key], [...preimage[key]].sort(), key);
  }
});

test("clause 10: the policy digest is deterministic and taken over the exact canonical bytes", () => {
  const expected = "sha256:" + createHash("sha256")
    .update(v5CommandSupervisorPolicyCanonicalBytes()).digest("hex");
  assert.equal(v5CommandSupervisorPolicyDigest(), expected);
  assert.equal(v5CommandSupervisorPolicyDigest(), v5CommandSupervisorPolicyDigest());
  admit();
  admit({ capability: capability({ mode: "safe" }) });
  assert.equal(v5CommandSupervisorPolicyDigest(), expected);
});


test("the four-axis compatibility answer is an input, read and never recomputed", () => {
  const skewed = versionAnswer({ clientCodeDigest: "b".repeat(40) });
  assert.equal(skewed.decision, "refuse");
  const result = admit({ version_compatibility: skewed });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "command_version_incompatible");
  assert.equal(result.check_states.command_version_compatibility.version_reason_id, skewed.reason_id);

  const absent = admit({ version_compatibility: null });
  assert.equal(absent.reason_id, "command_version_answer_unobservable");
  assert.equal(absent.check_states.command_version_compatibility.state, "unobservable");

  assert.throws(() => admit({ version_compatibility: { schema_version: "something-else", decision: "allow" } }),
    boundaryError("foreign_version_answer"));
  assert.throws(() => admit({
    version_compatibility: { ...versionAnswer(), runtime_admission_granted: true },
  }), boundaryError("foreign_version_answer"));
  assert.throws(() => admit({
    version_compatibility: { ...versionAnswer(), reason_id: "invented_reason" },
  }), boundaryError("foreign_version_answer"));
});

test("the direct-credential clause is deferred by name, never guessed", () => {
  assert.equal(V5_DIRECT_CREDENTIAL_CLAUSE_SEAM,
    "step:v5-f07-direct-credential-refusal-boundary-decision");
  assert.deepEqual([...V5_DIRECT_CREDENTIAL_BLOCKING_DECISION_IDS], ["Q055.D1", "Q056.D1"]);
  const preimage = v5CommandSupervisorPolicyPreimage();
  assert.equal(preimage.direct_credential_clause, "deferred");
  assert.equal(preimage.decides_direct_credential_attempts, false);
  assert.deepEqual(preimage.direct_credential_clause_blocking_decision_ids,
    [...V5_DIRECT_CREDENTIAL_BLOCKING_DECISION_IDS].sort());

  const source = readFileSync(SRC_PATH, "utf8");
  const header = source.slice(0, source.indexOf("\nimport "));
  assert.ok(/DIRECT-CREDENTIAL HALF IS NOT/.test(header));
  assert.ok(header.includes("Q055.D1") && header.includes("Q056.D1"));

  assert.throws(() => evaluateCommandSupervisorAdmission({
    actor: actor(), registry: registry(), capability: capability(), observation: observation(),
    version_compatibility: versionAnswer(), direct_credential: { presented: true },
  }), boundaryError("unknown_field"));
});

test("two kinds of no: policy answers are returned and contract violations throw", () => {
  assert.equal(admit({ observation: observation({ path: { symlink_followed: null } }) }).decision, "refuse");

  assert.throws(() => normalizeSupervisorCapability({
    capability_id: "c1", issued_to: HOLDER, mode: "paranoid", state: "active",
  }), boundaryError("unknown_supervisor_mode"));
  assert.throws(() => normalizeSupervisorCapability({
    capability_id: "c1", issued_to: HOLDER, mode: "normal", state: "probably_fine",
  }), boundaryError("unknown_capability_state"));
  assert.throws(() => normalizeCommandExecutionObservation({
    command_id: COMMAND_ID, path: { resolution: "vibes" },
  }), boundaryError("unknown_path_resolution_mode"));
  assert.throws(() => normalizeCommandExecutionObservation({
    command_id: COMMAND_ID, path: { resolution: "descriptor_relative" },
    nonce: { nonce_id: "n", state: "maybe" },
  }), boundaryError("unknown_nonce_state"));
  assert.throws(() => normalizeCommandSupervisorRegistry({
    registry_digest: REGISTRY_DIGEST,
    entries: { [COMMAND_ID]: { ...registryEntry(), declared_root: "relative/root" } },
  }), boundaryError("invalid_path"));
  assert.throws(() => normalizeCommandSupervisorRegistry({
    registry_digest: REGISTRY_DIGEST,
    entries: { [COMMAND_ID]: { ...registryEntry(), executable_digest: "deadbeef" } },
  }), boundaryError("invalid_digest"));
  assert.throws(() => evaluateCommandSupervisorAdmission({
    actor: { slug: HOLDER, authority: "root" }, registry: registry(), capability: capability(),
    observation: observation(), version_compatibility: versionAnswer(),
  }), boundaryError("unknown_field"));
  assert.throws(() => admit({ registry: { ...registry(), report_kind: "not_a_registry" } }),
    boundaryError("unnormalized_report"));
});

test("normalized reports and results are frozen, and reading one rewrites nothing", () => {
  const result = admit();
  assert.ok(Object.isFrozen(result));
  assert.ok(Object.isFrozen(result.check_states.symlink));
  assert.ok(Object.isFrozen(observation()));
  assert.ok(Object.isFrozen(registry().entries[COMMAND_ID]));
  assert.throws(() => { result.decision = "allow"; }, TypeError);
  assert.throws(() => { result.checks_required.push("nothing"); }, TypeError);
  assert.equal(admit().decision, "allow");
});

test("the module launches nothing and reads no clock, filesystem, network or environment", () => {
  const source = readFileSync(SRC_PATH, "utf8");
  for (const pattern of [
    /\bnode:fs\b/, /\bnode:net\b/, /\bnode:http\b/, /\bnode:https\b/, /\bnode:child_process\b/,
    /\bnode:worker_threads\b/, /\bspawn\w*\s*\(/, /\bexec\w*\s*\(/, /\bfetch\s*\(/,
    /\bprocess\.env\b/, /\bnew Date\b/, /\bDate\.now\b/, /\bsetTimeout\b/, /\bsetInterval\b/,
    /\brequire\s*\(/, /\bimport\s*\(/, /\bglobalThis\b/,
  ]) {
    assert.ok(!pattern.test(source), `module source must not contain ${pattern}`);
  }
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)].map(match => match[1]).sort();
  assert.deepEqual(imports, [
    "./artifact-trust.js", "./command-version-compatibility.v5.js",
    "./global-boundaries.v5.js", "./identity.js",
  ], "the evaluator reuses the existing kernels and keeps no second actor registry");
});
