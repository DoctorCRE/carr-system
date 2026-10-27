// Self-test of the parity harness in Chromium against a synthetic target page.
// No template exists before slice S10, so the target is a small page rendered
// here from the synthetic fixture's values with the template's neutral names.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { runParity } from '../portal-parity/harness.mjs';

const ROOT = new URL('../../', import.meta.url);
const PROBES = new URL('client-portal/parity/probes.v1.json', ROOT).pathname;
const PKG = JSON.parse(await readFile(new URL('client-portal/fixtures/example-health.market-study.json', ROOT), 'utf8'));
const ROWS = 'V-G01,V-S01,V-S03,V-S10,V-T01,V-L01..V-L08,V-L13,V-A10';
const LOGO = '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="#002f6c"/></svg>';
const esc = text => String(text).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function targetPage(pkg, { extraAction = '' } = {}) {
  const views = { home: pkg.market.home_title, purchases: 'Purchase Options', tour: 'Tour List', demographics: 'Local Demographics', sources: 'Sources' };
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${esc(pkg.client.display_name)}</title>
<style>body{margin:0;font:16px sans-serif}.scroll-progress{height:4px;background:#f57f29}.site-header{display:flex;gap:24px;padding:12px}
.site-nav{display:flex;gap:12px}.home-actions{padding:16px}.button{display:inline-block;padding:8px 14px;background:#002f6c;color:#fff}</style></head>
<body><div class="scroll-progress" aria-hidden="true"></div><a class="skip-link" href="#main-content">Skip to content</a>
<div class="site-header"><div class="brand-link"><a class="carr-link" href="https://carr.us/" target="_blank" rel="noopener" aria-label="CARR website, opens in a new tab"><img class="brand-logo" src="carr-logo.svg" alt="CARR"></a>
<a class="client-brand-link" href="#home"><span class="brand-wordmark client-logo-fallback"><strong>${esc(pkg.client.wordmark.line1)}</strong> <small>${esc(pkg.client.wordmark.line2)}</small></span></a></div>
<nav class="site-nav" aria-label="Main navigation">${Object.keys(views).map(v => `<a href="#${v}">${v}</a>`).join('')}</nav></div>
<main id="main-content"><h1 id="sticky-page-title" aria-live="polite"></h1>
<div id="home" class="site-view"><h2>${esc(pkg.market.headline)}</h2><p>${esc(pkg.market.intro)}</p>
<div class="home-actions"><a class="button" href="#purchases">Review Purchase Options</a>${extraAction}</div></div>
${Object.keys(views).slice(1).map(v => `<div id="${v}" class="site-view" hidden><p>${esc(v)}</p></div>`).join('')}</main>
<script>const titles=${JSON.stringify(views)};function show(){const key=(location.hash||'#home').slice(1);const target=document.getElementById(key)||document.getElementById('home');
document.querySelectorAll('.site-view').forEach(v=>v.hidden=v!==target);document.querySelectorAll('.site-nav a').forEach(a=>a.setAttribute('aria-current',a.hash==='#'+target.id?'page':'false'));
document.getElementById('sticky-page-title').textContent=titles[target.id];}addEventListener('hashchange',show);show();</script></body></html>`;
}

// Serves one target folder at /portal/, with the bare-path redirect the gate rows expect.
async function serve(t, html) {
  const dir = await mkdtemp(path.join(tmpdir(), 'parity-target-'));
  await writeFile(path.join(dir, 'index.html'), html);
  await writeFile(path.join(dir, 'carr-logo.svg'), LOGO);
  const types = { html: 'text/html; charset=utf-8', svg: 'image/svg+xml' };
  const server = createServer(async (request, response) => {
    const { pathname } = new URL(request.url, 'http://x');
    if (pathname === '/portal') { response.writeHead(301, { Location: '/portal/' }).end(); return; }
    const name = pathname.replace(/^\/portal\//, '') || 'index.html';
    try {
      const body = await readFile(path.join(dir, path.basename(name)));
      response.writeHead(200, { 'Content-Type': types[name.split('.').at(-1)] ?? 'application/octet-stream' }).end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  return `http://127.0.0.1:${server.address().port}/portal/`;
}

const run = (reference, candidate, rows = ROWS) => runParity({ stage: 2, site: 'REF-V', probesFile: PROBES, reference, candidate, rows });

test('the synthetic target served twice passes every selected probe', async t => {
  const page = targetPage(PKG);
  const result = await run(await serve(t, page), await serve(t, page));
  assert.deepEqual(result.results.filter(r => r.verdict !== 'PASS'), []);
  assert.equal(result.results.length, 15);
  assert.ok(result.ok);
  assert.match(result.lines.at(-1), /^REF-V stage 2: rows=15 PASS=15 ALLOWED=0 FAIL=0 UNRUN=0$/);
});

test('one deliberate DOM change fails exactly that row', async t => {
  const reference = await serve(t, targetPage(PKG));
  const candidate = await serve(t, targetPage(PKG, { extraAction: '<p>Changed call to action note</p>' }));
  const result = await run(reference, candidate);
  assert.deepEqual(result.results.filter(r => r.verdict !== 'PASS').map(r => [r.id, r.verdict]), [['V-S10', 'FAIL']]);
  assert.ok(result.results.find(r => r.id === 'V-S10').aspects.includes('text'));
  assert.equal(result.ok, false);
});

test('--rows runs only the named rows', async t => {
  const page = targetPage(PKG);
  const result = await run(await serve(t, page), await serve(t, page), 'V-L04..V-L06,V-A10');
  assert.deepEqual(result.results.map(r => r.id), ['V-L04', 'V-L05', 'V-L06', 'V-A10']);
});

test('a probe whose target the reference lacks fails instead of comparing two absences', async t => {
  const page = targetPage(PKG);
  const result = await run(await serve(t, page), await serve(t, page), 'V-S13');
  assert.deepEqual(result.results, [{ id: 'V-S13', verdict: 'FAIL', aspects: ['reference-lacks-target'] }]);
});
