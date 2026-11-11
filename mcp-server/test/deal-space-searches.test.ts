// Exercise saved briefs through registered verbs on disposable PostgreSQL.
import assert from 'node:assert/strict';
import test from 'node:test';
import { writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';

const filters = { query: null, counties: ['Escambia'], property_types: [], min_square_feet: 2000,
  max_square_feet: 8000, availability: [], entrance_verified: null, public_projection_ready: null,
  photos_available: null, sort: 'updated_desc', cursor: null, limit: 100 };

test('saved search lifecycle, canonical run, reopen, conflict, archive and authority', async () => {
  const dsn = await snapshotDatabase('deal-space-search-');
  const c = new pg.Client({ connectionString: dsn });
  await c.connect();
  try {
    const actor = (await c.query(`insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner')
      on conflict(slug) do update set active=true returning id`)).rows[0];
    Object.assign(actor, { slug: 'joe', human: true });
    const party = (await c.query(`insert into party(kind,name,created_by,updated_by) values('person','Synthetic client',$1,$1) returning id`, [actor.id])).rows[0].id;
    const client = (await c.query('insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id', [party,actor.id])).rows[0].id;
    const deal = (await c.query(`insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by)
      values($1,'Synthetic search','other','legal','joe',$2,$2) returning id`, [client,actor.id])).rows[0].id;
    const invoke = async (verb: string, args: object, who = actor) => {
      await c.query('begin');
      try {
        const value = await executeRegisteredTool(c, who, verb, args);
        await c.query('commit');
        return value;
      } catch (e) { await c.query('rollback'); throw e; }
    };
    const search_id = randomUUID();
    const save = { idempotency_key: randomUUID(), deal_id: deal, search_id, expected_version: 0,
      name: 'Medical space', status: 'draft', criteria: filters,
      requirements: 'Lease, budget $30/SF/year NNN, occupy next summer. Parking and ground floor required.',
      client_prefill_source: { client_id: client, source_ref: 'client-intake:synthetic', observed_at: '2026-11-10T00:00:00Z' } };
    const created = await invoke('save-deal-space-search-v1', save);
    assert.equal(created.search.state, 'draft');
    assert.equal(created.search.version, 1);
    assert.deepEqual(await invoke('save-deal-space-search-v1', save), created, 'idempotent create');
    const saved = await invoke('save-deal-space-search-v1', {...save,idempotency_key:randomUUID(),expected_version:1,status:'saved'});
    assert.equal(saved.search.state,'saved');
    assert.equal(saved.search.last_run_at,null);
    const run = await invoke('run-deal-space-search-v1', {idempotency_key:randomUUID(),deal_id:deal,search_id,expected_version:2});
    assert.equal(run.search.version,3);
    assert.ok(run.search.last_run_at);
    assert.deepEqual(run.search.result_property_ids,[]);
    assert.equal(run.search.has_more,false);
    const reopened = await invoke('read-deal-space-searches-v1', {deal_id:deal,search_id});
    assert.deepEqual(reopened.searches,[run.search]);
    assert.equal((await invoke('read-deal-space-searches-v1', {deal_id:deal,search_id:null})).searches.length,1);
    await assert.rejects(invoke('save-deal-space-search-v1', {...save,idempotency_key:randomUUID(),expected_version:1}), /space_search_version_conflict/);
    const stale = await invoke('save-deal-space-search-v1', {...save,idempotency_key:randomUUID(),expected_version:3,status:'saved',criteria:{...filters,min_square_feet:3000}});
    assert.equal(stale.search.state,'stale');
    assert.equal(stale.search.last_run_at,run.search.last_run_at);
    const archived = await invoke('save-deal-space-search-v1', {...save,idempotency_key:randomUUID(),expected_version:4,status:'archived'});
    assert.equal(archived.search.state,'archived');
    await assert.rejects(invoke('run-deal-space-search-v1', {idempotency_key:randomUUID(),deal_id:deal,search_id,expected_version:5}), /space_search_not_runnable/);
    const restored = await invoke('save-deal-space-search-v1', {...save,idempotency_key:randomUUID(),expected_version:5,status:'saved'});
    assert.equal(restored.search.state,'saved');
    for (const name of ['save-deal-space-search-v1','run-deal-space-search-v1','read-deal-space-searches-v1'] as const) {
      assert.equal(TOOLS[name].authorityOnly,true);
      await assert.rejects(invoke(name, {}, {id:randomUUID(),slug:'unauthorized',human:false}), /human_only/);
    }
    await c.query('update party set deleted_at=now() where id=$1',[party]);
    await assert.rejects(invoke('read-deal-space-searches-v1',{deal_id:deal,search_id}),/space_search_unavailable/);
    if (process.env.RECORD_SPACE_SEARCH_FIXTURE) writeFileSync(process.env.RECORD_SPACE_SEARCH_FIXTURE,JSON.stringify({created,saved,run,reopened,stale,archived,restored},null,2)+'\n');
  } finally { await c.end(); }
});
