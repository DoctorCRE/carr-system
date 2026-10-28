import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { migrationNames, readMigration } from './migration-source.testhelper.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('dream applies a synthetic memory consolidation in one doctrine change set', async () => {
  const bin = [process.env.CARR_LOCAL_PG_BIN_DIR, '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => p && existsSync(`${p}/initdb`));
  assert.ok(bin, 'PostgreSQL is required');
  const fixture = await acquireDisposablePostgres({ prefix: 'dream-p2-', pgCtl: `${bin}/pg_ctl` });
  const directory = mkdtempSync(join(tmpdir(), 'dream-p2-output-'));
  let c;
  try {
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    c = new pg.Client({ host: fixture.root, user: 'carr_ci', database: 'carr_ci' });
    await c.connect();
    await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
    await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', '-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname]);
    const applied = new Set((await c.query('select filename from schema_migrations')).rows.map(r => r.filename));
    for (const name of migrationNames().filter(name => !applied.has(name)).sort()) await c.query(readMigration(name));
    const actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true, client_id: 'synthetic-client' };
    async function call(name, args) {
      await c.query('begin');
      try {
        await c.query('set local role carr_writer');
        const result = await executeRegisteredTool(c, actor, name, name === 'dry-run-doctrine-gates' || name === 'read-doctrine' ? args : { idempotency_key: randomUUID(), ...args });
        await c.query('commit');
        return result;
      } catch (error) { await c.query('rollback'); throw error; }
    }
    const { attach_id } = await call('attach-memory', { surface: 'dream', access: 'read_write', host: 'synthetic' });
    for (const key of ['duplicate/0', 'duplicate/1', 'duplicate/2', 'duplicate/3', 'duplicate/4', 'stale', 'confirmed']) {
      await call('write-doctrine-section', { document: 'team-memory', attach_id, section_key: key, title: key, body_text: key === 'stale' ? 'old script bin/fleet-sync.sh' : 'synthetic lesson', base_version: 0 });
    }
    await c.query("update doctrine_section set review_after='2000-01-01' where section_key='confirmed'");
    const before = await call('read-doctrine', { document: 'team-memory' });
    const child = spawn('python3', [new URL('../../ops/fixtures/dream/postgres_driver.py', import.meta.url).pathname, attach_id, join(directory, 'mem')], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stderr = '';
    child.stderr.setEncoding('utf8'); child.stderr.on('data', chunk => { stderr += chunk; });
    const finished = new Promise((resolve, reject) => { child.on('error', reject); child.on('close', code => code === 0 ? resolve() : reject(new Error(`dream fixture exited ${code}: ${stderr}`))); });
    let changes = 0, report;
    for await (const line of createInterface({ input: child.stdout })) {
      const request = JSON.parse(line);
      if (request.done) { report = request.done; continue; }
      try {
        if (request.verb === 'change-doctrine-sections') {
          const current = await call('read-doctrine', { document: 'team-memory' });
          if (changes === 0) assert.deepEqual(current.sections, before.sections, 'input remains unchanged before apply');
          if (changes++ === 0) {
            const target = current.sections.find(s => s.section_key === 'duplicate/1');
            await call('write-doctrine-section', { document: 'team-memory', attach_id, section_key: target.section_key, body_text: 'concurrent edit', base_version: target.version });
          }
        }
        child.stdin.write(JSON.stringify(await call(request.verb, request.args)) + '\n');
      } catch (error) { child.stdin.write(JSON.stringify(error.payload || { error: String(error) }) + '\n'); }
    }
    await finished;
    assert.equal(changes, 2);
    assert.equal(report.changed, 5);
    assert.deepEqual(report.dropped, [{ sections: ['duplicate/1'], sessions: ['synthetic-a'], note: 'duplicate' }]);
    const log = await call('read-doctrine', { document: 'dreams-log' });
    assert.deepEqual(JSON.parse(log.sections[0].body.text), report);
    const after = await call('read-doctrine', { document: 'team-memory' });
    assert.deepEqual(after.sections.filter(s => s.section_key.startsWith('duplicate/')).map(s => s.section_key), ['duplicate/0', 'duplicate/1']);
    assert.equal(after.sections.some(s => s.section_key === 'stale'), false);
    assert.match(after.sections.find(s => s.section_key === 'confirmed').body.text, /Verified from session synthetic-a/);
    const row = (await c.query("select review_after from doctrine_section where section_key='confirmed'")).rows[0];
    assert.ok(row.review_after > new Date('2000-01-01'));
    const sets = await c.query("select count(*)::int as n from doctrine_change_set where idempotency_key ~ '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'");
    assert.equal(sets.rows[0].n, 1);
  } finally { await c?.end(); await fixture.close(); rmSync(directory, { recursive: true, force: true }); }
});
