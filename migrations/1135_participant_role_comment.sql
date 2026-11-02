-- Replace historical client provenance with the vocabulary's domain meaning.
-- rollback: forward-only; restore prose through a new comment migration if needed.
COMMENT ON TABLE public.participant_role IS 'What a party is ON a deal. investor and capital_partner cover parties funding a venture for equity or profit share. Adding a role is a reference row, not a migration.';
