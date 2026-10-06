// export-email-domains.test.mjs — the verb that lets a second machine hold no
// database credential (decision 2026-08-19).
//
// WHAT THIS VERB IS FOR. ops/fetch-allowlist.py builds the egress guard's
// allowlist from two export views, and reading them directly needed
// CARR_DB_EXPORTER_URL. On Dell's Mac that was the ONLY thing requiring a
// direct connection, so the whole "should he get database credentials" question
// reduced to this one query. The verb answers it by handing back DOMAINS,
// aggregated in SQL — strictly less than the connection could reach.
//
// WHAT THE TESTS PIN, and why each one is a real failure and not a formality:
//   1. An address can never come back. The aggregation is the security
//      property; if a change ever selects the raw column, this fails.
//   2. One unreadable view does not silently shrink the answer. A quietly
//      short allowlist is indistinguishable from a correct one, and the guard
//      would start refusing real client domains with nothing to read.
//   3. Both views unreadable raises rather than returning empty. An empty list
//      written to disk would strip every client domain the guard trusts.
//   4. Per-source counts survive, because the caller reports "N seen, M kept"
//      per view and the union alone cannot produce that line.
//   5. The verb is read-only — no write flag — since a write here would make
//      the credential argument it exists to settle moot.
//
// Run with: node --test mcp-server/test/export-email-domains.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const verb = TOOLS["export-email-domains"];

// A stub connection: answers each view from a table, or throws for a view
// named in `broken`, which is how a permission failure actually presents.
const conn = (rowsByView, broken = []) => ({
  query: async (sql) => {
    const view = ["v_export_clients", "v_export_leads"].find(v => sql.includes(v));
    if (broken.includes(view)) {
      const e = new Error("permission denied for view " + view);
      e.name = "InsufficientPrivilege";
      throw e;
    }
    return { rows: (rowsByView[view] || []).map(domain => ({ domain })) };
  },
});

test("the verb exists and is READ-ONLY — a write here would defeat its purpose", () => {
  assert.ok(verb, "export-email-domains is not registered");
  assert.ok(!verb.write, "must not be a write verb");
});

test("returns domains only — an email address cannot leave through this verb", async () => {
  const out = await verb.handler(conn({
    v_export_clients: ["example-pelvichealth.com", "example-dental.com"],
    v_export_leads: ["example-familymed.com"],
  }));
  assert.equal(out.ok, true);
  for (const d of out.domains)
    assert.ok(!d.includes("@"), `"${d}" looks like an address, not a domain`);
  assert.deepEqual(out.domains,
    ["example-dental.com", "example-familymed.com", "example-pelvichealth.com"]);
});

test("the union is deduplicated across both books", async () => {
  const out = await verb.handler(conn({
    v_export_clients: ["shared-practice.com", "only-client.com"],
    v_export_leads: ["shared-practice.com", "only-lead.com"],
  }));
  assert.deepEqual(out.domains, ["only-client.com", "only-lead.com", "shared-practice.com"]);
  assert.equal(out.counts.v_export_clients, 2);
  assert.equal(out.counts.v_export_leads, 2);
});

test("per-source domains survive, so the caller can still report N seen per view", async () => {
  const out = await verb.handler(conn({
    v_export_clients: ["a-clinic.com"],
    v_export_leads: ["b-clinic.com", "c-clinic.com"],
  }));
  assert.deepEqual(out.by_source.v_export_clients, ["a-clinic.com"]);
  assert.deepEqual(out.by_source.v_export_leads, ["b-clinic.com", "c-clinic.com"]);
});

test("ONE unreadable view is REPORTED, never folded silently into a shorter answer", async () => {
  const out = await verb.handler(conn({
    v_export_clients: ["still-readable.com"],
    v_export_leads: ["never-seen.com"],
  }, ["v_export_leads"]));
  assert.equal(out.ok, true);
  assert.deepEqual(out.domains, ["still-readable.com"]);
  assert.equal(out.counts.v_export_leads, undefined, "a skipped view must not report a count");
  assert.equal(out.notes.length, 1);
  assert.match(out.notes[0], /v_export_leads: skipped/);
});

test("BOTH views unreadable raises — an empty allowlist is not a valid answer", async () => {
  await assert.rejects(
    () => verb.handler(conn({}, ["v_export_clients", "v_export_leads"])),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload?.error ?? JSON.parse(err.message).error, "no_export_view_readable");
      return true;
    });
});

test("blank and whitespace domains are dropped rather than counted", async () => {
  const out = await verb.handler(conn({
    v_export_clients: ["", "  ", "real-practice.com", ".trailing-dot.com."],
    v_export_leads: [],
  }));
  assert.deepEqual(out.domains, ["real-practice.com", "trailing-dot.com"]);
  assert.equal(out.counts.v_export_clients, 2);
});
