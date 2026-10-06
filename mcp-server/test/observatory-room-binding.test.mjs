// The Observatory composer and turn feed must use the room the local bridge
// polls, or a browser post (the only origin trusted for Flash code tasks) never
// reaches the queue. 2026-09-27: both of Joe's @queue posts landed in
// partner-line while the bridge watched model-room, and nothing ran.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { OBSERVATORY_ROOM, DEFAULT_ROOM } from "../src/partner-room.js";

const repo = fileURLToPath(new URL("../../", import.meta.url));
const read = (p) => readFileSync(repo + p, "utf8");

test("the Observatory room is the room the installed bridge polls", () => {
  const plist = read("ops/launchd/com.carr.room-bridge.plist");
  const polled = plist.match(/<key>CARR_ROOM_BRIDGE_ROOM<\/key><string>([^<]+)<\/string>/);
  assert.ok(polled, "bridge plist must name its room explicitly");
  assert.equal(OBSERVATORY_ROOM, polled[1]);
});

test("the Worker reads and posts Observatory turns in that room; the queue projection stays put", () => {
  const src = read("mcp-server/src/index.js");
  const adapter = (name) => {
    const at = src.indexOf(`${name}:`);
    assert.ok(at >= 0, `${name} adapter missing`);
    return src.slice(at, src.indexOf("\n  },", at));
  };
  assert.match(adapter("roomReadFn"), /room: OBSERVATORY_ROOM/);
  assert.match(adapter("roomWriteFn"), /room: OBSERVATORY_ROOM/);
  // Hermes projects the queue into partner-line with fixed provenance
  // (tools/room-bridge/verb_io.project_room_queue); the panel reads it there.
  assert.match(adapter("queueReadFn"), /room: DEFAULT_ROOM/);
  assert.equal(DEFAULT_ROOM, "partner-line");
});

test("assign-profile's wire receipt lands where the panel reads", () => {
  const src = read("mcp-server/src/agent-profiles.js");
  assert.match(src, /room: OBSERVATORY_ROOM, sponsor, seat: "claude", kind: "receipt"/);
  assert.doesNotMatch(src, /"partner-line"/);
});
