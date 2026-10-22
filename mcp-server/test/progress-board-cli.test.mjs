import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

test("the published board and durable answer poller pass their CLI contract", (t) => {
  const root = mkdtempSync(join(tmpdir(), "progress-board-cli-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const run = spawnSync("python3", ["-m", "unittest",
    "tools/test-progress-hierarchy.py", "tools/test-progress-board.py", "tools/test-progress-board-consumer-proof.py",
    "tools/test-progress-board-1380.py", "tools/test-progress-board-clean.py",
    "tools/test-progress-board-size.py", "tools/test-progress-board-github.py",
  ], {
    cwd: new URL("../../", import.meta.url), encoding: "utf8",
    env: { ...process.env, TMPDIR: root, TMP: root, TEMP: root,
      CARR_WORK_QUEUE_DB: join(root, "work-queue.sqlite3") },
  });
  assert.equal(run.status, 0, run.stderr);
  assert.match(run.stderr, /Ran \d+ tests/);
  assert.match(run.stderr, /^OK$/m);
});
