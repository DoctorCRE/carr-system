// Tests duplicate party candidate matching and explanations.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { createServer } from './helpers/proof-release-fixture.ts';
import { tmpdir } from 'node:os';
import path from 'node:path';
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
    await t.test('recorded address lists preserve Unicode membership and deduplicate every pair', async () => {
      const phone = ['202', '555', '0123'].join('');
      const addresses = "First@Example.invalid; Élise@例子.invalid; shared@example.invalid; SHARED@example.invalid";
      await add(60, { email: addresses, name: 'Synthetic Shared Person', phone, npi: '1234567890' });
      await add(61, { email: 'other@example.invalid, shared@EXAMPLE.invalid', name: ' synthetic  shared  person ', phone, npi: '1234567890' });
      await add(62, { email: 'Élise@例子.invalid' });
      await add(63, { email: 'notshared@example.invalid' });
      await add(64, { email: 'shared@example.invalid.extra.invalid' });
      const first = await read({ limit: 1 });
      assert.deepEqual(first.candidates.map(p => [p.left.id,p.right.id]), [[id(60),id(61)]]);
      assert.deepEqual(first.candidates[0].reasons.map(r => r.code), ['same_email','same_npi','same_name_phone']);
      const second = await read({ limit: 1, cursor: first.next_cursor });
      assert.deepEqual(second.candidates.map(p => [p.left.id,p.right.id]), [[id(60),id(62)]]);
      assert.equal(second.has_more, false);
      assert.equal(second.next_cursor, null);
      await c.query('update party set email=null where id=$1', [id(62)]);
      assert.equal((await read()).candidates.length, 1);
      await c.query('update party set deleted_at=now() where id=$1', [id(61)]);
      assert.equal((await read()).candidates.length, 0);
    });
    await t.test('membership agrees with the registered intake parser for recorded address syntax', async () => {
      const values = [null, '', 'unknown', 'foo@invalid', 'A@Example.invalid; b@example.invalid',
        "Élise@例子.invalid; élise@例子.invalid", "O'Neil@sub.example.invalid, o'neil@SUB.example.invalid",
        'e\u0301@例子.invalid', 'e\u0301@例子.invalid; extra@example.invalid', 'a@example.invalid.extra.invalid'];
      const input = values.map((email, index) => ({ email, ref: `P-${80 + index}`, name: `Syntax ${index}` }));
      for (const [index, row] of input.entries()) await add(80 + index, { email: row.email });
      const parsed = await TOOLS['email-match-contacts'].handler({ query: async () => ({ rows: input.filter(r => r.email?.includes('@')) }) });
      const expected = [];
      for (let a = 0; a < input.length; a++) for (let b = a + 1; b < input.length; b++) {
        const left = parsed.contacts.filter(c => c.ref === input[a].ref).map(c => c.address);
        const right = parsed.contacts.filter(c => c.ref === input[b].ref).map(c => c.address);
        if (left.some(address => right.includes(address))) expected.push([id(80+a), id(80+b)]);
      }
      assert.deepEqual((await read()).candidates.map(p => [p.left.id,p.right.id]), expected);
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
    await t.test('proof CLI uses registered responses and refuses missing, contradictory or unavailable witnesses', async () => {
      await add(70, { email: 'unique@example.invalid; shared@example.invalid' });
      await add(71, { email: 'other@example.invalid; shared@example.invalid' });
      const raw = [70,71].map(n => ({ ref: `P-${n}`, name: `Synthetic Person ${n}`,
        email: `${n === 70 ? 'unique' : 'other'}@example.invalid; shared@example.invalid`,
        party_id: id(n), party_version: 1, party_name: `Synthetic Person ${n}`,
        org_id: null, company_id: null, phone: null, cell: null, title: null, company_name: null }));
      const contacts = await TOOLS['email-match-contacts'].handler({ query: async () => ({ rows: raw }) });
      assert.equal(contacts.contacts.length, 4);
      const research = {};
      for (const n of [70,71]) research[`P-${n}`] = await TOOLS['prospect-research'].handler(
        { query: async () => ({ rows: [{ id: id(n), ref: `P-${n}`, name: `Synthetic Person ${n}`,
          specialty: null, city: null, state: null, contact_state: 'active', relationship: 'contact', evidence: [] }] }) }, actor, { subject: `P-${n}` });
      const page = await read();
      assert.equal(page.candidates.length, 1);
      const dir = mkdtempSync(path.join(tmpdir(), 'duplicate-proof-'));
      const fixturePath = path.join(dir, 'responses.json');
      const wrapper = path.join(dir, 'wrapper');
      const runner = new URL('tools/prove-duplicate-candidates-address-membership.py', root).pathname;
      let mode, releases;
      const server = createServer((req, res) => {
        assert.equal(req.url, '/release');
        assert.equal(req.headers['user-agent'], 'doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)');
        releases++;
        res.end(JSON.stringify({ git_sha: { value: (mode === 'release-change' && releases > 1 ? 'b' : 'a').repeat(40) } }));
      });
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
      try {
        for (const [scenario, expected] of [['pass',0],['missing-reason',1],['missing-pair',78],['empty',78],
          ['auth',78],['timeout',78],['release-change',78],['repeat',1],['bound',78],['bad-contract',78]]) {
          mode = scenario; releases = 0;
          const candidate = structuredClone(page);
          if (mode === 'missing-reason') candidate.candidates[0].reasons = [];
          if (mode === 'missing-pair') candidate.candidates = [];
          if (mode === 'repeat') candidate.candidates.push(candidate.candidates[0]);
          if (mode === 'bound') {
            candidate.has_more = true;
            candidate.next_cursor = { left_id: id(70), right_id: id(71) };
          }
          writeFileSync(fixturePath, JSON.stringify({ contacts: mode === 'empty' ? { ...contacts, contacts: [] } : contacts,
            research, page: candidate }));
          writeFileSync(wrapper, `#!/usr/bin/env python3
import json, sys, time
from pathlib import Path
mode=${JSON.stringify(mode)}
if mode=='auth': sys.exit(1)
if mode=='timeout': time.sleep(2)
f=json.loads(Path(${JSON.stringify(fixturePath)}).read_text())
v,a=sys.argv[2],json.loads(sys.argv[3])
assert sys.argv[1]=='call'
if mode=='bad-contract': print('{}'); sys.exit(0)
if v=='email-match-contacts': result=f['contacts']
elif v=='prospect-research': result=f['research'][a['subject']]
else:
 assert v=='party-duplicate-candidates'
 result=f['page']
 if mode=='bound' and 'cursor' in a:
  n=int(a['cursor']['right_id'][-12:])+1
  result['candidates'][0]['right']['id']='aa000000-0000-4000-8000-'+str(n).zfill(12)
  result['next_cursor']['right_id']=result['candidates'][0]['right']['id']
print(json.dumps(result))
`);
          chmodSync(wrapper, 0o700);
          const result = await new Promise((resolve, reject) => {
            const child = spawn('python3', [runner, '--origin', `http://127.0.0.1:${server.address().port}`,
              '--call-wrapper', wrapper, '--timeout-seconds', mode === 'timeout' ? '0.2' : '30']);
            let stdout = '', stderr = '';
            child.stdout.on('data', chunk => { stdout += chunk; });
            child.stderr.on('data', chunk => { stderr += chunk; });
            child.on('error', reject);
            child.on('close', code => resolve({ code, stdout, stderr }));
          });
          assert.equal(result.code, expected, `${mode}: ${result.stdout} ${result.stderr}`);
          const receipt = JSON.parse(result.stdout);
          assert.ok(receipt.wrapper_calls <= 20);
          assert.ok(receipt.examined_rows <= 1000);
          assert.equal(result.stdout.includes('@'), false);
          if (mode === 'pass') {
            assert.equal(receipt.assertions, 3);
            assert.deepEqual(receipt.party_ids, [id(70),id(71)]);
            assert.equal(releases, 2);
          }
        }
      } finally {
        await new Promise(resolve => server.close(resolve));
        rmSync(dir, { recursive: true, force: true });
      }
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
