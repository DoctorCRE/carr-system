insert into actor(id,slug,kind,display_name)
values (md5('index-actor')::uuid,'index-proof','human','Synthetic index proof');
insert into party(id,kind,name,created_by,updated_by)
values (md5('index-party')::uuid,'person','Synthetic index proof',md5('index-actor')::uuid,md5('index-actor')::uuid);
insert into client(id,party_id,created_by,updated_by)
values (md5('index-client')::uuid,md5('index-party')::uuid,md5('index-actor')::uuid,md5('index-actor')::uuid);
insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by)
select md5('index-deal')::uuid,md5('index-client')::uuid,'Synthetic index proof',
 (select slug from deal_type_ref order by slug limit 1),
 (select slug from deal_phase order by slug limit 1),md5('index-actor')::uuid,md5('index-actor')::uuid;
insert into lead(id,party_id,stage,created_by,updated_by)
select md5('index-lead-'||n)::uuid,md5('index-party')::uuid,
 (select slug from lead_stage where slug<>'do_not_contact' order by slug limit 1),
 md5('index-actor')::uuid,md5('index-actor')::uuid from generate_series(1,2) n;
insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,
 session_token_hash,expires_at,state_at)
values (md5('index-session')::uuid,'synthetic','synthetic',md5('index-actor')::uuid,
 'meeting',now(),now(),repeat('0',64),now()+interval '1 hour',now());
insert into capture_post_call_candidate(id,session_id,idempotency_key,batch_hash,item_index,
 kind,deal_id,assignee_slug,action_description,evidence_quote,confidence)
select md5('index-candidate-'||n)::uuid,md5('index-session')::uuid,'synthetic-'||n,
 repeat('0',64),n,'assigned_action',md5('index-deal')::uuid,'joe','Synthetic action','Synthetic evidence',1
from generate_series(1,20000) n;
insert into next_action(id,subject_type,subject_id,owner_id,due_on,description,status,
 created_by,hold_until)
select md5('index-next-'||n)::uuid,'lead',md5('index-subject-'||n)::uuid,md5('index-actor')::uuid,
 case when n=4 then null when n=3 then current_date+1 else current_date-1 end,
 'Synthetic action',case when n<=100 then 'open' when n%2=0 then 'done' else 'dropped' end,
 md5('index-actor')::uuid,case when n=2 then current_date+1 when n=5 then current_date end
from generate_series(1,20000) n;
insert into capture_post_call_action(id,candidate_id,deal_id,owner_id,due_on,description,
 accepted_by,status,completed_at)
select md5('index-post-'||n)::uuid,md5('index-candidate-'||n)::uuid,md5('index-deal')::uuid,
 md5('index-actor')::uuid,
 case when n=4 then null when n=3 then current_date+1 else current_date end,
 'Synthetic action',md5('index-actor')::uuid,
 case when n<=100 then 'open' when n%2=0 then 'done' else 'dropped' end,
 case when n>100 and n%2=0 then now() end
from generate_series(1,20000) n;
insert into critical_date(id,deal_id,kind,due_on,source,status,created_by)
select md5('index-date-'||n)::uuid,md5('index-deal')::uuid,'synthetic',
 case when n=1 then current_date+14 when n=2 then current_date+15 else current_date-1 end,
 'stated',case when n<=100 then 'open' when n%2=0 then 'passed' else 'cleared' end,
 md5('index-actor')::uuid from generate_series(1,20000) n;
insert into ingest_inbox(id,source,external_id,payload,status,received_at)
select md5('index-inbox-'||n)::uuid,'synthetic','synthetic-'||n,'{"summary":"Synthetic inbox"}',
 case when n<=100 then 'new' else 'triaged' end,current_date-interval '1 day'
from generate_series(1,20000) n;
insert into activity(id,occurred_at,actor_id,kind,summary,lead_id)
select md5('index-activity-'||n)::uuid,timestamptz '2020-01-01 UTC'+(n/2)*interval '1 second',
 md5('index-actor')::uuid,(select slug from activity_kind order by slug limit 1),'Synthetic correspondence',
 case when n<=120 then md5('index-lead-1')::uuid when n<=240 then md5('index-lead-2')::uuid end
from generate_series(1,20000) n;
insert into event(id,occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause)
select md5('index-event-'||n)::uuid,timestamptz '2020-01-01 UTC'-n*interval '1 second',
 md5('index-actor')::uuid,'synthetic-index-proof',case when n>19800 then 'deal' else 'lead' end,
 case when n<=120 or n>240 then md5('index-lead-1')::uuid else md5('index-lead-2')::uuid end,
 case when n<=240 or n>19800 then '{"stage":"qualified"}'::jsonb
      when n%2=0 then '{"stage":null}'::jsonb else '{}'::jsonb end,'system'
from generate_series(1,20000) n order by n;
insert into party(id,kind,name,created_by,updated_by)
select md5('index-vendor-party-'||n)::uuid,'person','Synthetic vendor '||n,
 md5('index-actor')::uuid,md5('index-actor')::uuid from generate_series(1,20000) n;
insert into vendor(id,party_id,category,created_by,updated_by,deal_history_verified_at,merged_into,deal_evidence)
select md5('index-vendor-'||n)::uuid,
 case when n<=5 then md5('index-vendor-party-1')::uuid else md5('index-vendor-party-'||n)::uuid end,
 'lender',md5('index-actor')::uuid,md5('index-actor')::uuid,
 case when n not in (3,5) then timestamptz '2020-01-01 UTC'+(n-1)*interval '1 day' end,
 case when n=5 then md5('index-vendor-1')::uuid end,
 case when n=1 then jsonb_build_array(jsonb_build_object('deal_id',md5('index-deal')::uuid,
 'role','worked','occurred_at','2020-01-01T00:00:00Z','evidence_ref','synthetic')) else '[]'::jsonb end
from generate_series(1,20000) n;
insert into party_link(id,from_party,to_party,kind,note,source,created_by)
select md5('index-link-'||n)::uuid,md5('index-vendor-party-'||n)::uuid,md5('index-party')::uuid,
 'referral','Synthetic referral','entry',md5('index-actor')::uuid from generate_series(1,20000) n;
insert into party_link_deal(link_id,deal_id,referred_by,occurred_on,note,created_by)
select md5('index-link-'||n)::uuid,md5('index-deal')::uuid,md5('index-vendor-party-'||n)::uuid,
 date '2020-01-02','Synthetic attribution',md5('index-actor')::uuid from generate_series(1,20000) n;
