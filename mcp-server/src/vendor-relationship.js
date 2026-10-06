import { exactDealAssociations } from "./relationship-network.js";
// Trust uses verified deal history; missing coverage never becomes zero.
export function computedTrust(stats, now) {
  if (!stats?.coverage_verified_at || !Number.isInteger(stats.deals_worked) || !Number.isInteger(stats.won) || !Number.isInteger(stats.lost)) return 'Unrated';
  const years = stats.first_worked_at ? (Date.parse(now) - Date.parse(stats.first_worked_at)) / (365.25 * 86400000) : 0;
  const recency = stats.last_contacted_at ? (Date.parse(now) - Date.parse(stats.last_contacted_at)) / 86400000 : Infinity;
  const resolved = stats.won + stats.lost;
  const rate = resolved ? stats.won / resolved : null;
  if (years >= 2 && stats.deals_worked >= 8 && rate >= .7 && recency >= 0 && recency <= 180) return 'Proven';
  if (years >= 1 && stats.deals_worked >= 3 && rate >= .5 && recency >= 0 && recency <= 365) return 'Established';
  return 'Trial';
}
export function enrichRelationship(payload, now) {
  const stats = payload || {};
  const verified = Boolean(stats.coverage_verified_at);
  return { ...stats, deals_referred: verified ? stats.deals_referred : null, deals_worked: verified ? stats.deals_worked : null,
    win_rate: verified && stats.won + stats.lost > 0 ? stats.won / (stats.won + stats.lost) : null,
    computed_tier: computedTrust(stats, now), formula_version: 'vendor-trust.v1' };
}
export function requireRelationshipPartner(actor, code = 'AUTHORIZATION_REFUSED') {
  if (actor?.human !== true || !['joe', 'dell'].includes(actor?.slug)) throw Object.assign(new Error(code), { code });
}
export function trustedOverride(value, actor, now = new Date().toISOString()) {
  requireRelationshipPartner(actor);
  if (value === null) return null;
  if (!value || !['Proven', 'Established', 'Trial'].includes(value.tier) || typeof value.reason !== 'string' || !value.reason.trim() || value.reason.length > 500 || Object.keys(value).some(k => !['tier', 'reason'].includes(k))) throw Object.assign(new Error('trust_override_invalid'), { code: 'trust_override_invalid' });
  return { tier: value.tier, reason: value.reason.trim(), recorded_by: actor.slug, recorded_at: now };
}
export function dealEvidenceEntries(value) {
  if (!Array.isArray(value) || value.length > 100) throw Object.assign(new Error('deal_evidence_invalid'), { code: 'deal_evidence_invalid' });
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const seen = new Set();
  return value.map(entry => {
    const occurred_at = canonicalEvidenceTimestamp(entry?.occurred_at);
    if (!entry || typeof entry.deal_id !== 'string' || !uuid.test(entry.deal_id) || !['referred','worked'].includes(entry.role) || !['mail','calendar','salesforce','entry'].includes(entry.evidence_kind) || !occurred_at || typeof entry.evidence_ref !== 'string' || !entry.evidence_ref.trim() || entry.evidence_ref.length > 500 || Object.keys(entry).some(k => !['deal_id','role','evidence_kind','evidence_ref','occurred_at'].includes(k))) throw Object.assign(new Error('deal_evidence_invalid'), { code: 'deal_evidence_invalid' });
    const deal_id = entry.deal_id.toLowerCase();
    const key = `${deal_id}:${entry.role}`;
    if (seen.has(key)) throw Object.assign(new Error('deal_evidence_duplicate'), { code: 'deal_evidence_duplicate' });
    seen.add(key); return { deal_id, role: entry.role, occurred_at, evidence_kind: entry.evidence_kind, evidence_ref: entry.evidence_ref.trim() };
  });
}

// Supported input is an ISO date (UTC midnight) or an ISO timestamp with an
// explicit timezone. Validate the calendar before Date.parse can normalize it.
function canonicalEvidenceTimestamp(value) {
  if (typeof value !== 'string') return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(Z|[+-]\d{2}:\d{2}))?$/.exec(value);
  if (!parts) return null;
  const [, y, m, d, h = '00', minute = '00', second = '00', zone = 'Z'] = parts;
  const year = Number(y), month = Number(m), day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1] || Number(h) > 23 || Number(minute) > 59 || Number(second) > 59 || (zone !== 'Z' && (Number(zone.slice(1,3)) > 23 || Number(zone.slice(4)) > 59))) return null;
  const parsed = Date.parse(value.length === 10 ? `${value}T00:00:00Z` : value);
  if (!Number.isFinite(parsed)) return null;
  const canonical = new Date(parsed).toISOString();
  return /^[0-9]{4}-/.test(canonical) && !canonical.startsWith('0000-') ? canonical : null;
}

// Preserve all programs and exact associations. Conflicting observations keep
// the survivor's value and are reported with the loser's value in merge history.
// Coverage transfers only with an unchanged complete evidence set.
export function mergeRelationshipFields(survivor, loser) {
  const filled = {}, conflicts = [];
  const a = dealEvidenceEntries(survivor.deal_evidence || []);
  const b = dealEvidenceEntries(loser.deal_evidence || []);
  const entries = new Map(a.map(e => [`${e.deal_id}:${e.role}`, e]));
  for (const entry of b) {
    const key = `${entry.deal_id}:${entry.role}`, current = entries.get(key);
    if (!current) entries.set(key, entry);
    else if (JSON.stringify(current) !== JSON.stringify(entry)) conflicts.push({field:'deal_evidence',survivor:current,merged:entry});
  }
  filled.deal_evidence = dealEvidenceEntries([...entries.values()]);
  const programs = [...new Set([...(survivor.loan_programs || []), ...(loser.loan_programs || [])])];
  if (programs.length) filled.loan_programs = programs;
  if (!survivor.trust_override && loser.trust_override) filled.trust_override = loser.trust_override;
  else if (survivor.trust_override && loser.trust_override && JSON.stringify(survivor.trust_override) !== JSON.stringify(loser.trust_override))
    conflicts.push({field:'trust_override',survivor:survivor.trust_override,merged:loser.trust_override});
  const fingerprint = entries => JSON.stringify(entries.map(e => JSON.stringify(e)).sort());
  if (!a.length && !survivor.deal_history_verified_at) filled.deal_history_verified_at = loser.deal_history_verified_at || null;
  else if (!b.length && !loser.deal_history_verified_at) filled.deal_history_verified_at = survivor.deal_history_verified_at || null;
  else if (fingerprint(a) === fingerprint(b)) filled.deal_history_verified_at = survivor.deal_history_verified_at || loser.deal_history_verified_at || null;
  else filled.deal_history_verified_at = null;
  return {filled, conflicts};
}
// Exact IDs only. Neither a relationship edge nor a matching name proves a
// vendor worked a deal. Historical Salesforce records remain canonical deals.
// Counts come from the party's exact associations, the same set the network
// snapshot reads. Coverage holds only while every live vendor row for the party
// is verified. Association writes invalidate those attestations atomically.
export const vendorRelationshipJoin = `left join lateral (
  select max(e.occurred_at) as last_deal_at,
    json_build_object(
      'coverage_verified_at', max(c.oldest),
      'deals_referred', count(distinct e.deal_id) filter (where e.role='referred'),
      'deals_worked', count(distinct e.deal_id) filter (where e.role='worked'),
      'won', count(distinct e.deal_id) filter (where e.role='worked' and e.outcome='won'),
      'lost', count(distinct e.deal_id) filter (where e.role='worked' and e.outcome='lost'),
      'first_worked_at', min(e.occurred_at) filter (where e.role='worked'),
      'last_contacted_at', (select max(a.occurred_at) from public.activity a where a.vendor_id=v.id and a.kind in ('call','email_out','email_in','meeting','text')),
      'last_contact_note', (select a.summary from public.activity a where a.vendor_id=v.id and a.kind in ('call','email_out','email_in','meeting','text') order by a.occurred_at desc,a.id desc limit 1),
      'override', v.trust_override,
      'recent_entries', (select coalesce(json_agg(t.entry order by t.occurred_at desc), '[]'::json) from (select a.occurred_at, json_build_object('id',a.id,'kind',a.kind,'when',a.occurred_at,'summary',a.summary,'detail',a.detail) as entry from public.activity a where a.vendor_id=v.id order by a.occurred_at desc,a.id desc limit 20) t),
      'introductions', (select coalesce(json_agg(json_build_object('id',l.id,'kind',l.kind,'from_name',fp.name,'to_name',tp.name,'note',l.note,'occurred_at',l.occurred_on,'via_party',l.via_party) order by l.created_at desc), '[]'::json) from public.party_link l join public.party fp on fp.id=l.from_party join public.party tp on tp.id=l.to_party left join public.party bp on bp.id=l.via_party where (l.from_party=p.id or l.to_party=p.id or l.via_party=p.id) and l.kind in ('intro','intro_received','introduced','can_introduce','intro_requested') and fp.merged_into is null and tp.merged_into is null and fp.deleted_at is null and tp.deleted_at is null and (l.via_party is null or (bp.merged_into is null and bp.deleted_at is null)))
    ) as payload
  from (select case when bool_and(s.deal_history_verified_at is not null) then min(s.deal_history_verified_at) end oldest
    from public.vendor s where s.party_id=v.party_id and s.merged_into is null) c
  left join (${exactDealAssociations}) e on e.party_id=v.party_id
) vr on true`;
