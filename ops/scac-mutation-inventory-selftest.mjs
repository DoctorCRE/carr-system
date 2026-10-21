import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mcpInventory } from "./scac-mutation-inventory.mjs";
import { TOOLS } from "../mcp-server/src/tools.js";
import * as inventory from "./scac-mutation-inventory.mjs";

test("the caller census includes direct, helper-mediated, and dynamic lifecycle writers", () => {
  const rows = new Map(mcpInventory(TOOLS).map(row => [row.operation, row]));
  for (const [table, verbs] of Object.entries({
    "public.deal": ["update-deal", "new-deal", "reassign-deal", "set-lead", "set-next-action",
      "create-national-market-deal", "patch-deal-field", "revert-deal-field", "resolve-conflict",
      "set-next-step", "add-deal-note"],
    "public.client": ["new-client", "set-prospect-engagement-status", "create-national-account",
      "create-national-market-deal", "confirm-merge"],
  })) {
    for (const verb of verbs)
      assert.ok(rows.get(verb)?.tables_written?.includes(table), `${verb} writes ${table}`);
  }
  for (const table of ["public.lead", "public.client", "public.vendor"])
    assert.ok(rows.get("confirm-merge").tables_written.includes(table), `confirm-merge writes ${table}`);
  assert.ok(!rows.get("get-deal-room").tables_written.includes("public.deal"));
});

test("SQL targets come from executable handler SQL, with aliases, schema names, and bounded table loops", () => {
  const rows = mcpInventory({ fixture: {
    registrySource: "mcp-server/src/tools.js", write: true,
    handler: async c => {
      await c.query('update "public"."deal" d set name=$1');
      await c.query("delete from client");
      for (const table of ["lead", "vendor"]) await c.query(`insert into ${table} (id) values ($1)`);
      await c.query("select * from party for update");
      await c.query("select 'update client set notes' as example");
    },
  } });
  assert.deepEqual(rows[0].tables_written, ["public.client", "public.deal", "public.lead", "public.vendor"]);
});

test("unresolved SQL table expressions refuse rather than silently claiming empty coverage", () => {
  assert.throws(() => mcpInventory({ fixture: {
    registrySource: "mcp-server/src/tools.js",
    handler: async (c, table) => c.query(`update ${table} set name=$1`),
  } }), /unresolved SQL table table/);
});

test("SQL examples, comments, and row locks do not become database writers", () => {
  const [row] = mcpInventory({ fixture: {
    registrySource: "mcp-server/src/tools.js",
    handler: async c => {
      await c.query("select 'update client set notes' as example");
      await c.query("select * from deal for update");
      await c.query("select 1 /* update deal set name='example' */");
    },
  } });
  assert.deepEqual(row.tables_written, []);
});

test("the version-bound evidence names helper sites and all wave-one inserts without authorizing retirement", () => {
  assert.equal(typeof inventory.legacyLifecycleCensus, "function");
  const census = inventory.legacyLifecycleCensus(TOOLS);
  assert.equal(census.retires_any_caller, false);
  assert.equal(census.establishes_migration_readiness, false);
  assert.match(census.source_set_digest, /^sha256:[a-f0-9]{64}$/);
  for (const helper of ["applyDealRoomField", "appendDealNextStep", "supersedeOverdueDealAction"])
    assert.ok(census.write_sites.some(site => site.caller === helper && site.table === "public.deal"), helper);
  assert.deepEqual(census.write_sites.filter(site => site.source_locator === "pipelines/import_wave1.py")
    .map(site => [site.line, site.table]), [[320, "public.client"], [367, "public.client"], [385, "public.deal"]]);
  assert.ok(census.write_sites.some(site => site.source_locator === "ops/cre-lifecycle-local-pg-gate.py" &&
    site.line === 230 && site.table === "public.deal"), "local verification delete is also a source writer");
  assert.deepEqual(census.script_operations.find(row => row.operation === "pipelines/import_wave1.py").tables_written,
    ["public.client", "public.deal"]);
  assert.equal(census.doctorcre_app.direct_database_writers, 0);
  assert.equal(census.doctorcre_app.proxy, "env.CARR.fetch");
  assert.match(census.doctorcre_app.source_commit, /^[a-f0-9]{40}$/);
  assert.match(census.doctorcre_app.source_digest, /^sha256:[a-f0-9]{64}$/);
});

test("freshness accepts the exact generated census and rejects a removed writer", () => {
  assert.equal(typeof inventory.assertLegacyLifecycleCensus, "function");
  const rendered = inventory.renderLegacyLifecycleCensus(TOOLS);
  assert.equal(inventory.assertLegacyLifecycleCensus(TOOLS, rendered), true);
  const stale = JSON.parse(rendered);
  stale.operations = stale.operations.filter(row => row.operation !== "confirm-merge");
  assert.throws(() => inventory.assertLegacyLifecycleCensus(TOOLS, JSON.stringify(stale, null, 2) + "\n"), /census is stale/);
  assert.equal(readFileSync(new URL("../mcp-server/src/legacy-lifecycle-callers.generated.json", import.meta.url), "utf8"), rendered);
});
