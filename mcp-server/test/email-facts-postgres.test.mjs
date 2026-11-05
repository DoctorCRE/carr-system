// Prove that the email adapter cannot bypass installed canonical fact authority.
import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';

const dsn = await snapshotDatabase('email-facts-');
const source = value => JSON.parse(execFileSync('python3', ['-c', `
from lib.email_facts import EmailFactSource, adapt_email_facts
from uuid import UUID
import json
body = 'Term is ${value} months.'
fields = [{'fact_key': key, 'status': 'not_present', 'reason': 'absent'}
          for key in ('base_rent_per_sf', 'landlord_ti_per_sf', 'free_rent_months')]
fields += [{'fact_key': 'term_months', 'status': 'observed', 'value': '${value}',
            'unit': 'months', 'page': 1, 'cited_text': body, 'region': 'scalar'}]
print(json.dumps(adapt_email_facts(EmailFactSource(
    UUID('00000000-0000-4000-8000-000000000139'),
    UUID('00000000-0000-4000-8000-000000000140'), '${value}', body, True), {'fields': fields})))
`], { cwd: new URL('../../', import.meta.url), encoding: 'utf8' }));

test('email evidence retries once, preserves human terms and remains tenant-scoped', async () => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect();
  try {
    const human = randomUUID(), job = randomUUID();
    await c.query(`insert into actor(id,slug,kind,display_name) values
      ($1,$2,'human','Synthetic reviewer'),($3,$4,'automation','Synthetic extractor')`,
      [human, `human-${human}`, job, `job-${job}`]);
    const record = async batch => (await c.query('select ops.record_fact_observations($1,$2,$3,$4,$5::jsonb) r',
      ['carr-internal', job, batch.idempotency_key, batch.entity_ref, JSON.stringify(batch.observations)])).rows[0].r;
    const batch = source('60');
    const first = await record(batch), retry = await record(batch);
    assert.equal(first.ok, true);
    assert.equal(retry.ok, true);
    assert.equal(retry.batch_id, first.batch_id);
    assert.equal(first.observations.length, 1);
    const promote = async (actor, basis, ids, version) => (await c.query(
      'select ops.promote_fact($1,$2,$3,$4,$5,$6,$7::uuid[],$8) r',
      ['carr-internal', actor, randomUUID(), batch.entity_ref, 'term_months', basis, ids, version])).rows[0].r;
    const id = first.observations[0].id;
    assert.equal((await promote(job, 'independent_agreement', [id], 0)).ok, false);
    assert.equal((await promote(human, 'human_review', [id], 0)).ok, true);
    const correction = await record(source('72'));
    assert.equal(correction.ok, true);
    assert.equal((await promote(job, 'independent_agreement', [correction.observations[0].id], 1)).ok, false);
    const read = async tenant => (await c.query('select ops.list_fact_review($1,$2,100,null,true) r',
      [tenant, batch.entity_ref])).rows[0].r;
    const review = await read('carr-internal');
    assert.equal(review.items.length, 1);
    assert.equal(review.items[0].pending, true);
    assert.equal(review.items[0].canonical_fact.normalized_value, '60');
    assert.equal(review.items[0].canonical_history.length, 1);
    assert.equal(review.items[0].observations.length, 2);
    assert.deepEqual((await read('another-tenant')).items, []);
  } finally { await c.end(); }
});
