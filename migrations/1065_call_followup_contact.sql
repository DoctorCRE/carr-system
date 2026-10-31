-- Carry a resolved counterpart on a call follow-up without guessing from its deal.
-- rollback: leave the nullable column in place and roll back the application.
-- lock-review: small capture proposal table; nullable column adds no rewrite.
alter table public.capture_post_call_candidate
  add column contact_party_id uuid references public.party(id);
