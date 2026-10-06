// THE AUDIT HELPER TAKES POSITIONAL ARGUMENTS, AND THE OBJECT FORM IS SILENT.
//
// `writeEvent` in src/tools.js is
//   writeEvent(client, actor, verb, subjectType, subjectId, fields = {})
// A caller who instead writes `writeEvent(c, { subject_type, subject_id, verb,
// payload })` passes an options object where the actor belongs. Nothing in
// JavaScript objects to it: the actor is a plain object with no `id`, and the
// verb, subject type and subject id all arrive undefined. The mistake surfaces
// only at the audit insert, as event.actor_id not_null_violation, which rolls
// back the whole enclosing transaction — so the verb's real work is lost too,
// and the caller sees a constraint message that names nothing they wrote.
//
// That shipped in all three work-portfolio.js write verbs and was found by the
// first live propose-portfolio-revision, the DoctorCRE v5 constitution itself,
// on 2026-09-12 (defect 65ed5e3e-db28-498a-a0e7-84ae53658dea). Five more sites
// in two v5 store modules carried the identical shape, unregistered at the time
// and therefore unable to fail in any test that existed.
//
// THIS IS THE CLASS GUARD, and it is deliberately a source scan rather than a
// behavioural test: the defect's whole character is that an unregistered verb
// cannot be driven, so the only check that catches the NEXT one before it is
// wired is one that reads every line of src/ whether or not anything calls it.
// The per-handler recording-double tests beside it prove the registered verbs
// pass the right values; this proves nobody anywhere has reintroduced the shape.
//
// THE SCAN READS WHOLE FILES, NOT LINES. The first version of this guard
// pre-filtered to lines containing the literal `writeEvent(`, which quietly
// exempted every call a formatter might write as `writeEvent (c, {` or break
// after the identifier — the guard claimed to cover the repository and did not
// (re-review of PR 1008, 2026-09-12). Whitespace and newlines are now tolerated
// everywhere inside the call head, and the reported line number is derived from
// the match offset rather than from a loop index.
//
// MUTATION CONTROL: the executable fixtures below write the three object-form
// spellings and a legitimate positional call whose SIXTH argument is an object
// to a temp directory and run this same scanner over it; the three are expected
// to be flagged, the positional one to pass. An earlier control, run 2026-09-12
// against real source, converted benchmark-acceptance-store.v5.js:959 back to
// the object form and confirmed this test failed naming that file and line.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../src", import.meta.url));

/** Every .js/.mjs file under dir, recursively. */
function sourceFiles(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) { found.push(...sourceFiles(path)); continue; }
    if (/\.(js|mjs)$/.test(entry)) found.push(path);
  }
  return found;
}

// The object form: any writeEvent call whose SECOND argument opens an object
// literal. Every gap inside the call head — before the paren, around the first
// argument, around the comma — may be any run of whitespace INCLUDING newlines,
// so a formatter cannot hide a call from this by breaking it across lines. The
// positional form's second argument is always an identifier (`actor`), so the
// `{` required immediately after the first comma cannot match it.
const OBJECT_FORM = /writeEvent\s*\(\s*[A-Za-z_$][\w$]*\s*,\s*\{/;
// Any call at all, used only to prove the sweep is still finding call sites.
const ANY_CALL = /\bwriteEvent\s*\(/;

/** 1-based line number of a character offset in text. */
function lineOf(text, offset) {
  let line = 1;
  for (let i = 0; i < offset; i += 1) if (text[i] === "\n") line += 1;
  return line;
}

/**
 * Scan whole file contents. Returns the object-form hits (file:line plus the
 * line the call starts on) and the number of writeEvent call sites seen.
 */
function scanTree(dir) {
  const files = sourceFiles(dir);
  const hits = [];
  let calls = 0;
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const lines = text.split("\n");
    const label = file.slice(dir.length + 1);

    for (const _ of text.matchAll(new RegExp(ANY_CALL.source, "g"))) calls += 1;

    for (const match of text.matchAll(new RegExp(OBJECT_FORM.source, "g"))) {
      const line = lineOf(text, match.index);
      hits.push(`${label}:${line}: ${lines[line - 1].trim()}`);
    }
  }
  return { files, hits, calls };
}

test("no writeEvent call in mcp-server/src uses the object form", () => {
  const { files, hits, calls } = scanTree(SRC);
  // A collector that found nothing would pass this test while proving nothing;
  // the counts are asserted so an empty sweep fails instead.
  assert.ok(files.length > 50, `the source sweep found only ${files.length} files`);
  assert.ok(calls > 50, `the sweep found only ${calls} writeEvent call sites`);

  assert.deepEqual(hits, [],
    `writeEvent takes (client, actor, verb, subjectType, subjectId, fields); ` +
    `these calls pass an options object as the actor:\n${hits.join("\n")}`);
});

test("the scanner catches every spelling of the object form and spares the positional one", () => {
  // The mutation control, kept executable rather than only described above:
  // real files on disk, scanned by the same function the real sweep uses.
  const dir = mkdtempSync(join(tmpdir(), "write-event-call-shape-"));
  try {
    // The plain form, exactly as it shipped.
    writeFileSync(join(dir, "plain.js"), [
      "export async function record(c) {",
      '  await writeEvent(c, { subject_type: "portfolio", verb: "x" });',
      "}",
      "",
    ].join("\n"));

    // A space between the identifier and the paren — invisible to a scan that
    // pre-filters on the literal `writeEvent(`.
    writeFileSync(join(dir, "spaced.js"), [
      "export async function record(c) {",
      '  await writeEvent (c, { subject_type: "portfolio", verb: "x" });',
      "}",
      "",
    ].join("\n"));

    // The call head broken across three lines, in a nested directory so the
    // recursive walk is exercised too.
    mkdirSync(join(dir, "nested"));
    writeFileSync(join(dir, "nested", "wrapped.js"), [
      "export async function record(c) {",
      "  await writeEvent(",
      "    c,",
      "    {",
      '      subject_type: "portfolio",',
      "    });",
      "}",
      "",
    ].join("\n"));

    // The legitimate call: six positional arguments, the last an object. This
    // one must NOT be flagged, including when it is broken across lines.
    writeFileSync(join(dir, "positional.js"), [
      "export async function record(c, actor, id) {",
      '  await writeEvent(c, actor, "propose-portfolio-revision", "portfolio", id, {',
      '    payload: { note: "fine" },',
      "  });",
      "  await writeEvent(",
      "    c,",
      "    actor,",
      '    "accept-portfolio-revision",',
      '    "portfolio",',
      "    id,",
      "    { payload: {} },",
      "  );",
      "}",
      "",
    ].join("\n"));

    const { hits, calls } = scanTree(dir);
    // The hit list is asserted BEFORE the call count so that a scanner which
    // has narrowed back to some subset of the spellings names the spelling it
    // dropped, rather than failing first on an arithmetic mismatch.
    assert.deepEqual(hits.map((hit) => hit.split(":").slice(0, 2).join(":")).sort(), [
      "nested/wrapped.js:2",
      "plain.js:2",
      "spaced.js:2",
    ]);
    assert.equal(calls, 5, `the fixture sweep found ${calls} call sites, expected 5`);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("the scanner actually matches the object form it is looking for", () => {
  // The same shapes asserted against the pattern directly, so a regression in
  // the pattern is named even if the fixture plumbing changes.
  assert.ok(OBJECT_FORM.test('await writeEvent(c, { subject_type: "benchmark",'));
  assert.ok(OBJECT_FORM.test("await writeEvent(client, {"));
  assert.ok(OBJECT_FORM.test("await writeEvent (c, {"));
  assert.ok(OBJECT_FORM.test("await writeEvent(\n  c,\n  {"));
  assert.equal(
    OBJECT_FORM.test('await writeEvent(c, actor, "propose-portfolio-revision", "portfolio", id,'),
    false);
  assert.equal(OBJECT_FORM.test("await writeEvent(c, actor, verb, \"deal\", dealId, {"), false);
  assert.equal(OBJECT_FORM.test("await writeEvent(\n  c,\n  actor,\n  verb,\n  \"deal\",\n  id,\n  {"), false);
});
