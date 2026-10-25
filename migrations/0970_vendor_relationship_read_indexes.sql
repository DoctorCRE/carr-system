-- rollback: forward-only; a successor may drop these performance-only indexes.
-- expand-contract: expand; no data or read contract changes.
-- lock-review: ordinary index builds take SHARE locks; retain runner timeouts.
create index vendor_live_party_idx
  on public.vendor (party_id, id)
  where merged_into is null;
create index party_link_deal_referrer_idx
  on public.party_link_deal (referred_by, deal_id);
