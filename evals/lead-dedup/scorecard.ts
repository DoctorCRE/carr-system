// Score lead deduplication evaluation results.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { runInNewContext } from "node:vm";

const workflowUrl = new URL("../../workflows/lead-sweep.workflow.js", import.meta.url);
const fixtureUrl = new URL("./pairs.v1.json", import.meta.url);

type Outcome = "duplicate" | "distinct";
type Lead = Record<string, unknown>;
interface Fixture {
  work_request?: unknown;
  adjudication?: unknown;
  provenance?: unknown;
  pairs: { id: string; expected: Outcome; rationale?: unknown; leads: Lead[] }[];
}

function record(value: unknown): value is Lead {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fixtureInput(value: unknown): asserts value is Fixture {
  assert.ok(record(value) && Array.isArray(value.pairs), "Fixture must contain pairs");
  for (const pair of value.pairs) {
    assert.ok(record(pair) && typeof pair.id === "string", "Pair must contain an id");
    assert.ok(pair.expected === "duplicate" || pair.expected === "distinct", "Invalid pair outcome");
    assert.ok(Array.isArray(pair.leads) && pair.leads.every(record), "Pair must contain lead objects");
  }
}

export async function scorecard(fixture?: unknown) {
  const source = await readFile(workflowUrl, "utf8");
  fixture ??= JSON.parse(await readFile(fixtureUrl, "utf8"));
  fixtureInput(fixture);
  const counts = { pairs: 0, duplicate: 0, distinct: 0, true_merge: 0, true_split: 0, false_merge: 0, false_split: 0 };
  const cases = [];
  for (const pair of fixture.pairs) {
    const leads = pair.leads.map((lead, index) => ({
      ...lead,
      signal: `Synthetic observation ${index}`,
      confidence: index ? "HIGH" : "LOW",
      vertical: "dental",
      sourceUrl: `https://example.invalid/${pair.id}/${index}`,
    }));
    const result: unknown = await runInNewContext(
      `(async () => { ${source.replace(/^export const meta =/m, "const meta =")} })()`,
      {
        args: { today: "SYNTHETIC", lanes: ["one", "two"], regions: ["fixture"], cap: 2 },
        phase() {},
        log() {},
        parallel: (jobs: (() => Promise<unknown>)[]) => Promise.all(jobs.map((job) => job())),
        agent: async (_prompt: string, options: { phase: string; label: string }) => {
          if (options.phase === "Sweep") return { leads: [leads[Number(options.label.split(":")[1]) - 1]] };
          if (options.phase === "Verify") return { checks: [] };
          throw new Error(`Unexpected agent phase ${options.phase}`);
        },
      },
      { timeout: 1000 },
    );
    assert.ok(
      record(result) && Array.isArray(result.leads) && result.leads.every(record),
      `Invalid result for ${pair.id}`,
    );
    if (![1, 2].includes(result.leads.length)) throw new Error(`Invalid result for ${pair.id}`);
    const observed = result.leads.length === 1 ? "duplicate" : "distinct";
    const correct = observed === pair.expected;
    counts.pairs++;
    counts[pair.expected]++;
    const classification: "true_merge" | "true_split" | "false_merge" | "false_split" =
      observed === "duplicate" ? (correct ? "true_merge" : "false_merge") : correct ? "true_split" : "false_split";
    counts[classification]++;
    cases.push({
      id: pair.id,
      expected: pair.expected,
      observed,
      correct,
      rationale: pair.rationale,
      presented: result.leads,
    });
  }
  return {
    work_request: fixture.work_request,
    adjudication: fixture.adjudication,
    provenance: fixture.provenance,
    workflow: "workflows/lead-sweep.workflow.js",
    workflow_sha256: createHash("sha256").update(source).digest("hex"),
    counts,
    rates: {
      false_merge: counts.distinct ? counts.false_merge / counts.distinct : null,
      false_split: counts.duplicate ? counts.false_split / counts.duplicate : null,
    },
    cases,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(await scorecard(), null, 2));
}
