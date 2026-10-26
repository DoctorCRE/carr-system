// Tests complete decision reads by identifier and prefix.
import assert from 'node:assert/strict';
import test from 'node:test';
import { TOOLS } from '../src/tools.js';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { readMigration } from './migration-source.testhelper.mjs';

const decisionId = '4825f2a3-0000-4000-8000-000000000001';
const eventId = '10000000-0000-4000-8000-000000000001';
const actorId = '20000000-0000-4000-8000-000000000001';
const rationale = 'Synthetic reasoning with unicode café. '.repeat(30) + 'The final paragraph must survive.';

test('read-decision returns one complete recorded decision', async t => {
  const fixture = await postgresDatabase('read-decision-');
  const c = fixture.client;
  try {
    await c.query(`
      create table actor(id uuid primary key, slug text);
      create table event(id uuid primary key, subject_id uuid, actor_id uuid,
        verb text, occurred_at timestamptz, recorded_at timestamptz default now(),
        new_value jsonb, human_quote text, agent_rationale text, cause text);
      create table record_source(external_key text, entity_id uuid, entity_type text, source_system text);
      create role carr_reader;
    `);
    const migration = readMigration('0110_decision_entry_occurred_at.sql');
    await c.query(migration.slice(migration.indexOf('create or replace view v_decision_entry as'),
      migration.indexOf('comment on view v_decision_entry is')));
    await c.query('grant select on v_decision_entry to carr_reader');
    await c.query('insert into actor values($1, $2)', [actorId, 'synthetic-partner']);
    await c.query(`insert into event(id,subject_id,actor_id,verb,occurred_at,new_value,human_quote,agent_rationale,cause)
      values($1,$2,$3,'log-decision','2026-08-01T12:00:00Z',$4,$5,$6,'human_request')`,
    [eventId, decisionId, actorId, JSON.stringify({title:'Synthetic full decision', provenance:'synthetic fixture',
      quote_absent:false, cost_delta:'one synthetic unit', quality_delta:'complete rationale'}),
    'Keep the complete reasoning.', rationale]);
    await c.query(`insert into record_source values('synthetic#session-one',$1,'event','decision-history')`, [eventId]);

    await t.test('full UUID preserves rationale, human quote and provenance without truncation', async () => {
      await c.query('set role carr_reader');
      const out = await TOOLS['read-decision'].handler(c, {}, {decision_id:decisionId});
      await c.query('reset role');
      assert.equal(out.ok, true);
      assert.equal(out.decision.agent_rationale, rationale);
      assert.equal(out.decision.title, 'Synthetic full decision');
      assert.equal(out.decision.human_quote, 'Keep the complete reasoning.');
      assert.equal(out.decision.provenance, 'synthetic fixture');
      assert.equal(out.decision.decision_id, decisionId);
      assert.equal(out.decision.event_id, eventId);
      assert.equal(out.decision.author, 'synthetic-partner');
      assert.equal(out.decision.session_key, 'session-one');
      assert.equal(out.decision.cost_delta, 'one synthetic unit');
      assert.equal(out.decision.quality_delta, 'complete rationale');
    });

    await t.test('an eight-character prefix reads the same decision and latest corrected text', async () => {
      await c.query('update event set agent_rationale=$1 where id=$2', ['Corrected synthetic rationale.', eventId]);
      await c.query('set role carr_reader');
      try {
        const out = await TOOLS['read-decision'].handler(c, {}, {decision_id:' 4825F2A3 '});
        assert.equal(out.decision.decision_id, decisionId);
        assert.equal(out.decision.agent_rationale, 'Corrected synthetic rationale.');
      } finally { await c.query('reset role'); }
    });

    await t.test('missing full IDs and prefixes return decision_not_found', async () => {
      await c.query('set role carr_reader');
      try {
        for (const decision_id of ['ffffffff-0000-4000-8000-000000000001', 'ffffffff', eventId]) {
          await assert.rejects(TOOLS['read-decision'].handler(c, {}, {decision_id}),
            error => error.payload?.error === 'decision_not_found');
        }
      } finally { await c.query('reset role'); }
    });

    await t.test('blank and malformed IDs have actionable errors', async () => {
      for (const [decision_id, expected] of [['', 'decision_id_required'], ['not-an-id', 'decision_id_malformed']]) {
        await assert.rejects(TOOLS['read-decision'].handler(c, {}, {decision_id}),
          error => error.payload?.error === expected);
      }
    });

    await t.test('ambiguous prefixes refuse with both candidate IDs', async () => {
      const otherId = '4825f2a3-0000-4000-8000-000000000002';
      await c.query(`insert into event(id,subject_id,actor_id,verb,new_value)
        values('10000000-0000-4000-8000-000000000002',$1,$2,'log-decision','{"title":"Another synthetic decision"}')`,
      [otherId, actorId]);
      await c.query(`insert into record_source values('synthetic#session-two',
        '10000000-0000-4000-8000-000000000002','event','decision-history')`);
      await c.query('set role carr_reader');
      try {
        await assert.rejects(TOOLS['read-decision'].handler(c, {}, {decision_id:'4825f2a3'}), error => {
          assert.equal(error.payload?.error, 'ambiguous_decision_id');
          assert.deepEqual(error.payload.candidates.map(row => row.decision_id), [decisionId, otherId]);
          return true;
        });
      } finally { await c.query('reset role'); }
    });

    await t.test('absent optional facts stay null instead of being invented', async () => {
      await c.query(`update event set human_quote=null, agent_rationale=null,
        new_value='{"title":"Unquoted synthetic decision","quote_absent":true}' where id=$1`, [eventId]);
      const out = await TOOLS['read-decision'].handler(c, {}, {decision_id:decisionId});
      for (const field of ['human_quote', 'agent_rationale', 'provenance', 'cost_delta', 'quality_delta']) {
        assert.equal(out.decision[field], null, field);
      }
      assert.equal(out.decision.quote_absent, true);
      assert.equal(out.decision.priced, false);
    });
  } finally { await fixture.close(); }
});
