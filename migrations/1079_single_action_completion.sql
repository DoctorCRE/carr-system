-- rollback: forward-only; retain action provenance and versions for guarded callers.
-- expand-contract: expand; v1 complete-action retains its subject-wide behavior.
-- lock-review: short table locks for constant defaults, one trigger and a view replacement; no destructive rewrite.
set local lock_timeout = '5s';
set local statement_timeout = '30s';

alter table public.next_action add column organization_tenant_id text not null default 'carr-internal';
alter table public.capture_post_call_action add column organization_tenant_id text not null default 'carr-internal';
alter table public.capture_post_call_action add column version integer not null default 1;
create trigger capture_post_call_action_touch before update on public.capture_post_call_action
  for each row execute function public.trg_touch_row();

CREATE OR REPLACE VIEW public.v_deal_room_action AS
 SELECT n.id,
    n.subject_id AS deal_id,
    a.slug AS owner,
    n.description,
    n.due_on,
    n.status,
    n.updated_at,
    n.hold_until,
    n.item_title,
    n.item_bullets,
    n.source_ref,
    n.created_at AS created_order,
    n.version
   FROM (public.next_action n
     JOIN public.actor a ON ((a.id = n.owner_id)))
  WHERE (n.subject_type = 'deal'::text)
UNION ALL
 SELECT pca.id,
    pca.deal_id,
    a.slug AS owner,
    pca.description,
    pca.due_on,
    pca.status,
    pca.updated_at,
    NULL::date AS hold_until,
    pca.item_title,
    pca.item_bullets,
    pca.source_ref,
    pca.accepted_at AS created_order,
    pca.version
   FROM (public.capture_post_call_action pca
     JOIN public.actor a ON ((a.id = pca.owner_id)));

CREATE OR REPLACE VIEW public.v_deal_room_event AS
 SELECT e.id, e.recorded_at, a.slug AS actor, e.verb, e.subject_type, e.subject_id,
    e.field,
    (e.old_value - ARRAY['sf_commission_placeholder'::text, 'sf_close_date_placeholder'::text]) AS old_value,
    (e.new_value - ARRAY['sf_commission_placeholder'::text, 'sf_close_date_placeholder'::text]) AS new_value,
    e.mutation_order
   FROM public.event e JOIN public.actor a ON a.id = e.actor_id
  WHERE e.subject_type = 'deal'::text
    AND e.field IS DISTINCT FROM 'sf_commission_placeholder'::text
    AND e.field IS DISTINCT FROM 'sf_close_date_placeholder'::text;
