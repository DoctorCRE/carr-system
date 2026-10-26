// Tests duplicate party candidate matching and explanations.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { TOOLS } from '../src/tools.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const root = new URL('../../', import.meta.url);
const schema = readFileSync(new URL('db/schema.sql', root), 'utf8');
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { id: id(1), slug: 'joe' };

test('party duplicate candidates through the registered reader verb', async t => {
  const tool = TOOLS['party-duplicate-candidates'];
  assert.ok(tool, 'candidate report must be registered');
  assert.equal(tool.write, false);
  const fixture = await postgresDatabase('party-candidates-');
  const c = fixture.client;
  try {
    await c.query('create role carr_reader');
    await c.query(schema.match(/CREATE TABLE public\.party \([\s\S]*?\n\);/)[0]);
    const grants = readFileSync(new URL('migrations/0500_workspace_business_reader_columns.sql', root), 'utf8');
    await c.query(grants.match(/GRANT SELECT \([^;]+ON TABLE public\.party TO carr_reader;/)[0]);
    const add = async (n, fields = {}) => {
      const row = { id: id(n), kind: 'person', name: `Synthetic Person ${n}`, ref: `P-${n}`, ...fields };
      await c.query(`insert into party(id,kind,name,ref,email,phone,cell,npi,merged_into,deleted_at,created_by,updated_by)
        values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11)`,
      [row.id,row.kind,row.name,row.ref,row.email ?? null,row.phone ?? null,row.cell ?? null,row.npi ?? null,row.merged_into ?? null,row.deleted_at ?? null,actor.id]);
      return { id: row.id, ref: row.ref, name: row.name, kind: row.kind };
    };
    const read = async (args = {}) => {
      await c.query('begin read only');
      await c.query('set local role carr_reader');
      try { return await tool.handler(c, actor, args); }
      finally { await c.query('rollback'); }
    };
    t.beforeEach(async () => { await c.query('truncate party'); });
    await t.test('report explains normalized email matches without choosing a survivor', async () => {
      const left = await add(10, { email: ' Shared@Example.invalid ' });
      const right = await add(11, { email: 'shared@example.invalid' });
      const report = await read();
      assert.equal(report.schema_version, 'party-duplicate-candidates.v1');
      assert.equal(report.read_only, true);
      assert.deepEqual(report.candidates, [{ left, right, reasons: [{ code: 'same_email', explanation: 'Same email after trimming and case normalization.' }] }]);
      assert.equal(report.has_more, false);
      assert.equal(report.next_cursor, null);
      assert.match(report.caution, /different/);
      assert.equal(JSON.stringify(report).includes('score'), false);
      assert.equal(JSON.stringify(report).includes('survivor'), false);
      assert.deepEqual(await read(), report);
    });
    await t.test('same-name, phone-only, domain-only and absent identifiers stay distinct', async () => {
      await add(20, { name: 'Synthetic Same Name', email: 'first@example.invalid', phone: '2025550100' });
      await add(21, { name: ' synthetic  same name ', email: 'second@example.invalid', phone: '2025550101' });
      await add(22, { name: 'Synthetic Different Name', phone: '2025550100' });
      await add(23, { email: '  ', phone: ' ', npi: '' });
      await add(24, { email: '', phone: '', npi: ' ' });
      await add(25, { email: '\t' });
      await add(26, { email: '\t' });
      await add(27, { email: 'unknown' });
      await add(28, { email: 'unknown' });
      assert.deepEqual((await read()).candidates, []);
    });
    await t.test('NPI and full-name plus phone produce explicit reasons for people and companies', async () => {
      await add(30, { npi: '1234567890' });
      await add(31, { npi: ' 1234567890 ' });
      await add(32, { kind: 'org', name: 'Synthetic Firm', phone: '+1 (202) 555-0123' });
      await add(33, { kind: 'org', name: ' synthetic   firm ', cell: '202.555.0123' });
      const report = await read();
      assert.deepEqual(report.candidates.map(pair => [pair.left.id, pair.right.id, pair.reasons]), [
        [id(30), id(31), [{ code: 'same_npi', explanation: 'Same ten-digit NPI on two person records.' }]],
        [id(32), id(33), [{ code: 'same_name_phone', explanation: 'Same full name after whitespace and case normalization, with a matching phone or cell number.' }]],
      ]);
    });
    await t.test('paging reports every pair once and retains all matching reasons', async () => {
      for (const n of [40,41,42]) await add(n, { name: 'Synthetic Shared', email: 'shared@example.invalid', phone: '2025550123', npi: '1234567890' });
      const first = await read({ limit: 1 });
      assert.equal(first.candidates.length, 1);
      assert.equal(first.has_more, true);
      assert.deepEqual(first.next_cursor, { left_id: id(40), right_id: id(41) });
      assert.deepEqual(first.candidates[0].reasons.map(reason => reason.code), ['same_email','same_npi','same_name_phone']);
      const rest = await read({ limit: 2, cursor: first.next_cursor });
      assert.deepEqual(rest.candidates.map(pair => [pair.left.id,pair.right.id]), [[id(40),id(42)],[id(41),id(42)]]);
      assert.equal(rest.has_more, false);
      assert.equal(rest.next_cursor, null);
      assert.equal((await read({ cursor: { left_id: id(41), right_id: id(42) } })).candidates.length, 0);
    });
    await t.test('merged, deleted, cross-kind and malformed identifiers do not create candidates', async () => {
      await add(50, { email: 'synthetic@example.invalid' });
      await add(51, { email: 'synthetic@example.invalid', merged_into: id(50) });
      await add(52, { email: 'synthetic@example.invalid', deleted_at: '2020-01-01T00:00:00Z' });
      await add(53, { kind: 'org', email: 'synthetic@example.invalid' });
      await add(54, { name: 'Synthetic Invalid', phone: '2025550123 ext 4', npi: '123' });
      await add(55, { name: 'Synthetic Invalid', cell: '20255501234', npi: '123' });
      await add(56, { kind: 'org', npi: '1234567890' });
      await add(57, { kind: 'org', npi: '1234567890' });
      assert.deepEqual((await read()).candidates, []);
    });
    await t.test('invalid limits and malformed cursors refuse at the report interface', async () => {
      for (const limit of [null,0,101,1.5,'1'])
        await assert.rejects(read({ limit }), error => error.payload?.error === 'invalid_duplicate_report_limit');
      for (const cursor of [null,{}, { left_id: id(1) }, { left_id: id(2),right_id: id(1) }, { left_id: 'bad',right_id: id(2) }, { left_id: id(1),right_id: id(2),extra: true }])
        await assert.rejects(read({ cursor }), error => error.payload?.error === 'invalid_duplicate_report_cursor');
      await assert.rejects(read({ merge: true }), error => error.payload?.error === 'invalid_duplicate_report_arguments');
    });
  } finally {
    await fixture.close();
  }
});
