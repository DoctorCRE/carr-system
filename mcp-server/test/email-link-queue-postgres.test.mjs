import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { emailProposalTools } from '../src/email-proposals.js';

test('link queue executes hydration, filing and cursor paging in PostgreSQL', async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let fixture, admin, database, c;
  try {
    if (ciDsn) {
      const url = new URL(ciDsn);
      assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
      assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname));
      assert.equal(url.search, '');
      admin = new pg.Client({ connectionString: ciDsn });
      await admin.connect();
      database = `email_link_${randomUUID().replaceAll('-', '')}`;
      await admin.query(`create database "${database}" template template0`);
      url.pathname = `/${database}`;
      c = new pg.Client({ connectionString: url.href });
    } else {
      const bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
        '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
      assert.ok(bin, 'PostgreSQL is required');
      fixture = await acquireDisposablePostgres({ prefix: 'email-link-', pgCtl: `${bin}/pg_ctl` });
      await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
      await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
      c = new pg.Client({ host: fixture.root, user: 'carr_ci', database: 'postgres' });
    }
    await c.connect();
    await c.query(`create table activity (id uuid primary key, deal_id uuid, client_id uuid, vendor_id uuid,
      lead_id uuid, actor_id uuid, kind text, detail text, email_context jsonb, email_source jsonb,
      occurred_at timestamptz, recorded_at timestamptz)`);
    const deal = '20000000-0000-0000-0000-000000000001';
    const actor = '30000000-0000-0000-0000-000000000001';
    const ids = [1, 2, 3, 4].map(n => `10000000-0000-0000-0000-00000000000${n}`);
    await c.query(`insert into activity(id,deal_id,actor_id,kind,detail,email_context,occurred_at,recorded_at) values
      ($1,$5,$6,'email_in','preview',null,now(),now()),
      ($2,$5,$6,'email_out','second',null,now(),now()),
      ($3,null,$6,'email_in','unfiled',null,now(),now()),
      ($4,$5,$6,'note','full body',jsonb_build_object('source_activity_id',$1::text),now(),now())`, [...ids, deal, actor]);
    const queue = emailProposalTools({})['email-router-queue'];
    const caller = { human: true, slug: 'joe', id: actor };
    const first = await queue.handler(c, caller, { purpose: 'links', limit: 1 });
    assert.equal(first.emails[0].body, 'full body');
    assert.equal(first.emails[0].activity_id, ids[0]);
    assert.equal(first.next_cursor, ids[0]);
    const next = await queue.handler(c, caller, { purpose: 'links', limit: 1, after: first.next_cursor });
    assert.deepEqual(next.emails.map(row => row.activity_id), [ids[1]]);
    assert.equal(next.more, false);
    const other = await queue.handler(c, { human: true, slug: 'dell', id: '30000000-0000-0000-0000-000000000002' }, { purpose: 'links' });
    assert.deepEqual(other.emails.map(row => row.activity_id), [ids[0]]);
  } finally {
    await c?.end();
    if (database) await admin.query(`drop database "${database}"`);
    await admin?.end();
    await fixture?.close();
  }
});
