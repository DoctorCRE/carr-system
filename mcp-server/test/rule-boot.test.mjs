import { test } from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool } from "../src/tools.js";
import { ruleBootPage } from "../src/rule-boot.js";

const JOE = { id: "11111111-1111-4111-8111-111111111111", slug: "joe", human: true, via: "oauth-google" };
const rows = [
  { id: "11111111-0000-4000-8000-000000000000", statement: "Shared rule.", personal_to: null },
  { id: "22222222-0000-4000-8000-000000000000", statement: "Joe personal rule.", personal_to: "joe" },
  { id: "33333333-0000-4000-8000-000000000000", statement: "Dell personal rule.", personal_to: "dell" },
];

test("digest is deterministic and changes for every visible rule amendment", async () => {
  const boot = await ruleBootPage(rows, "joe");
  assert.deepEqual(await ruleBootPage([...rows].reverse(), "joe"), boot);
  for (const index of [0, 1]) {
    const amended = rows.map((r, i) => i === index ? { ...r, statement: r.statement + " Amendment." } : r);
    assert.notEqual((await ruleBootPage(amended, "joe")).digest, boot.digest);
  }
  const ownerChanged = rows.map((r, i) => i === 1 ? { ...r, personal_to: null } : r);
  assert.notEqual((await ruleBootPage(ownerChanged, "joe")).digest, boot.digest);
  const added = [...rows, { ...rows[0], id: "44444444-0000-4000-8000-000000000000" }];
  assert.notEqual((await ruleBootPage(added, "joe")).digest, boot.digest);
  assert.notEqual((await ruleBootPage(rows.slice(1), "joe")).digest, boot.digest);
});

test("other sponsors' personal rows affect neither digest nor counts", async () => {
  for (const sponsor of ["joe", "dell", null]) {
    const visible = rows.filter(r => !r.personal_to || r.personal_to === sponsor);
    const boot = await ruleBootPage(rows, sponsor);
    assert.deepEqual(boot, await ruleBootPage(visible, sponsor));
    assert.deepEqual(boot.counts, { rules: sponsor ? 2 : 1, shared: 1, personal: sponsor ? 1 : 0 });
    assert.doesNotMatch(boot.text, /personal rule/);
  }
  assert.notEqual((await ruleBootPage(rows, "joe")).digest, (await ruleBootPage(rows, "dell")).digest);
});

test("empty corpus still has a valid one-page boot", async () => {
  const boot = await ruleBootPage([], null);
  assert.equal(boot.pages_total, 1);
  assert.equal(boot.total_chars, boot.text.length);
  assert.deepEqual(boot.counts, { rules: 0, shared: 0, personal: 0 });
  assert.match(boot.next, /^complete/);
});

test("scope edits invalidate the digest while object key order does not", async () => {
  const boot = await ruleBootPage([{ ...rows[0], scope: { tools: ["Bash"], moment: "Before writing" } }], "joe");
  const reordered = await ruleBootPage([{ ...rows[0], scope: { moment: "Before writing", tools: ["Bash"] } }], "joe");
  assert.equal(boot.digest, reordered.digest);
  const amended = await ruleBootPage([{ ...rows[0], scope: { tools: ["Read"], moment: "Before reading" } }], "joe");
  assert.notEqual(boot.digest, amended.digest);
});

test("registered boot uses authenticated sponsor and retains the old client envelope", async () => {
  const client = { query: async (sql, params) => {
    if (/standing-context:rule-boot/.test(sql)) {
      assert.deepEqual(params, ["joe"]);
      assert.match(sql, /personal_to is null/);
      assert.match(sql, /select id, statement, scope, personal_to/);
      return { rows };
    }
    return { rows: [] };
  } };
  const out = await executeRegisteredTool(client, JOE, "standing-context", { detail: "boot", page: 1 });
  assert.equal(out.ok, true);
  assert.equal(out.rule_boot.schema, "carr-rule-boot/v1");
  assert.equal(out.rule_boot.pages_total, 1);
  assert.equal(out.identity.personal_brain_scope, "joe-personal");
  assert.deepEqual(out.rule_boot, await ruleBootPage(rows.slice(0, 2), "joe"));
  await assert.rejects(
    executeRegisteredTool(client, JOE, "standing-context", { detail: "boot", page: 2 }),
    e => e.payload?.error === "page_out_of_range");
});
