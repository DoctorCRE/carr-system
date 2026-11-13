import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

// The registry remains a legacy JavaScript boundary until its own conversion.
const registry: unknown = await import(new URL("./src/tools.js", import.meta.url).href);
if (typeof registry !== "object" || registry === null || !("executeRegisteredTool" in registry))
  throw new Error("name validation failed");
const executeRegisteredTool = registry.executeRegisteredTool;
if (typeof executeRegisteredTool !== "function") throw new Error("name validation failed");

const client = {
  query() {
    throw new Error("name validation must not access the database");
  },
};
const actor = { slug: "calendar-name-check", human: false, via: "agent-token" };
const input: unknown = JSON.parse(readFileSync(0, "utf8"));
if (!Array.isArray(input) && typeof input !== "string") throw new Error("name validation failed");
const names: Iterable<unknown> = input;
const results: boolean[] = [];
for (const name of names) {
  try {
    await executeRegisteredTool(client, actor, "add-party", {
      idempotency_key: randomUUID(),
      kind: "person",
      name: name || " ",
      validate_only: true,
    });
    results.push(true);
  } catch (error) {
    if (
      typeof error !== "object" ||
      error === null ||
      !("payload" in error) ||
      typeof error.payload !== "object" ||
      error.payload === null ||
      !("error" in error.payload) ||
      error.payload.error !== "full_person_name_required"
    )
      throw new Error("name validation failed");
    results.push(false);
  }
}
process.stdout.write(JSON.stringify(results));
