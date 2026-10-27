// Launch the disposable headless Chromium the parity harness drives.
import { CHROMIUM_SAFETY_ARGS } from "../../tools/chromium-safety-args.mjs";
import { chromium } from "playwright-core";
import { findDisposableChromium } from "../test/helpers/chromium-binary.mjs";

export async function launchParityBrowser() {
  const chrome = await findDisposableChromium();
  if (!chrome) throw new Error("Install disposable Playwright Chromium; the parity harness never uses the system Chrome profile");
  return chromium.launch({ executablePath: chrome, headless: true, args: [...CHROMIUM_SAFETY_ARGS] });
}
