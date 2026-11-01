import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { executeRegisteredTool } from "./src/tools.js";

const client = { query() { throw new Error("name validation must not access the database"); } };
const actor = { slug: "calendar-name-check", human: false, via: "agent-token" };
const results = [];
for (const name of JSON.parse(readFileSync(0, "utf8"))) {
  try {
    await executeRegisteredTool(client, actor, "add-party", {
      idempotency_key: randomUUID(), kind: "person", name: name || " ", validate_only: true,
    });
    results.push(true);
  } catch (error) {
    if (error.payload?.error !== "full_person_name_required") throw new Error("name validation failed");
    results.push(false);
  }
}
process.stdout.write(JSON.stringify(results));
