// new-deal-error-mapping.test.ts — the friendly constraint errors must survive
// the transaction they are raised in.
//
// THE LATENT BUG (found 2026-08-14 while fixing the add-party self-collision,
// defect 18b12fda). new-deal's catch block maps a duplicate-Salesforce-id
// insert (SQLSTATE 23505) to a friendly salesforce_id_in_use answer, and an
// unknown deal_type/phase/lane (23503) to a list of the valid slugs — but it
// gathered that friendliness by QUERYING THE SAME TRANSACTION THE INSERT JUST
// KILLED. After any SQL error Postgres aborts the transaction (25P02) until a
// rollback, so the diagnostic query itself blew up and the caller got a
// different opaque error instead of the answer the mapper meant to give. The
// mapping code was effectively dead the whole time.
//
// THE FIX UNDER TEST: the insert runs under a savepoint (same pattern as
// insertOrgPartyGuarded, same file), and the mapper rolls back to it before
// asking any follow-up question. The fakes here ENFORCE the abort discipline:
// once the insert throws, every query except "rollback to savepoint" fails —
// so a regression to querying the poisoned transaction fails these tests.
//
// Run with: node --test mcp-server/test/new-deal-error-mapping.test.ts
// Also picked up by npm test's test-suite discovery.

import assert from "node:assert/strict";
import { test } from "node:test";
import { ToolError } from "../src/tool-error.ts";
import { handler } from "./helpers/legacy-test-registry.ts";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  client: "60000000-0000-0000-0000-000000000001",
  deal: "60000000-0000-0000-0000-000000000002",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true, via: "mcp", client_id: "claude" };

const PHASES = ["prospect", "loi", "lease_signed", "closed"];

type InsertError = { code: string; constraint: string | null };
class Fake {
  insertError: InsertError | null;
  aborted: boolean;
  rollbacks: number;
  events: unknown[][];
  constructor({ insertError = null }: { insertError?: InsertError | null } = {}) {
    this.insertError = insertError; // {code, constraint} the deal insert should raise
    this.aborted = false; // 25P02 discipline
    this.rollbacks = 0;
    this.events = [];
  }

  async query(text: string, params: unknown[] = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (this.aborted && !/^rollback to savepoint/i.test(sql)) {
      const e = Object.assign(
        new Error("current transaction is aborted, commands ignored until end of transaction block"),
        { code: "25P02" },
      );
      throw e;
    }

    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.includes("from v_ref_index where subject_type='client' and ref ilike"))
      return { rows: [{ subject_id: ids.client }] };
    if (sql.includes("subject_type='deal' and lower(display_name)")) return { rows: [] };

    if (/^savepoint\s/i.test(sql)) return { rows: [] };
    if (/^rollback to savepoint\s/i.test(sql)) {
      this.rollbacks++;
      this.aborted = false;
      return { rows: [] };
    }

    if (sql.startsWith("insert into deal")) {
      if (this.insertError) {
        this.aborted = true;
        const e = Object.assign(new Error("constraint violation"), this.insertError);
        throw e;
      }
      return { rows: [{ id: ids.deal }] };
    }

    if (sql.startsWith("select name from deal where salesforce_id="))
      return { rows: [{ name: "Dr. Example — 4301 Spanish Trail lease" }] };
    if (sql.startsWith("select slug from deal_phase")) return { rows: PHASES.map((slug) => ({ slug })) };
    if (sql.startsWith("select slug from deal_type_ref") || sql.startsWith("select slug from deal_lane"))
      return { rows: [] };

    if (sql.startsWith("insert into event")) {
      this.events.push(params);
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) return { rows: [] };

    throw new Error(`fake received unexpected SQL: ${sql}`);
  }
}

const newDeal = (c: Fake, extra: Record<string, unknown> = {}) =>
  handler("new-deal", c, joe, {
    idempotency_key: "44444444-4444-4444-4444-444444444444",
    client: "C-001",
    name: "Test Deal",
    deal_type: "lease",
    phase: "prospect",
    ...extra,
  });

test("duplicate salesforce_id surfaces salesforce_id_in_use WITH the holder's name", async () => {
  const c = new Fake({ insertError: { code: "23505", constraint: "deal_salesforce_id_uniq" } });
  await assert.rejects(
    newDeal(c, { salesforce_id: "006XX0000012345" }),
    (e) =>
      e instanceof ToolError &&
      e.payload.error === "salesforce_id_in_use" &&
      e.payload.held_by === "Dr. Example — 4301 Spanish Trail lease",
  );
  assert.equal(c.rollbacks, 1, "the mapper rolled back to the savepoint before its lookup");
});

test("unknown phase surfaces unknown_phase with the valid slugs", async () => {
  const c = new Fake({ insertError: { code: "23503", constraint: "deal_phase_fkey" } });
  await assert.rejects(
    newDeal(c, { phase: "negotiating" }),
    (e) =>
      e instanceof ToolError &&
      e.payload.error === "unknown_phase" &&
      e.payload.given === "negotiating" &&
      assert.deepEqual(e.payload.valid, PHASES) === undefined,
  );
  assert.equal(c.rollbacks, 1);
});

test("a clean insert takes the happy path untouched", async () => {
  const c = new Fake();
  const res = await newDeal(c);
  assert.equal(res.ok, true);
  assert.equal(res.deal_id, ids.deal);
  assert.equal(c.rollbacks, 0);
});

test("an error outside the mapped classes still propagates raw", async () => {
  const c = new Fake({ insertError: { code: "22P02", constraint: null } });
  await assert.rejects(newDeal(c), (e) => e instanceof Error && "code" in e && e.code === "22P02");
});
