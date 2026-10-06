// Generate the SQL leg's design-contract vectors from the SHARED F03 corpus.
//
// WHY THIS EXISTS.  The engineering-slice-plan.v2 design contract is stated
// three times: in JavaScript (mcp-server/src/engineering-runtime.js), in Python
// (tools/room-bridge/engineering_passport.py) and in SQL (the two candidates
// under ops/).  The first two are proved equivalent by replaying one shared,
// versioned corpus -- test/fixtures/f03-design-contract-parity.v1.json -- through
// both validators.  The SQL leg consumed none of it: its two fixtures were
// 2,941 lines of hand-written vectors, so on the day someone finally ran them
// they would have proved the database against a DIFFERENT set of cases than the
// two validators it has to agree with, and a case added for JS and Python would
// never have reached the database at all.
//
// This generator closes that.  It materializes every corpus vector exactly as
// f03-design-contract-parity.test.mjs materializes it -- the same four pure
// ops, the same three seal modes, the same canonicalDigest -- and emits them as
// SQL parts the two hand-written fixtures include.  A vector added to the
// corpus for JS and Python is then automatically a vector the SQL leg must
// satisfy, and there is nothing to keep in step by hand.
//
// THE OUTPUT IS GENERATED, NEVER EDITED.  A generated fixture someone can edit
// by hand is the same defect one level down, so both emitted files carry a
// do-not-edit header and the corpus digest they came from, and
// f03-design-contract-parity.test.mjs byte-compares the files on disk against
// what this generator produces from the corpus in the tree.  Editing either
// file, or changing the corpus without regenerating, turns that test red.
//
// NO VALIDATION RULE LIVES HERE.  This file moves JSON and writes SQL.  The two
// places it makes a judgement about the SQL leg are stated in the open, carried
// into the generated headers, and both are structural facts about a vector
// rather than a restatement of any validator's logic:
//
//   1. THE v1 BOUNDARY.  ops.engineering_slice_plan_refusal returns null for
//      any engineering-slice-plan.v1 plan on purpose (see the candidate's "THE
//      v1 BOUNDARY" comment): an accepted v1 plan is not put through the
//      successor version's rules.  So the SQL leg accepts all six v1-legacy
//      corpus vectors, including the five that JS and Python both refuse.  That
//      is asserted here as the SQL leg's own documented divergence rather than
//      hidden by dropping the vectors -- the same treatment the corpus already
//      gives the JS/Python divergences.
//   2. WHICH REJECTIONS THE PER-SLICE SEAM OWNS.  The receipt candidate's
//      ops.engineering_receipt_design_contract_refusal validates ONE slice, so
//      it cannot see a plan-level rule.  A rejected vector is asserted against
//      it only when the vector is structurally incapable of tripping one: every
//      op lands strictly inside a single slice's design_contract, the plan is
//      resealed, the base carries exactly one slice (so no cross-slice rule has
//      two slices to compare), and no op touches seam_decision (seam authority
//      is a plan-level rule in this seam).  Everything else is recorded as an
//      excluded case with its reason and counted at run time, never silently
//      dropped.  Those vectors keep their whole-plan assertion in the other
//      generated part.
//
// Usage:
//   node mcp-server/test/f03-sql-corpus-generator.mjs --check   (default)
//   node mcp-server/test/f03-sql-corpus-generator.mjs --write
//
// This script reads two files, writes two files under --write, and does nothing
// else.  No database, no network, no record layer.

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { canonicalDigest } from "../src/engineering-runtime.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const CORPUS_PATH = path.join(HERE, "fixtures", "f03-design-contract-parity.v1.json");
export const PLAN_OUTPUT_PATH = path.join(HERE, "f03-design-contract-corpus-plan-postgres.generated.sql");
export const SLICE_OUTPUT_PATH = path.join(HERE, "f03-design-contract-corpus-slice-postgres.generated.sql");

const GENERATOR_REF = "mcp-server/test/f03-sql-corpus-generator.mjs";

// --- the corpus applier -------------------------------------------------------
//
// Byte-for-byte the same four operations f03-design-contract-parity.test.mjs
// applies, and the same three seal modes.  Kept as a copy of that harness on
// purpose: a shared helper would mean the corpus and the generator could only
// ever agree with each other, and the point is that this produces the SAME
// materialized plan the two validators were tested against.

function containerAt(root, segments) {
  let node = root;
  for (const segment of segments) node = node[segment];
  return node;
}

function applyOp(root, op) {
  const segments = op.path;
  if (op.op === "reorder") {
    if (!segments.length) return Object.fromEntries(Object.entries(root).reverse());
    const holder = containerAt(root, segments.slice(0, -1));
    const key = segments[segments.length - 1];
    holder[key] = Object.fromEntries(Object.entries(holder[key]).reverse());
    return root;
  }
  if (op.op === "append") {
    containerAt(root, segments).push(structuredClone(op.value));
    return root;
  }
  const holder = containerAt(root, segments.slice(0, -1));
  const key = segments[segments.length - 1];
  if (op.op === "delete") delete holder[key];
  else if (op.op === "set") holder[key] = structuredClone(op.value);
  else throw new Error(`unknown corpus op: ${op.op}`);
  return root;
}

function withoutDigest(plan) {
  return Object.fromEntries(Object.entries(plan).filter(([key]) => key !== "plan_digest"));
}

function materializePlan(corpus, vector) {
  let plan = structuredClone(corpus.bases[vector.base]);
  plan.plan_digest = canonicalDigest(withoutDigest(plan));
  for (const op of vector.ops || []) plan = applyOp(plan, op);
  const seal = vector.seal || "reseal";
  if (seal === "reseal") plan.plan_digest = canonicalDigest(withoutDigest(plan));
  else if (seal === "literal") plan.plan_digest = vector.plan_digest;
  else if (seal !== "as_is") throw new Error(`unknown seal mode: ${seal}`);
  return plan;
}

// --- the two structural judgements, both stated in the header -----------------

function sqlVerdict(plan, vector) {
  if (plan.schema_version === "engineering-slice-plan.v1") {
    return { expect: "accepted", divergent: vector.expect !== "accepted" };
  }
  return { expect: vector.expect, divergent: false };
}

/**
 * Decide whether a rejected vector can be asserted against the PER-SLICE
 * design-contract seam.  Returns the slice index when it can, or a reason when
 * it cannot.  Every test here is a fact about the vector's own shape; none of
 * them reads or restates a validation rule.
 */
function sliceScopedRejection(corpus, vector) {
  const base = corpus.bases[vector.base];
  if (base.schema_version !== "engineering-slice-plan.v2")
    return { reason: "engineering-slice-plan.v1 base: the per-slice v2 seam is not reached" };
  if ((vector.seal || "reseal") !== "reseal")
    return { reason: "a stale or literal plan_digest is a plan-level refusal" };
  if (base.slices.length !== 1)
    return { reason: "multi-slice base: a cross-slice rule could own this refusal" };
  if (!(vector.ops || []).length)
    return { reason: "no ops: nothing to attribute to a slice" };
  let index = null;
  for (const op of vector.ops) {
    const segments = op.path;
    const inContract = segments.length >= 4 && segments[0] === "slices" && segments[2] === "design_contract";
    if (!inContract)
      return { reason: "an op lands outside a slice's design_contract" };
    if (segments[3] === "seam_decision")
      return { reason: "seam authority is a plan-level rule in this seam" };
    if (index === null) index = segments[1];
    else if (index !== segments[1]) return { reason: "ops span two slices" };
  }
  return { index };
}

// --- SQL emission -------------------------------------------------------------

const DOLLAR_TAG = "$f03json$";

function sqlJson(value) {
  const text = JSON.stringify(value);
  if (text.includes(DOLLAR_TAG)) throw new Error("corpus content collides with the dollar-quote tag");
  return `${DOLLAR_TAG}${text}${DOLLAR_TAG}::jsonb`;
}

function sqlText(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function commentLines(text, indent = "-- ") {
  const words = String(text).split(/\s+/).filter(Boolean);
  const lines = [];
  let current = "";
  for (const word of words) {
    if (current && (current.length + 1 + word.length) > 72) { lines.push(current); current = word; }
    else current = current ? `${current} ${word}` : word;
  }
  if (current) lines.push(current);
  return lines.map(line => `${indent}${line}`);
}

function header(title, corpusDigest, corpus, bodyLines) {
  return [
    `-- ${title}`,
    "--",
    "-- GENERATED FILE -- DO NOT EDIT.  Every vector below is derived from the",
    `-- shared corpus mcp-server/test/fixtures/f03-design-contract-parity.v1.json`,
    `-- by ${GENERATOR_REF}.  Regenerate with:`,
    "--",
    `--     node ${GENERATOR_REF} --write`,
    "--",
    `-- corpus_version: ${corpus.corpus_version}`,
    `-- corpus_sha256:  ${corpusDigest}`,
    "--",
    "-- NOT RUN.  Nothing in this file has been executed and no result below is",
    "-- claimed.  It is reviewable source only, and it is a PART: it opens no",
    "-- transaction and closes none.  The including fixture owns the BEGIN and the",
    "-- ROLLBACK, and every object created here is a temp object that disappears",
    "-- with that transaction.  It reads no ledger table and writes no row.",
    "--",
    ...bodyLines,
    "",
  ].join("\n");
}

function planPart(corpus, corpusDigest) {
  const rows = [];
  const divergences = [];
  for (const vector of corpus.vectors) {
    const plan = materializePlan(corpus, vector);
    const verdict = sqlVerdict(plan, vector);
    rows.push({ vector, expect: verdict.expect, plan });
    if (verdict.divergent) divergences.push(vector);
  }

  const body = [
    ...commentLines(
      "WHAT A LIVE RUN OF THIS PART WOULD PROVE.  That ops.engineering_slice_plan_refusal "
      + "accepts and refuses exactly the plans the server validator (requirePlan) and the "
      + "portable validator (validate_engineering_slice_plan) accept and refuse -- the same "
      + "inputs, not a parallel case table that happens to resemble them."),
    "--",
    ...commentLines(
      "AND ONE THING THE HAND-WRITTEN FIXTURES EXPLICITLY DO NOT PROVE.  Every plan_digest "
      + "below was computed by the JavaScript producer's canonicalDigest and is embedded as a "
      + "literal, so an accepted vector only stays accepted if the database's own "
      + "ops.guidance_import_canonical_json agrees with it byte for byte. The companion "
      + "fixtures compute the digest with the same SQL expression the validator checks "
      + "against, which is why their headers disclaim canonicalization parity; this part does "
      + "not, and a canonicalization divergence surfaces here as a refused positive."),
    "--",
    ...commentLines(
      `THE v1 BOUNDARY, ASSERTED RATHER THAN AVOIDED.  ops.engineering_slice_plan_refusal `
      + `returns null for any engineering-slice-plan.v1 plan by design, so the SQL leg accepts `
      + `all ${corpus.vectors.filter(v => corpus.bases[v.base].schema_version === "engineering-slice-plan.v1").length} `
      + `v1-legacy vectors, including ${divergences.length} that BOTH other validators refuse. `
      + `Those ${divergences.length} are listed below and are expected to be accepted here. `
      + `A future change that starts refusing them turns this part red, which is the point: `
      + `the boundary is a decision and it should not be able to move quietly.`),
    "--",
    ...divergences.flatMap(vector => commentLines(
      `v1 divergence: ${vector.id} -- refused by JS and Python, accepted here`, "--   ")),
    "--",
    ...commentLines(
      "ONE THING A PASS HERE CANNOT MEAN.  jsonb does not preserve object key order, so the "
      + "corpus's two key-order permutation vectors are tautologically accepted on this leg: "
      + "the permutation is gone before the validator sees the plan. They are emitted anyway "
      + "because their plan_digest still has to bind, which is the part that was worth "
      + "proving; the key-order independence itself is proved in JS and Python only."),
    "--",
    ...commentLines(
      `The ${corpus.divergence_vectors.length} corpus divergence_vectors are NOT emitted: each `
      + `records a different verdict for JS and for Python, so the corpus states no single `
      + `expectation for a third implementation to be held to. Their shapes are all v1-legacy `
      + `and the v1 boundary above already covers what the SQL leg does with them.`),
  ];

  const lines = [header(
    "f03-design-contract-corpus-plan-postgres.generated.sql", corpusDigest, corpus, body)];

  lines.push(
    "create temporary table f03_corpus_plan_case(",
    "  vector_id text primary key,",
    "  corpus_expect text not null,",
    "  sql_expect text not null,",
    "  plan jsonb not null",
    ") on commit drop;",
    "");
  lines.push("insert into f03_corpus_plan_case(vector_id, corpus_expect, sql_expect, plan) values");
  rows.forEach((row, index) => {
    lines.push(`  -- ${row.vector.id}: ${row.vector.describe}`);
    lines.push(`  (${sqlText(row.vector.id)}, ${sqlText(row.vector.expect)}, ${sqlText(row.expect)},`);
    lines.push(`   ${sqlJson(row.plan)})${index === rows.length - 1 ? ";" : ","}`);
  });
  lines.push("");
  lines.push(
    "do $corpus_plan$",
    "declare c record; v_refusal text; v_failures integer := 0; v_run integer := 0;",
    "begin",
    "  for c in select * from f03_corpus_plan_case order by vector_id loop",
    "    v_run := v_run + 1;",
    "    v_refusal := ops.engineering_slice_plan_refusal(c.plan);",
    "    if c.sql_expect = 'accepted' and v_refusal is not null then",
    "      v_failures := v_failures + 1;",
    "      raise warning 'CORPUS PLAN FAIL %: expected acceptance, got refusal %', c.vector_id, v_refusal;",
    "    elsif c.sql_expect = 'rejected' and v_refusal is null then",
    "      v_failures := v_failures + 1;",
    "      raise warning 'CORPUS PLAN FAIL %: expected a refusal, the plan was accepted', c.vector_id;",
    "    end if;",
    "  end loop;",
    "  if v_failures > 0 then",
    "    raise exception 'CORPUS PLAN: % of % shared vectors diverge from the SQL validator', v_failures, v_run;",
    "  end if;",
    `  raise notice 'CORPUS PLAN: % shared corpus vectors matched (corpus %)', v_run, ${sqlText(corpus.corpus_version)};`,
    "end $corpus_plan$;",
    "");
  return { text: lines.join("\n"), rows, divergences };
}

function slicePart(corpus, corpusDigest) {
  const accepted = [];
  const rejected = [];
  const excluded = [];
  for (const vector of corpus.vectors) {
    const base = corpus.bases[vector.base];
    if (base.schema_version !== "engineering-slice-plan.v2") {
      excluded.push({ vector, reason: "engineering-slice-plan.v1 base: the per-slice v2 seam is not reached" });
      continue;
    }
    const plan = materializePlan(corpus, vector);
    if (vector.expect === "accepted") {
      (plan.slices || []).forEach((slice, index) => accepted.push({ vector, index, slice }));
      continue;
    }
    const scoped = sliceScopedRejection(corpus, vector);
    if (scoped.reason !== undefined) { excluded.push({ vector, reason: scoped.reason }); continue; }
    rejected.push({ vector, index: scoped.index, slice: plan.slices[scoped.index] });
  }

  const body = [
    ...commentLines(
      "WHAT A LIVE RUN OF THIS PART WOULD PROVE.  That the receipt candidate's per-slice seam, "
      + "ops.engineering_receipt_design_contract_refusal, reads the SAME design contracts the "
      + "server and portable validators read: it accepts every slice of every plan the shared "
      + "corpus accepts, and it refuses the slice the corpus refuses wherever the refusal is "
      + "structurally a per-slice one."),
    "--",
    ...commentLines(
      "This part calls ONLY ops.engineering_receipt_design_contract_refusal, so it needs "
      + "ops/f03-receipt-validator.candidate.sql and nothing else -- the same single-candidate "
      + "property the including fixture's Part A states for itself."),
    "--",
    ...commentLines(
      `WHAT IS EXCLUDED AND WHY, counted rather than dropped.  ${excluded.length} rejected `
      + `corpus vectors are not asserted here because a one-slice predicate cannot be held to a `
      + `plan-level refusal. Each is listed in f03_corpus_slice_excluded with its reason and the `
      + `count is printed at run time. Every one of them keeps its whole-plan assertion in `
      + `f03-design-contract-corpus-plan-postgres.generated.sql, so nothing loses coverage; it `
      + `moves to the seam that owns it.`),
    "--",
    ...commentLines(
      `${accepted.length} accepted slices and ${rejected.length} refused slices are asserted.`),
  ];

  const lines = [header(
    "f03-design-contract-corpus-slice-postgres.generated.sql", corpusDigest, corpus, body)];

  lines.push(
    "create temporary table f03_corpus_slice_case(",
    "  vector_id text not null,",
    "  slice_index integer not null,",
    "  expect text not null,",
    "  slice jsonb not null,",
    "  primary key (vector_id, slice_index)",
    ") on commit drop;",
    "");
  lines.push("insert into f03_corpus_slice_case(vector_id, slice_index, expect, slice) values");
  const rows = [
    ...accepted.map(row => ({ ...row, expect: "accepted" })),
    ...rejected.map(row => ({ ...row, expect: "rejected" })),
  ];
  rows.forEach((row, index) => {
    lines.push(`  -- ${row.vector.id}[${row.index}]: ${row.expect}`);
    lines.push(`  (${sqlText(row.vector.id)}, ${row.index}, ${sqlText(row.expect)},`);
    lines.push(`   ${sqlJson(row.slice)})${index === rows.length - 1 ? ";" : ","}`);
  });
  lines.push("");

  lines.push(
    "create temporary table f03_corpus_slice_excluded(",
    "  vector_id text primary key,",
    "  reason text not null",
    ") on commit drop;",
    "");
  lines.push("insert into f03_corpus_slice_excluded(vector_id, reason) values");
  excluded.forEach((row, index) => {
    lines.push(`  (${sqlText(row.vector.id)}, ${sqlText(row.reason)})${index === excluded.length - 1 ? ";" : ","}`);
  });
  lines.push("");

  lines.push(
    "do $corpus_slice$",
    "declare c record; v_refusal text; v_failures integer := 0; v_run integer := 0; v_excluded integer;",
    "begin",
    "  for c in select * from f03_corpus_slice_case order by vector_id, slice_index loop",
    "    v_run := v_run + 1;",
    "    v_refusal := ops.engineering_receipt_design_contract_refusal(c.slice);",
    "    if c.expect = 'accepted' and v_refusal is not null then",
    "      v_failures := v_failures + 1;",
    "      raise warning 'CORPUS SLICE FAIL %[%]: expected acceptance, got refusal %',",
    "        c.vector_id, c.slice_index, v_refusal;",
    "    elsif c.expect = 'rejected' and v_refusal is null then",
    "      v_failures := v_failures + 1;",
    "      raise warning 'CORPUS SLICE FAIL %[%]: expected a refusal, the contract was accepted',",
    "        c.vector_id, c.slice_index;",
    "    end if;",
    "  end loop;",
    "  select count(*) into v_excluded from f03_corpus_slice_excluded;",
    "  if v_failures > 0 then",
    "    raise exception 'CORPUS SLICE: % of % shared slice cases diverge from the receipt validator',",
    "      v_failures, v_run;",
    "  end if;",
    "  raise notice 'CORPUS SLICE: % shared slice cases matched, % plan-level vectors excluded by reason',",
    "    v_run, v_excluded;",
    "end $corpus_slice$;",
    "");
  return { text: lines.join("\n"), accepted, rejected, excluded };
}

/** Produce both generated files as {absolute path: text}, from the corpus on disk. */
export function generateF03SqlCorpus() {
  const raw = readFileSync(CORPUS_PATH);
  const corpusDigest = createHash("sha256").update(raw).digest("hex");
  const corpus = JSON.parse(raw.toString("utf8"));
  return {
    [PLAN_OUTPUT_PATH]: planPart(corpus, corpusDigest).text,
    [SLICE_OUTPUT_PATH]: slicePart(corpus, corpusDigest).text,
  };
}

function main(argv) {
  const write = argv.includes("--write");
  const generated = generateF03SqlCorpus();
  let stale = 0;
  for (const [target, text] of Object.entries(generated)) {
    const relative = path.relative(path.join(HERE, "..", ".."), target);
    if (write) { writeFileSync(target, text); process.stdout.write(`wrote ${relative}\n`); continue; }
    let onDisk = null;
    try { onDisk = readFileSync(target, "utf8"); } catch { onDisk = null; }
    if (onDisk === text) { process.stdout.write(`current ${relative}\n`); continue; }
    stale += 1;
    process.stdout.write(onDisk === null
      ? `MISSING ${relative}\n`
      : `STALE   ${relative} (on disk ${onDisk.length} bytes, generated ${text.length} bytes)\n`);
  }
  if (stale) {
    process.stdout.write(`\n${stale} generated file(s) do not match the corpus. Regenerate with:\n`
      + `    node ${GENERATOR_REF} --write\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
