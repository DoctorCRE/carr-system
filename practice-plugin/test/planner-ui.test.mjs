import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { chromium } from 'playwright-core';
import { plannerHtml } from '../.build/planner-resource.mjs';
import { calculatePlan } from '../src/planner.mjs';

const dental = { practice_type: 'dental_gp', providers: 1, operatories: 5, exam_rooms: 0, market: 'mobile_downtown' };
const medical = { practice_type: 'medical', providers: 2, operatories: 0, exam_rooms: 6, market: 'other_market' };
const paths = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', chromium.executablePath()];

test('headless MCP App renders dental and medical, shares context, follows chat updates and clears removed context', async () => {
  const executablePath = paths.find(p => fs.existsSync(p));
  assert.ok(executablePath, 'Install Chrome or run npx playwright-core install chromium; this test must not skip.');
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(5000);
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.exposeFunction('calculate', params => calculatePlan(params.arguments));
    await page.route('https://host.synthetic.invalid/', route => route.fulfill({ contentType: 'text/html', body: '<html><body><iframe title="Planner" style="width:100%;height:850px;border:0"></iframe></body></html>' }));
    await page.goto('https://host.synthetic.invalid/');
    await page.evaluate(html => {
      window.shared = []; window.calls = []; window.attachedContext = null;
      const frame = document.querySelector('iframe');
      window.notify = params => frame.contentWindow.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params }, '*');
      window.toolResult = result => frame.contentWindow.postMessage({ jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: result }, '*');
      window.addEventListener('message', async event => {
        if (event.source !== frame.contentWindow || !event.data?.method || event.data.id === undefined) return;
        const m = event.data; let result;
        if (m.method === 'ui/initialize') result = { protocolVersion: '2026-01-26', hostInfo: { name: 'Synthetic host', version: '1' },
          hostCapabilities: { serverTools: {}, updateModelContext: { text: {}, structuredContent: {} }, experimental: { 'openai/modelContext': {} } }, hostContext: { displayMode: 'fullscreen', 'openai/modelContext': window.attachedContext } };
        else if (m.method === 'tools/call') { window.calls.push(m.params); result = await window.calculate(m.params); }
        else if (m.method === 'ui/update-model-context') { window.shared.push(m.params); window.attachedContext = { ...m.params, updateId: `local-${window.shared.length}` }; result = { _meta: { 'openai/modelContext': { updateId: window.attachedContext.updateId } } }; }
        else result = {};
        frame.contentWindow.postMessage({ jsonrpc: '2.0', id: m.id, result }, '*');
      });
      frame.srcdoc = html;
    }, plannerHtml);
    const frame = page.frameLocator('iframe');
    try { await frame.locator('#sync-note').filter({ hasText: 'shared with this conversation' }).waitFor(); }
    catch (error) { throw new Error(`${error.message}; page errors: ${errors.join('; ')}; status: ${await frame.locator('#status').innerText()}`); }
    await frame.locator('#rooms').fill('5'); await frame.locator('button[type=submit]').click();
    try { await frame.locator('.area').filter({ hasText: '2,000–2,000 usable SF' }).waitFor(); }
    catch (error) { throw new Error(`${error.message}; page errors: ${errors.join('; ')}; status: ${await frame.locator('#status').innerText()}; calls: ${JSON.stringify(await page.evaluate(() => window.calls))}`); }
    assert.match(await frame.locator('#plan').innerText(), /10–10 parking spaces/);
    assert.match(await frame.locator('#plan').innerText(), /Room functions[\s\S]*Building due diligence/);
    await page.waitForFunction(() => window.shared.length === 1);
    assert.deepEqual((await page.evaluate(() => window.shared[0])).structuredContent.practice_space_plan.inputs, dental);
    await page.evaluate(() => { const iframe = document.querySelector('iframe'); iframe.srcdoc = iframe.srcdoc; });
    await frame.locator('.area').filter({ hasText: '2,000–2,000 usable SF' }).waitFor();
    assert.equal(await page.evaluate(() => window.shared.length), 1, 'remount restores existing context without republishing');
    await frame.locator('details').first().click();
    assert.ok(await frame.locator('details').first().locator('li').count());
    fs.mkdirSync(new URL('../.build/screenshots/', import.meta.url), { recursive: true });
    await page.frames()[1].evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: new URL('../.build/screenshots/dental.png', import.meta.url).pathname });
    await page.evaluate(inputs => window.notify({ 'openai/modelContext': { updateId: 'chat-medical', structuredContent: { practice_space_plan: { inputs } } } }), medical);
    const expected = calculatePlan(medical).structuredContent.results.usable_square_feet;
    await frame.locator('.area').filter({ hasText: `${expected.low.toLocaleString()}–${expected.high.toLocaleString()} usable SF` }).waitFor();
    assert.equal(await frame.locator('#vertical').inputValue(), 'medical');
    assert.equal(await frame.locator('#rooms').inputValue(), '6');
    assert.deepEqual(await page.evaluate(() => window.calls.at(-1).arguments), medical);
    assert.equal(await page.evaluate(() => window.shared.length), 1, 'host context updates must not echo back');
    await page.frames()[1].evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: new URL('../.build/screenshots/medical.png', import.meta.url).pathname });
    await page.evaluate(result => window.toolResult(result), calculatePlan({ ...dental, operatories: 7 }));
    await frame.locator('.area').filter({ hasText: '2,800–2,800 usable SF' }).waitFor();
    await page.waitForFunction(() => window.shared.length === 2);
    await page.evaluate(() => window.notify({ 'openai/modelContext': null }));
    await frame.locator('#status').filter({ hasText: 'Choose a program' }).waitFor();
    assert.equal(await frame.locator('.area').count(), 0);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
