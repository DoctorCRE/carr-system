// Test synthetic Salesforce list capture in a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { findDisposableChromium } from './helpers/chromium-binary.mjs';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';

const headers = ['Opportunity Name', 'Type', 'Close Date', 'Commission', 'Stage', 'Transaction Type', 'Opportunity Owner', 'Account Name'];
const cells = ['Synthetic office', 'New', '12/01/2026', '$100', 'Legal', 'Lease', 'Joe Bookout', 'Example Practice'];
function fixture({ count = '1', rows = 1, names = headers, login = false } = {}) {
  return `<div class="countSortedByFilteredBy">${count} items • Updated just now</div>
    ${login ? '<input type="password" name="pw">' : ''}
    <table role="grid"><thead><tr>${names.map((name, i) => `<th aria-colindex="${i + 1}">${name}</th>`).join('')}</tr></thead>
    <tbody>${Array.from({ length: rows }, (_, row) => `<tr>${cells.map((value, col) => `<td aria-colindex="${col + 1}">${col === 0 ? `<a href="/lightning/r/Opportunity/00600000000000${row + 1}AAA/view">${value}</a>` : value}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', reject, { once: true });
  });
  let id = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(String(event.data));
    if (pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  });
  return {
    async evaluate(expression) {
      const key = ++id;
      const response = new Promise(resolve => pending.set(key, resolve));
      socket.send(JSON.stringify({ id: key, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true } }));
      const message = await response;
      assert.equal(message.error, undefined);
      assert.equal(message.result.exceptionDetails, undefined);
      return message.result.result.value;
    },
    close() { socket.close(); },
  };
}

test('synthetic Salesforce list capture proves complete rows and fails closed', { timeout: 90000 }, async t => {
  const chrome = await findDisposableChromium();
  assert.ok(chrome, 'A disposable Chromium must be available for the DOM proof');
  const bridge = new URL('../../tools/room-bridge/test-browser-cdp.mjs', import.meta.url);
  const child = spawn(process.execPath, [bridge.pathname, chrome], { stdio: ['pipe', 'pipe', 'pipe'] });
  const exited = once(child, 'exit');
  const lines = createInterface({ input: child.stdout });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  t.after(async () => { child.stdin.end(); await exited; lines.close(); });
  const first = await Promise.race([once(lines, 'line'), exited.then(() => { throw new Error(stderr); })]);
  const page = await connect(JSON.parse(first[0]).pageWsUrl);
  t.after(() => page.close());
  const script = await readFile(new URL('../../pipelines/capture-salesforce-list.js', import.meta.url), 'utf8');
  const run = async (html, setup = "true") => {
    await page.evaluate(`document.open(); document.write(${JSON.stringify(html)}); document.close(); true`);
    await page.evaluate(setup);
    await page.evaluate(script);
    return page.evaluate(`new Promise((resolve, reject) => {
      const started = Date.now(); const timer = setInterval(() => {
        if (window.__carrSalesforceRefresh.state !== 'running') { clearInterval(timer); resolve(window.__carrSalesforceRefresh); }
        else if (Date.now() - started > 15000) { clearInterval(timer); reject(new Error('capture timed out')); }
      }, 10);
    })`);
  };
  await t.test('complete snapshot carries full identity and loader fields', async () => {
    const result = await run(fixture());
    assert.equal(result.state, 'complete');
    assert.equal(result.expected_count, 1);
    assert.deepEqual(result.rows, [{ id: '006000000000001AAA', name: 'Synthetic office', type: 'New', close: '12/01/2026',
      commission: '$100', phase: 'Legal', transaction: 'Lease', owner: 'Joe Bookout', company: 'Example Practice' }]);
  });
  await t.test('signed-out page returns the exact sign-in finding and no rows', async () => {
    const result = await run(fixture({ login: true }));
    assert.equal(result.reason, 'Salesforce sign-in needed');
    assert.equal(result.rows, undefined);
  });
  await t.test('missing column is a page-shape failure', async () => {
    assert.equal((await run(fixture({ names: headers.slice(1) }))).reason, 'Salesforce page shape changed');
  });
  await t.test('partial rows cannot satisfy the list count', async () => {
    assert.equal((await run(fixture({ count: '2' }))).reason, 'Salesforce page shape changed');
  });
  await t.test('approximate Salesforce count cannot authorize import', async () => {
    assert.equal((await run(fixture({ count: '50+' }))).reason, 'Salesforce page shape changed');
  });
  await t.test('virtualized rows accumulate through vertical scrolling', async () => {
    const html = fixture({ count: '3' }).replace('<table', '<div id="scroller" style="height:100px;overflow:auto"><table')
      .replace('</table>', '</table><div style="height:350px"></div></div>');
    const result = await run(html, `(() => {
      const scroller = document.querySelector('#scroller');
      const body = document.querySelector('tbody');
      const initial = body.innerHTML;
      scroller.addEventListener('scroll', () => {
        const index = Math.min(3, 1 + Math.floor(scroller.scrollTop / 100));
        body.innerHTML = initial.replaceAll('006000000000001AAA', '00600000000000' + index + 'AAA');
      });
      return true;
    })()`);
    assert.equal(result.state, 'complete');
    assert.deepEqual(result.rows.map(row => row.id).sort(), ['006000000000001AAA', '006000000000002AAA', '006000000000003AAA']);
  });
  await t.test('duplicate opportunity rows fail the capture', async () => {
    const result = await run(fixture({ count: '2', rows: 2 }).replaceAll('006000000000002AAA', '006000000000001AAA'));
    assert.equal(result.reason, 'Salesforce page shape changed');
  });

  await t.test('virtualized columns merge by full opportunity identity', async () => {
    const html = fixture().replace('<table', '<div id="scroller" style="width:200px;overflow:auto"><table')
      .replace('</table>', '</table><div style="width:750px;height:1px"></div></div>');
    const result = await run(html, `(() => {
      const grid = document.querySelector('table');
      const head = grid.querySelector('thead tr'), body = grid.querySelector('tbody');
      const headings = [...head.children].map(node => node.outerHTML);
      const values = [...body.querySelector('tr').children].map(node => node.outerHTML);
      const render = right => {
        const start = right ? 4 : 0, end = right ? 8 : 4;
        head.innerHTML = headings.slice(start, end).join('');
        body.innerHTML = '<tr data-row-key-value="006000000000001AAA">' + values.slice(start, end).join('') + '</tr>';
      };
      render(false);
      document.querySelector('#scroller').addEventListener('scroll', event => render(event.target.scrollLeft > 100));
      return true;
    })()`);
    assert.equal(result.state, 'complete');
    assert.equal(result.rows[0].owner, 'Joe Bookout');
    assert.equal(result.rows[0].name, 'Synthetic office');
  });

});
