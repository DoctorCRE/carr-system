// Test the Outlook intake's contact read: every live party address, any market.
import test from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';

test('every live party address comes back with its record ref, in or out of market', async () => {
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
  assert.ok(bin, 'disposable PostgreSQL required for contact read proof');
  const release = await acquirePostgresFixtureGroup();
  let fixture, db;
  try {
    const socket = createServer();
    await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
    const port = socket.address().port;
    await new Promise(resolve => socket.close(resolve));
    fixture = await acquireDisposablePostgres({prefix: 'match-contacts-', pgCtl: path.join(bin, 'pg_ctl')});
    const data = path.join(fixture.root, 'data');
    await fixture.run(path.join(bin, 'initdb'), ['-D', data, '-U', 'fixture', '--auth=trust', '--no-locale']);
    await fixture.run(path.join(bin, 'pg_ctl'), ['-D', data, '-l', path.join(fixture.root, 'server.log'),
      '-o', `-k ${fixture.root} -h 127.0.0.1 -p ${port}`, '-w', 'start']);
    db = new pg.Client({connectionString: `postgresql://fixture@127.0.0.1:${port}/postgres`});
    await db.connect();
    await db.query(`create table party(id integer primary key, ref text, name text, email text, org_id integer,
        merged_into integer, deleted_at timestamptz, phone text);
      create table client(party_id integer, roster_ref text, merged_into integer);
      create table lead(party_id integer, registry_ref text, stage text);
      create table vendor(party_id integer, vendor_ref text, merged_into integer, out_of_market boolean);
      insert into party values
        (1, 'P-0001', 'Pat Client', ' Pat <PAT@Client.Test> ', null, null, null, '555'),
        (2, 'P-0002', 'Far Vendor', 'far@vendor.test', null, null, null, null),
        (3, 'P-0003', 'Lena Lender', 'lena@bank.test', null, null, null, null),
        (4, 'P-0004', 'Merged Away', 'merged@old.test', null, 1, null, null),
        (5, 'P-0005', 'Deleted', 'deleted@old.test', null, null, now(), null),
        (6, 'P-0006', '', 'dr@practice.test; frontdesk@practice.test', 7, null, null, null),
        (7, 'P-0007', 'Practice Org', 'info@practice.test', null, null, null, null),
        (8, 'P-0008', 'No Email', null, null, null, null, null),
        (9, 'P-0009', 'Old Client', 'old@client.test', null, null, null, null),
        (10, 'P-0010', 'Notes Only', 'call the office', null, null, null, null),
        (11, 'P-0011', 'Landlord Rep', 'rep@landlord.test', null, null, null, null);
      insert into client values (1, 'C-0101', null), (9, 'C-0109', 101);
      insert into lead values (6, 'L-0206', 'new'), (1, 'L-0201', 'lost');
      insert into vendor values (2, 'V-LAW-001', null, true), (4, 'V-OLD-004', null, false);
      -- Migration 1029's view: role-less, live participants of open deals. Its filter is the view's own.
      create table v_email_deal_contact("Deal ID" uuid, "Name" text, "Email" text);
      insert into v_email_deal_contact values
        ('aaaaaaaa-0000-4000-8000-000000000001', 'Bayside suite', ' Rep@Landlord.test '),
        ('aaaaaaaa-0000-4000-8000-000000000002', 'Office build', 'pm@build.test; office@build.test'),
        ('aaaaaaaa-0000-4000-8000-000000000003', 'Blank deal', 'no address here');`);
    // The deployed reader has these column grants (0500, 0839, 0989), not table ownership.
    await db.query(`create role carr_reader;
      grant select(id,name,ref,email,merged_into,deleted_at,org_id) on party to carr_reader;
      grant select(party_id,roster_ref,merged_into) on client to carr_reader;
      grant select(party_id,registry_ref) on lead to carr_reader;
      grant select(party_id,vendor_ref,merged_into) on vendor to carr_reader;
      grant select on v_email_deal_contact to carr_reader;
      set role carr_reader;`);
    const tool = TOOLS['email-match-contacts'];
    assert.equal(tool.write, false);
    const reply = await tool.handler(db, {}, {});
    assert.equal(reply.schema_version, 'email-match-contacts.v1');
    assert.deepEqual(reply.contacts, [
      {address: 'pat@client.test', ref: 'C-0101', name: 'Pat Client', exact: true},
      {address: 'pat@client.test', ref: 'L-0201', name: 'Pat Client', exact: true},
      {address: 'dr@practice.test', ref: 'L-0206', name: 'Practice Org', exact: true},
      {address: 'frontdesk@practice.test', ref: 'L-0206', name: 'Practice Org', exact: false},
      {address: 'lena@bank.test', ref: 'P-0003', name: 'Lena Lender', exact: true},
      {address: 'info@practice.test', ref: 'P-0007', name: 'Practice Org', exact: true},
      {address: 'old@client.test', ref: 'P-0009', name: 'Old Client', exact: true},
      {address: 'rep@landlord.test', ref: 'P-0011', name: 'Landlord Rep', exact: true},
      {address: 'far@vendor.test', ref: 'V-LAW-001', name: 'Far Vendor', exact: true},
      {address: 'rep@landlord.test', ref: 'aaaaaaaa-0000-4000-8000-000000000001', name: 'Bayside suite', exact: true},
      {address: 'pm@build.test', ref: 'aaaaaaaa-0000-4000-8000-000000000002', name: 'Office build', exact: true},
      {address: 'office@build.test', ref: 'aaaaaaaa-0000-4000-8000-000000000002', name: 'Office build', exact: false},
    ], 'out-of-market and role-less parties are read, plus open-deal contacts by deal ref; merged-away and deleted parties never are');
  } finally {
    try { if (db) await db.end(); }
    finally { try { await fixture?.close(); } finally { await release(); } }
  }
});
