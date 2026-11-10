import assert from "node:assert/strict";
import test from "node:test";
import { readClientAnniversaries } from "../src/client-anniversary-read.ts";
import type { Db } from "../src/verb-contract.ts";

const row = {
  client_id: "c1",
  client_name: "Synthetic client",
  deal_id: "d1",
  deal_name: "Synthetic deal",
  closed_on: "2020-02-29",
  phase: "closed",
  outcome: "won",
  deal_version: 3,
  client_version: 2,
  party_version: 1,
};
type Options = Partial<Parameters<typeof readClientAnniversaries>[0]>;
// These SELECT-only fixtures deliberately implement only the query result consumed by the parser.
const database = (client: object): Db => client as Db;
const read = (rows: unknown[], extra: Omit<Options, "client"> & { client?: object } = {}) =>
  readClientAnniversaries({
    actor: { id: "synthetic-actor", slug: "joe" },
    now: () => new Date("2027-02-28T05:00:00Z"),
    ...extra,
    client: database(extra.client ?? { query: async () => ({ rows }) }),
  });
function firstRow(r: Awaited<ReturnType<typeof read>>) {
  const row = r.rows[0];
  assert.ok(row);
  return row;
}
test("Chicago date and February 29 policy preserve canonical evidence", async () => {
  const r = await read([row]);
  assert.equal(r.as_of, "2027-02-27");
  assert.equal(r.state, "ready");
  assert.equal(firstRow(r).anniversary_on, "2027-02-28");
  assert.equal(firstRow(r).days_until, 1);
  assert.equal(firstRow(r).years, 7);
  assert.equal(firstRow(r).closed_on, "2020-02-29");
  assert.equal(firstRow(r).evidence_ref, "deal:d1@3#closed_on");
});
test("missing and impossible closing dates are skipped without substituting other dates", async () => {
  const r = await read([
    { ...row, closed_on: null },
    { ...row, deal_id: "bad", closed_on: "2026-02-30", next_date: "2020-02-28" },
  ]);
  assert.equal(r.state, "empty");
  assert.equal(r.coverage.skipped_invalid, 2);
  assert.equal(r.coverage.complete, true);
});
test("future first-year and lost or open deals never qualify", async () => {
  const r = await read([
    { ...row, closed_on: "2027-03-01" },
    { ...row, outcome: "lost" },
    { ...row, phase: "negotiating" },
  ]);
  assert.equal(r.state, "empty");
});
test("next anniversary advances after the day and preserves leap years", async () => {
  let r = await read([row], { now: () => new Date("2028-02-29T18:00:00Z") });
  assert.equal(firstRow(r).days_until, 0);
  assert.equal(firstRow(r).anniversary_on, "2028-02-29");
  r = await read([row], { now: () => new Date("2027-03-01T18:00:00Z") });
  assert.equal(r.state, "empty");
});
test("deduplication and deterministic offset pages use next_offset", async () => {
  const rows = [{ ...row, deal_id: "d2" }, row, row];
  const first = await read(rows, { args: { limit: 1 } });
  assert.equal(firstRow(first).deal_id, "d1");
  assert.equal(first.next_offset, 1);
  const second = await read(rows, { args: { limit: 1, offset: first.next_offset } });
  assert.equal(firstRow(second).deal_id, "d2");
  assert.equal(second.next_offset, null);
});
test("invalid input and unauthorized actor refuse before source access", async () => {
  const client = {
    query: () => {
      throw new Error("must not query");
    },
  };
  for (const args of [
    { horizon_days: 0 },
    { horizon_days: 91 },
    { limit: 51 },
    { offset: -1 },
    { actor: "joe" },
    { client: "unknown" },
    { limit: "1" },
    null,
  ])
    assert.equal((await read([], { args, client })).state, "needs_input");
  await assert.rejects(read([], { actor: { id: "synthetic-actor", slug: "guest" }, client }), /unauthorized/);
  assert.equal((await read([], { actor: { id: "synthetic-actor", slug: "dell" } })).state, "empty");
});
test("failed, malformed, and oversized sources are unavailable rather than empty", async () => {
  for (const client of [
    {
      query: async () => {
        throw new Error("secret");
      },
    },
    { query: async () => ({ rows: [{}] }) },
    { query: async () => ({ rows: Array(1001).fill(row) }) },
  ]) {
    const r = await read([], { client });
    assert.equal(r.state, "unavailable");
    assert.equal(r.coverage.complete, false);
    assert.deepEqual(r.rows, []);
  }
});
