// Clear the deck (WR577): confirm or expire automation-created partner obligations.
import { personalScopeForActor } from './identity.js';

const KINDS = ['next_action', 'loop', 'candidate'];
const ACTIONS = ['keep', 'drop'];
const MATCH_LIMIT = 10000;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/;
const OTHER = { joe: ['dell', 'dell-local'], dell: ['joe', 'joe-local'] };

// One row source per kind. Every lookup locks the base row and reads its creator kind,
// so partner-created rows can never be kept, dropped or expired through this door.
const SOURCE = {
  next_action: {
    table: 'next_action', live: 'open',
    select: `select t.id, t.version, t.status, o.slug as owner, t.description as title, c.kind as creator_kind
      from next_action t join actor o on o.id = t.owner_id join actor c on c.id = t.created_by`,
    text: 't.description', eligible: 'true',
    keep: (ids, x) => [`update next_action set owner_id = $2, confirmed_at = now(), status = 'open', updated_by = $3
      where id = any($1::uuid[])`, [ids, x.partner, x.actor]],
    drop: (ids, x) => [`update next_action set status = 'expired', updated_by = $2
      where id = any($1::uuid[]) and status = 'open'`, [ids, x.actor]],
  },
  loop: {
    table: 'loop_item', live: 'open',
    select: `select t.id, t.version, t.status, t.owner, coalesce(nullif(t.title, ''), split_part(t.body, E'\\n', 1)) as title,
        c.kind as creator_kind
      from loop_item t join actor c on c.id = t.created_by`,
    text: "coalesce(nullif(t.title, ''), t.body)", eligible: "t.kind in ('open_loop', 'team_loop', 'action_required')",
    keep: (ids, x) => [`update loop_item set owner = $2, confirmed_at = now(), status = 'open', closed_at = null,
        closed_by = null, close_outcome = null, updated_by = $3 where id = any($1::uuid[])`, [ids, x.sponsor, x.actor]],
    drop: (ids, x) => [`update loop_item set status = 'expired', closed_at = now(), closed_by = $2, updated_by = $2,
        close_outcome = 'expired: not confirmed by a partner (Clear the deck)'
      where id = any($1::uuid[]) and status = 'open'`, [ids, x.actor]],
  },
  candidate: {
    table: 'candidate_pool', live: 'pool',
    select: `select t.id, t.version, t.status, null::text as owner, coalesce(t.org_name, t.name) as title, c.kind as creator_kind
      from candidate_pool t join actor c on c.id = t.created_by`,
    text: 'coalesce(t.org_name, t.name)', eligible: "t.source <> 'renewal-radar'",
    keep: (ids, x) => [`update candidate_pool set confirmed_at = now(), status = 'pool', updated_by = $2
      where id = any($1::uuid[])`, [ids, x.actor]],
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
  const othersOwn = (sponsor, owner) => owner != null && OTHER[sponsor].includes(String(owner).toLowerCase());
  const emptyCounts = () => Object.fromEntries(KINDS.map(kind => [kind, { kept: 0, expired: 0, unchanged: 0 }]));

  async function apply(c, actor, sponsor, key, plan) {
    const partner = (await c.query("select id from actor where slug = $1 and kind = 'human' and active", [sponsor])).rows[0];
    if (!partner) fail('actor_not_provisioned', { slug: sponsor });
    for (const kind of KINDS) for (const action of ACTIONS) {
      const ids = plan[kind][action];
      if (!ids.length) continue;
      const { rowCount } = await c.query(...SOURCE[kind][action](ids, { partner: partner.id, actor: actor.id, sponsor }));
      await writeEvent(c, actor, 'clear-the-deck-apply', 'clear_the_deck', key,
        { new: { kind, action, ids }, idempotency_key: key });
      plan.counts[kind][action === 'keep' ? 'kept' : 'expired'] += rowCount;
      plan.counts[kind].unchanged += ids.length - rowCount;
    }
  }

  async function planItems(c, sponsor, items, lock) {
    const seen = new Set();
    for (const item of items) {
      const id = `${item.kind}:${item.id.toLowerCase()}`;
      if (seen.has(id)) fail('deck_item_duplicate', { kind: item.kind, id: item.id });
      seen.add(id);
    }
    const plan = Object.fromEntries(KINDS.map(kind => [kind, { keep: [], drop: [] }]));
    const missing = [], foreign = [], conflicts = [];
    for (const kind of KINDS) {
      const wanted = items.filter(item => item.kind === kind);
      if (!wanted.length) continue;
      const source = SOURCE[kind];
      const rows = new Map((await c.query(`${source.select} where t.id = any($1::uuid[]) and ${source.eligible}
        and t.status in ($2, 'expired') ${lock ? 'for update of t' : ''}`,
        [wanted.map(item => item.id), source.live])).rows.map(row => [row.id, row]));
      for (const item of wanted) {
        const row = rows.get(item.id.toLowerCase());
        if (!row || row.creator_kind === 'human') missing.push({ kind, id: item.id });
        else if (othersOwn(sponsor, row.owner)) foreign.push({ kind, id: item.id });
        else if (row.version !== item.version) conflicts.push({ kind, id: item.id, version: row.version });
        else plan[kind][item.action].push(row.id);
      }
    }
    if (missing.length) fail('deck_item_not_found', { items: missing });
    if (foreign.length) fail('deck_item_not_yours', { items: foreign,
      hint: "Another partner owns these items; only that partner can keep or drop them." });
    if (conflicts.length) fail('version_conflict', { items: conflicts,
      hint: 'Re-read clear-the-deck and decide again on the current versions.' });
    return { ...plan, counts: emptyCounts() };
  }

  async function planMatching(c, sponsor, filter, lock) {
    if (!INSTANT.test(filter.created_from) || !INSTANT.test(filter.created_before)
        || !(Date.parse(filter.created_from) < Date.parse(filter.created_before)))
      fail('deck_filter_range_invalid', { hint: 'created_from and created_before are ISO timestamps with an offset, from < before.' });
    const creator = (await c.query('select id, kind from actor where slug = $1', [filter.created_by])).rows[0];
    if (!creator) fail('deck_filter_actor_unknown', { created_by: filter.created_by });
    if (creator.kind === 'human') fail('deck_partner_rows_never_expire', { created_by: filter.created_by });
    if (filter.owner !== undefined && othersOwn(sponsor, filter.owner)) fail('deck_item_not_yours', { owner: filter.owner });
    const source = SOURCE[filter.kind];
    const rows = (await c.query(`${source.select}
      where t.status = $1 and ${source.eligible} and t.created_by = $2
        and t.created_at >= $3::timestamptz and t.created_at < $4::timestamptz
        and ($5::text is null or lower(${filter.kind === 'next_action' ? 'o.slug' : filter.kind === 'loop' ? 't.owner' : 'null'}) = lower($5))
        and (cardinality($6::text[]) = 0 or exists (select 1 from unnest($6::text[]) p where starts_with(${source.text}, p)))
      order by t.created_at, t.id ${lock ? 'for update of t' : ''}`,
      [source.live, creator.id, filter.created_from, filter.created_before, filter.owner ?? null, filter.text_prefixes ?? []])).rows;
    if (rows.length > MATCH_LIMIT) fail('deck_match_too_large', { matched: rows.length, limit: MATCH_LIMIT,
      hint: 'Narrow the filter by date range or text prefix.' });
    const mine = rows.filter(row => !othersOwn(sponsor, row.owner));
    const plan = Object.fromEntries(KINDS.map(kind => [kind, { keep: [], drop: [] }]));
    plan[filter.kind].drop = mine.map(row => row.id);
    return { ...plan, counts: emptyCounts(), matched: rows.length, skipped_other_partner: rows.length - mine.length,
      sample: mine.slice(0, 10).map(({ id, title, owner }) => ({ id, title, owner })) };
  }

  const preview = plan => ({ ...Object.fromEntries(KINDS.map(kind => [kind,
    { keep: plan[kind].keep.length, drop: plan[kind].drop.length }])),
  ...(plan.matched === undefined ? {} : { matched: plan.matched, skipped_other_partner: plan.skipped_other_partner,
    sample: plan.sample }) });

  return {
    'clear-the-deck': {
      write: false,
      description: "Clear the deck for the authenticated partner: automation-created open next actions, loops (30 days) and pool candidates (60 days) that are already stale or expire within 7 days, unless a partner keeps them. Partner-created rows never appear; rows the other partner owns are left out. Returns groups by kind and bucket (stale, expiring_this_week) with totals and up to limit rows each (default 50), each row carrying the version clear-the-deck-apply needs. Stale rows are hidden from today-triage and Home until kept or dropped.",
      inputSchema: { type: 'object', additionalProperties: false, properties: {
        limit: { type: 'integer', minimum: 1, maximum: 200 } } },
      handler: async (c, actor, args) => {
        const sponsor = sponsorOf(actor);
        const limit = args.limit ?? 50;
        const { rows } = await c.query(`select * from (
            select kind, id, version, owner, title, created_by, created_at, expires_at,
                   case when stale then 'stale' else 'expiring_this_week' end as bucket,
                   count(*) over (partition by kind, stale)::int as bucket_total,
                   row_number() over (partition by kind, stale order by expires_at, id) as rn
              from v_deck_item
             where (owner is null or lower(owner) <> all($1::text[]))
               and (stale or expires_at < now() + interval '7 days')) d
          where rn <= $2 order by kind, bucket, rn`, [OTHER[sponsor], limit]);
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
      description: "Decide Clear the deck rows for the authenticated partner in one call. Pass items [{kind, id, version, action}] where keep confirms the row (assigns the partner as owner where the kind has one, restarts its clock, and restores an expired row) and drop stores the reversible 'expired' status. Or pass expire_all_matching {kind, created_by, created_from, created_before, owner?, text_prefixes?} to expire every open automation-created row in that explicit window; partner creators are refused and rows the other partner owns are skipped. Per-row versions are checked for items; any refusal changes nothing. dry_run:true returns the counts and changes nothing; otherwise idempotency_key is required and replays return the first result.",
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
        const plan = (lock) => args.items ? planItems(c, sponsor, args.items, lock)
          : planMatching(c, sponsor, args.expire_all_matching, lock);
        if (args.dry_run === true) return { ok: true, dry_run: true, sponsor, would: preview(await plan(false)) };
        return withEnvelope(c, actor, 'clear-the-deck-apply', args, async () => {
          const decided = await plan(true);
          await apply(c, actor, sponsor, args.idempotency_key, decided);
          return { ok: true, dry_run: false, sponsor, counts: decided.counts,
            ...(decided.matched === undefined ? {} : { matched: decided.matched, skipped_other_partner: decided.skipped_other_partner }) };
        });
      },
    },
  };
}
