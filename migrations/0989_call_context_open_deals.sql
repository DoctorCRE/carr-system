-- rollback: restore the board and context definitions from 0936 and 0094 in a forward migration.
-- expand-contract: expand; preserve both read interfaces and their grants.
-- lock-review: replace read definitions only; no business rows are changed.
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
          WHERE ((n.subject_type = 'deal'::text) AND (n.subject_id = d.id) AND (n.status = 'open'::text) AND n.item_title IS NULL)
          ORDER BY n.updated_at DESC, n.id DESC
         LIMIT 1), ( SELECT n.text
           FROM public.deal_note n
          WHERE ((n.deal_id = d.id) AND (n.kind = 'next_step'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM public.event e
                  WHERE ((e.subject_type = 'deal'::text) AND (e.subject_id = d.id) AND (e.field = 'next_step'::text) AND (e.new_value ? 'superseded_action_ids'::text) AND (e.recorded_at >= n.created_at))))))
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
  WHERE ((d.invoiced_on IS NULL AND d.outcome IS NULL AND d.closed_on IS NULL AND d.phase NOT IN ('closed', 'invoiced')) AND ((c.status <> 'prospect'::text) OR (c.prospect_engagement_status = 'active'::text)));

create or replace function capture_call_context(requested_deal_ids uuid[])
returns table (
  deal_id uuid,
  deal_name text,
  owner text,
  operating_state text,
  participant_party_id uuid,
  participant_party_ref text,
  participant_name text,
  participant_email text,
  participant_role text
)
language sql
security definer
set search_path = pg_catalog, public
as $$
  select d.id, d.name, d.owner, d.operating_state, dp.party_id, p.ref, p.name, p.email, dp.role
    from public.v_deal_room_board d
    left join public.deal_participant dp on dp.deal_id=d.id and dp.to_at is null
    left join public.party p on p.id=dp.party_id
   where d.id = any(requested_deal_ids)
     and d.operating_state = 'active'
$$;
