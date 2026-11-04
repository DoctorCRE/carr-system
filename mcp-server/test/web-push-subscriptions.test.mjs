import test from 'node:test';
import assert from 'node:assert/strict';
import { webPushTools } from '../src/web-push-subscriptions.ts';
import { ToolError } from '../src/tool-error.ts';
import { publicError } from '../src/public-errors.ts';
const uuid = '61700000-0000-4000-8000-000000000001';
const key = Buffer.concat([Buffer.from([4]), Buffer.alloc(64, 7)]).toString('base64url');
const args = { idempotency_key: uuid, installation_id: uuid, endpoint: 'https://push.example.test/private', p256dh: key, auth: Buffer.alloc(16, 9).toString('base64url') };
const config = { WEB_PUSH_VAPID_PUBLIC_KEY: key, WEB_PUSH_KEY_GENERATION: 'synthetic-1' };
const tools = (extra = {}) => webPushTools({ ToolError, withEnvelope: async (_c,_a,_v,x,fn) => { assert.equal(JSON.stringify(x).includes('private'), false); assert.equal('endpoint' in x, false); return fn(); }, config: () => config, ...extra });
test('registers_only_callers_installation_and_never_returns_endpoint_keys', async () => {
  const t = tools(); let calls = 0;
  const result = await t['register-web-push-subscription'].handler({ query: async (_sql, values) => {
    calls++; assert.equal(values[0].endpoint, args.endpoint);
    return { rows: [{ result: { ok: true, subscription_id: uuid, state: 'active', generation: 1 } }] };
  } }, { id: uuid }, args);
  assert.equal(calls, 1); assert.deepEqual(result, { ok: true, subscription_id: uuid, state: 'active', generation: 1 });
});
test('rejects_invalid_endpoints_credentials_keys_and_caller_identity_fields_before_database', async () => {
  const t = tools(); const c = { query: () => assert.fail('no database call') };
  for (const patch of [{endpoint:'http://push.test/x'}, {endpoint:'https://user:password@push.test/x'}, {endpoint:'https://push.test/x#secret'}, {p256dh:'bad'}, {auth:'bad'}, {actor_id:uuid}, {expirationTime:-1.5}, {idempotency_key:'bad'}])
    await assert.rejects(t['register-web-push-subscription'].handler(c,{id:uuid},{...args,...patch}), /web_push_invalid_input/);
});
test('missing_public_key_refuses_without_database_and_generation_changes_are_immediate', async () => {
  const t = tools({ config: () => ({}) });
  await assert.rejects(t['read-web-push-config'].handler(), /feature_unavailable/);
  await assert.rejects(t['register-web-push-subscription'].handler({}, {id:uuid}, args), /feature_unavailable/);
  const read = tools()['read-web-push-config'];
  assert.equal((await read.handler()).key_generation, 'synthetic-1');
  config.WEB_PUSH_KEY_GENERATION = 'synthetic-2';
  assert.equal((await read.handler()).key_generation, 'synthetic-2');
});
test('database_failure_drops_sensitive_diagnostics_and_never_fetches_endpoint', async () => {
  const original = globalThis.fetch; globalThis.fetch = () => assert.fail('no endpoint fetch');
  try {
    await assert.rejects(tools()['register-web-push-subscription'].handler({query:async()=>{throw new Error(args.endpoint + args.auth);}}, {id:uuid}, args), error => {
      const lines=[]; const old=console.error; console.error=line=>lines.push(line);
      try { assert.equal(publicError(error).error,'web_push_unavailable'); } finally {console.error=old;}
      for(const secret of [args.endpoint,args.p256dh,args.auth]) assert.equal(lines.join('').includes(secret),false);
      assert.equal(JSON.stringify(error).includes(args.endpoint), false); assert.equal(error.message.includes(args.auth), false); return true;
    });
  } finally {globalThis.fetch = original;}
});
