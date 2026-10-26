import { ToolError } from "./tool-error.js";

const compare = (a, b) => {
  for (let i = 0; i < a.length; i++) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
};

export async function briefQueuePage(items, { limit = 50, cursor, scope, key }) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50)
    throw new ToolError({ error: 'brief_queue_page_invalid' });
  let after;
  if (cursor !== undefined) {
    try {
      if (typeof cursor !== 'string' || !cursor.length || cursor.length > 8000) throw Error();
      after = JSON.parse(decodeURIComponent(atob(cursor)));
      if (after.v !== 1 || after.scope !== scope || !Array.isArray(after.key)
          || after.key.length !== 4 || !after.key.every(value => typeof value === 'string' && value.length <= 256)) throw Error();
    } catch { throw new ToolError({ error: 'brief_queue_cursor_invalid' }); }
  }
  const sorted = (await Promise.all(items.map(async item => ({ item, key: await key(item) }))))
    .sort((a, b) => compare(a.key, b.key));
  const available = after ? sorted.filter(row => compare(row.key, after.key) > 0) : sorted;
  const page = available.slice(0, limit);
  const remaining = available.length - page.length;
  return {
    items: page.map(row => row.item), total: sorted.length, returned: page.length, remaining,
    has_more: remaining > 0,
    next_cursor: remaining ? btoa(encodeURIComponent(JSON.stringify({ v: 1, scope, key: page.at(-1).key }))) : null,
    consistency: 'live_keyset; changed rows may move; restart for a fresh full traversal',
  };
}

export async function readBriefLoops(c) {
  return (await c.query(`select number, kind, domain, status, owner, marker, title,
      coalesce(nullif(title, ''),
        nullif(regexp_replace(split_part(body, E'\\n', 1), '\\*\\*', '', 'g'), '')) as label,
      blocker_class, blocker_detail, since_text, due_on::text as due_on, version
    from loop_item
    where kind='open_loop' and status='open' and lower(owner) in ('joe','dell')`)).rows
    .map(row => ({ ...row, owner_partner: row.owner.toLowerCase() }));
}

const fingerprint = async value => {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
};
export const briefLoopKey = sponsor => async row => [row.due_on || '~',
  row.owner_partner === sponsor ? '0' : '1', String(row.kind), await fingerprint(String(row.number))];
const tier = { work: '0', capability: '1', pending: '2' };
const timestamp = value => value != null && Number.isFinite(new Date(value).getTime()) ? new Date(value).toISOString() : '~';
export const briefNeedsJoeKey = async row => {
  const identity = await fingerprint(JSON.stringify([row.key, row.title]));
  return [tier[row.blocks.tier], timestamp(row.since), row.source, identity];
};
export const briefAssuranceKey = row => [({ failure: '0', action_required: '1', completion: '2' })[row.severity] || '3',
  timestamp(row.created_at), '', row.notification_id];
