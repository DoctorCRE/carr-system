// Test browser cleanup and macOS test browser visibility.
import test from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { testBrowser } from "./browser.mjs";

const run = promisify(execFile);

test("macOS test Chromium has no Dock icon or menu bar", {
  skip: process.platform !== "darwin", timeout: 30000,
}, async t => {
  const browser = await testBrowser(t);
  const cdp = await browser.newBrowserCDPSession();
  const { processInfo } = await cdp.send("SystemInfo.getProcessInfo");
  const pid = processInfo.find(process => process.type === "browser").id;
  const { stdout } = await run("/usr/bin/osascript", ["-l", "JavaScript", "-e",
    `ObjC.import('AppKit'); const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(${pid}); JSON.stringify({policy: Number(app.activationPolicy), active: Boolean(app.active)});`]);
  const { policy, active } = JSON.parse(stdout);
  assert.ok([1, 2].includes(policy), `activation policy ${policy} must be accessory or prohibited`);
  assert.equal(active, false);
});

// Playwright permits 180s to launch and 30s to close. Include child startup and reporting.
const probeTimeout = 180000 + 30000 + 15000;

test("a failed test closes its browser without caller cleanup", { timeout: probeTimeout + 15000 }, async () => {
  const source = `
    import test from 'node:test';
    import { testBrowser } from ${JSON.stringify(new URL("./browser.mjs", import.meta.url).href)};
    test('intentional failure', async t => {
      const browser = await testBrowser(t);
      const cdp = await browser.newBrowserCDPSession();
      const { processInfo } = await cdp.send('SystemInfo.getProcessInfo');
      console.log('browser-pid=' + processInfo.find(p => p.type === 'browser').id);
      throw new Error('intentional teardown probe');
    });`;
  const { NODE_TEST_CONTEXT, ...childEnv } = process.env;
  let result;
  try { await run(process.execPath, ["--test-reporter=tap", "--input-type=module", "--eval", source], { timeout: probeTimeout, env: childEnv }); }
  catch (error) { result = error; }
  assert.equal(result?.code, 1, JSON.stringify({
    code: result?.code, signal: result?.signal, killed: result?.killed,
    stdout: result?.stdout, stderr: result?.stderr,
  }));
  assert.match(result.stdout, /not ok 1 - intentional failure/);
  assert.match(result.stdout, /intentional teardown probe/);
  const pid = Number(result.stdout.match(/browser-pid=(\d+)/)?.[1]);
  assert.ok(pid > 0, result.stdout);
  assert.throws(() => process.kill(pid, 0), { code: "ESRCH" });
});
