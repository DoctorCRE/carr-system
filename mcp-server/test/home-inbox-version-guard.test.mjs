import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as home from '../src/home-inbox.js';
const { homeInboxTools, HOME_INBOX_COMPLETION_CONTRACT, HOME_INBOX_WAITING_CONTRACT } = home;

test('Home completion v4 stays frozen while waiting v5 extends it', () => {
  const contract = version => JSON.parse(readFileSync(new URL(`../contracts/home-inbox.v${version}.json`, import.meta.url), 'utf8'));
  assert.deepEqual(HOME_INBOX_COMPLETION_CONTRACT, contract(4));
  assert.equal(Object.hasOwn(HOME_INBOX_COMPLETION_CONTRACT.item_fields, 'waiting_on'), false);
  assert.ok(HOME_INBOX_WAITING_CONTRACT, 'waiting requires its own v5 contract');
  assert.deepEqual(HOME_INBOX_WAITING_CONTRACT, contract(5));
  assert.equal(HOME_INBOX_WAITING_CONTRACT.item_fields.completed_at, HOME_INBOX_COMPLETION_CONTRACT.item_fields.completed_at);
  assert.ok(HOME_INBOX_WAITING_CONTRACT.item_fields.waiting_on);
});

test('Home v4 and v5 read their own projections without relabeling', async () => {
  const tools = homeInboxTools({});
  for (const version of [4, 5]) {
    const snapshot = { ok: true, schema_version: `home-inbox.v${version}`, items: [] };
    const client = { query: async sql => {
      assert.ok(sql.includes(`ops.read_home_inbox_v${version}()`));
      return { rows: [{ result: snapshot, pending_reply_ids: [] }] };
    } };
    assert.equal((await tools[`read-home-inbox-v${version}`].handler(client)).schema_version, snapshot.schema_version);
  }
});
