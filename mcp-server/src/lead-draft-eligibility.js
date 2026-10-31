// Shared draft eligibility for approval and action links.
export const DRAFT_ELIGIBILITY_SQL = `d.party_id=l.party_id and l.stage='qualified' and not l.suppressed
  and p.merged_into is null and p.deleted_at is null and p.contact_state='active'`;
