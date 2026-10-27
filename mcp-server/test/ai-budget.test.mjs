import test from 'node:test';
import assert from 'node:assert/strict';
import { AiNeuronBudget, budgetedAi } from '../src/ai-budget.js';

const MODEL = '@cf/baai/bge-small-en-v1.5';
const input = (count = 1) => ({ text: Array(count).fill('tenant tour'), pooling: 'cls' });

function fixture({ budget = '2', run = async () => ({ data: [[1]] }) } = {}) {
  const values = new Map();
  let tail = Promise.resolve(), writes = 0, providerCalls = 0, requests = 0;
  const storage = {
    get: async key => values.get(key),
    put: async (key, value) => { writes++; values.set(key, structuredClone(value)); },
    transaction(operation) {
      const result = tail.then(() => operation(storage));
      tail = result.catch(() => {});
      return result;
    },
  };
  const env = { CARR_ENV: 'production', AI_DAILY_NEURON_BUDGET: budget,
    AI: { run: async (...args) => { providerCalls++; return run(...args); } } };
  let object = new AiNeuronBudget({ storage }, env);
  env.AI_NEURON_BUDGET = { idFromName: name => name, get: name => {
    assert.equal(name, 'production');
    return { fetch: request => { requests++; return object.fetch(request); } };
  } };
  return { env, storage, values, restart: () => { object = new AiNeuronBudget({ storage }, env); },
    get writes() { return writes; }, get providerCalls() { return providerCalls; }, get requests() { return requests; } };
}

test('independent callers share one daily reservation and rejected calls never reach AI', async () => {
  const f = fixture();
  const results = await Promise.allSettled(Array.from({ length: 12 }, () => budgetedAi(f.env).run(MODEL, input())));
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 2);
  for (const result of results.filter(result => result.status === 'rejected')) {
    assert.equal(result.reason.code, 'ai_daily_budget_exhausted');
    assert.match(result.reason.message, /UTC/);
  }
  assert.equal(f.providerCalls, 2);
  assert.equal(f.writes, 2, 'refusals do not write the counter');
  f.restart();
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  assert.equal(f.providerCalls, 2, 'eviction does not reset spending');
});

test('default cap permits exactly 8000 full-context estimates', async () => {
  const f = fixture();
  delete f.env.AI_DAILY_NEURON_BUDGET;
  for (let batch = 0; batch < 500; batch++) await budgetedAi(f.env).run(MODEL, input(16));
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  assert.equal(f.values.get('daily').spent, 8000);
  assert.equal(f.providerCalls, 500);
});

test('provider failure spends the reservation and cannot be retried past the cap', async () => {
  const f = fixture({ budget: '1', run: async () => { throw new Error('provider timeout'); } });
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), /provider timeout/);
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  assert.equal(f.providerCalls, 1);
});

test('UTC rollover restores the budget while a lowered cap cannot reset spending', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 27, 23, 59, 59) });
  const f = fixture();
  await budgetedAi(f.env).run(MODEL, input(2));
  f.env.AI_DAILY_NEURON_BUDGET = '1';
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  t.mock.timers.tick(1000);
  await budgetedAi(f.env).run(MODEL, input());
  assert.deepEqual(f.values.get('daily'), { day: '2026-10-28', spent: 1 });
});

for (const budget of ['-1', '8001', 'NaN', '1.5', '', 2]) test(`invalid budget ${JSON.stringify(budget)} fails closed`, async () => {
  const f = fixture({ budget });
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_budget_invalid_config' });
  assert.equal(f.providerCalls, 0);
  assert.equal(f.requests, 0);
});

test('disabled budget is refused without counter operations', async () => {
  const f = fixture({ budget: '0' });
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  assert.equal(f.requests, 0);
  assert.equal(f.providerCalls, 0);
});

for (const [model, data, options] of [
  ['@cf/baai/bge-large-en-v1.5', input(), []],
  [MODEL, { text: ['ok'], pooling: 'cls', max_tokens: 1000 }, []],
  [MODEL, { text: 'ok', pooling: 'cls' }, []],
  [MODEL, input(17), []],
  [MODEL, input(0), []],
  [MODEL, { text: ['é'.repeat(241)], pooling: 'cls' }, []],
  [MODEL, input(), [{}]],
]) test(`unpriced model or input is refused ${JSON.stringify([model, data.text?.length, options.length])}`, async () => {
  const f = fixture();
  await assert.rejects(budgetedAi(f.env).run(model, data, ...options), { code: 'ai_budget_unsupported_input' });
  assert.equal(f.providerCalls, 0);
  assert.equal(f.requests, 0);
});

test('input mutation during a reservation cannot increase provider work', async () => {
  let seen;
  const f = fixture({ run: async (_model, data) => { seen = data; return {}; } });
  const data = input();
  const pending = budgetedAi(f.env).run(MODEL, data);
  data.text.push('extra request');
  data.pooling = 'mean';
  await pending;
  assert.deepEqual(seen, input());
  assert.equal(f.values.get('daily').spent, 1);
});

test('counter outages and malformed receipts fail closed', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 27) });
  for (const result of [null, {}, { day: '2026-10-27', spent: 0, remaining: 2, limit: 2 }]) {
    const f = fixture();
    f.env.AI_NEURON_BUDGET.get = () => ({ fetch: async () => result === null
      ? Promise.reject(new Error('counter unavailable')) : Response.json(result) });
    await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_budget_unavailable' });
    assert.equal(f.providerCalls, 0);
  }
});

test('counter binding absence and unknown deployment environment fail closed', async () => {
  const f = fixture();
  delete f.env.AI_NEURON_BUDGET;
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_budget_unavailable' });
  f.env.CARR_ENV = 'unknown';
  await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_budget_invalid_config' });
  assert.equal(f.providerCalls, 0);
});

test('direct reservations ignore caller attempts to raise the cap', async () => {
  const f = fixture({ budget: '1' });
  const object = new AiNeuronBudget({ storage: f.storage }, f.env);
  const reserve = body => object.fetch(new Request('https://ai-budget.internal/reserve', {
    method: 'POST', body: JSON.stringify(body),
  }));
  assert.equal((await reserve({ neurons: 1, limit: 8000 })).status, 400);
  assert.equal((await reserve({ neurons: 2 })).status, 429);
  assert.equal(f.writes, 0);
});

test('doctrine search and reembedding report budget exhaustion without vector writes', async () => {
  const { searchDoctrineWithEmbeddings, embedDoctrineRevisions } = await import('../src/doctrine-embeddings.ts');
  const f = fixture({ budget: '0' });
  const c = { ai: budgetedAi(f.env), query: async () => { assert.fail('exhausted AI cannot query vectors'); } };
  const search = await searchDoctrineWithEmbeddings(c, { q: 'tenant tour' }, null);
  assert.equal(search.semantic.reason, 'ai_daily_budget_exhausted');
  assert.match(search.semantic.hint, /UTC/);
  const bulk = await embedDoctrineRevisions(c, [{ revision_id: 'revision', plain_text: 'tenant tour' }]);
  assert.equal(bulk.reason, 'ai_daily_budget_exhausted');
  assert.equal(bulk.embedded, 0);
  assert.equal(f.providerCalls, 0);
});

test('a bulk operation larger than the daily cap is refused before any provider call', async () => {
  const { embedDoctrineRevisions } = await import('../src/doctrine-embeddings.ts');
  const f = fixture({ budget: '17', run: async (_model, data) => ({ data: data.text.map(() => [1, ...Array(383).fill(0)]) }) });
  const c = { ai: budgetedAi(f.env), query: async () => { assert.fail('refused bulk load cannot write vectors'); } };
  const result = await embedDoctrineRevisions(c, [{ revision_id: 'revision', plain_text: 'x'.repeat(7000) }]);
  assert.equal(result.reason, 'ai_daily_budget_exhausted');
  assert.equal(f.providerCalls, 0);
  assert.equal(f.requests, 0);
});

test('real workerd SQLite reservations remain atomic and survive a Worker reload', async () => {
  const { Miniflare, convertV4MiniflareOptions } = await import('miniflare');
  const { readFile, mkdtemp, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const directory = await mkdtemp(join(tmpdir(), 'ai-budget-'));
  const source = await readFile(new URL('../src/ai-budget.js', import.meta.url), 'utf8');
  const options = { modules: true, compatibilityDate: '2026-09-01',
    script: source + `\nexport default { fetch(request, env) {
      const namespace = env.AI_NEURON_BUDGET;
      return namespace.get(namespace.idFromName(env.CARR_ENV)).fetch(request);
    } };`,
    bindings: { CARR_ENV: 'production', AI_DAILY_NEURON_BUDGET: '3' },
    durableObjects: { AI_NEURON_BUDGET: { className: 'AiNeuronBudget', useSQLite: true } },
  };
  const persistence = { resourcePersistencePath: directory, unsafeEnableSharedStorage: false };
  const worker = new Miniflare({ ...convertV4MiniflareOptions(options), ...persistence });
  const reserve = () => worker.dispatchFetch('https://ai-budget.internal/reserve', {
    method: 'POST', body: JSON.stringify({ neurons: 1 }),
  });
  try {
    const results = await Promise.all(Array.from({ length: 30 }, reserve));
    assert.equal(results.filter(response => response.status === 200).length, 3);
    assert.equal(results.filter(response => response.status === 429).length, 27);
    await worker.setOptions({ ...convertV4MiniflareOptions({ ...options,
      bindings: { ...options.bindings, RELOAD_FIXTURE: '1' } }), ...persistence });
    assert.equal((await reserve()).status, 429, 'SQLite preserves the spent budget across reload');
    await worker.setOptions({ ...convertV4MiniflareOptions({ ...options,
      bindings: { ...options.bindings, CARR_ENV: 'staging' } }), ...persistence });
    assert.equal((await reserve()).status, 200, 'staging has a separate counter');
  } finally {
    await worker.dispose();
    await rm(directory, { recursive: true, force: true });
  }
});

test('exhaustion is cached within an isolate and expires at the next UTC day', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: Date.UTC(2026, 9, 27, 23, 59, 59) });
  const f = fixture({ budget: '1' });
  await budgetedAi(f.env).run(MODEL, input());
  for (let retry = 0; retry < 20; retry++)
    await assert.rejects(budgetedAi(f.env).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  assert.equal(f.requests, 1, 'exhausted retries do not add counter invocations');
  const freshBinding = { ...f.env, AI_NEURON_BUDGET: { ...f.env.AI_NEURON_BUDGET } };
  for (let retry = 0; retry < 20; retry++)
    await assert.rejects(budgetedAi(freshBinding).run(MODEL, input()), { code: 'ai_daily_budget_exhausted' });
  assert.equal(f.requests, 2, 'a cold isolate caches confirmed exhaustion after one refusal');
  t.mock.timers.tick(1000);
  await budgetedAi(f.env).run(MODEL, input());
  assert.equal(f.requests, 3);
});


test('a 3000-chunk revision with only 1000 neurons left admits no paid work', async () => {
  const { embedDoctrineRevisions } = await import('../src/doctrine-embeddings.ts');
  const f = fixture({ budget: '8000', run: async (_model, data) => ({ data: data.text.map(() => [1, ...Array(383).fill(0)]) }) });
  f.values.set('daily', { day: new Date().toISOString().slice(0, 10), spent: 7000 });
  let inserts = 0;
  const c = { ai: budgetedAi(f.env), query: async sql => {
    if (sql.includes('insert into doctrine_embedding')) inserts++;
    return { rows: [{ revision_id: 'revision' }] };
  } };
  const result = await embedDoctrineRevisions(c, [{ revision_id: 'revision', plain_text: 'x'.repeat(480 + 2999 * 384) }]);
  assert.equal(result.reason, 'ai_daily_budget_exhausted');
  assert.equal(result.embedded, 0);
  assert.equal(inserts, 0);
  assert.equal(f.providerCalls, 0, 'a refused revision must not burn the remaining budget');
  assert.equal(f.values.get('daily').spent, 7000);
});
