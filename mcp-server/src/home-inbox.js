// Project canonical work without changing its domain state.
export const HOME_INBOX_WRITES = new Set(['snooze-home-inbox', 'settle-home-inbox', 'unsettle-home-inbox']);
export function homeInboxTools({ withEnvelope, ToolError }) {
  const unwrap = result => {
    if (result?.ok !== true) throw new ToolError({ error: result?.error || 'inbox_unavailable' });
    return result;
  };
  const tools = {
    'read-home-inbox': {
      writerConnection: true,
      description: 'Read one actor-scoped Home inbox snapshot of tasks, accepted call actions, call processing, mailbox-owned reply drafts and grouped notifications. Done is source state, Settled is actor disposition. Refuses above 1000 items or 10000 notifications. Working is collapsed by default. Auto-settle is not enabled.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async c => unwrap((await c.query('select ops.read_home_inbox() as result')).rows[0]?.result),
    },
  };
  for (const [command, name] of [['snooze','snooze-home-inbox'],['settle','settle-home-inbox'],['unsettle','unsettle-home-inbox']]) {
    tools[name] = {
      write: true,
      writerConnection: true,
      description: `${command} up to 100 inbox items for the acting actor. Each item requires its base_version from read-home-inbox. Returns per-item success or version_conflict. Idempotent retries reuse the same key and arguments. Never closes a task or acknowledges a notification.`,
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string', format: 'uuid' },
        items: { type: 'array', minItems: 1, maxItems: 100, items: {
          type: 'object', additionalProperties: false, properties: {
            item_id: { type: 'string', minLength: 1, maxLength: 1000 },
            base_version: { type: 'string', pattern: '^[a-f0-9]{32}$' },
          }, required: ['item_id','base_version'],
        } },
        ...(command === 'snooze' ? { until: { type: 'string', format: 'date-time' } } : {}),
      }, required: ['idempotency_key','items',...(command === 'snooze' ? ['until'] : [])] },
      handler: async (c, actor, args) => withEnvelope(c, actor, name, args, async () => {
        const response = await c.query('select ops.change_home_inbox($1::uuid,$2::text,$3::jsonb,$4::timestamptz) as result',
          [args.idempotency_key,command,JSON.stringify(args.items),args.until ?? null]);
        return unwrap(response.rows[0]?.result);
      }),
    };
  }
  return tools;
}
