import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import vm from "node:vm";

function row(values: string[], links: string[] = []) {
  return {
    children: values.map((innerText) => ({ innerText })),
    querySelectorAll: () => links.map((href) => ({ getAttribute: () => href })),
  };
}

async function capture(rows: ReturnType<typeof row>[] | ((x: number) => ReturnType<typeof row>[]), hasFrame = true) {
  const scroller = { scrollHeight: 200, clientHeight: 100, scrollWidth: 200, clientWidth: 100, scrollLeft: 0 };
  const visibleRows = () => (typeof rows === "function" ? rows(scroller.scrollLeft) : rows);
  const doc = {
    querySelector: () => visibleRows()[0],
    querySelectorAll: (selector: string) => (selector === "tr" ? visibleRows() : [scroller]),
  };
  const context = vm.createContext({
    window: {},
    document: { querySelectorAll: () => (hasFrame ? [{ contentDocument: doc }] : []) },
    setTimeout: (callback: () => void) => callback(),
  });
  const source = await readFile(new URL("../../pipelines/capture-salesforce-report.ts", import.meta.url), "utf8");
  assert.equal(vm.runInContext(stripTypeScriptTypes(source), context), "started");
  for (let turn = 0; turn < 10; turn++) await new Promise<void>((resolve) => setImmediate(resolve));
  return (expression: string): unknown => vm.runInContext(`window.SF.${expression}`, context);
}

const header = () => row(["", "Deal Name Column Actions", "Company Name Column Actions", ""]);

test("capture preserves IDs, normalizes cells, chunks rows and exposes totals and missing rows", async () => {
  const read = await capture([
    header(),
    row(["2", "  Example\n expansion ", "Example Practice"], ["/lightning/r/Opportunity/006000000000001AAA/view"]),
    row(["1", "First deal", "First company"]),
    row(["3", "", "Report total"]),
    row(["4", "Misaligned", "extra", "cell"]),
  ]);
  assert.equal(read("tsv(0, 1)"), "row_index\tDeal Name\tCompany Name\tOpportunity ID\n1\tFirst deal\tFirst company\t");
  assert.equal(read("tsv(1, 1)"), "2\tExample expansion\tExample Practice\t006000000000001AAA");
  assert.equal(read("tsv(2, 1)"), "");
  assert.equal(read("missing(4)"), "[3,4]");
  assert.equal(read("totals()"), '{"dataRows":2,"totalsRow":[{"Company Name":"Report total"}]}');
  assert.equal(
    read("status()"),
    '{"done":true,"rows":3,"columns":3,"passes":"4/4","skippedMisaligned":4,"error":null}',
  );
  assert.equal(read("go()"), "started");
});

test("capture does not infer an opportunity ID from ambiguous or unrelated links", async () => {
  const read = await capture([
    header(),
    row(["1", "Ambiguous", "Company"], ["/006000000000001AAA", "/006000000000002AAA"]),
    row(["2", "Unrelated", "Company"], ["/001000000000001AAA"]),
  ]);
  assert.equal(read("tsv()"), "row_index\tDeal Name\tCompany Name\n1\tAmbiguous\tCompany\n2\tUnrelated\tCompany");
});

test("missing report iframe exposes the existing error without claiming completion", async () => {
  const read = await capture([], false);
  assert.equal(
    read("status()"),
    '{"done":false,"rows":0,"columns":0,"passes":"0/0","skippedMisaligned":0,"error":"report iframe not found — is the report open and finished loading?"}',
  );
  assert.equal(read("tsv()"), "");
});

test("horizontal passes merge by header and retain values when later cells are blank", async () => {
  const read = await capture((x) =>
    x === 0
      ? [header(), row(["1", "First deal", "First company"], ["/006000000000001", "/006000000000001"])]
      : [row(["", "Company Name Column Actions", "Stage Column Actions", ""]), row(["1", "", "Active"])],
  );
  assert.equal(
    read("tsv()"),
    "row_index\tDeal Name\tCompany Name\tOpportunity ID\tStage\n1\tFirst deal\tFirst company\t006000000000001\tActive",
  );
});
