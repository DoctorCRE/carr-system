import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The old runner's first cold launch exhausted a 20s poll budget after
// 23.094s wall time (CI run 36612182965). Allow 30s per launch, and measure
// each actual startup; isolation from the bulk Node pool removes contention.
export const CHROME_STARTUP_TIMEOUT_MS = 30_000;
export const CHROME_STOP_TIMEOUT_MS = 3000;

async function settlesWithin(promise, ms) {
  let timer;
  try {
    return await Promise.race([promise.then(() => true), new Promise((resolve) => {
      timer = setTimeout(() => resolve(false), ms);
    })]);
  } finally { clearTimeout(timer); }
}

function signalChild(child, signal) {
  // Reap Chrome's helpers too, before a retry can compete with the old tree.
  if (!child.pid) return;
  try {
    if (process.platform === "win32") child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch (error) { if (error.code !== "ESRCH") throw error; }
}

async function groupStopsWithin(child, ms) {
  const deadline = performance.now() + ms;
  for (;;) {
    try { process.kill(-child.pid, 0); }
    catch (error) {
      if (error.code === "ESRCH") return true;
      // macOS can report EPERM while an owned group is exiting. Keep waiting;
      // only ESRCH proves it is gone, and persistent errors still time out.
      if (error.code !== "EPERM") throw error;
    }
    const remaining = deadline - performance.now();
    if (remaining <= 0) return false;
    await wait(Math.min(50, remaining));
  }
}

async function removeProfile(profile) {
  // A helper may still be flushing a file as the browser process closes.
  for (let attempt = 0; ; attempt += 1) {
    try { await rm(profile, { recursive: true, force: true }); return; }
    catch (error) {
      if (error.code !== "ENOTEMPTY" || attempt === 2) throw error;
      await wait(200);
    }
  }
}

async function readyPage(profile, remainingMs) {
  const [portText, browserPath] = (await readFile(path.join(profile, "DevToolsActivePort"), "utf8")).split(/\r?\n/);
  const port = Number(portText);
  if (!/^\d+$/.test(portText) || port < 1 || port > 65535 || !/^\/devtools\/browser\/[^\s]+$/.test(browserPath ?? "")) {
    throw new Error("DevToolsActivePort is incomplete or invalid");
  }
  // File publication can precede HTTP/page readiness; both share the deadline.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.min(500, remainingMs));
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/list`, { signal: controller.signal });
    if (!response.ok) throw new Error(`DevTools page probe returned HTTP ${response.status}`);
    const targets = await response.json();
    const target = targets.find((item) => item.type === "page");
    if (!target?.webSocketDebuggerUrl) throw new Error("Chrome page target has not been published");
    const url = new URL(target.webSocketDebuggerUrl);
    if (url.protocol !== "ws:" || !["localhost", "127.0.0.1"].includes(url.hostname) || Number(url.port) !== port || !url.pathname.startsWith("/devtools/page/")) {
      throw new Error("Chrome did not expose a local page target");
    }
    return url.href;
  } finally { clearTimeout(timer); }
}

export async function launchChrome(chrome, { spawnChrome = spawn, timeoutMs = CHROME_STARTUP_TIMEOUT_MS, pollIntervalMs = 50 } = {}) {
  const totalStarted = performance.now();
  const attempts = [];
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    const profile = await mkdtemp(path.join(tmpdir(), "v5-j101-chrome-"));
    const started = performance.now();
    let child, stderr = "", spawnError, exited = false, exitPromise, closePromise, cleanup;
    const close = () => cleanup ??= (async () => {
      if (child) {
        signalChild(child, "SIGTERM");
        // A leader's exit says nothing about helpers that share its group.
        const stopped = process.platform !== "win32" && child.pid
          ? () => groupStopsWithin(child, CHROME_STOP_TIMEOUT_MS)
          : () => settlesWithin(exitPromise, CHROME_STOP_TIMEOUT_MS);
        if (!await stopped()) {
          signalChild(child, "SIGKILL");
          if (!await stopped()) throw new Error("Chrome process tree did not exit after SIGKILL");
        }
        if (!await settlesWithin(exitPromise, CHROME_STOP_TIMEOUT_MS)) throw new Error("Chrome did not exit after cleanup");
        // A detached crash reporter can inherit stderr after Chrome exits.
        // Drain briefly for diagnostics, then release our stream reference.
        await settlesWithin(closePromise, 100);
        child.stderr.destroy();
      }
      await removeProfile(profile);
    })();
    let lastProbe = "DevToolsActivePort has not been published";
    try {
      child = spawnChrome(chrome, [
        "--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--no-sandbox",
        "--disable-background-networking", "--disable-default-apps", "--disable-dev-shm-usage",
        "--allow-file-access-from-files", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
      ], { stdio: ["ignore", "ignore", "pipe"], detached: process.platform !== "win32" });
      child.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-4000); });
      exitPromise = new Promise((resolve) => {
        child.once("exit", () => { exited = true; resolve(); });
        child.once("error", (error) => { spawnError = error; exited = true; resolve(); });
      });
      closePromise = new Promise((resolve) => child.once("close", resolve));
      const deadline = started + timeoutMs;
      while (performance.now() < deadline) {
        if (spawnError) throw spawnError;
        if (exited || child.exitCode !== null || child.signalCode !== null) throw new Error("Chrome exited before DevTools started");
        try {
          const pageWsUrl = await readyPage(profile, Math.max(1, deadline - performance.now()));
          const elapsedMs = Math.round(performance.now() - started);
          attempts.push({ attempt, elapsedMs });
          return { pageWsUrl, startupMs: Math.round(performance.now() - totalStarted), attempts, close };
        } catch (error) {
          if (error.name !== "AbortError" && error.code && !["ENOENT", "ECONNREFUSED"].includes(error.code)) throw error;
          lastProbe = error.message;
        }
        const remaining = deadline - performance.now();
        if (remaining > 0) await wait(Math.min(pollIntervalMs, remaining));
      }
      throw new Error(`Chrome startup deadline exceeded (${timeoutMs}ms); last probe: ${lastProbe}`);
    } catch (error) {
      const elapsedMs = Math.round(performance.now() - started);
      await close();
      const diagnostic = `attempt ${attempt}: ${error.message}; elapsed=${elapsedMs}ms; code ${child?.exitCode ?? "null"}, signal ${child?.signalCode ?? "null"}; stderr: ${stderr || "<empty>"}`;
      attempts.push({ attempt, elapsedMs, diagnostic });
    }
  }
  throw new Error(`Chrome failed to start after one retry:\n${attempts.map((item) => item.diagnostic).join("\n")}`);
}
