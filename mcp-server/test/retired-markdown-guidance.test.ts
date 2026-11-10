// Test current guidance references after Markdown retirement.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new TypeError("Expected object");
  return Object.fromEntries(Object.entries(value));
}
const registry = record(await import(new URL("../src/tools.js", import.meta.url).href));
async function executeRegisteredTool(...args: unknown[]) {
  const invoke = registry.executeRegisteredTool;
  if (typeof invoke !== "function") throw new TypeError("Expected registered executor");
  return record(await invoke(...args));
}

test("weekly contact enrichment loads its research rule from the record API", () => {
  const prompt = readFileSync(
    new URL("../../ops/scheduled-tasks/contact-enrichment-weekly.SKILL.md", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(prompt, /compiled-rules-shared\.md/);
  assert.match(prompt, /standing-context/);
  assert.match(prompt, /57d83b75/);
});

test("introduction paths direct callers to current doctrine and compatibility rules", async () => {
  const client = {
    query: async (sql: string) => {
      if (sql.includes("with n as")) return { rows: [{ ref: "V-DEMO-002", name: "Demo target", merged: false }] };
      if (sql.includes("with recursive e as"))
        return {
          rows: [
            {
              hops: 1,
              ask_ref: "V-DEMO-001",
              ask_name: "Demo referrer",
              chain: "Demo referrer -knows-> Demo target",
              ref_path: ["V-DEMO-001", "V-DEMO-002"],
              first_note: "Synthetic connection",
            },
          ],
        };
      if (sql.includes("select count(*)::int as n")) return { rows: [{ n: 0 }] };
      if (sql.includes("select from_ref, from_name, kind")) return { rows: [] };
      throw new Error(`Unexpected query: ${sql}`);
    },
  };
  const result = await executeRegisteredTool(client, { id: "demo-actor", slug: "joe" }, "who-do-we-know", {
    target: "V-DEMO-002",
  });
  assert.equal(result.path_count, 1);
  assert.doesNotMatch(text(result.note), /DNA\/Network\/|introduction-rules\.md/);
  assert.match(text(result.note), /read-doctrine/);
  assert.match(text(result.note), /engine#section-1-ripe-introductions/);
  assert.match(text(result.note), /standing-context/);
  assert.match(text(result.note), /detail: "scopes"/);
  assert.match(text(result.note), /intro_politics/);
  assert.match(text(result.note), /A path existing does not make it a clean ask/);
});

function text(value: unknown): string {
  if (typeof value !== "string") throw new TypeError("Expected string");
  return value;
}
