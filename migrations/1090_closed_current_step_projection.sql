-- rollback: restore the board definition from 1077, then drop v_deal_current_action.
-- lock-review: replace read views only; no table rewrite or plan mutation.
CREATE VIEW public.v_deal_current_action AS
SELECT DISTINCT ON (n.subject_id) n.subject_id AS deal_id, n.id, n.status, n.description
FROM public.next_action n
WHERE n.subject_type='deal' AND n.item_title IS NULL AND n.status IN ('open','done')
  AND (n.status='open' OR NOT EXISTS (
    SELECT 1 FROM public.deal_note step
    WHERE step.deal_id=n.subject_id AND step.kind='next_step' AND step.created_at>n.updated_at))
  AND NOT EXISTS (
    SELECT 1 FROM public.event request
    WHERE request.subject_type='deal' AND request.subject_id=n.subject_id
      AND request.field='overdue_assessment_pending'
      AND request.new_value->>'candidate_kind'='next_step'
      AND request.new_value->'actions' @> jsonb_build_array(
        jsonb_build_object('id',n.id::text,'source','next_action'))
      AND NOT EXISTS (
        SELECT 1 FROM public.event undone
        WHERE undone.subject_type='deal' AND undone.subject_id=n.subject_id
          AND undone.field='call_change_undo'
          AND undone.new_value->>'candidate_id'=request.new_value->>'candidate_id'))
ORDER BY n.subject_id, (n.status='open') DESC, n.updated_at DESC, n.id DESC;
GRANT SELECT ON public.v_deal_current_action TO carr_reader, carr_writer;

CREATE OR REPLACE VIEW public.v_deal_room_board AS
 SELECT d.id,
    d.name,
    d.deal_type AS type,
    d.phase,
    d.owner,
    d.attention,
    CASE WHEN current_action.status='done' THEN NULL::date ELSE d.next_date END AS next_date,
    CASE WHEN current_action.status='done' THEN NULL::text ELSE COALESCE(
      current_action.description, ( SELECT n.text
           FROM public.deal_note n
          WHERE ((n.deal_id = d.id) AND (n.kind = 'next_step'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM public.event e
                  WHERE ((e.subject_type = 'deal'::text) AND (e.subject_id = d.id) AND (e.field = 'next_step'::text) AND (e.new_value ? 'superseded_action_ids'::text) AND (e.recorded_at >= n.created_at))))))
          ORDER BY n.created_at DESC, n.id DESC
         LIMIT 1)) END AS next_step,
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
  LEFT JOIN public.v_deal_current_action current_action ON current_action.deal_id=d.id
  WHERE ((d.invoiced_on IS NULL) AND ((c.status <> 'prospect'::text) OR (c.prospect_engagement_status = 'active'::text)));
