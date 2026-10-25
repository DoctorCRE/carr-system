import { CHROMIUM_SAFETY_ARGS } from "../../tools/chromium-safety-args.mjs";
import { chromium } from "playwright-core";
import { findDisposableChromium } from "../../mcp-server/test/helpers/chromium-binary.mjs";

export async function testBrowser(t) {
  const chrome = await findDisposableChromium();
  if (!chrome) throw new Error("Install disposable Playwright Chromium; browser tests must not skip");
  const browser = await chromium.launch({ executablePath: chrome, channel: "chromium", headless: true, args: [...CHROMIUM_SAFETY_ARGS] });
  t.after(() => browser.close());
  return browser;
}
