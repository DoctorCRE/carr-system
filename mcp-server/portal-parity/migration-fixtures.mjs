// Disposable loopback fixtures for offline rehearsal, never a production adapter.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { runParity } from './harness.mjs';
import { signIn, sourceFidelity } from './http.mjs';

async function fixture(portal, selection) {
  selection = structuredClone(selection);
  const files = new Map(portal.files.map(file => [file.path, file.content]));
  for (const alias of portal.aliases) files.set(alias.path, files.get(alias.target));
  const root = `/${portal.slug}/`;
  const cookie = `fixture-${portal.slug}=synthetic`;
  const server = createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    let body = '';
    for await (const chunk of request) {
      body += chunk;
      if (body.length > 65536) { response.writeHead(413).end(); return; }
    }
    if (pathname === root && request.method === 'POST') {
      if (new URLSearchParams(body).get('password') !== portal.password) { response.writeHead(401).end(); return; }
      response.writeHead(303, { 'Set-Cookie': `${cookie}; HttpOnly; SameSite=Lax`, Location: root }).end();
      return;
    }
    if (!pathname.startsWith(root)) { response.writeHead(404).end(); return; }
    if (request.headers.cookie !== cookie) { response.writeHead(401).end(); return; }
    const relative = pathname.slice(root.length);
    response.setHeader('X-Publication-Revision', String(portal.revision));
    if (relative === 'api/selection') {
      response.setHeader('Content-Type', 'application/json');
      if (request.method === 'PUT') {
        let save;
        try { save = JSON.parse(body); } catch { response.writeHead(400).end(); return; }
        if (save.version !== selection.version) { response.writeHead(409).end(JSON.stringify(selection)); return; }
        selection = { ...save, version: selection.version + 1 };
        response.writeHead(200).end(JSON.stringify(selection));
        return;
      }
      response.end(JSON.stringify(selection));
      return;
    }
    if (!files.has(relative)) { response.writeHead(404).end(); return; }
    response.setHeader('Content-Type', 'application/octet-stream');
    response.end(files.get(relative));
  });
  server.requestTimeout = 2000;
  server.headersTimeout = 2000;
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}${root}` };
}

export async function runMigrationFixtures(portals) {
  const reports = [];
  const dir = await mkdtemp(path.join(tmpdir(), 'portal-migration-fixtures-'));
  try {
    for (const [index, portal] of portals.entries()) {
      const servers = [];
      try {
        const reference = await fixture(portal, portal.freeze);
        servers.push(reference.server);
        const candidate = await fixture(portal, portal.imported);
        servers.push(candidate.server);
        const paths = [...portal.files.map(f => f.path), ...portal.aliases.map(a => a.path), 'api/selection'];
        const probes = { schema: 'carr.portal-parity-probes.v1', aliases: {}, allowed_differences: {},
          probes: paths.map((name, i) => ({ id: `A-G${String(i + 1).padStart(2, '0')}`, kind: 'http', td: 'T',
            requests: [{ method: 'GET', path: name, session: true }] })) };
        const probesFile = path.join(dir, `probes-${index}.json`);
        await writeFile(probesFile, JSON.stringify(probes));
        const parity = await runParity({ stage: 2, probesFile, reference: reference.base, candidate: candidate.base, password: portal.password });
        assert.ok(parity.ok && parity.results.every(r => r.verdict === 'PASS'), 'local parity must pass every selected row');
        const cookie = await signIn(candidate.base, portal.password);
        for (const name of paths) {
          const response = await fetch(new URL(name, candidate.base), { headers: { Cookie: cookie } });
          assert.equal(response.status, 200, 'matching errors cannot count as released files');
          assert.equal(response.headers.get('x-publication-revision'), String(portal.revision));
          if (name === 'api/selection') assert.deepEqual(await response.json(), portal.freeze, 'retain versions, retired IDs, and all notes');
        }
        const source = path.join(dir, `source-${index}`);
        const contents = new Map(portal.files.map(file => [file.path, file.content]));
        for (const alias of portal.aliases) contents.set(alias.path, contents.get(alias.target));
        for (const [name, content] of contents) {
          await mkdir(path.dirname(path.join(source, name)), { recursive: true });
          await writeFile(path.join(source, name), content);
        }
        const fidelity = await sourceFidelity(candidate.base, source, cookie,
          paths.filter(p => p !== 'api/selection').map(p => new URL(p, candidate.base).pathname));
        assert.equal(fidelity.failed, 0, 'released bytes must match the synthetic source');
        const stale = await fetch(new URL('api/selection', candidate.base), { method: 'PUT',
          headers: { Cookie: cookie }, body: JSON.stringify({ ...portal.copy, version: portal.imported.version - 1 }) });
        assert.equal(stale.status, 409, 'stale saves must conflict');
        assert.deepEqual(await stale.json(), portal.freeze);
        const save = () => fetch(new URL('api/selection', candidate.base), { method: 'PUT',
          headers: { Cookie: cookie }, body: JSON.stringify(portal.imported) });
        const accepted = await save();
        assert.equal(accepted.status, 200, 'a current version can save');
        assert.deepEqual(await accepted.json(), { ...portal.freeze, version: portal.freeze.version + 1 });
        const replay = await save();
        assert.equal(replay.status, 409, 'replaying the accepted version must conflict');
        assert.deepEqual(await replay.json(), { ...portal.freeze, version: portal.freeze.version + 1 });
        if (portal.kind === 'package') {
          const packageFile = path.join(dir, `package-${index}.json`);
          await writeFile(packageFile, JSON.stringify(portal.package.data));
          const stageOne = await runParity({ stage: 1, probesFile: new URL('../../client-portal/parity/probes.v1.json', import.meta.url).pathname,
            packageFile, rows: 'A-S02..A-S09,V-S06' });
          assert.ok(stageOne.ok && stageOne.results.every(r => r.verdict === 'PASS'), 'packages satisfy selected canonical data rows');
        }
        reports.push({ slug: portal.slug, revision: portal.revision, selection_version: portal.imported.version,
          parity_rows: parity.results.length, file_hashes: fidelity.files, stale_save_status: stale.status });
      } finally {
        await Promise.all(servers.map(server => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); })));
      }
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
  return { status: 'PASS', scope: 'synthetic-loopback-only', portals: reports };
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  runMigrationFixtures(JSON.parse(input)).then(result => console.log(JSON.stringify(result)), error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
