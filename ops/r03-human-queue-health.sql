-- Read-only V5-R03 standing check. Run with tools/db-tap.py sql on a database
-- where 0736 has landed. Counts metadata only; no titles or bodies are returned.
-- A persisted in-app row is not evidence that a human saw or acknowledged it.
-- notification-feed uses a read-only writer connection; before read-call audit
-- coverage of that route is deployed, zero tool_read_call rows do not establish
-- zero feed reads. Even after coverage, a successful call does not prove sight.
-- failed_attempts is historical; unresolved_failed_attempts counts failures
-- only while their recipient still lacks an in-app row.
with targeted as (
  select l.id,l.kind,l.owner,l.created_by,l.created_at,l.marker,l.due_on,
         r.id as recipient_actor,r.slug as recipient_slug,
         (l.personal_to is null or l.personal_to=r.id) as visible_to_recipient,
         (l.created_by <> r.id) as other_authored,
         (l.marker='dated' and (l.due_on is null or
           l.due_on > (now() at time zone coalesce(p.timezone,'UTC'))::date)) as deferred
    from public.loop_item l
    join public.actor r on r.slug=l.owner and r.kind='human' and r.active
    left join ops.notification_preference p on p.actor=r.id
   where l.status='open' and l.tier='shared'
     and l.kind = 'action_required'
     and l.owner in ('joe','dell')
), eligible as (
  select t.* from targeted t
   where t.visible_to_recipient and t.other_authored
     and not t.deferred
), measured as (
  select e.*,
         n.id as notification_id,
         d.id as in_app_id,
         device.id as device_id,
         nr.notification_id as read_id,
         exists (select 1 from public.event v
                  where v.subject_type='loop' and v.subject_id=e.id
                    and ((v.verb='add-loop' and v.actor_id=e.created_by and
                          v.new_value->>'owner'=e.owner and
                          v.new_value->>'kind'=e.kind)
                      or (v.verb='reconcile-loop-notification' and
                          v.cause='import_migration' and
                          v.new_value->>'source'='current_loop_item' and
                          v.new_value->>'owner'=e.owner and
                          v.new_value->>'kind'=e.kind and
                          exists (select 1 from public.actor x
                                   where x.id=v.actor_id and x.slug='claude'
                                     and x.kind='automation')))) as has_source_event,
         exists (select 1 from public.event v
                  where v.subject_type='loop' and v.subject_id=e.id
                    and v.verb='add-loop' and v.actor_id=e.created_by and
                    v.new_value->>'owner'=e.owner and
                    v.new_value->>'kind'=e.kind) as has_creation_event,
         (select count(*) from public.event a
           where a.subject_type='loop' and a.subject_id=e.id
             and a.verb='loop-notification-attempt'
             and a.new_value->>'recipient_actor'=e.recipient_actor::text
             and a.new_value->>'outcome'='failed'
             and exists (select 1 from public.actor x
                          where x.id=a.actor_id and x.slug='claude'
                            and x.kind='automation')) as failed_attempts,
         (select count(*) from public.event a
           where a.subject_type='loop' and a.subject_id=e.id
             and a.verb='loop-notification-attempt'
             and a.new_value->>'recipient_actor'=e.recipient_actor::text
             and a.new_value->>'outcome'='deduplicated'
             and exists (select 1 from public.actor x
                          where x.id=a.actor_id and x.slug='claude'
                            and x.kind='automation')) as deduped_attempts
    from eligible e
    left join lateral (
      select n.id from ops.notification n
       where n.subject_type='loop' and n.subject_ref=e.id::text
         and n.recipient_actor=e.recipient_actor
       order by n.created_at,n.id limit 1
    ) n on true
    left join ops.notification_delivery d on d.notification_id=n.id
      and d.channel='in_app'
    left join ops.notification_delivery device on device.notification_id=n.id
      and device.channel='device'
    left join ops.notification_read nr on nr.notification_id=n.id
      and nr.recipient_actor=e.recipient_actor
), recipients as (
  select id,slug from public.actor
   where slug in ('joe','dell') and kind='human' and active
)
select r.slug as recipient,
       count(m.id) as eligible_actions,
       count(m.notification_id) as persisted_notifications,
       count(m.in_app_id) as in_app_rows,
       count(m.read_id) as acknowledged,
       count(m.id) filter (where m.notification_id is null) as unnotified,
       count(m.id) filter (where m.in_app_id is null) as missing_in_app_rows,
       count(m.id) filter (where not m.has_source_event) as missing_source_event,
       coalesce(sum(m.failed_attempts),0) as failed_attempts,
       coalesce(sum(m.failed_attempts)
         filter (where m.in_app_id is null),0) as unresolved_failed_attempts,
       coalesce(sum(m.deduped_attempts),0) as deduped_attempts,
       (select count(*) from targeted t where t.recipient_actor=r.id
         and t.visible_to_recipient and t.other_authored and t.deferred) as deferred_excluded,
       count(m.id) filter (where m.notification_id is null and
         not m.has_creation_event)
         as unnotified_age_unknown,
       floor(extract(epoch from now()-min(m.created_at)
         filter (where m.notification_id is null and m.has_creation_event))/86400)::integer
         as oldest_unnotified_days,
       case when count(m.id) filter
                   (where m.in_app_id is null or not m.has_source_event) = 0
            then 'No open R03 producer gap in this recipient set; historical failures remain diagnostic.'
            else 'On missing in-app delivery or source provenance: R03 owner repairs the producer or retries the bounded reconciliation; verify the open-gap counters reach zero.'
       end as breach_response,
       count(m.device_id) as device_rows,
       (select count(*) from public.tool_read_call tr
         where tr.verb='notification-feed' and tr.actor_slug=r.slug) as feed_call_observations,
       (select count(*) from public.tool_read_call tr
         where tr.verb='notification-feed' and tr.actor_slug=r.slug and tr.ok)
         as successful_feed_call_observations,
       (select max(tr.created_at) from public.tool_read_call tr
         where tr.verb='notification-feed' and tr.actor_slug=r.slug)
         as latest_feed_call_at
  from recipients r left join measured m on m.recipient_actor=r.id
 group by r.id,r.slug order by r.slug;
