// Check a synthetic reference and real migration steps without production access.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { runParity } from './harness.mjs';
import { signIn, sourceFidelity } from './http.mjs';
import { registerCandidate, pendingSteps } from './migration-candidate.mjs';

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

export async function runMigrationFixtures(portals, candidateOptions) {
  const reports = [];
  const dir = await mkdtemp(path.join(tmpdir(), 'portal-migration-fixtures-'));
  try {
    for (const [index, portal] of portals.entries()) {
      const servers = [];
      try {
        const reference = await fixture(portal, portal.freeze);
        servers.push(reference.server);
        const paths = [...portal.files.map(f => f.path), ...portal.aliases.map(a => a.path)];
        const cookie = await signIn(reference.base, portal.password);
        const selectionResponse = await fetch(new URL('api/selection', reference.base), { headers: { Cookie: cookie } });
        assert.equal(selectionResponse.status, 200, 'reference-selection-readable');
        assert.deepEqual(await selectionResponse.json(), portal.freeze, 'reference-selection-fidelity');
        const source = path.join(dir, `source-${index}`);
        const contents = new Map(portal.files.map(file => [file.path, file.content]));
        for (const alias of portal.aliases) contents.set(alias.path, contents.get(alias.target));
        for (const [name, content] of contents) {
          await mkdir(path.dirname(path.join(source, name)), { recursive: true });
          await writeFile(path.join(source, name), content);
        }
        const fidelity = await sourceFidelity(reference.base, source, cookie,
          paths.map(p => new URL(p, reference.base).pathname));
        assert.equal(fidelity.failed, 0, 'released bytes must match the synthetic source');
        if (portal.kind === 'package') {
          const packageFile = path.join(dir, `package-${index}.json`);
          await writeFile(packageFile, JSON.stringify(portal.package.data));
          const stageOne = await runParity({ stage: 1, probesFile: new URL('../../client-portal/parity/probes.v1.json', import.meta.url).pathname,
            packageFile, rows: 'A-S02..A-S09,V-S06' });
          assert.ok(stageOne.ok && stageOne.results.every(r => r.verdict === 'PASS'), 'packages satisfy selected canonical data rows');
        }
        reports.push({ slug: portal.slug, revision: portal.revision, source_selection_version: portal.freeze.version,
          file_hashes: fidelity.files });
      } finally {
        await Promise.all(servers.map(server => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); })));
      }
    }
  } finally { await rm(dir, { recursive: true, force: true }); }
  const registration = await registerCandidate(portals, candidateOptions);
  return { status: 'pending-integration', scope: 'disposable-synthetic-storage',
    reference: { status: 'PASS', scope: 'reference-smoke-only', portals: reports },
    steps: { 'portal-registration': registration, ...structuredClone(pendingSteps) } };
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  runMigrationFixtures(JSON.parse(input)).then(result => {
    console.log(JSON.stringify(result));
    process.exitCode = 3;
  }, error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
