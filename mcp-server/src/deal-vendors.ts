import { ToolError } from './tool-error.ts';
import type { Actor, Db, WriteEvent } from './verb-contract.ts';

type DealAssignment = { deal_id: string; role: string; company_party_id: string; contact_party_id: string;
  active?: boolean; note?: string };
type VendorAssignmentState = { version: number; assignments: Set<string> };

const DEAL_VENDOR_ROLES = ['lender','contractor','architect','attorney','title','inspector','landlord_rep'];

export const DEAL_ASSIGNMENTS_SCHEMA = { type: 'array', minItems: 1, maxItems: 30,
  description: 'Explicit sourced vendor roles on deals. Upserts only these roles; active:false retires a role without deleting history. Company and contact are exact party UUIDs.',
  items: { type: 'object', additionalProperties: false, properties: {
    deal_id: { type: 'string', format: 'uuid' }, role: { type: 'string', enum: DEAL_VENDOR_ROLES },
    company_party_id: { type: 'string', format: 'uuid' }, contact_party_id: { type: 'string', format: 'uuid' },
    active: { type: 'boolean' }, note: { type: 'string', maxLength: 2000 },
  }, required: ['deal_id','role','company_party_id','contact_party_id'] } };

export async function lockDealAssignments(c: Db,
  assignments: { vendor_id?: unknown; company_party_id?: unknown; contact_party_id?: unknown }[]) {
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const vendors: unknown[] = [], parties: unknown[] = [];
  for (const entry of assignments) {
    if (![entry?.vendor_id,entry?.company_party_id,entry?.contact_party_id].every(id => typeof id === 'string' && uuid.test(id)))
      throw new ToolError({error:'deal_assignment_invalid'});
    vendors.push(entry.vendor_id);
    parties.push(entry.company_party_id,entry.contact_party_id);
  }
  // Company/contact checks also lock parties. Take their strongest locks before any vendor row.
  await c.query(`select id from public.party where id in (
    select party_id from public.vendor where id=any($1::uuid[]) union select unnest($2::uuid[])
  ) order by id for update`, [vendors,parties]);
  await c.query('select id from public.vendor where id=any($1::uuid[]) order by id for update',[vendors]);
}

export async function writeDealAssignments(c: Db, actor: Actor, vendorId: string, entries: DealAssignment[],
  recordEvent: WriteEvent, key: string, verb: string, baseVersion: unknown,
  {states = new Map(), human_quote}: { states?: Map<string, VendorAssignmentState>; human_quote?: string } = {}) {
  const refuse = (): never => { throw new ToolError({error:'deal_assignment_invalid'}); };
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!Array.isArray(entries) || !entries.length || entries.length > 30) refuse();
  const seen = new Set<string>();
  for (const entry of entries) {
    if (!entry || Object.keys(entry).some(k => !Object.hasOwn(DEAL_ASSIGNMENTS_SCHEMA.items.properties,k)) ||
        !(['deal_id','company_party_id','contact_party_id'] as const).every(k => typeof entry[k] === 'string' && uuid.test(entry[k])) ||
        !DEAL_VENDOR_ROLES.includes(entry.role) ||
        entry.active !== undefined && typeof entry.active !== 'boolean' ||
        entry.note !== undefined && (typeof entry.note !== 'string' || entry.note.length > 2000)) refuse();
    const identity = `${entry.deal_id.toLowerCase()}:${entry.role}`;
    if (seen.has(identity)) refuse();
    seen.add(identity);
  }
  const vendor = (await c.query(`select v.party_id,p.vendor_assignment_version from public.vendor v join public.party p on p.id=v.party_id
    where v.id=$1 and v.merged_into is null and p.merged_into is null and p.deleted_at is null`, [vendorId])).rows[0];
  if (!vendor) refuse();
  let state = states.get(vendor.party_id);
  const expected = state?.version ?? vendor.vendor_assignment_version;
  if (!Number.isInteger(baseVersion)) throw new ToolError({error:'invalid_assignment_base_version'});
  if (baseVersion !== expected) throw new ToolError({error:'version_conflict',current_assignment_version:expected});
  if (!state) {
    state = {version:expected,assignments:new Set()};
    states.set(vendor.party_id,state);
    await c.query('update public.party set vendor_assignment_version=vendor_assignment_version+1 where id=$1',[vendor.party_id]);
  }
  for (const identity of seen) {
    if (state.assignments.has(identity)) refuse();
    state.assignments.add(identity);
  }
  const changes: { deal_id: string; role: string; active: boolean }[] = [];
  for (const entry of entries) {
    const before = (await c.query(`select role,company_party_id,contact_party_id,active,note from public.deal_vendor_assignment
      where deal_id=$1 and party_id=$2 and role=$3 for update`, [entry.deal_id,vendor.party_id,entry.role])).rows[0] || null;
    const retiring = entry.active === false && before?.company_party_id === entry.company_party_id.toLowerCase() && before?.contact_party_id === entry.contact_party_id.toLowerCase();
    const valid = await c.query(`select d.id from public.deal d
      join public.party company on company.id=$2 and company.kind='org' and company.merged_into is null and company.deleted_at is null
      join public.party contact on contact.id=$3 and contact.kind='person' and (contact.org_id=company.id or $5::boolean) and contact.merged_into is null and contact.deleted_at is null
      where d.id=$1 and $4::uuid in (company.id,contact.id) for share of company,contact`,
    [entry.deal_id,entry.company_party_id,entry.contact_party_id,vendor.party_id,retiring]);
    if (!valid.rows.length) refuse();
    await c.query(`insert into public.deal_vendor_assignment(deal_id,party_id,role,company_party_id,contact_party_id,active,note,updated_by)
      values($1,$2,$3,$4,$5,$6,$7,$8) on conflict(deal_id,party_id,role) do update set
      company_party_id=excluded.company_party_id,contact_party_id=excluded.contact_party_id,active=excluded.active,
      note=excluded.note,updated_by=excluded.updated_by,updated_at=clock_timestamp()`,
    [entry.deal_id,vendor.party_id,entry.role,entry.company_party_id,entry.contact_party_id,entry.active ?? true,entry.note ?? before?.note ?? null,actor.id]);
    await recordEvent(c,actor,verb,'deal',entry.deal_id,{field:'vendor_assignment',old:{vendor_assignment:before},
      new:{vendor_assignment:{...entry,party_id:vendor.party_id,active:entry.active ?? true}},human_quote,idempotency_key:key});
    changes.push({deal_id:entry.deal_id,role:entry.role,active:entry.active ?? true});
  }
  return changes;
}

export const dealVendorCards = (alias: string) => `coalesce((select jsonb_agg(jsonb_build_object(
  'party_id',v.party_id,'role',v.role,'company',v.company,'contact',v.contact,'note',v.note,'updated_at',v.updated_at)
  order by v.role,v.company->>'name',v.contact->>'name',v.party_id)
  from public.v_deal_room_vendor v where v.deal_id=${alias}.id),'[]'::jsonb)`;
