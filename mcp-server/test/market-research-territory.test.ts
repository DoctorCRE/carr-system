import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

// Tests doctrine territory selection in market research prompts.

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("market research uses the current doctrine territory in its generated research prompts", async () => {
  const source = await readFile(path.join(ROOT, "workflows/market-research.workflow.js"), "utf8");
  const territory = "Synthetic market: Cedar Bay through North Inlet.";
  const prompts: string[] = [];
  let calls = 0;
  const agent = async (prompt: string) => {
    prompts.push(prompt);
    calls += 1;
    if (calls === 1)
      return {
        territory,
        angles: [{ name: "market conditions", searchBrief: "Find current vacancy and rent data." }],
      };
    if (prompt.startsWith("Write the ")) return "Synthetic report.";
    return { findings: [] };
  };
  const context = {
    args: { question: "What is changing in the local market?" },
    agent,
    parallel: async (jobs: (() => Promise<unknown>)[]) => Promise.all(jobs.map((job) => job())),
    phase() {},
    log() {},
  };

  const result: unknown = await vm.runInNewContext(
    `(async () => {\n${source.replace("export const meta", "const meta")}\n})()`,
    context,
  );

  assert.ok(result && typeof result === "object" && "territory" in result);
  assert.equal(result.territory, territory);
  assert.ok(
    prompts[0]?.includes("read-doctrine") &&
      prompts[0]?.includes("carr-profile") &&
      prompts[0]?.includes("our-territory"),
  );
  assert.ok(
    prompts.some((prompt) => prompt.includes(`Market territory from carr-profile#our-territory: ${territory}`)),
  );
});
