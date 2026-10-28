// Store lean Work Request slice and job facts with stable IDs and priority order.
import addressFormat from './progress-address.v1.json' with { type: 'json' };
import { organizationTenantForActor } from './identity.js';
import { readableWorkRequestSql } from './work-request-read.ts';

const text = (max = 100) => ({ type: 'string', minLength: 1, maxLength: max });
const positive = { type: 'integer', minimum: 1, maximum: 0x7fffffff };
const base = { idempotency_key: { type: 'string', format: 'uuid' }, work_request: text() };
const slice = { ...positive, default: 1 };
const jobFacts = ['executor', 'model', 'effort', 'card_title', 'description'];
const jobStart = ['job', 'runner_job_id'];
const repositories = Object.keys(addressFormat.repositories);
export function queueCardId(ref, ordinal, number) {
  const work_request = 'WR' + new RegExp(addressFormat.work_request_pattern).exec(ref)[1].replace(/^0+(?=\d)/u, '');
  return addressFormat[number == null ? 'slice_template' : 'job_template']
    .replace('{work_request}', work_request).replace('{slice}', ordinal).replace('{job}', number);
}

// A PR belongs to a job through a "Card: WRn.Sm.Jk" line in its body, and stage is derived from those PRs live.
// The one stored fact is a job's first observed merged or closed state (`finished`), so the search only has to
// span unfinished jobs and a finished job keeps its stage after its PR leaves GitHub's 100-result window.
export const PULL_REQUEST_TTL_MS = 60_000;
// Building is the only stage a job can stall in without anyone else holding it. Codex jobs run in hours, so a day
// with no push to the open draft means stalled. Queued jobs wait their priority turn (dispatch-job starts nothing),
// and review and merge wait on others, so none of those is ever stale.
export const STALE_AFTER_MS = 24 * 3_600_000;
const CARD_LINE = /^Card:[ \t]*(WR\d+\.S\d+\.J\d+)\b/gmu;
const unavailablePullRequests = async repos => repos.map(repo => ({ repo, state: 'unavailable', reason: 'no_github_reader', prs: [] }));

// One GitHub search per repository per TTL, shared by every caller in this isolate; failures are cached too.
// `since` bounds it to PRs updated on or after the oldest unfinished job's day: a card ID does not exist until
// dispatch-job mints it, so any PR carrying one was created or edited after that, and older PRs cannot fill the window.
// The cache is keyed by repository and bound day, so no caller receives a narrower window than it asked for.
export function createPullRequestSource({ token, fetchImpl = (...a) => fetch(...a), now = Date.now, ttlMs = PULL_REQUEST_TTL_MS }) {
  const memo = new Map();
  async function search(repo, since) {
    if (!token) return { repo, state: 'unavailable', reason: 'credential_not_configured', prs: [] };
    const bound = since ? ` updated:>=${since.slice(0, 10)}` : '';
    const query = new URLSearchParams({ q: `repo:${repo} is:pr in:body "Card: WR"${bound}`, sort: 'updated', order: 'desc', per_page: '100' });
    try {
      const response = await fetchImpl(`https://api.github.com/search/issues?${query}`, { headers: {
        accept: 'application/vnd.github+json', authorization: `Bearer ${token}`, 'user-agent': 'carr-mcp-work-queue', 'x-github-api-version': '2022-11-28' } });
      if (!response.ok) return { repo, state: 'unavailable', reason: `github_http_${response.status}`, prs: [] };
      const body = await response.json();
      const prs = body.items.flatMap(item => {
        const cards = [...new Set([...String(item.body ?? '').matchAll(CARD_LINE)].map(match => match[1]))];
        return cards.length ? [{ repo, number: item.number, url: item.html_url, title: item.title, draft: item.draft === true, updated_at: item.updated_at,
          state: item.pull_request?.merged_at ? 'merged' : item.state === 'open' ? 'open' : 'closed', cards }] : [];
      });
      return { repo, state: 'ok', observed_at: new Date(now()).toISOString(), truncated: body.total_count > body.items.length, prs };
    } catch { return { repo, state: 'unavailable', reason: 'github_unreachable', prs: [] }; }
  }
  return (repos, since) => Promise.all(repos.map(repo => {
    const key = `${repo} ${since?.slice(0, 10) ?? ''}`;
    const hit = memo.get(key);
    if (hit && now() - hit.at < ttlMs) return hit.value;
    const value = search(repo, since);
    for (const [old, entry] of memo) if (now() - entry.at >= ttlMs) memo.delete(old);
    memo.set(key, { at: now(), value });
    return value;
  }));
}

let isolateSource;
export function isolatePullRequests(token) {
  if (!isolateSource || isolateSource.token !== token) isolateSource = { token, read: createPullRequestSource({ token }) };
  return isolateSource.read;
}

export function jobStage(prs) {
  const open = prs.filter(pr => pr.state === 'open');
  if (open.some(pr => !pr.draft)) return 'review';
  if (open.length) return 'build';
  if (prs.some(pr => pr.state === 'merged')) return 'merged';
  return prs.length ? 'closed' : 'queued';
}

export function queueView(rows, sources, at) {
  const byCard = new Map();
  for (const pr of sources.flatMap(source => source.prs)) for (const card of pr.cards) {
    const { cards, ...fact } = pr;
    byCard.set(card, [...(byCard.get(card) ?? []), { ...fact, card_id: `${card}.${addressFormat.repositories[pr.repo]}#${pr.number}` }]);
  }
  // A stall is only claimed when every repository answered in full: an unreadable or truncated one may hold the newer push.
  const complete = sources.every(source => source.state === 'ok' && !source.truncated);
  const finishes = [];
  const jobs = rows.map(({ work_request_id, created_at, finished, ...row }) => {
    const card_id = queueCardId(row.work_request, row.slice_ordinal, row.j_number);
    const live = byCard.get(card_id) ?? [];
    const prs = live.length || !finished ? live : finished.prs;
    const stage = live.length || !finished ? jobStage(live) : finished.stage;
    if (!finished && (stage === 'merged' || stage === 'closed'))
      finishes.push({ key: [work_request_id, row.slice_ordinal, row.j_number], finished: { stage, prs } });
    const since = stage === 'build' ? Math.max(...prs.filter(pr => pr.state === 'open').map(pr => Date.parse(pr.updated_at))) : NaN;
    return { ...row, card_id, created_at: new Date(created_at).toISOString(), stage, stale: complete && at - since >= STALE_AFTER_MS, prs };
  });
  return { jobs, finishes, github: sources.map(({ prs, ...source }) => source) };
}

export function workQueueTools({ withEnvelope, writeEvent, ToolError }) {
  const refuse = error => { throw new ToolError({ error }); };
  const validate = (args, properties, required) => {
    if (Object.keys(args).some(key => !Object.hasOwn(properties, key)) || required.some(key => args[key] == null)) refuse('invalid_work_queue_fields');
    for (const [key, value] of Object.entries(args)) {
      const schema = properties[key];
      if (schema.type === 'string' && (typeof value !== 'string' || !value.trim() || value.length > (schema.maxLength || 100))) refuse('invalid_work_queue_fields');
      if (schema.type === 'integer' && (!Number.isSafeInteger(value) || value < 1 || value > schema.maximum)) refuse('invalid_work_queue_fields');
    }
    if (args.work_request && !new RegExp(addressFormat.work_request_pattern).test(args.work_request)) refuse('invalid_work_request');
    if (args.idempotency_key && !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(args.idempotency_key)) refuse('invalid_idempotency_key');
  };
  const request = async (c, actor, ref) => {
    const row = (await c.query(`select w.id,w.ref,w.title from ops.work_request w
      where w.ref=$1 and w.organization_tenant_id=$2 and ${readableWorkRequestSql('w')}`, [ref, organizationTenantForActor(actor)])).rows[0];
    if (!row) refuse('work_request_not_found');
    return row;
  };
  const ordered = async (c, actor) => (await c.query(`select j.*,w.ref as work_request,w.title as work_request_title from ops.work_request_job j
    join ops.work_request w on w.id=j.work_request_id where w.organization_tenant_id=$1
    and ${readableWorkRequestSql('w')} order by j.priority,w.ref,j.slice_ordinal,j.j_number`, [organizationTenantForActor(actor)])).rows;
  const command = (name, description, extra, required, run, schema = {}) => {
    const properties = { ...base, ...extra }, needed = ['idempotency_key', 'work_request', ...required];
    return { write: true, description,
      inputSchema: { type: 'object', additionalProperties: false, properties, required: needed, ...schema },
      handler: async (c, actor, args) => {
        validate(args, properties, needed);
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, name, args, async () => {
          await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', ['work-queue:' + organizationTenantForActor(actor)]);
          const wr = await request(c, actor, args.work_request);
          const result = await run(c, actor, args, wr);
          await writeEvent(c, actor, name, 'ops_work_request', wr.id, { field: 'work_queue', new: result, idempotency_key: args.idempotency_key });
          return { ok: true, ...result };
        });
      } };
  };
  return {
    'plan-work-request-slices': command('plan-work-request-slices', 'Record ordered slice titles once. Omit slices for implicit S1. Repeating the same plan preserves identities.',
      { slices: { type: 'array', minItems: 1, maxItems: 100, items: text(200) } }, [], async (c, actor, args, wr) => {
        const titles = Object.hasOwn(args, 'slices') ? args.slices : [wr.title];
        if (!Array.isArray(titles) || !titles.length || titles.length > 100 || titles.some(title => typeof title !== 'string' || !title.trim() || title.length > 200)) refuse('invalid_slice_plan');
        const prior = (await c.query('select title from ops.work_request_slice where work_request_id=$1 order by ordinal', [wr.id])).rows;
        if (prior.length && JSON.stringify(prior.map(row => row.title)) !== JSON.stringify(titles)) refuse('slice_plan_conflict');
        if (!prior.length) await c.query(`insert into ops.work_request_slice
          select $1,ordinality,title from unnest($2::text[]) with ordinality as s(title,ordinality)`, [wr.id, titles]);
        return { slices: titles.map((title, index) => ({ slice_id: queueCardId(wr.ref, index + 1), title })) };
      }),
    'dispatch-job': command('dispatch-job', 'Mint a queued job from facts, or record a runner start for an existing job exactly once.',
      { slice, job: positive, runner_job_id: text(), repository: { ...text(), enum: repositories }, executor: text(), model: text(), effort: text(), card_title: text(80), description: text(500) },
      [], async (c, actor, args, wr) => {
        const starting = jobStart.some(key => Object.hasOwn(args, key));
        if ((starting ? jobStart : jobFacts).some(key => args[key] == null)
          || (starting ? jobFacts : jobStart).some(key => Object.hasOwn(args, key))) refuse('invalid_work_queue_fields');
        if ((starting || Object.hasOwn(args, 'repository')) && !repositories.includes(args.repository)) refuse('invalid_work_queue_fields');
        if (starting) {
          const row = (await c.query(`select runner_job_id,repository from ops.work_request_job
            where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [wr.id, args.slice || 1, args.job])).rows[0];
          if (!row) refuse('job_not_found');
          if (row.repository !== args.repository) refuse('job_repository_mismatch');
          if (row.runner_job_id != null) refuse('job_already_started');
          await c.query(`update ops.work_request_job set runner_job_id=$4,started_at=now()
            where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [wr.id, args.slice || 1, args.job, args.runner_job_id]);
          return { card_id: queueCardId(wr.ref, args.slice || 1, args.job), runner_job_id: args.runner_job_id };
        }
        if (!/^[A-Za-z']+( [A-Za-z']+){1,2}$/.test(args.card_title)) refuse('invalid_card_title');
        if (args.description.trim().split(/[.!?](?:\s|$)/u).filter(Boolean).length !== 1) refuse('invalid_job_description');
        const ordinal = args.slice || 1;
        await c.query(`insert into ops.work_request_slice select $1,1,$2
          where not exists (select 1 from ops.work_request_slice where work_request_id=$1)`, [wr.id, wr.title]);
        if (!(await c.query('select 1 from ops.work_request_slice where work_request_id=$1 and ordinal=$2', [wr.id, ordinal])).rows.length) refuse('slice_not_found');
        const priority = Number((await c.query(`select coalesce(max(j.priority),0)+1 as next from ops.work_request_job j
          join ops.work_request w on w.id=j.work_request_id where w.organization_tenant_id=$1`, [organizationTenantForActor(actor)])).rows[0].next);
        const number = (await c.query(`insert into ops.work_request_job
          (work_request_id,slice_ordinal,j_number,priority,executor,model,effort,card_title,description,repository)
          select $1,$2,coalesce(max(j_number),0)+1,$3,$4,$5,$6,$7,$8,$9 from ops.work_request_job
          where work_request_id=$1 and slice_ordinal=$2 returning j_number`,
        [wr.id, ordinal, priority, args.executor, args.model, args.effort, args.card_title, args.description, args.repository ?? null])).rows[0].j_number;
        return { card_id: queueCardId(wr.ref, ordinal, number), priority };
      }, { anyOf: [
        { required: jobFacts },
        { required: [...jobStart, 'repository'] },
      ] }),
    'prioritize-job': command('prioritize-job', 'Move one visible job to a one-based queue position, preserving the relative order of its siblings.',
      { slice, job: positive, position: positive }, ['job', 'position'], async (c, actor, args, wr) => {
        const rows = await ordered(c, actor), slots = rows.map(row => row.priority);
        const index = rows.findIndex(row => row.work_request_id === wr.id && row.slice_ordinal === (args.slice || 1) && row.j_number === args.job);
        if (index < 0) refuse('job_not_found');
        if (args.position > rows.length) refuse('priority_out_of_range');
        rows.splice(args.position - 1, 0, rows.splice(index, 1)[0]);
        for (const [position, row] of rows.entries()) if (row.priority !== slots[position]) {
          await c.query(`update ops.work_request_job set priority=$4
            where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [row.work_request_id, row.slice_ordinal, row.j_number, slots[position]]);
        }
        return { card_id: queueCardId(wr.ref, args.slice || 1, args.job), priority: slots[args.position - 1] };
      }),
    'work-queue': { write: false, description: 'Read all visible jobs in priority order with stable card IDs, Work Request titles, and stage, PRs, and staleness derived from Card lines on GitHub pull requests.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async (c, actor, args) => {
        validate(args, {}, []);
        const rows = await ordered(c, actor);
        const oldest = rows.filter(row => !row.finished).reduce((min, row) => Math.min(min, new Date(row.created_at).getTime()), Infinity);
        const sources = await (c.pullRequests ?? unavailablePullRequests)(Object.keys(addressFormat.repositories),
          Number.isFinite(oldest) ? new Date(oldest).toISOString() : undefined);
        const { finishes, ...view } = queueView(rows, sources, Date.now());
        // One conditional single-row update per newly finished job, on the reader route's optional side connection:
        // it runs only on the first observation, writes no audit row, and a lost write is redone by the next read.
        for (const { key, finished } of finishes) c.sideWrite?.(`update ops.work_request_job set finished=$4::jsonb
          where work_request_id=$1 and slice_ordinal=$2 and j_number=$3 and finished is null`, [...key, JSON.stringify(finished)]);
        return { ok: true, stale_after_hours: STALE_AFTER_MS / 3_600_000, ...view };
      } },
  };
}
