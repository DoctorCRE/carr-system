-- rollback: forward-only; the added columns preserve existing action and note history.
-- expand-contract: expand; structured items extend the two canonical action stores.
-- lock-review: nullable metadata columns and a replacement partial index; no business-data rewrite.
alter table next_action add column item_title text, add column item_bullets jsonb not null default '[]', add column source_ref text;
alter table capture_post_call_action add column item_title text, add column item_bullets jsonb not null default '[]', add column source_ref text;

alter table next_action add constraint next_action_item_shape check (
  item_title is null or (subject_type='deal' and cardinality(regexp_split_to_array(btrim(item_title),'\s+')) between 2 and 4
    and source_ref is not null and btrim(source_ref) <> '' and jsonb_typeof(item_bullets)='array'));
alter table capture_post_call_action add constraint capture_post_call_item_shape check (
  item_title is null or (cardinality(regexp_split_to_array(btrim(item_title),'\s+')) between 2 and 4
    and source_ref is not null and btrim(source_ref) <> '' and jsonb_typeof(item_bullets)='array'));

drop index one_open_next_action_per_owner;
create unique index one_open_next_action_per_owner on next_action(subject_type,subject_id,owner_id)
  where status='open' and item_title is null;
create unique index deal_item_source_unique on next_action(subject_id,source_ref,item_title)
  where subject_type='deal' and item_title is not null;

create or replace view v_deal_room_action as
select n.id,n.subject_id as deal_id,a.slug as owner,n.description,n.due_on,n.status,n.updated_at,n.hold_until,
       n.item_title,n.item_bullets,n.source_ref,n.created_at as created_order
  from next_action n join actor a on a.id=n.owner_id where n.subject_type='deal'
union all
select pca.id,pca.deal_id,a.slug,pca.description,pca.due_on,pca.status,pca.updated_at,null::date,
       pca.item_title,pca.item_bullets,pca.source_ref,pca.accepted_at
  from capture_post_call_action pca join actor a on a.id=pca.owner_id;
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
  WHERE ((d.invoiced_on IS NULL) AND ((c.status <> 'prospect'::text) OR (c.prospect_engagement_status = 'active'::text)));
