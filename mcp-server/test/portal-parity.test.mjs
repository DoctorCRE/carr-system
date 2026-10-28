// Tests the parity harness's row selection, probe file, Stage 1 and Stage 0 without a browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtemp, readFile, writeFile, mkdir, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { parityLaunchOptions } from '../portal-parity/browser.mjs';
import { runParity } from '../portal-parity/harness.mjs';
import { FILE_FETCH_PACE_MS } from '../portal-parity/http.mjs';
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
async function gatedSite(files, { signInStatus = 303, cookieAttributes = 'Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax', statusFor = {}, log = [] } = {}) {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, 'http://x');
    log.push({ method: request.method, path: url.pathname, at: performance.now() });
    if (statusFor[url.pathname]) { response.writeHead(statusFor[url.pathname], { 'Retry-After': '60' }).end(); return; }
    if (url.pathname === '/portal') { response.writeHead(301, { Location: '/portal/' }).end(); return; }
    if (!url.pathname.startsWith('/portal/')) { response.writeHead(404).end(); return; }
    if (request.method === 'POST') {
      let body = ''; for await (const chunk of request) body += chunk;
      if (new URLSearchParams(body).get('password') !== 'example-health') { response.writeHead(401, { 'Content-Type': 'text/html' }).end('no'); return; }
      if (signInStatus !== 303) { response.writeHead(signInStatus).end(); return; }
      response.writeHead(303, { Location: '/portal/', 'Set-Cookie': `__Host-portal-example-health=ok; ${cookieAttributes}` }).end();
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

// The Worker's generated route manifest: a module whose default export is a Map
// of served path to encrypted blob. Stage 0 reads it as data, never imports it.
const manifestModule = paths => `// Generated by tools/pack.mjs. All served assets are encrypted.\nexport default new Map(${
  JSON.stringify(paths.map((p, i) => [p, { asset: `site/${i}.bin`, type: 'text/plain' }]), null, 1)});\n`;

async function stageZeroSource(files, manifestPaths) {
  const source = await mkdtemp(path.join(tmpdir(), 'parity-source-'));
  for (const [name, body] of Object.entries(files)) {
    await mkdir(path.dirname(path.join(source, name)), { recursive: true });
    await writeFile(path.join(source, name), body);
  }
  const out = await mkdtemp(path.join(tmpdir(), 'parity-out-'));
  const manifest = path.join(out, 'files.js');
  await writeFile(manifest, manifestModule(manifestPaths));
  return { source, manifest, out };
}

test('Stage 0 hashes every manifest path against its source file and records the gate baseline', async t => {
  const { source, manifest, out } = await stageZeroSource(
    { 'index.html': 'home', 'media/logo.svg': 'logo', 'notes.txt': 'changed since packing', 'unserved.json': '{}' },
    ['/portal/index.html', '/portal/', '/portal/media/logo.svg', '/portal/notes.txt']);
  const { server, base } = await gatedSite({ 'index.html': 'home', 'media/logo.svg': 'logo', 'notes.txt': 'packed' });
  t.after(() => server.close());
  const baselineOut = path.join(out, 'baseline.json'), report = path.join(out, 'report.json');
  const run = await runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: source, manifest, baselineOut, report, rows: 'V-G01,V-G02,V-G11,V-G16' });
  const files = run.lines.filter(line => line.startsWith('FILE '));
  assert.deepEqual(files.map(line => line.split(' ')[2]), ['PASS', 'PASS', 'PASS', 'FAIL', 'UNSERVED'],
    'manifest paths in manifest order (the directory route included), then the source file the manifest omits');
  assert.match(run.lines.join('\n'), /^files=5 FAIL=1 UNSERVED=1$/m);
  assert.equal(run.ok, false, 'a file that differs from the live site fails Stage 0');
  assert.ok(files.every(line => !/home|logo|notes|unserved|portal/.test(line)), 'lines carry ordinals and hashes only');
  const baseline = JSON.parse(await readFile(baselineOut, 'utf8'));
  assert.deepEqual(Object.keys(baseline).sort(), ['V-G01', 'V-G02', 'V-G16']);
  assert.equal(baseline['V-G01'].facts['r0.status'], 301);
  assert.equal(baseline['V-G01'].facts['r0.location'], '');
  assert.equal(baseline['V-G16'].facts['r0.empty'], true);
  assert.deepEqual(run.results.find(r => r.id === 'V-G11'), { id: 'V-G11', verdict: 'UNRUN', reason: 'not-a-stage-0-probe' });
  const posted = JSON.parse(await readFile(report, 'utf8'));
  assert.deepEqual(posted.files.map(f => f.verdict), ['PASS', 'PASS', 'PASS', 'FAIL', 'UNSERVED'], 'the report carries the file verdicts for the Work Request');
  assert.deepEqual(Object.keys(posted.files[0]).sort(), ['live', 'ordinal', 'source', 'verdict'], 'file entries hold ordinals, verdicts and hashes only');
});

test('Stage 0 fails a manifest path with no source file and a source file the site serves outside the manifest', async t => {
  const { source, manifest } = await stageZeroSource({ 'index.html': 'home', 'extra.txt': 'extra' }, ['/portal/index.html', '/portal/gone.txt']);
  const { server, base } = await gatedSite({ 'index.html': 'home', 'gone.txt': 'gone', 'extra.txt': 'extra' });
  t.after(() => server.close());
  const run = await runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: source, manifest, rows: 'V-G01' });
  const files = run.lines.filter(line => line.startsWith('FILE '));
  assert.deepEqual(files.map(line => line.split(' ')[2]), ['PASS', 'FAIL', 'FAIL']);
  assert.match(files[1], /source=missing /);
  assert.match(run.lines.join('\n'), /^files=3 FAIL=2 UNSERVED=0$/m);
  assert.equal(run.ok, false);
});

test('Stage 0 refuses a manifest path outside the site, a file that is not a route manifest, and a run without one', async t => {
  const { server, base } = await gatedSite({ 'index.html': 'home' });
  t.after(() => server.close());
  const outside = await stageZeroSource({ 'index.html': 'home' }, ['/other-site/index.html']);
  const stage0 = manifest => runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: outside.source, manifest, rows: 'V-G01' });
  await assert.rejects(stage0(outside.manifest), /manifest path 1 is outside the reference/);
  const notManifest = path.join(outside.out, 'not-a-manifest.js');
  await writeFile(notManifest, 'export default 1;\n');
  await assert.rejects(stage0(notManifest), /not a route manifest/);
  await assert.rejects(stage0(undefined), /stage 0 needs .*--manifest/);
});

test('Stage 0 counts a source file outside the manifest as UNSERVED only on a 404; a 429 or 403 fails', async t => {
  const { source, manifest } = await stageZeroSource({ 'index.html': 'home', 'limited.txt': 'a', 'forbidden.txt': 'b', 'absent.txt': 'c' },
    ['/portal/index.html']);
  const { server, base } = await gatedSite({ 'index.html': 'home' },
    { statusFor: { '/portal/limited.txt': 429, '/portal/forbidden.txt': 403 } });
  t.after(() => server.close());
  const run = await runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: source, manifest, rows: 'V-G01' });
  const files = run.lines.filter(line => line.startsWith('FILE ')).map(line => [line.split(' ')[2], line.split('live=')[1]]);
  assert.deepEqual(files.slice(1).sort(), [['FAIL', 'status-403'], ['FAIL', 'status-429'], ['UNSERVED', 'status-404']]);
  assert.equal(run.ok, false);
});

test('Stage 0 writes no baseline when any row is rate-limited, so a good baseline is never overwritten', async t => {
  const { source, manifest, out } = await stageZeroSource({ 'index.html': 'home' }, ['/portal/index.html']);
  const { server, base } = await gatedSite({ 'index.html': 'home' }, { statusFor: { '/portal/parity-probe-missing.txt': 429 } });
  t.after(() => server.close());
  const baselineOut = path.join(out, 'baseline.json');
  await writeFile(baselineOut, '{"earlier":"full baseline"}\n');
  const run = await runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: source, manifest, baselineOut, rows: 'V-G01,V-G10' });
  assert.deepEqual(run.results.map(r => [r.id, r.verdict]), [['V-G01', 'PASS'], ['V-G10', 'FAIL']]);
  assert.match(run.lines.join('\n'), /^V-G10 FAIL rate-limited$/m);
  assert.match(run.lines.join('\n'), /^baseline not written: rate-limited rows; run again after a minute$/m);
  assert.equal(await readFile(baselineOut, 'utf8'), '{"earlier":"full baseline"}\n');
  assert.equal(run.ok, false);
});

test('Stage 0 paces its file fetches so they cannot trip the burst limit', async t => {
  const { source, manifest } = await stageZeroSource({ 'index.html': 'home', 'a.txt': 'a', 'b.txt': 'b', 'c.txt': 'c' },
    ['/portal/index.html', '/portal/a.txt', '/portal/b.txt', '/portal/c.txt']);
  const log = [];
  const { server, base } = await gatedSite({ 'index.html': 'home', 'a.txt': 'a', 'b.txt': 'b', 'c.txt': 'c' }, { log });
  t.after(() => server.close());
  await runParity({ stage: 0, site: 'REF-V', probesFile: PROBES, reference: base, password: 'example-health',
    sourceDir: source, manifest, rows: 'V-G01' });
  const fetches = log.filter(entry => /\/(index\.html|[abc]\.txt)$/.test(entry.path)).map(entry => entry.at);
  assert.equal(fetches.length, 4);
  const gaps = fetches.slice(1).map((at, i) => at - fetches[i]);
  assert.ok(gaps.every(gap => gap >= FILE_FETCH_PACE_MS - 5), `gaps ${gaps.map(Math.round)} ms`);
});

// The doc: "A wrong-password probe counts toward nothing today ... and the run
// sends fewer than five." Counted here: every Stage 0 request that can reach the
// password check: a same-origin POST to the sign-in page (the site root or its
// index.html, both checked by the live gates) that is not the real sign-in.
test('Stage 0 sends fewer than five wrong-password attempts per site', async () => {
  const doc = await loadProbes(PROBES);
  for (const site of ['A', 'V']) {
    const attempts = doc.probes.filter(p => p.id.startsWith(`${site}-`) && p.kind === 'http' && p.stage0 && !p.retired && !p.fault)
      .flatMap(p => p.requests)
      .filter(r => r.method === 'POST' && ['', 'index.html'].includes(r.path) && !r.sign_in && r.headers?.Origin !== '@cross-origin');
    assert.ok(attempts.length > 0 && attempts.length < 5, `${site}: ${attempts.length}`);
  }
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
  const dir = await mkdtemp(path.join(tmpdir(), 'parity-baseline-'));
  const baselineFile = path.join(dir, 'baseline.json');
  await writeFile(baselineFile, JSON.stringify({ 'A-G14': { facts: { 'r0.status': 410 } } }));
  const drift = await runParity({ stage: 2, site: 'REF-A', probesFile: PROBES, reference: base, candidate: base, baselineFile, rows: 'A-G14' });
  assert.equal(drift.results[0].verdict, 'FAIL');
});

test('an allowed difference excuses only the aspects it declares: a 500 sign-in fails AD2', async t => {
  const reference = await gatedSite({ 'index.html': 'home' });
  const broken = await gatedSite({ 'index.html': 'home' }, { signInStatus: 500 });
  const renamed = await gatedSite({ 'index.html': 'home' }, { cookieAttributes: 'Path=/; Max-Age=600; HttpOnly; Secure; SameSite=Strict' });
  t.after(() => { reference.server.close(); broken.server.close(); renamed.server.close(); });
  const run = candidate => runParity({ stage: 2, site: 'REF-A', probesFile: PROBES, reference: reference.base, candidate,
    password: 'example-health', rows: 'A-G04' });
  const failed = await run(broken.base);
  assert.equal(failed.results[0].verdict, 'FAIL');
  assert.ok(failed.results[0].aspects.includes('r0.status'));
  assert.match(failed.lines[0], /^A-G04 FAIL .*r0\.status/);
  const cookieOnly = await run(renamed.base);
  assert.deepEqual([cookieOnly.results[0].verdict, cookieOnly.results[0].allowed], ['ALLOWED', ['AD2']]);
});

// A candidate that answers 500 to everything, the sign-in included.
async function brokenSite() {
  const server = createServer((request, response) => { response.writeHead(500).end('boom'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}/portal/` };
}

test('with a password, a side that refuses sign-in fails its session rows instead of leaving them UNRUN', async t => {
  const reference = await gatedSite({ 'index.html': 'home' });
  const broken = await brokenSite();
  t.after(() => { reference.server.close(); broken.server.close(); });
  const run = (ref, candidate, password) => runParity({ stage: 2, site: 'REF-V', probesFile: PROBES, reference: ref, candidate, password, rows: 'V-G08' });
  const candidateRefused = await run(reference.base, broken.base, 'example-health');
  assert.deepEqual(candidateRefused.results, [{ id: 'V-G08', verdict: 'FAIL', reason: 'candidate-sign-in-refused', aspects: ['candidate-sign-in-status-500'] }]);
  assert.match(candidateRefused.lines[0], /^V-G08 FAIL candidate-sign-in-refused candidate-sign-in-status-500$/);
  assert.equal(candidateRefused.ok, false, 'a slice with a refused sign-in fails');
  const referenceRefused = await run(broken.base, reference.base, 'example-health');
  assert.equal(referenceRefused.results[0].verdict, 'FAIL');
  assert.equal(referenceRefused.results[0].reason, 'reference-sign-in-refused');
  assert.equal(referenceRefused.ok, false);
  const noPassword = await run(reference.base, broken.base, undefined);
  assert.deepEqual(noPassword.results, [{ id: 'V-G08', verdict: 'UNRUN', reason: 'needs-session' }]);
  assert.equal(noPassword.ok, true, 'without a password, session rows stay UNRUN and a slice passes');
});

const CLI = new URL('../portal-parity/cli.mjs', import.meta.url).pathname;

test('the CLI refuses run-data paths inside the repository, through a symlink too, and names the flag', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'parity-cli-'));
  const link = path.join(dir, 'repo-link');
  await symlink(new URL('.', ROOT).pathname, link);
  const cli = (...args) => spawnSync(process.execPath, [CLI, '--stage', '1', '--probes', PROBES, '--package', FIXTURE, '--rows', 'V-S06', ...args], { encoding: 'utf8' });
  const viaLink = cli('--report', path.join(link, 'out', 'report.json'));
  assert.equal(viaLink.status, 2);
  assert.match(viaLink.stderr, /--report must point outside the repository/);
  const outside = cli('--report', path.join(dir, 'report.json'));
  assert.equal(outside.status, 0, outside.stderr);
  const aliases = cli('--candidate-aliases', path.join(link, 'aliases.json'));
  assert.match(aliases.stderr, /--candidate-aliases must point outside the repository/);
});

// A two-row probe file, so the full-run rule can be shown without 456 rows.
async function miniProbes() {
  const doc = JSON.parse(await readFile(PROBES, 'utf8'));
  doc.probes = doc.probes.filter(p => ['A-G14', 'A-G15'].includes(p.id));
  const dir = await mkdtemp(path.join(tmpdir(), 'parity-mini-'));
  await writeFile(path.join(dir, 'probes.json'), JSON.stringify(doc));
  return path.join(dir, 'probes.json');
}

test('exit rule follows the doc: a --rows slice passes on FAIL = 0; a full run must account for every row', async t => {
  const { server, base } = await gatedSite({ 'index.html': 'home' });
  t.after(() => server.close());
  const probesFile = await miniProbes();
  const slice = await runParity({ stage: 2, probesFile, reference: base, candidate: base, rows: 'A-G14,A-G15' });
  assert.deepEqual(slice.results.map(r => r.verdict), ['PASS', 'UNRUN']);
  assert.equal(slice.ok, true, 'UNRUN rows are reported but do not fail a slice');
  const full = await runParity({ stage: 2, probesFile, reference: base, candidate: base });
  assert.deepEqual(full.results.map(r => r.verdict), ['PASS', 'UNRUN']);
  assert.equal(full.ok, false, 'a full run counts an UNRUN row as not accounted for');
});

test('a replaced row whose feeds lie outside the slice is UNRUN and leaves the slice passing', async () => {
  const run = await runParity({ stage: 2, probesFile: PROBES, reference: 'http://127.0.0.1:9/', candidate: 'http://127.0.0.1:9/',
    packageFile: FIXTURE, rows: 'A-A01' });
  assert.deepEqual(run.results, [{ id: 'A-A01', verdict: 'UNRUN', reason: 'feeds-not-in-run' }]);
  assert.equal(run.ok, true);
});
