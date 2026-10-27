// Launch sealed headless Chrome for one social-batch card (tools/social_batch.py render).
//
//   node tools/card-chrome.mjs run   <chrome> <port> <width> <height> <output-flag> <page>
//   node tools/card-chrome.mjs print <chrome> <port> <width> <height> <output-flag> <page>
//
// The page loads from http://card.test/ through the 127.0.0.1 card server, which is
// Chrome's only proxy (loopback included) and answers that one origin. Every host name
// resolves to nothing, and WebRTC may not send UDP around the proxy. `print` writes the
// argv as JSON so callers can pin it; `run` launches Chrome with a throwaway profile.
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { CHROMIUM_SAFETY_ARGS } from "./chromium-safety-args.mjs";

export const CARD_HOST = "card.test";

export function cardLaunch({ profile, port, width, height, output, page }) {
  return {
    args: [...CHROMIUM_SAFETY_ARGS, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
      "--no-default-browser-check", `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
      `--proxy-server=http://127.0.0.1:${port}`, "--proxy-bypass-list=<-loopback>",
      "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
      "--virtual-time-budget=5000", output, `http://${CARD_HOST}/${encodeURI(page)}`],
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mode, chrome, port, width, height, output, page = ""] = process.argv.slice(2);
  if (!["run", "print"].includes(mode) || !chrome || !output) {
    console.error("usage: card-chrome.mjs run|print <chrome> <port> <width> <height> <output-flag> <page>");
    process.exit(64);
  }
  if (mode === "print") {
    process.stdout.write(JSON.stringify(cardLaunch({ profile: "<profile>", port, width, height, output, page }).args));
    process.exit(0);
  }
  const profile = mkdtempSync(join(tmpdir(), "card-chrome-"));
  try {
    const { args } = cardLaunch({ profile, port, width, height, output, page });
    const result = spawnSync(chrome, args, { stdio: ["ignore", "inherit", "inherit"], timeout: 90_000 });
    process.exitCode = result.status ?? 124;
  } finally {
    rmSync(profile, { recursive: true, force: true });
  }
}
