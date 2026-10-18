import { test } from "node:test";
import assert from "node:assert/strict";
import { ruleBootPage } from "../src/rule-boot.js";

test("boot is one bounded digest page even for a large corpus", async () => {
  const rows = Array.from({ length: 1000 }, (_, i) => ({
    id: `${i.toString(16).padStart(8, "0")}-0000-4000-8000-000000000000`,
    statement: "fixture-rule-body ".repeat(1000), personal_to: null,
  }));
  const boot = await ruleBootPage(rows, "joe");
  assert.equal(boot.schema, "carr-rule-boot/v1");
  assert.equal(boot.page, 1);
  assert.equal(boot.pages_total, 1);
  assert.equal(boot.total_chars, boot.text.length);
  assert.ok(boot.text.length > 0 && JSON.stringify(boot).length < 2000);
  assert.match(boot.digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(boot.text, /standing-context/);
  assert.doesNotMatch(boot.text, /fixture-rule-body/);
});

test("a string page of \"1\" is accepted and other pages are rejected", async () => {
  const rows = [{ id: "00000000-0000-4000-8000-000000000000", statement: "x", personal_to: null }];
  const ok = await ruleBootPage(rows, "joe", "1");
  assert.equal(ok.page, 1);
  assert.equal(ok.error, undefined);
  assert.equal((await ruleBootPage(rows, "joe", 2)).error, "page_out_of_range");
  assert.equal((await ruleBootPage(rows, "joe", "2")).error, "page_out_of_range");
});
