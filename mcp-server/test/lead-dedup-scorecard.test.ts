// Test lead deduplication against labeled identity matching cases.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { scorecard } from "../../evals/lead-dedup/scorecard.ts";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function presented(value: unknown): Record<string, unknown>[] {
  assert.ok(isRecord(value) && Array.isArray(value.leads) && value.leads.every(isRecord));
  return value.leads;
}

const root = fileURLToPath(new URL("../../", import.meta.url));
const run = () => execFileSync(process.execPath, ["evals/lead-dedup/scorecard.ts"], { cwd: root, encoding: "utf8" });
const decide = async (...leads: Record<string, unknown>[]) => {
  const report = await scorecard({ work_request: "WR-AI-008", pairs: [{ id: "probe", expected: "distinct", leads }] });
  const first = report.cases[0];
  assert.ok(first);
  return first.observed;
};

test("lead workflow matches every labelled pair in the scorecard", async () => {
  const report = await scorecard();
  assert.equal(run(), `${JSON.stringify(report, null, 2)}\n`);
  assert.equal(report.work_request, "WR-AI-008");
  assert.equal(report.adjudication, "pending-human-review");
  assert.deepEqual(report.counts, {
    pairs: 10,
    duplicate: 6,
    distinct: 4,
    true_merge: 6,
    true_split: 4,
    false_merge: 0,
    false_split: 0,
  });
  assert.deepEqual(report.rates, { false_merge: 0, false_split: 0 });
});

test("same name without a corroborating field stays distinct", async () => {
  assert.equal(await decide({ name: "Avery Example" }, { name: "Avery Example" }), "distinct");
});

test("the whole location is compared, not a prefix", async () => {
  assert.equal(
    await decide(
      { name: "Example Dental", location: "100 Example Medical Plaza Suite 100" },
      { name: "Example Dental", location: "100 Example Medical Plaza Suite 200" },
    ),
    "distinct",
  );
});

test("location whitespace and case do not split one identity", async () => {
  assert.equal(
    await decide(
      { name: "Example Dental", location: " Example  City" },
      { name: "Example Dental", location: "EXAMPLE CITY " },
    ),
    "duplicate",
  );
});

test("a shared practice corroborates when only one lead has a location", async () => {
  assert.equal(
    await decide(
      { name: "Avery Example", practice: "Example Dental", location: "Example City" },
      { name: "Avery Example", practice: "Example Dental" },
    ),
    "duplicate",
  );
});

test("a conflicting corroborating field keeps leads distinct", async () => {
  assert.equal(
    await decide(
      { name: "Avery Example", practice: "Example Dental", location: "Example City" },
      { name: "Avery Example", practice: "Example Dental", location: "Other Example City" },
    ),
    "distinct",
  );
  assert.equal(
    await decide(
      { name: "Avery Example", practice: "Example Dental", location: "Example City" },
      { name: "Avery Example", practice: "Other Dental", location: "Example City" },
    ),
    "distinct",
  );
});

test("scorecard preserves low-confidence presentation and merged source evidence", async () => {
  const report = await scorecard();
  const mergedCase = report.cases.find((row) => row.id === "corroborated-name");
  assert.ok(mergedCase);
  const merged = mergedCase.presented;
  assert.ok(merged[0]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].sourceUrl, "https://example.invalid/corroborated-name/0");
  assert.equal(
    merged[0].signal,
    "Synthetic observation 0 | ALSO: Synthetic observation 1 (https://example.invalid/corroborated-name/1)",
  );
  assert.equal(merged[0].confidence, "HIGH");
  const distinctCase = report.cases.find((row) => row.id === "different-name");
  assert.ok(distinctCase);
  const distinct = distinctCase.presented;
  assert.deepEqual(
    Array.from(distinct, (lead) => lead.confidence),
    ["LOW", "HIGH"],
  );
  assert.deepEqual(
    Array.from(distinct, (lead) => lead.tier),
    ["UNVERIFIED (present anyway, flag)", "UNVERIFIED (present anyway, flag)"],
  );
});

test("same-name leads at different locations keep their own verify result (WR-000513)", async () => {
  const source = readFileSync(new URL("workflows/lead-sweep.workflow.js", `file://${root}`), "utf8");
  const leads = ["Example City", "Other Example City"].map((location, index) => ({
    name: "Example Dental",
    location,
    vertical: "dental",
    confidence: "LOW",
    signal: `Synthetic observation ${index}`,
    sourceUrl: `https://example.invalid/same-name/${index}`,
  }));
  const result: unknown = await runInNewContext(
    `(async () => { ${source.replace(/^export const meta =/m, "const meta =")} })()`,
    {
      args: { today: "SYNTHETIC", lanes: ["one", "two"], regions: ["fixture"], cap: 2 },
      phase() {},
      log() {},
      parallel: (jobs: (() => Promise<unknown>)[]) => Promise.all(jobs.map((job) => job())),
      agent: async (_prompt: string, options: { phase: string; label: string }) =>
        options.phase === "Sweep"
          ? { leads: [leads[Number(options.label.split(":")[1]) - 1]] }
          : {
              checks: [
                { item: 1, name: "Example Dental", exists: true, note: "first", corroboration: "license" },
                { item: 2, name: "Example Dental", exists: false, note: "second" },
              ],
            },
    },
    { timeout: 1000 },
  );
  assert.deepEqual(
    JSON.parse(
      JSON.stringify(
        presented(result).map((lead) => [lead.location, isRecord(lead.check) ? lead.check.note : undefined, lead.tier]),
      ),
    ),
    [
      ["Example City", "first", "CORROBORATED"],
      ["Other Example City", "second", "UNVERIFIED (present anyway, flag)"],
    ],
  );
});

test("an ambiguous or mismatched verify claim is refused, never guessed (WR-000513)", async () => {
  const source = readFileSync(new URL("workflows/lead-sweep.workflow.js", `file://${root}`), "utf8");
  const leads = ["Example City", "Other Example City"].map((location, index) => ({
    name: index ? "Other Dental" : "Example Dental",
    location,
    vertical: "dental",
    confidence: "LOW",
    signal: `Synthetic observation ${index}`,
    sourceUrl: `https://example.invalid/refuse/${index}`,
  }));
  const result: unknown = await runInNewContext(
    `(async () => { ${source.replace(/^export const meta =/m, "const meta =")} })()`,
    {
      args: { today: "SYNTHETIC", lanes: ["one", "two"], regions: ["fixture"], cap: 2 },
      phase() {},
      log() {},
      parallel: (jobs: (() => Promise<unknown>)[]) => Promise.all(jobs.map((job) => job())),
      agent: async (_prompt: string, options: { phase: string; label: string }) =>
        options.phase === "Sweep"
          ? { leads: [leads[Number(options.label.split(":")[1]) - 1]] }
          : {
              checks: [
                { item: 1, name: "Example Dental", exists: true, note: "first", corroboration: "license" },
                { item: 1, name: "Example Dental", exists: false, note: "conflicting repeat" },
                { item: 2, name: "Example Dental", exists: true, note: "echoed the wrong lead" },
              ],
            },
    },
    { timeout: 1000 },
  );
  assert.deepEqual(JSON.parse(JSON.stringify(presented(result).map((lead) => [lead.name, lead.check, lead.tier]))), [
    ["Example Dental", null, "UNVERIFIED (present anyway, flag)"],
    ["Other Dental", null, "UNVERIFIED (present anyway, flag)"],
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(presented(result).map((lead) => lead.checkRefused))), [
    "verifier returned claim 1 more than once",
    'verifier echoed "Example Dental" for claim 2',
  ]);
});

test("scorecard refuses malformed fixture boundaries", async () => {
  for (const fixture of [
    {},
    { pairs: [null] },
    { pairs: [{ id: "bad", expected: "maybe", leads: [] }] },
    { pairs: [{ id: "bad", expected: "distinct", leads: [null] }] },
  ]) {
    await assert.rejects(scorecard(fixture), assert.AssertionError);
  }
});

test("scorecard counts both classification errors and empty-fixture rates", async () => {
  const report = await scorecard({
    pairs: [
      {
        id: "false-merge",
        expected: "distinct",
        leads: [
          { name: "Example", location: "Here" },
          { name: "Example", location: "Here" },
        ],
      },
      { id: "false-split", expected: "duplicate", leads: [{ name: "One" }, { name: "Two" }] },
    ],
  });
  assert.deepEqual(report.counts, {
    pairs: 2,
    duplicate: 1,
    distinct: 1,
    true_merge: 0,
    true_split: 0,
    false_merge: 1,
    false_split: 1,
  });
  assert.deepEqual(report.rates, { false_merge: 1, false_split: 1 });
  assert.deepEqual((await scorecard({ pairs: [] })).rates, { false_merge: null, false_split: null });
});
