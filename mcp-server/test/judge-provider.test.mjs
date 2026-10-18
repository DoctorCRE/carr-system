import test from 'node:test';
import assert from 'node:assert/strict';
import { judgeBinding, providerFor } from '../src/judge-provider.js';
import { prefetchJevAnswer } from '../src/jev-call-receipt.js';

test('default routing preserves request, result and post-commit cache', async () => {
  const request = { state: 'code', model: 'jev-1.13.0', questions: { q: { type: 'noul' } } };
  const response = { model: 'jev-1.13.0', answers: {}, usage: {} };
  const seen = [];
  const jev = async value => { seen.push(value); return response; };
  jev.cacheAfterCommit = async (...args) => seen.push(args);
  const ask = judgeBinding(jev);
  assert.equal(await ask(request), response);
  await ask.cacheAfterCommit(request, response);
  assert.deepEqual(seen, [request, [request, response]]);
});

test('system switch fails explicitly while runtime remains pinned', async () => {
  const routing = { schema: 'carr-judge-providers/v1', providers: {system_work: 'decisions', app_runtime: 'jev'} };
  let calls = 0;
  const jev = async () => { calls++; return 'legacy'; };
  await assert.rejects(judgeBinding(jev, 'system_work', routing)({}), e => e.payload.hint === 'decisions contract not yet verified / no key');
  assert.equal(calls, 0);
  assert.equal(await judgeBinding(jev, 'app_runtime', routing)({}), 'legacy');
  routing.providers.app_runtime = 'decisions';
  assert.throws(() => providerFor('app_runtime', routing), e => e.payload.error === 'judge_runtime_pinned');
});

test('proxy validates class and forwards admission without routing fields', async () => {
  const args = {idempotency_key: 'fixture-admission', session_id: 's', purpose: 'call', state: 'deal', questions: {q: {type: 'noul', instructions: 'fits?'}}};
  let request;
  assert.equal((await prefetchJevAnswer(args, async value => {request = value; return {}; }, 'app_runtime')).ok, true);
  assert.deepEqual(request, {state: 'deal', model: 'jev-latest', questions: args.questions,
    idempotency_key: args.idempotency_key, session_id: args.session_id});
  await assert.rejects(prefetchJevAnswer(args, async () => {}, 'typo'), /judge_class_invalid/);
});
