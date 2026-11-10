// Partner subscriptions preserve each actor's initial canonical subject baseline.
import { parseSubjectWatchV2, subjectWatchPolicySchema, validSubjectWatchPolicy } from '../contracts/subject-watch.v2.ts';
export { parseSubjectWatchV2 } from '../contracts/subject-watch.v2.ts';
const VERSION = 'subject-watch.v1';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const targetProperties = {
  subject_type: { type: 'string', enum: ['deal', 'lease', 'listing'] },
  subject_id: { type: 'string', pattern: UUID.source },
};

export function subjectWatchTools({ withEnvelope, ToolError }) {
  const unwrap = result => {
    if (!result?.ok) throw new ToolError({ error: result?.error || 'subject_watch_unavailable' });
    return result;
  };
  const target = async (c, args) => unwrap((await c.query(
    'select ops.read_subject_watch($1,$2::uuid) as result', [args.subject_type,args.subject_id])).rows[0]?.result);
  const commands = Object.fromEntries(['watch','unwatch'].map(command => {
    const verb = `${command}-subject-v1`;
    return [verb, {
      write: true,
      description: `${command === 'watch' ? 'Subscribe to' : 'Remove your subscription to'} one canonical deal or lease. Server-derived partner identity only. Listing targets refuse until a canonical listing contract exists. Watching captures an initial version and never emits a notification. Rewatch preserves the baseline. Unwatch works even after target access loss. UUID replay keys bind actor, verb and payload.`,
      inputSchema: { type: 'object', additionalProperties: false,
        properties: { ...targetProperties, idempotency_key: { type: 'string', pattern: UUID.source } },
        required: ['subject_type','subject_id','idempotency_key'] },
      handler: async (c, actor, args) => {
        if (command === 'watch') await target(c,args);
        else await c.query('select ops.read_subject_watch($1,$2::uuid)',[args.subject_type,args.subject_id]);
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[args.idempotency_key]);
        return withEnvelope(c,actor,verb,args,async () => unwrap((await c.query(
          'select ops.set_subject_watch($1,$2::uuid,$3) as result',
          [args.subject_type,args.subject_id,command === 'watch'])).rows[0]?.result));
      },
    }];
  }));
  return {
    ...commands,
    'read-subject-watch-v1': {
      writerConnection: true,
      description: 'Read your subscription to one currently visible canonical deal or lease. Other partners subscriptions are never returned. Unknown, deleted and inaccessible targets refuse watch_target_not_found.',
      inputSchema: { type: 'object', additionalProperties: false, properties: targetProperties, required: ['subject_type','subject_id'] },
      handler: (c,_actor,args) => target(c,args),
    },
    'list-subject-watches-v1': {
      writerConnection: true,
      description: 'List your visible canonical subscriptions in watch UUID order with a maximum page size of 100. Pass next_cursor for the next live page. Inaccessible targets are omitted immediately. No notification preference or delivery state changes.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        cursor: { type: 'string', pattern: UUID.source }, limit: { type: 'integer', minimum: 1, maximum: 100, default: 50 },
      } },
      handler: async (c,_actor,args) => {
        const limit=args.limit ?? 50;
        const result=unwrap((await c.query('select ops.list_subject_watches($1::uuid,$2) as result',[args.cursor ?? null,limit])).rows[0]?.result);
        const has_more=result.watches.length>limit;
        const watches=result.watches.slice(0,limit);
        return { ok: true, schema_version: VERSION, watches, has_more, next_cursor: has_more ? watches.at(-1).id : null };
      },
    },
  };
}

export function subjectWatchV2Tools({ withEnvelope, ToolError }) {
  const unwrap = result => {
    if (!result?.ok) throw new ToolError({ error: result?.error || 'subject_watch_unavailable' });
    return parseSubjectWatchV2(result);
  };
  const tools = {};
  for (const command of ['watch', 'pause', 'resume', 'replace-policy', 'unwatch', 'read', 'list']) {
    const write = !['read', 'list'].includes(command);
    const verb = { watch: 'watch-subject-v2', unwatch: 'unwatch-subject-v2', list: 'list-subject-watches-v2',
      read: 'read-subject-watch-v2', pause: 'pause-subject-watch-v2', resume: 'resume-subject-watch-v2',
      'replace-policy': 'replace-subject-watch-policy-v2' }[command];
    const properties = command === 'list' ? {
      cursor: { type: 'string', pattern: UUID.source },
      limit: { type: 'integer', minimum: 1, maximum: 100, default: 50 },
    } : { ...targetProperties };
    const required = command === 'list' ? [] : ['subject_type', 'subject_id'];
    if (write) {
      properties.idempotency_key = { type: 'string', pattern: UUID.source };
      required.push('idempotency_key');
    }
    if (write && command !== 'watch') {
      properties.subscription_id = { type: 'string', pattern: UUID.source };
      properties.base_version = { type: 'integer', minimum: 1 };
      required.push('subscription_id', 'base_version');
    }
    if (['watch', 'replace-policy'].includes(command)) {
      properties.policy = subjectWatchPolicySchema;
      required.push('policy');
    }
    tools[verb] = {
      humanOnly: true,
      humanCallerOnly: true,
      ...(write ? { write: true } : { writerConnection: true }),
      description: `Watch v2 ${command}. Server-derived partner only. Separate v2 policy lifecycle; v1 unchanged. Listing targets refuse until a canonical listing contract exists. Watch and resume are quiet. Exact subscription and lifecycle version required for changes.`,
      inputSchema: { type: 'object', additionalProperties: false, properties, required },
      handler: async (c, actor, args) => {
        if (args.policy && !validSubjectWatchPolicy(args.policy)) throw new ToolError({ error: 'watch_policy_invalid' });
        const run = async () => unwrap((await c.query(
          'select ops.subject_watch_v2($1,$2::jsonb) as result', [command, JSON.stringify(args)])).rows[0]?.result);
        if (!write) return run();
        await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))', [args.idempotency_key]);
        return withEnvelope(c, actor, verb, args, run);
      },
    };
  }
  return tools;
}
