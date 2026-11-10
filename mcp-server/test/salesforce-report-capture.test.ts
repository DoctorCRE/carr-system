// Test browser capture of Salesforce report opportunities.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

test("browser report capture preserves opportunity IDs from visible row links for the importer", async () => {
  const cells = (values: string[]) => values.map((innerText) => ({ innerText }));
  const header = {
    children: cells(["", "Deal Name Column Actions", "Company Name Column Actions", ""]),
    querySelectorAll: () => [],
  };
  const row = {
    children: cells(["1", "Example expansion", "Example Practice"]),
    querySelectorAll: () => [{ getAttribute: () => "/lightning/r/Opportunity/006000000000001AAA/view" }],
  };
  const scroller = { scrollHeight: 200, clientHeight: 100, scrollWidth: 100, clientWidth: 100 };
  const doc = {
    querySelector: () => row,
    querySelectorAll: (selector: string) => (selector === "tr" ? [header, row] : [scroller]),
  };
  const window: { SF?: { tsv(): string; status(): string } } = {};
  const source = await readFile(new URL("../../pipelines/capture-salesforce-report.js", import.meta.url), "utf8");
  vm.runInNewContext(source, {
    window,
    document: { querySelectorAll: () => [{ contentDocument: doc }] },
    setTimeout: (callback: () => void) => callback(),
  });
  for (let turn = 0; turn < 10; turn++) await new Promise((resolve) => setImmediate(resolve));
  assert.ok(window.SF);
  const tsv = window.SF.tsv();
  assert.match(tsv, /Opportunity ID/);
  assert.match(tsv, /006000000000001AAA/);
  assert.equal(JSON.parse(window.SF.status()).rows, 1);
});
