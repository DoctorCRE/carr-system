// Exercise row-bound upload capabilities through HTTP and registered verbs.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { randomUUID, createHash, createHmac } from 'node:crypto';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { createRequirementUploadHandler, MAX_UPLOAD_BYTES } from '../src/requirement-upload.js';
import { readDealChecklist, CHECKLIST_SOURCES } from '../src/deal-checklist.ts';

const hash = value => createHash('sha256').update(value).digest('hex');
const actor = { id: randomUUID(), slug: 'joe', human: true };
const deal = randomUUID(), other = randomUUID();
const pdf = Buffer.from('%PDF-1.7\nsynthetic closing document');

test('a requested checklist row receives a quarantined PDF without becoming satisfied', async t => {
  const fixture = await acquireDisposablePostgres({prefix:'requirement-upload-',snapshot:true});
  const c = new pg.Client(fixture.connection);
  await c.connect();
  const objects = new Map();
  try {
    actor.id = (await c.query("insert into actor(id,slug,kind,display_name,active) values($1,'joe','human','Synthetic partner',true) on conflict(slug) do update set active=true returning id", [actor.id])).rows[0].id;
    const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic requirement client',$1,$1) returning id",[actor.id])).rows[0].id;
    const client=(await c.query('insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id',[party,actor.id])).rows[0].id;
    await c.query("insert into deal(id,name,client_id,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic closing',$3,'purchase','research','joe',$4,$4),($2,'Synthetic other',$3,'purchase','research','joe',$4,$4)",[deal,other,client,actor.id]);
    const doc=(await c.query("insert into doctrine_document(slug,title,content_class,visibility) values('synthetic-requirements','Synthetic requirements','reference','shared') returning id")).rows[0].id;
    for (const [index, source] of CHECKLIST_SOURCES.entries()) await c.query(`insert into doctrine_section(id,document_id,section_key,status,body_hash,ordinal)
      values($1,$2,$3,'active',$4,$5) on conflict(id) do update set status='active',body_hash=excluded.body_hash`,[source.section_id,doc,'source-'+index,source.content_hash,index]);
    const call = async (name, args) => {
      await c.query('begin');
      try {
        const result = await executeRegisteredTool(c, actor, name, { ...(name === 'list-requirement-requests' ? {} : { idempotency_key: randomUUID() }), ...args });
        await c.query('commit'); return result;
      } catch (error) { await c.query('rollback'); throw error; }
    };
    const requested = await call('request-checklist-document', { deal, row_key: 'title_commitment', owner: 'joe', due_on: '2020-01-01' });
    assert.match(requested.work_request,/^WR-[0-9]+$/);
    assert.equal(requested.request.document_type, 'title_commitment');
    assert.equal(requested.request.source.section_id, CHECKLIST_SOURCES[2].section_id);
    await t.test('the checklist projects row ownership, deadline and request without altering milestone state', async () => {
      const checklist = await readDealChecklist(c, { id: deal, type: 'purchase', owner: 'joe' });
      const row = checklist.phases.flatMap(p => p.items).find(r => r.id === 'title_commitment');
      assert.equal(row.request.id, requested.request.id);
      assert.equal(row.owner, 'joe');
      assert.equal(row.due_on, '2020-01-01');
      assert.ok(checklist.due_items.some(item => item.id === row.id));
      assert.equal(row.status, 'overdue');
    });
    await t.test('missing requests view degrades the checklist', async () => {
      await c.query('alter view v_requirement_request rename to hidden_requests');
      try {
        const checklist = await readDealChecklist(c, { id: deal, type: 'purchase' });
        assert.equal(checklist.template_state, 'current');
        assert.ok(checklist.phases.flatMap(p => p.items).some(row => row.id === 'title_commitment'));
        assert.equal(checklist.requests_state, 'unavailable');
      }
      finally { await c.query('alter view hidden_requests rename to v_requirement_request'); }
    });
    const request_id = requested.request.id;
    const grant = async (overrides = {}) => {
      const token = randomUUID().replaceAll('-', '') + randomUUID().replaceAll('-', '');
      const issued = await call('issue-requirement-upload', { request_id, token_sha256: hash(token), expires_in_seconds: 600, ...overrides });
      return { token, ...issued };
    };
    const buildHandler = (db, put = async (key, bytes) => { objects.set(key, Buffer.from(bytes)); }) => createRequirementUploadHandler({
      visitorSecret: 'synthetic-visitor-secret',
      transaction: async fn => {
        await db.query('begin');
        await db.query('set local role carr_writer');
        try { const result = await fn(db); await db.query('commit'); return result; }
        catch (error) { await db.query('rollback'); throw error; }
      },
      bucket: { put, delete: async key => { objects.delete(key); } },
    });
    const handler = buildHandler(c);
    const upload = (g, { tenant = 'carr-internal', dealId = deal, row = 'title_commitment', body = pdf, headers = {}, visitor = '192.0.2.1' } = {}) => handler(new Request(
      `https://share.doctorcre.com/requirement-upload/${tenant}/${dealId}/${row}/${g.grant_id}`,
      { method: 'POST', body, headers: { authorization: `Bearer ${g.token}`, 'content-type': 'application/pdf',
        'content-length': String(body.length), 'cf-connecting-ip': visitor, ...headers }, duplex: 'half' }));

    await t.test('visitor hashes are keyed and old visitor rows are pruned on write', async () => {
      await c.query("insert into ops.requirement_upload_visitor values($1,now()-interval '25 hours',5)", ['0'.repeat(64)]);
      assert.equal((await upload(await grant(), { visitor: '192.0.2.99' })).status, 201);
      const rows = (await c.query('select visitor_hash from ops.requirement_upload_visitor')).rows.map(r => r.visitor_hash);
      assert.ok(rows.includes(createHmac('sha256', 'synthetic-visitor-secret').update('192.0.2.99').digest('hex')));
      assert.ok(!rows.includes(hash('192.0.2.99')));
      assert.ok(!rows.includes('0'.repeat(64)));
      objects.clear();
      await c.query('delete from ops.requirement_upload_receipt; update ops.requirement_upload_quota set reserved_bytes=0');
    });
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
      assert.equal(Number((await c.query('select reserved_bytes from ops.requirement_upload_quota')).rows[0].reserved_bytes), [...objects.values()].reduce((n, b) => n + b.length, 0));
      const lying = await grant();
      assert.equal((await upload(lying, { headers: { 'content-length': '5' }, visitor: '192.0.2.3' })).status, 413);
      const short = await grant();
      assert.equal((await upload(short, { headers: { 'content-length': '99' }, visitor: '192.0.2.4' })).status, 413);
    });
    await t.test('storage and receipt failures, expiry and revocation refund the byte reservation', async () => {
      for (const outcome of ['put', 'receipt', 'expired', 'revoked']) {
        const before = objects.size;
        const quota = Number((await c.query('select reserved_bytes from ops.requirement_upload_quota')).rows[0].reserved_bytes);
        const g = await grant();
        const failing = buildHandler(c, async (key, bytes) => {
          if (outcome === 'put') throw new Error('synthetic storage failure');
          objects.set(key, Buffer.from(bytes));
          if (outcome === 'receipt') await c.query(`create function pg_temp.reject_receipt() returns trigger language plpgsql as $$begin raise exception 'synthetic receipt failure'; end$$;
            create trigger reject_receipt before insert on ops.requirement_upload_receipt for each row execute function pg_temp.reject_receipt()`);
          if (outcome === 'expired') await c.query("update ops.requirement_upload_grant set created_at=now()-interval '2 hours',expires_at=now()-interval '1 hour' where id=$1", [g.grant_id]);
          if (outcome === 'revoked') await call('revoke-requirement-upload', { grant_id: g.grant_id });
        });
        try {
          const response = await failing(new Request(`https://share.doctorcre.com${g.upload_path}`, { method: 'POST', body: pdf,
            headers: { authorization: `Bearer ${g.token}`, 'content-type': 'application/pdf', 'content-length': String(pdf.length), 'cf-connecting-ip': `synthetic-${outcome}` } }));
          assert.equal(response.status, ['expired', 'revoked'].includes(outcome) ? 403 : 503);
          assert.equal(objects.size, before);
          assert.equal(Number((await c.query('select reserved_bytes from ops.requirement_upload_quota')).rows[0].reserved_bytes), quota);
        } finally { if (outcome === 'receipt') await c.query('drop trigger reject_receipt on ops.requirement_upload_receipt'); }
      }
    });
    await t.test('lost receipt confirmation preserves the uploaded object and quota', async () => {
      const g = await grant();
      const before = Number((await c.query('select reserved_bytes from ops.requirement_upload_quota')).rows[0].reserved_bytes);
      let transactions = 0;
      const lostConfirmation = createRequirementUploadHandler({ visitorSecret: 'synthetic-visitor-secret',
        bucket: { put: async (key, bytes) => objects.set(key, Buffer.from(bytes)), delete: async key => objects.delete(key) },
        transaction: async fn => {
          await c.query('begin');
          try {
            const result = await fn(c);
            await c.query('commit');
            if (++transactions === 4) throw new Error('synthetic lost confirmation');
            return result;
          } catch (error) { await c.query('rollback'); throw error; }
        },
      });
      const response = await lostConfirmation(new Request(`https://share.doctorcre.com${g.upload_path}`, { method: 'POST', body: pdf,
        headers: { authorization: `Bearer ${g.token}`, 'content-type': 'application/pdf', 'content-length': String(pdf.length), 'cf-connecting-ip': '192.0.2.88' } }));
      assert.equal(response.status, 503);
      const receipt = (await c.query('select r2_key from ops.requirement_upload_receipt where grant_id=$1', [g.grant_id])).rows[0];
      assert.ok(objects.has(receipt.r2_key));
      assert.equal(Number((await c.query('select reserved_bytes from ops.requirement_upload_quota')).rows[0].reserved_bytes), before + pdf.length);
    });
    await t.test('per-visitor write reservations stop the third attempt and leave another visitor independent', async () => {
      const visitor = '192.0.2.20';
      for (let i = 0; i < 2; i++) assert.equal((await upload(await grant(), { visitor })).status, 201);
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
      assert.equal((await upload(await grant(), { visitor: '192.0.2.30' })).status, 503);
      assert.equal((await upload(await grant(), { visitor: '192.0.2.30' })).status, 429);
    });
    await t.test('request creation refuses stale doctrine, critical dates and authoritative invoice milestones', async () => {
      const args = { deal, owner: 'joe', due_on: '2026-11-15' };
      for (const row_key of ['critical-date:synthetic', 'invoice_recorded', 'not_a_row'])
        await assert.rejects(call('request-checklist-document', { ...args, row_key }), /row_not_requestable/);
      await c.query("update doctrine_section set body_hash='stale' where id=$1", [CHECKLIST_SOURCES[0].section_id]);
      await assert.rejects(call('request-checklist-document', { ...args, row_key: 'appraisal' }), /row_not_requestable/);
    });
  } finally { try { await c.end(); } finally { await fixture.close(); } }
});
