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
  const ordered = async (c, actor) => (await c.query(`select j.*,w.ref as work_request from ops.work_request_job j
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
    'work-queue': { write: false, description: 'Read all visible job facts in priority order with stable card IDs. The board resolves PRs and stages live.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async (c, actor, args) => {
        validate(args, {}, []);
        return { ok: true, jobs: (await ordered(c, actor)).map(({ work_request_id, ...row }) => ({ ...row,
          card_id: queueCardId(row.work_request, row.slice_ordinal, row.j_number) })) };
      } },
  };
}
