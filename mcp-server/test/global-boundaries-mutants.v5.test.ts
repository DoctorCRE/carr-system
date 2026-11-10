// V5-S01 — planted-bug mutants, one or more per refusal the live door owns.
//
// A refusal nobody has watched fail is indistinguishable from a refusal that
// never ran. For each negative this file plants one realistic bug in a COPY of
// the source, loads the copy, and asserts that the probe for that negative
// catches it — after first asserting the same probe passes on the real source.
// Every anchor must occur exactly once, so an edit that moves the anchored line
// breaks this file loudly instead of letting a mutant silently become a no-op.

import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const DOOR = "global-boundaries-door.v5.js";
const POLICY = "global-boundaries.v5.js";
const WORK = mkdtempSync(join(tmpdir(), "v5-boundary-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));

let serial = 0;

/** Rewrite relative imports to absolute URLs; `overrides` swaps named files for mutant copies. */
function relink(source: string, overrides: Record<string, string> = {}) {
  return source.replace(
    /from "\.\/([^"]+)"/g,
    (_, file) => `from "${overrides[file] ?? pathToFileURL(join(SRC, file)).href}"`,
  );
}

function mutate(file: string, anchor: string, replacement: string) {
  const source = readFileSync(join(SRC, file), "utf8");
  const count = source.split(anchor).length - 1;
  assert.equal(count, 1, `mutant anchor must occur exactly once in ${file}: ${anchor}`);
  return source.replace(anchor, replacement);
}

function write(name: string, text: string) {
  serial += 1;
  const path = join(WORK, `${serial}-${name}.mjs`);
  writeFileSync(path, text);
  return pathToFileURL(path).href;
}

function record(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
function invoke(module: Record<string, unknown>, name: string, input?: unknown): unknown {
  const callable = module[name];
  assert.ok(typeof callable === "function");
  return callable(input);
}
async function loadModules(doorUrl: string, policyUrl: string) {
  const door = record(await import(doorUrl));
  const policy = record(await import(policyUrl));
  return {
    door: {
      evaluateDispatchBoundaries(input: unknown) {
        const out = record(invoke(door, "evaluateDispatchBoundaries", input));
        assert.ok(Array.isArray(out.refusals));
        const refusals = out.refusals.map((value: unknown) => {
          const row = record(value);
          assert.ok(typeof row.reason_id === "string");
          return { ...row, reason_id: row.reason_id };
        });
        return { ...out, refusals, boundary_refused: out.boundary_refused };
      },
      v5PlatformMatrix() {
        const out = record(invoke(door, "v5PlatformMatrix"));
        assert.ok(Array.isArray(out.rows));
        return {
          role_matrix_unchanged_by_node_state: record(out.role_matrix_unchanged_by_node_state),
          rows: out.rows.map((value: unknown) => {
            const row = record(value);
            assert.ok(typeof row.node_state === "string" && typeof row.availability === "string");
            return { ...row, node_state: row.node_state, availability: row.availability };
          }),
        };
      },
      passBoundaryDoor(input: unknown) {
        return invoke(door, "passBoundaryDoor", input);
      },
    },
    policy: {
      evaluateActorAuthority(input: unknown) {
        return record(invoke(policy, "evaluateActorAuthority", input));
      },
    },
  };
}
async function loadReal() {
  return loadModules(pathToFileURL(join(SRC, DOOR)).href, pathToFileURL(join(SRC, POLICY)).href);
}
async function loadDoorMutant(anchor: string, replacement: string) {
  const url = write("door", relink(mutate(DOOR, anchor, replacement)));
  return loadModules(url, pathToFileURL(join(SRC, POLICY)).href);
}
async function loadPolicyMutant(anchor: string, replacement: string) {
  const policyUrl = write("policy", relink(mutate(POLICY, anchor, replacement)));
  const doorUrl = write("door", relink(readFileSync(join(SRC, DOOR), "utf8"), { [POLICY]: policyUrl }));
  return loadModules(doorUrl, policyUrl);
}

// ------------------------------------------------------------------ probes
// Each returns true when the refusal it names holds.

const NOW = "2026-09-25T12:00:00Z";
const DELL = { slug: "dell", human: true, via: "oauth-google" };
const HERMES = {
  slug: "hermes-pilot",
  human: false,
  hermes: true,
  via: "hermes-token",
  sponsoring_human_slug: "joe",
  human_slug: "joe",
};
const ctx = (connectivity = "online") => ({ door: "cloud_dispatch_seam", connectivity, now: NOW });
type Modules = Awaited<ReturnType<typeof loadReal>>;
const reasonsOf = (v: { refusals: { reason_id: string }[] }) => v.refusals.map((r) => r.reason_id);
const run = (m: Modules, over: Record<string, unknown>) =>
  m.door.evaluateDispatchBoundaries({
    verb: "log-activity",
    write: true,
    actor: DELL,
    args: {},
    context: ctx(),
    ...over,
  });

const PROBES = {
  offline_write: (m: Modules) => reasonsOf(run(m, { context: ctx("offline") })).includes("offline_mutation_refused"),
  phi_top_level: (m: Modules) =>
    reasonsOf(run(m, { args: { patient_name: "x" } })).includes("phi_or_raw_patient_location_refused"),
  phi_routed_held: (m: Modules) => run(m, { args: { patient_heatmap: {} } }).boundary_refused === true,
  listing_exposure: (m: Modules) =>
    reasonsOf(run(m, { args: { representation_side: "landlord" } })).includes("listing_side_exposure_refused"),
  listing_activation: (m: Modules) =>
    reasonsOf(run(m, { args: { activate_listing_side: true } })).includes("listing_side_activation_refused"),
  admin_dell: (m: Modules) => reasonsOf(run(m, { verb: "approve-rule" })).includes("system_authority_reserved_to_joe"),
  admin_non_partner: (m: Modules) =>
    reasonsOf(run(m, { verb: "approve-rule", actor: HERMES })).includes("actor_not_verified_partner"),
  platform_authority_unchanged: (m: Modules) =>
    Object.values(m.door.v5PlatformMatrix().role_matrix_unchanged_by_node_state).every((v) => v === true),
  platform_loss_reported: (m: Modules) =>
    m.door
      .v5PlatformMatrix()
      .rows.filter((r: { node_state: string; availability: string }) => r.node_state !== "available")
      .every((r: { availability: string }) => r.availability !== "available"),
  shadow_never_throws: (m: Modules) => {
    try {
      m.door.passBoundaryDoor({ verb: "x", write: "bad", actor: DELL, args: {}, now: NOW, log: () => {} });
      return true;
    } catch {
      return false;
    }
  },
  enforce_refuses: (m: Modules) => {
    try {
      m.door.passBoundaryDoor({
        verb: "approve-rule",
        write: true,
        actor: DELL,
        args: {},
        now: NOW,
        mode: "enforce",
        log: () => {},
      });
      return false;
    } catch (error) {
      return (
        typeof error === "object" &&
        error !== null &&
        "payload" in error &&
        typeof error.payload === "object" &&
        error.payload !== null &&
        "error" in error.payload &&
        error.payload.error === "v5_boundary_refused"
      );
    }
  },
  bounded_output: (m: Modules) => {
    const verdict = run(m, {
      args: { rows: Array.from({ length: 2000 }, () => ({ representation_side: "landlord" })) },
    });
    return verdict.refusals.length === 1 && JSON.stringify(verdict).length < 8000;
  },
  args_untouched: (m: Modules) => {
    const args = { rows: [{ representation_side: "landlord" }], note: "x" };
    const before = JSON.stringify(args);
    run(m, { args });
    return JSON.stringify(args) === before;
  },
  grant_fix_valid_redecision_bad_delegation: (m: Modules) =>
    m.policy.evaluateActorAuthority({
      actor: DELL,
      action: "developer.change_source",
      tenant: "carr-internal",
      now: NOW,
      redecision: {
        redecision_ref: "R",
        decided_by: "joe",
        subject: "dell",
        action: "developer.change_source",
        cited_source_digest: "c".repeat(64),
        decided_at: "2026-09-20T00:00:00Z",
      },
      delegation: {
        delegation_ref: "D",
        granted_by: "joe",
        granted_to: "dell",
        action: "developer.change_source",
        receipt_digest: "d".repeat(64),
        issued_at: "2026-09-01T00:00:00Z",
        expires_at: "2026-09-02T00:00:00Z",
      },
    }).reason_id === "delegation_expired",
  grant_fix_valid_delegation_bad_redecision: (m: Modules) =>
    m.policy.evaluateActorAuthority({
      actor: DELL,
      action: "developer.change_source",
      tenant: "carr-internal",
      now: NOW,
      redecision: {
        redecision_ref: "R",
        decided_by: "dell",
        subject: "dell",
        action: "developer.change_source",
        cited_source_digest: "c".repeat(64),
        decided_at: "2026-09-20T00:00:00Z",
      },
      delegation: {
        delegation_ref: "D",
        granted_by: "joe",
        granted_to: "dell",
        action: "developer.change_source",
        receipt_digest: "d".repeat(64),
        issued_at: "2026-09-25T00:00:00Z",
        expires_at: "2026-09-26T00:00:00Z",
      },
    }).reason_id === "redecision_author_not_system_authority",
};

// ----------------------------------------------------------------- mutants

const MUTANTS: [keyof typeof PROBES, string, string, string, string][] = [
  [
    "offline_write",
    "door",
    "every dispatch is treated as a read",
    'const operation_kind = write ? "mutation" : "read";',
    'const operation_kind = "read";',
  ],
  [
    "phi_top_level",
    "door",
    "top-level fields escape the privacy scan",
    "if (dataClass) privacyHits.push",
    "if (dataClass && depth > 0) privacyHits.push",
  ],
  [
    "phi_routed_held",
    "door",
    "an aggregate awaiting its privacy route is passed as allowed",
    'decision: answer.decision === "allow" ? "allow" : "refuse",',
    'decision: answer.decision === "refuse" ? "refuse" : "allow",',
  ],
  [
    "listing_exposure",
    "door",
    "a representation assertion is read as a structural value",
    'intent: "expose", surface: verb',
    'intent: "structural_record", surface: verb',
  ],
  [
    "listing_activation",
    "door",
    "only a literal 'activate' string counts as activation",
    "if (hit.value === false || hit.value === null || hit.value === undefined) continue;",
    'if (hit.value !== "activate") continue;',
  ],
  [
    "admin_dell",
    "door",
    "the admin verb registry is never consulted",
    "if (!Object.hasOwn(V5_DOOR_SYSTEM_AUTHORITY_VERBS, verb)) return null;",
    "return null;",
  ],
  [
    "admin_non_partner",
    "door",
    "an unverified sponsor claim is accepted as the authority subject",
    "const subject = partnerAuthoritySlugForActor(actor);",
    "const subject = partnerAuthoritySlugForActor(actor) ?? actor?.sponsoring_human_slug ?? null;",
  ],
  [
    "platform_authority_unchanged",
    "door",
    "a lost node demotes the partner",
    "if (continuity_context !== undefined) request.continuity_context = continuity_context;",
    "if (continuity_context !== undefined) { request.continuity_context = continuity_context; " +
      'if (continuity_context.local_platform_state === "unavailable") request.actor = { slug, human: false }; }',
  ],
  [
    "platform_loss_reported",
    "policy",
    "a cloud fallback reports itself fully available",
    'availability: "degraded", execution: "cloud_fallback" });',
    'availability: "available", execution: "cloud_fallback" });',
  ],
  [
    "shadow_never_throws",
    "door",
    "shadow rethrows an internal door error",
    'if (mode === "enforce") throw error;',
    "throw error;",
  ],
  [
    "enforce_refuses",
    "door",
    "enforce computes the verdict and never refuses",
    'enforced: mode === "enforce" && boundary_refused,',
    "enforced: false,",
  ],
  [
    "bounded_output",
    "door",
    "per-field checks are no longer merged (#1268 round-1 blocker)",
    "const key = JSON.stringify(identity);",
    "const key = JSON.stringify(identity) + Math.random();",
  ],
  [
    "bounded_output",
    "door",
    "the stored field-path list is uncapped",
    "if (entry.fields.length < V5_DOOR_MAX_FIELD_PATHS) entry.fields.push(boundedPath(path));",
    "if (true) entry.fields.push(path);",
  ],
  [
    "args_untouched",
    "door",
    "the door marks the caller's arguments",
    "const scan = scanArguments(args ?? {});",
    "if (isPlainObject(args)) args.__door_seen = true;\n  const scan = scanArguments(args ?? {});",
  ],
  [
    "grant_fix_valid_redecision_bad_delegation",
    "policy",
    "#929 regression: a valid redecision skips the delegation",
    "if (hasDelegation) {\n      const reason = checkDelegation",
    "if (hasDelegation && !hasRedecision) {\n      const reason = checkDelegation",
  ],
  [
    "grant_fix_valid_delegation_bad_redecision",
    "policy",
    "#929 regression: a valid delegation skips the redecision",
    "if (hasRedecision) {\n      const reason = checkRedecision",
    "if (hasRedecision && !hasDelegation) {\n      const reason = checkRedecision",
  ],
];

test("every probe holds on the real source", async () => {
  const real = await loadReal();
  for (const [name, probe] of Object.entries(PROBES)) assert.equal(probe(real), true, name);
  // Every probe is exercised by at least one mutant.
  assert.deepEqual([...new Set(MUTANTS.map((m) => m[0]))].sort(), Object.keys(PROBES).sort());
});

for (const [probeName, target, story, anchor, replacement] of MUTANTS) {
  test(`mutant caught: ${probeName} — ${story}`, async () => {
    const mutant =
      target === "door" ? await loadDoorMutant(anchor, replacement) : await loadPolicyMutant(anchor, replacement);
    let held: boolean;
    try {
      held = PROBES[probeName](mutant);
    } catch {
      held = false;
    }
    assert.equal(held, false, `the ${probeName} probe did not catch the planted bug: ${story}`);
  });
}
