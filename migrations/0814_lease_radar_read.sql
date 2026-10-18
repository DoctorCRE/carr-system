-- Authenticated partner lease radar: recorded dates, explicit touch eligibility.
-- Reader gets this projection only; it never gains SELECT on the lease ledger.
create view public.v_client_lease_radar as
with clock as (select (now() at time zone 'America/Chicago')::date as today),
policy as (select today, (today + interval '24 months')::date as ends_on from clock),
leases as (
  select l.id, l.client_id, l.deal_id, p.name as client_name,
         c.status as client_status, cs.label as client_status_label,
         p.city, p.state, c.vertical, owner.slug as owner,
         coalesce(c.owner_label, owner.display_name) as owner_label,
         p.contact_state, p.contact_state_until,
         l.expiration_on, l.commencement_on, l.executed_on,
         l.term_months, l.status as lease_status, l.evidence_kind,
         l.options_note, l.evidence_ref, l.version,
         action.id as touch_id, action.due_on as touch_due_on,
         action.description as touch_summary,
         action_owner.slug as touch_owner,
         (c.status='past_client' and p.contact_state in ('active','nurture')
          and action.due_on <= policy.today and action_owner.slug in ('joe','dell')) as touch_eligible,
         notice.due_on as notice_on, notice.note as notice_note
    from public.lease l
    join public.client c on c.id=l.client_id and c.merged_into is null
    join public.party p on p.id=c.party_id and p.merged_into is null and p.deleted_at is null
    left join public.client_status cs on cs.slug=c.status
    left join public.actor owner on owner.id=c.owner_id
    cross join policy
    left join lateral (
      select n.id,n.due_on,n.description,n.owner_id
        from public.next_action n
       where n.status='open'
         and (n.hold_until is null or n.hold_until <= policy.today) and
         ((n.subject_type='client' and n.subject_id=c.id) or
          (n.subject_type='deal' and n.subject_id=l.deal_id))
       order by n.due_on nulls last,n.created_at,n.id limit 1
    ) action on true
    left join public.actor action_owner on action_owner.id=action.owner_id
    left join lateral (
      select cd.due_on,cd.note from public.critical_date cd
       where cd.deal_id=l.deal_id and cd.kind='option_window' and cd.status='open'
       order by cd.due_on,cd.id limit 1
    ) notice on true
   where l.status <> 'superseded'
     and (l.expiration_on is null or l.expiration_on between policy.today and policy.ends_on)
)
select policy.today as starts_on, policy.ends_on,
       coalesce(jsonb_agg(to_jsonb(l) order by l.expiration_on nulls last,l.id)
         filter (where l.id is not null),'[]'::jsonb) as leases
  from policy left join leases l on true
 group by policy.today, policy.ends_on;
revoke all on public.v_client_lease_radar from public;
grant select on public.v_client_lease_radar to carr_reader;
