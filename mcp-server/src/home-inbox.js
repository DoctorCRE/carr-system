// Project canonical work without changing its domain state.
import { homeReviewReference } from './home-review.js';
export const HOME_INBOX_CONTRACT = Object.freeze({
  schema_version: 'home-inbox.v2',
  read_verb: 'read-home-inbox-v2',
  item_fields: {
    context_label: 'string|null; deal or notification subject name',
    due_on: 'YYYY-MM-DD|null; canonical action due date',
    open_url: 'string|null; relative destination or source notification deep link',
    kind_label: 'Task|Call follow-up|Call|Reply draft|Update',
    merged_source_ids: 'UUID[]; canonical source IDs collapsed into this card',
    action_id: 'UUID|null; next_action or capture_post_call_action ID for complete-one-action',
    action_version: 'integer|null; canonical action version, sent as complete-one-action base_version',
    deal_id: 'UUID|null; owning deal for completion readback, null for non-deal work',
  },
  sections: ['inbox', 'working', 'held', 'done', 'snoozed', 'settled'],
  done_window_days: 7,
  notification_subject_types: ['deal', 'loop', 'call', 'email', 'doc_conversation_request'],
  limit: 1000,
  truncated: 'boolean; the item limit or 10000-notification scan bound was exceeded',
  ordering: 'Unfinished first, newest activity first, item ID as tie breaker',
  completion: 'Only task items have action_id and action_version. Complete the representative action only; merged_source_ids are display provenance, never a bulk completion instruction. No task reopening verb is added.',
});
export const HOME_INBOX_SETTLEMENT_CONTRACT = Object.freeze({
  ...HOME_INBOX_CONTRACT,
  schema_version: 'home-inbox.v3',
  read_verb: 'read-home-inbox-v3',
  item_fields: { ...HOME_INBOX_CONTRACT.item_fields, kind_label: 'Task|Call follow-up|Call|Reply draft|Update|Fact review|Tour feedback', auto_settled: 'boolean; canonical resolution placed this item in Settled without a disposition write',
    review_reference: 'home-review.v1 reference|null; exact item and source version with candidate domain operations; load read-home-review for current detail and available operations' },
  auto_settle_source_types: ['next_action', 'capture_post_call_action', 'email_proposal', 'fact_review', 'tour_feedback'],
  settlement: 'Canonical done tasks, sent replies, resolved fact reviews and received tour feedback settle automatically. A future snooze and explicit un-settle take precedence. Notification completion and call-processing completion do not prove all underlying work resolved. Tour feedback resolves only for the exact share grant that received a client shortlist or comment. Versions remain the existing disposition-command authority; retry exact requests and hydrate receipts through this read.',
});

export const HOME_INBOX_COMPLETION_CONTRACT = Object.freeze({
  ...HOME_INBOX_SETTLEMENT_CONTRACT,
  schema_version: 'home-inbox.v4',
  read_verb: 'read-home-inbox-v4',
  item_fields: { ...HOME_INBOX_SETTLEMENT_CONTRACT.item_fields,
    completed_at: 'UTC ISO-8601 instant|null; proven canonical completion, never activity or ingestion time' },
  completion_time: 'Done ordinary tasks use the exact complete-one-action, complete-action or complete-deal-item event, superseded by reopen-one-action. Done call actions use completed_at. Sent replies use send_result.sent_at. Open work, unsupported sources and unproven legacy completions return null. Later notifications never move completion. Clients group by America/Chicago.',
});

function withAutomaticSettlement(snapshot, pendingReplyIds) {
  return { ...snapshot, auto_settle_enabled: true,
    items: snapshot.items.map(item => {
      const auto_settled = item.source_state === 'done' && item.disposition === 'inbox' &&
        !item.explicit_unsettle && HOME_INBOX_SETTLEMENT_CONTRACT.auto_settle_source_types.includes(item.source_type);
      const section = item.disposition !== 'inbox' ? item.disposition : item.explicit_unsettle ? 'inbox' : auto_settled ? 'settled' : item.section;
      return { ...item, section, disposition: auto_settled ? 'settled' : item.disposition, auto_settled,
        review_reference: item.source_type === 'email_proposal' && !pendingReplyIds.has(item.source_id) ? null : homeReviewReference(item) };
    }),
  };
}

export const HOME_INBOX_WRITES = new Set(['snooze-home-inbox', 'settle-home-inbox', 'unsettle-home-inbox']);
export function homeInboxTools({ withEnvelope, ToolError }) {
  const unwrap = result => {
    if (result?.ok !== true) throw new ToolError({ error: result?.error || 'inbox_unavailable' });
    return result;
  };
  const readWithSettlement = async (c, version) => {
    const row = (await c.query(`with snapshot as materialized (select ops.read_home_inbox_${version}() as result)
      select result,array(select p.id::text from jsonb_array_elements(result->'items') i
        join email_proposal p on p.id=case when i->>'source_type'='email_proposal' then (i->>'source_id')::uuid end
        where p.status='pending') as pending_reply_ids from snapshot`)).rows[0];
    return withAutomaticSettlement(unwrap(row?.result), new Set(row.pending_reply_ids));
  };
  const tools = {
    'read-home-inbox': {
      writerConnection: true,
      description: 'Read the stable home-inbox.v1 snapshot. Refuses above 1000 items or 10000 notifications.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async c => unwrap((await c.query('select ops.read_home_inbox() as result')).rows[0]?.result),
    },
    'read-home-inbox-v4': {
      writerConnection: true,
      description: 'Read Home inbox with v3 automatic settlement and nullable source-backed completed_at UTC instants for Done today. Unknown completion times remain null. Clients group by America/Chicago. Uses home-inbox.v4; v1 through v3 remain unchanged.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: c => readWithSettlement(c, 'v4'),
    },
    'read-home-inbox-v3': {
      writerConnection: true,
      description: 'Read the Home inbox with automatic settlement of canonical done tasks, sent replies, resolved fact reviews and received tour feedback. Manual snooze and un-settle take precedence. No domain rows or dispositions are written. Uses home-inbox.v3; v1 and v2 remain unchanged.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: c => readWithSettlement(c, 'v3'),
    },
    'read-home-inbox-v2': {
      writerConnection: true,
      description: 'Read one actor-scoped Home inbox snapshot of tasks, accepted call actions, call processing, mailbox-owned reply drafts and grouped notifications. Uses home-inbox.v2 with context, due dates and links. Duplicate open tasks collapse. Done work stays on a seven-day Done shelf; dropped work is hidden. Future holds use Held. Engineering and release notifications are excluded. Returns the newest 1000 items with a truncation flag. Working is collapsed by default. Auto-settle is not enabled.',
      inputSchema: { type: 'object', additionalProperties: false, properties: {} },
      handler: async c => unwrap((await c.query('select ops.read_home_inbox_v2() as result')).rows[0]?.result),
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
