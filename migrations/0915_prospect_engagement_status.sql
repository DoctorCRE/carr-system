-- rollback: forward-only. Remove consumers before dropping the status column and read view.
-- lock-review: constant default and two-value check on the small client table.
ALTER TABLE public.client ADD COLUMN prospect_engagement_status text NOT NULL DEFAULT 'inactive'
  CHECK (prospect_engagement_status IN ('active','inactive'));
COMMENT ON COLUMN public.client.prospect_engagement_status IS
  'Explicit prospect engagement. Independent of lifecycle status and per-deal operating state. Inactive until deliberately activated.';
CREATE VIEW public.v_client_engagement_status AS
SELECT id AS client_id, status AS client_status, version AS client_version,
       prospect_engagement_status
FROM public.client WHERE merged_into IS NULL;
GRANT SELECT ON public.v_client_engagement_status TO carr_reader,carr_writer;
