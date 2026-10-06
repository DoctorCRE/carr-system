import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  assertReadOnly, decodeCursor, encodeCursor, readWorkInventoryCensus,
  WORK_INVENTORY_KINDS, WORK_INVENTORY_LEGS, WORK_INVENTORY_LIMIT_MAX, WORK_INVENTORY_PATH,
} from "../src/work-inventory-census.v5.js";

const JOE = { slug: "joe" };
const NOW = () => new Date("2026-09-16T12:00:00.000Z");
const CORRELATION = "corr-census";

// One row per kind, deliberately spanning statuses a capped active queue hides:
// superseded, declined, dormant, dropped.
const ROWS = {
  "ops.work_request": [
    { id: "WR-000100", version: 7, title: "Live work request", status: "in_progress",
      updated_at: "2026-09-16T11:00:00.000Z", organization_tenant_id: "carr-internal",
      related_work_request: null, related_origin: "doctrine:control-room#census" },
    { id: "WR-000041", version: 3, title: "Superseded work request", status: "superseded",
      updated_at: "2026-09-15T10:00:00.000Z", organization_tenant_id: "carr-internal",
      related_work_request: "WR-000100", related_origin: null },
    { id: "WR-000099", version: 1, title: "Other tenant work request", status: "captured",
      updated_at: "2026-09-16T11:30:00.000Z", organization_tenant_id: "other-tenant",
      related_work_request: null, related_origin: null },
  ],
  "ops.portfolio_node": [
    { id: "V5-F01", version: 2, title: "V5-F01", status: "slice",
      updated_at: "2026-09-14T09:00:00.000Z", organization_tenant_id: null,
      related_parent: "product-journeys", related_child: "product-journeys" },
  ],
  loop_item: [
    { id: "250", version: 4, title: "Living orb panel visual", status: "open",
      updated_at: "2026-09-13T08:00:00.000Z", organization_tenant_id: null, related_domain: "surface" },
    { id: "118", version: 9, title: "Dormant loop", status: "dropped",
      updated_at: "2026-09-12T08:00:00.000Z", organization_tenant_id: null, related_domain: null },
  ],
  "ops.work_shape_revision": [
    { id: "3f1c0f1e-0000-4000-8000-000000000001", version: 2, title: "Shaped work",
      status: "not_required", updated_at: "2026-09-11T08:00:00.000Z",
      organization_tenant_id: "carr-internal", related_work_request: "WR-000100" },
  ],
  "ops.engineering_slice_plan": [
    { id: "3f1c0f1e-0000-4000-8000-000000000002", version: 5, title: "Census slice plan",
      status: "registered", updated_at: "2026-09-10T08:00:00.000Z",
      organization_tenant_id: "carr-internal", related_work_request: "WR-000100" },
  ],
  "ops.rule_admission": [
    { id: "3f1c0f1e-0000-4000-8000-000000000003", version: 1, title: "A rule awaiting approval",
      status: "admitted", updated_at: "2026-09-09T08:00:00.000Z",
      organization_tenant_id: null, related_intake: null },
  ],
};

const TOTALS = {
  "ops.work_request": 412, "ops.portfolio_node": 21, loop_item: 573,
  "ops.work_shape_revision": 9, "ops.engineering_slice_plan": 14, "ops.rule_admission": 218,
};

function keyFor(sql) {
  for (const key of Object.keys(ROWS)) if (sql.includes(`from ${key}`)) return key;
  return null;
}

/** Fake client keyed on the SQL text alone — no database, no schema knowledge. */
function makeClient({ throwOn = null, throwCode = "ECONNREFUSED", totals = TOTALS, rows = ROWS } = {}) {
  const seen = [];
  return {
    seen,
    async query(sql, params) {
      seen.push({ sql, params });
      const key = keyFor(sql);
      assert.ok(key, `unmapped leg SQL: ${sql.slice(0, 80)}`);
      if (throwOn === key) throw Object.assign(new Error("leg down"), { code: throwCode });
      if (sql.includes("count(*)")) {
        const total = totals[key];
        return { rows: [{ count: total === undefined ? null : total }] };
      }
      const cursorAt = params.find((value) => typeof value === "string" && value.endsWith("Z")) || null;
      const limit = params[params.length - 1];
      const statuses = params.find((value) => Array.isArray(value)) || null;
      let list = rows[key] || [];
      if (statuses) list = list.filter((row) => statuses.includes(row.status));
      if (cursorAt) list = list.filter((row) => row.updated_at <= cursorAt);
      return { rows: list.slice(0, limit) };
    },
  };
}

const read = (overrides = {}) => readWorkInventoryCensus({
  client: makeClient(), actor: JOE, correlationId: CORRELATION, now: NOW, ...overrides,
});

test("the path is the one contract constant and every leg SQL is a read", () => {
  assert.equal(WORK_INVENTORY_PATH, "/api/v1/work-inventory");
  assert.deepEqual(WORK_INVENTORY_LEGS.map((leg) => leg.kind), WORK_INVENTORY_KINDS);
  assert.equal(WORK_INVENTORY_LEGS.length, 6);
  for (const leg of WORK_INVENTORY_LEGS) {
    assert.equal(assertReadOnly(leg.rowsSql), true, `${leg.kind} rowsSql must be a select`);
    assert.equal(assertReadOnly(leg.countSql), true, `${leg.kind} countSql must be a select`);
  }
  // The predicate has to be able to fail, or it proves nothing.
  assert.equal(assertReadOnly("update ops.work_request set state='x'"), false);
  assert.equal(assertReadOnly("select 1 from ops.work_request for update"), false);
  assert.equal(assertReadOnly("select 1 from t where true; delete from t"), false);
  // ...and must NOT fire on the column names every leg legitimately selects.
  assert.equal(assertReadOnly("select updated_at, created_at from t"), true);
});

test("the census returns every kind, enumerates hidden statuses and orders deterministically", async () => {
  const payload = await read();
  assert.deepEqual(Object.keys(payload).sort(), [
    "census_complete", "coverage", "items", "kinds", "limit", "next_cursor",
    "source", "statuses", "tenant", "viewer",
  ]);
  assert.equal(payload.viewer, "joe");
  assert.equal(payload.tenant, "carr-internal");
  assert.equal(payload.statuses, null, "default statuses must be all, not a curated active set");
  assert.deepEqual(payload.kinds, WORK_INVENTORY_KINDS);
  assert.deepEqual([...new Set(payload.items.map((item) => item.kind))].sort(), [...WORK_INVENTORY_KINDS].sort());
  const statuses = payload.items.map((item) => item.status);
  assert.ok(statuses.includes("superseded"), "a superseded work request must remain discoverable");
  assert.ok(statuses.includes("dropped"), "a dormant/dropped loop must remain discoverable");
  const order = payload.items.map((item) => item.updated_at);
  assert.deepEqual(order, [...order].sort().reverse());
  const wr = payload.items.find((item) => item.id === "WR-000100");
  assert.equal(wr.source_ref, "ops.work_request");
  assert.equal(wr.version, "7");
  assert.equal(wr.open, "/system-work.html");
  assert.deepEqual(wr.related, [{ kind: "doctrine_section", id: "doctrine:control-room#census" }]);
  assert.equal(payload.items.find((item) => item.kind === "loop").open, null, "never invent a route");
  assert.equal(payload.census_complete, true);
  assert.equal(payload.source.freshness, "fresh");
  assert.equal(payload.source.correlation_id, CORRELATION);
  assert.equal(payload.source.observed_at, "2026-09-16T12:00:00.000Z");
  assert.equal(payload.source.valid_until, "2026-09-16T12:01:00.000Z");
});

test("a row scoped to another tenant never reaches the response", async () => {
  const payload = await read();
  assert.equal(payload.items.some((item) => item.id === "WR-000099"), false);
  const wrCoverage = payload.coverage.find((entry) => entry.kind === "work_request");
  assert.equal(wrCoverage.excluded_other_tenant, 1);
});

test("an item carrying no source links is flagged unlinked", async () => {
  const payload = await read();
  const dormant = payload.items.find((item) => item.id === "118");
  assert.deepEqual(dormant.related, []);
  assert.equal(dormant.unlinked, true);
  assert.equal(payload.items.find((item) => item.id === "250").unlinked, false);
});

test("coverage reports complete, partial and unavailable, and an outage never empties the census", async () => {
  const complete = await read();
  for (const entry of complete.coverage) {
    assert.equal(entry.state, "complete", `${entry.kind} should be complete`);
    assert.equal(entry.reason, null);
    assert.equal(typeof entry.count_total, "number");
    assert.ok(entry.count_returned > 0);
  }

  const outage = await readWorkInventoryCensus({
    client: makeClient({ throwOn: "loop_item", throwCode: "ECONNREFUSED" }),
    actor: JOE, correlationId: CORRELATION, now: NOW,
  });
  const down = outage.coverage.find((entry) => entry.kind === "loop");
  assert.equal(down.state, "unavailable");
  assert.equal(down.reason, "DEPENDENCY_UNAVAILABLE");
  assert.equal(down.count_returned, 0);
  assert.equal(down.count_total, null);
  assert.equal(outage.census_complete, false);
  assert.equal(outage.source.freshness, "unknown");
  assert.match(outage.source.safe_explanation, /INCOMPLETE, not empty/);
  // The other five legs still answer: an outage is explicit, not an empty list.
  assert.ok(outage.items.length > 0);
  assert.equal(outage.items.some((item) => item.kind === "loop"), false);
  assert.deepEqual(
    outage.coverage.filter((entry) => entry.state === "complete").map((entry) => entry.kind).sort(),
    ["governance_item", "portfolio_node", "slice_plan", "work_request", "work_shape"],
  );

  // A MISSING GRANT IS A DEPENDENCY, NOT A DEFECT. public.rule is readable only
  // by carr_writer/carr_authority, so a leg that reached it would 42501 on every
  // production request; if that surfaced as INTERNAL_ERROR nobody would look for
  // a grant. The governance leg no longer joins it, and this asserts the code.
  const denied = await readWorkInventoryCensus({
    client: makeClient({ throwOn: "ops.rule_admission", throwCode: "42501" }),
    actor: JOE, correlationId: CORRELATION, now: NOW,
  });
  const refusedLeg = denied.coverage.find((entry) => entry.kind === "governance_item");
  assert.equal(refusedLeg.state, "unavailable");
  assert.equal(refusedLeg.reason, "DEPENDENCY_UNAVAILABLE");
  assert.equal(refusedLeg.source_ref, "ops.rule_admission");
  assert.equal(denied.census_complete, false);
  assert.match(denied.source.safe_explanation, /INCOMPLETE, not empty/);
  assert.equal(denied.items.some((item) => item.kind === "governance_item"), false);
  assert.ok(denied.items.length > 0, "a grant gap on one leg never empties the census");

  const uncounted = await readWorkInventoryCensus({
    client: makeClient({ totals: { ...TOTALS, "ops.portfolio_node": undefined } }),
    actor: JOE, correlationId: CORRELATION, now: NOW,
  });
  const partial = uncounted.coverage.find((entry) => entry.kind === "portfolio_node");
  assert.equal(partial.state, "partial");
  assert.equal(partial.reason, "count_unavailable");
  assert.equal(partial.count_total, null);
  assert.ok(partial.count_returned > 0, "a partial leg still returns its rows");
  assert.equal(uncounted.census_complete, false);

  // A row with no order key cannot be paged, so it is named rather than dropped in silence.
  const unorderable = await readWorkInventoryCensus({
    client: makeClient({ rows: { ...ROWS, "ops.rule_admission": [
      { ...ROWS["ops.rule_admission"][0], updated_at: null },
    ] } }),
    actor: JOE, correlationId: CORRELATION, now: NOW,
  });
  const missing = unorderable.coverage.find((entry) => entry.kind === "governance_item");
  assert.equal(missing.state, "partial");
  assert.equal(missing.reason, "rows_missing_order_key:1");
});

test("the cursor round trips: page two continues page one and repeats nothing", async () => {
  const first = await read({ limit: 3 });
  assert.equal(first.items.length, 3);
  assert.equal(first.limit, 3);
  assert.equal(typeof first.next_cursor, "string");
  assert.deepEqual(decodeCursor(first.next_cursor), {
    updated_at: first.items[2].updated_at, kind: first.items[2].kind, id: first.items[2].id,
  });
  const second = await read({ limit: 3, cursor: first.next_cursor });
  const firstKeys = first.items.map((item) => `${item.kind}:${item.id}`);
  const secondKeys = second.items.map((item) => `${item.kind}:${item.id}`);
  assert.equal(firstKeys.some((key) => secondKeys.includes(key)), false, "no item may repeat across pages");
  assert.ok(second.items[0].updated_at <= first.items[2].updated_at);
  // Walking to exhaustion covers the whole census exactly once.
  const all = [...firstKeys];
  let cursor = first.next_cursor;
  while (cursor) {
    const page = await read({ limit: 3, cursor });
    all.push(...page.items.map((item) => `${item.kind}:${item.id}`));
    cursor = page.next_cursor;
  }
  assert.equal(new Set(all).size, all.length);
  const full = await read();
  assert.deepEqual(all.sort(), full.items.map((item) => `${item.kind}:${item.id}`).sort());
  assert.equal(full.next_cursor, null);
  assert.equal(encodeCursor(full.items[0]), encodeCursor(full.items[0]));
});

test("limit is bounded and authorization refusals are typed", async () => {
  assert.equal((await read({ limit: 9999 })).limit, WORK_INVENTORY_LIMIT_MAX);
  assert.equal((await read({ limit: null })).limit, 100);
  const refusal = async (overrides, code) => {
    await assert.rejects(() => read(overrides), (error) => error.code === code, `${code} expected`);
  };
  await refusal({ actor: { slug: "mallory" } }, "AUTHORIZATION_REFUSED");
  await refusal({ actor: {} }, "AUTHORIZATION_REFUSED");
  await refusal({ tenant: "other-tenant" }, "TENANT_SCOPE_REFUSED");
  await refusal({ correlationId: "" }, "INTERNAL_ERROR");
  await refusal({ limit: 0 }, "AUTHORIZATION_REFUSED");
  await refusal({ limit: "many" }, "AUTHORIZATION_REFUSED");
  await refusal({ cursor: "not-base64-json" }, "AUTHORIZATION_REFUSED");
  await refusal({ kinds: "not_a_kind" }, "AUTHORIZATION_REFUSED");
  await assert.rejects(
    () => readWorkInventoryCensus({
      client: makeClient({ throwOn: "loop_item", throwCode: "ECONNREFUSED" }),
      actor: { slug: "mallory" }, correlationId: CORRELATION, now: NOW,
    }),
    (error) => error.code === "AUTHORIZATION_REFUSED");
});

test("kinds and statuses narrow the census without widening it", async () => {
  const client = makeClient();
  const payload = await readWorkInventoryCensus({
    client, actor: JOE, correlationId: CORRELATION, now: NOW,
    kinds: "work_request,loop", statuses: "superseded,dropped",
  });
  assert.deepEqual(payload.kinds, ["work_request", "loop"]);
  assert.deepEqual(payload.statuses, ["superseded", "dropped"]);
  assert.deepEqual(payload.coverage.map((entry) => entry.kind), ["work_request", "loop"]);
  assert.deepEqual(payload.items.map((item) => item.id), ["WR-000041", "118"]);
  assert.equal(client.seen.every(({ sql }) => assertReadOnly(sql)), true);
  assert.equal(client.seen.length, 4, "two legs, each one rows query and one count query");
});

// -----------------------------------------------------------------------------
// The check the fake client cannot make. Every leg runs as carr_reader (the role
// behind DATABASE_URL_READER), so a leg may only touch a relation the schema
// actually grants SELECT to that role. The allowlist is DERIVED from
// db/schema.sql, not written here, so it tracks the schema instead of a belief
// about it: a future leg that joins an unreadable table fails HERE rather than
// 42501-ing on every production request behind a green suite.
const REPO_ROOT = fileURLToPath(new URL("../../", import.meta.url));

function readerReadableRelations() {
  const schema = readFileSync(`${REPO_ROOT}db/schema.sql`, "utf8");
  const relations = new Set();
  for (const match of schema.matchAll(/^grant select on table ([a-z0-9_.]+) to carr_reader;$/gim)) {
    const name = match[1].toLowerCase();
    relations.add(name.includes(".") ? name : `public.${name}`);
  }
  return relations;
}

/** Relations named after `from` or `join`, normalized to a schema-qualified name. */
function relationsIn(sql) {
  const found = new Set();
  for (const match of String(sql).matchAll(/\b(?:from|join)\s+([a-z0-9_.]+)/gi)) {
    const name = match[1].toLowerCase();
    found.add(name.includes(".") ? name : `public.${name}`);
  }
  return found;
}

test("every leg reads only relations the schema grants to carr_reader", () => {
  const readable = readerReadableRelations();
  // Guard the derivation itself: an empty or tiny allowlist would make this
  // test vacuous, and a regex that silently stopped matching is exactly how a
  // check like this rots into a no-op.
  assert.ok(readable.size > 100, `derived allowlist looks wrong: ${readable.size} relations`);
  assert.equal(readable.has("ops.work_request"), true);
  // The relation that caused this test to exist is NOT readable by carr_reader.
  assert.equal(readable.has("public.rule"), false);

  for (const leg of WORK_INVENTORY_LEGS) {
    for (const sql of [leg.rowsSql, leg.countSql]) {
      const relations = [...relationsIn(sql)];
      assert.ok(relations.length > 0, `${leg.kind} names no relation`);
      for (const relation of relations) {
        assert.ok(readable.has(relation),
          `${leg.kind} reads ${relation}, which db/schema.sql does not grant to carr_reader`);
      }
    }
  }
  // The extractor has to be able to catch the defect it was written for.
  assert.equal(relationsIn("select 1 from ops.rule_admission a join rule r on r.id = a.rule_id").has("public.rule"), true);
  assert.equal(relationsIn("select 1 from loop_item").has("public.loop_item"), true);
});

test("insufficient_privilege is classed as a dependency, not an internal defect", async () => {
  for (const code of ["42501", "ECONNREFUSED", "08000", "57P01", "DEPENDENCY_UNAVAILABLE"]) {
    const payload = await readWorkInventoryCensus({
      client: makeClient({ throwOn: "ops.work_request", throwCode: code }),
      actor: JOE, correlationId: CORRELATION, now: NOW,
    });
    const entry = payload.coverage.find((item) => item.kind === "work_request");
    assert.equal(entry.reason, "DEPENDENCY_UNAVAILABLE", `${code} must be a dependency`);
  }
  // An unrecognized failure stays OUR defect and is not laundered into a dependency.
  const internal = await readWorkInventoryCensus({
    client: makeClient({ throwOn: "ops.work_request", throwCode: "22P02" }),
    actor: JOE, correlationId: CORRELATION, now: NOW,
  });
  assert.equal(internal.coverage.find((item) => item.kind === "work_request").reason, "INTERNAL_ERROR");
});
