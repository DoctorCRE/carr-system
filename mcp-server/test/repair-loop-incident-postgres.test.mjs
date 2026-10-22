import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
import { acquirePostgresFixtureGroup, acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';

const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
const source = 'repair-loop:jev:availability:v1';
const actors = ['joe', 'dell'].map(slug => ({
  id: randomUUID(), slug, human: false, via: 'local-token', client_id: `incident-test-${slug}`,
}));
const opening = (source_note = source) => ({
  idempotency_key: randomUUID(), kind: 'open_loop', domain: 'system', owner: 'claude',
  body: 'Jev judgments are unavailable. Restore usable judgments.', source_note,
  blocker: 'other_lane', blocker_detail: 'The orchestrator must restore TypeSafe judgments',
});
let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try {
    const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
  } catch { /* Try the next supported PostgreSQL installation. */ }
}

test('Repair incident identity on disposable PostgreSQL', { skip: !bin && 'PostgreSQL unavailable' }, async t => {
  const releaseBudget = await acquirePostgresFixtureGroup();
  let fixture;
  const clients = [];
  try {
    fixture = await acquireDisposablePostgres({ prefix: 'repair-incident-', pgCtl: path.join(bin, 'pg_ctl'), dataName: '.' });
    const dir = fixture.root;
    await fixture.run(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start']);
    for (let i = 0; i < 3; i++) {
      const client = new pg.Client({ host: dir, user: 'fixture', database: 'postgres', statement_timeout: 5000 });
      await client.connect();
      clients.push(client);
    }
    const [first, second, observer] = clients;
    for (const name of ['actor', 'event', 'tool_call', 'loop_item', 'loop_block', 'loop_domain', 'loop_amendment']) {
      const definition = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`));
      assert.ok(definition, `${name} must come from the checked-in schema`);
      await observer.query(definition[0]);
    }
    await restoreEventIdentity(observer, schema);
    await observer.query('alter table tool_call add primary key(idempotency_key)');
    await observer.query(schema.match(/CREATE UNIQUE INDEX loop_item_open_number_unique[^;]+;/)[0]);
    await observer.query(schema.match(/CREATE FUNCTION public\.trg_touch_row\(\)[\s\S]*?end \$\$;/)[0]);
    await observer.query(schema.match(/CREATE TRIGGER loop_item_touch[^;]+;/)[0]);
    await observer.query(schema.match(/CREATE FUNCTION public\.loop_amendment_history\(p_loop_id uuid\)[\s\S]*?\n\$\$;/)[0]);
    for (const actor of actors) {
      await observer.query("insert into actor(id,slug,display_name,kind) values($1,$2,'Synthetic incident writer','automation')", [actor.id, actor.slug]);
    }
    await observer.query("insert into loop_domain(slug,label,sort) values('system','System',1)");
    await observer.query(`insert into loop_block(kind,block_key,rel_path,seq,col_order,created_by,updated_by)
      values('open_loop','backlog','synthetic',1,array[]::text[],$1,$1)`, [actors[0].id]);
    const invoke = async (client, verb, args, actor = actors[0]) => {
      await client.query('begin');
      try {
        const result = await TOOLS[verb].handler(client, actor, args);
        await client.query('commit');
        return result;
      } catch (error) {
        await client.query('rollback');
        throw error;
      }
    };
    const read = args => TOOLS['read-loop'].handler(observer, actors[0], args);
    const close = async (loop_id, resolution) => {
      const { loop } = await read({ loop_id });
      await invoke(first, 'close-loop', {
        idempotency_key: randomUUID(), loop_id, base_version: loop.version, resolution,
        outcome: resolution === 'done' ? 'A usable Jev judgment verified recovery.' : 'The synthetic incident was deliberately abandoned.',
      });
    };
    let incident;
    const original = opening();
    await t.test('overlapping writers with distinct principals converge under the real transaction lock', async () => {
      assert.equal((await read({ source_note: source })).loop, null);
      await first.query('begin');
      let pending;
      try {
        const opened = await TOOLS['add-loop'].handler(first, actors[0], original);
        const firstPid = (await first.query('select pg_backend_pid() pid')).rows[0].pid;
        const secondPid = (await second.query('select pg_backend_pid() pid')).rows[0].pid;
        pending = invoke(second, 'add-loop', opening(), actors[1]);
        pending.catch(() => {});
        let blocked = false;
        const deadline = Date.now() + 3000;
        while (Date.now() < deadline) {
          const row = (await observer.query(`select wait_event, pg_blocking_pids(pid) blockers
            from pg_stat_activity where pid=$1`, [secondPid])).rows[0];
          if (row.wait_event === 'advisory' && row.blockers.includes(firstPid)) { blocked = true; break; }
          await new Promise(resolve => setTimeout(resolve, 10));
        }
        assert.equal(blocked, true, 'the second writer must overlap and wait on the first incident transaction');
        await first.query('commit');
        const deduplicated = await pending;
        assert.equal(deduplicated.loop_id, opened.loop_id);
        assert.equal(deduplicated.deduplicated, true);
        incident = (await read({ source_note: source })).loop;
        assert.equal(incident.loop_id, opened.loop_id);
        assert.equal(incident.status, 'open');
      } finally {
        await first.query('rollback');
        if (pending) await pending.catch(() => {});
      }
    });
    await t.test('overlapping retries of the same pending key replay after the first commits', async () => {
      const pendingOpen = opening();
      await first.query('begin');
      let pending;
      try {
        const opened = await TOOLS['add-loop'].handler(first, actors[0], pendingOpen);
        pending = invoke(second, 'add-loop', pendingOpen);
        pending.catch(() => {});
        let blocked = false;
        const deadline = Date.now() + 3000;
        while (Date.now() < deadline) {
          const row = (await observer.query(`select wait_event, pg_blocking_pids(pid) blockers
            from pg_stat_activity where pid=$1`, [second.processID])).rows[0];
          if (row.wait_event === 'advisory' && row.blockers.includes(first.processID)) { blocked = true; break; }
          await new Promise(resolve => setTimeout(resolve, 10));
        }
        assert.equal(blocked, true, 'a pending-key retry must wait for the original transaction');
        await first.query('commit');
        const replayed = await pending;
        assert.equal(replayed.loop_id, opened.loop_id);
        assert.equal(replayed.replayed, true);
      } finally {
        await first.query('rollback');
        if (pending) await pending.catch(() => {});
      }
    });
    await t.test('incident deduplication preserves principal-bound envelope replay', async () => {
      assert.equal((await invoke(first, 'add-loop', original)).replayed, true);
      await assert.rejects(invoke(second, 'add-loop', original, actors[1]), error => error.payload?.error === 'key_reuse');
    });
    await t.test('done and dropped incidents remain history while later failures receive new records', async () => {
      for (const resolution of ['done', 'dropped']) {
        const previous = incident.loop_id;
        await close(previous, resolution);
        assert.equal((await read({ source_note: source })).loop, null);
        assert.equal((await read({ loop_id: previous })).loop.status, resolution);
        const next = await invoke(second, 'add-loop', opening(), actors[1]);
        assert.notEqual(next.loop_id, previous);
        incident = (await read({ source_note: source })).loop;
        assert.equal(incident.loop_id, next.loop_id);
      }
    });
    await t.test('identity adoption refuses collisions and the adopted identity cannot be removed', async () => {
      const legacy = await invoke(first, 'add-loop', opening('https://example.com/synthetic-legacy'));
      await assert.rejects(invoke(second, 'update-loop', {
        idempotency_key: randomUUID(), loop_id: legacy.loop_id, base_version: 1, source_note: source,
      }, actors[1]), error => error.payload?.error === 'incident_identity_taken');
      await assert.rejects(invoke(second, 'update-loop', {
        idempotency_key: randomUUID(), loop_id: legacy.loop_id, base_version: 1,
        source_note: source, domain: null,
      }, actors[1]), error => error.payload?.error === 'incident_identity_requires_system_loop');
      for (const patch of [{ source_note: null }, { source_note: 'https://example.com' }, { domain: null }]) {
        await assert.rejects(invoke(first, 'update-loop', {
          idempotency_key: randomUUID(), loop_id: incident.loop_id, base_version: incident.version, ...patch,
        }), error => error.payload?.error === 'incident_identity_immutable');
      }
      assert.equal((await read({ loop_id: legacy.loop_id })).loop.source_note, 'https://example.com/synthetic-legacy');
      assert.equal((await read({ source_note: source })).loop.loop_id, incident.loop_id);
      await close(incident.loop_id, 'done');
      await invoke(second, 'update-loop', {
        idempotency_key: randomUUID(), loop_id: legacy.loop_id, base_version: 1, source_note: source,
      }, actors[1]);
      const adopted = (await read({ source_note: source })).loop;
      assert.equal(adopted.loop_id, legacy.loop_id);
      assert.equal(adopted.version, 2);
      await invoke(first, 'update-loop', {
        idempotency_key: randomUUID(), loop_id: adopted.loop_id, base_version: adopted.version,
        body: 'The same Jev outage now reports a different provider failure.',
      });
      const updated = (await read({ source_note: source })).loop;
      assert.equal(updated.loop_id, adopted.loop_id);
      assert.equal(updated.version, 3);
      assert.equal(updated.body, 'The same Jev outage now reports a different provider failure.');
    });
  } finally {
    await Promise.allSettled(clients.map(client => client.end()));
    try { await fixture?.close(); }
    finally { await releaseBudget(); }
  }
});
