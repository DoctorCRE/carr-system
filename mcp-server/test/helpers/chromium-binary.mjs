import { existsSync, realpathSync } from "node:fs";
import { readdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const SYSTEM_CHROME = path.normalize(
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome");
const CI_LINUX_CHROMIUM = [
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
];

function accepted(candidate, exists, realpath) {
  if (!candidate) return false;
  const normalized = path.normalize(candidate);
  if (!exists(normalized)) return false;
  try { return path.normalize(realpath(normalized)) !== SYSTEM_CHROME; }
  catch { return false; }
}

export async function findDisposableChromium({
  env = process.env,
  home = os.homedir(),
  platform = process.platform,
  exists = existsSync,
  realpath = realpathSync,
  listDirectories = async directory => (await readdir(directory, { withFileTypes: true }))
    .filter(entry => entry.isDirectory()).map(entry => entry.name),
} = {}) {
  for (const candidate of [env.CHROME_FOR_TESTING_PATH,
    env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH]) {
    if (accepted(candidate, exists, realpath)) return path.normalize(candidate);
  }

  if (platform === "linux" && env.GITHUB_ACTIONS === "true") {
    for (const candidate of CI_LINUX_CHROMIUM) {
      if (accepted(candidate, exists, realpath)) return path.normalize(candidate);
    }
  }

  const roots = [];
  if (env.PLAYWRIGHT_BROWSERS_PATH) roots.push(env.PLAYWRIGHT_BROWSERS_PATH);
  roots.push(platform === "darwin"
    ? path.join(home, "Library/Caches/ms-playwright")
    : path.join(home, ".cache/ms-playwright"));
  for (const root of roots) {
    let releases = [];
    try { releases = await listDirectories(root); } catch { continue; }
    for (const release of releases.filter(name => /^chromium-\d+$/.test(name))
      .sort((left, right) => Number(right.split("-").at(-1)) - Number(left.split("-").at(-1)))) {
      const base = path.join(root, release);
      const candidates = platform === "darwin" ? [
        path.join(base, "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
        path.join(base, "chrome-mac/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
      ] : [path.join(base, "chrome-linux/chrome"), path.join(base, "chrome-linux64/chrome")];
      for (const candidate of candidates) if (accepted(candidate, exists, realpath)) return candidate;
    }
  }
  return null;
}
