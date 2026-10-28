// Reserve daily Workers AI neurons before provider work.
const MODEL = '@cf/baai/bge-small-en-v1.5';
const DEFAULT_BUDGET = 8000;
const DAY_MS = 24 * 60 * 60 * 1000;
const exhaustedNamespaces = new WeakMap();

export class AiBudgetError extends Error {
  constructor(code, message = code) {
    super(message);
    this.code = code;
  }
}

function dailyLimit(env) {
  if (!['production', 'staging'].includes(env.CARR_ENV))
    throw new AiBudgetError('ai_budget_invalid_config');
  const maximum = env.CARR_ENV === 'staging' ? 1500 : DEFAULT_BUDGET;
  const raw = env.AI_DAILY_NEURON_BUDGET ?? String(maximum);
  if (typeof raw !== 'string' || !/^(0|[1-9]\d*)$/.test(raw) || Number(raw) > maximum)
    throw new AiBudgetError('ai_budget_invalid_config');
  return Number(raw);
}

function utcDay() { return new Date().toISOString().slice(0, 10); }
function nextDay(day) { return new Date(Date.parse(day) + DAY_MS).toISOString(); }

function exhausted(day) {
  return new AiBudgetError('ai_daily_budget_exhausted',
    `ai_daily_budget_exhausted: retry after ${nextDay(day)} UTC; reduce the backfill page size`);
}

function embeddingInput(model, input, options) {
  if (model !== MODEL || options.length || !input || typeof input !== 'object' ||
      Object.keys(input).sort().join(',') !== 'pooling,text' || input.pooling !== 'cls' ||
      !Array.isArray(input.text) || input.text.length < 1 || input.text.length > 16 ||
      input.text.some(text => typeof text !== 'string' || new TextEncoder().encode(text).length > 480))
    throw new AiBudgetError('ai_budget_unsupported_input');
  return Object.freeze({ text: Object.freeze([...input.text]), pooling: 'cls' });
}

function estimatedNeurons(textCount) {
  // Reserve the full model context, independent of tokenizer normalization.
  return Math.ceil(textCount * 512 * 1841 / 1_000_000);
}

export function budgetedAi(env) {
  async function reserve(neurons) {
    const limit = dailyLimit(env);
    if (neurons > limit) throw exhausted(utcDay());
    if (!env.AI?.run || !env.AI_NEURON_BUDGET?.get || !env.AI_NEURON_BUDGET?.idFromName)
      throw new AiBudgetError('ai_budget_unavailable');
    const namespace = env.AI_NEURON_BUDGET;
    const cached = exhaustedNamespaces.get(namespace);
    if (cached?.day === utcDay() && cached.limit === limit) throw exhausted(cached.day);
    let response, receipt;
    try {
      const stub = namespace.get(namespace.idFromName(env.CARR_ENV));
      response = await stub.fetch(new Request('https://ai-budget.internal/reserve', {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ neurons }),
      }));
      receipt = await response.json();
    } catch {
      throw new AiBudgetError('ai_budget_unavailable');
    }
    if (response.status === 429 && receipt?.error === 'ai_daily_budget_exhausted') {
      if (receipt.day === utcDay() && receipt.limit === limit && receipt.remaining === 0)
        exhaustedNamespaces.set(namespace, { day: receipt.day, limit });
      throw exhausted(utcDay());
    }
    if (!response.ok || receipt?.day !== utcDay() || receipt.limit !== limit ||
        !Number.isInteger(receipt.spent) || receipt.spent < neurons || receipt.spent > limit ||
        receipt.remaining !== limit - receipt.spent)
      throw new AiBudgetError('ai_budget_unavailable');
    if (receipt.remaining === 0) exhaustedNamespaces.set(namespace, { day: receipt.day, limit });
  }
  return {
    async reserveOperation(textCount) {
      if (!Number.isSafeInteger(textCount) || textCount < 1)
        throw new AiBudgetError('ai_budget_unsupported_input');
      let remaining = Math.floor(textCount / 16) * estimatedNeurons(16) + estimatedNeurons(textCount % 16);
      await reserve(remaining);
      return {
        async run(model, input, ...options) {
          const snapshot = embeddingInput(model, input, options);
          const neurons = estimatedNeurons(snapshot.text.length);
          if (neurons > remaining) throw exhausted(utcDay());
          remaining -= neurons;
          return env.AI.run(model, snapshot);
        },
      };
    },
    async run(model, input, ...options) {
      const snapshot = embeddingInput(model, input, options);
      await reserve(estimatedNeurons(snapshot.text.length));
      // A failed or timed-out provider call may still bill. Never refund it.
      return env.AI.run(model, snapshot);
    },
  };
}

export class AiNeuronBudget {
  constructor(state, env) {
    this.storage = state.storage;
    this.env = env;
  }

  async fetch(request) {
    if (request.method !== 'POST' || new URL(request.url).pathname !== '/reserve')
      return new Response(null, { status: 404 });
    let input, limit;
    try {
      input = await request.json();
      limit = dailyLimit(this.env);
    } catch {
      return Response.json({ error: 'ai_budget_unavailable' }, { status: 503 });
    }
    if (!input || Object.keys(input).join(',') !== 'neurons' || !Number.isInteger(input.neurons) || input.neurons < 1)
      return Response.json({ error: 'ai_budget_invalid_reservation' }, { status: 400 });
    return this.storage.transaction(async storage => {
      const day = utcDay();
      const saved = await storage.get('daily');
      if (saved && (typeof saved.day !== 'string' || saved.day > day ||
          !Number.isInteger(saved.spent) || saved.spent < 0 || saved.spent > DEFAULT_BUDGET))
        return Response.json({ error: 'ai_budget_unavailable' }, { status: 503 });
      const spent = saved?.day === day ? saved.spent : 0;
      if (input.neurons > limit - spent)
        return Response.json({ error: 'ai_daily_budget_exhausted', day, limit, remaining: Math.max(0, limit - spent) }, { status: 429 });
      const updated = spent + input.neurons;
      await storage.put('daily', { day, spent: updated });
      return Response.json({ day, spent: updated, limit, remaining: limit - updated });
    });
  }
}
