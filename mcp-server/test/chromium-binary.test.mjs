import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { readdir } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { launchChrome } from "../../dealroom/test/chrome-launch.mjs";
import { findDisposableChromium } from "../../dealroom/test/chromium-binary.mjs";

const SYSTEM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

test("browser tests refuse the installed auto-updating Chrome", async () => {
  const result = await findDisposableChromium({
    env: { CHROME_FOR_TESTING_PATH: SYSTEM_CHROME, CHROME_PATH: SYSTEM_CHROME },
    home: "/Users/tester",
    platform: "darwin",
    exists: () => true,
    realpath: candidate => candidate,
    listDirectories: async () => [],
  });
  assert.equal(result, null);
});

test("browser tests select Playwright Chrome for Testing", async () => {
  const home = "/Users/tester";
  const cache = path.join(home, "Library/Caches/ms-playwright");
  const executable = path.join(cache, "chromium-1243/chrome-mac-arm64",
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing");
  const result = await findDisposableChromium({
    env: {}, home, platform: "darwin",
    exists: candidate => candidate === executable,
    realpath: candidate => candidate,
    listDirectories: async candidate => candidate === cache ? ["chromium-1243"] : [],
  });
  assert.equal(result, executable);
});

test("browser tests refuse an alias that resolves to installed Chrome", async () => {
  const alias = "/tmp/chrome-for-testing";
  const result = await findDisposableChromium({
    env: { CHROME_FOR_TESTING_PATH: alias },
    home: "/Users/tester",
    platform: "darwin",
    exists: () => true,
    realpath: candidate => candidate === alias ? SYSTEM_CHROME : candidate,
    listDirectories: async () => [],
  });
  assert.equal(result, null);
});

test("browser tests use the GitHub runner Chrome only inside CI", async () => {
  const runnerChrome = "/usr/bin/google-chrome";
  const result = await findDisposableChromium({
    env: { GITHUB_ACTIONS: "true" },
    home: "/home/runner",
    platform: "linux",
    exists: candidate => candidate === runnerChrome,
    realpath: candidate => candidate,
    listDirectories: async () => [],
  });
  assert.equal(result, runnerChrome);

  const developerResult = await findDisposableChromium({
    env: {},
    home: "/home/developer",
    platform: "linux",
    exists: candidate => candidate === runnerChrome,
    realpath: candidate => candidate,
    listDirectories: async () => [],
  });
  assert.equal(developerResult, null);
});

async function codeSignClones(root) {
  try {
    return (await readdir(root)).filter(name => name.startsWith("code_sign_clone."));
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

test("completed disposable Chrome launch leaves no new macOS code-sign clone", {
  timeout: 90000,
  skip: process.platform !== "darwin" ? "macOS code-sign clone producer proof" : false,
}, async t => {
  const chrome = await findDisposableChromium();
  if (!chrome) {
    t.skip("Chrome for Testing/Playwright Chromium is unavailable; producer proof not run");
    return;
  }
  const userTemp = execFileSync("/usr/bin/getconf", ["DARWIN_USER_TEMP_DIR"],
    { encoding: "utf8" }).trim();
  const root = path.join(userTemp, "..", "X", "com.google.Chrome.code_sign_clone");
  const before = new Set(await codeSignClones(root));
  const browser = await launchChrome(chrome);
  let socket;
  try {
    socket = new WebSocket(browser.pageWsUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    const evaluated = new Promise((resolve, reject) => {
      socket.addEventListener("message", event => {
        const response = JSON.parse(event.data);
        if (response.id !== 1) return;
        if (response.error) reject(new Error(JSON.stringify(response.error)));
        else resolve(response.result);
      });
      socket.addEventListener("error", reject, { once: true });
    });
    socket.send(JSON.stringify({ id: 1, method: "Runtime.evaluate",
      params: { expression: "6 * 7", returnByValue: true } }));
    assert.equal((await evaluated).result.value, 42);
  } finally {
    socket?.close();
    await browser.close();
  }
  const added = (await codeSignClones(root)).filter(name => !before.has(name));
  assert.deepEqual(added, [], "completed test launch leaked code-sign clones");
});
