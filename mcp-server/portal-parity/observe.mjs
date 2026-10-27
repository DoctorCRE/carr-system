// Observe one browser probe on one side. Observations carry digests, never values.
import { createHash } from 'node:crypto';
import { neutralize } from './probes.mjs';

const SCREENSHOT_WIDTHS = Object.freeze([1440, 820, 390]);
const HEIGHT = 900;
const STEP_TIMEOUT = 15000;
const MAP_MASK = '.maplibregl-map, canvas';

export const digest = value => createHash('sha256').update(value).digest('hex');

export function relativeTo(base, href) {
  let url;
  try { url = new URL(href, base); } catch { return `invalid:${digest(String(href))}`; }
  const root = new URL(base);
  if (url.origin !== root.origin) return url.href;
  const path = url.pathname.startsWith(root.pathname) ? url.pathname.slice(root.pathname.length) : url.pathname;
  return `${path}${url.search}${url.hash}`;
}

async function settle(page) {
  await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
  await page.evaluate(async () => {
    await document.fonts?.ready;
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

class Missing extends Error {}

async function first(page, selector) {
  const locator = page.locator(selector).first();
  try { await locator.waitFor({ state: 'attached', timeout: STEP_TIMEOUT }); }
  catch { throw new Missing(selector); }
  return locator;
}

async function runStep(page, step, state) {
  if (step.dialog) { state.dialog = step.dialog; return; }
  if (step.view !== undefined) { await page.evaluate(view => { location.hash = view; }, step.view); return; }
  if (step.wait !== undefined) { await page.waitForTimeout(step.wait); return; }
  if (step.scroll !== undefined) { await page.evaluate(y => scrollTo(0, y), step.scroll); return; }
  if (step.press !== undefined) { await page.keyboard.press(step.press); return; }
  // A forced click (map markers) dispatches the event on the element itself,
  // so overlapping map chrome at a given viewport cannot swallow it.
  if (step.click) return step.force ? (await first(page, step.click)).dispatchEvent('click')
    : (await first(page, step.click)).click({ timeout: STEP_TIMEOUT });
  if (step.hover) return (await first(page, step.hover)).hover({ timeout: STEP_TIMEOUT, force: true });
  if (step.focus) return (await first(page, step.focus)).focus({ timeout: STEP_TIMEOUT });
  if (step.fill) return (await first(page, step.fill)).fill(String(step.value), { timeout: STEP_TIMEOUT });
  if (step.select) return (await first(page, step.select)).selectOption({ index: step.index }, { timeout: STEP_TIMEOUT });
  if (step.drag) return (await first(page, step.drag)).dragTo(await first(page, step.to), { timeout: STEP_TIMEOUT });
  throw new Error(`unknown step ${JSON.stringify(Object.keys(step))}`);
}

// Element state that an interaction can change. Text and values come back raw
// and are digested in Node before they become facts.
// `style` names computed properties (custom properties included) and
// `geometry` adds the rounded box, for rows about layout rather than content.
function snapshot([selector, { text: withText, style = [], geometry = false }]) {
  const nodes = [...document.querySelectorAll(selector)].slice(0, 60);
  return {
    count: document.querySelectorAll(selector).length,
    nodes: nodes.map(el => ({
      tag: el.tagName.toLowerCase(),
      text: withText ? (el.innerText ?? el.textContent ?? '').replace(/\s+/g, ' ').trim() : '',
      cls: [...el.classList].sort().join(' '),
      hidden: el.hidden || el.closest('[hidden]') !== null,
      open: el.open ?? null,
      disabled: el.disabled ?? null,
      checked: el.checked ?? null,
      value: typeof el.value === 'string' ? el.value : null,
      aria: ['aria-pressed', 'aria-current', 'aria-expanded', 'aria-selected'].map(a => el.getAttribute(a)).join('|'),
      style: style.map(name => getComputedStyle(el).getPropertyValue(name).trim()).join('|'),
      box: geometry ? (({ x, y, width, height }) => [x, y, width, height].map(Math.round).join(','))(el.getBoundingClientRect()) : '',
    })),
  };
}

function focusDescriptor() {
  const el = document.activeElement;
  if (!el || el === document.body) return 'body';
  const parentIndex = el.parentElement ? [...el.parentElement.children].indexOf(el) : 0;
  return `${el.tagName.toLowerCase()}#${el.id}.${[...el.classList].sort().join('.')}:${parentIndex}`;
}

async function pixelDifference(page, left, right) {
  return page.evaluate(async ([a, b]) => {
    const load = src => new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = reject; img.src = `data:image/png;base64,${src}`; });
    const [x, y] = await Promise.all([load(a), load(b)]);
    if (x.width !== y.width || x.height !== y.height) return 1;
    const pixels = img => { const c = document.createElement('canvas'); c.width = img.width; c.height = img.height; const g = c.getContext('2d'); g.drawImage(img, 0, 0); return g.getImageData(0, 0, img.width, img.height).data; };
    const p = pixels(x), q = pixels(y);
    let differing = 0;
    for (let i = 0; i < p.length; i += 4) {
      if (Math.max(Math.abs(p[i] - q[i]), Math.abs(p[i + 1] - q[i + 1]), Math.abs(p[i + 2] - q[i + 2]), Math.abs(p[i + 3] - q[i + 3])) > 8) differing++;
    }
    return differing / (p.length / 4 || 1);
  }, [left.toString('base64'), right.toString('base64')]);
}

export async function compareScreenshots(browser, left, right) {
  const page = await browser.newPage();
  try {
    const ratios = {};
    for (const width of Object.keys({ ...left, ...right })) {
      ratios[width] = left[width] && right[width] ? await pixelDifference(page, left[width], right[width])
        : (left[width] || right[width]) ? 1 : 0;
    }
    return ratios;
  } finally { await page.close(); }
}

async function locateMedia(page, probe, requests, base) {
  if (probe.path !== undefined) return new URL(probe.path, base).href;
  if (probe.request_match !== undefined) {
    const pattern = new RegExp(probe.request_match);
    return requests.find(url => pattern.test(url)) ?? null;
  }
  if (probe.font_face !== undefined) {
    return page.evaluate(n => {
      const urls = [];
      for (const sheet of document.styleSheets) {
        let rules; try { rules = sheet.cssRules; } catch { continue; }
        for (const rule of rules) if (rule instanceof CSSFontFaceRule) {
          const match = /url\(["']?([^"')]+)["']?\)/.exec(rule.style.getPropertyValue('src'));
          if (match) urls.push(new URL(match[1], sheet.href ?? location.href).href);
        }
      }
      return urls[n - 1] ?? null;
    }, probe.font_face);
  }
  const element = await first(page, probe.target);
  const value = await element.getAttribute(probe.attr ?? 'src');
  return value === null ? null : new URL(value, page.url()).href;
}

// Returns { facts, shots } for one side. `facts` is a flat map compared for
// equality; `shots` holds PNG buffers per width for `dom` probes.
export async function observeBrowserProbe(browser, base, probe, aliases, { cookies = [] } = {}) {
  const context = await browser.newContext({ viewport: { width: SCREENSHOT_WIDTHS[0], height: HEIGHT },
    // Reduced motion keeps screenshots stable; a probe of motion itself opts out.
    reducedMotion: probe.motion ? 'no-preference' : 'reduce' });
  if (cookies.length) await context.addCookies(cookies);
  const page = await context.newPage();
  const state = { dialog: 'accept' };
  const requests = [], calls = [];
  let recording = false;
  page.on('dialog', dialog => (state.dialog === 'dismiss' ? dialog.dismiss() : dialog.accept()).catch(() => {}));
  page.on('request', request => {
    requests.push(request.url());
    const same = new URL(request.url()).origin === new URL(base).origin;
    if (recording && same && ['fetch', 'xhr'].includes(request.resourceType())) {
      calls.push(`${request.method()} ${neutralize(relativeTo(base, request.url()).replace(/\?.*$/, ''), aliases)}`);
    }
  });
  const facts = {};
  let shots;
  try {
    if (probe.kind === 'link' && probe.navigate !== undefined) {
      await page.goto(new URL(probe.navigate, base).href, { waitUntil: 'load' });
      await page.waitForTimeout(1500);
      await settle(page);
      facts.destination = neutralize(relativeTo(base, page.url()), aliases);
      return { facts };
    }
    await page.goto(probe.view ? `${base}#${probe.view}` : base, { waitUntil: 'load' });
    await settle(page);
    recording = true;
    for (const step of probe.steps ?? []) await runStep(page, step, state);
    await settle(page);
    if (probe.kind === 'dom') {
      const locator = page.locator(probe.target);
      facts.count = await locator.count();
      if (!facts.count) throw new Missing(probe.target);
      const text = (await locator.evaluateAll(els => els.map(el => el.innerText ?? el.textContent ?? '').join('\n'))).replace(/\s+/g, ' ').trim();
      facts.text = digest(text);
      // A section that is not on screen after its view and steps is a missing
      // target, never an equal "hidden on both sides".
      if (!await locator.first().isVisible()) throw new Missing(probe.target);
      shots = {};
      for (const width of SCREENSHOT_WIDTHS) {
        await page.setViewportSize({ width, height: HEIGHT });
        await settle(page);
        shots[width] = await locator.first().screenshot({ mask: [page.locator(MAP_MASK)], animations: 'disabled', timeout: STEP_TIMEOUT });
      }
    } else if (probe.kind === 'interaction') {
      for (const [index, entry] of probe.observe.entries()) {
        const { selector, text = true, style, geometry } = typeof entry === 'string' ? { selector: entry } : entry;
        const shot = await page.evaluate(snapshot, [selector, { text, style, geometry }]);
        if (!shot.count) throw new Missing(selector);
        for (const node of shot.nodes) {
          node.text = digest(node.text);
          if (node.value !== null) node.value = digest(node.value);
        }
        facts[`observed-${index + 1}`] = neutralize(JSON.stringify(shot), aliases);
      }
      facts.focus = neutralize(await page.evaluate(focusDescriptor), aliases);
      facts.requests = calls.join(', ');
    } else if (probe.kind === 'link') {
      const element = await first(page, probe.target);
      const link = await element.evaluate(el => ({
        href: el.getAttribute('href'), target: el.getAttribute('target') ?? '',
        rel: (el.getAttribute('rel') ?? '').split(/\s+/).filter(Boolean).sort().join(' '),
        label: (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(),
      }));
      facts.href = link.href === null ? 'none'
        : neutralize(link.href.startsWith('#') ? link.href : relativeTo(base, new URL(link.href, page.url()).href), aliases);
      facts.target = link.target;
      facts.rel = link.rel;
      facts.label = digest(link.label);
    } else if (probe.kind === 'media') {
      const url = await locateMedia(page, probe, requests, base);
      if (!url) throw new Missing('media locator');
      const response = await context.request.get(url, { failOnStatusCode: false, maxRedirects: 0 });
      facts.status = response.status();
      facts.type = response.status() === 200 ? (response.headers()['content-type'] ?? '') : '';
      facts.sha256 = response.status() === 200 ? digest(await response.body()) : '';
    }
    return { facts, shots };
  } catch (error) {
    if (error instanceof Missing) return { missing: true, facts };
    return { error: error.message.split('\n')[0].slice(0, 200), facts };
  } finally {
    await context.close();
  }
}
