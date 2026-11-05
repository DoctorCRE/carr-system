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
