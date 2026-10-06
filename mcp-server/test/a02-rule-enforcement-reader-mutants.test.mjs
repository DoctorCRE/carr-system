// a02-rule-enforcement-reader-mutants.test.mjs — planted-bug mutants for the
// JS half of V5-A02: every check in lifecycle-assurance.v5.js's coverage
// record validation, and the fallback verb's handler in tools.js.
//
// A refusal nobody has watched fail is indistinguishable from a refusal that
// never ran (the global-boundaries-mutants / amend-closed-loop-mutants
// discipline). Each case below plants ONE realistic bug in a COPY of the
// source, loads the copy, and asserts that a probe which passes on the real
// source FAILS on the mutant. Each validation probe feeds a record with ONE
// planted inconsistency that only the mutated check can catch, so no other
// check can mask a surviving mutant.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const WORK = mkdtempSync(join(tmpdir(), "a02-rule-enforcement-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));

let serial = 0;
function relink(source) {
  return source.replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(SRC, file)).href}"`);
}
async function loadReal(file) {
  return import(pathToFileURL(join(SRC, file)).href);
}
async function loadMutant(file, anchor, replacement) {
  const source = readFileSync(join(SRC, file), "utf8");
  const count = source.split(anchor).length - 1;
  assert.equal(count, 1, `mutant anchor must occur exactly once in ${file}: ${JSON.stringify(anchor)}`);
  serial += 1;
  const path = join(WORK, `${serial}-${file.replace(/\.js$/, "")}.mjs`);
  writeFileSync(path, relink(source.replace(anchor, replacement)));
  return import(pathToFileURL(path).href);
}

// ---------------------------------------------------------------------------
// The coverage record validation (lifecycle-assurance.v5.js).
// ---------------------------------------------------------------------------

const LA = "lifecycle-assurance.v5.js";
const R1 = "11111111-1111-4111-8111-111111111111";

function coverage(overrides = {}) {
  return {
    schema_version: "doctorcre-v5-a02-rule-enforcement-coverage.v2",
    observed_at: "2026-09-25T20:00:00Z",
    active_rule_count: 2,
    covered_rule_count: 2,
    gap_count: 0,
    coverage_state: "complete",
    coverage_complete: true,
    gaps: [],
    evidence_digest: `sha256:${"a".repeat(64)}`,
    ...overrides,
  };
}
const gap = (reason_id, rule_id = R1) => ({ rule_id, reason_id, detail: "planted" });
const dbReturning = payload => ({ async query() { return { rows: [{ coverage: payload }] }; } });

async function refusedAsInvalid(mod, payload) {
  const result = await mod.readRuleEnforcementCoverage(dbReturning(payload));
  return result.status === "unavailable" && result.reason_id === "rule_coverage_record_invalid";
}

const VALIDATION_MUTANTS = [
  { id: "J1", check: "coverage_complete agrees with coverage_state",
    anchor: '  if (record.coverage_complete !== (record.coverage_state === "complete")) return false;\n',
    payload: coverage({ coverage_complete: false }) },
  { id: "J2", check: "covered + gaps == active",
    anchor: "  if (record.covered_rule_count + record.gap_count !== record.active_rule_count) return false;\n",
    payload: coverage({ active_rule_count: 3 }) },
  { id: "J3", check: "gap_count == gaps.length",
    anchor: "  if (record.gap_count !== record.gaps.length) return false;\n",
    payload: coverage({ covered_rule_count: 1, gap_count: 1, coverage_state: "gaps",
      coverage_complete: false, gaps: [] }) },
  { id: "J4", check: "one gap per rule",
    anchor: "    if (seen.has(gap.rule_id)) return false;\n",
    payload: coverage({ covered_rule_count: 0, gap_count: 2, coverage_state: "gaps",
      coverage_complete: false,
      gaps: [gap("active_rule_control_unmapped"), gap("active_rule_fallback_absent")] }) },
  { id: "J5", check: "gap reason vocabulary is closed",
    anchor: "    && V5_A02_COVERAGE_GAP_REASONS.has(gap.reason_id)\n",
    payload: coverage({ active_rule_count: 1, covered_rule_count: 0, gap_count: 1,
      coverage_state: "gaps", coverage_complete: false, gaps: [gap("made_up_reason")] }) },
  { id: "J8", check: "schema_version is the v2 record",
    anchor: "  if (record.schema_version !== V5_A02_COVERAGE_SCHEMA_VERSION) return false;\n",
    payload: coverage({ schema_version: "doctorcre-v5-a02-rule-enforcement-coverage.v1" }) },
  { id: "J9", check: "coverage_state is derived from the counts (ruling 5b: empty is never complete)",
    anchor: "  if (record.coverage_state !== coverageStateFor(record)) return false;\n",
    payload: coverage({ active_rule_count: 0, covered_rule_count: 0 }) },
];

test("PROBE SANITY: every validation probe is refused by the real source", async () => {
  const real = await loadReal(LA);
  for (const m of VALIDATION_MUTANTS)
    assert.equal(await refusedAsInvalid(real, m.payload), true, `${m.id} probe must pass on real source`);
});

for (const m of VALIDATION_MUTANTS) {
  test(`MUTANT ${m.id} (drop: ${m.check}) is killed`, async () => {
    const mutant = await loadMutant(LA, m.anchor, "");
    assert.equal(await refusedAsInvalid(mutant, m.payload), false,
      `${m.id} survived: its planted inconsistency was still refused without the check`);
  });
}

test("MUTANT J6 (driver error reported as available-complete) is killed", async () => {
  const failing = { async query() { throw new Error("connection reset"); } };
  const probe = async mod => {
    const r = await mod.readRuleEnforcementCoverage(failing);
    return r.status === "unavailable" && r.coverage_complete === undefined;
  };
  assert.equal(await probe(await loadReal(LA)), true);
  const mutant = await loadMutant(LA,
    "  } catch {\n    return unavailableRuleEnforcementCoverage();\n  }",
    "  } catch {\n    return { status: 'available', coverage_complete: true };\n  }");
  assert.equal(await probe(mutant), false);
});

test("MUTANT J7 (validation bypassed) is killed", async () => {
  const mutant = await loadMutant(LA, "  if (!validCoverageRecord(record))\n", "  if (false)\n");
  assert.equal(await refusedAsInvalid(mutant, VALIDATION_MUTANTS[1].payload), false);
});

// ---------------------------------------------------------------------------
// The fallback verb's handler (tools.js).
// ---------------------------------------------------------------------------

const TOOLS_FILE = "tools.js";
const VERB = "record-rule-enforcement-fallback";
const FULL_ID = "a0200009-0000-4000-8000-000000000000";
const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

class Fake {
  constructor({ result, error } = {}) {
    this.result = result;
    this.error = error;
    this.calls = [];
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select id, status, left(statement"))
      return { rows: [{ id: FULL_ID, status: "active", gist: "synthetic" }] };
    if (sql.startsWith("select ops.record_rule_enforcement_fallback")) {
      if (this.error) throw this.error;
      return { rows: [{ result: this.result }] };
    }
    return { rows: [{ id: "row-1" }] };
  }
}
const receipt = () => ({ schema_version: "rule-enforcement-fallback-receipt.v1",
  receipt_id: "f0000000-0000-4000-8000-000000000001", rule_id: FULL_ID, rule_version: 1,
  fallback_kind: "refuse_closed", procedure_ref: "p", recorded_by: "joe",
  created_at: "2026-09-26T00:00:00Z" });
const args = () => ({ idempotency_key: `k-${Math.random()}`, rule_id: FULL_ID.slice(0, 8),
  fallback_kind: "refuse_closed", procedure_ref: "p", reason: "r" });

/** T2: a short id reaches SQL only after resolveRuleId turned it into the full uuid. */
async function shortIdResolved(mod) {
  const fake = new Fake({ result: receipt() });
  await mod.TOOLS[VERB].handler(fake, joe, args());
  const call = fake.calls.find(c => c.sql.startsWith("select ops.record_rule_enforcement_fallback"));
  return call?.params[0] === FULL_ID;
}
/** T3: a row without a receipt is refused by name, never reported. */
async function missingReceiptRefused(mod) {
  try {
    await mod.TOOLS[VERB].handler(new Fake({ result: null }), joe, args());
    return false;
  } catch (e) {
    return e instanceof mod.ToolError && e.payload.error === "rule_enforcement_fallback_not_recorded";
  }
}
/** T4: a database refusal reaches the caller under its own name. */
async function namedRefusalMapped(mod) {
  const error = Object.assign(new Error("rule_enforcement_fallback_already_recorded"), { code: "P0001" });
  try {
    await mod.TOOLS[VERB].handler(new Fake({ error }), joe, args());
    return false;
  } catch (e) {
    return e instanceof mod.ToolError && e.payload.error === "rule_enforcement_fallback_already_recorded";
  }
}
/** T1: the verb only ever runs on an authority connection. */
async function authorityOnly(mod) {
  return mod.TOOLS[VERB].authorityOnly === true && mod.TOOLS[VERB].write === true;
}

test("PROBE SANITY: every handler probe passes against the real tools.js", async () => {
  const real = await loadReal(TOOLS_FILE);
  assert.equal(await shortIdResolved(real), true);
  assert.equal(await missingReceiptRefused(real), true);
  assert.equal(await namedRefusalMapped(real), true);
  assert.equal(await authorityOnly(real), true);
});

test("MUTANT T1 (verb not authorityOnly) is killed", async () => {
  const mutant = await loadMutant(TOOLS_FILE,
    `  "${VERB}": {\n    write: true,\n    authorityOnly: true,`,
    `  "${VERB}": {\n    write: true,\n    authorityOnly: false,`);
  assert.equal(await authorityOnly(mutant), false);
});

test("MUTANT T2 (handler skips resolveRuleId) is killed", async () => {
  const mutant = await loadMutant(TOOLS_FILE,
    "        const ruleId = await resolveRuleId(c, args.rule_id);\n        let recorded;",
    "        const ruleId = args.rule_id;\n        let recorded;");
  assert.equal(await shortIdResolved(mutant), false);
});

test("MUTANT T3 (handler drops the receipt check) is killed", async () => {
  const mutant = await loadMutant(TOOLS_FILE,
    "        if (!result?.receipt_id)\n          throw new ToolError({ error: \"rule_enforcement_fallback_not_recorded\", rule_id: ruleId });\n",
    "");
  assert.equal(await missingReceiptRefused(mutant), false);
});

test("MUTANT T4 (handler stops naming database refusals) is killed", async () => {
  const mutant = await loadMutant(TOOLS_FILE,
    "          if (RULE_ENFORCEMENT_FALLBACK_REFUSALS.has(e?.message))\n",
    "          if (false)\n");
  assert.equal(await namedRefusalMapped(mutant), false);
});
