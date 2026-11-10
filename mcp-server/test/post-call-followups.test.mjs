// Tests read-only pending call follow-up snapshots.
import test from 'node:test';
import { writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { allowedIn, connectionRouteForTool } from '../src/mcp.js';
import { TOOLS, executeRegisteredTool, assertRegisteredToolInput } from '../src/tools.js';

test('pending call follow-ups return an explicit empty read-only snapshot when there is nothing to review', async () => {
  const result = await executeRegisteredTool({ async query() { return { rows: [] }; } }, { slug: 'joe' }, 'read-post-call-followups', {});
  assert.deepEqual(result, { schema_version: 'post-call-followups.v1', candidates: [], send: false });
});


test('call follow-ups reject caller scope and run on the full-profile read-only writer route', async () => {
  const tool = TOOLS['read-post-call-followups'];
  assert.equal(connectionRouteForTool(tool), 'writer_read_only');
  assert.equal(allowedIn('full', 'read-post-call-followups', tool), true);
  await assert.doesNotReject(() => assertRegisteredToolInput('read-post-call-followups', tool, {}));
  await assert.rejects(() => assertRegisteredToolInput('read-post-call-followups', tool, { session_id: 'other' }), /unregistered_operation_fields/);
});

test('call outcomes preview current action matches and acceptance rechecks them', async t => {
  const { acquireDisposablePostgres } = await import('./helpers/disposable-postgres.ts');
  const { default: pg } = await import('pg');
  const { randomUUID } = await import('node:crypto');
  const fixture = await acquireDisposablePostgres({ prefix: 'call-action-preview-', snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  try {
    const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
    const dell = (await c.query("select id from actor where slug='dell'")).rows[0].id;
    const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic preview physician',$1,$1) returning id", [actor.id])).rows[0].id;
    await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
    const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'fixture_active',$2,$2) returning id", [party, actor.id])).rows[0].id;
    let deal;
    const session = (await c.query(`insert into capture_session(nonce,device_id,actor_id,mode,started_at,consent_announced_at,
      session_token_hash,expires_at,state,state_at,post_call) values($1,'fixture',$2,'meeting',now(),now(),$1,
      now()+interval '1 day','distilling',now(),true) returning id`, [randomUUID(), actor.id])).rows[0].id;
    const command = async (name, args = {}) => {
      await c.query('begin');
      try { const result = await executeRegisteredTool(c, actor, name, args); await c.query('commit'); return result; }
      catch (error) { await c.query('rollback'); throw error; }
    };
    const freshDeal = async () => {
      deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic isolated preview',$1,'lease','site_selection','joe',$2,$2) returning id", [client, actor.id])).rows[0].id;
    };
    const caseTest = (name, run) => t.test(name, async () => { await freshDeal(); await run(); });
    let index = 0;
    const proposal = async (text, due = '2027-02-02', defaulted = false) => (await c.query(`insert into capture_post_call_candidate
      (session_id,idempotency_key,batch_hash,item_index,kind,deal_id,assignee_slug,action_description,due_on,due_defaulted,evidence_quote,confidence)
      values($1,$2,$2,$3,'assigned_action',$4,'joe',$5,$6,$7,'Joe will review the synthetic survey',1) returning id`,
      [session, randomUUID(), index++, deal, text, due, defaulted])).rows[0].id;
    const seed = async (source, candidate, text, owner = actor.id, status = 'open', structured = true) => {
      if (source === 'next_action') await c.query("update next_action set status='dropped' where subject_id=$1 and owner_id=$2 and status='open'", [deal, owner]);
      if (source === 'next_action') return (await c.query(`insert into next_action
        (subject_type,subject_id,owner_id,description,status,due_on,created_by)
        values('deal',$1,$2,$3,$4,'2027-01-01',$5) returning id`, [deal, owner, text, status, actor.id])).rows[0].id;
      const historicalCandidate = await proposal(text);
      return (await c.query(`insert into capture_post_call_action
        (candidate_id,deal_id,owner_id,description,status,due_on,accepted_by,item_title,source_ref,completed_at)
        values($1,$2,$3,$4,$5,'2027-01-01',$6,$7,'synthetic',case when $5='done' then now() end) returning id`,
        [historicalCandidate, deal, owner, text, status, actor.id, structured ? 'Review survey' : null])).rows[0].id;
    };
    const preview = async id => (await command('read-call-outcomes')).calls.find(call => call.session_id === session)
      .open_cards.find(card => card.candidate_id === id).existing_action_comparison;
    for (const source of ['next_action', 'capture_post_call_action']) await caseTest(`${source} exact preview matches accepted merge`, async () => {
      const text = `Review synthetic ${source} survey`;
      const id = await proposal(text);
      const action = await seed(source, id, `  ${text.toUpperCase()}  `);
      const before = await preview(id);
      assert.equal(before.status, 'exact_match');
      assert.deepEqual(before.exact_match, { id: action, source, owner: 'joe', description: `  ${text.toUpperCase()}  `,
        status: 'open', due_on: '2027-01-01' });
      assert.deepEqual(before.date_effect, { current_due_on: '2027-01-01', proposed_due_on: '2027-02-02',
        resulting_due_on: '2027-02-02', changes_due_on: true, reason: 'stated_date' });
      const result = await command('resolve-post-call-candidate', { candidate_id: id, accept: true, idempotency_key: randomUUID() });
      assert.equal(result.ref, action);
      assert.equal((await c.query(`select due_on::text from ${source} where id=$1`, [action])).rows[0].due_on, before.date_effect.resulting_due_on);
    });
    for (const source of ['next_action', 'capture_post_call_action']) {
      for (const defaulted of [true, false]) await caseTest(`${source} edge whitespace reuses identity and preserves bullets and date ${defaulted}`, async () => {
        const edge = '\t\n\r\v\f \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff';
        const id = await proposal(`${edge}Review\t synthetic   survey${edge}`, '2027-02-02', defaulted);
        const action = await seed(source, id, `${edge}REVIEW   SYNTHETIC\nSURVEY${edge}`);
        await c.query(`update ${source} set item_bullets='["Keep concrete recipient", "Keep survey date"]'::jsonb where id=$1`, [action]);
        const observation = await command('read-call-outcomes');
        if (process.env.POST_CALL_EDGE_FIXTURE_OUTPUT && source === 'next_action' && defaulted) {
          const call = observation.calls.find(call => call.session_id === session);
          writeFileSync(process.env.POST_CALL_EDGE_FIXTURE_OUTPUT, JSON.stringify({ ...observation,
            calls: [{ ...call, applied: [], open_cards: call.open_cards.filter(card => card.candidate_id === id) }] }));
        }
        const before = observation.calls.find(call => call.session_id === session).open_cards
          .find(card => card.candidate_id === id).existing_action_comparison;
        assert.equal(before.status, 'exact_match');
        assert.equal(before.exact_match.id, action);
        assert.equal(before.date_effect.reason, defaulted ? 'defaulted_date' : 'stated_date');
        const args = { candidate_id: id, accept: true, idempotency_key: randomUUID() };
        const result = await command('resolve-post-call-candidate', args);
        assert.equal(result.ref, action);
        assert.equal((await command('resolve-post-call-candidate', args)).ref, action);
        const stored = (await c.query(`select due_on::text,item_bullets from ${source} where id=$1`, [action])).rows[0];
        assert.equal(stored.due_on, defaulted ? '2027-01-01' : '2027-02-02');
        assert.ok(stored.item_bullets.includes('Keep concrete recipient'));
        assert.ok(stored.item_bullets.includes('Keep survey date'));
      });
    }
    await caseTest('proof context accepts all actor owners and PostgreSQL task dates', async () => {
      const helper = (await c.query("insert into actor(slug,kind,display_name) values('synthetic-helper','automation','Synthetic helper') returning id")).rows[0].id;
      const exact = await proposal('Review exact survey');
      await seed('next_action', exact, '\tReview exact survey\n');
      const related = await proposal('Review helper survey');
      for (const [i, due] of ['infinity', '-infinity', '10000-01-01', '0001-01-01 BC'].entries()) {
        const source = i === 0 ? 'next_action' : 'capture_post_call_action';
        const task = source === 'next_action'
          ? (await c.query("insert into next_action(subject_type,subject_id,owner_id,description,status,created_by) values('deal',$1,$2,$3,'open',$4) returning id",
            [deal, helper, '\tReview helper survey\n', actor.id])).rows[0].id
          : await seed(source, related, '\tReview helper survey\n', helper);
        await c.query(`update ${source} set due_on=$1::date where id=$2`, [due, task]);
      }
      const observation = await command('read-call-outcomes');
      const call = observation.calls.find(call => call.session_id === session);
      const cards = call.open_cards.filter(card => [exact, related].includes(card.candidate_id));
      assert.equal(cards.find(card => card.candidate_id === exact).existing_action_comparison.status, 'exact_match');
      const context = cards.find(card => card.candidate_id === related).existing_action_comparison;
      assert.equal(context.status, 'related_open_tasks');
      assert.deepEqual(context.related_open_tasks.filter(task => task.owner === 'synthetic-helper')
        .map(task => task.due_on).sort(), ['infinity', '-infinity', '10000-01-01', '0001-01-01 BC'].sort());
      if (process.env.POST_CALL_EDGE_CONTRACT_FIXTURE_OUTPUT) writeFileSync(process.env.POST_CALL_EDGE_CONTRACT_FIXTURE_OUTPUT,
        JSON.stringify({ ...observation, calls: [{ ...call, applied: [], open_cards: cards }] }));
    });
    for (const [label, source, text, owner, status, structured] of [
      ['different owner', 'next_action', 'Review owner survey', dell, 'open', true],
      ['completed', 'capture_post_call_action', 'Review completed survey', actor.id, 'done', true],
      ['legacy', 'capture_post_call_action', 'Review legacy survey', actor.id, 'open', false],
      ['similar wording', 'next_action', 'Review the synthetic floor survey', actor.id, 'open', true],
    ]) await caseTest(`${label} is advisory and reads never mutate`, async () => {
      const id = await proposal(label === 'similar wording' ? 'Review synthetic floor survey' : text);
      const action = await seed(source, id, text, owner, status, structured);
      const stored = (await c.query(`select * from ${source} where id=$1`, [action])).rows[0];
      const before = await preview(id);
      assert.equal(before.status, status === 'open' ? 'related_open_tasks' : 'no_match');
      assert.equal(before.exact_match, null);
      assert.deepEqual(before.related_open_tasks.map(match => match.id), status === 'open' ? [action] : []);
      assert.deepEqual((await c.query(`select * from ${source} where id=$1`, [action])).rows[0], stored);
      assert.equal((await command('resolve-post-call-candidate', { candidate_id: id, accept: true, idempotency_key: randomUUID() })).ref === action, false);
      assert.deepEqual((await c.query(`select * from ${source} where id=$1`, [action])).rows[0], stored);
    });
    for (const [label, proposed, existing, expected] of [
      ['different call recipient', 'Call the landlord', 'Call the broker', 'no_match'],
      ['different document', 'Send the LOI', 'Send the survey', 'no_match'],
      ['different follow-up recipient', 'Follow up with the tenant', 'Follow up with the lender', 'no_match'],
      ['only filler words shared', 'Meet at the property', 'Work at the office', 'no_match'],
      ['reworded rent roll task', 'Get rent roll from landlord', 'Ask landlord for the rent roll', 'no_match'],
    ]) await caseTest(`${label} does not infer similarity from closed history`, async () => {
      const id = await proposal(proposed);
      await seed('capture_post_call_action', id, existing, actor.id, 'done');
      const before = await preview(id);
      assert.equal(before.status, expected);
      assert.equal(before.exact_match, null);
      assert.deepEqual(before.related_open_tasks, []);
    });
    await caseTest('open task context is capped to the five newest with a total', async () => {
      const id = await proposal('A distinct proposed task');
      const ids = [];
      for (let i = 0; i < 6; i++) {
        const action = await seed('capture_post_call_action', id, `Existing task ${i}`);
        await c.query("update capture_post_call_action set accepted_at = timestamp '2027-01-01' + $2 * interval '1 day' where id=$1", [action, i]);
        ids.push(action);
      }
      const stored = (await c.query('select * from capture_post_call_action where deal_id=$1 order by id', [deal])).rows;
      const before = await preview(id);
      assert.deepEqual(before.related_open_tasks.map(action => action.id), ids.slice(1).reverse());
      assert.equal(before.related_open_task_total, 6);
      assert.deepEqual((await c.query('select * from capture_post_call_action where deal_id=$1 order by id', [deal])).rows, stored);
    });
    await caseTest('identical text on another deal gives no match', async () => {
      const text = 'Request synthetic zoning certificate';
      const id = await proposal(text);
      const otherDeal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic other deal',$1,'lease','site_selection','joe',$2,$2) returning id", [client, actor.id])).rows[0].id;
      await c.query(`insert into next_action(subject_type,subject_id,owner_id,description,status,created_by)
        values('deal',$1,$2,$3,'open',$2)`, [otherDeal, actor.id, text]);
      const before = await preview(id);
      assert.equal(before.status, 'no_match');
      assert.equal(before.exact_match, null);
      assert.deepEqual(before.related_open_tasks, []);
    });
    for (const [due, defaulted, reason] of [[null, false, 'unstated_date'], ['2027-03-03', true, 'defaulted_date'], ['2027-04-04', null, 'defaulted_date']]) {
      await freshDeal();
      const id = await proposal(`Keep synthetic date ${reason} ${defaulted}`, due, defaulted);
      const action = await seed('next_action', id, `Keep synthetic date ${reason} ${defaulted}`);
      const before = await preview(id);
      assert.equal(before.date_effect.reason, reason);
      assert.equal(before.date_effect.resulting_due_on, '2027-01-01');
      await command('resolve-post-call-candidate', { candidate_id: id, accept: true, idempotency_key: randomUUID() });
      assert.equal((await c.query('select due_on::text from next_action where id=$1', [action])).rows[0].due_on, '2027-01-01');
    }
    await freshDeal();
    const fresh = await proposal('Arrange completely unrelated inspection');
    assert.equal((await preview(fresh)).status, 'no_match');
    const later = await seed('next_action', fresh, 'Arrange completely unrelated inspection');
    assert.equal((await preview(fresh)).exact_match.id, later, 'each read compares current state');
    await c.query("update next_action set status='done' where id=$1", [later]);
    assert.notEqual((await command('resolve-post-call-candidate', { candidate_id: fresh, accept: true, idempotency_key: randomUUID() })).ref, later,
      'acceptance rechecks state changed since preview');
  } finally { await c.end(); await fixture.close(); }
});
