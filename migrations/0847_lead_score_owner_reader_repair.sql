-- rollback: forward-only — restore recorded old field values through a versioned correction; preserve repair events and additive reader columns.
-- lock-review: Lead has a small live inventory; additive DDL holds its table lock through this transaction. Party share locks keep the target states stable. Rehearse lock duration on a throwaway Neon branch and use the sanctioned runner's timeouts.
-- Keep numeric storage because existing views depend on its type. The check
-- makes the integer contract apply to every writer without rounding history.
alter table lead add column if not exists score_reason text;
alter table lead add constraint lead_score_integer_range
  check (score is null or (score between 0 and 100 and score=trunc(score))) not valid;
alter table lead validate constraint lead_score_integer_range;

create or replace view v_lead_board as
select l.id,
       l.registry_ref,
       p.name,
       p.specialty,
       p.city,
       p.county,
       p.state,
       l.lane,
       l.stage,
       ls.label as stage_label,
       ls.sort as stage_sort,
       l.score,
       l.segment,
       l.suppressed,
       l.est_lease_event,
       l.event_confidence,
       coalesce(lt.last_touch, l.last_touch) as last_touch,
       l.next_action_date,
       owner.slug as owner,
       coalesce(l.owner_label, initcap(owner.slug)) as owner_label,
       l.version as base_version,
       l.created_at,
       l.updated_at,
       l.party_id,
       l.client_id is not null as converted,
       coalesce(moves.items, '[]'::jsonb) as stage_moves
  from lead l
  join party p on p.id = l.party_id
  join lead_stage ls on ls.slug = l.stage
  left join actor owner on owner.id = l.owner_id
  left join v_last_touch lt
    on lt.subject_type = 'lead' and lt.subject_id = l.id
  left join lateral (
    select jsonb_agg(jsonb_build_object('move_id',m.id,'from_stage',m.from_stage,
      'to_stage',m.to_stage,'reason',m.reason,'evidence_ref',m.evidence_ref,'status',m.status,
      'created_at',m.created_at,'undone_at',m.undone_at,'undone_by',a.display_name)
      order by m.created_at,m.id) as items
    from lead_stage_move m left join actor a on a.id=m.undone_by where m.lead_id=l.id
  ) moves on true;

-- Spell out the prior board fields because PostgreSQL freezes a view's b.* at
-- creation. Preserve workspace column order, including its existing party_id.
create or replace view v_lead_workspace as
select b.id,b.registry_ref,b.name,b.specialty,b.city,b.county,b.state,b.lane,b.stage,
       b.stage_label,b.stage_sort,b.score,b.segment,b.suppressed,b.est_lease_event,
       b.event_confidence,b.last_touch,b.next_action_date,b.owner,b.owner_label,
       b.base_version,b.created_at,b.updated_at,
       life.party_id,life.client_id,life.is_client,life.linked_client,life.is_deal,
       life.contact_state,life.do_not_contact,life.contact_eligible,
       case when p.kind='person' then p.name end as doctor_name,
       org.name as practice_name,p.name as entity_name,
       coalesce(matches.items,'[]'::jsonb) as possible_clients,
       last_move.item as last_stage_move,l.score_reason
from v_lead_board b join v_lead_workspace_lifecycle life on life.id=b.id
join lead l on l.id=b.id
join party p on p.id=life.party_id left join party org on org.id=p.org_id
left join lateral (
 select jsonb_agg(jsonb_build_object('client_id',cl.id,'name',cp.name) order by cl.id) as items
 from client cl join party cp on cp.id=cl.party_id
 where lower(trim(cp.name))=lower(trim(p.name)) and cp.id<>p.id
   and cl.merged_into is null and cp.merged_into is null and cp.deleted_at is null
) matches on true
left join lateral (
 select jsonb_build_object('event_id',e.event_id,'idempotency_key',e.idempotency_key,
   'from',e.prior_stage,'to',e.stage,'occurred_at',e.occurred_at,
   'automatic',e.automatic,'reason',e.reason,'evidence_date',e.stage_review->>'evidence_date',
   'undone',e.stage_review->>'undo_event_id') as item
 from v_lead_stage_transition e where e.lead_id=b.id order by e.mutation_order desc limit 1
) last_move on true
where not life.suppressed and life.live_party;

create temp table lead_territory_score_repair on commit drop as
select l.id,l.registry_ref,p.state,l.version as old_version,
       l.score as old_score,l.score_reason as old_score_reason,l.segment as old_segment,
       a.slug as old_owner,l.owner_id as old_owner_id,l.owner_label as old_owner_label,
       ((regexp_match(l.segment,'^Expansion signal [–-] est[.] score ([0-9]{1,3})$'))[1])::integer as new_score,
       'Estimated territory lead score recovered from original segment text'::text as new_score_reason,
       'Expansion signal'::text as new_segment,
       case when p.state='AL' then dell.slug else a.slug end as new_owner,
       case when p.state='AL' then dell.id else l.owner_id end as new_owner_id,
       case when p.state='AL' then dell.display_name else l.owner_label end as new_owner_label,
       exists(select 1 from event e where e.subject_type='lead' and e.subject_id=l.id
              and e.verb='backfill-lead-score-owner' and e.idempotency_key='wr-000218:'||l.registry_ref) as repaired
from lead l join party p on p.id=l.party_id
left join actor a on a.id=l.owner_id
left join actor dell on dell.slug='dell' and dell.active
where l.registry_ref in (select 'L-'||n from generate_series(269,313) n)
for share of p;

do $$
declare n integer; done integer;
begin
  select count(*),count(*) filter(where repaired) into n,done from lead_territory_score_repair;
  if n=0 then return; end if;
  if n<>45 then raise exception 'WR-000218 refuses partial territory batch: % of 45 rows',n; end if;
  if done=45 then
    if exists(
      select 1 from lead_territory_score_repair t
      where (select count(*) from event e where e.subject_type='lead' and e.subject_id=t.id
             and e.verb='backfill-lead-score-owner' and e.idempotency_key='wr-000218:'||t.registry_ref)<>1
        or not exists(
          select 1 from event e join actor a on a.id=e.actor_id
          where e.subject_type='lead' and e.subject_id=t.id and e.verb='backfill-lead-score-owner'
            and e.idempotency_key='wr-000218:'||t.registry_ref and e.cause='import_migration' and a.slug='system'
            and e.old_value->>'owner'='joe' and e.old_value->'score'='null'::jsonb
            and e.new_value->>'segment'='Expansion signal'
            and e.new_value->>'score_reason'=t.new_score_reason
            and e.new_value->>'score'=((regexp_match(e.old_value->>'segment','^Expansion signal [–-] est[.] score ([0-9]{1,3})$'))[1])::integer::text
            and e.new_value->>'owner'=case when t.state='AL' then 'dell' else 'joe' end
            and e.new_value->'score'=to_jsonb(t.old_score)
            and e.new_value->>'score_reason'=t.old_score_reason
            and e.new_value->>'segment'=t.old_segment
            and e.new_value->>'owner_id'=t.old_owner_id::text
            and e.new_value->>'owner_label' is not distinct from t.old_owner_label)) then
      raise exception 'WR-000218 refuses inconsistent prior repair receipts or target values';
    end if;
    return;
  end if;
  if done<>0 then raise exception 'WR-000218 refuses partial prior repair: % of 45 receipts',done; end if;
  if (select count(*) from lead_territory_score_repair where state='AL')<>44 then
    raise exception 'WR-000218 expected exactly 44 Alabama leads';
  end if;
  if exists(select 1 from lead_territory_score_repair
            where new_score is null or new_score not between 0 and 100
              or old_score is not null or old_score_reason is not null
              or old_owner is distinct from 'joe' or new_owner_id is null) then
    raise exception 'WR-000218 refuses changed or ambiguous territory score/owner values';
  end if;
end $$;

select registry_ref,state,old_version,old_score,new_score,old_score_reason,new_score_reason,
       old_segment,new_segment,old_owner,new_owner,old_owner_id,new_owner_id,old_owner_label,new_owner_label
from lead_territory_score_repair where not repaired order by registry_ref;

do $$
declare r record; system_actor uuid; changed integer;
begin
  if not exists(select 1 from lead_territory_score_repair where not repaired) then return; end if;
  select id into strict system_actor from actor where slug='system' and active;
  for r in select * from lead_territory_score_repair where not repaired order by registry_ref loop
    update lead set score=r.new_score,score_reason=r.new_score_reason,segment=r.new_segment,
                    owner_id=r.new_owner_id,owner_label=r.new_owner_label,updated_by=system_actor
    where id=r.id and version=r.old_version and score is not distinct from r.old_score
      and score_reason is not distinct from r.old_score_reason and segment is not distinct from r.old_segment
      and owner_id is not distinct from r.old_owner_id and owner_label is not distinct from r.old_owner_label;
    get diagnostics changed=row_count;
    if changed<>1 then raise exception 'WR-000218 concurrent change on %',r.registry_ref; end if;
    insert into event(occurred_at,actor_id,verb,subject_type,subject_id,old_value,new_value,
                      cause,agent_rationale,idempotency_key)
    values(now(),system_actor,'backfill-lead-score-owner','lead',r.id,
      jsonb_build_object('score',r.old_score,'score_reason',r.old_score_reason,'segment',r.old_segment,
        'owner',r.old_owner,'owner_id',r.old_owner_id,'owner_label',r.old_owner_label,'version',r.old_version),
      jsonb_build_object('score',r.new_score,'score_reason',r.new_score_reason,'segment',r.new_segment,
        'owner',r.new_owner,'owner_id',r.new_owner_id,'owner_label',r.new_owner_label,'version',r.old_version+1),
      'import_migration','WR-000218: recover territory estimates and restore Alabama licensed ownership',
      'wr-000218:'||r.registry_ref);
  end loop;
end $$;

select l.registry_ref,p.state,t.old_version,l.version as new_version,
       t.old_score,l.score as new_score,t.old_score_reason,l.score_reason as new_score_reason,
       t.old_segment,l.segment as new_segment,t.old_owner,a.slug as new_owner,
       t.old_owner_id,l.owner_id as new_owner_id,t.old_owner_label,l.owner_label as new_owner_label
from lead_territory_score_repair t join lead l on l.id=t.id join party p on p.id=l.party_id
left join actor a on a.id=l.owner_id where not t.repaired order by l.registry_ref;
