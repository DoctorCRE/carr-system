// Tests the parity harness's row selection, probe file, Stage 1 and Stage 0 without a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, readFile, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { parityLaunchOptions } from '../portal-parity/browser.mjs';
import { runParity } from '../portal-parity/harness.mjs';
import { loadProbes, schemaHasField } from '../portal-parity/probes.mjs';
import { selectRows } from '../portal-parity/rows.mjs';

const ROOT = new URL('../../', import.meta.url);
const PROBES = new URL('client-portal/parity/probes.v1.json', ROOT).pathname;
const FIXTURE = new URL('client-portal/fixtures/example-health.market-study.json', ROOT).pathname;
const DOC = await readFile(new URL('docs/client-portal/standard-site-design.md', ROOT), 'utf8');
const SCHEMA = JSON.parse(await readFile(new URL('client-portal/market-study.v1.schema.json', ROOT), 'utf8'));
const INVENTORY = [...DOC.matchAll(/^\| ([AV]-[GSTLA]\d{2}) \|[^|]*\| (T\+D|T|D) \|/gm)].map(([, id, td]) => ({ id, td }));

function expandIds(text) {
  return text.split(',').flatMap(part => {
    const ids = [...part.matchAll(/[AV]-[GSTLA]\d{2}/g)].map(m => m[0]);
    if (!/ to /.test(part)) return ids;
    const [from, to] = ids;
    return INVENTORY.map(row => row.id).filter(id => id.slice(0, 3) === from.slice(0, 3) && id >= from && id <= to);
  });
}

test('the harness browser is headless and off the login keychain unless --headed is passed', () => {
  const defaults = parityLaunchOptions('/chrome');
  assert.equal(defaults.headless, true);
  assert.ok(defaults.args.includes('--use-mock-keychain'));
  assert.ok(defaults.args.includes('--password-store=basic'));
  const headed = parityLaunchOptions('/chrome', { headed: true });
  assert.equal(headed.headless, false);
  assert.deepEqual(headed.args, defaults.args);
});

test('the inventory in the design doc has 456 rows: 281 REF-A and 175 REF-V', () => {
  assert.equal(INVENTORY.length, 456);
  assert.equal(INVENTORY.filter(row => row.id.startsWith('A-')).length, 281);
});

test('--rows takes IDs and ranges, keeps inventory order, and refuses unknown or cross-group ranges', () => {
  const ids = INVENTORY.map(row => row.id);
  assert.deepEqual(selectRows('A-T42,A-T40..A-T41,V-L01', ids), ['A-T40', 'A-T41', 'A-T42', 'V-L01']);
  assert.equal(selectRows('A-T40..A-T56,A-S16..A-S25', ids).length, 27);
  assert.equal(selectRows(undefined, ids).length, 456);
  assert.throws(() => selectRows('A-T99', ids), /unknown inventory ID: A-T99/);
  assert.throws(() => selectRows('A-T01..A-S03', ids), /bad row range/);
  assert.throws(() => selectRows('A-T05..A-T01', ids), /bad row range/);
  assert.throws(() => selectRows(' , ', ids), /names no rows/);
});

test("the probe file's IDs and T/D marks equal the design doc's inventory, in order", async () => {
  const doc = await loadProbes(PROBES);
  assert.deepEqual(doc.probes.map(p => [p.id, p.td]), INVENTORY.map(row => [row.id, row.td]));
});

test('every D and T+D row maps to package fields the schema declares', async () => {
  const doc = await loadProbes(PROBES);
  for (const probe of doc.probes) {
    if (probe.td !== 'T' && !probe.retired) assert.ok(probe.fields?.length, probe.id);
    for (const field of probe.fields ?? []) assert.ok(schemaHasField(SCHEMA, field), `${probe.id} ${field}`);
  }
});

test("allowed differences in the probe file equal the doc's declared table", async () => {
  const doc = await loadProbes(PROBES);
  const table = DOC.slice(DOC.indexOf('### Declared allowed differences'), DOC.indexOf('## Migration that keeps'));
  const declared = {};
  for (const [, ad, rows] of table.matchAll(/^\| (AD\d) \| [^|]+ \| ([^|]+) \|/gm)) {
    for (const id of expandIds(rows.replace(/and the body text of[^|]*/, ''))) (declared[id] ??= []).push(ad);
  }
  const probeAllowed = Object.fromEntries(doc.probes.filter(p => p.allowed || p.retired)
    .map(p => [p.id, [...(p.allowed ?? []), ...(p.retired ? [p.retired] : [])]]));
  assert.deepEqual(probeAllowed, declared);
});

test('Stage 1 passes rows whose fields the package maps and fails a row whose field is absent', async () => {
  const rows = 'A-S02..A-S09,A-S47,V-S06';
  const full = await runParity({ stage: 1, probesFile: PROBES, packageFile: FIXTURE, rows });
  assert.deepEqual(full.results.map(r => r.verdict), Array(full.results.length).fill('PASS'));
  assert.ok(full.ok);
  const pkg = JSON.parse(await readFile(FIXTURE, 'utf8'));
  delete pkg.client.current_practice;
  const dir = await mkdtemp(path.join(tmpdir(), 'parity-stage1-'));
  await writeFile(path.join(dir, 'package.json'), JSON.stringify(pkg));
  const partial = await runParity({ stage: 1, probesFile: PROBES, packageFile: path.join(dir, 'package.json'), rows });
  assert.deepEqual(partial.results.filter(r => r.verdict === 'FAIL').map(r => r.id), ['A-S47']);
  assert.match(partial.lines.join('\n'), /^A-S47 FAIL unmapped:\/client\/current_practice\/address/m);
  assert.equal(partial.ok, false);
});

// A gated site in the shape of the reference gates: bare redirect, password
// form, session cookie, and released files.
async function gatedSite(files) {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://x');
    if (url.pathname === '/portal') { response.writeHead(301, { Location: '/portal/' }).end(); return; }
    if (!url.pathname.startsWith('/portal/')) { response.writeHead(404).end(); return; }
    if (request.method === 'POST') {
      let body = ''; for await (const chunk of request) body += chunk;
      if (new URLSearchParams(body).get('password') !== 'example-health') { response.writeHead(401, { 'Content-Type': 'text/html' }).end('no'); return; }
      response.writeHead(303, { Location: '/portal/', 'Set-Cookie': '__Host-portal-example-health=ok; Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax' }).end();
      return;
    }
    if (request.headers.cookie !== '__Host-portal-example-health=ok') {
      response.writeHead(401, { 'Content-Type': 'text/html' }).end(request.method === 'HEAD' ? undefined : 'sign in');
      return;
    }
    const name = url.pathname.slice('/portal/'.length) || 'index.html';
    if (!(name in files)) { response.writeHead(404).end(); return; }
    response.writeHead(200, { 'Content-Type': 'text/plain' }).end(files[name]);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}/portal/` };
}

test('Stage 0 hashes every source file against the live site and records the gate baseline', async t => {
  const source = await mkdtemp(path.join(tmpdir(), 'parity-source-'));
  await mkdir(path.join(source, 'media'));
  await writeFile(path.join(source, 'index.html'), 'home');
  await writeFile(path.join(source, 'media', 'logo.svg'), 'logo');
  await writeFile(path.join(source, 'notes.txt'), 'changed since packing');
  await writeFile(path.join(source, 'unserved.json'), '{}');
  const { server, base } = await gatedSite({ 'index.html': 'home', 'media/logo.svg': 'logo', 'notes.txt': 'packed' });
  t.after(() => server.close());
  const out = path.join(source, 'baseline.json');
  const run = await runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: source, baselineOut: out, rows: 'V-G01,V-G02,V-G11,V-G16' });
  const files = run.lines.filter(line => line.startsWith('FILE '));
  assert.equal(files.length, 4);
  assert.deepEqual(files.map(line => line.split(' ')[2]).sort(), ['FAIL', 'PASS', 'PASS', 'UNSERVED']);
  assert.match(run.lines.join('\n'), /^files=4 FAIL=1 UNSERVED=1$/m);
  assert.equal(run.ok, false, 'a file that differs from the live site fails Stage 0');
  assert.ok(files.every(line => !/home|logo|notes|unserved/.test(line)), 'lines carry ordinals and hashes only');
  const baseline = JSON.parse(await readFile(out, 'utf8'));
  assert.deepEqual(Object.keys(baseline).sort(), ['V-G01', 'V-G02', 'V-G16']);
  assert.equal(baseline['V-G01'].facts['r0.status'], 301);
  assert.equal(baseline['V-G01'].facts['r0.location'], '');
  assert.equal(baseline['V-G16'].facts['r0.empty'], true);
  assert.deepEqual(run.results.find(r => r.id === 'V-G11'), { id: 'V-G11', verdict: 'UNRUN', reason: 'not-a-stage-0-probe' });
});

test('Stage 2 gate rows compare against the recorded baseline; retired and fault rows never pass silently', async t => {
  const { server, base } = await gatedSite({ 'index.html': 'home', 'styles.css': 'css' });
  t.after(() => server.close());
  const same = await runParity({ stage: 2, site: 'REF-A', probesFile: PROBES, reference: base, candidate: base,
    password: 'example-health', rows: 'A-G01,A-G02,A-G04,A-G14,A-G21' });
  assert.deepEqual(same.results.map(r => r.verdict), ['ALLOWED', 'PASS', 'PASS', 'PASS', 'PASS']);
  assert.match(same.lines.join('\n'), /^A-G01 ALLOWED AD8 retired$/m);
  const fault = await runParity({ stage: 2, site: 'REF-A', probesFile: PROBES, reference: base, candidate: base, rows: 'A-G15' });
  assert.deepEqual(fault.results, [{ id: 'A-G15', verdict: 'UNRUN', reason: 'fault:secrets-unconfigured' }]);
  assert.equal(fault.ok, false);
  const dir = await mkdtemp(path.join(tmpdir(), 'parity-baseline-'));
  const baselineFile = path.join(dir, 'baseline.json');
  await writeFile(baselineFile, JSON.stringify({ 'A-G14': { facts: { 'r0.status': 410 } } }));
  const drift = await runParity({ stage: 2, site: 'REF-A', probesFile: PROBES, reference: base, candidate: base, baselineFile, rows: 'A-G14' });
  assert.equal(drift.results[0].verdict, 'FAIL');
});
