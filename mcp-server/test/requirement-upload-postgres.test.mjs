// Exercise row-bound upload capabilities through HTTP and registered verbs.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID, createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { createRequirementUploadHandler, MAX_UPLOAD_BYTES } from '../src/requirement-upload.js';
import { readDealChecklist, CHECKLIST_SOURCES } from '../src/deal-checklist.ts';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
const actor = { id: randomUUID(), slug: 'joe', human: true };
const deal = randomUUID(), other = randomUUID();
const pdf = Buffer.from('%PDF-1.7\nsynthetic closing document');

test('a requested checklist row receives a quarantined PDF without becoming satisfied', async t => {
  const fixture = await postgresDatabase('requirement-upload-');
  const c = fixture.client;
  const objects = new Map();
  try {
    const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'attachment', 'event', 'tool_call'])
      await c.query(schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
    await restoreEventIdentity(c, schema);
    await c.query(`alter table actor add primary key(id); alter table attachment add primary key(id);
      alter table tool_call add primary key(idempotency_key);
      create role carr_reader; create role carr_writer; create role carr_authority; create schema ops;
      create table deal(id uuid primary key, client_id uuid, deal_type text, owner text);
      create table activity(id uuid primary key);
      create table v_ref_index(subject_type text, subject_id uuid, ref text, display_name text);
      create table doctrine_section(id uuid, status text, body_hash text);
      create table v_deal_room_commission(deal_id uuid, commission_id uuid, status text, received_on date);
      create function ops.scac_current_grant_contract() returns jsonb language sql as $$select '{"relation_dml":[]}'::jsonb$$;`);
    await c.query("insert into actor(id,slug,kind,display_name,active) values($1,'joe','human','Synthetic partner',true)", [actor.id]);
    await c.query("insert into deal values($1,$3,'purchase','joe'),($2,$3,'purchase','joe')", [deal, other, randomUUID()]);
    await c.query("insert into v_ref_index values('deal',$1,'D-1','Synthetic closing'),('deal',$2,'D-2','Synthetic other')", [deal, other]);
    for (const s of CHECKLIST_SOURCES) await c.query("insert into doctrine_section values($1,'active',$2)", [s.section_id, s.content_hash]);
    await c.query(readFileSync(new URL('../../migrations/1102_requirement_requests.sql', import.meta.url), 'utf8'));
    await c.query(readFileSync(new URL('../../migrations/1104_requirement_document_replies.sql', import.meta.url), 'utf8'));
    const call = async (name, args) => {
      await c.query('begin');
      try {
        const result = await executeRegisteredTool(c, actor, name, { ...(name === 'list-requirement-requests' ? {} : { idempotency_key: randomUUID() }), ...args });
        await c.query('commit'); return result;
      } catch (error) { await c.query('rollback'); throw error; }
    };
    const requested = await call('request-checklist-document', { deal, row_key: 'title_commitment', owner: 'joe', due_on: '2026-11-15' });
    assert.equal(requested.request.document_type, 'title_commitment');
    assert.equal(requested.request.source.section_id, CHECKLIST_SOURCES[2].section_id);
    await t.test('the checklist projects row ownership, deadline and request without altering milestone state', async () => {
      const checklist = await readDealChecklist(c, { id: deal, type: 'purchase', owner: 'joe' });
      const row = checklist.phases.flatMap(p => p.items).find(r => r.id === 'title_commitment');
      assert.equal(row.request.id, requested.request.id);
      assert.equal(row.owner, 'joe');
      assert.equal(row.due_on, '2026-11-15');
      assert.equal(row.status, 'not_recorded');
    });
    const request_id = requested.request.id;
    const grant = async (overrides = {}) => {
      const token = randomUUID().replaceAll('-', '') + randomUUID().replaceAll('-', '');
      const issued = await call('issue-requirement-upload', { request_id, token_sha256: hash(token), expires_in_seconds: 600, ...overrides });
      return { token, ...issued };
    };
    const buildHandler = db => createRequirementUploadHandler({
      transaction: async fn => {
        await db.query('begin');
        try { const result = await fn(db); await db.query('commit'); return result; }
        catch (error) { await db.query('rollback'); throw error; }
      },
      bucket: { put: async (key, bytes) => { objects.set(key, Buffer.from(bytes)); } },
    });
    const handler = buildHandler(c);
    const upload = (g, { tenant = 'carr-internal', dealId = deal, row = 'title_commitment', body = pdf, headers = {}, visitor = '192.0.2.1' } = {}) => handler(new Request(
      `https://share.doctorcre.com/requirement-upload/${tenant}/${dealId}/${row}/${g.grant_id}`,
      { method: 'POST', body, headers: { authorization: `Bearer ${g.token}`, 'content-type': 'application/pdf',
        'content-length': String(body.length), 'cf-connecting-ip': visitor, ...headers }, duplex: 'half' }));

    await t.test('receives only on its tenant, deal and row, and refuses replay', async () => {
      const g = await grant();
      for (const mismatch of [{ tenant: 'other-tenant' }, { dealId: other }, { row: 'appraisal' }])
        assert.equal((await upload(g, mismatch)).status, 403);
      assert.equal((await upload({ ...g, token: '0'.repeat(64) })).status, 403);
      const result = await upload(g);
      assert.equal(result.status, 201);
      const receipt = await result.json();
      assert.equal(receipt.state, 'quarantined');
      assert.equal(receipt.request_id, request_id);
      assert.deepEqual([...objects.values()], [pdf]);
      assert.equal((await upload(g)).status, 403);
      const listed = await call('list-requirement-requests', { deal });
      assert.equal(listed.requests[0].receipts[0].state, 'quarantined');
      assert.equal(listed.requests[0].satisfied, false);
      assert.equal((await c.query('select count(*)::int n from attachment')).rows[0].n, 0);
    });
    await t.test('revocation and expiry refuse receipt without writing an object', async () => {
      const before = objects.size;
      const revoked = await grant();
      await call('revoke-requirement-upload', { grant_id: revoked.grant_id });
      assert.equal((await upload(revoked)).status, 403);
      const expired = await grant();
      await c.query("update ops.requirement_upload_grant set created_at=now()-interval '2 hours',expires_at=now()-interval '1 hour' where id=$1", [expired.grant_id]);
      assert.equal((await upload(expired)).status, 403);
      assert.equal(objects.size, before);
    });
    await t.test('rejects size headers before accessing the body, and inspects bytes rather than declared MIME', async () => {
      const g = await grant();
      class UnreadBody extends Request { get body() { throw new Error('must not read body'); } }
      for (const length of [null, '0', String(MAX_UPLOAD_BYTES + 1)]) {
        const headers = new Headers({ authorization: `Bearer ${g.token}`, 'content-type': 'application/pdf' });
        if (length !== null) headers.set('content-length', length);
        const request = new UnreadBody(`https://share.doctorcre.com${g.upload_path}`, { method: 'POST', headers });
        assert.equal((await handler(request)).status, 413);
      }
      assert.equal((await upload(g, { body: Buffer.from('not a PDF'), visitor: '192.0.2.2' })).status, 415);
      assert.equal((await upload(g)).status, 403, 'failed content inspection consumes the capability');
      const lying = await grant();
      assert.equal((await upload(lying, { headers: { 'content-length': '5' }, visitor: '192.0.2.3' })).status, 413);
      const short = await grant();
      assert.equal((await upload(short, { headers: { 'content-length': '99' }, visitor: '192.0.2.4' })).status, 413);
    });
    await t.test('per-visitor write reservations stop the fifth attempt and leave another visitor independent', async () => {
      const visitor = '192.0.2.20';
      for (let i = 0; i < 4; i++) assert.equal((await upload(await grant(), { visitor })).status, 201);
      const limited = await grant();
      assert.equal((await upload(limited, { visitor })).status, 429);
      assert.equal((await upload(limited, { visitor: '192.0.2.21' })).status, 201);
    });
    await t.test('two concurrent requests cannot consume the same capability twice', async () => {
      const second = new pg.Client(c.connectionParameters);
      await second.connect();
      try {
        const g = await grant();
        const request = () => new Request(`https://share.doctorcre.com${g.upload_path}`, { method: 'POST', body: pdf,
          headers: { authorization: `Bearer ${g.token}`, 'content-type': 'application/pdf',
            'content-length': String(pdf.length), 'cf-connecting-ip': '192.0.2.25' } });
        const responses = await Promise.all([handler(request()), buildHandler(second)(request())]);
        assert.deepEqual(responses.map(r => r.status).sort(), [201, 403]);
      } finally { await second.end(); }
    });
    await t.test('exhausted lifetime storage budget refuses before an object write', async () => {
      const before = objects.size;
      await c.query('update ops.requirement_upload_quota set reserved_bytes=104857600 where singleton');
      assert.equal((await upload(await grant(), { visitor: '192.0.2.30' })).status, 503);
      assert.equal(objects.size, before);
    });
    await t.test('request creation refuses stale doctrine, critical dates and authoritative invoice milestones', async () => {
      const args = { deal, owner: 'joe', due_on: '2026-11-15' };
      for (const row_key of ['critical-date:synthetic', 'invoice_recorded', 'not_a_row'])
        await assert.rejects(call('request-checklist-document', { ...args, row_key }), /row_not_requestable/);
      await c.query("update doctrine_section set body_hash='stale' where id=$1", [CHECKLIST_SOURCES[0].section_id]);
      await assert.rejects(call('request-checklist-document', { ...args, row_key: 'appraisal' }), /row_not_requestable/);
    });
  } finally { await fixture.close(); }
});
