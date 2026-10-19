import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { neonConfig } from '@neondatabase/serverless';
import pg from 'pg';
import { withFailureRecording } from '../src/trace.js';
import { TOOLS } from '../src/tools.js';
import { reconcileRuntimeErrors } from '../../ops/runtime-error-watch.mjs';
import { runtimeErrorDatabase } from './helpers/runtime-error-postgres.mjs';

const actor = { id: '10000000-0000-4000-8000-000000000004', slug: 'joe-local',
  human: false, via: 'local-token', client_id: 'local', sponsoring_human_slug: 'joe',
  organization_tenant_id: 'carr-internal' };

test('a Worker throw yields one durable redacted finding and queued WR; recurrence replays both', async (t) => {
  const db = await runtimeErrorDatabase();
  const previous = neonConfig.fetchFunction;
  try {
    neonConfig.fetchFunction = async (_url, options) => {
      const { query, params } = JSON.parse(options.body);
      const result = await db.client.query({ text: query, values: params, rowMode: 'array' });
      return Response.json({ fields: result.fields.map(f => ({ name: f.name, dataTypeID: f.dataTypeID })),
        rows: result.rows, rowCount: result.rowCount, command: result.command, rowAsArray: true });
    };
    const call = async (verb, args) => {
      await db.client.query('begin');
      try {
        const reply = await TOOLS[verb].handler(db.client, actor, args);
        await db.client.query('commit');
        return reply;
      } catch (error) { await db.client.query('rollback'); throw error; }
    };
    const message = 'Synthetic Person synthetic@example.invalid Bearer synthetic-secret-value';
    const handler = withFailureRecording(async () => { throw new TypeError(message); });
    const trigger = async (route = '/private/synthetic@example.invalid') => {
      const pending = [];
      await assert.rejects(() => handler(new Request(`https://example.invalid${route}?token=synthetic-secret-value`),
        { DATABASE_URL_WRITER: 'postgresql://fixture@fixture.invalid/postgres', CARR_ENV: 'local', CORRELATION_ID: randomUUID() },
        { waitUntil: promise => pending.push(promise) }), TypeError);
      await Promise.all(pending);
    };
    await trigger();
    assert.equal((await db.client.query('select * from ops.incident')).rowCount, 1, 'Worker recorder persisted its source');
    await reconcileRuntimeErrors(call);
    assert.equal((await db.client.query('select * from record_flag')).rowCount, 1, 'exactly one finding reaches the existing intake');
    assert.equal((await db.client.query('select * from ops.work_request')).rowCount, 1, 'finding enters the repair queue');
    await trigger();
    await reconcileRuntimeErrors(call);
    const findings = await db.client.query('select * from record_flag');
    const requests = await db.client.query('select * from ops.work_request');
    assert.equal(findings.rowCount, 1);
    assert.equal(requests.rowCount, 1);
    assert.equal(requests.rows[0].state, 'captured');
    assert.equal((await db.client.query('select * from ops.incident_link')).rowCount, 1);
    assert.equal((await db.client.query('select * from ops.incident')).rowCount, 1);
    assert.equal((await db.client.query('select * from ops.incident_fact')).rowCount, 2);
    const durable = JSON.stringify([findings.rows, requests.rows, (await db.client.query('select * from event')).rows,
      (await db.client.query('select * from tool_call')).rows]);
    for (const forbidden of ['Synthetic Person', 'synthetic@example.invalid', 'synthetic-secret-value', '/private/', 'Bearer'])
      assert.equal(durable.includes(forbidden), false, `finding and intake receipts exclude ${forbidden}`);
    assert.equal(findings.rows[0].kind, 'runtime_error');
    assert.match(requests.rows[0].desired_outcome, new RegExp(findings.rows[0].id));

    for (const lostVerb of ['record-finding', 'report-problem', 'link-incident-work-request']) {
      await t.test(`a lost ${lostVerb} acknowledgement replays committed receipts`, async () => {
        await trigger(`/lost-ack/${lostVerb}`);
        let lost = false;
        await assert.rejects(() => reconcileRuntimeErrors(async (verb, args) => {
          const reply = await call(verb, args);
          if (verb === lostVerb && !lost) { lost = true; throw new Error('synthetic lost acknowledgement'); }
          return reply;
        }), /synthetic lost acknowledgement/);
        await reconcileRuntimeErrors(call);
        await reconcileRuntimeErrors(call);
        const count = 2 + ['record-finding', 'report-problem', 'link-incident-work-request'].indexOf(lostVerb);
        assert.equal((await db.client.query('select * from record_flag')).rowCount, count);
        assert.equal((await db.client.query('select * from ops.work_request')).rowCount, count);
        assert.equal((await db.client.query('select * from ops.incident_link')).rowCount, count);
        assert.equal((await db.client.query("select * from event where verb='record-finding'")).rowCount, count);
      });
    }
    const snapshot = (await db.client.query('select value from record_flag order by id')).rows;
    const observer = new pg.Client(db.client.connectionParameters);
    try {
      await observer.connect();
      assert.deepEqual((await observer.query('select value from record_flag order by id')).rows, snapshot,
        'findings survive a new database connection');
    } finally { await observer.end(); }
  } finally { neonConfig.fetchFunction = previous; await db.close(); }
});

test('an incomplete incident census never starts a finding write', async () => {
  for (const page of [
    { ok: true, coverage: [{ kind: 'incident', state: 'unavailable', count_total: 0 }], items: [] },
    { ok: true, coverage: [{ kind: 'incident', state: 'complete', count_total: 1 }], items: [] },
    { ok: false, coverage: [{ kind: 'incident', state: 'complete', count_total: 0 }], items: [] },
  ]) {
    const calls = [];
    await assert.rejects(() => reconcileRuntimeErrors(async (verb) => { calls.push(verb); return page; }), /runtime_incident_census/);
    assert.deepEqual(calls, ['unfinished-work']);
  }
});

test('source details and malformed identities cannot enter runtime findings', async () => {
  const ref = 'INC-20260101-01';
  for (const incident of [
    { ref, source_ref: 'elsewhere', environment: 'local' },
    { ref, source_ref: 'mcp-server/src/trace.js', detected_source: 'carr-mcp-worker',
      environment: 'secret@example.invalid', fingerprint: 'carr-mcp|secret@example.invalid|/route|http_5xx' },
    { ref, source_ref: 'mcp-server/src/trace.js', detected_source: 'carr-mcp-worker',
      environment: 'local', fingerprint: 'carr-mcp|local|/route|private-error-value' },
  ]) {
    const calls = [];
    const result = await reconcileRuntimeErrors(async (verb) => {
      calls.push(verb);
      return verb === 'unfinished-work' ? { ok: true, coverage: [{ kind: 'incident', state: 'complete', count_total: 1 }],
        items: [{ id: ref, kind: 'incident' }] } : { incident, links: [] };
    });
    assert.equal(result.linked, 0);
    assert.deepEqual(calls, ['unfinished-work', 'get-incident']);
  }
});

test('all incident pages are read before writes, and a repeated cursor refuses', async () => {
  const ref = 'INC-20260101-01';
  const calls = [];
  await reconcileRuntimeErrors(async (verb, args) => {
    calls.push([verb, args]);
    if (verb === 'get-incident') return { incident: { source_ref: 'unrelated' }, links: [] };
    return { ok: true, coverage: [{ kind: 'incident', state: 'complete', count_total: 1 }],
      items: args.cursor ? [{ id: ref, kind: 'incident' }] : [], next_cursor: args.cursor ? null : 'next' };
  });
  assert.deepEqual(calls.map(([verb]) => verb), ['unfinished-work', 'unfinished-work', 'get-incident']);
  assert.equal(calls[1][1].cursor, 'next');
  await assert.rejects(() => reconcileRuntimeErrors(async () => ({ ok: true,
    coverage: [{ kind: 'incident', state: 'complete', count_total: 0 }], items: [], next_cursor: 'same' })),
  /runtime_incident_cursor_repeated/);
});

test('a 1,501-incident backlog bounds card reads and keeps progress through existing links', async (t) => {
  for (const failAt of [null, 8]) {
    await t.test(failAt ? 'a later card failure preserves earlier links and resumes' : 'stop after ten groups', async (t) => {
      const rows = Array.from({ length: 1501 }, (_, i) => ({
        id: `INC-20260101-${String(i + 1).padStart(4, '0')}`, kind: 'incident',
      }));
      const links = new Set();
      let reads = 0, failed = false;
      const call = async (verb, args) => {
        if (verb === 'unfinished-work') {
          const offset = Number(args.cursor ?? 0);
          return { ok: true, coverage: [{ kind: 'incident', state: 'complete', count_total: rows.length }],
            items: rows.slice(offset, offset + 500), next_cursor: offset + 500 < rows.length ? String(offset + 500) : null };
        }
        if (verb === 'get-incident') {
          reads += 1;
          if (reads === failAt && !failed) { failed = true; throw new Error('synthetic late card failure'); }
          return { incident: { ref: args.ref, source_ref: 'mcp-server/src/trace.js',
            detected_source: 'carr-mcp-worker', environment: 'local', state: 'detected',
            fingerprint: 'carr-mcp|local|/synthetic|http_5xx' },
          links: links.has(args.ref) ? [{ kind: 'work_request' }] : [] };
        }
        if (verb === 'record-finding') return { flag_id: '10000000-0000-4000-8000-000000000001' };
        if (verb === 'report-problem') return { human_ref: 'WR-1' };
        assert.equal(verb, 'link-incident-work-request');
        links.add(args.incident_ref);
        return { ok: true, incident_ref: args.incident_ref, work_request: args.work_request };
      };
      if (failAt) {
        await assert.rejects(() => reconcileRuntimeErrors(call), /synthetic late card failure/);
        t.diagnostic(`${reads} card reads, ${links.size} completed groups before failure`);
        assert.deepEqual([...links], rows.slice(0, 7).map(row => row.id));
        reads = 0;
      }
      const result = await reconcileRuntimeErrors(call);
      t.diagnostic(`${reads} card reads, ${result.linked} new groups, ${links.size} total linked`);
      assert.equal(result.linked, 10);
      assert.equal(reads, failAt ? 17 : 10);
      assert.deepEqual([...links], rows.slice(0, failAt ? 17 : 10).map(row => row.id));
    });
  }
});
