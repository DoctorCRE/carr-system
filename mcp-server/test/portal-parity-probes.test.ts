import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import {
  expand,
  loadProbes,
  neutralize,
  resolveField,
  schemaHasField,
  sideAliases,
  siteOf,
} from "../portal-parity/probes.ts";
import { selectRows } from "../portal-parity/rows.ts";

test("probe aliases expand recursively and neutralize longest values first", () => {
  const aliases = { brand: "client", logo: "client-logo" };
  assert.equal(siteOf("A-G01"), "REF-A");
  assert.equal(siteOf("V-G01"), "REF-V");
  assert.equal(siteOf("X-G01"), undefined);
  assert.deepEqual(sideAliases({ aliases }, "REF-A", { "REF-A": { brand: "renamed" } }), {
    brand: "renamed",
    logo: "client-logo",
  });
  assert.deepEqual(expand({ a: ["@{logo}", null, 3, false], b: "@{brand}" }, aliases), {
    a: ["client-logo", null, 3, false],
    b: "client",
  });
  assert.equal(neutralize("client-logo client", aliases), "@{logo} @{brand}");
});

test("package pointers distinguish missing and empty values and decode escaped segments", () => {
  const pkg = { "a/b": { "~key": [{ value: "" }, { value: 0 }] }, empty: [], nil: null };
  assert.equal(resolveField(pkg, "/a~1b/~0key/*/value"), true);
  for (const pointer of ["/empty", "/nil", "/missing", "/a~1b/~0key/0/value"])
    assert.equal(resolveField(pkg, pointer), false);
  assert.equal(
    schemaHasField(
      { $defs: { item: { properties: { label: {} } } }, properties: { items: { items: { $ref: "#/$defs/item" } } } },
      "/items/*/label",
    ),
    true,
  );
  assert.equal(schemaHasField({ anyOf: [{ patternProperties: { "^x": {} } }] }, "/xyz"), true);
  assert.equal(schemaHasField({ properties: {} }, "/missing"), false);
});

test("probe loading refuses undeclared aliases, kinds, differences and duplicate IDs", async (t) => {
  const dir = await mkdtemp(path.join(tmpdir(), "parity-invalid-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, "probes.json");
  const probe = { id: "A-G02", kind: "dom", td: "T", target: "@{missing}" };
  const cases: [Record<string, unknown>, RegExp][] = [
    [{ ...probe }, /undeclared alias missing/],
    [{ ...probe, target: "body", kind: "nope" }, /unknown kind nope/],
    [{ ...probe, target: "body", allowed: ["AD2"] }, /allowed difference AD2/],
    [{ ...probe, target: "body", td: "D" }, /list package field paths/],
    [{ ...probe, target: "body", fields: ["bad"] }, /not a pointer/],
    [{ ...probe, target: "body", retired: "AD1" }, /only AD8/],
  ];
  for (const [row, message] of cases) {
    await writeFile(
      file,
      JSON.stringify({
        schema: "carr.portal-parity-probes.v1",
        aliases: {},
        allowed_differences: { AD1: [] },
        probes: [row],
      }),
    );
    await assert.rejects(loadProbes(file), message);
  }
  await writeFile(
    file,
    JSON.stringify({
      schema: "carr.portal-parity-probes.v1",
      aliases: {},
      probes: [
        { ...probe, target: "body" },
        { ...probe, target: "body" },
      ],
    }),
  );
  await assert.rejects(loadProbes(file), /duplicate id/);
});

test("probe loading keeps the default when allowed differences are omitted", async (t) => {
  const dir = await mkdtemp(path.join(tmpdir(), "parity-default-differences-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const file = path.join(dir, "probes.json");
  const doc = {
    schema: "carr.portal-parity-probes.v1",
    aliases: {},
    probes: [{ id: "A-G01", kind: "dom", td: "T", target: "body" }],
  };
  await writeFile(file, JSON.stringify(doc));
  assert.deepEqual(await loadProbes(file), doc);
});

for (const allowed_differences of ["bad", [[]]]) {
  test(`probe loading refuses malformed allowed differences ${JSON.stringify(allowed_differences)}`, async (t) => {
    const dir = await mkdtemp(path.join(tmpdir(), "parity-differences-"));
    t.after(() => rm(dir, { recursive: true, force: true }));
    const file = path.join(dir, "probes.json");
    await writeFile(
      file,
      JSON.stringify({
        schema: "carr.portal-parity-probes.v1",
        aliases: {},
        allowed_differences,
        probes: [{ id: "A-G01", kind: "dom", td: "T", target: "body" }],
      }),
    );
    await assert.rejects(loadProbes(file), {
      message: "allowed difference 0 must list the aspects it may differ in",
    });
  });
}

test("row selection preserves inventory order and refuses malformed ranges", () => {
  const ids = ["A-T01", "A-T02", "A-T03", "V-G01"];
  assert.deepEqual(selectRows("A-T03,A-T01..A-T02,A-T01", ids), ids.slice(0, 3));
  assert.deepEqual(selectRows(null, ids), ids);
  for (const spec of ["A-T03..A-T01", "A-T01..V-G01", "A-T01..A-T02..A-T03", ",", "A-T99"])
    assert.throws(() => selectRows(spec, ids));
  assert.throws(() => selectRows("bad..bad", ["bad"]), /not an inventory ID/);
});
