-- rollback: forward-only. Remove consumers before dropping the status column and read view.
-- lock-review: constant default and two-value check on the small client table.
ALTER TABLE public.client ADD COLUMN prospect_engagement_status text NOT NULL DEFAULT 'active'
  CHECK (prospect_engagement_status IN ('active','inactive'));
COMMENT ON COLUMN public.client.prospect_engagement_status IS
  'Explicit prospect engagement. Independent of lifecycle status and per-deal operating state. Active until explicitly returned to Leads.';

-- The constant default backfills existing rows without changing their versions.
-- Record each live prospect's initialization separately from partner choices.
INSERT INTO public.event (occurred_at,actor_id,verb,subject_type,subject_id,
                          field,old_value,new_value,cause,agent_rationale,idempotency_key)
SELECT now(),(SELECT id FROM public.actor WHERE slug='system'),
       'backfill-prospect-engagement-status','client',c.id,
       'prospect_engagement_status',NULL,
       jsonb_build_object('prospect_engagement_status',c.prospect_engagement_status),
       'import_migration','0915: preserve existing prospects until an explicit partner engagement choice',
       '0915:prospect-engagement:' || c.id::text
FROM public.client c WHERE c.status='prospect' AND c.merged_into IS NULL;

CREATE VIEW public.v_client_engagement_status AS
SELECT id AS client_id, status AS client_status, version AS client_version,
       prospect_engagement_status
FROM public.client WHERE merged_into IS NULL;
GRANT SELECT ON public.v_client_engagement_status TO carr_reader,carr_writer;

CREATE VIEW public.v_lead_prospect_engagement AS
 SELECT l.id AS lead_id, c.id AS prospect_client_id,
    c.version AS prospect_client_version, c.prospect_engagement_status
 FROM public.lead l JOIN public.client c ON c.id=l.client_id
 WHERE c.status='prospect' AND c.merged_into IS NULL;
GRANT SELECT ON public.v_lead_prospect_engagement TO carr_reader,carr_writer;

CREATE OR REPLACE VIEW public.v_deal_room_board AS
 SELECT d.id,
    d.name,
    d.deal_type AS type,
    d.phase,
    d.owner,
    d.attention,
    d.next_date,
    COALESCE(( SELECT n.description
           FROM public.next_action n
          WHERE ((n.subject_type = 'deal'::text) AND (n.subject_id = d.id) AND (n.status = 'open'::text))
          ORDER BY n.updated_at DESC, n.id DESC
         LIMIT 1), ( SELECT n.text
           FROM public.deal_note n
          WHERE ((n.deal_id = d.id) AND (n.kind = 'next_step'::text))
          ORDER BY n.created_at DESC, n.id DESC
         LIMIT 1)) AS next_step,
    d.city AS market,
    d.segment,
    c.id AS client_id,
    c.roster_ref AS client_ref,
    cp.name AS client_name,
    vca.account_client_id,
    vca.account_client_ref,
    vca.account_name,
    ao.slug AS account_owner,
    dma.agent_name AS market_agent,
    dma.agent_party_id AS market_agent_party_id,
    lt.last_touch,
    ( SELECT max(i.reviewed_at) AS max
           FROM (public.deal_review_item i
             JOIN public.deal_review_session s ON ((s.id = i.session_id)))
          WHERE ((i.deal_id = d.id) AND (i.disposition = 'reviewed'::text) AND (s.status = 'completed'::text))) AS last_review_at,
        CASE
            WHEN (vca.account_client_id IS NULL) THEN 'team'::text
            ELSE 'national_account'::text
        END AS workspace_kind,
    d.operating_state,
    d.parking_reason,
    d.parking_note,
    d.parked_at,
    pa.slug AS parked_by,
    d.invoiced_on,
    d.closed_on,
    d.lane,
    d.outcome
   FROM ((((((((public.deal d
     JOIN public.client c ON ((c.id = d.client_id)))
     JOIN public.party cp ON ((cp.id = c.party_id)))
     LEFT JOIN public.v_client_account vca ON (((vca.client_id = c.id) AND vca.is_sub_client)))
     LEFT JOIN public.national_account_owner nao ON ((nao.account_client_id = vca.account_client_id)))
     LEFT JOIN public.actor ao ON ((ao.id = nao.owner_actor_id)))
     LEFT JOIN public.deal_market_assignment dma ON ((dma.deal_id = d.id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'deal'::text) AND (lt.subject_id = d.id))))
     LEFT JOIN public.actor pa ON ((pa.id = d.parked_by)))
  WHERE ((d.invoiced_on IS NULL) AND (c.status <> 'prospect'::text OR c.prospect_engagement_status = 'active'::text));
