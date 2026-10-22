// Stored room history retains its server-side routing.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { DEFAULT_ROOM } from "../src/partner-room.js";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const read = (p) => readFileSync(repo + p, "utf8");

test("the Worker keeps stored Observatory turns and legacy queue receipts in their own rooms", () => {
  const src = read("mcp-server/src/index.js");
  const adapter = (name) => {
    const at = src.indexOf(`${name}:`);
    assert.ok(at >= 0, `${name} adapter missing`);
    return src.slice(at, src.indexOf("\n  },", at));
  };
  assert.match(adapter("roomReadFn"), /room: OBSERVATORY_ROOM/);
  assert.match(adapter("roomWriteFn"), /room: OBSERVATORY_ROOM/);
  // Existing queue receipt history remains in partner-line.
  assert.match(adapter("queueReadFn"), /room: DEFAULT_ROOM/);
  assert.equal(DEFAULT_ROOM, "partner-line");
});

test("assign-profile's wire receipt lands where the panel reads", () => {
  const src = read("mcp-server/src/agent-profiles.js");
  assert.match(src, /room: OBSERVATORY_ROOM, sponsor, seat: "claude", kind: "receipt"/);
  assert.doesNotMatch(src, /"partner-line"/);
});
