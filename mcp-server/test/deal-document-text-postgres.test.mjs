import test from 'node:test';
import { execFileSync } from 'node:child_process';
import { unattendedActorForToken } from '../src/identity.js';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { Validator } from '@cfworker/json-schema';
import { publicError } from '../src/public-errors.ts';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const partner = { id: '00000000-0000-4000-8000-000000000001', slug: 'joe', human: true, via: 'oauth' };
const robot = { id: '00000000-0000-4000-8000-000000000002', slug: 'codex', human: false, sponsoring_human_slug: 'joe' };
const deal = '00000000-0000-4000-8000-000000000003';
const doc = '00000000-0000-4000-8000-000000000004';
const hash = createHash('sha256').update('synthetic original PDF bytes').digest('hex');
const fixtureRoot = new URL('../../docs/contracts/deal-document-text.v1/', import.meta.url);
const schema = JSON.parse(readFileSync(new URL('response.schema.json', fixtureRoot)));
const validate = new Validator(schema);
const bound = { deal_id: deal, attachment_id: doc };
const retained = { document_ref: doc, entity_ref: deal, content_hash: hash,
  text_format_version: 'page-text.v1', status: 'produced', pages: ['first\n\nlast\n', '😀second'] };

test('retained text lifecycle and authenticated registered contract', async t => {
  const db = await postgresDatabase('document-text-');
  const c = db.client;
  const invoke = async (verb, args, actor = partner) => {
    await c.query(`set role ${verb === 'record-deal-document-text' ? 'carr_writer' : 'carr_reader'}`);
    try { return await executeRegisteredTool(c, actor, verb, args); }
    finally { await c.query('reset role'); }
  };
  const read = async (args = {}) => {
    const value = await invoke('read-deal-document-text', { ...bound, ...args });
    assert.deepEqual(validate.validate(value).errors, []);
    return value;
  };
  const record = (args = {}) => invoke('record-deal-document-text', { ...retained, ...args }, robot);
  const refuses = (promise, error) => assert.rejects(promise, e => {
    if (['unauthenticated', 'document_not_found', 'source_changed', 'content_hash_required', 'invalid_position', 'derivative_conflict'].includes(error))
      assert.equal(publicError(e).error, error);
    return e.payload?.error === error;
  });
  const fixture = (name, value) => {
    const target = new URL(`${name}.json`, fixtureRoot);
    if (process.argv.includes('--record-fixtures')) writeFileSync(target, JSON.stringify(value, null, 2) + '\n');
    else assert.deepEqual(value, JSON.parse(readFileSync(target)));
  };
  try {
    const source = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
    for (const name of ['actor', 'attachment']) {
      const sql = source.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(sql); await c.query(sql);
      await c.query(`alter table ${name} add primary key(id)`);
    }
    await c.query('create schema ops; create role carr_reader; create role carr_writer; grant usage on schema ops to carr_reader,carr_writer');
    await c.query('grant select(id,slug) on public.actor to carr_reader');
    await c.query(`create function ops.scac_current_grant_contract() returns jsonb language sql immutable as $$select '{"secdef_execute":[]}'::jsonb$$`);
    await c.query('create table v_ref_index(subject_type text,subject_id uuid)');
    await c.query(readFileSync(new URL('../../migrations/1086_deal_documents.sql', import.meta.url), 'utf8'));
    await c.query(readFileSync(new URL('../../migrations/1164_deal_document_text.sql', import.meta.url), 'utf8'));
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic'),($2,'codex','automation','Synthetic')", [partner.id, robot.id]);
    await c.query("insert into v_ref_index values('deal',$1)", [deal]);
    await c.query("insert into attachment(id,subject_type,subject_id,r2_key,filename,mime,sha256,bytes,created_by) values($1,'deal',$2,'documents/synthetic.pdf','Synthetic.pdf','application/pdf',$3,10,$4)", [doc, deal, hash, partner.id]);
    await t.test('slug-only automation resolves on the stateless production path', async () => {
      const { id: _robotId, ...readerRobot } = robot;
      const sources = await invoke('list-deal-document-text-sources', { limit: 1 }, readerRobot);
      assert.equal(sources.documents[0].document_ref, doc);
    });
    await t.test('slug-only partner resolves on the stateless production path', async () => {
      const { id: _partnerId, ...readerPartner } = partner;
      const value = await invoke('read-deal-document-text', bound, readerPartner);
      assert.deepEqual(validate.validate(value).errors, []);
      assert.equal(value.reason, 'not_produced');
    });
    await t.test('nonexistent attachment exposes a typed public refusal', async () => {
      try {
        await invoke('read-deal-document-text', { ...bound, attachment_id: '00000000-0000-0000-0000-000000000000' });
        assert.fail('nonexistent attachment must refuse');
      } catch (error) {
        assert.equal(publicError(error).error, 'document_not_found');
      }
    });
    await t.test('absent returns unavailable', async () => fixture('not-produced', await read()));
    await t.test('absent to produced and immutable replay', async () => {
      const first = await record();
      assert.deepEqual(await record(), first);
      fixture('produced', await read({ max_characters: 5 }));
      await refuses(record({ pages: ['changed'] }), 'derivative_conflict');
    });
    await t.test('unattended and non-partner registered readers refuse retained text', async () => {
      const unattended = unattendedActorForToken('Bearer fixture', JSON.stringify({ joe: { nightly: 'fixture' } }));
      unattended.id = robot.id;
      await refuses(invoke('read-deal-document-text', bound, unattended), 'unattended_runner_authority_not_admitted');
      await refuses(invoke('read-deal-document-text', bound, robot), 'human_only_verb_requires_verified_partner');
    });
    await t.test('Python producer whitespace persists through the registered writer', async () => {
      const pages = ['\u00a0', '\u001c\u001d\u001e\u001f', '\u0085\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000', ' \t\n\r\v\f'];
      const unicodeHash = 'a'.repeat(64);
      const payload = JSON.parse(execFileSync(process.env.CARR_CI_PYTHON || 'python3', ['-c',
        'import json,sys; from lib.deal_document_text import retained_pages; print(json.dumps(retained_pages(*json.load(sys.stdin))))'],
        { input: JSON.stringify([{ ...retained, content_hash: unicodeHash }, pages]), cwd: new URL('../..', import.meta.url), encoding: 'utf8' }));
      assert.equal(payload.status, 'no_text');
      await c.query('update attachment set sha256=$1 where id=$2', [unicodeHash, doc]);
      try {
        await invoke('record-deal-document-text', payload, robot);
        assert.equal((await read()).status, 'no_text');
        const stored = await c.query('select pages from ops.deal_document_text where content_hash=$1', [unicodeHash]);
        assert.deepEqual(stored.rows[0].pages, pages);
        const visibleHash = 'f'.repeat(64);
        await c.query('update attachment set sha256=$1 where id=$2', [visibleHash, doc]);
        await record({ content_hash: visibleHash, status: 'produced', pages: ['\u200b'] });
        assert.equal((await read()).status, 'produced');
      } finally { await c.query('update attachment set sha256=$1 where id=$2', [hash, doc]); }
    });
    await t.test('pagination preserves identity and complete page and line text', async () => {
      let next = {}, chunks = [], identity;
      do {
        const value = await read({ ...next, max_characters: 2 });
        const current = [value.deal_id,value.attachment_id,value.content_hash,value.text_hash,value.text_format_version];
        if (identity) assert.deepEqual(current, identity);
        identity = current;
        chunks.push(...value.lines); next = value.next;
      } while (next);
      const pages = retained.pages.map((p, n) => p.split('\n').map((line, i) =>
        chunks.filter(c => c.page === n + 1 && c.line === i + 1).map(c => c.text).join('')).join('\n'));
      assert.deepEqual(pages, retained.pages);
      await refuses(read({ page: 1 }), 'content_hash_required');
      await refuses(read({ content_hash: hash, page: 999 }), 'invalid_position');
    });
    await t.test('not in deal, hidden deal, unauthenticated and partner write refusal', async () => {
      await refuses(invoke('read-deal-document-text', { ...bound, deal_id: doc }), 'document_not_found');
      await c.query('delete from v_ref_index');
      await refuses(read(), 'document_not_found');
      await c.query("insert into v_ref_index values('deal',$1)", [deal]);
      await refuses(TOOLS['read-deal-document-text'].handler(c, null, bound), 'unauthenticated');
      await refuses(TOOLS['read-deal-document-text'].handler(c, { ...partner, id: undefined, slug: 'unknown' }, bound), 'unauthenticated');
      await refuses(invoke('record-deal-document-text', retained), 'not_in_profile');
      await refuses(invoke('list-deal-document-text-sources', { limit: 100 }), 'not_in_profile');
    });
    await t.test('source scan includes retained flag and keyset bounds', async () => {
      const result = await invoke('list-deal-document-text-sources', { limit: 1 }, robot);
      assert.equal(result.documents[0].retained, true);
      await c.query('update attachment set bytes=26214401 where id=$1', [doc]);
      const oversized = await invoke('list-deal-document-text-sources', { limit: 1 }, robot);
      assert.equal(oversized.documents[0].bytes, 26214401);
      await c.query('update attachment set bytes=10 where id=$1', [doc]);
      assert.equal((await invoke('list-deal-document-text-sources', { limit: 1, after: doc }, robot)).documents.length, 0);
    });
    for (const initial of ['produced', 'no_text', 'unavailable']) {
      await t.test(`${initial} to superseded and new produced or no-text`, async () => {
        const newHash = initial[0].repeat(64).replaceAll('p','b').replaceAll('n','c').replaceAll('u','d');
        await c.query('update attachment set sha256=$1 where id=$2', [newHash, doc]);
        await refuses(read({ content_hash: hash }), 'source_changed');
        assert.equal((await read()).reason, 'not_produced');
        await refuses(record(), 'source_changed');
        await record({ content_hash: newHash, status: initial, pages: initial === 'produced' ? retained.pages : initial === 'no_text' ? ['', ''] : [] });
        const value = await read();
        assert.equal(value.status, initial);
        if (initial !== 'produced') fixture(initial, value);
        await record({ content_hash: newHash, status: initial, pages: initial === 'produced' ? retained.pages : initial === 'no_text' ? ['', ''] : [] });
      });
    }
    await t.test('every produced and unavailable supersession transition', async () => {
      for (const oldState of ['produced', 'no_text', 'unavailable']) {
        for (const newState of ['produced', 'no_text', 'unavailable']) {
          const priorHash = createHash('sha256').update('prior' + oldState + newState).digest('hex');
          const nextHash = createHash('sha256').update('next' + oldState + newState).digest('hex');
          const pages = state => state === 'produced' ? ['complete'] : state === 'no_text' ? [''] : [];
          await c.query('update attachment set sha256=$1 where id=$2', [priorHash, doc]);
          await record({ content_hash: priorHash, status: oldState, pages: pages(oldState) });
          await c.query('update attachment set sha256=$1 where id=$2', [nextHash, doc]);
          await refuses(read({ content_hash: priorHash }), 'source_changed');
          assert.equal((await read()).reason, 'not_produced');
          await record({ content_hash: nextHash, status: newState, pages: pages(newState) });
          assert.equal((await read()).status, newState);
        }
      }
    });
    await t.test('all derivative states to deleted refuse reads and writes', async () => {
      for (const currentHash of [hash, 'b'.repeat(64), 'c'.repeat(64), 'd'.repeat(64), 'e'.repeat(64)]) {
        await c.query('update attachment set sha256=$1,deleted_at=now() where id=$2', [currentHash, doc]);
        await refuses(read(), 'document_not_found');
        await refuses(record({ content_hash: currentHash }), 'document_not_found');
        await c.query('update attachment set deleted_at=null where id=$1', [doc]);
      }
    });
  } finally { await db.close(); }
});
