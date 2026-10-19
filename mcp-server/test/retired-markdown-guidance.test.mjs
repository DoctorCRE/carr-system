import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { executeRegisteredTool } from "../src/tools.js";

test("weekly contact enrichment loads its research rule from the record API", () => {
  const prompt = readFileSync(new URL("../../ops/scheduled-tasks/contact-enrichment-weekly.SKILL.md", import.meta.url), "utf8");
  assert.doesNotMatch(prompt, /compiled-rules-shared\.md/);
  assert.match(prompt, /standing-context/);
  assert.match(prompt, /57d83b75/);
});

test("introduction paths direct callers to current doctrine and compatibility rules", async () => {
  const client = { query: async (sql) => {
    if (sql.includes("with n as"))
      return { rows: [{ ref: "V-DEMO-002", name: "Demo target", merged: false }] };
    if (sql.includes("with recursive e as"))
      return { rows: [{ hops: 1, ask_ref: "V-DEMO-001", ask_name: "Demo referrer",
        chain: "Demo referrer -knows-> Demo target",
        ref_path: ["V-DEMO-001", "V-DEMO-002"], first_note: "Synthetic connection" }] };
    if (sql.includes("select count(*)::int as n")) return { rows: [{ n: 0 }] };
    if (sql.includes("select from_ref, from_name, kind")) return { rows: [] };
    throw new Error(`Unexpected query: ${sql}`);
  } };
  const result = await executeRegisteredTool(client, { id: "demo-actor", slug: "joe" },
    "who-do-we-know", { target: "V-DEMO-002" });
  assert.equal(result.path_count, 1);
  assert.doesNotMatch(result.note, /DNA\/Network\/|introduction-rules\.md/);
  assert.match(result.note, /read-doctrine/);
  assert.match(result.note, /engine#section-1-ripe-introductions/);
  assert.match(result.note, /standing-context/);
  assert.match(result.note, /detail: "scopes"/);
  assert.match(result.note, /intro_politics/);
  assert.match(result.note, /A path existing does not make it a clean ask/);
});
