// amend-closed-loop-mutants.test.mjs — planted-bug mutants for the two
// properties amend-closed-loop.test.mjs's own fake-client assertions cannot,
// by construction, prove are load-bearing: that the version check actually
// gates the write, and that the amendment insert actually runs before the
// projection update (append-only, never the reverse).
//
// A refusal nobody has watched fail is indistinguishable from a refusal that
// never ran (same discipline as global-boundaries-mutants.v5.test.mjs). For
// each property this file plants one realistic bug in a COPY of tools.js,
// loads the copy, and asserts the same probe that passes on the real source
// FAILS against the mutant.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const FILE = "tools.js";
const WORK = mkdtempSync(join(tmpdir(), "amend-closed-loop-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));

let serial = 0;

/** Rewrite tools.js's relative imports to absolute file URLs so a copy in a
 * scratch directory still resolves every sibling module it depends on. */
function relink(source) {
  // tools.js wraps a couple of long import statements across two lines
  // (`import { x } from\n  "./y.js";`), so the gap between `from` and the
  // string literal is whitespace, not always a single space.
  return source.replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(SRC, file)).href}"`);
}

function mutate(anchor, replacement) {
  const source = readFileSync(join(SRC, FILE), "utf8");
  const count = source.split(anchor).length - 1;
  assert.equal(count, 1, `mutant anchor must occur exactly once in ${FILE}: ${JSON.stringify(anchor)}`);
  return relink(source.replace(anchor, replacement));
}

async function loadReal() {
  return import(pathToFileURL(join(SRC, FILE)).href);
}

async function loadMutant(anchor, replacement) {
  serial += 1;
  const path = join(WORK, `${serial}-tools.mjs`);
  writeFileSync(path, mutate(anchor, replacement));
  return import(pathToFileURL(path).href);
}

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

const CLOSED = {
  id: "cccccccc-0000-0000-0000-000000000723",
  kind: "open_loop", number: "723", status: "done",
  marker: "none", due_on: null, close_outcome: "x",
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};

const GOOD_OUTCOME = "The card visual system shipped in PR #900, not the bio-header reminder.";
const GOOD_REASON = "Original close mistakenly recorded outcome 'x' — defect a2c04ffa.";

class Fake {
  constructor({ version = 3 } = {}) {
    this.version = version;
    this.writes = [];
  }
  async query(text, params) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select version from loop_item"))
      return { rows: [{ version: this.version }] };
    if (sql.startsWith("select created_at from loop_item"))
      return { rows: [{ created_at: new Date("2026-09-01T00:00:00Z") }] };
    if (sql.startsWith("select a.slug as actor")) return { rows: [] };
    if (sql.startsWith("select li.id, li.kind, li.number")) return { rows: [CLOSED] };
    this.writes.push({ sql, params });
    if (sql.startsWith("insert into loop_amendment")) return { rows: [{ id: "amend-1" }] };
    if (sql.startsWith("update loop_item")) return { rows: [{ ...CLOSED, version: this.version + 1 }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

function args(overrides = {}) {
  return { idempotency_key: `k-${Math.random()}`, loop_id: CLOSED.id, base_version: 2,
    outcome: GOOD_OUTCOME, reason: GOOD_REASON, ...overrides };
}

/** Probe 1: a STALE base_version (caller read v2, live row is v3) must be
 * refused with version_conflict, and nothing may be written. */
async function staleVersionIsRefused(mod) {
  const fake = new Fake({ version: 3 });
  try {
    await mod.TOOLS["amend-closed-loop"].handler(fake, joe, args({ base_version: 2 }));
    return false; // it must have thrown — reaching here means the mutant let it through
  } catch (e) {
    return e instanceof mod.ToolError && e.payload.error === "version_conflict"
      && fake.writes.length === 0;
  }
}

/** Probe 2: the amendment row must be appended BEFORE loop_item's projection
 * is updated — never the reverse, and both must happen exactly once. */
async function appendPrecedesProjectionUpdate(mod) {
  const fake = new Fake({ version: 2 });
  await mod.TOOLS["amend-closed-loop"].handler(fake, joe, args({ base_version: 2 }));
  const amendIdx = fake.writes.findIndex((w) => w.sql.startsWith("insert into loop_amendment"));
  const updateIdx = fake.writes.findIndex((w) => w.sql.startsWith("update loop_item"));
  return amendIdx >= 0 && updateIdx >= 0 && amendIdx < updateIdx;
}

test("PROBE SANITY: both probes pass against the real, unmutated source", async () => {
  const real = await loadReal();
  assert.equal(await staleVersionIsRefused(real), true,
    "the real source must refuse a stale base_version");
  assert.equal(await appendPrecedesProjectionUpdate(real), true,
    "the real source must append the amendment before updating loop_item");
});

test("MUTANT: versionGuard call deleted — a stale base_version silently goes through", async () => {
  const mutant = await loadMutant(
    `const cur = await resolveLoop(c, args, { anyStatus: true });\n      await versionGuard(c, "loop_item", cur.id, args.base_version);\n      if (cur.status === "open")`,
    `const cur = await resolveLoop(c, args, { anyStatus: true });\n      if (cur.status === "open")`,
  );
  assert.equal(await staleVersionIsRefused(mutant), false,
    "removing the versionGuard call must make the stale-version probe fail — proving the real call is load-bearing");
});

test("MUTANT: versionGuard called with a hardcoded version that always matches the live row — version_conflict never fires", async () => {
  // A different way the check could be defeated: the CALL survives, but its
  // base_version argument is no longer the caller's — it is a literal that
  // happens to equal what the fake's `select version ... for update` always
  // answers in this probe (3), so the comparison can never disagree.
  const mutant = await loadMutant(
    `await versionGuard(c, "loop_item", cur.id, args.base_version);\n      if (cur.status === "open")\n        throw new ToolError({ error: "loop_open"`,
    `await versionGuard(c, "loop_item", cur.id, 3);\n      if (cur.status === "open")\n        throw new ToolError({ error: "loop_open"`,
  );
  assert.equal(await staleVersionIsRefused(mutant), false,
    "hardcoding versionGuard's expected version must defeat the stale-version refusal");
});

test("MUTANT: the amendment insert is dropped — only the projection update runs", async () => {
  const mutant = await loadMutant(
    `      await c.query(\n        \`insert into loop_amendment (loop_id, prior_outcome, new_outcome, prior_resolution,\n           new_resolution, reason, actor_id, idempotency_key)\n         values ($1,$2,$3,$4,$5,$6,$7,$8)\`,\n        [cur.id, priorOutcome, newOutcome, priorResolution, resolution, reason, actor.id,\n         args.idempotency_key]);\n\n      // The loop's current outcome reads the latest amendment:`,
    `      // mutant: amendment insert deliberately removed\n\n      // The loop's current outcome reads the latest amendment:`,
  );
  assert.equal(await appendPrecedesProjectionUpdate(mutant), false,
    "dropping the loop_amendment insert must make the append-before-update probe fail — proving the insert is load-bearing, not incidental");
});

test("MUTANT: insert and update order reversed — the projection is written before the history that justifies it", async () => {
  const insertStatement =
    `      await c.query(\n        \`insert into loop_amendment (loop_id, prior_outcome, new_outcome, prior_resolution,\n           new_resolution, reason, actor_id, idempotency_key)\n         values ($1,$2,$3,$4,$5,$6,$7,$8)\`,\n        [cur.id, priorOutcome, newOutcome, priorResolution, resolution, reason, actor.id,\n         args.idempotency_key]);`;
  const updateStatement =
    `      await c.query(\n        \`update loop_item set close_outcome=$1, outcome=$1, status=$2, updated_by=$3 where id=$4\`,\n        [newOutcome, resolution, actor.id, cur.id]);`;
  const source = readFileSync(join(SRC, FILE), "utf8");
  assert.ok(source.includes(insertStatement), "insert anchor must be present verbatim");
  assert.ok(source.includes(updateStatement), "update anchor must be present verbatim");
  // Swap the two statements' bodies in place, leaving everything around and
  // between them (including the comment block that explains the ordering)
  // untouched — so this mutant differs from the real source ONLY in which
  // statement runs first.
  const mutated = source
    .replace(insertStatement, "\u0000PLACEHOLDER_UPDATE\u0000")
    .replace(updateStatement, insertStatement)
    .replace("\u0000PLACEHOLDER_UPDATE\u0000", updateStatement);
  assert.notEqual(mutated, source, "the swap must actually change the source");
  serial += 1;
  const path = join(WORK, `${serial}-tools.mjs`);
  writeFileSync(path, relink(mutated));
  const mutant = await import(pathToFileURL(path).href);
  assert.equal(await appendPrecedesProjectionUpdate(mutant), false,
    "reversing the statement order must make the append-before-update probe fail");
});
