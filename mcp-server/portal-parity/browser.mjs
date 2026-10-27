// Launch the disposable headless Chromium the parity harness drives.
import { CHROMIUM_SAFETY_ARGS } from "../../tools/chromium-safety-args.mjs";
import { chromium } from "playwright-core";
import { findDisposableChromium } from "../test/helpers/chromium-binary.mjs";

// Headless unless a partner asks for a window with --headed; the safety args
// keep the browser off the login keychain either way. The binary stays Chrome
// for Testing: headless, it registers with macOS as BackgroundOnly (no Dock
// icon), while the headless shell registers as a UIElement app.
export function parityLaunchOptions(executablePath, { headed = false } = {}) {
  return { executablePath, headless: !headed, args: [...CHROMIUM_SAFETY_ARGS] };
}

export async function launchParityBrowser({ headed = false } = {}) {
  const chrome = await findDisposableChromium();
  if (!chrome) throw new Error("Install disposable Playwright Chromium; the parity harness never uses the system Chrome profile");
  return chromium.launch(parityLaunchOptions(chrome, { headed }));
}
