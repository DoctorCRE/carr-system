import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { findDisposableChromium } from "../../dealroom/test/chromium-binary.mjs";

const bridge = new URL("../../tools/room-bridge/test-browser-cdp.mjs", import.meta.url);

for (const disconnect of ["stdin", "SIGTERM"]) {
  test(`tool browser closes its DevTools endpoint after ${disconnect}`, { timeout: 90000 }, async t => {
    const executable = await findDisposableChromium();
    if (!executable) { t.skip("Disposable Chromium is unavailable"); return; }
    const child = spawn(process.execPath, [bridge.pathname, executable], { stdio: ["pipe", "pipe", "pipe"] });
    const exited = once(child, "exit");
    let stderr = "";
    child.stderr.on("data", chunk => { stderr += chunk; });
    t.after(() => child.stdin.end());
    const lines = createInterface({ input: child.stdout });
    const first = await Promise.race([
      once(lines, "line"),
      exited.then(() => { throw new Error(`Browser bridge exited before readiness: ${stderr}`); }),
    ]);
    const { pageWsUrl } = JSON.parse(first[0]);
    const endpoint = new URL("/json/list", pageWsUrl.replace("ws:", "http:"));
    assert.ok((await (await fetch(endpoint)).json()).some(page => page.type === "page"));
    if (disconnect === "stdin") child.stdin.end();
    else child.kill(disconnect);
    const [code, signal] = await exited;
    assert.equal(code, 0, stderr);
    assert.equal(signal, null);
    await assert.rejects(fetch(endpoint), /fetch failed/);
    lines.close();
  });
}
