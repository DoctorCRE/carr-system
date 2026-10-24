import { filterBriefOwners } from './brief-owner-filter.js';
import { normalizeChangeEvent, compareReceipts } from './change-receipts.mjs';
import { toDay } from './calendar-model.js';

export function localDay(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
const text = value => typeof value === 'string' && value.trim();
const section = value => value && ['ready','empty','unavailable'].includes(value.state) && Array.isArray(value.items);
export function validBrief(value) {
  return ['joe','dell'].includes(value?.sponsor) && ['today','deals','loops'].every(key => section(value.sections?.[key]));
}
const short = (value, count) => {
  const words = String(value || '').trim().split(/\s+/);
  return words.length > count ? `${words.slice(0,count).join(' ')}…` : words.join(' ');
};

export function morningBriefView(payload, { now = new Date(), since, events = [], caughtUp = false, owner = 'all' } = {}) {
  if (!validBrief(payload)) return null;
  const { sponsor, sections } = payload, day = localDay(now);
  const deals = new Map(sections.deals.state === 'unavailable' ? [] : sections.deals.items.map(row => [row.id,row]));
  const due = [];
  if (sections.today.state !== 'unavailable') for (const row of sections.today.items) {
    const deal = deals.get(row.subject_id);
    const dueOn = toDay(row.due_on);
    if (!text(row.what) || !dueOn || dueOn > day) continue;
    if (row.subject_type === 'deal' && deal) {
      if (deal.operating_state === 'parked' || /^closed$/i.test(deal.phase || '')) continue;
      due.push({ key:row.id ? `action:${row.id}` : `deal:${row.subject_id}`, kind:'deal', id:row.subject_id, action:row.id ? row : null, title:deal.name, summary:row.what, due:dueOn, owner_partner:row.owner_partner });
    } else if (row.item_kind === 'next_action' && text(row.id)) {
      due.push({ key:`action:${row.id}`, kind:'action', id:row.id, action:row, title:row.subject_name || row.what, summary:row.what, due:dueOn, owner_partner:row.owner_partner });
    }
  }
  if (sections.loops.state !== 'unavailable') for (const row of sections.loops.items) {
    const title = text(row.title) || text(row.label);
    const dueOn = toDay(row.due_on);
    if (row.status !== 'open' || row.kind !== 'open_loop' || !title || !text(String(row.number || '')) || !dueOn || dueOn > day) continue;
    due.push({ key:`loop:${row.kind}:${row.number}`, kind:'loop', id:String(row.number), loopKind:row.kind, title, summary:row.blocker_detail || '', due:dueOn, owner_partner:row.owner_partner });
  }
  due.sort((a,b) => a.due.localeCompare(b.due) || a.key.localeCompare(b.key));
  const seen = new Set();
  const ranked = due.filter(row => { if (seen.has(row.key)) return false; seen.add(row.key); return true; });
  const unique = filterBriefOwners(ranked, owner);
  const first = unique[0] || null, teamFirst = ranked[0] || null;
  const receipts = caughtUp ? events.map(event => normalizeChangeEvent(event, { dealName:id => deals.get(id)?.name })).filter(row => row && Date.parse(row.recorded_at) >= Date.parse(since) && Date.parse(row.recorded_at) <= now.valueOf()).sort(compareReceipts) : [];
  const change = receipts.find(row => ['phase','next_step','next_date','note','attention','operating_state'].includes(row.field) && !(teamFirst?.kind === 'deal' && row.deal_id === teamFirst.id));
  const overnight = change ? { key:`deal:${change.deal_id}`, kind:'deal', id:change.deal_id, title:change.deal_name, summary:change.kind === 'note' ? 'New note' : `${change.action}${change.after ? `: ${change.after}` : ''}`, due:null } : null;
  const remaining = unique.find(row => row.key !== first?.key && !(row.kind === 'deal' && row.id === overnight?.id)) || null;
  const groups = [ ['first','Do first',first], ['overnight','Overnight',overnight], ['today','Today',remaining] ]
    .filter(([, ,row]) => row).map(([key,label,row]) => ({key,label,row:{ ...row, title:short(row.title,6), summary:short(row.summary,10) }}));
  const unavailable = [];
  if (sections.today.state === 'unavailable' || sections.loops.state === 'unavailable' || sections.deals.state === 'unavailable') unavailable.push('Today’s priorities unavailable');
  if (!caughtUp || sections.deals.state === 'unavailable') unavailable.push('Overnight updates unavailable');
  const spoken = [...unavailable,...groups.map(group => `${group.label}. ${group.row.title}. ${group.row.summary}`)].join('. ');
  return { sponsor, day, groups, unavailable, spoken, observedAt:now.toISOString() };
}
