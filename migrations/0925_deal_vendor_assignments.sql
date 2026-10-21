-- rollback: remove the views and assignment table only after exporting attributed assignment history
-- lock-review: new table and views only; no existing rows rewritten
alter table public.party add column vendor_assignment_version integer not null default 1;

create table public.deal_vendor_assignment (
  deal_id uuid not null references public.deal(id),
  party_id uuid not null references public.party(id),
  role text not null check (role in ('lender','contractor','architect','attorney','title','inspector','landlord_rep')),
  company_party_id uuid not null references public.party(id),
  contact_party_id uuid not null references public.party(id),
  active boolean not null default true,
  note text,
  updated_by uuid not null references public.actor(id),
  updated_at timestamptz not null default now(),
  primary key (deal_id,party_id,role)
);

create unique index deal_vendor_assignment_active_contact
  on public.deal_vendor_assignment(deal_id,role,contact_party_id) where active;

create view public.v_deal_room_vendor as
select a.deal_id,a.party_id,a.role,
  jsonb_build_object('id',company.id,'name',company.name) as company,
  jsonb_build_object('id',contact.id,'name',contact.name,'email',contact.email,'phone',contact.phone) as contact,
  a.note,to_jsonb(a.updated_at)#>>'{}' as updated_at
from public.deal_vendor_assignment a
join public.party p on p.id=a.party_id and p.merged_into is null and p.deleted_at is null
join public.party company on company.id=a.company_party_id and company.merged_into is null and company.deleted_at is null
join public.party contact on contact.id=a.contact_party_id and contact.merged_into is null and contact.deleted_at is null
where a.active and exists (select 1 from public.vendor v where v.party_id=a.party_id and v.merged_into is null);

grant select on public.v_deal_room_vendor to carr_reader,carr_writer;
grant select,insert,update on public.deal_vendor_assignment to carr_writer;

create view public.v_vendor_assignment_context as
select v.id,v.vendor_ref as ref,v.version as base_version,p.vendor_assignment_version as assignment_version,v.party_id,
  case when company.id is not null then jsonb_build_object('id',company.id,'name',company.name) end as company,
  coalesce((select jsonb_agg(jsonb_build_object('id',contact.id,'name',contact.name) order by contact.name,contact.id)
    from public.party contact where contact.kind='person' and contact.merged_into is null and contact.deleted_at is null
    and ((p.kind='person' and contact.id=p.id) or (p.kind='org' and contact.org_id=p.id))),'[]'::jsonb) as contacts
from public.vendor v join public.party p on p.id=v.party_id
left join public.party company on company.id=case when p.kind='org' then p.id else p.org_id end
  and company.kind='org' and company.merged_into is null and company.deleted_at is null
where v.merged_into is null and p.merged_into is null and p.deleted_at is null;
grant select on public.v_vendor_assignment_context to carr_reader,carr_writer;

create view public.v_vendor_duplicate_report as
with live as (
  select v.id,v.party_id,p.org_id,
    nullif(lower(btrim(p.email)),'') as email,
    nullif(lower(regexp_replace(btrim(p.name),'\s+',' ','g')),'') as name
  from public.vendor v join public.party p on p.id=v.party_id
  where v.merged_into is null and p.merged_into is null and p.deleted_at is null
), same_party as (
  select count(*) as n from live group by party_id having count(*)>1
), email_match as (
  select email from live where email is not null group by email having count(distinct party_id)>1
), name_company_match as (
  select name,org_id from live where name is not null and org_id is not null
  group by name,org_id having count(distinct party_id)>1
)
select (select count(*)::int from live) as live_vendor_rows,
  (select count(*)::int from same_party) as same_party_groups,
  (select coalesce(sum(n-1),0)::int from same_party) as extra_vendor_rows,
  (select count(*)::int from email_match) as email_match_groups,
  (select count(*)::int from name_company_match) as name_company_match_groups;
grant select on public.v_vendor_duplicate_report to carr_reader,carr_writer;

-- Current catalog reseal
-- Register exactly the two new writer grants. Historical registry rows remain sealed.
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.deal_vendor_assignment for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.deal_vendor_assignment for each statement execute function ops.scac_reference_monitor_guard();

CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $fn$ SELECT $current_grants${
  "registry_version": "scac-mutation-registry.v118",
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "secdef_execute": [
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, public, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.transfer_studio_leader(p_source text, p_target text, p_epoch bigint, p_evidence jsonb)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.transfer_studio_leader(p_source text, p_target text, p_epoch bigint, p_evidence jsonb):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.write_feature_switch(p_action text, p_args jsonb)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.write_feature_switch(p_action text, p_args jsonb):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "retired_secdef_execute": [
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.approve_rule(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.classify_sourced_work_request_build(p_work_request text, p_base_version integer, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.classify_sourced_work_request_build(p_work_request text, p_base_version integer, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "public",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.heavy_build_ready_plan_gate()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.heavy_build_ready_plan_gate():public:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, public, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_heavy_build jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_heavy_build jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.record_sourced_heavy_build_admission(p_plan_id uuid, p_work_request text, p_base_version integer, p_classifier_reasons jsonb, p_contract jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.record_sourced_heavy_build_admission(p_plan_id uuid, p_work_request text, p_base_version integer, p_classifier_reasons jsonb, p_contract jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.review_sourced_heavy_build_plan(p_work_request text, p_plan_hash text, p_admission_hash text, p_reviewer_actor_id uuid, p_verdict text, p_reviewer_session_ref text, p_review_summary text, p_evidence_refs jsonb, p_gaps jsonb, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.review_sourced_heavy_build_plan(p_work_request text, p_plan_hash text, p_admission_hash text, p_reviewer_actor_id uuid, p_verdict text, p_reviewer_session_ref text, p_review_summary text, p_evidence_refs jsonb, p_gaps jsonb, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.sourced_heavy_build_review_target(p_work_request text, p_plan_hash text, p_admission_hash text)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.sourced_heavy_build_review_target(p_work_request text, p_plan_hash text, p_admission_hash text):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "relation_dml": [
    {
      "ingress_key": "db-relation-acl:public.deal_vendor_assignment:carr_writer:insert",
      "ingress_kind": "db_relation_acl",
      "relation": "public.deal_vendor_assignment",
      "relation_kind": "r",
      "grantee": "carr_writer",
      "privilege": "insert",
      "grantable": false
    },
    {
      "ingress_key": "db-relation-acl:public.deal_vendor_assignment:carr_writer:update",
      "ingress_kind": "db_relation_acl",
      "relation": "public.deal_vendor_assignment",
      "relation_kind": "r",
      "grantee": "carr_writer",
      "privilege": "update",
      "grantable": false
    }
  ],
  "runtime_dml_grants": {
    "count": 338,
    "digest": "sha256:1d16268baa3cd3bf925281f2c8d2ae29b2bf5abd31e098fe0155cf346213fa8b"
  }
}$current_grants$::jsonb $fn$;

CREATE OR REPLACE FUNCTION ops.scac_mutation_catalog_v118_current() RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'ops', 'pg_temp'
    AS $$
declare observed_count integer; observed_digest text; registered_count integer;
  current_grants jsonb := ops.scac_current_grant_contract();
begin
  if not exists (
    select 1 from ops.scac_mutation_registry_version
     where registry_version=current_grants->>'registry_version'
       and registry_digest=current_grants->>'registry_digest'
  ) then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  live_observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles))),
  observed as (select * from live_observed union all
    select value->>'ingress_key',value from jsonb_array_elements(coalesce(current_grants->'retired_secdef_execute','[]'::jsonb)))
  select count(*) filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
    'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
      jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C")
        filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
      '[]'::jsonb)),'UTF8'),'sha256'),'hex'),
    count(*) filter (where row in (select value from jsonb_array_elements(current_grants->'secdef_execute')))
    into observed_count,observed_digest,registered_count from observed;
  if registered_count<>jsonb_array_length(current_grants->'secdef_execute') then return false; end if;
  if observed_count<>1267 or observed_digest<>'sha256:587e762e80c6691d4a07c5b35e48a259c7b8bdf885d8595ffd0c14928c657efa' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*) filter (where row not in (select value from jsonb_array_elements(current_grants->'relation_dml'))),
    'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
      jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C")
        filter (where row not in (select value from jsonb_array_elements(current_grants->'relation_dml'))),
      '[]'::jsonb)),'UTF8'),'sha256'),'hex'),
    count(*) filter (where row in (select value from jsonb_array_elements(current_grants->'relation_dml')))
    into observed_count,observed_digest,registered_count from observed;
  if registered_count<>jsonb_array_length(current_grants->'relation_dml') then return false; end if;
  if observed_count<>321 or observed_digest<>'sha256:2ece3984f1389d78749dd3055ecf72516f02f97e33d260192e66daff062292e0' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>15 or observed_digest<>'sha256:f7a33d14ce431ba588cc07dff07fc9fd91a0dcdb41112de6fecd390fb42dc098' then return false; end if;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;
  return observed_count=13 and observed_digest='sha256:93724fe71ed216afac9b4bf48eee500e693de057cae0b59f59ff8e68cc6a2bc0';
end $$;

CREATE OR REPLACE FUNCTION ops.scac_reference_monitor_state() RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'ops', 'pg_temp'
    AS $$
declare grant_snapshot jsonb; mode_state jsonb; epoch_chain jsonb;
        latest ops.scac_policy_epoch%rowtype;
        registry ops.scac_mutation_registry_version%rowtype; missing_guards integer;
        unsupported_writable integer;
        grant_state text; guard_state text;
begin
  grant_snapshot:=ops.scac_runtime_dml_grant_snapshot();
  mode_state:=ops.scac_reference_monitor_mode();
  epoch_chain:=ops.scac_policy_epoch_chain_state();
  select * into latest from ops.scac_policy_epoch order by epoch desc limit 1;
  select * into registry from ops.scac_mutation_registry_version
    where registry_version=latest.registry_version;
  grant_state:=case when registry.registry_version='scac-mutation-registry.v118' and
    (grant_snapshot->>'entry_count')::integer=338 and
    grant_snapshot->>'grant_digest'='sha256:1d16268baa3cd3bf925281f2c8d2ae29b2bf5abd31e098fe0155cf346213fa8b'
    then 'current' else 'drifted_or_unbound' end;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
  ), writable as (
    select distinct c.oid from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    where c.relkind in ('r','p') and a.grantee<>c.relowner
      and (a.grantee=0 or a.grantee in(select oid from connected))
      and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
    union
    select distinct c.oid from pg_attribute att join pg_class c on c.oid=att.attrelid
    cross join lateral aclexplode(att.attacl) a
    where c.relkind in ('r','p') and att.attnum>0 and not att.attisdropped
      and a.grantee<>c.relowner
      and (a.grantee=0 or a.grantee in(select oid from connected))
      and a.privilege_type in ('INSERT','UPDATE')
  )
  select count(*) into missing_guards from writable w where not exists(
    select 1 from pg_trigger t where t.tgrelid=w.oid and not t.tgisinternal
      and t.tgfoid='ops.scac_reference_monitor_guard()'::regprocedure
      and (t.tgtype & 1)=1);
  with runtime_roles as (
    select oid from pg_roles where rolname in ('carr_writer','carr_jobs','carr_authority')
  ), unsupported as (
    select distinct c.oid from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
    where n.nspname not in ('pg_catalog','information_schema') and
      c.relkind in ('v','m','f') and a.grantee<>c.relowner and
      (a.grantee=0 or a.grantee in(select oid from runtime_roles))
      and a.privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
    union
    select distinct c.oid from pg_attribute att join pg_class c on c.oid=att.attrelid
    join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(att.attacl) a
    where n.nspname not in ('pg_catalog','information_schema') and
      c.relkind in ('v','m','f') and att.attnum>0 and not att.attisdropped and
      a.grantee<>c.relowner and
      (a.grantee=0 or a.grantee in(select oid from runtime_roles))
      and a.privilege_type in ('INSERT','UPDATE')
  ) select count(*) into unsupported_writable from unsupported;
  guard_state:=case when unsupported_writable>0 then 'unsupported_writable_relation'
    when missing_guards=0 then 'complete' else 'incomplete' end;
  return jsonb_build_object('schema_version','scac-reference-monitor-state.v1',
    'monitor_state',case when grant_state='current' and guard_state='complete'
      and coalesce((epoch_chain->>'valid')::boolean,false)
      and (epoch_chain->>'current_epoch')::bigint=latest.epoch
      and epoch_chain->>'current_epoch_digest'=latest.epoch_digest
      and mode_state->>'integrity_state'<>'invalid_fail_closed' then 'current'
      else 'unavailable' end,
    'grant_state',grant_state,'grant_digest',grant_snapshot->>'grant_digest',
    'guard_state',guard_state,'missing_guard_count',missing_guards,
    'unsupported_writable_relation_count',unsupported_writable,
    'mode',mode_state->>'mode','mode_integrity_state',mode_state->>'integrity_state',
    'policy_epoch_state',case when coalesce((epoch_chain->>'valid')::boolean,false)
      and (epoch_chain->>'current_epoch')::bigint=latest.epoch
      and epoch_chain->>'current_epoch_digest'=latest.epoch_digest
      then 'current' else 'invalid_or_unavailable' end,
    'policy_epoch',latest.epoch,'policy_epoch_digest',latest.epoch_digest,
    'registry_version',latest.registry_version,'registry_digest',latest.registry_digest,
    'direct_database_grant_cutover',false,'production_enforcement_active',false);
end $$;
