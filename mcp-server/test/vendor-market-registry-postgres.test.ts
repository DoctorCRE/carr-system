import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import test from "node:test";
import { Validator } from "@cfworker/json-schema";
import pg from "pg";

import { acquireDisposablePostgres } from "./helpers/disposable-postgres.ts";

const registryPath = "../src/tools.js";
const registry: unknown = await import(registryPath);
assert.ok(typeof registry === "object" && registry !== null && "executeRegisteredTool" in registry);
const invoke = registry.executeRegisteredTool;
assert.ok(typeof invoke === "function");
function object(value: unknown): Record<string, unknown> {
  assert.ok(typeof value === "object" && value !== null && !Array.isArray(value));
  return Object.fromEntries(Object.entries(value));
}
function objects(value: unknown) {
  assert.ok(Array.isArray(value));
  return value.map(object);
}
function named(value: unknown, key: string, id: string) {
  const row = objects(value).find((x) => x[key] === id);
  assert.ok(row);
  return row;
}
const tools = object(object(registry).TOOLS);
const read = object(tools["read-vendor-markets-v1"]);

test("reviewed market registry read and constraints", async (t) => {
  const fixture = await acquireDisposablePostgres({ prefix: "vendor-market-", snapshot: true, baseline: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async () => {
    try {
      await c.end();
    } finally {
      await fixture.close();
    }
  });
  if (!(await c.query("select to_regclass('public.vendor_market') present")).rows[0]?.present)
    await c.query(readFileSync(new URL("../../migrations/1173_vendor_market_registry.sql", import.meta.url), "utf8"));
  const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  const call = async (args: { market?: string } = {}, who = actor, tenant = "carr-internal") => {
    await c.query("begin read only");
    try {
      await c.query("select set_config('carr.organization_tenant_id',$1,true)", [tenant]);
      await c.query("set local role carr_reader");
      const observed: unknown = await invoke(c, who, "read-vendor-markets-v1", args);
      const result = object(observed);
      await c.query("commit");
      return result;
    } catch (error) {
      await c.query("rollback");
      throw error;
    }
  };
  assert.equal(read.write, false);
  assert.deepEqual(await call({ market: "Sandestin" }), {
    ok: true,
    schema_version: "vendor-markets.v1",
    state: "empty",
    selected_market: null,
    markets: [],
    vendors: [],
  });
  for (const [slug, kind, boundary] of [
    ["sandestin", "market", "Synthetic Sandestin boundary; statewide Florida not implied."],
    ["other", "market", "Synthetic second boundary."],
    ["panhandle", "regional", "Synthetic Florida Panhandle coverage; statewide Florida not implied."],
  ])
    await c.query(
      "insert into vendor_market(slug,name,kind,boundary,source,reviewed_by,reviewed_at) values($1,$1,$2,$3,$4,$5,now())",
      [slug, kind, boundary, "Synthetic reviewed fixture", actor.id],
    );
  await c.query("insert into vendor_market_key values('sand destin','sandestin')");
  const vendor = async (name: string, territory: string) => {
    const p = (
      await c.query("insert into party(kind,name,created_by,updated_by) values('person',$1,$2,$2) returning id", [
        name,
        actor.id,
      ])
    ).rows[0].id;
    return (
      await c.query(
        "insert into vendor(party_id,vendor_ref,category,territory,created_by,updated_by) values($1,$2,'banker',$3,$4,$4) returning id",
        [p, `V-SYN-${randomUUID()}`, territory, actor.id],
      )
    ).rows[0].id;
  };
  const multi = await vendor("Synthetic multi", "Opaque unrelated territory");
  const regional = await vendor("Synthetic regional", "Sandestin");
  const undeclared = await vendor("Synthetic undeclared", "Sandestin Florida Panhandle");
  for (const [market, id, office, coverage] of [
    ["sandestin", multi, true, "Explicit local service"],
    ["other", multi, false, "Explicit second service"],
    ["panhandle", regional, false, "Explicit Florida Panhandle service"],
  ])
    await c.query("insert into vendor_market_membership values($1,$2,$3,$4,$5,$6,now())", [
      market,
      id,
      office,
      coverage,
      "Synthetic reviewed fixture",
      actor.id,
    ]);
  await assert.rejects(
    c.query("insert into vendor_market_membership select * from vendor_market_membership limit 1"),
    /duplicate key/,
  );
  await assert.rejects(c.query("insert into vendor_market_key values('sandestin','other')"), /duplicate key/);
  await c.query(
    "insert into vendor_market_regional_reference(market_slug,regional_slug,note,source,reviewed_by,reviewed_at) values('sandestin','panhandle','Regional reference, not local membership','Synthetic fixture',$1,now())",
    [actor.id],
  );
  const result = await call({ market: "  SAND DESTIN  " });
  assert.equal(result.selected_market, "sandestin");
  assert.equal(result.state, "ready");
  const local = named(result.markets, "slug", "sandestin");
  assert.equal(local.vendor_count, 1);
  assert.deepEqual(
    objects(local.memberships).map((m) => m.vendor_id),
    [multi],
  );
  assert.equal(objects(local.regional_references)[0]?.regional_slug, "panhandle");
  assert.equal(objects(local.memberships)[0]?.office_presence, true);
  assert.equal(objects(local.memberships)[0]?.service_coverage, "Explicit local service");
  const person = named(result.vendors, "id", multi);
  assert.deepEqual(person.market_slugs, ["other", "sandestin"]);
  assert.equal(objects(person.people).length, 1);
  assert.equal(objects(person.people)[0]?.office, null);
  assert.equal(object(objects(person.people)[0]?.contact).email, null);
  assert.equal(
    objects(result.vendors).some((v) => v.id === undeclared),
    false,
  );
  assert.equal(named(result.markets, "slug", "panhandle").vendor_count, 1);
  await assert.rejects(call({ market: "unknown" }), /vendor_market_not_found/);
  await assert.rejects(call({}, actor, "foreign-tenant"), /vendor_market_tenant_refused/);
  await assert.rejects(call({}, { ...actor, human: false }), /partner_required/);
  const contract = JSON.parse(readFileSync(new URL("../contracts/vendor-markets.v1.json", import.meta.url), "utf8"));
  assert.equal(result.schema_version, contract.schema_version);
  assert.equal(new Validator(contract.response_schema).validate(result).valid, true);
  if (process.env.RECORD_VENDOR_MARKET_FIXTURE === "1")
    writeFileSync(
      new URL("../contracts/fixtures/vendor-markets.v1.json", import.meta.url),
      `${JSON.stringify(result, null, 2)}\n`,
    );
  const recorded = JSON.parse(
    readFileSync(new URL("../contracts/fixtures/vendor-markets.v1.json", import.meta.url), "utf8"),
  );
  assert.equal(new Validator(contract.response_schema).validate(recorded).valid, true);
  if (process.env.RECORD_VENDOR_MARKET_FIXTURE === "1")
    writeFileSync(
      new URL("../contracts/fixtures/vendor-markets.v1.json", import.meta.url),
      `${JSON.stringify(result, null, 2)}\n`,
    );
  await c.query("update party set deleted_at=now() where id=(select party_id from vendor where id=$1)", [regional]);
  assert.equal(named((await call()).markets, "slug", "panhandle").vendor_count, 0);
  await c.query("update party set deleted_at=null where id=(select party_id from vendor where id=$1)", [regional]);
  await c.query("update vendor set merged_into=$1 where id=$2", [undeclared, multi]);
  assert.equal(named((await call()).markets, "slug", "sandestin").vendor_count, 0);
  await c.query(
    "insert into vendor_market(slug,name,kind,boundary,source,reviewed_by,reviewed_at) select 'cap-'||n,'Synthetic cap','market','Explicit synthetic boundary','Synthetic source',$1,now() from generate_series(1,101) n",
    [actor.id],
  );
  await assert.rejects(call(), /vendor_market_capacity_exceeded/);
});
