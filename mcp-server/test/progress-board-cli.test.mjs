import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("the published board and durable answer poller pass their CLI contract", () => {
  const run = spawnSync("python3", ["-m", "unittest", "tools/test-progress-board.py"], {
    cwd: new URL("../../", import.meta.url), encoding: "utf8",
  });
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stderr, /Ran \d+ tests/);
  assert.match(run.stderr, /^OK$/m);
});
