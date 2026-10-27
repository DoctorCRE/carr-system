import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { executeRegisteredTool } from '../src/tools.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { restoreEventIdentity } from './helpers/snapshot-schema.mjs';

const robot = { id: randomUUID(), slug: 'outlook-intake', human: false };
const PDF = 'application/pdf';
const dealId = randomUUID();
const otherDeal = randomUUID();
let fixture, c;
before(async () => {
  fixture = await postgresDatabase('deal-files-');
  c = fixture.client;
  const schema = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
  for (const name of ['actor', 'attachment', 'event', 'tool_call']) {
    const definition = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
    assert.ok(definition, name);
    await c.query(definition);
  }
  await restoreEventIdentity(c, schema);
  await c.query('alter table tool_call add primary key(idempotency_key)');
  await c.query('alter table attachment add primary key(id)');
  await c.query('create table v_ref_index(subject_type text, subject_id uuid, ref text, display_name text, status text, client_ref text, city text)');
  await c.query("insert into v_ref_index values('deal',$1,'D-1','Synthetic Deal A','open','C-1',null),('deal',$2,'D-2','Synthetic Deal B','open','C-1',null),('lead',$3,'L-1','Synthetic Lead','open',null,null)",
    [dealId, otherDeal, randomUUID()]);
  await c.query("insert into actor(id,slug,kind,display_name,active) values($1,$2,'automation',$2,true)", [robot.id, robot.slug]);
});
after(async () => fixture?.close());

function file(name = 'Lease.pdf', body = 'synthetic one') {
  const sha256 = createHash('sha256').update(body).digest('hex');
  return { deal: dealId, filename: name, mime: PDF, sha256, bytes: body.length,
    r2_key: `documents/C-1/${sha256.slice(0, 16)}/${name}`, source: 'msg-1/att-1' };
}
async function attach(args, key = randomUUID()) {
  await c.query('begin');
  try {
    const result = await executeRegisteredTool(c, robot, 'attach-deal-file', { idempotency_key: key, ...args });
    await c.query('commit');
    return result;
  } catch (error) {
    await c.query('rollback');
    throw error;
  }
}
const refusal = (args, error) => assert.rejects(attach(args), e => e.payload?.error === error || e.error === error || e.message.includes(error));

test('stores the file against the matched deal and writes one event', async () => {
  const args = file();
  const result = await attach(args);
  assert.equal(result.existing, false);
  const row = (await c.query('select * from attachment where id=$1', [result.attachment_id])).rows[0];
  assert.deepEqual([row.subject_type, row.subject_id, row.r2_key, row.filename, row.mime, row.sha256, Number(row.bytes), row.created_by],
    ['deal', dealId, args.r2_key, 'Lease.pdf', PDF, args.sha256, args.bytes, robot.id]);
  const events = (await c.query("select cause, agent_rationale from event where verb='attach-deal-file' and subject_id=$1", [dealId])).rows;
  assert.deepEqual(events, [{ cause: 'ingest_email', agent_rationale: 'email attachment msg-1/att-1' }]);
});

test('a replay with a new key returns the same row and writes nothing more', async () => {
  const before = (await c.query('select count(*)::int n from attachment')).rows[0].n;
  const result = await attach(file());
  assert.equal(result.existing, true);
  assert.equal((await c.query('select count(*)::int n from attachment')).rows[0].n, before);
  assert.equal((await c.query("select count(*)::int n from event where verb='attach-deal-file'")).rows[0].n, 1);
});

test('the same bytes under a different name on the same deal are not filed twice', async () => {
  const same = file('Copy of Lease.pdf');
  assert.equal((await attach({ ...same, r2_key: `documents/C-1/${same.sha256.slice(0, 16)}/Copy_of_Lease.pdf` })).existing, true);
});

test('refuses a key already filed on another deal', async () => {
  await refusal({ ...file(), deal: otherDeal }, 'r2_key_on_other_record');
});

test('the same bytes may be filed on a second deal under their own key', async () => {
  const args = file();
  const result = await attach({ ...args, deal: otherDeal, r2_key: `documents/C-9/${args.sha256.slice(0, 16)}/Lease.pdf` });
  assert.equal(result.existing, false);
});

test('refuses a subject that is not a deal', async () => {
  await refusal({ ...file('Lead.pdf', 'lead body'), deal: 'L-1' }, 'not_a_deal');
});

test('refuses malformed metadata before touching the table', async () => {
  const good = file('Ok.pdf', 'ok body');
  await refusal({ ...good, sha256: 'ABC' }, 'invalid_sha256');
  await refusal({ ...good, bytes: 26 * 1024 * 1024 }, 'invalid_bytes');
  await refusal({ ...good, bytes: 0 }, 'invalid_bytes');
  await refusal({ ...good, mime: 'image/png' }, 'unsupported_mime');
  await refusal({ ...good, filename: '../x.pdf' }, 'invalid_filename');
  await refusal({ ...good, r2_key: `documents/C-1/${'0'.repeat(16)}/Ok.pdf` }, 'invalid_r2_key');
  await refusal({ ...good, r2_key: `documents/../${good.sha256.slice(0, 16)}/Ok.pdf` }, 'invalid_r2_key');
  await refusal({ ...good, r2_key: `documents/C-1/${good.sha256.slice(0, 16)}/..` }, 'invalid_r2_key');
  await refusal({ ...good, source: 'x'.repeat(201) }, 'invalid_source');
  assert.equal((await c.query("select count(*)::int n from attachment where sha256=$1", [good.sha256])).rows[0].n, 0);
});
