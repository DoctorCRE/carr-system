import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';

test('suppression read returns only exact normalized recipient decisions', async () => {
  const tool = TOOLS['mail-contact-policy'];
  assert.equal(tool.write, false);
  const db = {query: async (sql, values) => {
    assert.deepEqual(values, [['person@example.test']]);
    return {rows: [{email: 'person@example.test', blocked: true}]};
  }};
  assert.deepEqual(await tool.handler(db, {}, {emails: [' Person@Example.Test ']}),
    {recipients: [{email: 'person@example.test', blocked: true}]});
});

test('invalid address sets refuse before reading records', async () => {
  for (const emails of [[], ['bad'], ['a@example.test,b@example.test'], Array(51).fill('a@example.test')]) {
    await assert.rejects(TOOLS['mail-contact-policy'].handler({query: () => assert.fail('query')}, {}, {emails}),
      error => error.payload?.error === 'invalid_mail_recipients' || error.error === 'invalid_mail_recipients'
        || String(error).includes('invalid_mail_recipients'));
  }
});

test('existing party and lead instructions block exact email matches in PostgreSQL', async () => {
  const { execFileSync } = await import('node:child_process');
  const { existsSync } = await import('node:fs');
  const { createServer } = await import('node:net');
  const path = await import('node:path');
  const { default: pg } = await import('pg');
  const { acquireDisposablePostgres, acquirePostgresFixtureGroup } = await import('./helpers/disposable-postgres.mjs');
  let bin;
  for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
    try {
      const candidate = execFileSync(config, ['--bindir'], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']}).trim();
      if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
    } catch { /* Try the next supported PostgreSQL installation. */ }
  }
  assert.ok(bin, 'disposable PostgreSQL required for suppression proof');
  const release = await acquirePostgresFixtureGroup();
  let fixture, db;
  try {
    const socket = createServer();
    await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
    const port = socket.address().port;
    await new Promise(resolve => socket.close(resolve));
    fixture = await acquireDisposablePostgres({prefix: 'mail-policy-', pgCtl: path.join(bin, 'pg_ctl')});
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h 127.0.0.1 -p ${port}`, '-w', 'start']);
    db = new pg.Client({connectionString: `postgresql://fixture@127.0.0.1:${port}/postgres`});
    await db.connect();
    await db.query(`create table party(id integer primary key, email text, contact_state text, merged_into integer, deleted_at timestamptz);
      create table lead(party_id integer, suppressed boolean, stage text);
      insert into party values
        (1, ' Blocked@Example.Test ', 'do_not_contact', 2, now()),
        (2, 'blocked@example.test', 'active', null, null),
        (3, 'suppressed@example.test', 'active', null, null),
        (4, 'stage@example.test', 'active', null, null),
        (5, 'allowed@example.test', 'active', null, null);
      insert into lead values(3, true, 'new'), (4, false, 'do_not_contact');`);
    // The deployed reader has these column grants (0500 and 0839), not table ownership.
    await db.query(`create role carr_reader;
      grant select(id,email,contact_state) on party to carr_reader;
      grant select(party_id,suppressed,stage) on lead to carr_reader;
      set role carr_reader;`);
    const read = emails => TOOLS['mail-contact-policy'].handler(db, {}, {emails});
    assert.deepEqual(await read(['unknown@example.test', 'allowed@example.test', 'blocked@example.test',
      'suppressed@example.test', 'stage@example.test']), {recipients: [
      {email: 'allowed@example.test', blocked: false},
      {email: 'blocked@example.test', blocked: true},
      {email: 'stage@example.test', blocked: true},
      {email: 'suppressed@example.test', blocked: true},
      {email: 'unknown@example.test', blocked: false},
    ]});
    await db.query("reset role");
    await db.query("update party set contact_state='do_not_contact' where id=5");
    await db.query('set role carr_reader');
    assert.deepEqual(await read(['allowed@example.test']),
      {recipients: [{email: 'allowed@example.test', blocked: true}]}, 'each read sees a new instruction');
  } finally {
    try { if (db) await db.end(); }
    finally { try { await fixture?.close(); } finally { await release(); } }
  }
});
