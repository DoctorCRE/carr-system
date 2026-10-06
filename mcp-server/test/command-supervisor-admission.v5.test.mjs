// V5-F07 phase 2 — the supervisor admission decision, proved clause by clause.
//
// The positive case comes first on purpose: a rule that only ever refuses
// cannot be told apart from a broken one, so every negative below is a single
// named mutation of ONE clean request that allows.
//
// The four-axis compatibility answer this suite feeds in is a REAL answer from
// command-version-compatibility.v5.js, built through that module's own
// normalizers, not a hand-written stand-in — so if that module's answer shape
// changes, this suite breaks rather than the wiring silently rotting.
//
//   node --test mcp-server/test/command-supervisor-admission.v5.test.mjs
//                mcp-server/test/command-version-compatibility.v5.test.mjs
//                mcp-server/test/global-boundaries.v5.test.mjs

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

// ---------------------------------------------------------------------------
// The four-axis answer, from the real comparator.
// ---------------------------------------------------------------------------

/** A /release payload in the shape release.js emits, read by the real adapter. */
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

// ---------------------------------------------------------------------------
// One clean request, and the single-field mutations of it.
// ---------------------------------------------------------------------------

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

/** The one clean request every negative below mutates by exactly one field. */
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

// ---------------------------------------------------------------------------
// The positive.
// ---------------------------------------------------------------------------

test("positive: a clean observation clears every registered negative and allows", () => {
  const result = admit();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "admitted_after_all_supervisor_negatives_cleared");
  assert.equal(result.blocking_check, null);
  assert.deepEqual(result.checks_satisfied, [...V5_SUPERVISOR_ADMISSION_CHECKS]);
  assert.deepEqual(result.checks_not_reached, []);
  assert.equal(result.command.registered, true);
  assert.equal(result.supervisor.actor_slug, HOLDER);
  // Read from identity.js rather than re-derived here.
  assert.equal(result.supervisor.actor_authorization_class, "sponsored_agent");
});

// ---------------------------------------------------------------------------
// Clause 1 — substitution.
// ---------------------------------------------------------------------------

test("clause 1: substituted executable bytes refuse, and a matching label does not rescue them", () => {
  const result = admit({
    observation: observation({ executable_digest: SUBSTITUTED_BYTES, executable_label: "deal-room-read" }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "executable_digest_substituted");
  assert.equal(result.blocking_check, "executable_substitution");
  const state = result.check_states.executable_substitution;
  assert.equal(state.state, "violated");
  // The label matched exactly and changed nothing: a label never outvotes a digest.
  assert.equal(state.label_matched, true);
  assert.equal(state.registered_digest, REGISTERED_BYTES);
  assert.equal(state.observed_digest, SUBSTITUTED_BYTES);

  // And an observation that reports no bytes at all is unobservable, not an allow.
  const silent = admit({ observation: observation({ executable_digest: null }) });
  assert.equal(silent.decision, "refuse");
  assert.equal(silent.reason_id, "executable_digest_unobservable");
  assert.equal(silent.check_states.executable_substitution.state, "unobservable");
});

test("clause 1: a registry entry whose own sealed digest has moved refuses", () => {
  // The entry is sealed against ONE executable digest and then re-points at
  // another without resealing — the registry-side half of substitution.
  const moved = { ...registryEntry(), executable_digest: SUBSTITUTED_BYTES };
  const result = admit({
    registry: registry({ entries: { [COMMAND_ID]: moved } }),
    observation: observation({ executable_digest: SUBSTITUTED_BYTES }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  // It refuses BEFORE the substitution check, so a re-pointed entry cannot be
  // laundered by observing the bytes it now names.
  assert.equal(result.check_states.executable_substitution.state, "not_reached");
  assert.notEqual(result.check_states.registry_entry_integrity.recomputed_entry_digest,
    result.check_states.registry_entry_integrity.sealed_entry_digest);
});

// ---------------------------------------------------------------------------
// The seal itself — pinned, and proved sensitive field by field.
//
// registryEntry() builds sealed_entry_digest by calling the module's own
// commandSupervisorRegistryEntryDigest over the fields the entry then carries.
// On its own that fixture would agree with a seal function that returned a
// constant, or that quietly stopped hashing one of its fields: the fixture and
// the check would both use the broken function and both be wrong the same way.
//
// The tests below remove that agreement. The first pins the seal to bytes
// written out by hand and to the literal sha256 of those bytes, so nothing on
// the asserting side comes from the function under test. The second proves the
// seal moves when ANY field it covers moves. The three after them each take a
// field OUT of the seal's coverage in the only way a test can — mutate it after
// sealing — and each is shaped so that if that field left the hashed object,
// the request would ALLOW.
// ---------------------------------------------------------------------------

/**
 * The exact canonical bytes the clean fixture entry's seal is taken over,
 * written out by hand rather than read back from the module. Every field the
 * seal covers is visible here in the order canonicalJson emits it, so a field
 * the source stops hashing simply stops matching this literal.
 */
const SEALED_ENTRY_BYTES = "{\"command_id\":\"deal-room.read\","
  + "\"declared_root\":\"/opt/carr/supervisor/handlers\","
  + "\"entry_kind\":\"command-supervisor-registry-entry.v1\","
  + "\"executable_digest\":\"sha256:" + "1".repeat(64) + "\","
  + "\"executable_label\":\"deal-room-read\","
  + "\"schema_version\":\"doctorcre-v5-command-supervisor-admission.v1\"}";

/** The sha256 of exactly those bytes, computed once and written down. */
const SEALED_ENTRY_DIGEST = "sha256:99c64b2289a70d9eee522c81ace36fa3df4e40de77430996258893ef13bf4f8b";

/** The clean sealed fields, as a plain object the seal can be recomputed over. */
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
  // The two literals agree with each other without the module being consulted
  // at all, so this line stands even if every export in the module is broken.
  assert.equal("sha256:" + createHash("sha256").update(SEALED_ENTRY_BYTES).digest("hex"),
    SEALED_ENTRY_DIGEST);

  // And the module's seal is the seal of exactly those bytes. A seal that
  // returned a constant, or that stopped hashing any one of schema_version,
  // entry_kind, command_id, executable_digest, executable_label or
  // declared_root, fails here: nothing on the expected side of this assertion
  // is computed by the function under test.
  assert.equal(commandSupervisorRegistryEntryDigest(sealedFields()), SEALED_ENTRY_DIGEST);

  // The literal names the sealed SHAPE, so a reader can see what is covered
  // without reading the source, and a field added to or dropped from the seal
  // changes this list rather than passing unremarked.
  assert.deepEqual(Object.keys(JSON.parse(SEALED_ENTRY_BYTES)).sort(),
    ["command_id", "declared_root", "entry_kind", "executable_digest",
      "executable_label", "schema_version"]);
});

test("clause 1: every field the registry-entry seal covers moves the seal", () => {
  // Field by field. A field that is not hashed cannot move the digest, so an
  // omission fails here BY NAME rather than being absorbed. This is the
  // property the literal pin above is the belt for.
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
  // An entry with no label is not the same entry as a labelled one: silence
  // about the label is a distinct sealed value, not a wildcard.
  assert.notEqual(commandSupervisorRegistryEntryDigest(sealedFields({ executable_label: null })),
    SEALED_ENTRY_DIGEST);
  // And the clean fields still seal to the clean seal, so the loop above is
  // failing on the mutation rather than on a fixture that never matched.
  assert.equal(commandSupervisorRegistryEntryDigest(sealedFields()), SEALED_ENTRY_DIGEST);
});

test("clause 1: a registry entry re-pointed at another root after sealing refuses", () => {
  // declared_root is the anchor path resolution compares against, and the
  // comparison is exact equality (src checkPathResolution), so an unsealed root
  // would let a signed entry be re-pointed at a different directory and the
  // observation simply report that directory. The seal is taken over the
  // handlers root; the entry is then overwritten to name another one.
  const REPOINTED_ROOT = "/opt/carr/supervisor/other";
  const repointed = { ...registryEntry(), declared_root: REPOINTED_ROOT };
  const result = admit({
    registry: registry({ entries: { [COMMAND_ID]: repointed } }),
    observation: observation({
      path: { resolved_root: REPOINTED_ROOT, resolved_path: `${REPOINTED_ROOT}/deal-room-read` },
    }),
  });
  // Every other check on this request is clean — the observed path really is
  // inside the root the entry now names — so if declared_root left the hashed
  // object this request would ALLOW. The seal is the only thing refusing it.
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  assert.equal(result.check_states.registry_entry_integrity.state, "violated");
  // It refuses BEFORE path resolution, so the re-pointed root never gets to
  // decide whether the observed path is inside it.
  assert.equal(result.check_states.path_resolution.state, "not_reached");
  assert.notEqual(result.check_states.registry_entry_integrity.recomputed_entry_digest,
    result.check_states.registry_entry_integrity.sealed_entry_digest);
});

test("clause 1: a registry entry relabelled after sealing refuses", () => {
  // executable_label is the label the module carries into its answer as the one
  // that must never outvote a digest. An unsealed label could be rewritten on a
  // signed entry, and since the digests still match, executable_substitution
  // would be satisfied and the request would ALLOW.
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
  // command_id is sealed too, and on a normalized registry it comes from the
  // entry's KEY rather than from the entry body — so the way to move it after
  // sealing is to file the sealed entry somewhere else. Unsealed, this is a
  // signed entry for one command silently answering for a different one, and
  // every other check would pass.
  const OTHER_COMMAND_ID = "deal-room.write";
  const result = admit({
    registry: registry({ entries: { [OTHER_COMMAND_ID]: registryEntry() } }),
    observation: observation({ command_id: OTHER_COMMAND_ID }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "registry_entry_digest_moved");
  assert.equal(result.blocking_check, "registry_entry_integrity");
  // The command IS registered under that key — this is not an unregistered
  // command wearing a different reason id.
  assert.equal(result.check_states.command_registration.state, "satisfied");
  assert.equal(result.check_states.executable_substitution.state, "not_reached");
});

// ---------------------------------------------------------------------------
// Clause 2 — path.
// ---------------------------------------------------------------------------

test("clause 2: a command not reached relative to a held directory descriptor refuses", () => {
  for (const resolution of ["path_lookup", "absolute_path"]) {
    const result = admit({ observation: observation({ path: { resolution } }) });
    assert.equal(result.decision, "refuse", resolution);
    assert.equal(result.reason_id, "path_not_descriptor_relative", resolution);
    assert.equal(result.check_states.path_resolution.resolution, resolution);
  }
  // An unstated resolution mode blocks rather than defaulting to the good one.
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

  // The boundary case that a string prefix gets wrong: "/opt/carr/supervisor/handlersX"
  // starts with the declared root and is a DIFFERENT directory.
  const prefixSibling = admit({
    observation: observation({ path: { resolved_path: DECLARED_ROOT + "X/deal-room-read" } }),
  });
  assert.equal(prefixSibling.decision, "refuse");
  assert.equal(prefixSibling.reason_id, "path_outside_declared_root");

  // A path still carrying a relative segment has not been resolved at all.
  const unresolved = admit({
    observation: observation({ path: { resolved_path: DECLARED_ROOT + "/../etc/deal-room-read" } }),
  });
  assert.equal(unresolved.decision, "refuse");
  assert.equal(unresolved.reason_id, "path_outside_declared_root");
});

// ---------------------------------------------------------------------------
// Clause 3 — symlink.
// ---------------------------------------------------------------------------

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
    // The distinction that matters: unobservable is its own state and its own
    // reason, and it is NOT the default-allow of a check that did not run.
    assert.equal(result.check_states.symlink.state, "unobservable");
    assert.notEqual(result.check_states.symlink.state, "satisfied");
    assert.notEqual(result.check_states.symlink.state, "not_reached");
  }
});

// ---------------------------------------------------------------------------
// Clause 4 — multi-link.
// ---------------------------------------------------------------------------

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

  // And exactly one link is the only count that passes.
  assert.equal(admit({ observation: observation({ path: { link_count: 1 } }) }).decision, "allow");
});

// ---------------------------------------------------------------------------
// Clause 5 — nonce and replay.
// ---------------------------------------------------------------------------

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
  // Stated in the answer, not only in a comment, so a consumer that records the
  // result records the limit with it.
  const result = admit();
  assert.equal(result.enforces_single_use_lock, false);
  assert.equal(result.decided_on_caller_supplied_consumption_claim, true);
  assert.equal(v5CommandSupervisorPolicyPreimage().enforces_single_use_lock, false);
  // Two evaluations of the same unconsumed claim agree: nothing was spent here.
  assert.equal(admit().decision, "allow");
  assert.equal(admit().decision, "allow");
});

// ---------------------------------------------------------------------------
// Clause 6 — monotonic anti-rollback.
// ---------------------------------------------------------------------------

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
  // Only a strictly advanced counter on the SAME counter passes.
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

  // The whole vocabulary is IN the bytes that are hashed, verbatim — not merely
  // declared somewhere in the module. This is the assertion that fails if the
  // states go back to being inline literals at the comparison site.
  const bytes = v5CommandSupervisorPolicyCanonicalBytes();
  assert.ok(bytes.includes(
    `"rollback_counter_states":${canonicalJson([...V5_ROLLBACK_COUNTER_STATES].sort())}`));
  // And the digest is a FUNCTION of that list: one extra state through the same
  // canonicalizer must not reproduce the shipped digest.
  const widened = { ...preimage, rollback_counter_states: [...preimage.rollback_counter_states, "skipped"] };
  assert.notEqual(digest(widened), v5CommandSupervisorPolicyDigest());
  assert.equal(digest(preimage), v5CommandSupervisorPolicyDigest());
  // Every state the evaluator can produce is in the closed list.
  for (const state of ["advanced", "regressed", "uncomparable", "unchanged", "unstated"]) {
    assert.ok(V5_ROLLBACK_COUNTER_STATES.includes(state));
  }
  // The caller cannot name the state; there is no field for it.
  assert.throws(() => normalizeCommandExecutionObservation({
    command_id: COMMAND_ID,
    path: { resolution: "descriptor_relative" },
    rollback: { admitted: null, proposed: null, state: "advanced" },
  }), boundaryError("unknown_field"));
  assert.equal(preimage.caller_may_name_rollback_state, false);
});

// ---------------------------------------------------------------------------
// Clause 7 — safe mode and revocation.
// ---------------------------------------------------------------------------

test("clause 7: in safe mode every command refuses with its own distinct reason id", () => {
  // The request is otherwise the clean one that allows.
  const result = admit({ capability: capability({ mode: "safe" }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "safe_mode_refuses_all_commands");
  assert.equal(result.blocking_check, "supervisor_mode");
  // Distinct: it is not any other check's reason, and in particular not the
  // revocation reason that a lazier implementation would reuse.
  assert.notEqual(result.reason_id, "capability_revoked");
  assert.notEqual(result.reason_id, "command_not_registered");
  assert.notEqual(result.reason_id, "admitted_after_all_supervisor_negatives_cleared");

  // Safe mode refuses a request whose OTHER facts are all clean, and a request
  // whose other facts are dirty, with the SAME reason: it is not a fallthrough.
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
      // Bytes that would fail substitution: proving the digest is never reached.
      observation: observation({ executable_digest: SUBSTITUTED_BYTES }),
    });
    assert.equal(result.decision, "refuse", state);
    assert.equal(result.reason_id, reason_id, state);
    assert.equal(result.blocking_check, "capability_state");
    assert.equal(result.check_states.registry_entry_integrity.state, "not_reached");
    assert.equal(result.check_states.executable_substitution.state, "not_reached");
    assert.equal(result.check_states.executable_substitution.blocked_by, "capability_state");
  }
  // Structural, not incidental: the three door checks precede every check that
  // reads a digest, in the module's own declared order.
  const digestReaders = ["registry_entry_integrity", "executable_substitution"];
  for (const door of V5_CHECKS_BEFORE_ANY_DIGEST_IS_READ) {
    for (const reader of digestReaders) {
      assert.ok(V5_SUPERVISOR_ADMISSION_CHECKS.indexOf(door) <
        V5_SUPERVISOR_ADMISSION_CHECKS.indexOf(reader), `${door} must precede ${reader}`);
    }
  }
  // A capability issued to somebody else is the same door.
  const otherHolder = admit({
    capability: capability({ issued_to: "dell-local" }),
    observation: observation({ executable_digest: SUBSTITUTED_BYTES }),
  });
  assert.equal(otherHolder.reason_id, "capability_holder_mismatch");
  assert.equal(otherHolder.check_states.executable_substitution.state, "not_reached");
});

// ---------------------------------------------------------------------------
// Clause 8 — unknown command.
// ---------------------------------------------------------------------------

test("clause 8: an unregistered command refuses at the door and no caller may name which checks apply", () => {
  const result = admit({ observation: observation({ command_id: "not-registered.verb" }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "command_not_registered");
  assert.equal(result.blocking_check, "command_registration");
  assert.equal(result.command.registered, false);
  // At the door: nothing downstream of registration was evaluated, so the
  // refusal cannot be mined for which negatives a registered command faces.
  for (const check of ["registry_entry_integrity", "executable_substitution", "path_resolution",
    "symlink", "link_count", "nonce_replay", "rollback_counter"]) {
    assert.equal(result.check_states[check].state, "not_reached", check);
    assert.equal(result.check_states[check].reason_id, null, check);
  }

  // The closed-key discipline: a request cannot narrow, waive or vouch for the test.
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

// ---------------------------------------------------------------------------
// Clause 9 — every result is a non-grant.
// ---------------------------------------------------------------------------

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
    // The deferred half of the credential clause, named in every answer.
    assert.equal(result.direct_credential_attempt_decided, false);
    assert.equal(result.direct_credential_clause_seam, V5_DIRECT_CREDENTIAL_CLAUSE_SEAM);
    assert.ok(V5_COMMAND_SUPERVISOR_REASON_IDS.includes(result.reason_id));
    for (const check of V5_SUPERVISOR_ADMISSION_CHECKS) {
      assert.ok(V5_ADMISSION_CHECK_STATES.includes(result.check_states[check].state), check);
    }
  }
});

// ---------------------------------------------------------------------------
// Clause 10 — the closed, versioned policy preimage.
// ---------------------------------------------------------------------------

test("clause 10: the policy preimage is closed and versioned, with both state vocabularies enumerated", () => {
  const preimage = v5CommandSupervisorPolicyPreimage();
  assert.equal(preimage.schema_version, V5_COMMAND_SUPERVISOR_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_COMMAND_SUPERVISOR_POLICY_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(preimage.checks_in_order, [...V5_SUPERVISOR_ADMISSION_CHECKS]);

  // BOTH state vocabularies, and every other closed list this module decides
  // against. A vocabulary missing here can change without the digest moving.
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

  // The KEY SET is closed and written out by hand. Clause 10's assertions above
  // name twenty of these fields, but a field dropped from the preimage moves
  // the policy digest without any assertion in this file objecting — the same
  // shape of hole the registry seal had, asked of the other digest in the
  // module. This list is the answer: a preimage field that leaves, or arrives,
  // fails here rather than silently changing what consumers are pinned to.
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

  // Every list in the preimage is SORTED, not merely alphabetical by luck.
  for (const key of ["check_states", "rollback_counter_states", "supervisor_modes", "capability_states",
    "nonce_states", "path_resolution_modes", "reason_ids",
    "direct_credential_clause_blocking_decision_ids"]) {
    assert.deepEqual(preimage[key], [...preimage[key]].sort(), key);
  }
});

test("clause 10: the policy digest is deterministic and taken over the exact canonical bytes", () => {
  // Re-hashed by hand rather than compared against a copy of the module's own
  // answer, so the digest is checked instead of merely echoed.
  const expected = "sha256:" + createHash("sha256")
    .update(v5CommandSupervisorPolicyCanonicalBytes()).digest("hex");
  assert.equal(v5CommandSupervisorPolicyDigest(), expected);
  assert.equal(v5CommandSupervisorPolicyDigest(), v5CommandSupervisorPolicyDigest());
  // Nothing situational is bound: the digest does not move between requests.
  admit();
  admit({ capability: capability({ mode: "safe" }) });
  assert.equal(v5CommandSupervisorPolicyDigest(), expected);
});

// ---------------------------------------------------------------------------
// The reuse contract, the deferred clause, and the two kinds of no.
// ---------------------------------------------------------------------------

test("the four-axis compatibility answer is an input, read and never recomputed", () => {
  // A real refusing answer from the real comparator blocks admission.
  const skewed = versionAnswer({ clientCodeDigest: "b".repeat(40) });
  assert.equal(skewed.decision, "refuse");
  const result = admit({ version_compatibility: skewed });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "command_version_incompatible");
  assert.equal(result.check_states.command_version_compatibility.version_reason_id, skewed.reason_id);

  // No answer at all is unobservable and blocks; it is not "no news is good news".
  const absent = admit({ version_compatibility: null });
  assert.equal(absent.reason_id, "command_version_answer_unobservable");
  assert.equal(absent.check_states.command_version_compatibility.state, "unobservable");

  // A hand-built object that is not that module's answer is unreadable, and an
  // answer claiming it granted admission is unreadable too.
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

  // The header says it out loud, and names the two decisions it is waiting on.
  const source = readFileSync(SRC_PATH, "utf8");
  const header = source.slice(0, source.indexOf("\nimport "));
  assert.ok(/DIRECT-CREDENTIAL HALF IS NOT/.test(header));
  assert.ok(header.includes("Q055.D1") && header.includes("Q056.D1"));

  // No caller can smuggle the deferred question in as a request field.
  assert.throws(() => evaluateCommandSupervisorAdmission({
    actor: actor(), registry: registry(), capability: capability(), observation: observation(),
    version_compatibility: versionAnswer(), direct_credential: { presented: true },
  }), boundaryError("unknown_field"));
});

test("two kinds of no: policy answers are returned and contract violations throw", () => {
  // Returned: an honest "I cannot say" is an answer with a reason id.
  assert.equal(admit({ observation: observation({ path: { symlink_followed: null } }) }).decision, "refuse");

  // Thrown: an unreadable request is not a policy question.
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
  // A hand-built report that is incoherent is unreadable rather than refusable.
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
