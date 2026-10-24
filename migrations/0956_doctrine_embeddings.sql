-- rollback: Drop public.search_doctrine_semantic and public.doctrine_embedding; retain the vector extension and hardened shared visibility function.
create extension if not exists vector with schema public;
grant execute on function public.vector(public.vector,integer,boolean) to carr_reader,carr_writer;
grant execute on function public.vector_norm(public.vector) to carr_writer;

create table public.doctrine_embedding (
  revision_id uuid not null references public.doctrine_revision(id),
  chunk_index integer not null default 0 check (chunk_index >= 0),
  primary key (revision_id,chunk_index),
  model text not null check (model = '@cf/baai/bge-small-en-v1.5:cls:chunks-v1'),
  embedding public.vector(384) not null check (public.vector_norm(embedding) > 0),
  embedded_at timestamptz not null default now()
);
create index doctrine_embedding_cosine_idx on public.doctrine_embedding
  using hnsw (embedding public.vector_cosine_ops);
revoke all on public.doctrine_embedding from public;
grant select, insert on public.doctrine_embedding to carr_writer;

create or replace function public.current_retrievable_doctrine(
  p_actor_id uuid, p_content_classes text[] default null
) returns table (
  section_id uuid, section_key text, section_title text, doc_slug text,
  doc_title text, content_class text, plain_text text,
  body_search_vector tsvector, section_title_vector tsvector,
  document_title_vector tsvector
) language sql stable security invoker set search_path = '' as $function$
  select s.id,s.section_key,s.title,d.slug,d.title,d.content_class,
         r.plain_text,r.search_vector,s.title_search_vector,d.title_search_vector
    from public.doctrine_section s
    join public.doctrine_document d on d.id=s.document_id
    join public.doctrine_revision r on r.id=s.current_revision_id
   where s.status='active'
     and (d.visibility <> 'personal' or d.owner_actor_id=p_actor_id)
     and (p_content_classes is null or d.content_class=any(p_content_classes))
$function$;

create function public.search_doctrine_semantic(
  p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer,
  p_policy_id text, p_embeddings public.vector[]
) returns jsonb
language sql stable security definer
set search_path = pg_catalog, pg_temp
as $function$
with policy as materialized (
  select policy_id,version from public.retrieval_ranking_policy
   where policy_id=coalesce(p_policy_id,
     (select policy_id from public.retrieval_ranking_policy where is_default and status='active'))
     and status in ('candidate','active')
),
visible as materialized (
  select c.*, s.current_revision_id as revision_id
    from public.current_retrievable_doctrine(p_actor_id,p_content_classes) c
    join public.doctrine_section s on s.id=c.section_id
),
coverage as materialized (
  select count(distinct v.section_id)::integer as visible,
         count(distinct e.revision_id)::integer as embedded
    from visible v
    left join public.doctrine_revision r on r.id=v.revision_id and r.section_id=v.section_id
    left join public.doctrine_embedding e on e.revision_id=r.id
      and e.model='@cf/baai/bge-small-en-v1.5:cls:chunks-v1'
),
lexical as materialized (
  select l.*, row_number() over (
    order by l.final_score desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id) as position
    from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,100,p_policy_id,true) l
),
chunk_scores as materialized (
  select v.section_id,
         max(1 - (e.embedding OPERATOR(public.<=>) q.embedding))::double precision as similarity
    from visible v
    join public.doctrine_revision r on r.id=v.revision_id and r.section_id=v.section_id
    join public.doctrine_embedding e on e.revision_id=r.id
      and e.model='@cf/baai/bge-small-en-v1.5:cls:chunks-v1'
    cross join unnest(p_embeddings) as q(embedding)
   group by v.section_id
),
nearest as materialized (
  select v.*,c.similarity from visible v join chunk_scores c on c.section_id=v.section_id
   order by c.similarity desc,v.section_id limit 100
),
semantic as (
  select n.*, row_number() over (order by similarity desc,section_id) as position
    from nearest n where similarity > 0
),
blended as (
  select v.section_id,v.section_key,v.section_title as title,v.doc_slug,v.content_class,
         coalesce(l.snippet,left(v.plain_text,240)) as snippet,
         coalesce(l.lexical_score,0) as lexical_score,
         coalesce(l.concept_score,0) as concept_score,
         (30.5 * (coalesce(1.0/(60+l.position),0) + coalesce(1.0/(60+s.position),0)))::double precision as final_score,
         coalesce(l.provenance,'{}'::jsonb) || jsonb_build_object(
           'complete',true,'policy_id',p.policy_id,'policy_version',p.version,
           'blend_id','rrf-60-v1','rrf_k',60,'rrf_scale',30.5,
           'lexical_position',l.position,'semantic_position',s.position,
           'lexical_final_score',l.final_score,
           'semantic_similarity',s.similarity,
           'embedding_model','@cf/baai/bge-small-en-v1.5:cls:chunks-v1') as provenance,
         coalesce(s.similarity,0) as similarity
    from visible v cross join policy p
    left join lexical l on l.section_id=v.section_id
    left join semantic s on s.section_id=v.section_id
   where l.section_id is not null or s.section_id is not null
),
limited as (
  select b.*,b.final_score as rank from blended b
   order by final_score desc,similarity desc,concept_score desc,lexical_score desc,section_key,section_id
   limit greatest(1,least(coalesce(p_limit,20),100))
),
hits as (
  select coalesce(jsonb_agg((to_jsonb(l)-'similarity') || jsonb_build_object(
    'provenance',l.provenance || jsonb_build_object('final_score',l.final_score))
    order by l.final_score desc,l.similarity desc,l.concept_score desc,l.lexical_score desc,l.section_key,l.section_id),'[]'::jsonb) as value
  from limited l
)
select jsonb_build_object('visible',c.visible,'embedded',c.embedded,'hits',
  case when c.embedded=0 then
    (select coalesce(jsonb_agg(to_jsonb(l)),'[]'::jsonb)
       from public.search_doctrine_situations(p_query,p_actor_id,p_content_classes,p_limit,p_policy_id,true) l)
  else h.value end)
from coverage c cross join hits h
$function$;
revoke all on function public.search_doctrine_semantic(text,uuid,text[],integer,text,public.vector[]) from public;
grant execute on function public.search_doctrine_semantic(text,uuid,text[],integer,text,public.vector[]) to carr_reader,carr_writer;

-- Account for the two semantic search EXECUTEs and writer embedding INSERT in the live catalog.
CREATE OR REPLACE FUNCTION ops.scac_mutation_catalog_v118_current()
 RETURNS boolean
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'ops', 'pg_temp'
AS $function$
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
  if observed_count<>1269 or observed_digest<>'sha256:bfaf0d3af69f2ad229d9523c2a6660825c0ef6e60d539c832ac00ad787191d77' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>322 or observed_digest<>'sha256:e8f392451cfe7190d0cdd0ef44c86578cc675452137d8117b39c5ac0d89e51d8' then return false; end if;
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
end $function$
