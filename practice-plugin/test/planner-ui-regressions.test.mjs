import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright-core';
import { plannerHtml } from '../.build/planner-resource.mjs';
import { calculatePlan, callPlanner } from '../src/planner.mjs';

const dental = { practice_type: 'dental_gp', providers: 1, operatories: 5, exam_rooms: 0, market: 'mobile_downtown' };
async function host(t, { delay = false, contextSupport = true, echo = false } = {}) {
  const executablePath = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', chromium.executablePath()].find(p => fs.existsSync(p));
  assert.ok(executablePath, 'Browser regressions must run, never skip');
  const browser = await chromium.launch({ executablePath, headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(3000);
  await page.exposeFunction('calculate', params => calculatePlan(params.arguments));
  await page.route('https://host.synthetic.invalid/', route => route.fulfill({ contentType: 'text/html', body: '<iframe style="width:100%;height:800px;border:0"></iframe>' }));
  await page.goto('https://host.synthetic.invalid/');
  await page.evaluate(({ html, delay, contextSupport, echo }) => {
    window.shared = []; window.calls = []; window.pending = []; window.pendingCalls = []; window.delayCalls = false; window.attachedContext = null;
    const frame = document.querySelector('iframe');
    window.notify = context => { window.attachedContext = context; frame.contentWindow.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { 'openai/modelContext': context } }, '*'); };
    window.toolResult = result => frame.contentWindow.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: result }, '*');
    window.complete = () => window.pending.shift()();
    window.addEventListener('message', async event => {
      if (event.source !== frame.contentWindow || !event.data?.method || event.data.id === undefined) return;
      const m = event.data;
      const reply = result => frame.contentWindow.postMessage({ jsonrpc: '2.0', id: m.id, result }, '*');
      if (m.method === 'ui/initialize') reply({ protocolVersion: '2026-01-26', hostInfo: { name: 'Synthetic host', version: '1' }, hostCapabilities: { serverTools: {}, ...(contextSupport ? { updateModelContext: { text: {}, structuredContent: {} }, experimental: { 'openai/modelContext': {} } } : {}) }, hostContext: { displayMode: 'fullscreen', ...(contextSupport ? { 'openai/modelContext': window.attachedContext } : {}) } });
      else if (m.method === 'tools/call') {
        window.calls.push(m.params);
        const result = await window.calculate(m.params);
        if (window.delayCalls) window.pendingCalls.push(() => reply(result)); else reply(result);
      }
      else if (m.method === 'ui/update-model-context') {
        window.shared.push(m.params);
        const complete = () => {
          window.attachedContext = { ...m.params, updateId: `local-${m.id}` };
          if (echo === 'before') window.notify(window.attachedContext);
          reply({ _meta: { 'openai/modelContext': { updateId: window.attachedContext.updateId } } });
          if (echo === 'after') window.notify(window.attachedContext);
        };
        if (delay) window.pending.push(complete); else complete();
      } else reply({});
    });
    frame.srcdoc = html;
  }, { html: plannerHtml, delay, contextSupport, echo });
  const frame = page.frameLocator('iframe');
  await frame.locator('#sync-note').filter({ hasText: contextSupport ? 'shared with this conversation' : 'does not support' }).waitFor();
  return { page, frame };
}
const submit = async (frame, rooms) => { await frame.locator('#rooms').fill(String(rooms)); await frame.locator('button[type=submit]').click(); await frame.locator('.area').filter({ hasText: `${(rooms * 400).toLocaleString()}–${(rooms * 400).toLocaleString()} usable SF` }).waitFor(); };
const remount = page => page.evaluate(() => { const frame = document.querySelector('iframe'); frame.srcdoc = frame.srcdoc; });

for (const echo of ['after', 'before']) test(`obsolete removal echo ${echo} its receipt preserves the newer render`, async t => {
  const { page, frame } = await host(t, { delay: true, echo });
  await submit(frame, 5); await page.waitForFunction(() => window.pending.length === 1);
  await page.evaluate(() => window.notify(null));
  await frame.locator('#status').filter({ hasText: 'Choose a program' }).waitFor();
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => window.pending.length === 1 && window.shared.length === 2);
  await submit(frame, 7);
  const newerRender = await frame.locator('#plan').innerText();
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => window.pending.length === 1 && window.shared.length === 3);
  await page.frames()[1].evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  // The next publication starts only after the clear receipt/echo is processed.
  assert.equal(await frame.locator('#plan').innerText(), newerRender, 'obsolete clear must not erase the newer plan');
  assert.match(await frame.locator('#status').innerText(), /Space plan updated/);
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => window.attachedContext.structuredContent.practice_space_plan.inputs.operatories === 7);
  await remount(page); await frame.locator('.area').filter({ hasText: '2,800–2,800' }).waitFor();
  // A distinct host removal still clears the plan after publication echoes.
  await page.evaluate(() => window.notify({ content: [], structuredContent: {}, updateId: 'host-removal' }));
  await frame.locator('#status').filter({ hasText: 'Choose a program' }).waitFor();
  assert.equal(await frame.locator('.area').count(), 0);
});

test('delayed publications cannot replace a newer plan or reattach a removed plan', async t => {
  const { page, frame } = await host(t, { delay: true });
  await submit(frame, 5); await page.waitForFunction(() => window.pending.length === 1);
  await submit(frame, 7);
  assert.equal(await page.evaluate(() => window.shared.length), 1, 'only one host publication may be outstanding');
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => window.pending.length === 1);
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => window.attachedContext.structuredContent.practice_space_plan.inputs.operatories === 7);
  await remount(page); await frame.locator('.area').filter({ hasText: '2,800–2,800' }).waitFor();
  await submit(frame, 6); await page.waitForFunction(() => window.pending.length === 1);
  await page.evaluate(() => window.notify(null));
  await frame.locator('#status').filter({ hasText: 'Choose a program' }).waitFor();
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => window.pending.length === 1);
  await page.evaluate(() => window.complete());
  await page.waitForFunction(() => !window.attachedContext.structuredContent.practice_space_plan);
  await remount(page); await frame.locator('#status').filter({ hasText: 'Choose a program' }).waitFor();
  assert.equal(await frame.locator('.area').count(), 0);
});

test('partial plans and incompatible host inputs retain the last complete render and report failure', async t => {
  const { page, frame } = await host(t);
  await submit(frame, 5);
  await page.waitForFunction(() => window.shared.length === 1);
  const before = await frame.locator('#plan').innerText();
  const valid = calculatePlan(dental);
  const mutations = [
    p => { delete p.results.parking; },
    p => { p.results.usable_square_feet.low = 'invalid'; },
    p => { p.results.due_diligence.hvac = null; },
    p => { p.inputs_used.operatories = 0; },
    p => { p.inputs_used.practice_type = 'dental_endo'; p.inputs_used.providers = 2; p.results.practice_type = 'dental_endo'; },
  ];
  for (const mutate of mutations) {
    const incoming = structuredClone(valid); mutate(incoming.structuredContent);
    await page.evaluate(result => window.toolResult(result), incoming);
    await frame.locator('#status').filter({ hasText: 'could not' }).waitFor();
    assert.equal(await frame.locator('#plan').innerText(), before);
    assert.equal(await frame.locator('#undo').isDisabled(), true, 'invalid plans must not replace undo state');
  }
  for (const inputs of [{ ...dental, operatories: 0 }, { ...dental, practice_type: 'dental_endo', providers: 2 }]) {
    await page.evaluate(inputs => window.notify({ updateId: `invalid-${inputs.practice_type}`, structuredContent: { practice_space_plan: { inputs } } }), inputs);
    await frame.locator('#status').filter({ hasText: 'could not' }).waitFor();
    assert.equal(await frame.locator('#plan').innerText(), before);
  }
  assert.equal(await page.evaluate(() => window.calls.length), 1, 'invalid host inputs never reach the calculator');
  assert.equal(await page.evaluate(() => window.shared.length), 1, 'invalid plans never publish');
});

test('unknown inputs and unsupported markets cannot reach attached context or summary', async t => {
  const { page, frame } = await host(t);
  await submit(frame, 5); await page.waitForFunction(() => window.shared.length === 1);
  const before = await frame.locator('#plan').innerText();
  for (const unknownMarket of [false, true]) {
    const incoming = calculatePlan(dental);
    if (unknownMarket) incoming.structuredContent.market = 'SYNTHETIC_UNSUPPORTED_MARKET';
    else incoming.structuredContent.inputs_used.synthetic_unknown = 'SYNTHETIC_SENTINEL';
    await page.evaluate(result => window.toolResult(result), incoming);
    await frame.locator('#status').filter({ hasText: 'could not' }).waitFor();
    assert.equal(await frame.locator('#plan').innerText(), before);
    assert.equal(await page.evaluate(() => window.shared.length), 1);
    assert.doesNotMatch(JSON.stringify(await page.evaluate(() => window.shared)), /SYNTHETIC_SENTINEL|SYNTHETIC_UNSUPPORTED_MARKET|synthetic_unknown/);
  }
  for (const inputs of [{ ...dental, synthetic_unknown: 'SYNTHETIC_SENTINEL' }, { ...dental, market: 'SYNTHETIC_UNSUPPORTED_MARKET' }]) {
    await page.evaluate(inputs => window.notify({ updateId: JSON.stringify(inputs), structuredContent: { practice_space_plan: { inputs } } }), inputs);
    await frame.locator('#status').filter({ hasText: 'could not' }).waitFor();
  }
  assert.equal(await page.evaluate(() => window.calls.length), 1);
});

test('area conversion survives context remount, edits and undo', async t => {
  const { page, frame } = await host(t);
  const inputs = { ...dental, rentable_to_usable_factor: 1.25 };
  const plan = calculatePlan(inputs);
  await page.evaluate(result => window.toolResult(result), plan);
  await frame.locator('.area').waitFor(); await page.waitForFunction(() => window.shared.length === 1);
  await remount(page); await frame.locator('.area').waitFor();
  assert.deepEqual(await page.evaluate(() => window.calls.at(-1).arguments), inputs);
  assert.deepEqual(calculatePlan(await page.evaluate(() => window.calls.at(-1).arguments)).structuredContent, plan.structuredContent, 'restored result retains rentable area and missing-input list');
  await submit(frame, 7); await page.waitForFunction(() => window.shared.length === 2);
  assert.equal(await page.evaluate(() => window.shared.at(-1).structuredContent.practice_space_plan.inputs.rentable_to_usable_factor), 1.25);
  await frame.locator('#undo').click();
  await frame.locator('.area').filter({ hasText: '2,000–2,000' }).waitFor();
  assert.deepEqual(await page.evaluate(() => window.calls.at(-1).arguments), inputs);
});

test('planning relationships are visible at phone width and honor reduced motion', async t => {
  const { page, frame } = await host(t);
  await submit(frame, 5);
  const visual = frame.locator('svg[role=img]');
  await visual.waitFor();
  assert.match(await visual.getAttribute('aria-label'), /planning relationships/i);
  for (const label of ['Program inputs', 'Sizing rule', 'Usable area', 'Preliminary parking', 'Building due diligence']) assert.match(await visual.textContent(), new RegExp(label));
  assert.match(await visual.textContent(), /2,000–2,000 usable SF/);
  assert.match(await visual.textContent(), /Local requirements unverified/);
  const sizing = await page.frames()[1].evaluate(() => ({ viewport: innerWidth, scroll: document.documentElement.scrollWidth, animations: document.getAnimations().length }));
  assert.equal(sizing.scroll, sizing.viewport);
  assert.equal(sizing.animations, 0);
  fs.mkdirSync(new URL('../.build/screenshots/', import.meta.url), { recursive: true });
  await visual.screenshot({ path: new URL('../.build/screenshots/planning-phone.png', import.meta.url).pathname });
});

test('removal clears busy state and ignores the outstanding calculation', async t => {
  const { page, frame } = await host(t);
  await page.evaluate(() => { window.delayCalls = true; });
  await frame.locator('button[type=submit]').click();
  await page.waitForFunction(() => window.pendingCalls.length === 1);
  await page.evaluate(() => window.notify(null));
  await frame.locator('#status').filter({ hasText: 'Choose a program' }).waitFor();
  assert.equal(await frame.locator('#status').getAttribute('class'), '', 'removed context must not look busy');
  await page.evaluate(() => window.pendingCalls.shift()());
  await page.frames()[1].evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.evaluate(() => { window.delayCalls = false; });
  assert.equal(await frame.locator('.area').count(), 0);
  assert.equal(await page.evaluate(() => window.shared.length), 0);
});

test('hosts without Model-App Context open in a ready empty state', async t => {
  const { frame } = await host(t, { contextSupport: false });
  assert.match(await frame.locator('#status').innerText(), /Choose a program/);
  await submit(frame, 5);
});

test('opening entrypoint results retain the ready prompt until a plan is calculated', async t => {
  const { page, frame } = await host(t);
  await page.evaluate(result => window.toolResult(result), callPlanner('open_practice_space_planner'));
  await page.frames()[1].evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.match(await frame.locator('#status').innerText(), /Choose a program/);
  assert.equal(await page.evaluate(() => window.shared.length), 0);
});
