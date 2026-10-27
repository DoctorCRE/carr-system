-- rollback: forward-only — drop the column once no approval or edit verb reads it.
-- expand-contract: expand; adds a defaulted column and no existing reader changes.
-- lock-review: public.lead_contact_draft holds one row per lead; the constant default adds the column without a rewrite.
-- WR-000467 S2: each draft carries a revision. An edit increments it and clears approval,
-- and approval names the revision the partner read, so edited content can never ride an old approval.
alter table public.lead_contact_draft
  add column revision integer not null default 1 constraint lead_contact_draft_revision_check check (revision >= 1);
