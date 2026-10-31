// Store lean Work Request slice and job facts with stable IDs and priority order.
import cardVerbs from './job-card-verbs.v1.json' with { type: 'json' };
import jobTypes from './job-types.v1.json' with { type: 'json' };
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
// Historical finished records remain readable; the queue read never writes observations.
export const PULL_REQUEST_TTL_MS = 60_000;
// Building is the only stage a job can stall in without anyone else holding it. Codex jobs run in hours, so a day
// with no push to the open draft means stalled. Queued jobs wait their priority turn (dispatch-job starts nothing),
// and review and merge wait on others, so none of those is ever stale.
export const STALE_AFTER_MS = 24 * 3_600_000;
const CARD_LINE = /^Card:[ \t]*(WR\d+\.S\d+\.J\d+)\b/gmu;

// Readiness is derived from the recorded plan; it grants no completion authority.
const validSliceChange = change => change && typeof change === 'object' && Object.keys(change).length === 2
  && typeof change.pr_url === 'string' && typeof change.merge_commit_sha === 'string'
  && /^https:\/\/github[.]com\/(DoctorCRE\/carr-system|jbookout\/(doctorcre-app|software-factory))\/pull\/[1-9][0-9]*$/.test(change.pr_url)
  && /^[0-9a-fA-F]{40}$/.test(change.merge_commit_sha);
export function readyToClose(state, slices) {
  if (['confirmed_closed','declined','superseded','failed'].includes(state) || !slices.length) return false;
  return slices.every(slice => slice.outcome === 'retired' ||
    (slice.outcome === 'done' && Array.isArray(slice.change_refs) && slice.change_refs.length > 0 && slice.change_refs.every(validSliceChange)))
    && slices.some(slice => slice.outcome === 'done');
}

const unavailablePullRequests = async repos => repos.map(repo => ({ repo, state: 'unavailable', reason: 'no_github_reader', prs: [] }));

// One GitHub search per repository per TTL, shared by every caller in this isolate; failures are cached too.
// `since` bounds it to PRs updated on or after the oldest started, unfinished job's day.
// Search all available pages so a first read can find a PR behind newer results.
// The cache is keyed by repository and bound day, so no caller receives a narrower window than it asked for.
export function createPullRequestSource({ token, fetchImpl = (...a) => fetch(...a), now = Date.now, ttlMs = PULL_REQUEST_TTL_MS }) {
  const memo = new Map();
  async function search(repo, since) {
    if (!token) return { repo, state: 'unavailable', reason: 'credential_not_configured', prs: [] };
    const bound = since ? ` updated:>=${since.slice(0, 10)}` : '';
    const query = new URLSearchParams({ q: `repo:${repo} is:pr in:body "Card: WR"${bound}`, sort: 'updated', order: 'desc', per_page: '100' });
    try {
      const items = [];
      let total = 0, incomplete = false;
      for (let page = 1; page <= 10; page++) {
        query.set('page', String(page));
        const response = await fetchImpl(`https://api.github.com/search/issues?${query}`, { headers: {
          accept: 'application/vnd.github+json', authorization: `Bearer ${token}`, 'user-agent': 'carr-mcp-work-queue', 'x-github-api-version': '2022-11-28' } });
        if (!response.ok) return { repo, state: 'unavailable', reason: `github_http_${response.status}`, prs: [] };
        const body = await response.json();
        total = body.total_count;
        incomplete ||= body.incomplete_results === true;
        items.push(...body.items);
        if (items.length >= total || body.items.length < 100) break;
      }
      const prs = items.flatMap(item => {
        const cards = [...new Set([...String(item.body ?? '').matchAll(CARD_LINE)].map(match => match[1]))];
        return cards.length ? [{ repo, number: item.number, url: item.html_url, title: item.title, draft: item.draft === true, updated_at: item.updated_at,
          state: item.pull_request?.merged_at ? 'merged' : item.state === 'open' ? 'open' : 'closed', cards }] : [];
      });
      return { repo, state: 'ok', observed_at: new Date(now()).toISOString(), truncated: incomplete || total > items.length, prs };
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


const RELEASE_URLS = {
  'DoctorCRE/carr-system': 'https://api.doctorcre.com/release',
  'jbookout/doctorcre-app': 'https://app.doctorcre.com/app-release',
};
const SHA = /^[0-9a-f]{40}$/i;

// Cache successful and failed observations alike so opening the board never starts a poller.
export function createProductionSource({ token, fetchImpl = (...args) => fetch(...args), now = Date.now }) {
  const cache = new Map();
  const cursors = new Map();
  const cached = (key, load, ttl = PULL_REQUEST_TTL_MS) => {
    const hit = cache.get(key);
    if (hit && now() - hit.at < hit.ttl) return hit.value;
    const entry = { at: now(), ttl, value: null };
    entry.value = Promise.resolve().then(load).catch(() => null).then(result => {
      if (result === null) entry.ttl = PULL_REQUEST_TTL_MS;
      return result;
    });
    if (cache.size >= 2000) cache.delete(cache.keys().next().value);
    cache.set(key, entry);
    return entry.value;
  };
  let active = 0;
  const waiting = [];
  const json = async (url, options = {}) => {
    if (active >= 4) await new Promise(resolve => waiting.push(resolve));
    else active++;
    try {
      const response = await fetchImpl(url, { ...options, signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error('read failed');
      return await response.json();
    } finally { const next = waiting.shift(); if (next) next(); else active--; }
  };
  const headers = { accept: 'application/vnd.github+json', authorization: `Bearer ${token}`, 'user-agent': 'carr-mcp-work-queue' };
  return async prs => {
    const results = prs.map(() => null);
    if (!token) return results;
    const repos = Object.keys(RELEASE_URLS).filter(repo => prs.some(pr => pr.repo === repo && pr.state === 'merged'));
    for (const repo of repos) {
      let budget = Math.floor(8 / repos.length);
      const eligible = prs.map((pr, index) => ({ pr, index })).filter(({ pr }) => pr.repo === repo && pr.state === 'merged'
        && Number.isSafeInteger(pr.number) && pr.number > 0);
      if (!eligible.length) continue;
      const start = (cursors.get(repo) ?? 0) % eligible.length;
      const items = [...eligible.slice(start), ...eligible.slice(0, start)].slice(0, 250);
      cursors.set(repo, (start + items.length) % eligible.length);
      const release = await cached(`release:${repo}`, async () => {
        const data = await json(RELEASE_URLS[repo]);
        const sha = repo === 'DoctorCRE/carr-system' ? data.env?.value === 'production' ? data.git_sha?.value : null
          : data.environment === 'production' ? data.source_commit : null;
        return SHA.test(sha || '') ? sha : null;
      });
      if (!release) continue;
      const [owner, name] = repo.split('/');
      const fields = items.map(({ pr }, i) => `p${i}: pullRequest(number: ${pr.number}) { mergeCommit { oid } }`).join(' ');
      const batch = await cached(`prs:${repo}:${items.map(({ pr }) => pr.number).join(',')}`, async () => {
        const data = await json('https://api.github.com/graphql', { method: 'POST', headers: { ...headers, 'content-type': 'application/json' },
          body: JSON.stringify({ query: `query { repository(owner: "${owner}", name: "${name}") { ${fields} } }` }) });
        return data.errors?.length ? null : data.data?.repository ?? null;
      });
      if (!batch) continue;
      const tasks = [];
      for (const [i, { pr, index }] of items.entries()) {
        const merge = batch[`p${i}`]?.mergeCommit?.oid;
        if (!SHA.test(merge || '')) continue;
        if (merge === release) { results[index] = true; continue; }
        const key = `ancestry:${repo}:${merge}:${release}`;
        const hit = cache.get(key);
        if (!hit || now() - hit.at >= hit.ttl) { if (!budget) continue; budget--; }
        tasks.push({ index, key, merge });
      }
      for (let offset = 0; offset < tasks.length; offset += 4) {
        await Promise.all(tasks.slice(offset, offset + 4).map(async ({ index, key, merge }) => {
          results[index] = await cached(key, async () => {
            const comparison = await json(`https://api.github.com/repos/${repo}/compare/${merge}...${release}`, { headers });
            if (comparison.status === 'ahead' || comparison.status === 'identical') return true;
            if (comparison.status === 'behind' || comparison.status === 'diverged') return false;
            return null;
          }, 24 * 60 * 60_000);
        }));
      }
    }
    return results;
  };
}

let productionSource;
export function isolateProduction(token) {
  if (!productionSource || productionSource.token !== token) productionSource = { token, read: createProductionSource({ token }) };
  return productionSource.read;
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
  // A stall is only claimed when every repository answered in full: an unreadable or truncated (including GitHub's incomplete_results) one may hold the newer push.
  const complete = sources.every(source => source.state === 'ok' && !source.truncated);
  const jobs = rows.map(({ work_request_id, created_at, finished, ...row }) => {
    const card_id = queueCardId(row.work_request, row.slice_ordinal, row.j_number);
    const live = byCard.get(card_id) ?? [];
    const prs = live.length || !finished ? live : finished.prs;
    const stage = live.length || !finished ? (live.length ? jobStage(live) : row.started_at ? 'running' : 'queued') : finished.stage;
    const since = stage === 'build' ? Math.max(...prs.filter(pr => pr.state === 'open').map(pr => Date.parse(pr.updated_at))) : NaN;
    return { ...row, card_id, created_at: new Date(created_at).toISOString(), stage, stale: complete && at - since >= STALE_AFTER_MS,
      pr_evidence_complete: complete && (!finished || finished.prs.every(saved => live.some(pr => pr.repo === saved.repo && pr.number === saved.number))), prs };
  });
  return { jobs, github: sources.map(({ prs, ...source }) => source) };
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
  const suggestTitle = async (c, wr, args, current) => {
    const words = (String(args.card_title || args.description || '').match(/[A-Za-z']+/g) || []).filter(word => /[A-Za-z]/.test(word));
    const verb = cardVerbs.includes(words[0]) ? words.shift() : 'Build';
    const subject = (words[0] || 'work').slice(0, 30);
    const used = new Set((await c.query(`select card_title from ops.work_request_job where work_request_id=$1
      and not (slice_ordinal=$2 and j_number=$3)`, [wr.id, current?.slice ?? 0, current?.job ?? 0])).rows.map(row => row.card_title.toLowerCase()));
    let title = `${verb} ${subject}`;
    for (let n = 0; used.has(title.toLowerCase()); n++) {
      let suffix = '', value = n;
      do { suffix = String.fromCharCode(97 + value % 26) + suffix; value = Math.floor(value / 26) - 1; } while (value >= 0);
      title = `${verb} ${subject} ${suffix}`;
    }
    return title;
  };
  const suggestDescription = value => {
    const words = String(value || 'the work').replace(/[.!?\r\n]+/gu, ' ').trim().split(/\s+/u);
    const verb = cardVerbs.includes(words[0]) ? words.shift() : 'Build';
    return `${verb} ${words.join(' ').slice(0, 490) || 'the work'}.`;
  };
  const validateCopy = async (c, wr, args, current) => {
    const titleRefusal = async error => { throw new ToolError({ error, suggested_card_title: await suggestTitle(c, wr, args, current) }); };
    if (!/^[A-Za-z']+( [A-Za-z']+){1,2}$/.test(args.card_title)) await titleRefusal('invalid_card_title');
    if (!cardVerbs.includes(args.card_title.split(' ')[0])) await titleRefusal('card_title_verb');
    if (args.description != null && (/[\r\n]/u.test(args.description) || args.description.trim().split(/[.!?](?:\s|$)/u).filter(Boolean).length !== 1
      || !cardVerbs.includes(args.description.split(/\s/u)[0]))) {
      throw new ToolError({ error: 'invalid_job_description', suggested_description: suggestDescription(args.description) });
    }
    if ((await c.query(`select 1 from ops.work_request_job where work_request_id=$1
      and lower(card_title)=lower($2) and not (slice_ordinal=$3 and j_number=$4)`,
    [wr.id, args.card_title, current?.slice ?? 0, current?.job ?? 0])).rows.length) await titleRefusal('card_title_duplicate');
  };
  const request = async (c, actor, ref) => {
    const row = (await c.query(`select w.id,w.ref,w.title,w.state from ops.work_request w
      where w.ref=$1 and w.organization_tenant_id=$2 and ${readableWorkRequestSql('w')}`, [ref, organizationTenantForActor(actor)])).rows[0];
    if (!row) refuse('work_request_not_found');
    return row;
  };
  const ordered = async (c, actor) => (await c.query(`select j.*,w.ref as work_request,w.title as work_request_title from ops.work_request_job j
    join ops.work_request w on w.id=j.work_request_id where w.organization_tenant_id=$1
    and ${readableWorkRequestSql('w')} order by j.priority,w.ref,j.slice_ordinal,j.j_number`, [organizationTenantForActor(actor)])).rows;
  const command = (name, description, extra, required, run, schema = {}) => {
    const byCard = Object.hasOwn(extra, 'card_id');
    const properties = { ...(byCard ? { idempotency_key: base.idempotency_key } : base), ...extra };
    const needed = ['idempotency_key', ...(byCard ? [] : ['work_request']), ...required];
    return { write: true, description,
      inputSchema: { type: 'object', additionalProperties: false, properties, required: needed, ...schema },
      handler: async (c, actor, args) => {
        try { validate(args, properties, needed); } catch (error) {
          if (name === 'dispatch-job' && error.message === 'invalid_work_queue_fields') {
            const invalid = key => Object.hasOwn(args, key) && (typeof args[key] !== 'string' || !args[key].trim() || args[key].length > properties[key].maxLength);
            if (invalid('card_title') || invalid('description')) {
              let wr;
              try { wr = await request(c, actor, args.work_request); } catch { throw error; }
              throw new ToolError({ error: error.message,
                ...(invalid('card_title') ? { suggested_card_title: await suggestTitle(c, wr, args) } : {}),
                ...(invalid('description') ? { suggested_description: suggestDescription(args.description || args.card_title) } : {}) });
            }
          }
          throw error;
        }
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, name, args, async () => {
          await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', ['work-queue:' + organizationTenantForActor(actor)]);
          const card = byCard && /^WR([1-9]\d*)[.]S([1-9]\d*)[.]J([1-9]\d*)$/.exec(args.card_id);
          if (byCard && (!card || card.slice(2).some(n => !Number.isSafeInteger(Number(n)) || Number(n) > positive.maximum))) refuse('job_not_found');
          const wr = await request(c, actor, byCard ? `WR-${card[1].padStart(6, '0')}` : args.work_request);
          if (['confirmed_closed', 'declined', 'superseded', 'failed'].includes(wr.state)) refuse('work_request_terminal_state');
          const result = await run(c, actor, byCard ? { ...args, slice: Number(card[2]), job: Number(card[3]) } : args, wr);
          await writeEvent(c, actor, name, 'ops_work_request', wr.id, { field: 'work_queue', new: result, idempotency_key: args.idempotency_key });
          return { ok: true, ...result };
        });
      } };
  };
  return {
    'plan-work-request-slices': command('plan-work-request-slices', 'Record or append ordered slice titles. Existing titles must remain an exact prefix. Omit slices for implicit S1. Repeating the same plan preserves identities.',
      { slices: { type: 'array', minItems: 1, maxItems: 100, items: text(200) } }, [], async (c, actor, args, wr) => {
        const titles = Object.hasOwn(args, 'slices') ? args.slices : [wr.title];
        if (!Array.isArray(titles) || titles.length > 100 || titles.some(title => typeof title !== 'string' || !title.trim() || title.length > 200)) refuse('invalid_slice_plan');
        const prior = (await c.query('select title from ops.work_request_slice where work_request_id=$1 order by ordinal', [wr.id])).rows;
        if (prior.some((row, index) => row.title !== titles[index]))
          throw new ToolError({ error: 'slice_plan_conflict', recorded_titles: prior.map(row => row.title) });
        if (!titles.length) refuse('invalid_slice_plan');
        if (titles.length > prior.length) await c.query(`insert into ops.work_request_slice (work_request_id,ordinal,title)
          select $1,$3::integer+ordinality,title from unnest($2::text[]) with ordinality as s(title,ordinality)`, [wr.id, titles.slice(prior.length), prior.length]);
        return { slices: titles.map((title, index) => ({ slice_id: queueCardId(wr.ref, index + 1), title })) };
      }),
    'close-work-request-slice': command('close-work-request-slice', 'Record an open slice as done or retired exactly once, using the current slice version as base_version. Done requires merged change evidence.',
      { slice, base_version: positive, outcome: { ...text(), enum: ['done', 'retired'] }, reason: text(2000), superseded_by: positive,
        change_refs: { type: 'array', minItems: 1, items: { type: 'object', additionalProperties: false,
          properties: { pr_url: text(300), merge_commit_sha: text(40) }, required: ['pr_url', 'merge_commit_sha'] } } },
      ['slice', 'outcome', 'base_version'], async (c, actor, args, wr) => {
        const current = (await c.query('select outcome,version from ops.work_request_slice where work_request_id=$1 and ordinal=$2 for update', [wr.id, args.slice])).rows[0];
        if (!current) refuse('slice_not_found');
        if (current.version !== args.base_version) refuse('version_conflict');
        if (current.outcome.outcome !== 'open') refuse('slice_outcome_already_recorded');
        if (!['done', 'retired'].includes(args.outcome)) refuse('invalid_slice_outcome');
        if (args.outcome !== 'done' && !args.reason?.trim()) refuse('slice_reason_required');
        if (args.superseded_by != null && (args.outcome !== 'retired' || args.superseded_by === args.slice)) refuse('invalid_slice_successor');
        if (args.superseded_by != null && !(await c.query('select 1 from ops.work_request_slice where work_request_id=$1 and ordinal=$2',
          [wr.id, args.superseded_by])).rows.length) refuse('slice_not_found');
        const changes = args.change_refs;
        if (args.outcome === 'done') {
          if (!Array.isArray(changes) || !changes.length || changes.some(change => !validSliceChange(change))) refuse('slice_merged_change_required');
        } else if (changes != null) refuse('invalid_slice_outcome');
        const record = { outcome: args.outcome, outcome_by: actor.slug, outcome_at: new Date().toISOString(),
          ...(args.reason ? { reason: args.reason.trim() } : {}),
          ...(args.outcome === 'done' ? { change_refs: changes } : {}),
          ...(args.superseded_by ? { superseded_by: queueCardId(wr.ref, args.superseded_by) } : {}) };
        const saved = (await c.query(`update ops.work_request_slice set outcome=$3::jsonb,version=version+1
          where work_request_id=$1 and ordinal=$2 and outcome->>'outcome'='open' returning ordinal,version`, [wr.id, args.slice, JSON.stringify(record)])).rows[0];
        if (!saved) refuse('slice_not_found');
        return { slice_id: queueCardId(wr.ref, saved.ordinal), version: saved.version, ...record };
      }),
    'dispatch-job': command('dispatch-job', 'Mint a queued job with a caller-supplied job_type from job-workflows.v1.yaml, or record a runner start for an existing job exactly once.',
      { slice, job: positive, runner_job_id: text(), repository: { ...text(), enum: repositories }, job_type: { ...text(), enum: jobTypes }, executor: text(), model: text(), effort: text(), card_title: text(80), description: text(500) },
      [], async (c, actor, args, wr) => {
        const starting = jobStart.some(key => Object.hasOwn(args, key));
        if ((!starting || Object.hasOwn(args, 'job_type')) && !jobTypes.includes(args.job_type)) throw new ToolError({ error: 'invalid_job_type', allowed_values: jobTypes });
        if ((starting ? jobStart : jobFacts).some(key => args[key] == null)
          || (jobFacts.some(key => Object.hasOwn(args, key)) && jobFacts.some(key => args[key] == null))) {
          throw new ToolError({ error: 'invalid_work_queue_fields',
            ...(!starting && args.card_title == null ? { suggested_card_title: await suggestTitle(c, wr, args) } : {}),
            ...(!starting && args.description == null ? { suggested_description: suggestDescription(args.card_title) } : {}) });
        }
        if ((starting || Object.hasOwn(args, 'repository')) && !repositories.includes(args.repository)) refuse('invalid_work_queue_fields');
        if (starting) {
          const row = (await c.query(`select runner_job_id,repository,job_type from ops.work_request_job
            where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [wr.id, args.slice || 1, args.job])).rows[0];
          if (!row && jobFacts.some(key => args[key] == null)) refuse('job_not_found');
          if (row) {
            if (args.job_type != null && row.job_type != null && row.job_type !== args.job_type) refuse('job_type_mismatch');
            if (row.repository != null && row.repository !== args.repository) refuse('job_repository_mismatch');
            if (row.runner_job_id != null) refuse('job_already_started');
            await c.query(`update ops.work_request_job set runner_job_id=$4,started_at=now(),
              executor=coalesce($5,executor),model=coalesce($6,model),effort=coalesce($7,effort),repository=coalesce(repository,$8),job_type=coalesce(job_type,$9)
              where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [wr.id, args.slice || 1, args.job, args.runner_job_id,
              args.executor ?? null, args.model ?? null, args.effort ?? null, args.repository, args.job_type ?? null]);
            return { card_id: queueCardId(wr.ref, args.slice || 1, args.job), runner_job_id: args.runner_job_id };
          }
        }
        if (!jobTypes.includes(args.job_type)) throw new ToolError({ error: 'invalid_job_type', allowed_values: jobTypes });
        await validateCopy(c, wr, args);
        const ordinal = args.slice || 1;
        if (!starting) await c.query(`insert into ops.work_request_slice (work_request_id,ordinal,title) select $1,1,$2
          where not exists (select 1 from ops.work_request_slice where work_request_id=$1)`, [wr.id, wr.title]);
        if (!(await c.query('select 1 from ops.work_request_slice where work_request_id=$1 and ordinal=$2', [wr.id, ordinal])).rows.length) refuse('slice_not_found');
        const priority = Number((await c.query(`select coalesce(max(j.priority),0)+1 as next from ops.work_request_job j
          join ops.work_request w on w.id=j.work_request_id where w.organization_tenant_id=$1`, [organizationTenantForActor(actor)])).rows[0].next);
        const number = (await c.query(`insert into ops.work_request_job
          (work_request_id,slice_ordinal,j_number,priority,executor,model,effort,card_title,description,repository,runner_job_id,started_at,job_type)
          select $1,$2,coalesce($10,max(j_number)+1,1),$3,$4,$5,$6,$7,$8,$9,$11,case when $11::text is not null then now() end,$12 from ops.work_request_job
          where work_request_id=$1 and slice_ordinal=$2 returning j_number`,
        [wr.id, ordinal, priority, args.executor, args.model, args.effort, args.card_title, args.description, args.repository ?? null, args.job ?? null, args.runner_job_id ?? null, args.job_type])).rows[0].j_number;
        return { card_id: queueCardId(wr.ref, ordinal, number), priority,
          ...(starting ? { runner_job_id: args.runner_job_id } : {}) };
      }, { anyOf: [
        { required: jobFacts },
        { required: [...jobStart, 'repository'] },
      ] }),
    'retitle-job': command('retitle-job', 'Rename one job card using its current base_version and optionally replace its one-sentence description. Preserve all other job facts.',
      { card_id: text(), card_title: text(80), description: text(500), base_version: positive }, ['card_id', 'card_title', 'base_version'], async (c, actor, args, wr) => {
        const current = (await c.query(`select card_title,description,version from ops.work_request_job
          where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [wr.id, args.slice, args.job])).rows[0];
        if (!current) refuse('job_not_found');
        if (current.version !== args.base_version) throw new ToolError({ error: 'version_conflict', current_version: current.version });
        const description = args.description ?? current.description;
        await validateCopy(c, wr, { ...args, description }, args);
        await c.query(`update ops.work_request_job set card_title=$4,description=$5,version=version+1
          where work_request_id=$1 and slice_ordinal=$2 and j_number=$3`, [wr.id, args.slice, args.job, args.card_title, description]);
        return { card_id: args.card_id, card_title: args.card_title, description, version: current.version + 1 };
      }),
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
    'work-queue': { write: false, description: 'Read all visible jobs in priority order with stable card IDs, Work Request titles, and stage, PRs, and staleness derived from Card lines on GitHub pull requests. ready_to_close lists requests whose nonempty slice plan has shipped work, merged evidence for every done slice, and no open slices; completion still requires independent attestation.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async (c, actor, args) => {
        validate(args, {}, []);
        const rows = await ordered(c, actor);
        const sources = await (c.pullRequests ?? unavailablePullRequests)(Object.keys(addressFormat.repositories));
        const view = queueView(rows, sources, Date.now());
        const prs = view.jobs.flatMap(job => job.prs);
        const live = c.production ? await c.production(prs) : prs.map(() => null);
        prs.forEach((pr, index) => { pr.production_live = live[index]; });
        const slices = (await c.query(`select s.*,w.ref as work_request,w.title as work_request_title,w.state as work_request_state from ops.work_request_slice s
          join ops.work_request w on w.id=s.work_request_id where w.organization_tenant_id=$1 and ${readableWorkRequestSql('w')}
          order by w.ref,s.ordinal`, [organizationTenantForActor(actor)])).rows;
        const projected = slices.map(row => ({ slice_id: queueCardId(row.work_request, row.ordinal),
          work_request: row.work_request, title: row.title, version: row.version, ...row.outcome }));
        const requests = Map.groupBy(slices, row => row.work_request);
        const ready_to_close = [...requests].filter(([, group]) => readyToClose(group[0].work_request_state, group.map(row => row.outcome)))
          .map(([human_ref, group]) => ({ human_ref, title: group[0].work_request_title, state: group[0].work_request_state }));
        return { ok: true, ready_to_close, slices: projected, stale_after_hours: STALE_AFTER_MS / 3_600_000, ...view };
      } },
  };
}
