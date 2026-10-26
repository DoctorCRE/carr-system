// Test standing-context territory text and provenance.
import test from 'node:test';
import assert from 'node:assert/strict';
import { executeRegisteredTool } from '../src/tools.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';

const SOURCE = 'aa3868ef-d0a2-4bc2-9954-525ad418636b';
const ACTOR = { id: '11111111-1111-4111-8111-111111111111', slug: 'joe', human: true, via: 'oauth-google' };

async function territoryFixture() {
  const fixture = await postgresDatabase('standing-territory-');
  try {
    await fixture.client.query(`
      create table v_compiled_rules(id uuid, statement text, human_quote text, taught_by text,
        personal_to text, scope jsonb, activated_at timestamptz);
      create table loop_item(number integer, title text, body text, owner text, render_seq integer, kind text, status text);
      create table doctrine_meta(id integer, generation bigint);
      insert into doctrine_meta values (1,42);
      create table v_defect_class(defect_class text, occurrences integer, caught_by_human integer,
        first_seen timestamptz, last_seen timestamptz, sources_unread jsonb);
      create table doctrine_document(id uuid primary key, slug text, visibility text);
      create table doctrine_section(id uuid primary key, document_id uuid, section_key text, title text,
        status text, current_revision_id uuid, current_version bigint, review_after timestamptz);
      create table doctrine_revision(id uuid primary key, section_id uuid, body jsonb, content_hash text);
      insert into doctrine_document values ('20000000-0000-4000-8000-000000000001','carr-profile','shared');
      insert into doctrine_section values ('${SOURCE}','20000000-0000-4000-8000-000000000001',
        'our-territory','Our Territory','active','30000000-0000-4000-8000-000000000001',7,'2099-01-01Z');
      insert into doctrine_revision values ('30000000-0000-4000-8000-000000000001','${SOURCE}',
        '{"text":"Fixture coverage: Market Alpha through Market Beta. Key markets: Alpha, Beta."}','fixture-hash-seven');
    `);
    const client = {
      query: (sql, params) => /v_guidance_registry_state|from rule\s+where status = 'proposed'/.test(sql)
        ? Promise.resolve({ rows: [] }) : fixture.client.query(sql, params),
    };
    return { ...fixture, client, database: fixture.client };
  } catch (error) { await fixture.close(); throw error; }
}

test('standing-context includes the current canonical territory text and provenance', async () => {
  const fixture = await territoryFixture();
  try {
    for (const args of [{}, { detail: 'full' }]) {
      const result = await executeRegisteredTool(fixture.client, ACTOR, 'standing-context', args);
      const { review_after, ...territory } = result.territory || {};
      assert.deepEqual(territory, {
        status: 'available', section_id: SOURCE, doc_slug: 'carr-profile', section_key: 'our-territory',
        title: 'Our Territory', version: 7,
        body: { text: 'Fixture coverage: Market Alpha through Market Beta. Key markets: Alpha, Beta.' },
        content_hash: 'fixture-hash-seven',
      });
      assert.equal(new Date(review_after).toISOString(), '2099-01-01T00:00:00.000Z');
      assert.equal(result.doctrine.generation, 42);
      assert.deepEqual(result.shared_rules, []);
    }
  } finally { await fixture.close(); }
});

test('standing-context follows the current revision rather than history or a copied market list', async () => {
  const fixture = await territoryFixture();
  try {
    await fixture.database.query(`
      insert into doctrine_revision values
        ('30000000-0000-4000-8000-000000000002','${SOURCE}','{"text":"Fixture coverage now includes Market Gamma."}','fixture-hash-eight'),
        ('30000000-0000-4000-8000-000000000003','${SOURCE}','{"text":"Unselected historical territory."}','history-hash');
      update doctrine_section set current_revision_id='30000000-0000-4000-8000-000000000002',current_version=8;
      update doctrine_document set slug='renamed-carr-profile';
    `);
    const result = await executeRegisteredTool(fixture.client, ACTOR, 'standing-context', {});
    assert.equal(result.territory.status, 'available');
    assert.deepEqual(result.territory.body, { text: 'Fixture coverage now includes Market Gamma.' });
    assert.equal(result.territory.version, 8);
    assert.equal(result.territory.content_hash, 'fixture-hash-eight');
    assert.equal(result.territory.doc_slug, 'renamed-carr-profile');
  } finally { await fixture.close(); }
});

test('standing-context explicitly reports unavailable territory without leaking noncurrent or personal text', async t => {
  const fixture = await territoryFixture();
  try {
    const cases = [
      ['retired', "update doctrine_section set status='retired'"],
      ['personal', "update doctrine_document set visibility='personal'"],
      ['missing revision', "update doctrine_section set current_revision_id=null"],
      ['foreign revision', `update doctrine_revision set section_id='40000000-0000-4000-8000-000000000001'`],
      ['missing section', 'delete from doctrine_section'],
    ];
    for (const [name, mutation] of cases) {
      await t.test(name, async () => {
        await fixture.database.query('begin');
        try {
          await fixture.database.query(mutation);
          const result = await executeRegisteredTool(fixture.client, ACTOR, 'standing-context', {});
          assert.deepEqual(result.territory, { status: 'unavailable', section_id: SOURCE });
          assert.equal(result.ok, true);
        } finally { await fixture.database.query('rollback'); }
      });
    }
  } finally { await fixture.close(); }
});

test('territory database failure stays closed while boot and scopes keep their existing read contracts', async () => {
  const fixture = await territoryFixture();
  try {
    await fixture.database.query('drop table doctrine_revision');
    await assert.rejects(executeRegisteredTool(fixture.client, ACTOR, 'standing-context', {}),
      /database_refused_the_statement/);
    const boot = await executeRegisteredTool(fixture.client, ACTOR, 'standing-context', { detail: 'boot' });
    assert.equal(boot.ok, true);
    assert.ok(boot.rule_boot);
    assert.equal(boot.territory, undefined);
    const scopes = await executeRegisteredTool(fixture.client, ACTOR, 'standing-context', { detail: 'scopes' });
    assert.equal(scopes.schema, 'rule-scope-catalog/v1');
    assert.deepEqual(scopes.rules, []);
    assert.equal(scopes.territory, undefined);
  } finally { await fixture.close(); }
});
