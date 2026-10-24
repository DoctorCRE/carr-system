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
create trigger scac_reference_monitor_guard_row before insert or update or delete
  on public.doctrine_embedding for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
  on public.doctrine_embedding for each statement execute function ops.scac_reference_monitor_guard();

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

-- Register semantic grants alongside the current main contract; preserve all existing grants and retirements.
CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $fn$ SELECT $current_grants${
  "relation_dml": [
    {
      "grantee": "carr_writer",
      "relation": "public.deal_vendor_assignment",
      "grantable": false,
      "privilege": "insert",
      "ingress_key": "db-relation-acl:public.deal_vendor_assignment:carr_writer:insert",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    },
    {
      "grantee": "carr_writer",
      "relation": "public.deal_vendor_assignment",
      "grantable": false,
      "privilege": "update",
      "ingress_key": "db-relation-acl:public.deal_vendor_assignment:carr_writer:update",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    },
    {
      "grantee": "carr_writer",
      "relation": "public.doctrine_embedding",
      "grantable": false,
      "privilege": "insert",
      "ingress_key": "db-relation-acl:public.doctrine_embedding:carr_writer:insert",
      "ingress_kind": "db_relation_acl",
      "relation_kind": "r"
    }
  ],
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
    },
    {
      "config": [
        "search_path=pg_catalog, pg_temp"
      ],
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[])",
      "volatility": "s",
      "ingress_key": "db-function-acl:public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[]):carr_reader:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[])",
      "volatility": "s",
      "ingress_key": "db-function-acl:public.search_doctrine_semantic(p_query text, p_actor_id uuid, p_content_classes text[], p_limit integer, p_policy_id text, p_embeddings vector[]):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "registry_version": "scac-mutation-registry.v118",
  "runtime_dml_grants": {
    "count": 339,
    "digest": "sha256:a210dbda8843ef57aecf1985cb0b97f7ab6fac5e1e99df0926542eb2945254bc"
  },
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
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.jev_call_receipt_integrity()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.jev_call_receipt_integrity():carr_reader:execute",
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
      "signature": "ops.jev_call_receipt_integrity()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.jev_call_receipt_integrity():carr_writer:execute",
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
      "grantee": "carr_reader",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text):carr_reader:execute",
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
      "signature": "ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.read_jev_call_receipts(p_session text, p_since timestamp with time zone, p_lim integer, p_actor_slug text):carr_writer:execute",
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
      "signature": "ops.record_jev_call_receipt(p_session_id text, p_purpose text, p_question_ids text[], p_facets text[], p_model_requested text, p_model_answered text, p_state_sha256 text, p_questions_sha256 text, p_answers_sha256 text, p_prompt_sha256 text, p_answers jsonb, p_usage jsonb, p_actor_id uuid, p_actor_slug text, p_idempotency_key text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.record_jev_call_receipt(p_session_id text, p_purpose text, p_question_ids text[], p_facets text[], p_model_requested text, p_model_answered text, p_state_sha256 text, p_questions_sha256 text, p_answers_sha256 text, p_prompt_sha256 text, p_answers jsonb, p_usage jsonb, p_actor_id uuid, p_actor_slug text, p_idempotency_key text):carr_writer:execute",
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
  ]
}$current_grants$::jsonb $fn$;

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
  grant_state:=case when registry.registry_version=ops.scac_current_grant_contract()->>'registry_version' and
    (grant_snapshot->>'entry_count')::integer=
      (ops.scac_current_grant_contract()->'runtime_dml_grants'->>'count')::integer and
    grant_snapshot->>'grant_digest'=ops.scac_current_grant_contract()->'runtime_dml_grants'->>'digest'
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
