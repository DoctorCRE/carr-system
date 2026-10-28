// Clear the deck (WR577): confirm or expire automation-made partner obligations.
import { personalScopeForActor } from './identity.js';

const KINDS = ['next_action', 'loop', 'candidate'];
const ACTIONS = ['keep', 'drop'];
const MATCH_LIMIT = 10000;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/;
// A deck row was never partner-confirmed. Migration 1030 stamps confirmed_at on every partner-made
// row (carr_is_partner_made) and on any joe/dell edit, so this one column is the rule.
const UNCONFIRMED = 't.confirmed_at is null';

// One row source per kind. Partners are equal operators: either may decide any
// automation-made row, whoever owns it; the event keeps each row's prior owner and status.
const SOURCE = {
  next_action: {
    live: 'open', owner: 'o.slug', text: 't.description', eligible: 'true',
    select: `select t.id, t.version, t.status, o.slug as owner, t.description as title
      from next_action t join actor o on o.id = t.owner_id`,
    keep: (ids, x) => [`update next_action set owner_id = $2, confirmed_by = $2, confirmed_at = now(), status = 'open',
        updated_by = $3 where id = any($1::uuid[])`, [ids, x.partner, x.actor]],
    drop: (ids, x) => [`update next_action set status = 'expired', updated_by = $2
      where id = any($1::uuid[]) and status = 'open'`, [ids, x.actor]],
  },
  loop: {
    live: 'open', owner: 't.owner', text: "coalesce(nullif(t.title, ''), t.body)",
    eligible: "t.kind in ('open_loop', 'team_loop', 'action_required')",
    select: `select t.id, t.version, t.status, t.owner, coalesce(nullif(t.title, ''), split_part(t.body, E'\\n', 1)) as title
      from loop_item t`,
    keep: (ids, x) => [`update loop_item set owner = $2, confirmed_by = $3, confirmed_at = now(), status = 'open',
        closed_at = null, closed_by = null, close_outcome = null, updated_by = $4 where id = any($1::uuid[])`,
    [ids, x.sponsor, x.partner, x.actor]],
    drop: (ids, x) => [`update loop_item set status = 'expired', closed_at = now(), closed_by = $2, updated_by = $2,
        close_outcome = 'expired: not confirmed by a partner (Clear the deck)'
      where id = any($1::uuid[]) and status = 'open'`, [ids, x.actor]],
  },
  candidate: {
    live: 'pool', owner: null, text: 'coalesce(t.org_name, t.name)', eligible: "t.source <> 'renewal-radar'",
    select: `select t.id, t.version, t.status, null::text as owner, coalesce(t.org_name, t.name) as title
      from candidate_pool t`,
    keep: (ids, x) => [`update candidate_pool set confirmed_by = $2, confirmed_at = now(), status = 'pool', updated_by = $3
      where id = any($1::uuid[])`, [ids, x.partner, x.actor]],
    drop: (ids, x) => [`update candidate_pool set status = 'expired', updated_by = $2
      where id = any($1::uuid[]) and status = 'pool'`, [ids, x.actor]],
  },
};

export function clearTheDeckTools({ ToolError, withEnvelope, writeEvent }) {
  const fail = (error, extra = {}) => { throw new ToolError({ error, ...extra }); };
  const sponsorOf = actor => {
    const scope = personalScopeForActor(actor);
    if (scope.status !== 'personal') fail('deck_requires_partner_scope');
    return scope.sponsor;
  };
  const emptyPlan = () => Object.fromEntries(KINDS.map(kind => [kind, { keep: [], drop: [] }]));
  const emptyCounts = () => Object.fromEntries(KINDS.map(kind => [kind, { kept: 0, expired: 0, unchanged: 0 }]));

  async function apply(c, actor, sponsor, key, plan) {
    const partner = (await c.query("select id from actor where slug = $1 and kind = 'human' and active", [sponsor])).rows[0];
    if (!partner) fail('actor_not_provisioned', { slug: sponsor });
    const counts = emptyCounts();
    for (const kind of KINDS) for (const action of ACTIONS) {
      const rows = plan[kind][action];
      if (!rows.length) continue;
      const ids = rows.map(row => row.id);
      const { rowCount } = await c.query(...SOURCE[kind][action](ids, { partner: partner.id, actor: actor.id, sponsor }));
      await writeEvent(c, actor, 'clear-the-deck-apply', 'clear_the_deck', key, { idempotency_key: key,
        new: { kind, action, rows: rows.map(({ id, owner, status }) => ({ id, owner, status })) } });
      counts[kind][action === 'keep' ? 'kept' : 'expired'] += rowCount;
      counts[kind].unchanged += ids.length - rowCount;
    }
    return counts;
  }

  async function planItems(c, items, lock) {
    const seen = new Set();
    for (const item of items) {
      const id = `${item.kind}:${item.id.toLowerCase()}`;
      if (seen.has(id)) fail('deck_item_duplicate', { kind: item.kind, id: item.id });
      seen.add(id);
    }
    const plan = emptyPlan();
    const missing = [], conflicts = [];
    for (const kind of KINDS) {
      const wanted = items.filter(item => item.kind === kind);
      if (!wanted.length) continue;
      const source = SOURCE[kind];
      const rows = new Map((await c.query(`${source.select} where t.id = any($1::uuid[]) and ${source.eligible}
        and ${UNCONFIRMED} and t.status in ($2, 'expired') ${lock ? 'for update of t' : ''}`,
      [wanted.map(item => item.id), source.live])).rows.map(row => [row.id, row]));
      for (const item of wanted) {
        const row = rows.get(item.id.toLowerCase());
        if (!row) missing.push({ kind, id: item.id });
        else if (row.version !== item.version) conflicts.push({ kind, id: item.id, version: row.version });
        else plan[kind][item.action].push(row);
      }
    }
    if (missing.length) fail('deck_item_not_found', { items: missing,
      hint: 'Only open or expired automation-made rows that no partner confirmed can be decided here.' });
    if (conflicts.length) fail('version_conflict', { items: conflicts,
      hint: 'Re-read clear-the-deck and decide again on the current versions.' });
    return plan;
  }

  async function planMatching(c, filter, lock) {
    if (!INSTANT.test(filter.created_from) || !INSTANT.test(filter.created_before)
        || !(Date.parse(filter.created_from) < Date.parse(filter.created_before)))
      fail('deck_filter_range_invalid', { hint: 'created_from and created_before are ISO timestamps with an offset, from < before.' });
    const source = SOURCE[filter.kind];
    if (filter.owner !== undefined && !source.owner) fail('deck_filter_owner_not_applicable', { kind: filter.kind });
    const creator = (await c.query('select id, carr_is_partner_actor(id) as partner from actor where slug = $1',
      [filter.created_by])).rows[0];
    if (!creator) fail('deck_filter_actor_unknown', { created_by: filter.created_by });
    if (creator.partner) fail('deck_partner_rows_never_expire', { created_by: filter.created_by });
    const rows = (await c.query(`${source.select}
      where t.status = $1 and ${source.eligible} and ${UNCONFIRMED} and t.created_by = $2
        and t.created_at >= $3::timestamptz and t.created_at < $4::timestamptz
        ${filter.owner === undefined ? '' : `and lower(${source.owner}) = lower($7)`}
        and (cardinality($5::text[]) = 0 or exists (select 1 from unnest($5::text[]) p where starts_with(${source.text}, p)))
      order by t.created_at, t.id limit $6 ${lock ? 'for update of t' : ''}`,
    [source.live, creator.id, filter.created_from, filter.created_before, filter.text_prefixes ?? [], MATCH_LIMIT + 1,
      ...(filter.owner === undefined ? [] : [filter.owner])])).rows;
    if (rows.length > MATCH_LIMIT) fail('deck_match_too_large', { limit: MATCH_LIMIT,
      hint: 'Narrow the filter by date range or text prefix.' });
    const plan = emptyPlan();
    plan[filter.kind].drop = rows;
    return plan;
  }

  const preview = (plan, matching) => ({
    ...Object.fromEntries(KINDS.map(kind => [kind, { keep: plan[kind].keep.length, drop: plan[kind].drop.length }])),
    ...(matching ? { matched: plan[matching].drop.length,
      sample: plan[matching].drop.slice(0, 10).map(({ id, title, owner }) => ({ id, title, owner })) } : {}),
  });

  return {
    'clear-the-deck': {
      write: false,
      description: "Clear the deck for either partner: open automation-made next actions and loops (30 days) and pool candidates (60 days) that no partner ever created, edited or kept, and that are stale or expire within 7 days. Partner-confirmed rows never appear. Stale only fills this list; it hides nothing from triage or Home. Returns groups by kind and bucket (stale, expiring_this_week) with totals and up to limit rows each (default 50), each row with its owner and the version clear-the-deck-apply needs.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        limit: { type: 'integer', minimum: 1, maximum: 200 } } },
      handler: async (c, actor, args) => {
        const sponsor = sponsorOf(actor);
        const { rows } = await c.query(`select * from (
            select kind, id, version, owner, title, created_by, created_at, expires_at,
                   case when stale then 'stale' else 'expiring_this_week' end as bucket,
                   count(*) over (partition by kind, stale)::int as bucket_total,
                   row_number() over (partition by kind, stale order by expires_at, id) as rn
              from v_deck_item where stale or expires_at < now() + interval '7 days') d
          where rn <= $1 order by kind, bucket, rn`, [args.limit ?? 50]);
        const groups = [];
        for (const kind of KINDS) for (const bucket of ['stale', 'expiring_this_week']) {
          const items = rows.filter(row => row.kind === kind && row.bucket === bucket);
          groups.push({ kind, bucket, total: items[0]?.bucket_total ?? 0,
            items: items.map(({ kind: _k, bucket: _b, bucket_total: _t, rn: _r, ...row }) => ({ ...row, actions: ACTIONS })) });
        }
        return { schema_version: 'clear-the-deck.v1', sponsor, groups };
      },
    },
    'clear-the-deck-apply': {
      write: true, humanOnly: true,
      description: "Decide Clear the deck rows in one call; either partner may decide any automation-made row. Pass items [{kind, id, version, action}]: keep confirms the row for good (it never returns to the deck), assigns the calling partner as owner where the kind has one, and restores an expired row; drop stores the reversible 'expired' status, which takes the row off triage and Home. Or pass expire_all_matching {kind, created_by, created_from, created_before, owner?, text_prefixes?} to expire every unconfirmed automation-made row in that explicit window; partner creators are refused. Per-row versions are checked for items; any refusal changes nothing. Events record each row's prior owner and status. dry_run:true returns counts and a sample and changes nothing; otherwise idempotency_key is required and replays return the first result.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        idempotency_key: { type: 'string', minLength: 1, maxLength: 200 },
        dry_run: { type: 'boolean' },
        items: { type: 'array', minItems: 1, maxItems: 500, items: { type: 'object', additionalProperties: false,
          required: ['kind', 'id', 'version', 'action'], properties: {
            kind: { type: 'string', enum: KINDS }, id: { type: 'string', format: 'uuid' },
            version: { type: 'integer', minimum: 1 }, action: { type: 'string', enum: ACTIONS } } } },
        expire_all_matching: { type: 'object', additionalProperties: false,
          required: ['kind', 'created_by', 'created_from', 'created_before'], properties: {
            kind: { type: 'string', enum: KINDS },
            created_by: { type: 'string', minLength: 1, maxLength: 80 },
            created_from: { type: 'string', minLength: 1, maxLength: 40 },
            created_before: { type: 'string', minLength: 1, maxLength: 40 },
            owner: { type: 'string', minLength: 1, maxLength: 80 },
            text_prefixes: { type: 'array', maxItems: 20, items: { type: 'string', minLength: 3, maxLength: 200 } } } },
      } },
      handler: async (c, actor, args) => {
        const sponsor = sponsorOf(actor);
        if ((args.items === undefined) === (args.expire_all_matching === undefined))
          fail('deck_choose_items_or_filter', { hint: 'Pass exactly one of items or expire_all_matching.' });
        const matching = args.expire_all_matching?.kind;
        const plan = lock => args.items ? planItems(c, args.items, lock) : planMatching(c, args.expire_all_matching, lock);
        if (args.dry_run === true) return { ok: true, dry_run: true, sponsor, would: preview(await plan(false), matching) };
        return withEnvelope(c, actor, 'clear-the-deck-apply', args, async () => {
          const decided = await plan(true);
          const counts = await apply(c, actor, sponsor, args.idempotency_key, decided);
          return { ok: true, dry_run: false, sponsor, counts, ...(matching ? { matched: decided[matching].drop.length } : {}) };
        });
      },
    },
  };
}
