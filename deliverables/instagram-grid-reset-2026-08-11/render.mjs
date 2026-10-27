// Render Instagram grid cards to images with headless Chromium.
import { CHROMIUM_SAFETY_ARGS } from "../../tools/chromium-safety-args.mjs";
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import puppeteer from 'puppeteer';

const directory = path.dirname(fileURLToPath(import.meta.url));
const output = path.join(directory, 'rendered');
const executablePath = '/Users/booko/.cache/puppeteer/chrome/mac-1069273/chrome-mac/Chromium.app/Contents/MacOS/Chromium';
const pageUrl = pathToFileURL(path.join(directory, 'index.html')).href;
const browser = await puppeteer.launch({
  headless: true,
  executablePath,
  userDataDir: path.join(directory, '.chrome-profile'),
  args: [...CHROMIUM_SAFETY_ARGS, '--no-sandbox'],
});

try {
  for (let card = 1; card <= 9; card += 1) {
    const page = await browser.newPage();
    await page.setViewport({ width: 1080, height: 1350, deviceScaleFactor: 1 });
    await page.goto(`${pageUrl}?card=${card}`, { waitUntil: 'networkidle0' });
    await page.screenshot({ path: path.join(output, `card-${String(card).padStart(2, '0')}.png`) });
    await page.close();
  }

  const grid = await browser.newPage();
  await grid.setViewport({ width: 3312, height: 4086, deviceScaleFactor: 1 });
  await grid.goto(pageUrl, { waitUntil: 'networkidle0' });
  await grid.screenshot({ path: path.join(output, 'grid-preview.png') });
  await grid.close();
} finally {
  await browser.close();
}
